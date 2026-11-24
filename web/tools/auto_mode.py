#!/usr/bin/env python3
"""Update the bot's auto-mode display; dispatcher independently authorizes builds."""
import re
import os
from datetime import datetime, timezone
from urllib.error import HTTPError

from report_preview import TARGET_REPOSITORY, api, verify_machine_user

MARKER = re.compile(r"<!-- specter-auto-mode:([1-9][0-9]*) -->")
BLOCK = re.compile(r"\n?<!-- specter-auto-begin -->\n.*?\n<!-- specter-auto-end -->\n?", re.DOTALL)
LOGIN = re.compile(r"[A-Za-z0-9-]{1,39}\Z")


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def valid_approval(number, comment_id, token, request_fn=api):
    """Read the source command for display; this does not authorize a build."""
    if type(number) is not int or number <= 0 or type(comment_id) is not int or comment_id <= 0:
        raise ValueError("invalid PR or comment ID")
    try:
        comment = request_fn("GET", f"/repos/{TARGET_REPOSITORY}/issues/comments/{comment_id}", token)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    if not isinstance(comment, dict) or comment.get("issue_url") != (
            f"https://api.github.com/repos/{TARGET_REPOSITORY}/issues/{number}"):
        return None
    if (comment.get("body") or "").strip().lower() != "/build-auto":
        return None
    created = timestamp(comment.get("created_at", ""))
    if created != timestamp(comment.get("updated_at", "")):
        return None
    username = (comment.get("user") or {}).get("login", "")
    if not isinstance(username, str) or not LOGIN.fullmatch(username):
        return None
    query = """query($number:Int!) { repository(owner:"cryptoadvance",name:"specter-diy") {
      pullRequest(number:$number) { state createdAt timelineItems(last:1,itemTypes:[REOPENED_EVENT]) {
        nodes { ... on ReopenedEvent { createdAt } } } } } }"""
    result = request_fn("POST", "/graphql", token, {"query": query, "variables": {"number": number}})
    if not isinstance(result, dict) or result.get("errors"):
        raise ValueError("cannot verify PR open period")
    try:
        pr = result["data"]["repository"]["pullRequest"]
        if pr["state"] != "OPEN":
            return None
        epoch = timestamp(pr["createdAt"])
        events = pr["timelineItems"]["nodes"]
        if len(events) > 1:
            raise ValueError("invalid reopen history")
        if events:
            epoch = max(epoch, timestamp(events[0]["createdAt"]))
    except (KeyError, TypeError) as exc:
        raise ValueError("cannot verify PR open period") from exc
    return username if created > epoch else None


def render_status(number, comment_id, username, created_at=None):
    if not LOGIN.fullmatch(username):
        raise ValueError("invalid approver")
    status = (f"**Build mode:** Auto · Approved by "
            f"[@{username}](https://github.com/{TARGET_REPOSITORY}/pull/{number}#issuecomment-{comment_id})\n"
            f"<!-- specter-auto-mode:{comment_id} -->")
    if created_at:
        timestamp(created_at)
        status += f"\n<!-- specter-auto-created:{created_at} -->"
    return status


def find_bot_comment(number, username, token, request_fn=api):
    """Search only the bot's own comments, not the PR's untrusted comment stream."""
    if not LOGIN.fullmatch(username):
        raise ValueError("invalid bot username")
    query = """query($login:String!,$cursor:String) { user(login:$login) {
      issueComments(first:100,after:$cursor) { pageInfo { hasNextPage endCursor }
        nodes { url body issue { number repository { nameWithOwner } } } } } }"""
    cursor = None
    for _ in range(100):
        result = request_fn("POST", "/graphql", token,
                            {"query": query, "variables": {"login": username, "cursor": cursor}})
        if not isinstance(result, dict) or result.get("errors"):
            raise ValueError("cannot load bot comments")
        try:
            page = result["data"]["user"]["issueComments"]
            for comment in page["nodes"]:
                if not isinstance(comment, dict) or not isinstance(comment.get("issue"), dict):
                    continue
                issue = comment["issue"]
                if (issue["number"] != number or
                        issue["repository"]["nameWithOwner"].lower() != TARGET_REPOSITORY or
                        "<!-- specter-web-simulator-preview -->" not in comment["body"]):
                    continue
                match = re.fullmatch(
                    rf"https://github\.com/{re.escape(TARGET_REPOSITORY)}/(?:pull|issues)/{number}#issuecomment-([1-9][0-9]*)",
                    comment["url"], re.IGNORECASE)
                if not match:
                    raise ValueError("invalid bot comment URL")
                return {"id": int(match.group(1)), "body": comment["body"], "user": {"login": username}}
            if not page["pageInfo"]["hasNextPage"]:
                return None
            next_cursor = page["pageInfo"]["endCursor"]
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise ValueError("invalid bot comment pagination")
            cursor = next_cursor
        except (KeyError, TypeError) as exc:
            raise ValueError("invalid bot comments response") from exc
    raise ValueError("bot comment lookup exceeded its page limit")


