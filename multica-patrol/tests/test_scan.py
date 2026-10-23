"""Discovery/pagination/cache/budget tests. All data are synthetic test fixtures."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import timedelta

from patrol import config
from patrol.cli import PatrolCLI
from patrol.scan import discover, run_scan
from patrol.state import initial_state
from tests.fakes import BASE_NOW, FakeMultica, make_issue, run


def make_cli(fake: FakeMultica) -> PatrolCLI:
    return PatrolCLI(allowed_write_ids=frozenset({config.LEDGER_ISSUE_ID}),
                     env={}, _exec=fake.exec, _sleep=lambda s: None, retries=0)


class Discovery(unittest.TestCase):
    def test_pagination_250_issues(self):
        fake = FakeMultica()
        fake.issues = [make_issue(f"ACM-{1000 + i}", "in_progress") for i in range(250)]
        issues, errors, _ = discover(make_cli(fake))
        self.assertEqual(errors, [])
        self.assertEqual(len(issues), 250)

    def test_page_failure_is_incomplete_not_empty(self):
        fake = FakeMultica()
        fake.issues = [make_issue(f"ACM-{1000 + i}", "in_progress") for i in range(150)]

        def failer(argv):
            args = argv[1:]
            if "--offset" in args and args[args.index("--offset") + 1] == "100" \
                    and args[args.index("--status") + 1] == "in_progress":
                return subprocess.CompletedProcess(argv, 1, "", "boom")
            return None

        fake.fail_handler = failer
        issues, errors, _ = discover(make_cli(fake))
        self.assertTrue(errors)
        self.assertEqual(issues, [])  # partial page must not masquerade as a result

    def test_total_mismatch_is_incomplete(self):
        fake = FakeMultica()
        fake.issues = [make_issue(f"ACM-{1000 + i}", "blocked") for i in range(150)]

        def shrinker(argv):
            args = argv[1:]
            if "--offset" in args and args[args.index("--offset") + 1] == "100" \
                    and args[args.index("--status") + 1] == "blocked":
                return subprocess.CompletedProcess(
                    argv, 0, json.dumps({"issues": [], "has_more": False,
                                         "limit": 100, "offset": 100, "total": 150}), "")
            return None

        fake.fail_handler = shrinker
        issues, errors, _ = discover(make_cli(fake))
        self.assertTrue(any("total" in e for e in errors))


class Cache(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMultica()
        self.cli = make_cli(self.fake)
        self.state = initial_state()

    def _one_stale_progress_issue(self):
        issue = make_issue("ACM-11", "in_progress",
                           updated_at=BASE_NOW - timedelta(minutes=65),
                           last_activity_at=BASE_NOW - timedelta(minutes=65))
        self.fake.issues = [issue]
        self.fake.timelines[issue["id"]] = []
        return issue

    def test_cache_hit_skips_deep_check(self):
        issue = self._one_stale_progress_issue()
        first = run_scan(self.cli, self.state, True, BASE_NOW, self.fake.monotonic)
        self.assertTrue(first["coverage"]["complete"])
        timelines_before = sum(1 for c in self.fake.calls if "timeline" in c)
        second = run_scan(self.cli, self.state, False, BASE_NOW, self.fake.monotonic)
        timelines_after = sum(1 for c in self.fake.calls if "timeline" in c)
        self.assertEqual(timelines_before, timelines_after)
        self.assertEqual(second["per_issue"][issue["id"]]["source"], "cache+active")

    def test_unchanged_issue_crossing_threshold_is_flagged(self):
        # last_progress 90min ago at T: below the 2h bar. next_due = T+30min.
        # At T+61min with the issue byte-identical, the cached entry must expire
        # on time and the finding must appear — without a full scan.
        issue = make_issue("ACM-12", "in_progress",
                           updated_at=BASE_NOW - timedelta(minutes=90),
                           last_activity_at=BASE_NOW - timedelta(minutes=90))
        self.fake.issues = [issue]
        self.fake.add_comment(issue["id"], {
            "id": "c1", "content": "work note", "author_type": "member",
            "author_id": "u1", "created_at": (BASE_NOW - timedelta(minutes=90)).isoformat(),
            "type": "comment"})
        first = run_scan(self.cli, self.state, True, BASE_NOW, self.fake.monotonic)
        self.assertEqual(first["per_issue"][issue["id"]]["findings"], [])
        later = BASE_NOW + timedelta(minutes=61)
        self.fake.now = later
        second = run_scan(self.cli, self.state, False, later, self.fake.monotonic)
        reasons = {f["reason"] for f in second["per_issue"][issue["id"]]["findings"]}
        self.assertIn("in_progress_no_active_run", reasons)

    def test_fingerprint_change_triggers_deep(self):
        issue = self._one_stale_progress_issue()
        run_scan(self.cli, self.state, True, BASE_NOW, self.fake.monotonic)
        issue["updated_at"] = BASE_NOW.isoformat()
        second = run_scan(self.cli, self.state, False, BASE_NOW, self.fake.monotonic)
        self.assertEqual(second["per_issue"][issue["id"]]["source"], "deep")

    def test_stale_cache_entries_dropped(self):
        issue = self._one_stale_progress_issue()
        run_scan(self.cli, self.state, True, BASE_NOW, self.fake.monotonic)
        self.fake.issues = []
        second = run_scan(self.cli, self.state, False, BASE_NOW, self.fake.monotonic)
        self.assertNotIn(issue["id"], self.state["cache"])
        self.assertEqual(second["coverage"]["issues_discovered"], 0)

    def test_cached_assigned_issue_new_failed_streak_flagged(self):
        """ACM-290-class regression: an unchanged fingerprint must not hide a
        new failed-run streak on a cached assigned issue."""
        issue = make_issue("ACM-91", "in_review",
                           updated_at=BASE_NOW - timedelta(hours=3),
                           last_activity_at=BASE_NOW - timedelta(hours=3))
        self.fake.issues = [issue]
        self.fake.runs[issue["id"]] = [
            run("completed", rid="r-old", completed_at=BASE_NOW - timedelta(days=1))]
        from tests.fakes import status_changed
        self.fake.timelines[issue["id"]] = [
            status_changed("in_review", BASE_NOW - timedelta(hours=1))]
        first = run_scan(self.cli, self.state, True, BASE_NOW, self.fake.monotonic)
        self.assertNotIn(
            "run_failed_no_retry",
            {f["reason"] for f in first["per_issue"][issue["id"]]["findings"]})
        # Same fingerprint, but a failed streak appears in the runs list.
        self.fake.runs[issue["id"]] = [
            run("failed", rid="r-f1", completed_at=BASE_NOW - timedelta(minutes=30)),
            run("failed", rid="r-f2", completed_at=BASE_NOW - timedelta(minutes=40)),
            run("completed", rid="r-old", completed_at=BASE_NOW - timedelta(days=1))]
        second = run_scan(self.cli, self.state, False, BASE_NOW, self.fake.monotonic)
        reasons = {f["reason"] for f in second["per_issue"][issue["id"]]["findings"]}
        self.assertIn("run_failed_no_retry", reasons)
        self.assertEqual(second["per_issue"][issue["id"]]["source"], "deep")

    def test_cached_flagged_issue_cleared_by_new_active_run(self):
        issue = make_issue("ACM-92", "in_progress",
                           updated_at=BASE_NOW - timedelta(hours=3),
                           last_activity_at=BASE_NOW - timedelta(hours=3))
        self.fake.issues = [issue]
        self.fake.runs[issue["id"]] = [
            run("failed", rid="r-f1", completed_at=BASE_NOW - timedelta(minutes=30)),
            run("failed", rid="r-f2", completed_at=BASE_NOW - timedelta(minutes=40))]
        first = run_scan(self.cli, self.state, True, BASE_NOW, self.fake.monotonic)
        self.assertIn("run_failed_no_retry",
                      {f["reason"] for f in first["per_issue"][issue["id"]]["findings"]})
        self.fake.runs[issue["id"]] = [
            run("queued", rid="r-q")] + self.fake.runs[issue["id"]]
        second = run_scan(self.cli, self.state, False, BASE_NOW, self.fake.monotonic)
        self.assertEqual(second["per_issue"][issue["id"]]["findings"], [])
        self.assertEqual(second["per_issue"][issue["id"]]["source"], "deep")

    def test_assigned_todo_trailing_failures_flagged(self):
        issue = make_issue("ACM-93", "todo")
        self.fake.issues = [issue]
        self.fake.runs[issue["id"]] = [
            run("failed", rid="r-f1", completed_at=BASE_NOW - timedelta(minutes=30)),
            run("failed", rid="r-f2", completed_at=BASE_NOW - timedelta(minutes=40))]
        result = run_scan(self.cli, self.state, True, BASE_NOW, self.fake.monotonic)
        self.assertIn("run_failed_no_retry",
                      {f["reason"] for f in result["per_issue"][issue["id"]]["findings"]})

    def test_incremental_light_refresh_within_budget(self):
        # 44 assigned issues, unchanged fingerprints: all go through the light
        # path; zero monotonic drift => comfortably inside the budget.
        self.fake.issues = [make_issue(f"ACM-{3000 + i}", "in_review")
                            for i in range(44)]
        run_scan(self.cli, self.state, True, BASE_NOW, self.fake.monotonic)
        self.fake.mono_step = 0.5
        second = run_scan(self.cli, self.state, False, BASE_NOW, self.fake.monotonic)
        self.assertTrue(second["coverage"]["complete"])
        self.assertEqual(second["coverage"]["from_cache"], 44)
        self.assertEqual(second["coverage"]["deep_checked"], 0)

    def test_budget_deadline_marks_incomplete(self):
        self.fake.issues = [make_issue(f"ACM-{2000 + i}", "in_progress") for i in range(5)]
        self.fake.mono_step = 600.0  # every monotonic() call jumps past the budget
        result = run_scan(self.cli, self.state, False, BASE_NOW, self.fake.monotonic)
        self.assertTrue(result["coverage"]["deadline_hit"])
        self.assertFalse(result["coverage"]["complete"])
        self.assertTrue(any("deferred_by_budget" in e for e in result["coverage"]["errors"]))


if __name__ == "__main__":
    unittest.main()
