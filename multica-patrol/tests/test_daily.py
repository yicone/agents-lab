"""daily-pack / daily-post tests. Synthetic fixtures only."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from patrol import config
from tests.fakes import (BASE_NOW, GOOD_ENV, FakeMultica, invoke, last_result,
                         make_issue)


def stale(identifier, status="in_progress", **kw):
    kw.setdefault("updated_at", BASE_NOW - timedelta(hours=5))
    kw.setdefault("last_activity_at", BASE_NOW - timedelta(hours=5))
    return make_issue(identifier, status, **kw)


class DailyCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir = Path(self.tmp.name)
        self.fake = FakeMultica()
        self.fake.issues = [
            make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID, assignee_id=None),
            make_issue("ACM-487", "in_progress", uid=config.INBOX_ISSUE_ID, assignee_id=None),
        ]
        self.body_file = self.state_dir / "daily.md"

    def _seed(self):
        child = stale("ACM-80", parent_issue_id="uuid-ACM-100")
        parent = make_issue("ACM-100", "in_review", uid="uuid-ACM-100",
                            updated_at=BASE_NOW - timedelta(days=2),
                            last_activity_at=BASE_NOW - timedelta(days=2))
        self.fake.issues += [child, parent]
        code, out, err = invoke(self.fake, ["run", "--slot", "2026102221"],
                                state_dir=self.state_dir)
        self.assertEqual(code, 0, err)


class TestDailyPack(DailyCase):
    def test_grouping_by_root_parent(self):
        self._seed()
        code, out, err = invoke(self.fake, ["daily-pack", "--format", "json"],
                                state_dir=self.state_dir)
        self.assertEqual(code, 0, err)
        pack = json.loads(out)
        roots = {g["root_identifier"] for g in pack["big_matters"]}
        self.assertIn("ACM-100", roots)  # child ACM-80 grouped under its root
        group = next(g for g in pack["big_matters"] if g["root_identifier"] == "ACM-100")
        members = {m["identifier"] for m in group["members"]}
        self.assertIn("ACM-80", members)
        self.assertIn("ACM-100", members)

    def test_md_format_and_no_mentions(self):
        self._seed()
        code, out, err = invoke(self.fake, ["daily-pack", "--format", "md"],
                                state_dir=self.state_dir)
        self.assertEqual(code, 0, err)
        self.assertIn("Big matters", out)
        self.assertNotIn("mention://agent/", out)
        self.assertNotIn("mention://member/", out)


class TestDailyPost(DailyCase):
    def test_posts_and_idempotent(self):
        self.body_file.write_text("需要你做：确认今日汇总\n\ndaily analysis body")
        code, out, err = invoke(self.fake, ["daily-post", "--file", str(self.body_file),
                                            "--slot", "20261022"],
                                state_dir=self.state_dir)
        self.assertEqual(code, 0, err)
        res = last_result(out)
        self.assertEqual(res["status"], "posted")
        self.assertEqual(len(self.fake.inbox_comments()), 1)
        content = self.fake.inbox_comments()[0]["content"].splitlines()
        self.assertTrue(content[0].startswith("需要你做："))
        self.assertEqual(content[1], "[patrol-daily:daily:SGT20261022 v2]")
        code, out, err = invoke(self.fake, ["daily-post", "--file", str(self.body_file),
                                            "--slot", "20261022"],
                                state_dir=self.state_dir)
        self.assertEqual(last_result(out)["status"], "duplicate")
        self.assertEqual(len(self.fake.inbox_comments()), 1)

    def test_rejects_mention_body(self):
        self.body_file.write_text("需要你做：x\nhello [@a](mention://member/xyz)")
        code, out, err = invoke(self.fake, ["daily-post", "--file", str(self.body_file)],
                                state_dir=self.state_dir)
        self.assertEqual(code, 2)
        self.assertIn("daily_body_invalid", err)
        self.assertEqual(self.fake.comment_add_count, 0)

    def test_rejects_bad_first_line(self):
        self.body_file.write_text("summary without required first line\nbody")
        code, out, err = invoke(self.fake, ["daily-post", "--file", str(self.body_file)],
                                state_dir=self.state_dir)
        self.assertEqual(code, 2)
        self.assertIn("first line format", err)
        self.assertEqual(self.fake.comment_add_count, 0)

    def test_rejects_empty_body(self):
        self.body_file.write_text("   \n")
        code, out, err = invoke(self.fake, ["daily-post", "--file", str(self.body_file)],
                                state_dir=self.state_dir)
        self.assertEqual(code, 2)

    def test_closed_inbox_refused(self):
        inbox = next(i for i in self.fake.issues if i["id"] == config.INBOX_ISSUE_ID)
        inbox["status"] = "done"
        self.body_file.write_text("需要你做：确认今日汇总\n\nbody")
        code, out, err = invoke(self.fake, ["daily-post", "--file", str(self.body_file)],
                                state_dir=self.state_dir)
        self.assertEqual(code, 2)
        self.assertIn("inbox", err)
        self.assertEqual(self.fake.comment_add_count, 0)

    def test_identity_required(self):
        self.body_file.write_text("需要你做：确认今日汇总\n\nbody")
        code, out, err = invoke(self.fake, ["daily-post", "--file", str(self.body_file)],
                                env={}, state_dir=self.state_dir)
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
