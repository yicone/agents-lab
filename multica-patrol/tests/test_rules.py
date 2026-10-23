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


    def test_unwoken_handoff_pure_boundaries(self):
        lc = {"id": "lc1", "author_type": "agent", "author_id": "a1",
              "created_at": iso(BASE_NOW - timedelta(minutes=16)),
              "has_valid_mention": False}
        issue = make_issue("ACM-8", "in_review")
        for entered_min, expected in ((14, False), (15, True)):
            f = facts(entered_status_at=iso(BASE_NOW - timedelta(minutes=entered_min)),
                      last_comment=lc, runs=[])
            self.assertEqual("unwoken_handoff" in reasons(rules.evaluate(issue, f, BASE_NOW)),
                             expected, entered_min)
        f = facts(entered_status_at=iso(BASE_NOW - timedelta(minutes=60)), last_comment=lc)
        self.assertIn("unwoken_handoff", reasons(rules.evaluate(issue, f, BASE_NOW)))
        # comment younger than grace does not fire
        lc2 = dict(lc, created_at=iso(BASE_NOW - timedelta(minutes=14)))
        f = facts(entered_status_at=iso(BASE_NOW - timedelta(minutes=60)), last_comment=lc2)
        self.assertNotIn("unwoken_handoff", reasons(rules.evaluate(issue, f, BASE_NOW)))
        # valid mention / member author / wrong status never fire
        f = facts(entered_status_at=iso(BASE_NOW - timedelta(minutes=60)),
                  last_comment=dict(lc, has_valid_mention=True))
        self.assertNotIn("unwoken_handoff", reasons(rules.evaluate(issue, f, BASE_NOW)))
        f = facts(entered_status_at=iso(BASE_NOW - timedelta(minutes=60)),
                  last_comment=dict(lc, author_type="member"))
        self.assertNotIn("unwoken_handoff", reasons(rules.evaluate(issue, f, BASE_NOW)))
        # a run started after the comment suppresses
        f = facts(entered_status_at=iso(BASE_NOW - timedelta(minutes=60)),
                  last_comment=lc,
                  runs=[{"id": "r", "status": "queued",
                         "started_at": iso(BASE_NOW - timedelta(minutes=10))}])
        self.assertNotIn("unwoken_handoff", reasons(rules.evaluate(issue, f, BASE_NOW)))


