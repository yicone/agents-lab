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


if __name__ == "__main__":
    unittest.main()
