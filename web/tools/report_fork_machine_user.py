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

from report_preview import api, _valid_success, list_comments, managed_comments, MARKER
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


def fork_comment_body(state, action, result, number):
    """Match the legacy compact Specter PR Build card, with provenance collapsed."""
    if action == "delete":
        return None
    if result not in ("success", "failure", "cancelled"):
        raise ValueError("Unexpected report result")
    short = state.get("latest_source_sha", "")
    if not isinstance(short, str) or not SHA_RE.fullmatch(short):
        raise ValueError("Invalid latest source SHA")
    source = state.get("head_repository", "")
    base_repo = state.get("base_repository", "")
    base_sha = state.get("base_sha", "")
    sim_sha = state.get("simulator_sha", "")
    if not all(isinstance(r, str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", r)
               for r in (source, base_repo)):
        raise ValueError("Invalid repository provenance")
    if not all(isinstance(h, str) and SHA_RE.fullmatch(h) for h in (base_sha, sim_sha)):
        raise ValueError("Invalid commit provenance")
    records = state.get("successful_previews", [])
    if not isinstance(records, list) or len(records) > 1:
        raise ValueError("Invalid successful preview state")
    site = "https://schnuartz-ai.github.io/specter-diy-web-simulator/"
    successful = [_valid_success(r, number, site, SIMULATOR) for r in records]
    status_icon = {"success": "✅", "failure": "❌", "cancelled": "⏹️"}[result]
    lines = [f"🧪 **Specter PR Build** · `{short[:7]}` {status_icon}", "",
             "<details>", "<summary>Build provenance</summary>", "",
             f"- **Source commit:** [`{source}@{short[:7]}`](https://github.com/{source}/commit/{short})",
             f"- **PR base commit:** [`{base_repo}@{base_sha[:7]}`](https://github.com/{base_repo}/commit/{base_sha})",
             f"- **Simulator commit:** [`{SIMULATOR}@{sim_sha[:7]}`](https://github.com/{SIMULATOR}/commit/{sim_sha})"]
    latest_id = state.get("workflow_run_id")
    if type(latest_id) is not int or latest_id <= 0:
        raise ValueError("Invalid latest build run ID")
    latest_run = f"https://github.com/{SIMULATOR}/actions/runs/{latest_id}"
    lines.append(f"- **Latest build:** [Build logs]({latest_run})")
    if successful:
        lines.append(f"- **Last successful build:** [Verified build logs]({successful[0][3]})")
    lines.extend(["", "</details>", ""])
    if result == "failure":
        lines.extend(["**Latest build failed.**", ""])
    elif result == "cancelled":
        lines.extend(["**Latest build was cancelled.**", ""])
    if successful:
        record_sha, preview, firmware, _ = successful[0]
        if record_sha != short:
            lines.extend([f"Last working preview is from an earlier commit `{record_sha[:7]}`.", ""])
        if preview:
            lines.extend([f"🖥️ [Open browser simulator]({preview})", ""])
        else:
            lines.extend(["Browser preview removed to stay within GitHub Pages limits. Push a new commit to generate a fresh preview.", ""])
        if firmware:
            lines.extend([f"⬇️ [Download firmware from the same commit]({firmware})", ""])
    else:
        lines.extend(["No successful browser preview is available yet.", ""])
    lines.extend([
        "⚠️ **Experimental development build. Never use real funds or enter a real seed phrase. Use dedicated test hardware for firmware builds.**",
        "", MARKER
    ])
    return "\n".join(lines)


def replace_owned(repository, number, comments, login, token, body, request_fn=api):
    """Keep the earliest owned preview comment; PATCH in place, never repost it.

    The stable comment ID avoids duplicate timeline entries and notifications.
    Delete only extra comments authored by this exact machine user and carrying
    our private preview marker. On edit failure do not delete anything.
    """
    owned = sorted(managed_comments(comments, login), key=lambda c: c.get("id", -1))
    ids = [c.get("id") for c in owned]
    if any(type(cid) is not int or cid <= 0 for cid in ids):
        raise ValueError("Invalid owned comment ID")
    if body is None:
        for cid in ids:
            request_fn("DELETE", f"/repos/{repository}/issues/comments/{cid}", token)
        return len(ids)
    if owned:
        survivor = owned[0]
        if survivor["body"] != body:
            response = request_fn(
                "PATCH", f"/repos/{repository}/issues/comments/{survivor['id']}",
                token, {"body": body}
            )
            if (not isinstance(response, dict) or
                response.get("id") != survivor["id"] or
                (response.get("user") or {}).get("login") != login):
                raise ValueError("Comment update was not confirmed for the expected bot")
        obsolete = ids[1:]
    else:
        response = request_fn(
            "POST", f"/repos/{repository}/issues/{number}/comments",
            token, {"body": body}
        )
        if (not isinstance(response, dict) or
            type(response.get("id")) is not int or response["id"] <= 0 or
            (response.get("user") or {}).get("login") != login):
            raise ValueError("Comment creation was not confirmed for the expected bot")
        obsolete = []
    for cid in obsolete:
        request_fn("DELETE", f"/repos/{repository}/issues/comments/{cid}", token)
    return len(obsolete)

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
    body = fork_comment_body(state, request["action"], result, number)
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
