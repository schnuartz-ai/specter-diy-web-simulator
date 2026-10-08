#!/usr/bin/env python3
"""Fork-only machine-user PR-comment reporter.

This file exists solely in the schnuartz-ai Web Simulator test fork.
The comment token is used only in the trusted finalizer, never in an
untrusted pull-request build job. Refuse every upstream target.
"""
import json
import os
import re
import sys
from pathlib import Path

from report_preview import api, comment_body, list_comments, managed_comments
from publish_preview import _load_state, _validate_live_pr
from validate_preview_request import parse_time, canonical_time

TARGET = "schnuartz-ai/specter-diy"
SIMULATOR = "schnuartz-ai/specter-diy-web-simulator"
SHA_RE = re.compile(r"[a-f0-9]{40}\Z")
LOGIN_RE = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?\Z")


def verify_machine_user(token, expected_login, request_fn=api):
    if not token or not LOGIN_RE.fullmatch(expected_login):
        raise ValueError("Missing token or invalid expected bot login")
    user = request_fn("GET", "/user", token)
    if not isinstance(user, dict) or user.get("type") != "User" or (
        str(user.get("login", "")).lower() != expected_login.lower()
    ):
        raise ValueError("The token does not belong to the configured machine user")
    return user["login"]


def replace_owned(repository, number, comments, login, token, body, request_fn=api):
    # Never modify a comment that is not both authored by our machine user
    # and tagged with the preview marker.
    owned = managed_comments(comments, login)
    ids = [c.get("id") for c in owned]
    if any(type(cid) is not int or cid <= 0 for cid in ids):
        raise ValueError("Invalid owned comment ID")
    if body is not None:
        response = request_fn("POST", f"/repos/{repository}/issues/{number}/comments",
                              token, {"body": body})
        if (not isinstance(response, dict) or
            type(response.get("id")) is not int or response["id"] <= 0 or
            (response.get("user") or {}).get("login") != login):
            raise ValueError("Comment creation was not confirmed for the expected bot")
    # Only after successful creation: preserve the former comment if POST fails.
    for cid in ids:
        request_fn("DELETE", f"/repos/{repository}/issues/comments/{cid}", token)
    return len(ids)


def report(request, pages, simulator_sha, run_id, run_attempt, result, token,
           expected_login, request_fn=api, pull_fetcher=None):
    if request.get("base_repository", "").lower() != TARGET or (
        os.environ.get("SIMULATOR_REPOSITORY", SIMULATOR).lower() != SIMULATOR
    ):
        raise ValueError("This test reporter only supports the two schnuartz-ai forks")
    if not SHA_RE.fullmatch(simulator_sha) or result not in (
        "success", "failure", "cancelled", "deleted"
    ):
        raise ValueError("Invalid build run identity")
    login = verify_machine_user(token, expected_login, request_fn)
    state = _load_state(pages / ".preview-state" / "pr" /
                        f"{request['pr_number']}.json")
    if not state:
        return {"applied": False, "status": "missing-state"}
    exact = {
        "latest_request_id": request["request_id"],
        "latest_action": request["action"],
        "latest_source_sha": request["head_sha"],
        "latest_source_updated_at": canonical_time(parse_time(request["source_updated_at"])),
        "workflow_run_id": run_id,
        "run_attempt": run_attempt,
        "status": result,
        "base_repository": request["base_repository"],
        "base_sha": request["base_sha"],
        "base_ref": request["base_ref"],
        "head_repository": request["head_repository"],
        "head_ref": request["head_ref"],
        "simulator_repository": SIMULATOR,
        "simulator_sha": simulator_sha,
    }
    if any(state.get(k) != v for k, v in exact.items()):
        return {"applied": False, "status": "stale-state"}
    fetch = pull_fetcher or (lambda repository, number, auth: request_fn(
        "GET", f"/repos/{repository}/pulls/{number}", auth))
    if not _validate_live_pr(request, token, fetch):
        return {"applied": False, "status": "stale-pr"}
    number = request["pr_number"]
    comments = list_comments(TARGET, number, token, request_fn)
    if request["action"] == "delete":
        removed = replace_owned(TARGET, number, comments, login, token, None, request_fn)
        return {"applied": True, "status": "deleted", "removed_comments": removed}
    site = "https://schnuartz-ai.github.io/specter-diy-web-simulator/"
    body = comment_body(state, request["action"], result, number, site, SIMULATOR)
    removed = replace_owned(TARGET, number, comments, login, token, body, request_fn)
    return {"applied": True, "status": result, "removed_comments": removed}


def main():
    token = os.environ["SPECTER_PREVIEW_BOT_TOKEN"]
    expected_login = os.environ["SPECTER_PREVIEW_BOT_LOGIN"]
    req = {
        "request_id": os.environ["REQUEST_ID"],
        "action": os.environ["ACTION"],
        "base_repository": os.environ["BASE_REPOSITORY"],
        "base_sha": os.environ["BASE_SHA"],
        "base_ref": os.environ["BASE_REF"],
        "pr_number": int(os.environ["PR_NUMBER"]),
        "head_repository": os.environ["HEAD_REPOSITORY"],
        "head_sha": os.environ["HEAD_SHA"],
        "head_ref": os.environ["HEAD_REF"],
        "source_updated_at": canonical_time(parse_time(os.environ["SOURCE_UPDATED_AT"])),
    }
    output = report(
        req, Path(os.environ["PAGES_DIR"]), os.environ["SIMULATOR_SHA"],
        int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"]),
        os.environ["BUILD_RESULT"], token, expected_login,
    )
    print(json.dumps(output, sort_keys=True))
    if not output["applied"]:
        raise SystemExit("Fork preview report not applied: " + output["status"])


if __name__ == "__main__":
    try:
        main()
    except (KeyError, RuntimeError, ValueError) as exc:
        print(f"Fork machine-user report failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        raise SystemExit(1)
