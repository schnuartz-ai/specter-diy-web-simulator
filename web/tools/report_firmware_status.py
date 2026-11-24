#!/usr/bin/env python3
"""Report the validated PR's firmware result on its exact source commit."""
import json
import os
import re
from urllib.request import Request, urlopen


SOURCE_REPOSITORY = "cryptoadvance/specter-diy"
SIMULATOR_REPOSITORY = "cryptoadvance/specter-diy-web-simulator"
STATUS_CONTEXT = "specter/firmware-build"
SHA_RE = re.compile(r"^[a-f0-9]{40}$")

DESCRIPTIONS = {
    "pending": "Native tests and Unix/STM32 firmware builds are running.",
    "success": "Native tests and Unix/STM32 firmware builds passed.",
    "failure": "Native tests or Unix/STM32 firmware build did not complete.",
}


def main() -> None:
    if os.environ.get("BASE_REPOSITORY", "").lower() != SOURCE_REPOSITORY:
        raise ValueError("Firmware status may only target the production Specter repository")
    if os.environ.get("SIMULATOR_REPOSITORY", "").lower() != SIMULATOR_REPOSITORY:
        raise ValueError("Firmware status must be reported by the production Web Simulator")

    action = os.environ.get("ACTION", "")
    if action != "build":
        raise ValueError("Firmware status is only valid for build requests")

    pr_number = os.environ.get("PR_NUMBER", "")
    if not re.fullmatch(r"[1-9][0-9]{0,6}", pr_number):
        raise ValueError("PR_NUMBER must be a valid positive pull request number")

    head_sha = os.environ.get("HEAD_SHA", "")
    if not SHA_RE.fullmatch(head_sha):
        raise ValueError("HEAD_SHA must be a full 40-character lowercase commit SHA")

    state = os.environ.get("STATUS_STATE", "")
    if state not in DESCRIPTIONS:
        raise ValueError("STATUS_STATE must be pending, success, or failure")

    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ValueError("GITHUB_RUN_ID must be a valid positive run ID")
    server_url = os.environ.get("GITHUB_SERVER_URL", "").rstrip("/")
    if server_url != "https://github.com":
        raise ValueError("Firmware status target URL must use github.com")

    token = os.environ.get("STATUS_TOKEN", "")
    if not token:
        raise ValueError("STATUS_TOKEN is required")

    body = json.dumps({
        "state": state,
        "context": STATUS_CONTEXT,
        "description": DESCRIPTIONS[state],
        "target_url": f"{server_url}/{SIMULATOR_REPOSITORY}/actions/runs/{run_id}",
    }).encode("utf-8")
    request = Request(
        f"https://api.github.com/repos/{SOURCE_REPOSITORY}/statuses/{head_sha}",
        data=body,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "specter-firmware-status-reporter",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=30) as response:
        result = json.load(response)
    if result.get("context") != STATUS_CONTEXT or result.get("state") != state:
        raise RuntimeError("GitHub returned a different commit status than requested")
    print(f"Reported {state} for PR #{pr_number} at {head_sha} as {STATUS_CONTEXT}")


if __name__ == "__main__":
    main()
