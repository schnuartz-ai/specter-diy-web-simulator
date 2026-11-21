#!/usr/bin/env python3
"""Notify maintainers of a real blocked native CI run; reuse the sole preview comment."""
from datetime import datetime, timezone
from urllib.parse import urlencode
import json
import os
import re
import time

from report_preview import (
    MARKER, TARGET_REPOSITORY, api, list_comments, managed_comments,
    replace_managed_comment, verify_machine_user,
)

SHA_RE = re.compile(r"[a-f0-9]{40}\Z")
EXPECTED_SIMULATOR = "cryptoadvance/specter-diy-web-simulator"


def action_required_ci_runs(number, sha, token, request_fn=api):
    """Find only real, still-blocked native CI workflows for this exact PR head."""
    query = urlencode({"event": "pull_request", "head_sha": sha, "per_page": 100})
    result = request_fn("GET", f"/repos/{TARGET_REPOSITORY}/actions/runs?{query}", token)
    if not isinstance(result, dict) or not isinstance(result.get("workflow_runs"), list):
        raise ValueError("GitHub returned an invalid Actions runs response")
    blocked = []
    for run in result["workflow_runs"]:
        if (run.get("name") != "CI" or run.get("event") != "pull_request" or
                run.get("head_sha") != sha or run.get("status") != "completed" or
                run.get("conclusion") != "action_required" or
                (run.get("repository") or {}).get("full_name", "").lower() != TARGET_REPOSITORY or
                not str(run.get("path", "")).startswith(".github/workflows/test.yml@")):
            continue
        run_id = run.get("id")
        if type(run_id) is not int or run_id <= 0:
            continue
        blocked.append(f"https://github.com/{TARGET_REPOSITORY}/actions/runs/{run_id}")
    return sorted(set(blocked))


def comment_body(sha, urls):
    links = "\n".join(f"- [Approve and run CI workflow]({url})" for url in urls)
    return (
        f"**Specter PR Build** · \`{sha[:7]}\` — **Maintainer approval required**\n\n"
        "GitHub has paused the native CI run for this pull request. "
        "A maintainer with write access must review the changes and approve the workflow:\n\n"
        f"{links}\n\n"
        "After approval, the same commit is used to build **Specter firmware and its browser simulator**. "
        "Both results will be linked here.\n\n"
        "A maintainer can also enter \`/build\` as a PR comment to request the combined "
        "build manually without waiting for the native CI approval.\n\n"
        "**Experimental build:** Never use real funds or seed phrases in a PR preview.\n\n"
        f"{MARKER}\n<!-- specter-approval-sha:" + sha + " -->"
    )


def live_pr_matches(number, sha, updated_at, token, request_fn=api):
    pr = request_fn("GET", f"/repos/{TARGET_REPOSITORY}/pulls/{number}", token)
    if (not isinstance(pr, dict) or pr.get("state") != "open" or
            pr.get("number") != number or
            (pr.get("head") or {}).get("sha") != sha or
            (pr.get("base") or {}).get("repo", {}).get("full_name", "").lower() != TARGET_REPOSITORY):
        return False
    # Comments may update pr.updated_at, but an event must never be from the future.
    def parse(value):
        if not isinstance(value, str):
            raise ValueError("invalid PR date")
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    return parse(pr.get("updated_at", "")) >= parse(updated_at)


def notify(number, sha, updated_at, token, login, request_fn=api, retries=8, sleep_fn=time.sleep):
    if (type(number) is not int or number <= 0 or
            not isinstance(sha, str) or not SHA_RE.fullmatch(sha)):
        raise ValueError("invalid PR number or head SHA")
    if not live_pr_matches(number, sha, updated_at, token, request_fn):
        return "stale-pr"

    # pull_request_target may run before the pending CI appears in the Actions API.
    blocked = []
    for attempt in range(retries):
        blocked = action_required_ci_runs(number, sha, token, request_fn)
        if blocked:
            break
        if attempt + 1 < retries:
            sleep_fn(5)
    if not blocked:
        return "no-approval-needed"
    if not live_pr_matches(number, sha, updated_at, token, request_fn):
        return "stale-pr"

    verify_machine_user(token, login, request_fn)
    comments = list_comments(TARGET_REPOSITORY, number, token, request_fn)
    owned = managed_comments(comments, login)
    # Do not overwrite a newer completed report for the same exact commit.
    for comment in owned:
        body = comment.get("body", "")
        if (f"\`{sha[:7]}\`" in body and
                ("[Open browser simulator]" in body or "Latest build failed." in body)):
            return "already-reported"
    replace_managed_comment(
        TARGET_REPOSITORY, number, comments, login, token,
        comment_body(sha, blocked), request_fn,
    )
    return "waiting"


def main():
    if os.environ["GITHUB_REPOSITORY"].lower() != EXPECTED_SIMULATOR:
        raise ValueError("Approval reporter must run only on production simulator")
    token = os.environ["SPECTER_PREVIEW_BOT_TOKEN"]
    login = os.environ["SPECTER_PREVIEW_BOT_LOGIN"]
    result = notify(
        int(os.environ["PR_NUMBER"]), os.environ["HEAD_SHA"],
        os.environ["SOURCE_UPDATED_AT"], token, login,
    )
    print(json.dumps({"status": result}, sort_keys=True))


if __name__ == "__main__":
    main()
