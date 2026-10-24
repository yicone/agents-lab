"""Unwoken-handoff supplement tests. ALL synthetic offline fixtures."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from patrol import config
from patrol.cli import CommandNotAllowed, PatrolCLI
from patrol.state import Store
from tests.fakes import (BASE_NOW, GOOD_ENV, FakeMultica, comment, iso, invoke,
                         last_result, make_issue, run, status_changed)

AGENT = "agent-worker-1"
TARGET_UUID = "11111111-2222-3333-4444-555555555555"
OTHER_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
DISP_MENTION = f"[@副调度](mention://agent/{config.DISPATCHER_AGENT_ID})"


class UnwokenCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir = Path(self.tmp.name)
        self.fake = FakeMultica()
        self.fake.agents.append({"id": AGENT, "name": "WorkerAgent"})
        self.fake.issues = [
            make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID, assignee_id=None),
            make_issue("ACM-216", "in_progress", uid=config.INBOX_ISSUE_ID, assignee_id=None)]
        state = Store(self.state_dir).load()
        state["agent_names"] = {config.DISPATCHER_AGENT_ID: "副调度", AGENT: "WorkerAgent"}
        Store(self.state_dir).save(state)

    @staticmethod
    def _dt(slot):
        return datetime.strptime(slot, "%Y%m%d%H").replace(
            tzinfo=timezone(config.SGT_OFFSET))

    def _slot(self, base, hours):
        return (self._dt(base) + timedelta(hours=hours)).strftime("%Y%m%d%H")

    def _run_at(self, slot, extra=None):
        now = self._dt(slot).astimezone(timezone.utc)
        self.fake.now = now
        return invoke(self.fake, ["run", "--slot", slot] + (extra or []),
                      now=now, state_dir=self.state_dir)

    def add_handoff(self, identifier="ACM-500", status="in_review",
                    content="delivery note, no mention", minutes_ago=40,
                    author_type="agent", author_id=AGENT, cid="lc-1",
                    entered_minutes_ago=60):
        issue = make_issue(identifier, status, assignee_id=AGENT,
                           updated_at=BASE_NOW - timedelta(minutes=minutes_ago),
                           last_activity_at=BASE_NOW - timedelta(minutes=minutes_ago))
        self.fake.issues.append(issue)
        self.fake.timelines[issue["id"]] = [
            status_changed(status, BASE_NOW - timedelta(minutes=entered_minutes_ago))]
        self.fake.runs[issue["id"]] = [run(
            "completed", rid="r-old", agent_id=AGENT,
            started_at=BASE_NOW - timedelta(minutes=minutes_ago + 30),
            completed_at=BASE_NOW - timedelta(minutes=minutes_ago + 25))]
        self.fake.add_comment(issue["id"], comment(
            content, BASE_NOW - timedelta(minutes=minutes_ago), cid=cid,
            author_type=author_type, author_id=author_id))
        return issue

class DispatchCase(UnwokenCase):
    def add_dispatch(self, identifier="ACM-600", assignee_id=AGENT,
                     content="不需要你做，下一步由 WorkerAgent 做：实现方案并回报结果。",
                     minutes_ago=40, cid="lc-1",
                     author_type="agent", author_id=config.DISPATCHER_AGENT_ID,
                     entered_minutes_ago=60, target_run=None):
        issue = make_issue(identifier, "in_progress", assignee_id=assignee_id,
                           updated_at=BASE_NOW - timedelta(minutes=minutes_ago),
                           last_activity_at=BASE_NOW - timedelta(minutes=minutes_ago))
        self.fake.issues.append(issue)
        self.fake.timelines[issue["id"]] = [
            status_changed("in_progress", BASE_NOW - timedelta(minutes=entered_minutes_ago))]
        runs = [run("completed", rid="r-old", agent_id=assignee_id,
                    started_at=BASE_NOW - timedelta(minutes=minutes_ago + 30),
                    completed_at=BASE_NOW - timedelta(minutes=minutes_ago + 25))]
        if target_run:
            runs = [target_run] + runs
        self.fake.runs[issue["id"]] = runs
        self.fake.add_comment(issue["id"], comment(
            content, BASE_NOW - timedelta(minutes=minutes_ago), cid=cid,
            author_type=author_type, author_id=author_id))
        return issue

    def nowake_comments(self, issue):
        return [c for c in self.fake.comments.get(issue["id"], [])
                if "[patrol-nowake:" in c.get("content", "")]


class TestWakeupOnly(DispatchCase):
    def assert_delivery(self, issue, reason="unwoken_handoff"):
        wakes = self.fake.wakeups.get(issue["id"], [])
        self.assertEqual(len(wakes), 1)
        wake = wakes[0]
        self.assertEqual(wake["agent_id"], config.DISPATCHER_AGENT_ID)
        self.assertIn("lc-1", wake["instruction"])
        self.assertIn("判定依据", wake["instruction"])
        self.assertIn("下一步", wake["instruction"])
        self.assertNotIn("副调度", wake["instruction"])
        self.assertNotIn("mention://", wake["instruction"])
        self.assertFalse(any(c[1:4] == ["issue", "comment", "add"] and c[4] == issue["id"]
                             for c in self.fake.calls))
        rec = Store(self.state_dir).load()["unwoken"][issue["id"]]
        self.assertEqual(rec["reason"], reason)
        self.assertEqual(rec["state"], "sent")
        self.assertEqual(rec["wakeup_result"]["id"], wake["id"])
        ledger = self.fake.ledger_comments()[-1]["content"]
        self.assertIn(wake["id"], ledger)
        self.assertNotIn("副调度", ledger)
        self.assertNotIn("mention://agent/", ledger)
        self.assertNotIn("mention://member/", ledger)

    def test_handoff_wakeup_only(self):
        issue = self.add_handoff()
        code, out, err = self._run_at("2026102221")
        self.assertEqual(code, 0, err)
        self.assertEqual(last_result(out)["unwoken_sent"], 1)
        self.assert_delivery(issue)

    def test_dispatch_wakeup_only(self):
        issue = self.add_dispatch()
        self._run_at("2026102221")
        self.assert_delivery(issue, "dispatch_no_wake")
        self.assertIn("确认派活", self.fake.wakeups[issue["id"]][0]["instruction"])

    def test_named_dispatch_target_in_instruction(self):
        issue = self.add_dispatch(
            assignee_id="another-agent",
            content="不需要你做，下一步由 WorkerAgent 做：继续实现。")
        self._run_at("2026102221")
        self.assert_delivery(issue, "dispatch_no_wake")
        instruction = self.fake.wakeups[issue["id"]][0]["instruction"]
        self.assertIn(f"目标 agent id：{AGENT}", instruction)

    def test_dispatcher_handoff_skipped(self):
        self.add_handoff(author_id=config.DISPATCHER_AGENT_ID)
        self._run_at("2026102221")
        self.assertFalse(self.fake.wakeups)

    def test_failure_is_durable_and_visible_and_resumes(self):
        issue = self.add_handoff()
        def fail(argv):
            if argv[1:4] == ["issue", "wakeup", "create"]:
                return subprocess.CompletedProcess(argv, 1, "", "instruction file rejected")
        self.fake.fail_handler = fail
        with self.assertLogs("patrol.unwoken", level="INFO") as logs:
            self._run_at("2026102221")
        rec = Store(self.state_dir).load()["unwoken"][issue["id"]]
        self.assertEqual(rec["state"], "wakeup_pending")
        self.assertIn("instruction file rejected", rec["last_error"])
        self.assertIn("instruction file rejected", self.fake.ledger_comments()[-1]["content"])
        self.assertIn("instruction file rejected", "".join(logs.output))
        self.fake.fail_handler = None
        self._run_at("2026102222")
        self.assert_delivery(issue)

    def test_uncertain_create_reconciles_without_duplicate(self):
        issue = self.add_handoff()
        original = self.fake.exec
        def timeout(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[1:4] == ["issue", "wakeup", "create"]:
                raise subprocess.TimeoutExpired(argv, 90)
            return result
        self.fake.exec = timeout
        self._run_at("2026102221")
        self.fake.exec = original
        self._run_at("2026102222")
        self.assert_delivery(issue)
        creates = [c for c in self.fake.calls if c[1:4] == ["issue", "wakeup", "create"]]
        self.assertEqual(len(creates), 1)

    def test_empty_create_response_is_not_success(self):
        issue = self.add_handoff()
        def empty(argv):
            if argv[1:4] == ["issue", "wakeup", "create"]:
                return subprocess.CompletedProcess(argv, 0, "{}", "")
        self.fake.fail_handler = empty
        _, out, _ = self._run_at("2026102221")
        self.assertEqual(last_result(out)["unwoken_sent"], 0)
        rec = Store(self.state_dir).load()["unwoken"][issue["id"]]
        self.assertEqual(rec["state"], "wakeup_pending")
        self.assertIn("no id", rec["last_error"])

    def test_legacy_pending_resumes_without_finding_or_comment(self):
        issue = self.add_handoff()
        store = Store(self.state_dir)
        state = store.load()
        state["unwoken"] = {issue["id"]: {
            "comment_id": "lc-1", "marker": f"[patrol-unwoken:{issue['id']}:lc-1]",
            "posted_comment_id": "legacy-comment", "reason": "unwoken_handoff",
            "state": "wakeup_pending", "sent_at": None}}
        store.save(state)
        self.fake.add_comment(issue["id"], comment(
            "legacy patrol comment", BASE_NOW - timedelta(minutes=5),
            cid="legacy-comment", author_type="agent", author_id=config.DISPATCHER_AGENT_ID))
        self._run_at("2026102221")
        self.assert_delivery(issue)

    def test_dedupe_and_cap_shared(self):
        for n in range(2):
            self.add_handoff(f"ACM-{700+n}", cid=f"lc-h{n}")
        for n in range(3):
            self.add_dispatch(f"ACM-{710+n}", cid=f"lc-d{n}")
        self._run_at("2026102221")
        self.assertEqual(sum(map(len, self.fake.wakeups.values())), config.MAX_UNWOKEN_PER_RUN)
        self._run_at("2026102222")
        self.assertTrue(all(len(w) == 1 for w in self.fake.wakeups.values()))

    def test_repeated_same_source_after_24h(self):
        issue = self.add_handoff()
        self._run_at("2026102221")
        self._run_at(self._slot("2026102221", 25))
        self.assertEqual(len(self.fake.wakeups[issue["id"]]), 2)

    def test_dry_run_no_writes_or_names(self):
        issue = self.add_handoff()
        # Raw issue bodies may contain historical names and side-effecting links.
        issue["description"] = "副调度 " + DISP_MENTION
        issue["labels"] = [{"id": "sample", "name": "副调度样本"}]
        self.add_dispatch()
        _, out, _ = self._run_at("2026102221", ["--dry-run"])
        self.assertEqual(last_result(out)["writes_attempted"], 0)
        self.assertEqual(last_result(out)["unwoken_would_send"], 2)
        self.assertNotIn("副调度", out)
        self.assertNotIn("mention://agent/", out)
        self.assertFalse(self.fake.wakeups)
        self.assertFalse(Store(self.state_dir).load().get("unwoken"))

    def test_age_and_run_gates(self):
        self.add_handoff("ACM-800", minutes_ago=14)
        issue = self.add_handoff("ACM-801", minutes_ago=15)
        other = self.add_dispatch("ACM-802")
        self.fake.runs[other["id"]].insert(0, run(
            "completed", rid="later", agent_id=AGENT,
            started_at=BASE_NOW - timedelta(minutes=20)))
        self._run_at("2026102221")
        self.assertEqual(set(self.fake.wakeups), {issue["id"]})

    def test_race_guard(self):
        issue = self.add_handoff()
        original = self.fake.exec
        def race(argv, **kwargs):
            if argv[1:4] == ["issue", "comment", "list"] and argv[4] == issue["id"] and "--since" in argv:
                self.fake.add_comment(issue["id"], comment("human arrived", BASE_NOW, cid="new"))
            return original(argv, **kwargs)
        self.fake.exec = race
        self._run_at("2026102221")
        self.assertFalse(self.fake.wakeups)

    def test_provider_rows_do_not_print_dispatcher_name(self):
        from patrol.ledger import render_batch
        issue = self.add_handoff()
        body = render_batch(
            "batch", "manual", "2026102221", "full",
            {"issues_discovered": 1, "deep_checked": 1, "from_cache": 0,
             "errors": [], "complete": True}, [], [],
            [{"issue": issue, "agent_id": config.DISPATCHER_AGENT_ID,
              "agent_name": "副调度", "action": "would_escalate", "run_id": "r"}],
            0, 0, [], [], [], 0, BASE_NOW)
        self.assertNotIn("副调度", body)
        self.assertIn(config.DISPATCHER_AGENT_ID, body)

    def test_cli_local_instruction_and_no_comment_capability(self):
        seen = []
        def inspect(argv, **kwargs):
            if argv[1:4] == ["issue", "wakeup", "create"]:
                path = Path(argv[argv.index("--instruction-file") + 1])
                self.assertTrue(path.is_relative_to(Path.cwd()))
                self.assertEqual(path.read_text(), "instruction")
                self.assertNotIn("--allow-external-file", argv)
                seen.append(path)
            return self.fake.exec(argv, **kwargs)
        cli = PatrolCLI(allowed_write_ids=frozenset(), _exec=inspect)
        with self.assertRaises(CommandNotAllowed):
            cli.run_unwoken_wakeup("i", "instruction")
        cli.permit_unwoken("i")
        cli.run_unwoken_wakeup("i", "instruction")
        self.assertFalse(seen[0].exists())
        with self.assertRaises(CommandNotAllowed):
            cli.run_unwoken_write("i", "unused")


class TestDispatchScanLevel(unittest.TestCase):
    def test_incremental_refires_after_grace(self):
        from patrol.cli import PatrolCLI
        from patrol.scan import run_scan
        from patrol.state import initial_state
        fake = FakeMultica()
        cli = PatrolCLI(allowed_write_ids=frozenset({config.LEDGER_ISSUE_ID}),
                        env={}, _exec=fake.exec, _sleep=lambda s: None, retries=0)
        issue = make_issue("ACM-720", "in_progress", assignee_id=AGENT)
        fake.issues = [issue]
        fake.timelines[issue["id"]] = [
            status_changed("in_progress", BASE_NOW - timedelta(hours=2))]
        fake.add_comment(issue["id"], comment(
            "需要你做：继续实现。", BASE_NOW - timedelta(minutes=10), cid="lc-s",
            author_type="agent", author_id=config.DISPATCHER_AGENT_ID))
        state = initial_state()
        first = run_scan(cli, state, True, BASE_NOW, fake.monotonic)
        cached = state["cache"][issue["id"]]
        due = datetime.fromisoformat(cached["next_due_at"])
        self.assertEqual(due, BASE_NOW - timedelta(minutes=10)
                         + timedelta(seconds=config.UNWOKEN_GRACE_S))
        # Before the threshold the finding is absent; at it, present.
        self.assertNotIn("dispatch_no_wake",
                         {f["reason"] for f in first["per_issue"][issue["id"]]["findings"]})
        second = run_scan(cli, state, False, BASE_NOW + timedelta(minutes=6),
                          fake.monotonic)
        self.assertIn("dispatch_no_wake",
                      {f["reason"] for f in second["per_issue"][issue["id"]]["findings"]})


class TestBoundariesGates(UnwokenCase):
    def test_comment_age_boundary(self):
        for mins, expect in ((14, 0), (15, 1), (16, 1)):
            self.tmp2 = tempfile.TemporaryDirectory()
            self.state_dir = Path(self.tmp2.name)
            self.fake = FakeMultica()
            self.fake.issues = [
                make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID, assignee_id=None),
                make_issue("ACM-216", "in_progress", uid=config.INBOX_ISSUE_ID, assignee_id=None)]
            issue = self.add_handoff(minutes_ago=mins)
            invoke(self.fake, ["run", "--slot", "2026102221"], state_dir=self.state_dir)
            self.assertEqual(len(self.fake.wakeups.get(issue["id"], [])), expect, mins)
            self.tmp2.cleanup()

    def test_status_and_author_gates(self):
        # todo / in_progress / done never fire; member-authored last comment never fires.
        for n, status in enumerate(("todo", "in_progress", "done")):
            issue = self.add_handoff(f"ACM-{510 + n}", status=status, cid=f"lc-{status}")
        self.add_handoff("ACM-520", status="in_review", cid="lc-m",
                         author_type="member", author_id="u1")
        code, out, err = self._run_at("2026102221")
        total = sum(len(self.fake.wakeups.get(i["id"], [])) for i in self.fake.issues)
        self.assertEqual(total, 0)

    def test_patrol_comments_ignored_as_last(self):
        issue = self.add_handoff(minutes_ago=40)
        self.fake.add_comment(issue["id"], comment(
            "[patrol-batch:x] heartbeat", BASE_NOW - timedelta(minutes=5),
            cid="pb", author_type="agent", author_id=config.DISPATCHER_AGENT_ID))
        code, out, err = self._run_at("2026102221")
        self.assertEqual(len(self.fake.wakeups.get(issue["id"], [])), 1)



class TestDispatchNoWakeGates(DispatchCase):
    def test_name_only_other_and_member_mentions_still_fire(self):
        for n, content in enumerate((
                "不需要你做，下一步由 WorkerAgent 做：改完回报 [@副调度](mention://agent/副调度)",
                f"不需要你做，下一步由 WorkerAgent 做：派活给 [@x](mention://agent/{OTHER_UUID})",
                f"不需要你做，下一步由 WorkerAgent 做：[@y](mention://member/{OTHER_UUID}) 已知悉")):
            self.tmp2 = tempfile.TemporaryDirectory()
            self.state_dir = Path(self.tmp2.name)
            self.fake = FakeMultica()
            self.fake.agents.append({"id": AGENT, "name": "WorkerAgent"})
            self.fake.issues = [
                make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID, assignee_id=None),
                make_issue("ACM-216", "in_progress", uid=config.INBOX_ISSUE_ID, assignee_id=None)]
            state = Store(self.state_dir).load()
            state["agent_names"] = {config.DISPATCHER_AGENT_ID: "副调度", AGENT: "WorkerAgent"}
            Store(self.state_dir).save(state)
            issue = self.add_dispatch(content=content)
            code, out, err = self._run_at("2026102221")
            self.assertEqual(len(self.fake.wakeups.get(issue["id"], [])), 1, content)
            self.tmp2.cleanup()

    def test_valid_uuid_mention_of_target_suppresses(self):
        issue = self.add_dispatch(
            assignee_id=TARGET_UUID,
            content=f"需要你做：[@t](mention://agent/{TARGET_UUID}) 开工")
        code, out, err = self._run_at("2026102221")
        self.assertEqual(self.fake.wakeups.get(issue["id"], []), [])

    def test_run_gates_by_target(self):
        comment_at = BASE_NOW - timedelta(minutes=40)
        for kind, r, expect in (
                ("after-completed", run("completed", rid="r-l", agent_id=AGENT,
                                        started_at=comment_at + timedelta(minutes=5),
                                        completed_at=comment_at + timedelta(minutes=8)), 0),
                ("after-queued", run("queued", rid="r-q", agent_id=AGENT,
                                     started_at=comment_at + timedelta(minutes=5)), 0),
                ("after-cancelled", run("cancelled", rid="r-c", agent_id=AGENT,
                                        started_at=comment_at + timedelta(minutes=5)), 0),
                ("after-none-agent", run("queued", rid="r-n", agent_id=None,
                                         started_at=comment_at + timedelta(minutes=5)), 0),
                ("before", run("completed", rid="r-b", agent_id=AGENT,
                               started_at=comment_at - timedelta(minutes=30),
                               completed_at=comment_at - timedelta(minutes=20)), 1),
                ("other-agent", run("completed", rid="r-o", agent_id="other-agent",
                                    started_at=comment_at + timedelta(minutes=5),
                                    completed_at=comment_at + timedelta(minutes=8)), 1)):
            with self.subTest(kind=kind):
                self.tmp2 = tempfile.TemporaryDirectory()
                self.state_dir = Path(self.tmp2.name)
                self.fake = FakeMultica()
                self.fake.agents.append({"id": AGENT, "name": "WorkerAgent"})
                self.fake.issues = [
                    make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID, assignee_id=None),
                    make_issue("ACM-216", "in_progress", uid=config.INBOX_ISSUE_ID, assignee_id=None)]
                state = Store(self.state_dir).load()
                state["agent_names"] = {config.DISPATCHER_AGENT_ID: "副调度", AGENT: "WorkerAgent"}
                Store(self.state_dir).save(state)
                issue = self.add_dispatch(target_run=r)
                code, out, err = self._run_at("2026102221")
                self.assertEqual(len(self.fake.wakeups.get(issue["id"], [])), expect, err)
                self.tmp2.cleanup()

    def test_non_dispatch_or_wrong_author_no_write(self):
        cases = (
            dict(author_type="agent", author_id=AGENT,
                 content="需要你做：看起来是派活但作者不是副调度"),
            dict(author_type="member", author_id="u1", content="需要你做：人工口吻"),
            dict(author_id=config.DISPATCHER_AGENT_ID,
                 content="已收件。ACM-343 保持 in_review，待你回来定稿。"),
        )
        for n, kw in enumerate(cases):
            self.tmp2 = tempfile.TemporaryDirectory()
            self.state_dir = Path(self.tmp2.name)
            self.fake = FakeMultica()
            self.fake.agents.append({"id": AGENT, "name": "WorkerAgent"})
            self.fake.issues = [
                make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID, assignee_id=None),
                make_issue("ACM-216", "in_progress", uid=config.INBOX_ISSUE_ID, assignee_id=None)]
            state = Store(self.state_dir).load()
            state["agent_names"] = {config.DISPATCHER_AGENT_ID: "副调度", AGENT: "WorkerAgent"}
            Store(self.state_dir).save(state)
            kw.setdefault("author_type", "agent")
            kw.setdefault("author_id", config.DISPATCHER_AGENT_ID)
            issue = self.add_dispatch(**kw)
            code, out, err = self._run_at("2026102221")
            self.assertEqual(self.fake.wakeups.get(issue["id"], []), [], kw["content"])
            self.tmp2.cleanup()
        # unassigned in_progress is covered by a different rule
        issue = self.add_dispatch("ACM-601", assignee_id=None)
        code, out, err = self._run_at("2026102221")
        self.assertEqual(self.fake.wakeups.get(issue["id"], []), [])

    def test_exempt_targets_no_write(self):
        for ident, uid in (("ACM-215", None), ("ACM-216", config.INBOX_ISSUE_ID),
                           ("ACM-229", config.LEDGER_ISSUE_ID)):
            issue = self.add_dispatch(ident, cid=f"lc-{ident}")
            if uid:
                issue["id"] = uid
        code, out, err = self._run_at("2026102221")
        total = sum(len(self.fake.wakeups.get(i["id"], [])) for i in self.fake.issues)
        self.assertEqual(total, 0)

    def test_age_boundary(self):
        for mins, expect in ((14, 0), (15, 1), (16, 1)):
            self.tmp2 = tempfile.TemporaryDirectory()
            self.state_dir = Path(self.tmp2.name)
            self.fake = FakeMultica()
            self.fake.agents.append({"id": AGENT, "name": "WorkerAgent"})
            self.fake.issues = [
                make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID, assignee_id=None),
                make_issue("ACM-216", "in_progress", uid=config.INBOX_ISSUE_ID, assignee_id=None)]
            state = Store(self.state_dir).load()
            state["agent_names"] = {config.DISPATCHER_AGENT_ID: "副调度", AGENT: "WorkerAgent"}
            Store(self.state_dir).save(state)
            issue = self.add_dispatch(minutes_ago=mins)
            invoke(self.fake, ["run", "--slot", "2026102221"], state_dir=self.state_dir)
            self.assertEqual(len(self.fake.wakeups.get(issue["id"], [])), expect, mins)
            self.tmp2.cleanup()


if __name__ == "__main__":
    unittest.main()