def update_mode(number, comment_id, mode, token, bot_login, request_fn=api):
    """Update the card after the dispatcher has performed its permission check."""
    if mode not in ("set", "clear"):
        raise ValueError("invalid update mode")
    verify_machine_user(token, bot_login, request_fn)
    card = find_bot_comment(number, bot_login, token, request_fn)
    approver = valid_approval(number, comment_id, token, request_fn) if mode == "set" else None
    if mode == "set" and not approver:
        return "invalid-approval"
    created_at = None
    if mode == "set":
        try:
            source = request_fn("GET", f"/repos/{TARGET_REPOSITORY}/issues/comments/{comment_id}", token)
        except HTTPError as exc:
            if exc.code == 404:
                return "invalid-approval"
            raise
        created_at = source["created_at"]
        timestamp(created_at)
    if mode == "set" and card:
        previous = MARKER.findall(card["body"])
        if len(previous) > 1:
            raise ValueError("multiple auto-mode pointers")
        if previous and int(previous[0]) != comment_id:
            # A delayed older request must not overwrite a newer approval. If
            # the old source has vanished, clear the existing pointer first.
            times = re.findall(r"<!-- specter-auto-created:([^ ]+) -->", card["body"])
            if len(times) != 1:
                return "superseded"
            if ((timestamp(created_at), comment_id) <=
                    (timestamp(times[0]), int(previous[0]))):
                return "superseded"
    if mode == "clear":
        if not card:
            return "no-card"
        stored = MARKER.findall(card["body"])
        if len(stored) != 1 or int(stored[0]) != comment_id:
            return "superseded"
    body = card["body"] if card else "**Specter PR Build**\n\n<!-- specter-web-simulator-preview -->"
    body = BLOCK.sub("\n", body).rstrip()
    if approver:
        body += "\n\n<!-- specter-auto-begin -->\n" + render_status(number, comment_id, approver, created_at) + "\n<!-- specter-auto-end -->"
    else:
        body += "\n\n<!-- specter-auto-begin -->\n**Build mode:** No auto approval\n<!-- specter-auto-end -->"
    if card and card["body"] == body:
        return "unchanged"
    if card:
        result = request_fn("PATCH", f"/repos/{TARGET_REPOSITORY}/issues/comments/{card['id']}", token, {"body": body})
    else:
        result = request_fn("POST", f"/repos/{TARGET_REPOSITORY}/issues/{number}/comments", token, {"body": body})
    if not isinstance(result, dict) or (result.get("user") or {}).get("login", "").lower() != bot_login.lower():
        raise ValueError("bot comment update was not confirmed")
    if card and result.get("id") != card["id"]:
        raise ValueError("bot comment update returned another ID")
    return "auto" if approver else "disabled"


if __name__ == "__main__":
    if os.environ.get("GITHUB_REPOSITORY", "").lower() != "cryptoadvance/specter-diy-web-simulator":
        raise ValueError("auto-mode reporter must run in the production simulator")
    print(update_mode(int(os.environ["PR_NUMBER"]), int(os.environ["COMMENT_ID"]),
                      os.environ["AUTO_MODE_ACTION"], os.environ["SPECTER_PREVIEW_BOT_TOKEN"],
                      os.environ["SPECTER_PREVIEW_BOT_LOGIN"]))
