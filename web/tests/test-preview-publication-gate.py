#!/usr/bin/env python3
"""Regression checks for the close/build finalization race."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web" / "tools"))
import check_preview_publication as gate


SHA = "a" * 40
BASE_SHA = "b" * 40
UPDATED = "2026-11-16T12:00:00Z"


def request(action="build"):
    return {
        "action": action,
        "base_repository": "cryptoadvance/specter-diy",
        "base_sha": BASE_SHA,
        "base_ref": "master",
        "pr_number": 440,
        "head_repository": "someone/specter-diy",
        "head_sha": SHA,
        "head_ref": "feature",
        "source_updated_at": UPDATED,
    }


def live_pr(req, state="open", sha=SHA):
    return {
        "number": req["pr_number"],
        "state": state,
        "updated_at": req["source_updated_at"],
        "base": {
            "repo": {"full_name": req["base_repository"]},
            "sha": req["base_sha"],
            "ref": req["base_ref"],
        },
        "head": {
            "repo": {"full_name": req["head_repository"]},
            "ref": req["head_ref"],
            "sha": sha,
        },
    }


class PublicationRaceTests(unittest.TestCase):
    def test_open_pr_at_publication_time_is_allowed(self):
        req = request()
        self.assertTrue(gate.publication_allowed(
            req, "token", lambda *_: live_pr(req)))

    def test_closed_after_earlier_validation_is_rejected(self):
        req = request()
        state = {"pr_state": "open"}

        def fetch(*_):
            return live_pr(req, state=state["pr_state"])

        # The same request was valid when the build completed.
        self.assertTrue(gate.publication_allowed(req, "token", fetch))
        # Closing the PR before git push must prevent *any* Pages deployment.
        state["pr_state"] = "closed"
        self.assertFalse(gate.publication_allowed(req, "token", fetch))

    def test_stale_commit_is_not_published(self):
        req = request()
        self.assertFalse(gate.publication_allowed(
            req, "token", lambda *_: live_pr(req, sha="c" * 40)))

    def test_delete_requires_closed_pr(self):
        req = request(action="delete")
        self.assertFalse(gate.publication_allowed(
            req, "token", lambda *_: live_pr(req, state="open")))
        self.assertTrue(gate.publication_allowed(
            req, "token", lambda *_: live_pr(req, state="closed")))

    def test_live_api_error_fails_closed(self):
        req = request()

        def fail(*_):
            raise RuntimeError("GitHub API unavailable")

        with self.assertRaisesRegex(RuntimeError, "API unavailable"):
            gate.publication_allowed(req, "token", fail)

    def test_workflow_guards_push_and_deployment(self):
        workflow = (ROOT / ".github/workflows/preview.yml").read_text(encoding="utf-8")
        self.assertIn("id: recheck", workflow)
        self.assertIn("python3 simulator/web/tools/check_preview_publication.py", workflow)
        recheck = workflow.index("- name: Revalidate live PR immediately before publishing Pages")
        push = workflow.index("- name: Commit and update GitHub Pages source tree")
        self.assertLess(recheck, push)
        for step in (
            "Commit and update GitHub Pages source tree",
            "Stage the complete public Pages tree without private state",
            "Upload static Pages artifact",
            "Deploy Web Simulator Pages",
        ):
            block = workflow.split(f"- name: {step}\n", 1)[1].split("\n      - name:", 1)[0]
            self.assertIn("if: steps.recheck.outputs.allowed == 'true'", block)

    def test_step_outputs_are_fail_closed(self):
        env = request()
        env = {
            "ACTION": env["action"], "BASE_REPOSITORY": env["base_repository"],
            "BASE_SHA": env["base_sha"], "BASE_REF": env["base_ref"],
            "PR_NUMBER": str(env["pr_number"]), "HEAD_REPOSITORY": env["head_repository"],
            "HEAD_SHA": env["head_sha"], "HEAD_REF": env["head_ref"],
            "SOURCE_UPDATED_AT": env["source_updated_at"], "GH_TOKEN": "token",
        }
        with TemporaryDirectory() as temp:
            output = Path(temp) / "out"
            env["GITHUB_OUTPUT"] = str(output)
            with patch.dict(os.environ, env, clear=True), patch.object(
                gate, "publication_allowed", return_value=False
            ):
                gate.main()
            self.assertEqual(output.read_text(), "allowed=false\n")


if __name__ == "__main__":
    unittest.main()
