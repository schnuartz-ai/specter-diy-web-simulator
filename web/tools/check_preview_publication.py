#!/usr/bin/env python3
"""Fail closed if a PR changes between finalizer preparation and Pages publication.

This check runs in the trusted finalizer *after* composing/pruning the Pages
tree and *just before* the first persistent write (git push).
"""
import os

from publish_preview import _validate_live_pr
from validate_preview_request import fetch_pull


def request_from_environment(env: dict) -> dict:
    return {
        "action": env["ACTION"],
        "base_repository": env["BASE_REPOSITORY"],
        "base_sha": env["BASE_SHA"],
        "base_ref": env["BASE_REF"],
        "pr_number": int(env["PR_NUMBER"]),
        "head_repository": env["HEAD_REPOSITORY"],
        "head_sha": env["HEAD_SHA"],
        "head_ref": env["HEAD_REF"],
        "source_updated_at": env["SOURCE_UPDATED_AT"],
    }


def publication_allowed(request: dict, token: str, pull_fetcher=fetch_pull) -> bool:
    """Re-use the publisher's live-PR identity and open/closed state checks."""
    return _validate_live_pr(request, token, pull_fetcher)


def main() -> None:
    request = request_from_environment(os.environ)
    allowed = publication_allowed(request, os.environ["GH_TOKEN"])
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(f"allowed={'true' if allowed else 'false'}\n")
    if not allowed:
        print("Skipping Pages publication: PR changed or closed after build validation.")


if __name__ == "__main__":
    main()
