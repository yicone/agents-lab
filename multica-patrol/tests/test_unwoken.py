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

    def unwoken_comments(self, issue):
        return [c for c in self.fake.comments.get(issue["id"], [])
                if "[patrol-unwoken:" in c.get("content", "")]


class TestSupplement(UnwokenCase):
    def test_one_supplement_written(self):
        issue = self.add_handoff()
        code, out, err = self._run_at("2026102221")
        self.assertEqual(code, 0, err)
        posted = self.unwoken_comments(issue)
        self.assertEqual(len(posted), 1)
        lines = posted[0]["content"].splitlines()
        self.assertTrue(lines[0].startswith("不需要你做，下一步由"))
        self.assertTrue(lines[1].startswith(f"[patrol-unwoken:{issue['id']}:lc-1"))
        self.assertIn(DISP_MENTION, posted[0]["content"])
        self.assertIn("WorkerAgent", posted[0]["content"])
        self.assertIn("复验", posted[0]["content"])
        state = Store(self.state_dir).load()
        self.assertEqual(state["unwoken"][issue["id"]]["state"], "sent")
        res = last_result(out)
        self.assertEqual(res["unwoken_sent"], 1)
        self.assertIn("未唤醒的交付补发", self.fake.ledger_comments()[0]["content"])

    def test_name_only_mention_still_fires(self):
        issue = self.add_handoff(content="done [@副调度](mention://agent/副调度)")
        code, out, err = self._run_at("2026102221")
        self.assertEqual(len(self.unwoken_comments(issue)), 1)

    def test_valid_mention_no_finding(self):
        for mention in (f"[@x](mention://agent/{AGENT}xxxx-xxxx)",
                        f"[@y](mention://member/{AGENT}xxxx-xxxx)"):
            pass  # placeholder replaced below
        issue = self.add_handoff(
            content=f"done [@x](mention://agent/{'1'*8}-{'1'*4}-{'1'*4}-{'1'*4}-{'1'*12})",
            cid="lc-a")
        self.add_handoff("ACM-501",
                         content=f"done [@y](mention://member/{'2'*8}-{'2'*4}-{'2'*4}-{'2'*4}-{'2'*12})",
                         cid="lc-b")
        code, out, err = self._run_at("2026102221")
        self.assertEqual(self.unwoken_comments(issue), [])
        issue2 = next(i for i in self.fake.issues if i["identifier"] == "ACM-501")
        self.assertEqual(self.unwoken_comments(issue2), [])
        self.assertEqual(last_result(out)["unwoken_sent"], 0)

    def test_run_after_comment_suppresses(self):
        for kind, r in (("completed", run("completed", rid="r-late",
                                        started_at=BASE_NOW - timedelta(minutes=20),
                                        completed_at=BASE_NOW - timedelta(minutes=15))),
                        ("cancelled", run("cancelled", rid="r-c",
                                          started_at=BASE_NOW - timedelta(minutes=20))),
                        ("queued", run("queued", rid="r-q",
                                       started_at=BASE_NOW - timedelta(minutes=20)))):
            self.tmp2 = tempfile.TemporaryDirectory()
            self.state_dir = Path(self.tmp2.name)
            self.setUp2 = None
            self.fake = FakeMultica()
            self.fake.issues = [
                make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID, assignee_id=None),
                make_issue("ACM-216", "in_progress", uid=config.INBOX_ISSUE_ID, assignee_id=None)]
            issue = self.add_handoff()
            self.fake.runs[issue["id"]] = [r] + self.fake.runs[issue["id"]]
            code, out, err = invoke(self.fake, ["run", "--slot", "2026102221"],
                                    state_dir=self.state_dir)
            self.assertEqual(self.unwoken_comments(issue), [], kind)
            self.tmp2.cleanup()
        # A run started BEFORE the comment does not suppress.
        issue = self.add_handoff("ACM-502")
        code, out, err = self._run_at("2026102221")
        self.assertEqual(len(self.unwoken_comments(issue)), 1)

    def test_dedupe_24h_per_issue(self):
        issue = self.add_handoff()
        self._run_at("2026102221")
        self._run_at("2026102222")
        self.assertEqual(len(self.unwoken_comments(issue)), 1)
        # New last comment within 24h still deduped per issue.
        self.fake.add_comment(issue["id"], comment(
            "another no-mention", BASE_NOW + timedelta(hours=1), cid="lc-2",
            author_type="agent", author_id=AGENT))
        issue["updated_at"] = iso(BASE_NOW + timedelta(hours=1))
        self._run_at("2026102222")
        self.assertEqual(len(self.unwoken_comments(issue)), 1)
        # After 24h the same last comment can be supplemented again.
        self._run_at(self._slot("2026102221", 25))
        self.assertEqual(len(self.unwoken_comments(issue)), 2)


