"""Boundary tests for the pure rules. All data are synthetic test fixtures."""

from __future__ import annotations

import unittest
from datetime import timedelta

from patrol import config, rules
from tests.fakes import BASE_NOW, iso, make_issue, run


def facts(**kw):
    base = {
        "entered_status_at": iso(BASE_NOW - timedelta(hours=1)),
        "last_progress_at": iso(BASE_NOW - timedelta(hours=1)),
        "approval_wait_at": None,
        "evidence_partial": False,
        "active_runs": [],
        "runs": [],
        "wakeups": [],
        "checked_at": iso(BASE_NOW),
    }
    base.update(kw)
    return base


def reasons(findings):
    return {f["reason"] for f in findings}


class RuleBoundaries(unittest.TestCase):
    def test_no_active_run_threshold(self):
        issue = make_issue("ACM-1", "in_progress")
        for minutes, expected in ((119, False), (120, True), (121, True)):
            f = facts(last_progress_at=iso(BASE_NOW - timedelta(minutes=minutes)))
            got = reasons(rules.evaluate(issue, f, BASE_NOW))
            self.assertEqual("in_progress_no_active_run" in got, expected, minutes)

    def test_no_active_run_requires_assignee_and_idle(self):
        issue = make_issue("ACM-1", "in_progress", assignee_id=None)
        f = facts(last_progress_at=iso(BASE_NOW - timedelta(hours=3)))
        self.assertNotIn("in_progress_no_active_run", reasons(rules.evaluate(issue, f, BASE_NOW)))
        issue2 = make_issue("ACM-1", "in_progress")
        f2 = facts(last_progress_at=iso(BASE_NOW - timedelta(hours=3)),
                   active_runs=[{"id": "r1", "status": "running"}])
        self.assertNotIn("in_progress_no_active_run", reasons(rules.evaluate(issue2, f2, BASE_NOW)))

    def test_in_review_stale(self):
        issue = make_issue("ACM-2", "in_review")
        for hours, expected in ((23, False), (24, True), (25, True)):
            f = facts(entered_status_at=iso(BASE_NOW - timedelta(hours=hours)))
            self.assertEqual("in_review_stale" in reasons(rules.evaluate(issue, f, BASE_NOW)),
                             expected, hours)

    def test_blocked_stale(self):
        issue = make_issue("ACM-3", "blocked")
        for hours, expected in ((47, False), (48, True), (49, True)):
            f = facts(entered_status_at=iso(BASE_NOW - timedelta(hours=hours)))
            self.assertEqual("blocked_stale" in reasons(rules.evaluate(issue, f, BASE_NOW)),
                             expected, hours)

    def test_approval_not_blocked(self):
        issue = make_issue("ACM-4", "in_progress")
        for minutes, expected in ((59, False), (60, True), (61, True)):
            f = facts(approval_wait_at=iso(BASE_NOW - timedelta(minutes=minutes)))
            self.assertEqual("approval_not_blocked" in reasons(rules.evaluate(issue, f, BASE_NOW)),
                             expected, minutes)
        blocked = make_issue("ACM-4", "blocked")
        f = facts(approval_wait_at=iso(BASE_NOW - timedelta(hours=3)),
                  entered_status_at=iso(BASE_NOW - timedelta(hours=1)))
        self.assertNotIn("approval_not_blocked", reasons(rules.evaluate(blocked, f, BASE_NOW)))

    def test_approved_unassigned(self):
        for status in ("todo", "backlog"):
            issue = make_issue("ACM-5", status, assignee_id=None, labels=["可开工"])
            self.assertIn("approved_unassigned", reasons(rules.evaluate(issue, facts(), BASE_NOW)))
        issue = make_issue("ACM-5", "todo", assignee_id="agent-x", labels=["可开工"])
        self.assertNotIn("approved_unassigned", reasons(rules.evaluate(issue, facts(), BASE_NOW)))
        issue = make_issue("ACM-5", "todo", assignee_id=None, labels=["other"])
        self.assertNotIn("approved_unassigned", reasons(rules.evaluate(issue, facts(), BASE_NOW)))
        issue = make_issue("ACM-5", "in_progress", assignee_id=None, labels=["可开工"],
                           updated_at=BASE_NOW - timedelta(minutes=10))
        f = facts(entered_status_at=iso(BASE_NOW - timedelta(minutes=10)))
        self.assertNotIn("approved_unassigned", reasons(rules.evaluate(issue, f, BASE_NOW)))

    def test_no_assignee_grace(self):
        issue = make_issue("ACM-6", "in_progress", assignee_id=None)
        for minutes, expected in ((29, False), (30, True), (31, True)):
            f = facts(entered_status_at=iso(BASE_NOW - timedelta(minutes=minutes)))
            self.assertEqual("in_progress_no_assignee" in reasons(rules.evaluate(issue, f, BASE_NOW)),
                             expected, minutes)

    # --- ACM-290-class fixture: synthetic failed-run streaks --------------
    def _acm290(self, runs, status="in_review", active=None):
        """Synthetic 'ACM-290 class' fixture: failed runs after an older completed run."""
        issue = make_issue("ACM-290", status)
        f = facts(runs=runs, active_runs=active or [])
        return issue, f

    def test_failed_run_streak_flags(self):
        old = run("completed", rid="r-old", completed_at=BASE_NOW - timedelta(days=1))
        trailing = [run("failed", rid=f"r-f{i}", completed_at=BASE_NOW - timedelta(hours=1))
                    for i in range(3)]
        issue, f = self._acm290(trailing + [old])
        self.assertIn("run_failed_no_retry", reasons(rules.evaluate(issue, f, BASE_NOW)))

    def test_failed_run_with_active_run_not_flagged(self):
        trailing = [run("failed", rid=f"r-f{i}", completed_at=BASE_NOW - timedelta(hours=1))
                    for i in range(3)]
        issue, f = self._acm290(trailing, active=[{"id": "r-q", "status": "queued"}])
        self.assertNotIn("run_failed_no_retry", reasons(rules.evaluate(issue, f, BASE_NOW)))

    def test_failed_then_completed_not_flagged(self):
        runs = [run("completed", rid="r-done", completed_at=BASE_NOW - timedelta(minutes=30)),
                run("failed", rid="r-f", completed_at=BASE_NOW - timedelta(hours=2))]
        issue, f = self._acm290(runs)
        self.assertNotIn("run_failed_no_retry", reasons(rules.evaluate(issue, f, BASE_NOW)))

    def test_single_failed_max_attempts_flags(self):
        runs = [run("failed", rid="r-last", attempt=3, max_attempts=3,
                    completed_at=BASE_NOW - timedelta(hours=1)),
                run("completed", rid="r-old", completed_at=BASE_NOW - timedelta(days=1))]
        issue, f = self._acm290(runs)
        self.assertIn("run_failed_no_retry", reasons(rules.evaluate(issue, f, BASE_NOW)))

    def test_failed_run_grace_period(self):
        runs = [run("failed", rid="r-f1", completed_at=BASE_NOW - timedelta(minutes=5)),
                run("failed", rid="r-f2", completed_at=BASE_NOW - timedelta(minutes=6))]
        issue, f = self._acm290(runs)
        self.assertNotIn("run_failed_no_retry", reasons(rules.evaluate(issue, f, BASE_NOW)))
        runs10 = [run("failed", rid="r-f1", completed_at=BASE_NOW - timedelta(minutes=10)),
                  run("failed", rid="r-f2", completed_at=BASE_NOW - timedelta(minutes=11))]
        issue, f = self._acm290(runs10)
        self.assertIn("run_failed_no_retry", reasons(rules.evaluate(issue, f, BASE_NOW)))

    def test_failed_run_ignores_cancelled_in_streak(self):
        runs = [run("cancelled", rid="r-c", completed_at=BASE_NOW - timedelta(minutes=20)),
                run("failed", rid="r-f1", completed_at=BASE_NOW - timedelta(hours=1)),
                run("failed", rid="r-f2", completed_at=BASE_NOW - timedelta(hours=2))]
        issue, f = self._acm290(runs)
        self.assertIn("run_failed_no_retry", reasons(rules.evaluate(issue, f, BASE_NOW)))

    def test_failed_run_todo_with_assignee(self):
        trailing = [run("failed", rid=f"r-f{i}", completed_at=BASE_NOW - timedelta(hours=1))
                    for i in range(2)]
        issue, f = self._acm290(trailing, status="todo")
        self.assertIn("run_failed_no_retry", reasons(rules.evaluate(issue, f, BASE_NOW)))