class DispatchNoWake(unittest.TestCase):
    """Pure boundaries for rule 10 (dispatch comment without wake)."""

    def _lc(self, **kw):
        lc = {"id": "lc1", "author_type": "agent",
              "author_id": config.DISPATCHER_AGENT_ID,
              "created_at": iso(BASE_NOW - timedelta(minutes=16)),
              "has_valid_mention": False, "mentioned_agent_ids": [],
              "dispatch_class": True, "first_line_target_name": None}
        lc.update(kw)
        return lc

    def _issue_facts(self, lc, runs=None, assignee="a1"):
        issue = make_issue("ACM-9", "in_progress", assignee_id=assignee)
        f = facts(last_comment=lc, runs=runs or [])
        return issue, f

    def test_fires_and_age_boundary(self):
        for mins, expected in ((14, False), (15, True), (16, True)):
            issue, f = self._issue_facts(
                self._lc(created_at=iso(BASE_NOW - timedelta(minutes=mins))))
            got = reasons(rules.evaluate(issue, f, BASE_NOW))
            self.assertEqual("dispatch_no_wake" in got, expected, mins)

    def test_gates(self):
        # wrong status / unassigned / non-dispatcher / non-dispatch-class
        issue, f = self._issue_facts(self._lc())
        self.assertIn("dispatch_no_wake", reasons(rules.evaluate(issue, f, BASE_NOW)))
        for status in ("in_review", "blocked", "todo", "done"):
            issue2 = make_issue("ACM-9", status, assignee_id="a1")
            self.assertNotIn("dispatch_no_wake",
                             reasons(rules.evaluate(issue2, f, BASE_NOW)), status)
        issue2 = make_issue("ACM-9", "in_progress", assignee_id=None)
        self.assertNotIn("dispatch_no_wake",
                         reasons(rules.evaluate(issue2, f, BASE_NOW)))
        for lc in (self._lc(author_type="member", author_id="u1"),
                   self._lc(author_id="agent-other"),
                   self._lc(dispatch_class=False)):
            issue2, f2 = self._issue_facts(lc)
            self.assertNotIn("dispatch_no_wake",
                             reasons(rules.evaluate(issue2, f2, BASE_NOW)))

    def test_mention_of_assignee_suppresses(self):
        uuid_a = "11111111-2222-3333-4444-555555555555"
        issue, f = self._issue_facts(
            self._lc(mentioned_agent_ids=[uuid_a.lower()]), assignee=uuid_a)
        self.assertNotIn("dispatch_no_wake", reasons(rules.evaluate(issue, f, BASE_NOW)))
        # a valid mention of some OTHER agent does not suppress
        issue, f = self._issue_facts(
            self._lc(mentioned_agent_ids=["aaaaaaaa-2222-3333-4444-555555555555"]),
            assignee=uuid_a)
        self.assertIn("dispatch_no_wake", reasons(rules.evaluate(issue, f, BASE_NOW)))

    def test_run_gates(self):
        comment_at = BASE_NOW - timedelta(minutes=16)
        later = iso(comment_at + timedelta(minutes=5))
        before = iso(comment_at - timedelta(minutes=5))
        for r, expected in (
                ({"id": "r1", "status": "queued", "agent_id": "a1",
                  "started_at": later}, False),
                ({"id": "r2", "status": "cancelled", "agent_id": "a1",
                  "started_at": later}, False),
                ({"id": "r3", "status": "running", "agent_id": None,
                  "started_at": later}, False),
                ({"id": "r4", "status": "completed", "agent_id": "a1",
                  "started_at": before, "completed_at": before}, True),
                ({"id": "r5", "status": "completed", "agent_id": "other",
                  "started_at": later}, True)):
            issue, f = self._issue_facts(self._lc(), runs=[r])
            got = reasons(rules.evaluate(issue, f, BASE_NOW))
            self.assertEqual("dispatch_no_wake" in got, expected, r)


class DispatchClassRegex(unittest.TestCase):
    """Pin which sample strings the dispatch-class heuristic matches."""

    def test_matches(self):
        for s in ("派活给 WorkerAgent：实现 X",
                  "请按原计划执行", "批准后开工", "批准执行",
                  "返工：上次交付不合格", "请复验", "重验通过后再关单",
                  "重新验收标准如下", "需要补改三处", "请现在开工"):
            self.assertTrue(config.DISPATCH_CLASS_RE.search(s), s)

    def test_non_matches(self):
        for s in ("已收件。ACM-343 保持 in_review，待你回来定稿。",
                  "状态更新：本轮无异常",
                  "delivery complete, awaiting review",
                  "[Error] provider timeout",
                  "summary of findings"):
            self.assertFalse(config.DISPATCH_CLASS_RE.search(s), s)


class Exemptions(unittest.TestCase):
    def test_persistent(self):
        issue = make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID)
        f = facts(last_progress_at=iso(BASE_NOW - timedelta(hours=5)))
        findings = rules.exemption_for(issue, rules.evaluate(issue, f, BASE_NOW), f, BASE_NOW)
        self.assertTrue(findings)
        self.assertTrue(all(x["exempt"]["kind"] == "persistent" for x in findings))

    def test_shelved_label_not_an_exemption(self):
        # Shelving moved to a fetch-stage label filter (scan.is_shelved); the
        # rules layer no longer special-cases identifiers.
        issue = make_issue("ACM-60", "blocked")
        f = facts(entered_status_at=iso(BASE_NOW - timedelta(days=5)))
        findings = rules.exemption_for(issue, rules.evaluate(issue, f, BASE_NOW), f, BASE_NOW)
        self.assertTrue(all(x["exempt"] is None for x in findings))

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