class TestBoundaries(UnwokenCase):
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
            self.assertEqual(len(self.unwoken_comments(issue)), expect, mins)
            self.tmp2.cleanup()

    def test_status_and_author_gates(self):
        # todo / in_progress / done never fire; member-authored last comment never fires.
        for n, status in enumerate(("todo", "in_progress", "done")):
            issue = self.add_handoff(f"ACM-{510 + n}", status=status, cid=f"lc-{status}")
        self.add_handoff("ACM-520", status="in_review", cid="lc-m",
                         author_type="member", author_id="u1")
        code, out, err = self._run_at("2026102221")
        total = sum(len(self.unwoken_comments(i)) for i in self.fake.issues)
        self.assertEqual(total, 0)

    def test_patrol_comments_ignored_as_last(self):
        issue = self.add_handoff(minutes_ago=40)
        self.fake.add_comment(issue["id"], comment(
            "[patrol-batch:x] heartbeat", BASE_NOW - timedelta(minutes=5),
            cid="pb", author_type="agent", author_id=config.DISPATCHER_AGENT_ID))
        code, out, err = self._run_at("2026102221")
        self.assertEqual(len(self.unwoken_comments(issue)), 1)


class TestGuardsAndProtocol(UnwokenCase):
    def test_guard_race_skips(self):
        # Member comment appears between scan and write -> re-read blocks it.
        issue = self.add_handoff()
        real_exec = self.fake.exec
        def racer(argv):
            args = argv[1:]
            words = [a for a in args if not a.startswith("-")]
            if words[:3] == ["issue", "comment", "list"] and words[3] == issue["id"] \
                    and "--since" in args:
                self.fake.add_comment(issue["id"], comment(
                    "human speaks", BASE_NOW + timedelta(minutes=1), cid="race"))
            return real_exec(argv)
        self.fake.exec = racer
        code, out, err = self._run_at("2026102221")
        self.assertEqual(self.unwoken_comments(issue), [])

    def test_dry_run_zero_writes(self):
        issue = self.add_handoff()
        code, out, err = invoke(self.fake, ["run", "--slot", "2026102221", "--dry-run"],
                                state_dir=self.state_dir)
        res = last_result(out)
        self.assertEqual(res["unwoken_would_send"], 1)
        self.assertEqual(self.unwoken_comments(issue), [])
        state = Store(self.state_dir).load()
        self.assertEqual(state.get("unwoken") or {}, {})

    def test_uncertain_post_no_blind_resend(self):
        issue = self.add_handoff()
        def timeout_issue_writes(argv):
            args = argv[1:]
            words = [a for a in args if not a.startswith("-")]
            if words[:3] == ["issue", "comment", "add"] and words[3] == issue["id"]:
                raise subprocess.TimeoutExpired(argv, 90)
            return None
        self.fake.fail_handler = timeout_issue_writes
        code, out, err = self._run_at("2026102221")
        state = Store(self.state_dir).load()
        self.assertEqual(state["unwoken"][issue["id"]]["state"], "pending")
        self.assertEqual(self.unwoken_comments(issue), [])
        # Next run: marker still absent -> guarded resend succeeds.
        self.fake.fail_handler = None
        code, out, err = self._run_at("2026102222")
        self.assertEqual(len(self.unwoken_comments(issue)), 1)

    def test_identity_violation_latches(self):
        issue = self.add_handoff()
        self.fake.author_id = "impostor"
        code, out, err = self._run_at("2026102221")
        self.assertEqual(code, 3)
        state = Store(self.state_dir).load()
        self.assertTrue(state["identity_violation"])

    def test_unwoken_write_allowlist(self):
        cli = PatrolCLI(allowed_write_ids=frozenset({config.INBOX_ISSUE_ID}),
                        env=GOOD_ENV, _exec=self.fake.exec, _sleep=lambda s: None, retries=0)
        p = self.state_dir / "u.md"
        p.write_text("不需要你做，下一步由 副调度 做：x\n[patrol-unwoken:u:c]\n"
                     + DISP_MENTION + "\n")
        with self.assertRaises(CommandNotAllowed):
            cli.run_unwoken_write("uuid-ACM-999", p)
        cli.permit_unwoken("uuid-ACM-999")
        self.assertIsNotNone(cli.run_unwoken_write("uuid-ACM-999", p))
        p.write_text("bad first line\n[patrol-unwoken:u:c]\n" + DISP_MENTION)
        with self.assertRaises(CommandNotAllowed):
            cli.run_unwoken_write("uuid-ACM-999", p)
        p.write_text("不需要你做，下一步由 副调度 做：x\nno marker\n" + DISP_MENTION)
        with self.assertRaises(CommandNotAllowed):
            cli.run_unwoken_write("uuid-ACM-999", p)
        p.write_text("不需要你做，下一步由 副调度 做：x\n[patrol-unwoken:u:c]\nno mention")
        with self.assertRaises(CommandNotAllowed):
            cli.run_unwoken_write("uuid-ACM-999", p)

    def test_cap_per_run(self):
        issues = [self.add_handoff(f"ACM-{600 + i}", cid=f"lc-{i}") for i in range(5)]
        code, out, err = self._run_at("2026102221")
        sent = sum(len(self.unwoken_comments(i)) for i in issues)
        self.assertEqual(sent, config.MAX_UNWOKEN_PER_RUN)
        self.assertIn("unwoken_deferred", self.fake.ledger_comments()[0]["content"])


