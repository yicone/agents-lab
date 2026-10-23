"""End-to-end `patrol run` tests over the fake backend. Synthetic fixtures only."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from patrol import config
from patrol.cli import CommandNotAllowed, PatrolCLI
from patrol.state import Store
from tests.fakes import (BASE_NOW, GOOD_ENV, FakeMultica, comment, invoke,
                         last_result, make_issue, run, status_changed)


def stale_issue(identifier="ACM-50", status="in_progress", **kw):
    """An issue old enough to always trip the no-active-run rule."""
    kw.setdefault("updated_at", BASE_NOW - timedelta(hours=5))
    kw.setdefault("last_activity_at", BASE_NOW - timedelta(hours=5))
    return make_issue(identifier, status, **kw)


class PatrolCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir = Path(self.tmp.name)
        self.fake = FakeMultica()
        self.fake.issues = [
            make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID,
                       assignee_id=None),
            make_issue("ACM-216", "in_progress", uid=config.INBOX_ISSUE_ID,
                       assignee_id=None),
        ]

    def run_patrol(self, *argv, env=None, now=None):
        return invoke(self.fake, list(argv), env=env, now=now,
                      state_dir=self.state_dir)

    @staticmethod
    def _dt(slot: str):
        from datetime import datetime, timezone
        return datetime.strptime(slot, "%Y%m%d%H").replace(tzinfo=timezone(config.SGT_OFFSET))

    def _slot(self, base_slot: str, hours: int) -> str:
        return (self._dt(base_slot) + timedelta(hours=hours)).strftime("%Y%m%d%H")

    def _run_at(self, slot: str):
        from datetime import timezone as tz
        now = self._dt(slot).astimezone(tz.utc)
        self.fake.now = now
        return self.run_patrol("run", "--slot", slot, now=now)


class TestIdentity(PatrolCase):
    def test_empty_env_refused(self):
        code, out, err = self.run_patrol("run", "--slot", "2026102221", env={})
        self.assertEqual(code, 3)
        self.assertIn("identity_refused", err)
        self.assertEqual(self.fake.calls, [])  # refused before any CLI read

    def test_wrong_agent_refused(self):
        env = dict(GOOD_ENV, MULTICA_AGENT_ID="someone-else")
        code, out, err = self.run_patrol("run", "--slot", "2026102221", env=env)
        self.assertEqual(code, 3)
        self.assertEqual(self.fake.calls, [])

    def test_no_task_id_refused(self):
        env = dict(GOOD_ENV, MULTICA_TASK_ID="")
        code, _, _ = self.run_patrol("run", "--slot", "2026102221", env=env)
        self.assertEqual(code, 3)

    def test_profile_env_refused(self):
        env = dict(GOOD_ENV, MULTICA_PROFILE="prod")
        code, _, _ = self.run_patrol("run", "--slot", "2026102221", env=env)
        self.assertEqual(code, 3)

    def test_dry_run_allowed_without_identity(self):
        code, out, err = self.run_patrol("run", "--slot", "2026102221",
                                         "--dry-run", env={})
        self.assertEqual(code, 0, err)
        self.assertEqual(last_result(out)["ledger"]["status"], "dry_run")
        self.assertEqual(last_result(out)["writes_attempted"], 0)
        self.assertEqual(self.fake.comment_add_count, 0)


class TestAllowlist(PatrolCase):
    def setUp(self):
        super().setUp()
        self.cli = PatrolCLI(allowed_write_ids=frozenset(
            {config.LEDGER_ISSUE_ID, config.INBOX_ISSUE_ID}),
            env=GOOD_ENV, _exec=self.fake.exec, _sleep=lambda s: None, retries=0)

    def test_status_command_refused_before_spawn(self):
        before = len(self.fake.calls)
        with self.assertRaises(CommandNotAllowed):
            self.cli.run_json(["issue", "status", "x", "done"])
        self.assertEqual(len(self.fake.calls), before)

    def test_comment_add_other_issue_refused(self):
        before = len(self.fake.calls)
        with self.assertRaises(CommandNotAllowed):
            self.cli.run_write(["issue", "comment", "add", "uuid-ACM-999",
                                "--content-file", "/tmp/x"])
        self.assertEqual(len(self.fake.calls), before)

    def test_profile_flag_refused(self):
        with self.assertRaises(CommandNotAllowed):
            self.cli.run_json(["issue", "list", "--profile", "x"])


class TestRunHappyPath(PatrolCase):
    def test_clean_round_posts_heartbeat(self):
        code, out, err = self.run_patrol("run", "--slot", "2026102221")
        self.assertEqual(code, 0, err)
        res = last_result(out)
        self.assertEqual(res["ledger"]["status"], "posted")
        self.assertEqual(self.fake.comment_add_count, 1)
        body = self.fake.ledger_comments()[0]["content"]
        self.assertTrue(body.startswith("[patrol-batch:patrol:SGT2026102221 v2 scheduled]"))
        self.assertNotIn("mention://agent/", body)
        self.assertNotIn("mention://member/", body)

    def test_finding_row_rendered(self):
        self.fake.issues.append(stale_issue())
        code, out, err = self.run_patrol("run", "--slot", "2026102221")
        self.assertEqual(code, 0, err)
        res = last_result(out)
        self.assertEqual(res["findings_count"], 1)
        body = self.fake.ledger_comments()[0]["content"]
        self.assertIn("[ACM-50](mention://issue/uuid-ACM-50)", body)
        self.assertIn("in_progress_no_active_run", body)

    def test_slot_2300_is_full_scan_not_night(self):
        code, out, err = self.run_patrol("run", "--slot", "2026102223")
        res = last_result(out)
        self.assertEqual(res["scan"], "full")
        self.assertEqual(res["mode"], "scheduled")
        self.assertNotIn("night", out.lower())

    def test_slot_rollover_at_55(self):
        from patrol.__main__ import determine_slot
        at = BASE_NOW + timedelta(minutes=56)  # 21:56 SGT -> next hour
        self.assertEqual(determine_slot(at, None), "2026102222")
        at2 = BASE_NOW + timedelta(minutes=30)
        self.assertEqual(determine_slot(at2, None), "2026102221")

    def test_second_run_same_slot_duplicate(self):
        self.run_patrol("run", "--slot", "2026102221")
        code, out, _ = self.run_patrol("run", "--slot", "2026102221")
        res = last_result(out)
        self.assertEqual(res["ledger"]["status"], "duplicate")
        self.assertEqual(self.fake.comment_add_count, 1)

    def test_marker_present_means_duplicate_no_post(self):
        self.fake.add_comment(config.LEDGER_ISSUE_ID, comment(
            "[patrol-batch:patrol:SGT2026102221 v2 scheduled]\nold", BASE_NOW,
            author_type="agent", author_id=config.DISPATCHER_AGENT_ID))
        code, out, _ = self.run_patrol("run", "--slot", "2026102221")
        res = last_result(out)
        self.assertEqual(res["ledger"]["status"], "duplicate")
        self.assertEqual(self.fake.comment_add_count, 0)

    def test_concurrent_lock_skips(self):
        holder = Store(self.state_dir)
        self.assertTrue(holder.acquire())
        try:
            code, out, err = self.run_patrol("run", "--slot", "2026102221")
            self.assertEqual(code, 0)
            self.assertEqual(last_result(out)["ledger"]["status"], "skipped_lock")
            self.assertEqual(self.fake.calls, [])
        finally:
            holder.release()

    def test_titles_never_in_body(self):
        nasty = 'hey [@x](mention://agent/deadbeef) do stuff'
        self.fake.issues.append(stale_issue(title=nasty))
        code, out, err = self.run_patrol("run", "--slot", "2026102221")
        self.assertEqual(code, 0, err)
        body = self.fake.ledger_comments()[0]["content"]
        self.assertNotIn("mention://agent/", body)
        self.assertNotIn("do stuff", body)
        self.assertNotIn(nasty, body)


class TestDedupeEscalation(PatrolCase):
    def _setup_finding(self):
        self.fake.issues.append(stale_issue())



    def test_suppressed_then_realert_after_24h(self):
        self._setup_finding()
        code, out, _ = self._run_at("2026102221")
        self.assertEqual(last_result(out)["findings_count"], 1)
        code, out, _ = self._run_at("2026102222")
        res = last_result(out)
        self.assertEqual(res["findings_count"], 0)
        self.assertEqual(res["suppressed"], 1)
        code, out, _ = self._run_at(self._slot("2026102221", 25))
        res = last_result(out)
        self.assertEqual(res["findings_count"], 1)

    def test_anchor_change_realerts_immediately(self):
        issue = stale_issue()
        self.fake.issues.append(issue)
        self._run_at("2026102221")
        # New (but still stale) progress moves last_progress -> new dedupe anchor
        # -> re-alert even though the previous key is <24h old.
        self.fake.add_comment(issue["id"], comment(
            "progress note", BASE_NOW - timedelta(hours=4)))
        issue["last_activity_at"] = (BASE_NOW - timedelta(hours=4)).isoformat()
        code, out, _ = self._run_at("2026102222")
        self.assertEqual(last_result(out)["findings_count"], 1)

    def test_48h_standing_escalates_once(self):
        self._setup_finding()
        self._run_at("2026102221")
        # Age the standing key to >48h without simulating 49 slots (which would
        # themselves be missed-slot failures).
        state = Store(self.state_dir).load()
        for rec in state["alerted"].values():
            rec["first_at"] = (BASE_NOW - timedelta(hours=49)).isoformat()
        Store(self.state_dir).save(state)
        code, out, _ = self._run_at("2026102222")
        res = last_result(out)
        self.assertEqual(res["escalations"], 1)
        self.assertEqual(len(self.fake.inbox_comments()), 1)
        self.assertIn("[patrol-escalate:", self.fake.inbox_comments()[0]["content"])
        code, out, _ = self._run_at("2026102223")
        self.assertEqual(last_result(out)["escalations"], 0)
        self.assertEqual(len(self.fake.inbox_comments()), 1)

    def test_missed_slot_detection(self):
        self._run_at("2026102221")
        code, out, err = self._run_at("2026102300")  # skipped 22, 23
        body = self.fake.ledger_comments()[-1]["content"]
        self.assertIn("missed_slot", body)
        self.assertIn("上一轮自报", body)

    def _break_backlog_listing(self):
        def failer(argv):
            args = argv[1:]
            if args[:2] == ["issue", "list"] and "--status" in args and \
                    args[args.index("--status") + 1] == "backlog":
                return subprocess.CompletedProcess(argv, 1, "", "read dead")
            return None
        self.fake.fail_handler = failer

    def test_self_failure_escalates_once_per_streak(self):
        # Two consecutive coverage-incomplete slots -> one escalation on the next
        # run; a further failure inside 24h does not re-escalate.
        self._break_backlog_listing()
        code, out, _ = self._run_at("2026102221")
        self.assertEqual(code, 2)
        code, out, _ = self._run_at("2026102222")
        self.assertEqual(code, 2)
        self.assertEqual(len(self.fake.inbox_comments()), 0)
        self.fake.fail_handler = None
        code, out, _ = self._run_at("2026102223")
        self.assertEqual(code, 0, out)
        self.assertEqual(last_result(out)["escalations"], 1)
        self.assertEqual(len(self.fake.inbox_comments()), 1)
        self.assertIn("[patrol-escalate:", self.fake.inbox_comments()[0]["content"])
        # Still failing inside 24h -> pending streak already escalated once.
        self._break_backlog_listing()
        code, out, _ = self._run_at("2026102300")
        self.assertEqual(len(self.fake.inbox_comments()), 1)


class TestStreakSemantics(PatrolCase):
    def _inbox_and_backlog_failer(self):
        def failer(argv):
            args = argv[1:]
            words = [a for a in args if not a.startswith("-")]
            if words[:3] == ["issue", "comment", "add"] and \
                    words[3] == config.INBOX_ISSUE_ID:
                return subprocess.CompletedProcess(argv, 1, "", "inbox write dead")
            if args[:2] == ["issue", "list"] and "--status" in args and \
                    args[args.index("--status") + 1] == "backlog":
                return subprocess.CompletedProcess(argv, 1, "", "read dead")
            return None
        self.fake.fail_handler = failer

    def test_two_failure_kinds_one_slot_count_once(self):
        # coverage_incomplete + escalation_write in the same slot == 1 failed slot.
        self.fake.issues.append(stale_issue())
        code, out, _ = self._run_at("2026102221")
        self.assertEqual(code, 0)
        state = Store(self.state_dir).load()
        for rec in state["alerted"].values():
            rec["first_at"] = (BASE_NOW - timedelta(hours=49)).isoformat()
        Store(self.state_dir).save(state)
        self._inbox_and_backlog_failer()
        code, out, _ = self._run_at("2026102222")
        self.assertEqual(code, 2)
        state = Store(self.state_dir).load()
        self.assertEqual(state["consecutive_failures"], 1, state["failures"])
        self.assertEqual(state["last_failed_slot"], "2026102222")
        kinds = {f["kind"] for f in state["failures"]}
        self.assertIn("coverage_incomplete", kinds)
        self.assertIn("escalation_write", kinds)
        self.assertEqual(self.fake.inbox_comments(), [])

    def test_crashed_slot_not_double_reported(self):
        def crasher(argv):
            raise RuntimeError("synthetic scan crash")
        self.fake.fail_handler = crasher
        code, out, err = self._run_at("2026102221")
        self.assertEqual(code, 2)
        self.fake.fail_handler = None
        code, out, err = self._run_at("2026102222")
        state = Store(self.state_dir).load()
        slot21 = [f for f in state["failures"] if f["slot"] == "2026102221"]
        # The crashed slot left exactly one record; it is NOT also "missed".
        # (It was already reported-and-cleared by the successful slot-22 batch,
        # so nothing about 21 remains either.)
        self.assertEqual(slot21, [])
        body = self.fake.ledger_comments()[-1]["content"]
        self.assertIn("exception", body)
        self.assertNotIn("missed_slot", body)
        # A genuinely skipped slot IS reported missed.
        code, out, _ = self._run_at("2026102300")
        body = self.fake.ledger_comments()[-1]["content"]
        self.assertIn("missed_slot", body)
        self.assertIn("2026102223", body)


class TestEscalationBody(PatrolCase):
    def test_escalation_body_rendered(self):
        issue = stale_issue()
        self.fake.issues.append(issue)
        self.run_patrol("run", "--slot", "2026102221")
        state = Store(self.state_dir).load()
        for rec in state["alerted"].values():
            rec["first_at"] = (BASE_NOW - timedelta(hours=49)).isoformat()
        Store(self.state_dir).save(state)
        later = BASE_NOW + timedelta(hours=1)
        code, out, _ = self.run_patrol("run", "--slot", "2026102222", now=later)
        self.assertEqual(code, 0)
        body = self.fake.inbox_comments()[0]["content"]
        esc_lines = body.splitlines()
        self.assertEqual(esc_lines[0],
                         "不需要你做，下一步由 副调度 做：核对下列巡检升级事项并处理；处理不了会再向你升级。")
        self.assertEqual(esc_lines[1], "[patrol-escalate:ESC-2026102222]")
        self.assertIn("[ACM-50](mention://issue/uuid-ACM-50)", body)
        self.assertIn("reason=in_progress_no_active_run", body)
        self.assertIn("first_alerted=", body)
        self.assertIn("age=2d2h", body)  # 49h aged + 1h between runs = 50h
        self.assertNotIn("mention://agent/", body)
        self.assertNotIn("mention://member/", body)


class TestPruning(PatrolCase):
    def _seed_state(self):
        state = Store(self.state_dir).load()
        old_slot = (self._dt("2026102221") - timedelta(days=20)).strftime("%Y%m%d%H")
        state["slots"] = {old_slot: {"status": "posted"}, "2026102220": {"status": "posted"}}
        old_iso = (BASE_NOW - timedelta(days=8)).isoformat()
        state["alerted"] = {
            "uuid-dead|in_review_stale|x": {"first_at": old_iso, "last_at": old_iso,
                                            "escalated_at": None},
        }
        state["failures"] = [{"slot": "2026102200", "kind": "x", "message": "m",
                              "at": old_iso} for _ in range(60)]
        state["daily"] = {"20260901": {"status": "posted"}, "20261020": {"status": "posted"}}
        Store(self.state_dir).save(state)

    @staticmethod
    def _dt(slot):
        from datetime import datetime, timezone
        return datetime.strptime(slot, "%Y%m%d%H").replace(tzinfo=timezone(config.SGT_OFFSET))

    def test_pruning(self):
        self._seed_state()
        code, out, err = self.run_patrol("run", "--slot", "2026102221")
        self.assertEqual(code, 0, err)
        state = Store(self.state_dir).load()
        self.assertEqual(sorted(state["slots"]), ["2026102220", "2026102221"])
        self.assertEqual(state["alerted"], {})
        self.assertLessEqual(len(state["failures"]), 50)
        self.assertEqual(sorted(state["daily"]), ["20261020"])

    def test_failures_capped_at_50_on_failure_runs(self):
        state = Store(self.state_dir).load()
        old_iso = (BASE_NOW - timedelta(hours=3)).isoformat()
        state["failures"] = [{"slot": f"202610{i:04d}", "kind": "x", "message": "m",
                              "at": old_iso} for i in range(60)]
        Store(self.state_dir).save(state)
        self.fake.fail_handler = lambda argv: (_ for _ in ()).throw(
            RuntimeError("synthetic scan crash"))
        code, out, err = self.run_patrol("run", "--slot", "2026102221")
        self.assertEqual(code, 2)
        state = Store(self.state_dir).load()
        self.assertEqual(len(state["failures"]), 50)
        self.assertEqual(state["failures"][-1]["kind"], "exception")

    def test_standing_key_not_pruned(self):
        self.fake.issues.append(stale_issue())
        self.run_patrol("run", "--slot", "2026102221")
        state = Store(self.state_dir).load()
        old_iso = (BASE_NOW - timedelta(days=8)).isoformat()
        key = next(iter(state["alerted"]))
        state["alerted"]["ghost|in_review_stale|x"] = {
            "first_at": old_iso, "last_at": old_iso, "escalated_at": None}
        Store(self.state_dir).save(state)
        later = BASE_NOW + timedelta(hours=1)
        self.run_patrol("run", "--slot", "2026102222", now=later)
        state = Store(self.state_dir).load()
        self.assertIn(key, state["alerted"])  # still standing -> kept
        self.assertNotIn("ghost|in_review_stale|x", state["alerted"])


class TestInflightRecovery(PatrolCase):
    def test_timeout_then_readback_found_no_resend(self):
        self.fake.write_mode = "timeout_found"
        code, out, err = self.run_patrol("run", "--slot", "2026102221")
        res = last_result(out)
        self.assertEqual(code, 0, err)
        self.assertEqual(res["ledger"]["status"], "posted")
        self.assertEqual(self.fake.comment_add_count, 1)

    def test_timeout_not_found_no_resend_same_run(self):
        self.fake.write_mode = "timeout_lost"
        code, out, err = self.run_patrol("run", "--slot", "2026102221")
        res = last_result(out)
        self.assertEqual(code, 2)
        self.assertEqual(res["ledger"]["status"], "uncertain")
        self.assertEqual(self.fake.comment_add_count, 1)
        self.assertEqual(self.fake.ledger_comments(), [])

    def test_next_run_under_10min_holds(self):
        self.fake.write_mode = "timeout_lost"
        self.run_patrol("run", "--slot", "2026102221")
        self.fake.write_mode = "normal"
        later = BASE_NOW + timedelta(minutes=5)
        code, out, err = self.run_patrol("run", "--slot", "2026102222", now=later)
        res = last_result(out)
        self.assertEqual(res["ledger"]["status"], "held")
        self.assertEqual(self.fake.comment_add_count, 1)

    def test_next_run_over_10min_resends_once(self):
        self.fake.write_mode = "timeout_lost"
        self.run_patrol("run", "--slot", "2026102221")
        self.fake.write_mode = "normal"
        later = BASE_NOW + timedelta(minutes=15)
        code, out, err = self.run_patrol("run", "--slot", "2026102222", now=later)
        # Resend of the held slot-21 batch + this slot's own new batch.
        self.assertEqual(self.fake.comment_add_count, 3)
        self.assertTrue(any("[patrol-batch:patrol:SGT2026102221" in c["content"]
                            for c in self.fake.ledger_comments()))
        self.assertTrue(any("[patrol-batch:patrol:SGT2026102222" in c["content"]
                            for c in self.fake.ledger_comments()))

    def test_readback_failure_holds(self):
        self.fake.write_mode = "timeout_lost"
        self.run_patrol("run", "--slot", "2026102221")
        self.fake.write_mode = "normal"
        self.fake.comment_list_fail_ids.add(config.LEDGER_ISSUE_ID)
        later = BASE_NOW + timedelta(minutes=15)
        code, out, err = self.run_patrol("run", "--slot", "2026102222", now=later)
        res = last_result(out)
        self.assertEqual(res["ledger"]["status"], "held")
        self.assertEqual(self.fake.comment_add_count, 1)


class TestExemptionsAndSections(PatrolCase):
    def _shelved_issue(self, status="blocked"):
        return make_issue("ACM-60", status,
                          labels=[{"id": config.SHELVED_LABEL_ID, "name": config.SHELVED_LABEL_NAME}],
                          updated_at=BASE_NOW - timedelta(days=5),
                          last_activity_at=BASE_NOW - timedelta(days=5))

    def test_shelved_label_invisible_everywhere(self):
        # Synthetic fixture: label 搁置 on a stale issue => zero per-issue calls,
        # absent from ledger and daily output entirely.
        for i, status in enumerate(("in_progress", "in_review", "blocked")):
            self.fake = FakeMultica()
            self.fake.issues = [
                make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID, assignee_id=None),
                make_issue("ACM-216", "in_progress", uid=config.INBOX_ISSUE_ID, assignee_id=None),
                self._shelved_issue(status),
                stale_issue("ACM-55"),
            ]
            self.state_dir = Path(tempfile.mkdtemp())
            code, out, err = self.run_patrol("run", "--slot", "2026102221")
            self.assertEqual(code, 0, err)
            called_ids = {w for argv in self.fake.calls for w in argv if "ACM-60" in str(w)}
            self.assertEqual(called_ids, set())
            body = self.fake.ledger_comments()[0]["content"]
            self.assertNotIn("ACM-60", body)
            self.assertIn("ACM-55", body)  # unlabelled identical sibling flagged
            code, out, _ = self.run_patrol("daily-pack", "--format", "md")
            self.assertNotIn("ACM-60", out)
            self.assertIn("ACM-55", out)

    def test_shelved_match_by_name_only_and_unlabel_recovers(self):
        issue = make_issue("ACM-60", "blocked", labels=[{"id": "other", "name": "搁置"}],
                           updated_at=BASE_NOW - timedelta(days=5),
                           last_activity_at=BASE_NOW - timedelta(days=5))
        self.fake.issues.append(issue)
        self.run_patrol("run", "--slot", "2026102221")
        self.assertNotIn("ACM-60", self.fake.ledger_comments()[0]["content"])
        # Label removed -> back in scope next run, flagged normally.
        issue["labels"] = []
        self.run_patrol("run", "--slot", "2026102222")
        self.assertIn("ACM-60", self.fake.ledger_comments()[-1]["content"])

    def test_awaiting_wakeup_exempt_listed(self):
        issue = make_issue("ACM-70", "in_review",
                           updated_at=BASE_NOW - timedelta(days=3),
                           last_activity_at=BASE_NOW - timedelta(days=3))
        self.fake.issues.append(issue)
        self.fake.wakeups[issue["id"]] = [{
            "enabled": True, "kind": "event",
            "expires_at": (BASE_NOW + timedelta(hours=4)).isoformat()}]
        code, out, err = self.run_patrol("run", "--slot", "2026102221")
        res = last_result(out)
        self.assertEqual(res["findings_count"], 0)
        body = self.fake.ledger_comments()[0]["content"]
        self.assertIn("awaiting_wakeup", body)
        self.assertIn("豁免", body)

    def test_acm215_unresolved_once_per_24h(self):
        self.fake.issues.append(make_issue("ACM-215", "blocked",
                                           updated_at=BASE_NOW - timedelta(days=9),
                                           last_activity_at=BASE_NOW - timedelta(days=9)))
        code, out, err = self.run_patrol("run", "--slot", "2026102221")
        self.assertEqual(code, 0, err)
        body = self.fake.ledger_comments()[0]["content"]
        self.assertIn("待用户口径", body)
        # It appears in its own section, never as a stuck-finding candidate row.
        candidates = body.split("## 候选异常")[1].split("##")[0]
        self.assertNotIn("ACM-215", candidates)
        res = last_result(out)
        # ACM-215 is not counted among alert candidates.
        self.assertEqual(res["findings_count"], 0)
        # Within 24h the section is not repeated.
        later = BASE_NOW + timedelta(hours=1)
        code, out, _ = self.run_patrol("run", "--slot", "2026102222", now=later)
        body2 = self.fake.ledger_comments()[-1]["content"]
        self.assertNotIn("待用户口径", body2)


class TestIdentityViolationLatch(PatrolCase):
    def test_author_mismatch_latches_and_blocks(self):
        self.fake.author_id = "an-impostor-agent"
        code, out, err = self.run_patrol("run", "--slot", "2026102221")
        self.assertEqual(code, 3)
        self.assertIn("identity_violation", err)
        self.fake.author_id = config.DISPATCHER_AGENT_ID
        code, out, err = self.run_patrol("run", "--slot", "2026102222")
        self.assertEqual(code, 3)
        self.assertIn("identity_violation", err)
        code, out, _ = self.run_patrol("clear-identity-violation")
        self.assertEqual(code, 0)
        self.assertIn("cleared", out)
        code, out, err = self.run_patrol("run", "--slot", "2026102222")
        self.assertEqual(code, 0, err)


class TestNoSecrets(PatrolCase):
    def test_token_never_in_output_or_state(self):
        env = dict(GOOD_ENV, MULTICA_TOKEN="mat_SECRETTOKEN9876543210zzzz")
        self.fake.issues.append(stale_issue())
        code, out, err = self.run_patrol("run", "--slot", "2026102221", env=env)
        self.assertEqual(code, 0, err)
        self.assertNotIn("mat_SECRETTOKEN", out)
        self.assertNotIn("mat_SECRETTOKEN", err)
        for path in Path(self.state_dir).iterdir():
            self.assertNotIn("mat_SECRETTOKEN", path.read_text(), path.name)
        for c in self.fake.ledger_comments():
            self.assertNotIn("mat_SECRETTOKEN", c["content"])


class TestFailureSelfReport(PatrolCase):
    def test_failed_run_reports_next_round(self):
        self.fake.fail_handler = lambda argv: subprocess.CompletedProcess(
            argv, 1, "", "read dead") if "issue" in argv and "list" in argv else None
        code, out, err = self.run_patrol("run", "--slot", "2026102221")
        self.assertEqual(code, 2)
        self.assertIn("[Error]", err)
        self.fake.fail_handler = None
        later = BASE_NOW + timedelta(hours=1)
        code, out, err = self.run_patrol("run", "--slot", "2026102222", now=later)
        self.assertEqual(code, 0, err)
        body = self.fake.ledger_comments()[-1]["content"]
        self.assertIn("上一轮自报", body)
        self.assertIn("read dead", body)


if __name__ == "__main__":
    unittest.main()
