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
PR_RUN_PATH = re.compile(r"@refs/pull/([1-9][0-9]*)/(?:merge|head)\Z")


def unique_live_pr(number, sha, pr, token, request_fn):
    """Allow fallback run matching only when this head identifies one open PR."""
    if not isinstance(pr, dict) or pr.get("number") != number or pr.get("state") != "open":
        return False
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    head_repo = (head.get("repo") or {}).get("full_name", "")
    head_ref = head.get("ref")
    if (head.get("sha") != sha or not isinstance(head_repo, str) or
            not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", head_repo) or
            not isinstance(head_ref, str) or not head_ref or
            (base.get("repo") or {}).get("full_name", "").lower() != TARGET_REPOSITORY.lower()):
        return False
    query = urlencode({"state": "open", "per_page": 100,
                       "head": f"{head_repo.split('/', 1)[0]}:{head_ref}"})
    pulls = request_fn("GET", f"/repos/{TARGET_REPOSITORY}/pulls?{query}", token)
    if not isinstance(pulls, list) or len(pulls) >= 100:
        return False
    matches = [candidate for candidate in pulls
               if (candidate.get("head") or {}).get("sha") == sha and
               (candidate.get("head") or {}).get("ref") == head_ref and
               ((candidate.get("head") or {}).get("repo") or {}).get("full_name", "").lower() == head_repo.lower() and
               ((candidate.get("base") or {}).get("repo") or {}).get("full_name", "").lower() == TARGET_REPOSITORY.lower()]
    return len(matches) == 1 and matches[0].get("number") == number


def action_required_ci_runs(number, sha, token, request_fn=api, pr=None):
    """Find only real, still-blocked native CI workflows for this exact PR head."""
    query = urlencode({"event": "pull_request", "head_sha": sha, "per_page": 100})
    result = request_fn("GET", f"/repos/{TARGET_REPOSITORY}/actions/runs?{query}", token)
    if not isinstance(result, dict) or not isinstance(result.get("workflow_runs"), list):
        raise ValueError("GitHub returned an invalid Actions runs response")
    blocked = []
    fallback_pr = None
    fallback_matches_are_unique = None
    for run in result["workflow_runs"]:
        if (run.get("name") != "CI" or run.get("event") != "pull_request" or
                run.get("head_sha") != sha or run.get("status") != "completed" or
                run.get("conclusion") != "action_required" or
                (run.get("repository") or {}).get("full_name", "").lower() != TARGET_REPOSITORY or
                not (run.get("path") == ".github/workflows/test.yml" or
                     str(run.get("path", "")).startswith(".github/workflows/test.yml@"))):
            continue

        associated = run.get("pull_requests")
        if associated:
            if (not isinstance(associated, list) or len(associated) != 1 or
                    not isinstance(associated[0], dict) or
                    associated[0].get("number") != number):
                continue
        else:
            path_match = PR_RUN_PATH.search(str(run.get("path", "")))
            if path_match:
                if int(path_match.group(1)) != number:
                    continue
            else:
                # Fork workflow runs may omit pull_requests. In that case,
                # use branch metadata only if that exact head maps to one open PR.
                if fallback_matches_are_unique is None:
                    fallback_pr = pr or request_fn(
                        "GET", f"/repos/{TARGET_REPOSITORY}/pulls/{number}", token)
                    fallback_matches_are_unique = unique_live_pr(
                        number, sha, fallback_pr, token, request_fn)
                if not fallback_matches_are_unique:
                    continue
                head = (fallback_pr or {}).get("head") or {}
                if (run.get("head_branch") != head.get("ref") or
                        (run.get("head_repository") or {}).get("full_name", "").lower() !=
                        (head.get("repo") or {}).get("full_name", "").lower()):
                    continue
        run_id = run.get("id")
        if type(run_id) is not int or run_id <= 0:
            continue
        blocked.append(f"https://github.com/{TARGET_REPOSITORY}/actions/runs/{run_id}")
    return sorted(set(blocked))


def comment_body(sha, urls):
    links = "\n".join(f"- [Approve and run CI workflow]({url})" for url in urls)
    return (
        f"**Specter PR Build** · `{sha[:7]}` — **Maintainer approval required**\n\n"
        "GitHub has paused the native CI run for this pull request. "
        "A maintainer with write access must review the changes and approve the workflow:\n\n"
        f"{links}\n\n"
        "After approval, the same commit is used to build **Specter firmware and its browser simulator**. "
        "Both results will be linked here.\n\n"
        "A maintainer can comment `/build` to build this commit once, or "
        "`/build-auto` to build now and automatically rebuild future commits while "
        "the PR stays open. Closing and reopening the PR requires a fresh "
        "`/build-auto` approval. Native CI approval remains separate.\n\n"
        "**Experimental build:** Never use real funds or seed phrases in a PR preview.\n\n"
        f"{MARKER}\n<!-- specter-approval-sha:" + sha + " -->"
    )


def live_pr_matches(number, sha, updated_at, token, request_fn=api):
    pr = request_fn("GET", f"/repos/{TARGET_REPOSITORY}/pulls/{number}", token)
    if (not isinstance(pr, dict) or pr.get("state") != "open" or
            pr.get("number") != number or
            (pr.get("head") or {}).get("sha") != sha or
            ((pr.get("base") or {}).get("repo") or {}).get("full_name", "").lower() != TARGET_REPOSITORY):
        return False
    # Comments may update pr.updated_at, but an event must never be from the future.
    def parse(value):
        if not isinstance(value, str):
            raise ValueError("invalid PR date")
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    return pr if parse(pr.get("updated_at", "")) >= parse(updated_at) else False


def notify(number, sha, updated_at, token, login, request_fn=api, retries=8, sleep_fn=time.sleep):
    if (type(number) is not int or number <= 0 or
            not isinstance(sha, str) or not SHA_RE.fullmatch(sha)):
        raise ValueError("invalid PR number or head SHA")
    pr = live_pr_matches(number, sha, updated_at, token, request_fn)
    if not pr:
        return "stale-pr"

    # pull_request_target may run before the pending CI appears in the Actions API.
    blocked = []
    for attempt in range(retries):
        blocked = action_required_ci_runs(number, sha, token, request_fn, pr)
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
        if (f"`{sha[:7]}`" in body and
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