class TestExemptTargets(UnwokenCase):
    def test_no_writes_on_inbox_ledger_or_unresolved(self):
        # ACM-215 (UNRESOLVED_RULE), ACM-216, ACM-229: zero writes even with facts.
        for ident, uid in (("ACM-215", None), ("ACM-216", config.INBOX_ISSUE_ID),
                           ("ACM-229", config.LEDGER_ISSUE_ID)):
            issue = self.add_handoff(ident, cid=f"lc-{ident}")
            if uid:
                issue["id"] = uid
        code, out, err = self._run_at("2026102221")
        total = sum(len(self.unwoken_comments(i)) for i in self.fake.issues)
        self.assertEqual(total, 0)
        self.assertEqual(last_result(out)["unwoken_sent"], 0)

    def test_agent_name_in_body_and_list_failure_fallback(self):
        issue = self.add_handoff()
        code, out, err = self._run_at("2026102221")
        self.assertIn("WorkerAgent", self.unwoken_comments(issue)[0]["content"])
        # A failing `agent list` keeps old names and never fails the round.
        self.tmp2 = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp2.name)
        self.fake = FakeMultica()
        self.fake.issues = [
            make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID, assignee_id=None),
            make_issue("ACM-216", "in_progress", uid=config.INBOX_ISSUE_ID, assignee_id=None)]
        issue = self.add_handoff()
        def fail_agents(argv):
            if argv[1:3] == ["agent", "list"]:
                return subprocess.CompletedProcess(argv, 1, "", "boom")
            return None
        self.fake.fail_handler = fail_agents
        code, out, err = invoke(self.fake, ["run", "--slot", "2026102221"],
                                state_dir=self.state_dir)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.unwoken_comments(issue)), 1)
        self.tmp2.cleanup()