class Exemptions(unittest.TestCase):
    def test_persistent(self):
        issue = make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID)
        f = facts(last_progress_at=iso(BASE_NOW - timedelta(hours=5)))
        findings = rules.exemption_for(issue, rules.evaluate(issue, f, BASE_NOW), f, BASE_NOW)
        self.assertTrue(findings)
        self.assertTrue(all(x["exempt"]["kind"] == "persistent" for x in findings))

    def test_shelved(self):
        issue = make_issue("ACM-60", "blocked")
        f = facts(entered_status_at=iso(BASE_NOW - timedelta(days=5)))
        findings = rules.exemption_for(issue, rules.evaluate(issue, f, BASE_NOW), f, BASE_NOW)
        self.assertTrue(all(x["exempt"]["kind"] == "shelved" for x in findings))

    def test_awaiting_wakeup(self):
        issue = make_issue("ACM-7", "in_review")
        f = facts(entered_status_at=iso(BASE_NOW - timedelta(days=2)),
                  wakeups=[{"enabled": True,
                            "expires_at": iso(BASE_NOW + timedelta(hours=5)), "kind": "event"}])
        findings = rules.exemption_for(issue, rules.evaluate(issue, f, BASE_NOW), f, BASE_NOW)
        self.assertEqual(findings[0]["exempt"]["kind"], "awaiting_wakeup")
        # expired wakeup does not exempt
        f2 = facts(entered_status_at=iso(BASE_NOW - timedelta(days=2)),
                   wakeups=[{"enabled": True,
                             "expires_at": iso(BASE_NOW - timedelta(hours=1)), "kind": "event"}])
        findings2 = rules.exemption_for(issue, rules.evaluate(issue, f2, BASE_NOW), f2, BASE_NOW)
        self.assertIsNone(findings2[0]["exempt"])


if __name__ == "__main__":
    unittest.main()
