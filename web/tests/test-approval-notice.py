#!/usr/bin/env python3
"""Regression tests for the one-comment GitHub approval notification."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import report_approval as notice
from report_preview import MARKER

SHA = "a" * 40
BASE = "b" * 40
TIME = "2026-11-20T12:00:00Z"
TOKEN = "mock-token"
LOGIN = "specter-preview-bot"


def pr(sha=SHA, state="open"):
    return {"number": 391, "state": state, "updated_at": TIME,
            "head": {"sha": sha},
            "base": {"repo": {"full_name": notice.TARGET_REPOSITORY}}}


def blocked_run(sha=SHA, conclusion="action_required"):
    return {"id": 12345, "name": "CI", "event": "pull_request", "head_sha": sha,
            "status": "completed", "conclusion": conclusion,
            "repository": {"full_name": notice.TARGET_REPOSITORY},
            "path": ".github/workflows/test.yml@refs/pull/391/merge"}


class ApprovalNoticeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.comments = []
        self.pr = pr()
        self.runs = [blocked_run()]
        self.login = LOGIN

    def api(self, method, path, token, data=None):
        self.calls.append((method, path, data))
        self.assertEqual(token, TOKEN)
        if method == "GET" and path.endswith("/pulls/391"):
            return self.pr
        if method == "GET" and "/actions/runs?" in path:
            return {"workflow_runs": self.runs}
        if method == "GET" and path == "/user":
            return {"type": "User", "login": LOGIN}
        if method == "GET" and "/issues/391/comments?" in path:
            return self.comments
        if method == "POST":
            return {"id": 333, "user": {"login": LOGIN}}
        if method == "PATCH":
            return {"id": int(path.rsplit("/", 1)[-1]), "user": {"login": LOGIN}}
        if method == "DELETE":
            return None
        self.fail(f"unexpected GitHub API call {method} {path}")

    def call(self):
        return notice.notify(391, SHA, TIME, TOKEN, LOGIN, request_fn=self.api,
                             retries=1, sleep_fn=lambda _: self.fail("no wait expected"))

    def writes(self):
        return [(m, p, d) for m, p, d in self.calls if m in ("POST", "PATCH", "DELETE")]

    def test_blocked_native_ci_is_reported_with_exact_approval_url(self):
        self.assertEqual(self.call(), "waiting")
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.writes()[0][0], "POST")
        body = self.writes()[0][2]["body"]
        self.assertIn("https://github.com/cryptoadvance/specter-diy/actions/runs/12345", body)
        self.assertIn("/build", body)
        self.assertIn("/build-auto", body)
        self.assertIn("Closing and reopening", body)
        self.assertIn(MARKER, body)
        self.assertIn("firmware and its browser simulator", body)

    def test_existing_own_comment_is_patched_not_reposted(self):
        self.comments = [
            {"id": 10, "user": {"login": LOGIN}, "body": MARKER + " old build"},
            {"id": 11, "user": {"login": "someone-else"}, "body": MARKER},
        ]
        self.assertEqual(self.call(), "waiting")
        self.assertEqual([(m, p) for m, p, _ in self.writes()],
                         [("PATCH", "/repos/cryptoadvance/specter-diy/issues/comments/10")])

    def test_extra_owned_comments_only_are_deleted(self):
        self.comments = [
            {"id": 10, "user": {"login": LOGIN}, "body": MARKER},
            {"id": 11, "user": {"login": LOGIN}, "body": MARKER},
            {"id": 12, "user": {"login": "other"}, "body": MARKER},
        ]
        self.call()
        self.assertEqual([m for m, _, _ in self.writes()], ["PATCH", "DELETE"])
        self.assertTrue(self.writes()[-1][1].endswith("/11"))

    def test_never_announces_unblocked_run(self):
        self.runs = [blocked_run(conclusion="success")]
        self.assertEqual(self.call(), "no-approval-needed")
        self.assertEqual(self.writes(), [])

    def test_stale_pr_is_not_notified(self):
        self.pr = pr(sha="c" * 40)
        self.assertEqual(self.call(), "stale-pr")
        self.assertEqual(self.writes(), [])

    def test_existing_current_success_is_not_overwritten(self):
        self.comments = [
            {"id": 10, "user": {"login": LOGIN},
             "body": f"**Specter PR Build** · " + chr(96) + SHA[:7] + chr(96) +
                     " [Open browser simulator](https://example.com) " + MARKER}
        ]
        self.assertEqual(self.call(), "already-reported")
        self.assertEqual(self.writes(), [])

    def test_ignores_other_workflows_or_head_commits(self):
        self.runs = [blocked_run(sha="d" * 40)]
        self.assertEqual(self.call(), "no-approval-needed")
        self.runs = [dict(blocked_run(), path=".github/workflows/malicious.yml@master")]
        self.assertEqual(self.call(), "no-approval-needed")


if __name__ == "__main__":
    unittest.main()