class TestScanLevel(unittest.TestCase):
    def test_cached_blocked_unassigned_refreshes_runs(self):
        from patrol.cli import PatrolCLI
        from patrol.scan import run_scan
        from patrol.state import initial_state
        fake = FakeMultica()
        cli = PatrolCLI(allowed_write_ids=frozenset({config.LEDGER_ISSUE_ID}),
                        env={}, _exec=fake.exec, _sleep=lambda s: None, retries=0)
        issue = make_issue("ACM-700", "blocked", assignee_id=None)
        fake.issues = [issue]
        fake.timelines[issue["id"]] = [status_changed("blocked", BASE_NOW - timedelta(minutes=40))]
        fake.add_comment(issue["id"], comment(
            "no mention", BASE_NOW - timedelta(minutes=16), author_type="agent", author_id=AGENT))
        state = initial_state()
        first = run_scan(cli, state, True, BASE_NOW, fake.monotonic)
        self.assertIn("unwoken_handoff",
                      {f["reason"] for f in first["per_issue"][issue["id"]]["findings"]})
        # Incremental run with unchanged fingerprint still refreshes runs.
        fake.calls.clear()
        second = run_scan(cli, state, False, BASE_NOW, fake.monotonic)
        runs_calls = [c for c in fake.calls if "runs" in c]
        self.assertTrue(runs_calls)
        # next_due_at includes the 15-min threshold -> deep again when due.
        self.assertIn("unwoken_handoff",
                      {f["reason"] for f in second["per_issue"][issue["id"]]["findings"]})


