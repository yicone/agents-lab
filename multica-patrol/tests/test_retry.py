"""Provider-error retry machine tests. ALL data are synthetic offline fixtures —
none of this is output of a natural patrol run."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from patrol import config
from patrol.cli import CommandNotAllowed, PatrolCLI
from patrol.state import Store
from tests.fakes import (BASE_NOW, GOOD_ENV, FakeMultica, comment, iso, invoke,
                         last_result, make_issue, run)

AGENT = "agent-worker-1"


def error_run(rid="r-err", agent=AGENT, minutes_ago=30):
    return run("completed", rid=rid, agent_id=agent,
               started_at=BASE_NOW - timedelta(minutes=minutes_ago + 2),
               completed_at=BASE_NOW - timedelta(minutes=minutes_ago))


def error_comment(run_, cid="err-c", content="\n\n[Error] provider exploded"):
    return comment(content, BASE_NOW - timedelta(minutes=30), cid=cid,
                   author_type="agent", author_id=run_["agent_id"])


class RetryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir = Path(self.tmp.name)
        self.fake = FakeMultica()
        self.fake.agents.append({"id": AGENT, "name": "WorkerAgent"})
        self.fake.issues = [
            make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID, assignee_id=None),
            make_issue("ACM-487", "in_progress", uid=config.INBOX_ISSUE_ID, assignee_id=None),
        ]
        # Pre-seed agent names used in retry/escalation bodies.
        state = Store(self.state_dir).load()
        state["agent_names"] = {config.DISPATCHER_AGENT_ID: "副调度", AGENT: "WorkerAgent"}
        Store(self.state_dir).save(state)

    def add_error_issue(self, identifier="ACM-335", agent=AGENT, minutes_ago=30):
        issue = make_issue(identifier, "in_progress", assignee_id=agent)
        r = error_run(agent=agent, minutes_ago=minutes_ago)
        self.fake.issues.append(issue)
        self.fake.runs[issue["id"]] = [r]
        self.fake.add_comment(issue["id"], error_comment(r))
        return issue, r

    def source_comments(self, issue):
        return self.fake.comments.get(issue["id"], [])

    def retry_comments(self, issue):
        return [c for c in self.source_comments(issue)
                if "[patrol-retry:" in c.get("content", "")]


class TestDetection(RetryCase):
    def run_dry(self):
        code, out, err = invoke(self.fake, ["run", "--slot", "2026102221",
                                            "--dry-run"], state_dir=self.state_dir)
        return code, out, err

    def test_completed_with_error_detected(self):
        self.add_error_issue()
        code, out, err = self.run_dry()
        self.assertEqual(code, 0, err)
        self.assertIn("completed_provider_error", out)
        self.assertIn("provider 报错重试", out)
        self.assertIn("would_retry", out)
        res = last_result(out)
        self.assertEqual(res["retries_would_send"], 1)
        self.assertEqual(self.fake.comment_add_count, 0)  # dry-run: zero writes

    def test_completed_without_error_not_detected(self):
        issue, r = self.add_error_issue()
        self.fake.comments[issue["id"]] = [comment("done fine", BASE_NOW - timedelta(minutes=30),
                                                   author_type="agent", author_id=AGENT)]
        code, out, _ = self.run_dry()
        self.assertNotIn("completed_provider_error", out)
        self.assertNotIn("provider 报错重试", out)

    def test_error_but_run_not_completed_not_detected(self):
        issue = make_issue("ACM-336", "in_progress", assignee_id=AGENT)
        r = run("failed", rid="r-fail", agent_id=AGENT,
                started_at=BASE_NOW - timedelta(minutes=32),
                completed_at=BASE_NOW - timedelta(minutes=30))
        self.fake.issues.append(issue)
        self.fake.runs[issue["id"]] = [r]
        self.fake.add_comment(issue["id"], error_comment(r))
        code, out, _ = self.run_dry()
        self.assertNotIn("provider 报错重试", out)
        self.assertEqual(self.retry_comments(issue), [])

    def test_error_by_different_author_not_detected(self):
        issue, r = self.add_error_issue()
        self.fake.comments[issue["id"]] = [comment("[Error] nope", BASE_NOW - timedelta(minutes=30),
                                                  author_type="agent", author_id="someone-else")]
        code, out, _ = self.run_dry()
        self.assertNotIn("provider 报错重试", out)

    def test_error_outside_window_not_detected(self):
        issue, r = self.add_error_issue()
        self.fake.comments[issue["id"]] = [comment(
            "[Error] late", BASE_NOW - timedelta(minutes=5),  # 25min after completed_at
            author_type="agent", author_id=AGENT)]
        code, out, _ = self.run_dry()
        self.assertNotIn("provider 报错重试", out)

    def test_stale_error_reported_no_write(self):
        issue, r = self.add_error_issue(minutes_ago=60 * 7)  # > 6h window
        c = error_comment(r)
        c["created_at"] = iso(BASE_NOW - timedelta(minutes=60 * 7))
        self.fake.comments[issue["id"]] = [c]
        code, out, err = self.run_dry()
        self.assertIn("retry_skipped_stale", out)
        code, out, err = invoke(self.fake, ["run", "--slot", "2026102221"],
                                state_dir=self.state_dir)
        self.assertEqual(code, 0, err)
        self.assertIn("retry_skipped_stale", self.fake.ledger_comments()[0]["content"])
        self.assertEqual(self.retry_comments(issue), [])


class TestRetryWrite(RetryCase):
    def test_first_detection_posts_one_retry_comment(self):
        issue, r = self.add_error_issue()
        code, out, err = invoke(self.fake, ["run", "--slot", "2026102221"],
                                state_dir=self.state_dir)
        self.assertEqual(code, 0, err)
        posted = self.retry_comments(issue)
        self.assertEqual(len(posted), 1)
        lines = posted[0]["content"].splitlines()
        self.assertEqual(lines[0], "不需要你做，下一步由 WorkerAgent 做：重试上一轮以 [Error] 结束的原任务。")
        self.assertTrue(lines[1].startswith(f"[patrol-retry:{issue['id']}:{r['id']}"))
        self.assertIn(f"mention://agent/{AGENT}", lines[2])
        self.assertIn("原任务要求不变", lines[2])  # original task requirement unchanged
        res = last_result(out)
        self.assertEqual(res["retries_sent"], 1)
        state = Store(self.state_dir).load()
        self.assertEqual(state["error_retry"][issue["id"]]["state"], "retried")

    def test_guards_block_write(self):
        cases = {
            "active_run": lambda i, r: self.fake.runs.__setitem__(i["id"], [run("queued", rid="r-q")] + self.fake.runs[i["id"]]),
            "assignee_changed": lambda i, r: i.__setitem__("assignee_id", "agent-other-2"),
            "member_replied": lambda i, r: self.fake.add_comment(i["id"], comment("seen", BASE_NOW - timedelta(minutes=25))),
            "issue_done": lambda i, r: i.__setitem__("status", "done"),
        }
        for why, mutate in cases.items():
            with self.subTest(why=why):
                self.tmp2 = tempfile.TemporaryDirectory()
                self.state_dir = Path(self.tmp2.name)
                self.fake = FakeMultica()
                self.fake.agents.append({"id": AGENT, "name": "WorkerAgent"})
                self.fake.issues = [
                    make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID, assignee_id=None),
                    make_issue("ACM-487", "in_progress", uid=config.INBOX_ISSUE_ID, assignee_id=None)]
                issue, r = self.add_error_issue()
                if why == "assignee_changed":
                    # detection requires pe.agent_id == assignee at guard time
                    issue["assignee_id"] = "agent-other-2"
                elif why == "issue_done":
                    issue["status"] = "done"
                elif why == "active_run":
                    self.fake.runs[issue["id"]] = [run("queued", rid="r-q")] + self.fake.runs[issue["id"]]
                elif why == "member_replied":
                    self.fake.add_comment(issue["id"], comment("ok seen", BASE_NOW - timedelta(minutes=25)))
                code, out, err = invoke(self.fake, ["run", "--slot", "2026102221"],
                                        state_dir=self.state_dir)
                self.assertEqual(self.retry_comments(issue), [])
                self.tmp2.cleanup()

    def test_duplicate_marker_no_second_write(self):
        issue, r = self.add_error_issue()
        self.fake.add_comment(issue["id"], comment(
            f"x [patrol-retry:{issue['id']}:{r['id']}] already", BASE_NOW - timedelta(minutes=29),
            author_type="agent", author_id=config.DISPATCHER_AGENT_ID))
        code, out, _ = invoke(self.fake, ["run", "--slot", "2026102221"],
                              state_dir=self.state_dir)
        self.assertEqual(len(self.retry_comments(issue)), 1)  # only the pre-existing one
        state = Store(self.state_dir).load()
        self.assertEqual(state["error_retry"][issue["id"]]["state"], "retried")


class TestRetryLifecycle(RetryCase):
    def _slot(self, base, hours):
        dt = self._dt(base) + timedelta(hours=hours)
        return dt.strftime("%Y%m%d%H")

    @staticmethod
    def _dt(slot):
        from datetime import datetime, timezone
        return datetime.strptime(slot, "%Y%m%d%H").replace(tzinfo=timezone(config.SGT_OFFSET))

    def _run_at(self, slot):
        from datetime import timezone as tz
        now = self._dt(slot).astimezone(tz.utc)
        self.fake.now = now
        return invoke(self.fake, ["run", "--slot", slot], now=now, state_dir=self.state_dir)

    def test_retry_dedupe_across_consecutive_slots(self):
        issue, r = self.add_error_issue()
        self._run_at("2026102221")
        self.assertEqual(len(self.retry_comments(issue)), 1)
        # Same error, next hourly slot: no duplicate retry comment.
        code, out, err = self._run_at("2026102222")
        self.assertEqual(len(self.retry_comments(issue)), 1)
        self.assertEqual(last_result(out)["retries_sent"], 0)

    def test_no_retry_write_on_unresolved_acm215(self):
        issue, r1 = self.add_error_issue()
        issue["identifier"] = "ACM-215"
        code, out, err = self._run_at("2026102221")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.retry_comments(issue), [])
        self.assertEqual(len(self.fake.inbox_comments()), 0)
        self.assertEqual(last_result(out)["retries_sent"], 0)

    def test_second_error_escalates_once_no_second_mention(self):
        issue, r1 = self.add_error_issue()
        self._run_at("2026102221")
        # Newer run, also completed with [Error] (times relative to run 2's now).
        t2 = BASE_NOW + timedelta(hours=1)
        r2 = run("completed", rid="r-err2", agent_id=AGENT,
                 started_at=t2 - timedelta(minutes=12),
                 completed_at=t2 - timedelta(minutes=10))
        self.fake.runs[issue["id"]] = [r2] + self.fake.runs[issue["id"]]
        self.fake.add_comment(issue["id"], {
            "id": "err-c2", "content": "[Error] again", "author_type": "agent",
            "author_id": AGENT, "created_at": iso(t2 - timedelta(minutes=10)),
            "type": "comment"})
        issue["updated_at"] = iso(t2)
        code, out, err = self._run_at("2026102222")
        self.assertEqual(len(self.retry_comments(issue)), 1)  # no second mention
        self.assertEqual(len(self.fake.inbox_comments()), 1)
        body = self.fake.inbox_comments()[0]["content"]
        self.assertTrue(body.splitlines()[0].startswith("不需要你做，下一步由 副调度 做：核对 ACM-335"))
        self.assertIn("[patrol-escalate:ERR-", body)
        self.assertNotIn("mention://agent/", body)
        # Escalated once: next run does not repost.
        code, out, _ = self._run_at("2026102223")
        self.assertEqual(len(self.fake.inbox_comments()), 1)

    def test_retry_then_clean_run_recovers(self):
        issue, r1 = self.add_error_issue()
        self._run_at("2026102221")
        r2 = run("completed", rid="r-ok", agent_id=AGENT,
                 started_at=BASE_NOW + timedelta(minutes=50),
                 completed_at=BASE_NOW + timedelta(minutes=55))
        self.fake.runs[issue["id"]] = [r2] + self.fake.runs[issue["id"]]
        self.fake.add_comment(issue["id"], comment(
            "finished ok", BASE_NOW + timedelta(minutes=55), author_type="agent", author_id=AGENT))
        issue["updated_at"] = iso(BASE_NOW + timedelta(hours=1))
        code, out, err = self._run_at("2026102222")
        self.assertEqual(code, 0, err)
        state = Store(self.state_dir).load()
        self.assertNotIn(issue["id"], state["error_retry"])
        self.assertEqual(len(self.fake.inbox_comments()), 0)

    def test_no_new_run_after_2h_escalates(self):
        issue, r1 = self.add_error_issue()
        self._run_at("2026102221")
        code, out, _ = self._run_at("2026102223")  # +2h, no newer run
        self.assertEqual(len(self.fake.inbox_comments()), 1)
        self.assertIn("retry_not_started", self.fake.inbox_comments()[0]["content"])

    def test_retry_run_still_active_after_2h_does_not_escalate(self):
        issue, r1 = self.add_error_issue()
        self._run_at("2026102221")
        r2 = run("running", rid="r-active", agent_id=AGENT,
                 started_at=BASE_NOW + timedelta(minutes=50), completed_at=None)
        self.fake.runs[issue["id"]] = [r2] + self.fake.runs[issue["id"]]
        issue["updated_at"] = iso(BASE_NOW + timedelta(hours=2))
        self._run_at("2026102223")
        self.assertEqual(len(self.fake.inbox_comments()), 0)

    def test_uncertain_retry_post_readback(self):
        issue, r = self.add_error_issue()
        # Write times out but the comment actually landed.
        self.fake.write_mode = "timeout_found"
        code, out, err = invoke(self.fake, ["run", "--slot", "2026102221"],
                                state_dir=self.state_dir)
        state = Store(self.state_dir).load()
        self.assertEqual(state["error_retry"][issue["id"]]["state"], "retried")
        self.assertEqual(len(self.retry_comments(issue)), 1)

    def test_uncertain_retry_lost_no_blind_resend(self):
        issue, r = self.add_error_issue()

        def lose_only_issue_writes(argv):
            args = argv[1:]
            words = [a for a in args if not a.startswith("-")]
            if words[:3] == ["issue", "comment", "add"] and words[3] == issue["id"]:
                raise subprocess.TimeoutExpired(argv, 90)
            return None
        self.fake.fail_handler = lose_only_issue_writes
        code, out, err = invoke(self.fake, ["run", "--slot", "2026102221"],
                                state_dir=self.state_dir)
        state = Store(self.state_dir).load()
        self.assertEqual(state["error_retry"][issue["id"]]["state"], "pending")
        self.assertEqual(self.retry_comments(issue), [])
        # Next run reconciles: still absent -> pending record resends via guards.
        self.fake.fail_handler = None
        code, out, err = invoke(self.fake, ["run", "--slot", "2026102222"],
                                now=BASE_NOW + timedelta(hours=1), state_dir=self.state_dir)
        self.assertEqual(len(self.retry_comments(issue)), 1)

    def test_dispatcher_error_escalates_directly(self):
        issue, r = self.add_error_issue(agent=config.DISPATCHER_AGENT_ID)
        r["agent_id"] = config.DISPATCHER_AGENT_ID
        self.fake.comments[issue["id"]] = [comment(
            "[Error] boom", BASE_NOW - timedelta(minutes=30),
            author_type="agent", author_id=config.DISPATCHER_AGENT_ID)]
        code, out, err = invoke(self.fake, ["run", "--slot", "2026102221"],
                                state_dir=self.state_dir)
        self.assertEqual(len(self.retry_comments(issue)), 0)
        self.assertEqual(len(self.fake.inbox_comments()), 1)
        self.assertIn("[patrol-escalate:ERR-", self.fake.inbox_comments()[0]["content"])

    def test_max_three_retries_per_run(self):
        issues = [self.add_error_issue(f"ACM-{400 + i}")[0] for i in range(5)]
        code, out, err = invoke(self.fake, ["run", "--slot", "2026102221"],
                                state_dir=self.state_dir)
        sent = sum(len(self.retry_comments(i)) for i in issues)
        self.assertEqual(sent, config.MAX_RETRIES_PER_RUN)
        self.assertIn("retry_deferred", self.fake.ledger_comments()[0]["content"])

    def test_identity_failure_no_writes(self):
        issue, r = self.add_error_issue()
        env = dict(GOOD_ENV, MULTICA_AGENT_ID="wrong-agent")
        code, out, err = invoke(self.fake, ["run", "--slot", "2026102221"], env=env,
                                state_dir=self.state_dir)
        self.assertEqual(code, 3)
        self.assertEqual(self.fake.comment_add_count, 0)


class TestRetryAllowlist(RetryCase):
    def test_unregistered_id_refused(self):
        cli = PatrolCLI(allowed_write_ids=frozenset(
            {config.LEDGER_ISSUE_ID, config.INBOX_ISSUE_ID}),
            env=GOOD_ENV, _exec=self.fake.exec, _sleep=lambda s: None, retries=0)
        p = self.state_dir / "b.md"
        p.write_text("不需要你做，下一步由 X 做：y\n[patrol-retry:u:r]\nbody")
        with self.assertRaises(CommandNotAllowed):
            cli.run_retry_write("uuid-ACM-999", p)
        cli.permit_retry("uuid-ACM-999")
        res = cli.run_retry_write("uuid-ACM-999", p)
        self.assertEqual(res.data["author_type"], "agent")

    def test_bad_retry_body_refused(self):
        cli = PatrolCLI(allowed_write_ids=frozenset({config.INBOX_ISSUE_ID}),
                        env=GOOD_ENV, _exec=self.fake.exec, _sleep=lambda s: None, retries=0)
        cli.permit_retry("uuid-ACM-9")
        p = self.state_dir / "b.md"
        p.write_text("random first line\n[patrol-retry:u:r]\n")
        with self.assertRaises(CommandNotAllowed):
            cli.run_retry_write("uuid-ACM-9", p)
        p.write_text("不需要你做，下一步由 X 做：y\nno marker\n")
        with self.assertRaises(CommandNotAllowed):
            cli.run_retry_write("uuid-ACM-9", p)


if __name__ == "__main__":
    unittest.main()
