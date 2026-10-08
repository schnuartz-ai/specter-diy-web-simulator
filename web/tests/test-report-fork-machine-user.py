#!/usr/bin/env python3
"""Security regression tests for fork-only machine-user commenting."""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import report_fork_machine_user as bot
from report_preview import MARKER

SIM_SHA = "9" * 40
HEAD_SHA = "a" * 40
UPDATED = "2026-10-08T12:30:00.000000Z"
USER = "specter-preview-test"
TOKEN = "fake-pat"


def request(action="build"):
    return {
        "request_id": "specter-pr-12-" + HEAD_SHA + "-99-1",
        "action": action, "base_repository": bot.TARGET,
        "base_sha": "b" * 40, "base_ref": "master",
        "pr_number": 12, "head_repository": "outside/specter-diy",
        "head_sha": HEAD_SHA, "head_ref": "feature",
        "source_updated_at": UPDATED,
    }


def state(req, outcome="success"):
    return {
        "latest_request_id": req["request_id"],
        "latest_action": req["action"],
        "latest_source_sha": req["head_sha"],
        "latest_source_updated_at": UPDATED,
        "workflow_run_id": 99, "run_attempt": 1, "status": outcome,
        "base_repository": bot.TARGET, "base_sha": req["base_sha"],
        "base_ref": req["base_ref"], "head_repository": req["head_repository"],
        "head_ref": req["head_ref"],
        "simulator_repository": bot.SIMULATOR, "simulator_sha": SIM_SHA,
        "successful_previews": [{
            "head_sha": req["head_sha"],
            "preview_url": "https://schnuartz-ai.github.io/specter-diy-web-simulator/pr/12/" + HEAD_SHA + "/",
            "firmware_url": "https://github.com/" + bot.SIMULATOR + "/actions/runs/99/artifacts/123",
            "run_url": "https://github.com/" + bot.SIMULATOR + "/actions/runs/99",
            "workflow_run_id": 99,
        }],
    }


def live_pr(req):
    return {
        "number": 12, "state": "closed" if req["action"] == "delete" else "open",
        "updated_at": UPDATED,
        "base": {"repo": {"full_name": bot.TARGET}, "sha": req["base_sha"], "ref": "master"},
        "head": {"repo": {"full_name": req["head_repository"]}, "sha": HEAD_SHA, "ref": "feature"},
    }


class MachineUserReporterTests(unittest.TestCase):
    def setUp(self):
        self.work = TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.pages = Path(self.work.name)
        p = self.pages / ".preview-state/pr/12.json"
        p.parent.mkdir(parents=True)
        self.state_path = p
        self.calls = []
        self.comments = [
            {"id": 11, "user": {"login": USER}, "body": MARKER + " older"},
            {"id": 12, "user": {"login": "some-other-contributor"}, "body": MARKER + " unrelated"},
            {"id": 13, "user": {"login": USER}, "body": "Unmarked personal message"},
        ]

    def mock_api(self, method, path, token, data=None):
        self.calls.append((method, path, token, data))
        self.assertEqual(token, TOKEN)
        if method == "GET" and path == "/user":
            return {"type": "User", "login": USER}
        if method == "GET" and path.startswith("/repos/" + bot.TARGET + "/issues/12/comments?"):
            return self.comments
        if method == "POST":
            return {"id": 51, "user": {"login": USER}}
        if method == "DELETE":
            return None
        raise AssertionError("Unexpected API: " + method + " " + path)

    def invoke(self, req=None, outcome="success", request_fn=None):
        req = req or request()
        return bot.report(req, self.pages, SIM_SHA, 99, 1,
                          outcome, TOKEN, USER, request_fn or self.mock_api,
                          pull_fetcher=lambda *_: live_pr(req))

    def test_own_marked_comment_only_and_post_before_delete(self):
        req = request()
        self.state_path.write_text(json.dumps(state(req)))
        result = self.invoke()
        self.assertEqual(result["removed_comments"], 1)
        writes = [(a, b) for a, b, _, _ in self.calls if a in ("POST", "DELETE")]
        self.assertEqual(writes, [
            ("POST", f"/repos/{bot.TARGET}/issues/12/comments"),
            ("DELETE", f"/repos/{bot.TARGET}/issues/comments/11"),
        ])
        posted = [d["body"] for a, _, _, d in self.calls if a == "POST"][0]
        self.assertIn("Open browser simulator", posted)
        self.assertIn("Firmware artifact", posted)
        self.assertIn("Never enter a real seed phrase", posted)

    def test_post_error_keeps_old_comment(self):
        req = request()
        self.state_path.write_text(json.dumps(state(req)))

        def api_error(method, path, token, data=None):
            if method == "POST":
                raise RuntimeError("API outage")
            return self.mock_api(method, path, token, data)

        with self.assertRaisesRegex(RuntimeError, "API outage"):
            self.invoke(request_fn=api_error)
        self.assertFalse(any(a == "DELETE" for a, _, _, _ in self.calls))

    def test_foreign_authentication_is_rejected_without_writes(self):
        req = request()
        self.state_path.write_text(json.dumps(state(req)))

        def wrong_user(method, path, token, data=None):
            if path == "/user":
                return {"login": "wrong-account", "type": "User"}
            return self.mock_api(method, path, token, data)

        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.invoke(request_fn=wrong_user)
        self.assertFalse(any(a in ("POST", "DELETE") for a, _, _, _ in self.calls))

    def test_only_fork_target_is_allowed(self):
        req = request()
        req["base_repository"] = "cryptoadvance/specter-diy"
        with self.assertRaisesRegex(ValueError, "only supports"):
            self.invoke(req)
        self.assertFalse(self.calls)

    def test_stale_state_rejected_without_writes(self):
        req = request()
        old = state(req)
        old["latest_source_sha"] = "f" * 40
        self.state_path.write_text(json.dumps(old))
        self.assertEqual(self.invoke()["status"], "stale-state")
        self.assertFalse(any(a in ("POST", "DELETE") for a, _, _, _ in self.calls))

    def test_closed_pr_deletes_only_existing_own_marked_comment(self):
        req = request("delete")
        self.state_path.write_text(json.dumps(state(req, "deleted")))
        result = self.invoke(req, "deleted")
        self.assertEqual(result["removed_comments"], 1)
        writes = [(a, b) for a, b, _, _ in self.calls if a in ("POST", "DELETE")]
        self.assertEqual(writes, [("DELETE", f"/repos/{bot.TARGET}/issues/comments/11")])

    def test_unconfirmed_post_response_does_not_delete(self):
        req = request()
        self.state_path.write_text(json.dumps(state(req)))
        def missing_id(method, path, token, data=None):
            if method == "POST":
                self.calls.append((method, path, token, data))
                return {"user": {"login": USER}}
            return self.mock_api(method, path, token, data)
        with self.assertRaisesRegex(ValueError, "not confirmed"):
            self.invoke(request_fn=missing_id)
        self.assertFalse(any(a == "DELETE" for a, _, _, _ in self.calls))


if __name__ == "__main__":
    unittest.main()
