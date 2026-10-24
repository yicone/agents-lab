"""ACM-435/438 regression replay. Historical evidence, not live-run acceptance."""
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from patrol import config, facts, rules, scan, unwoken
from patrol.cli import PatrolCLI
from patrol.state import Store
from tests.fakes import FakeMultica, make_issue, run, comment, invoke, last_result, iso

NOW = datetime(2026, 10, 24, 2, 1, tzinfo=timezone.utc)
BUILDER = "5305f7b9-52b7-4aa3-9c6d-180f3490c50d"
REVIEWER = "reviewer-fixture"


class RecoveryReplay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fake = FakeMultica()
        self.fake.now = NOW
        self.fake.agents.append({"id": REVIEWER, "name": "Reviewer · Hermes"})
        self.cli = PatrolCLI(frozenset(), _exec=self.fake.exec, _sleep=lambda _: None)
        self.state = {"agent_names": {REVIEWER: "Reviewer · Hermes"}}

    def collect(self, issue):
        f = facts.collect_light_facts(self.cli, issue, NOW)
        findings = rules.evaluate(issue, f, NOW)
        return f, findings

    def add(self, number, status="todo", owner=BUILDER):
        i = make_issue(f"ACM-{number}", status, assignee_id=owner,
                       created_at=NOW - timedelta(hours=6),
                       updated_at=NOW - timedelta(hours=6))
        self.fake.issues.append(i)
        return i

    def quota(self, completed=False):
        i = self.add(409, "in_progress")
        r = run("completed" if completed else "failed",
                rid="01a16a87-48c1-76b4-b1f0-ca50ec2d0376", agent_id=BUILDER,
                attempt=1, max_attempts=2,
                started_at=datetime(2026, 10, 23, 20, 50, 13, tzinfo=timezone.utc),
                completed_at=datetime(2026, 10, 23, 20, 53, 54, tzinfo=timezone.utc))
        r["error"] = "You've hit your usage limit. try again at 6:43 AM."
        r["failure_reason"] = "agent_error.provider_quota_limit"
        if completed:
            r["error"] = None
            self.fake.add_comment(i["id"], comment(
                "[Error] usage limit; try again at 6:43 AM.",
                datetime(2026, 10, 23, 20, 53, 53, tzinfo=timezone.utc),
                author_type="agent", author_id=BUILDER, cid="quota-comment"))
        self.fake.runs[i["id"]] = [r]
        return i

    def process(self, i, dry=False, state=None):
        f, findings = self.collect(i)
        return unwoken.process_unwoken(Store(self.root), self.cli, state or self.state,
                                      {i["id"]: {"issue": i, "facts": f, "findings": findings}},
                                      NOW, dry, lambda _: None)

    def test_acm374_assigned_without_task(self):
        i = self.add(374, owner=REVIEWER)
        f, findings = self.collect(i)
        self.assertEqual([x["reason"] for x in findings], ["assigned_no_task"])
        self.assertFalse(self.fake.wakeups)  # finding, never mechanical dispatch

    def test_assignment_age_and_partial_evidence(self):
        i = self.add(374)
        f, _ = self.collect(i)
        f["assigned_at"] = iso(NOW - timedelta(minutes=14))
        self.assertNotIn("assigned_no_task", {x["reason"] for x in rules.evaluate(i, f, NOW)})
        f["assigned_at"] = iso(NOW - timedelta(minutes=15))
        self.assertIn("assigned_no_task", {x["reason"] for x in rules.evaluate(i, f, NOW)})
        f["evidence_partial"] = True
        self.assertNotIn("assigned_no_task", {x["reason"] for x in rules.evaluate(i, f, NOW)})

    def test_queued_is_distinct_and_not_dispatched_again(self):
        i = self.add(374)
        q = run("queued", agent_id=BUILDER)
        q["started_at"] = None
        q["created_at"] = iso(NOW - timedelta(minutes=15))
        self.fake.runs[i["id"]] = [q]
        _, findings = self.collect(i)
        self.assertEqual([x["reason"] for x in findings], ["queued_unclaimed"])
        self.assertEqual(self.process(i).sent, 0)

    def test_acm422_delivery_in_todo_is_not_missing_task(self):
        i = self.add(422)
        start = datetime(2026, 10, 23, 22, 52, 45, tzinfo=timezone.utc)
        end = datetime(2026, 10, 23, 22, 56, 38, tzinfo=timezone.utc)
        self.fake.runs[i["id"]] = [run("completed", agent_id=BUILDER,
            rid="01a16af7-770e-7e16-af9d-e4d1f50d930a", started_at=start, completed_at=end)]
        self.fake.add_comment(i["id"], comment("READY：已交付，只读不改状态。",
            end - timedelta(seconds=8), author_type="agent", author_id=BUILDER,
            cid="01a16afa-e576-7571-b0d0-aa2a42e9c49f"))
        f, findings = self.collect(i)
        self.assertEqual([x["reason"] for x in findings], ["delivery_status_not_updated"])
        self.assertEqual(f["delivery"]["comment_id"], "01a16afa-e576-7571-b0d0-aa2a42e9c49f")
        self.assertEqual(self.process(i).sent, 0)

    def test_error_comment_is_not_delivery(self):
        i = self.quota(completed=True)
        f, findings = self.collect(i)
        self.assertIsNone(f["delivery"])
        self.assertIn("quota_wait", {x["reason"] for x in findings})
        self.assertNotIn("completed_provider_error", {x["reason"] for x in findings})

    def test_acm409_single_failure_attempt1_is_detected(self):
        i = self.quota()
        f, findings = self.collect(i)
        self.assertEqual(f["runs"][0]["error_kind"], "quota")
        self.assertEqual(f["runs"][0]["recheck_at"], "2026-10-24T06:43:00+08:00")
        self.assertIn("run_failed_no_retry", {x["reason"] for x in findings})
        self.assertIn("quota_wait", {x["reason"] for x in findings})

    def test_quota_wait_binds_time_agent_and_run_once(self):
        i = self.quota()
        self.assertEqual(self.process(i).sent, 1)
        rec = self.state["unwoken"][f"quota:{i['id']}:01a16a87-48c1-76b4-b1f0-ca50ec2d0376"]
        self.assertEqual(rec["target_id"], BUILDER)
        self.assertEqual(rec["recheck_at"], iso(NOW + timedelta(minutes=1)))  # already past hint
        instruction = self.fake.wakeups[i["id"]][0]["instruction"]
        for text in (BUILDER, rec["comment_id"], rec["recheck_at"], "新鲜额度", "最新", "负责人", "活动任务", "恢复一次", "无法确认"):
            self.assertIn(text, instruction)
        self.assertEqual(self.process(i).sent, 0)
        # A fired once-wakeup disappears; the same failure must never re-arm next day.
        self.fake.wakeups.clear()
        later = NOW + timedelta(days=2)
        f, findings = self.collect(i)
        result = unwoken.process_unwoken(Store(self.root), self.cli, self.state,
            {i["id"]: {"issue": i, "facts": f, "findings": findings}}, later, False, lambda _: None)
        self.assertEqual(result.sent, 0)

    def test_unknown_reset_routes_once_to_dispatcher(self):
        i = self.quota()
        self.fake.runs[i["id"]][0]["error"] = "usage limit"
        self.process(i)
        rec = next(iter(self.state["unwoken"].values()))
        self.assertEqual(rec["recheck_source"], "unknown_dispatcher_judgement")
        self.assertEqual(rec["recheck_at"], iso(NOW + timedelta(minutes=1)))

    def test_future_hint_uses_at_flag(self):
        i = self.quota()
        self.fake.runs[i["id"]][0]["reset_at"] = iso(NOW + timedelta(hours=2))
        self.process(i)
        call = next(c for c in self.fake.calls if c[1:4] == ["issue", "wakeup", "create"])
        self.assertEqual(call[call.index("--at") + 1], iso(NOW + timedelta(hours=2)))
        self.assertNotIn("--every", call)

    def test_completed_quota_uses_wait_not_retry(self):
        i = self.quota(completed=True)
        self.assertEqual(self.process(i).sent, 1)
        self.assertEqual(self.fake.comment_add_count, 0)

    def test_failure_classes_do_not_all_become_quota(self):
        for text, reason in (("Selected model is at capacity", "provider_capacity"),
                             ("An error occurred while processing your request", "agent_error"),
                             ("authentication failed", "agent_error"),
                             ("timeout", "agent_error")):
            i = self.quota()
            self.fake.runs[i["id"]][0].update(error=text, failure_reason=reason)
            _, findings = self.collect(i)
            self.assertNotIn("quota_wait", {x["reason"] for x in findings}, text)
            self.assertIn("run_failed_no_retry", {x["reason"] for x in findings}, text)
            self.fake.issues.clear()

    def test_successor_or_active_task_cancels_quota_candidate(self):
        for status in ("queued", "running", "completed"):
            i = self.quota()
            newer = run(status, rid="successor", agent_id=BUILDER,
                        started_at=NOW - timedelta(minutes=1), completed_at=NOW)
            self.fake.runs[i["id"]].insert(0, newer)
            self.assertEqual(self.process(i).sent, 0)
            self.fake.issues.clear()

    def cross_handoff(self, number, child, sentence):
        i = self.add(number, "in_progress")
        c = comment(sentence, NOW - timedelta(hours=1), author_type="agent",
                    author_id=config.DISPATCHER_AGENT_ID, cid=f"source-{number}")
        self.fake.add_comment(i["id"], c)
        target = self.add(child, "todo", REVIEWER)
        self.fake.runs[target["id"]] = [run("running", agent_id=REVIEWER,
                                          started_at=NOW - timedelta(minutes=50))]
        return i, target

    def test_acm378_and_405_cross_issue_replays(self):
        for number, child, sentence in (
            (378, 411, "不需要你做，下一步由 Reviewer · Hermes 在 ACM-411 复验提交 5ac1445。\n\nACM-378 保持 in_progress。"),
            (405, 406, "不需要你做，下一步由 Reviewer · Hermes 做非作者验收：验收单 ACM-406 已建立并已启动。")):
            i, target = self.cross_handoff(number, child, sentence)
            lc = facts._last_comment(self.fake.comments[i["id"]])
            self.assertEqual(lc["first_line_target_name"], "Reviewer · Hermes")
            self.assertEqual(lc["target_issue_ref"], target["identifier"])
            result = self.process(i)
            self.assertEqual(result.sent, 0)
            self.assertEqual(result.actions[0]["action"], "unwoken_skipped:run_started")
            self.assertEqual(self.process(i, dry=True).would_send, 0)

    def test_cross_issue_missing_run_schedules_judgement(self):
        i, target = self.cross_handoff(378, 411,
            "不需要你做，下一步由 Reviewer · Hermes 在 ACM-411 复验提交 5ac1445。\n\nACM-378 保持 in_progress。")
        self.fake.runs[target["id"]] = []
        self.assertEqual(self.process(i).sent, 1)

    def test_unknown_target_never_falls_back_to_owner(self):
        i = self.add(378, "in_progress")
        self.fake.add_comment(i["id"], comment("需要你做：安排复验。",
            NOW - timedelta(hours=1), author_type="agent", author_id=config.DISPATCHER_AGENT_ID))
        self.assertIsNone(unwoken._resolve_target(self.state, i, {}))
        result = self.process(i)
        self.assertEqual(result.sent, 0)
        self.assertEqual(result.actions[0]["action"], "unknown_target")

    def test_wakeup_failure_is_unhealthy_even_when_scan_complete(self):
        i = self.quota()
        self.fake.fail_handler = lambda argv: (
            subprocess.CompletedProcess(argv, 1, "", "wakeup creation denied")
            if argv[1:4] == ["issue", "wakeup", "create"] else None)
        code, out, err = invoke(self.fake, ["run", "--slot", "2026102410"],
                                state_dir=self.root, now=NOW)
        result = last_result(out)
        self.assertEqual(code, 2)
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["scheduling"], {"errors": 1, "complete": False})
        self.assertGreater(result["self_failures_consecutive"], 0)
        self.assertIn("denied", out)

    def test_quota_dry_run_zero_writes(self):
        i = self.quota()
        result = self.process(i, dry=True)
        self.assertEqual(result.would_send, 1)
        self.assertEqual(self.cli.writes_attempted, 0)
        self.assertFalse(self.fake.wakeups)
        self.assertEqual(self.state.get("unwoken"), {})

    def test_refresh_guards_reject_reassigned_owner(self):
        i = self.quota()
        f, findings = self.collect(i)
        i["assignee_id"] = REVIEWER
        result = unwoken.process_unwoken(Store(self.root), self.cli, self.state,
            {i["id"]: {"issue": i, "facts": f, "findings": findings}}, NOW, False, lambda _: None)
        self.assertEqual(result.sent, 0)
        self.assertEqual(result.actions[0]["action"], "unwoken_skipped:assignee_changed")

    def test_pending_quota_timeout_reconciles_without_duplicate(self):
        i = self.quota()
        original = self.fake.exec
        did_timeout = []
        def timeout_after_create(argv, **kwargs):
            response = original(argv, **kwargs)
            if argv[1:4] == ["issue", "wakeup", "create"] and not did_timeout:
                did_timeout.append(True)
                raise subprocess.TimeoutExpired(argv, 90)
            return response
        self.cli._exec = timeout_after_create
        self.assertEqual(self.process(i).sent, 0)
        self.assertEqual(self.process(i).sent, 1)
        self.assertEqual(len(self.fake.wakeups[i["id"]]), 1)

    def test_pending_quota_reassignment_never_creates(self):
        i = self.quota()
        self.fake.fail_handler = lambda argv: (
            subprocess.CompletedProcess(argv, 1, "", "denied")
            if argv[1:4] == ["issue", "wakeup", "create"] else None)
        self.process(i)
        self.fake.fail_handler = None
        i["assignee_id"] = REVIEWER
        self.assertEqual(self.process(i).sent, 0)
        self.assertFalse(self.fake.wakeups)

    def test_latest_followup_prevents_obsolete_quota_wait(self):
        i = self.quota()
        self.fake.add_comment(i["id"], comment("已完成交付，请审阅。",
            NOW - timedelta(minutes=1), author_type="agent", author_id=BUILDER))
        self.assertEqual(self.process(i).sent, 0)

    def test_no_wake_false_positive_not_written_to_ledger(self):
        self.fake.issues.append(make_issue("ACM-229", "in_progress",
            uid=config.LEDGER_ISSUE_ID, assignee_id=None))
        i, target = self.cross_handoff(378, 411,
            "不需要你做，下一步由 Reviewer · Hermes 在 ACM-411 复验。")
        code, out, err = invoke(self.fake, ["run", "--slot", "2026102410"],
                               state_dir=self.root, now=NOW)
        ledger = self.fake.ledger_comments()[0]["content"]
        self.assertNotIn("dispatch_no_wake", ledger.split("## 候选异常")[1].split("##")[0])
        self.assertEqual(last_result(out)["unwoken_sent"], 0)

    def test_clock_hint_rollover_and_unknown(self):
        self.assertIsNone(facts.quota_recheck_at({"error": "usage limit"}))
        self.assertIsNone(facts.quota_recheck_at({"completed_at": iso(NOW), "error": "try again at 29:99 AM"}))
        reset = facts.quota_recheck_at({"completed_at": "2026-10-24T23:00:00+08:00",
                                       "error": "try again at 6:43 AM"})
        self.assertEqual(reset, "2026-10-25T06:43:00+08:00")


if __name__ == "__main__":
    unittest.main()
