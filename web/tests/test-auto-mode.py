#!/usr/bin/env python3
"""Verify that a stored auto-mode pointer never becomes authorization itself."""
from pathlib import Path
import sys
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import auto_mode as auto


class AutoModeTests(unittest.TestCase):
    def setUp(self):
        self.comment = {"issue_url": "https://api.github.com/repos/cryptoadvance/specter-diy/issues/42",
                        "body": "/build-auto", "user": {"login": "alice"},
                        "created_at": "2026-11-24T12:00:00Z", "updated_at": "2026-11-24T12:00:00Z"}
        self.permission = "write"
        self.reopened = []
        self.state = "OPEN"
        self.deleted = False

    def api(self, method, path, token, data=None):
        if path.endswith("/issues/comments/123"):
            if self.deleted:
                raise HTTPError(path, 404, "missing", None, None)
            return self.comment
        if path.endswith("/collaborators/alice/permission"):
            return {"permission": self.permission}
        if path == "/graphql":
            return {"data": {"repository": {"pullRequest": {"state": self.state,
                "createdAt": "2026-11-20T12:00:00Z", "timelineItems": {"nodes": self.reopened}}}}}
        self.fail(path)

    def check(self):
        return auto.valid_approval(42, 123, "token", self.api)

    def test_authorized_and_link_uses_real_handle(self):
        self.assertEqual(self.check(), "alice")
        status = auto.render_status(42, 123, self.check())
        self.assertIn("[@alice]", status)
        self.assertIn("/pull/42#issuecomment-123", status)

    def test_bot_lookup_pages_only_bot_comments_and_matches_pr(self):
        calls = []
        def query(method, path, token, data=None):
            calls.append(data["variables"]["cursor"])
            cursor = data["variables"]["cursor"]
            nodes = [] if cursor is None else [{
                "url": "https://github.com/cryptoadvance/specter-diy/pull/42#issuecomment-5",
                "body": "<!-- specter-web-simulator-preview -->",
                "issue": {"number": 42, "repository": {"nameWithOwner": "cryptoadvance/specter-diy"}}}]
            return {"data": {"user": {"issueComments": {"nodes": nodes,
                "pageInfo": {"hasNextPage": cursor is None, "endCursor": "next"}}}}}
        card = auto.find_bot_comment(42, "the-bot", "token", query)
        self.assertEqual(card["id"], 5)
        self.assertEqual(calls, [None, "next"])

    def test_set_patches_card_with_verified_handle_and_command_link(self):
        card = {"id": 5, "body": "Build result\n<!-- specter-web-simulator-preview -->",
                "user": {"login": "the-bot"}}
        writes = []
        def api(method, path, token, data=None):
            if path == "/user":
                return {"type": "User", "login": "the-bot"}
            if path.endswith("/issues/comments/123"):
                return {"created_at": "2026-11-24T12:00:00Z"}
            if method == "PATCH":
                writes.append(data["body"])
                return {"id": 5, "user": {"login": "the-bot"}}
            self.fail(path)
        with patch.object(auto, "find_bot_comment", return_value=card), patch.object(auto, "valid_approval", return_value="alice"):
            self.assertEqual(auto.update_mode(42, 123, "set", "token", "the-bot", api), "auto")
        self.assertIn("[@alice]", writes[0])
        self.assertIn("/pull/42#issuecomment-123", writes[0])

    def test_deleted_edited_or_wrong_pr_revokes(self):
        self.deleted = True
        self.assertIsNone(self.check())
        self.deleted = False
        self.comment["updated_at"] = "2026-11-24T12:01:00Z"
        self.assertIsNone(self.check())
        self.comment["updated_at"] = self.comment["created_at"]
        self.comment["issue_url"] += "0"
        self.assertIsNone(self.check())

    def test_closed_and_reopened_pr(self):
        self.state = "CLOSED"
        self.assertIsNone(self.check())
        self.state = "OPEN"
        self.reopened = [{"createdAt": "2026-11-24T12:01:00Z"}]
        self.assertIsNone(self.check())

    def test_deleted_approval_updates_card_to_manual_without_discarding_build(self):
        previous = ("Build result\n<!-- specter-web-simulator-preview -->\n\n"
                    "<!-- specter-auto-begin -->\n" + auto.render_status(42, 123, "alice") +
                    "\n<!-- specter-auto-end -->")
        card = {"id": 5, "body": previous, "user": {"login": "the-bot"}}
        writes = []
        def api(method, path, token, data=None):
            if path == "/user":
                return {"type": "User", "login": "the-bot"}
            if method == "PATCH":
                writes.append(data["body"])
                return {"id": 5, "user": {"login": "the-bot"}}
            self.fail(path)
        with patch.object(auto, "find_bot_comment", return_value=card), patch.object(auto, "valid_approval", return_value=None):
            self.assertEqual(auto.update_mode(42, 123, "clear", "token", "the-bot", api), "disabled")
        self.assertIn("Build result", writes[0])
        self.assertIn("**Build mode:** No auto approval", writes[0])
        self.assertNotIn("specter-auto-mode:123", writes[0])

    def test_old_refresh_cannot_overwrite_newer_approval(self):
        card = {"id": 5, "body": auto.render_status(42, 999, "bob", "2026-11-24T12:05:00Z"), "user": {"login": "the-bot"}}
        def api(method, path, token, data=None):
            if path == "/user":
                return {"type": "User", "login": "the-bot"}
            self.fail("must not write: " + path)
        with patch.object(auto, "find_bot_comment", return_value=card), patch.object(auto, "valid_approval", return_value=None):
            self.assertEqual(auto.update_mode(42, 123, "clear", "token", "the-bot", api), "superseded")

    def test_delayed_set_cannot_replace_more_recent_approval(self):
        card = {"id": 5, "body": auto.render_status(42, 999, "bob", "2026-11-24T12:05:00Z"), "user": {"login": "the-bot"}}
        def api(method, path, token, data=None):
            if path == "/user":
                return {"type": "User", "login": "the-bot"}
            if path.endswith("/999"):
                return {"created_at": "2026-11-24T12:05:00Z"}
            if path.endswith("/123"):
                return {"created_at": "2026-11-24T12:00:00Z"}
            self.fail("must not write: " + path)
        with patch.object(auto, "find_bot_comment", return_value=card), patch.object(auto, "valid_approval", return_value="alice"):
            self.assertEqual(auto.update_mode(42, 123, "set", "token", "the-bot", api), "superseded")

    def test_newer_set_can_replace_deleted_older_approval(self):
        card = {"id": 5, "body": auto.render_status(42, 99, "bob", "2026-11-24T11:00:00Z"), "user": {"login": "the-bot"}}
        writes = []
        def api(method, path, token, data=None):
            if path == "/user":
                return {"type": "User", "login": "the-bot"}
            if path.endswith("/123"):
                return {"created_at": "2026-11-24T12:00:00Z"}
            if method == "PATCH":
                writes.append(data["body"])
                return {"id": 5, "user": {"login": "the-bot"}}
            self.fail(path)
        with patch.object(auto, "find_bot_comment", return_value=card), patch.object(auto, "valid_approval", return_value="alice"):
            self.assertEqual(auto.update_mode(42, 123, "set", "token", "the-bot", api), "auto")
        self.assertIn("[@alice]", writes[0])


if __name__ == "__main__":
    unittest.main()