TARGET_UUID = "11111111-2222-3333-4444-555555555555"
OTHER_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class DispatchCase(UnwokenCase):
    def add_dispatch(self, identifier="ACM-600", assignee_id=AGENT,
                     content="需要你做：实现方案并回报结果。",
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


class TestDispatchNoWake(DispatchCase):
    def test_one_nowake_written(self):
        issue = self.add_dispatch()
        code, out, err = self._run_at("2026102221")
        self.assertEqual(code, 0, err)
        posted = self.nowake_comments(issue)
        self.assertEqual(len(posted), 1)
        lines = posted[0]["content"].splitlines()
        self.assertTrue(lines[0].startswith("不需要你做，下一步由 副调度 做："))
        self.assertTrue(lines[1].startswith(f"[patrol-nowake:{issue['id']}:lc-1"))
        self.assertIn(DISP_MENTION, posted[0]["content"])
        self.assertIn("lc-1", posted[0]["content"])
        self.assertIn(iso(BASE_NOW - timedelta(minutes=40)), posted[0]["content"])
        self.assertIn("WorkerAgent", posted[0]["content"])
        self.assertIn("15", posted[0]["content"])
        state = Store(self.state_dir).load()
        rec = state["unwoken"][issue["id"]]
        self.assertEqual(rec["state"], "sent")
        self.assertEqual(rec["reason"], "dispatch_no_wake")
        self.assertEqual(last_result(out)["unwoken_sent"], 1)
        ledger_body = self.fake.ledger_comments()[0]["content"]
        self.assertIn("dispatch_no_wake", ledger_body)

    def test_name_only_other_and_member_mentions_still_fire(self):
        for n, content in enumerate((
                "需要你做：改完回报 [@副调度](mention://agent/副调度)",
                f"需要你做：派活给 [@x](mention://agent/{OTHER_UUID})",
                f"需要你做：[@y](mention://member/{OTHER_UUID}) 已知悉")):
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
            self.assertEqual(len(self.nowake_comments(issue)), 1, content)
            self.tmp2.cleanup()

    def test_valid_uuid_mention_of_target_suppresses(self):
        issue = self.add_dispatch(
            assignee_id=TARGET_UUID,
            content=f"需要你做：[@t](mention://agent/{TARGET_UUID}) 开工")
        code, out, err = self._run_at("2026102221")
        self.assertEqual(self.nowake_comments(issue), [])

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
                self.assertEqual(len(self.nowake_comments(issue)), expect, err)
                self.tmp2.cleanup()

    def test_first_line_target_resolution(self):
        # `下一步由 WorkerAgent 做` names the target; assignee is someone else.
        issue = self.add_dispatch(
            assignee_id="agent-other",
            content="不需要你做，下一步由 WorkerAgent 做：继续实现。")
        code, out, err = self._run_at("2026102221")
        posted = self.nowake_comments(issue)
        self.assertEqual(len(posted), 1)
        self.assertIn("WorkerAgent", posted[0]["content"])
        # A valid @ of the named target suppresses even though assignee differs.
        self.tmp2 = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp2.name)
        self.fake = FakeMultica()
        self.fake.agents.append({"id": TARGET_UUID, "name": "WorkerAgent"})
        self.fake.issues = [
            make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID, assignee_id=None),
            make_issue("ACM-216", "in_progress", uid=config.INBOX_ISSUE_ID, assignee_id=None)]
        state = Store(self.state_dir).load()
        state["agent_names"] = {config.DISPATCHER_AGENT_ID: "副调度", TARGET_UUID: "WorkerAgent"}
        Store(self.state_dir).save(state)
        issue = self.add_dispatch(
            assignee_id="agent-other",
            content=f"不需要你做，下一步由 WorkerAgent 做：[@t](mention://agent/{TARGET_UUID}) 开工")
        code, out, err = self._run_at("2026102221")
        self.assertEqual(self.nowake_comments(issue), [])
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
            self.assertEqual(self.nowake_comments(issue), [], kw["content"])
            self.tmp2.cleanup()
        # unassigned in_progress is covered by a different rule
        issue = self.add_dispatch("ACM-601", assignee_id=None)
        code, out, err = self._run_at("2026102221")
        self.assertEqual(self.nowake_comments(issue), [])

    def test_exempt_targets_no_write(self):
        for ident, uid in (("ACM-215", None), ("ACM-216", config.INBOX_ISSUE_ID),
                           ("ACM-229", config.LEDGER_ISSUE_ID)):
            issue = self.add_dispatch(ident, cid=f"lc-{ident}")
            if uid:
                issue["id"] = uid
        code, out, err = self._run_at("2026102221")
        total = sum(len(self.nowake_comments(i)) for i in self.fake.issues)
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
            self.assertEqual(len(self.nowake_comments(issue)), expect, mins)
            self.tmp2.cleanup()

    def test_dedupe_24h_and_shared_with_rule9(self):
        issue = self.add_dispatch()
        self._run_at("2026102221")
        self._run_at("2026102222")
        self.assertEqual(len(self.nowake_comments(issue)), 1)
        # >24h after the first send a new dispatch comment can be supplemented.
        t2 = BASE_NOW + timedelta(hours=25)
        issue["updated_at"] = iso(t2)
        issue["last_activity_at"] = iso(t2)
        self.fake.add_comment(issue["id"], comment(
            "需要你做：再补改一轮。", t2 - timedelta(minutes=20), cid="lc-2",
            author_type="agent", author_id=config.DISPATCHER_AGENT_ID))
        self._run_at(self._slot("2026102221", 25))
        self.assertEqual(len(self.nowake_comments(issue)), 2)
        # Shared with the rule-9 record: an issue that got a handoff supplement
        # cannot also get a nowake supplement within 24h.
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
        issue = self.add_handoff()  # blocked/in_review supplement at slot 1
        self._run_at("2026102221")
        self.assertEqual(len(self.unwoken_comments(issue)), 1)
        # Same issue becomes in_progress with a dispatch comment one slot later.
        issue["status"] = "in_progress"
        issue["updated_at"] = iso(BASE_NOW + timedelta(hours=1))
        self.fake.timelines[issue["id"]] = [
            status_changed("in_progress", BASE_NOW + timedelta(minutes=30))]
        self.fake.add_comment(issue["id"], comment(
            "需要你做：返工重做。", BASE_NOW + timedelta(minutes=45), cid="lc-d",
            author_type="agent", author_id=config.DISPATCHER_AGENT_ID))
        self._run_at("2026102222")
        self.assertEqual(self.nowake_comments(issue), [])
        self.tmp2.cleanup()

    def test_shared_cap_with_rule9(self):
        # 2 blocked/in_review handoffs + 3 dispatch issues -> total sent == cap.
        for i in range(2):
            self.add_handoff(f"ACM-{700 + i}", cid=f"lc-h{i}")
        issues = [self.add_dispatch(f"ACM-{710 + i}", cid=f"lc-d{i}") for i in range(3)]
        code, out, err = self._run_at("2026102221")
        sent = sum(len(self.unwoken_comments(i)) + len(self.nowake_comments(i))
                   for i in self.fake.issues)
        self.assertEqual(sent, config.MAX_UNWOKEN_PER_RUN)
        self.assertIn("unwoken_deferred", self.fake.ledger_comments()[0]["content"])

    def test_dry_run_zero_writes(self):
        issue = self.add_dispatch()
        code, out, err = invoke(self.fake, ["run", "--slot", "2026102221", "--dry-run"],
                                state_dir=self.state_dir)
        self.assertEqual(last_result(out)["unwoken_would_send"], 1)
        self.assertEqual(self.nowake_comments(issue), [])
        state = Store(self.state_dir).load()
        self.assertEqual(state.get("unwoken") or {}, {})

    def test_guard_race_and_uncertain_no_blind_resend(self):
        issue = self.add_dispatch()
        real_exec = self.fake.exec
        def racer(argv):
            args = argv[1:]
            words = [a for a in args if not a.startswith("-")]
            if words[:3] == ["issue", "comment", "list"] and words[3] == issue["id"] \
                    and "--since" in args:
                self.fake.add_comment(issue["id"], comment(
                    "member arrived", BASE_NOW + timedelta(minutes=1), cid="race"))
            return real_exec(argv)
        self.fake.exec = racer
        code, out, err = self._run_at("2026102221")
        self.assertEqual(self.nowake_comments(issue), [])
        # Uncertain post: pending recorded, no blind resend; next slot delivers.
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
        issue = self.add_dispatch()
        def timeout_issue_writes(argv):
            args = argv[1:]
            words = [a for a in args if not a.startswith("-")]
            if words[:3] == ["issue", "comment", "add"] and words[3] == issue["id"]:
                raise subprocess.TimeoutExpired(argv, 90)
            return None
        self.fake.fail_handler = timeout_issue_writes
        self._run_at("2026102221")
        state = Store(self.state_dir).load()
        self.assertEqual(state["unwoken"][issue["id"]]["state"], "pending")
        self.assertEqual(self.nowake_comments(issue), [])
        self.fake.fail_handler = None
        self._run_at("2026102222")
        self.assertEqual(len(self.nowake_comments(issue)), 1)
        self.tmp2.cleanup()

    def test_nowake_marker_accepted_by_write_allowlist(self):
        cli = PatrolCLI(allowed_write_ids=frozenset({config.INBOX_ISSUE_ID}),
                        env=GOOD_ENV, _exec=self.fake.exec, _sleep=lambda s: None, retries=0)
        p = self.state_dir / "n.md"
        p.write_text("不需要你做，下一步由 副调度 做：x\n[patrol-nowake:u:c]\n"
                     + DISP_MENTION + "\n")
        cli.permit_unwoken("uuid-ACM-601")
        self.assertIsNotNone(cli.run_unwoken_write(
            "uuid-ACM-601", p, marker_prefix=config.NOWAKE_PREFIX))
        # A nowake marker does not satisfy the rule-9 prefix, and vice versa.
        with self.assertRaises(CommandNotAllowed):
            cli.run_unwoken_write("uuid-ACM-601", p)
        p.write_text("不需要你做，下一步由 副调度 做：x\n[patrol-unwoken:u:c]\n"
                     + DISP_MENTION + "\n")
        with self.assertRaises(CommandNotAllowed):
            cli.run_unwoken_write("uuid-ACM-601", p, marker_prefix=config.NOWAKE_PREFIX)


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


if __name__ == "__main__":
    unittest.main()
