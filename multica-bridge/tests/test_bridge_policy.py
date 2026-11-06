"""Routing, missing-credential fallback, actor gate, system comments, circuit alerts."""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agent_delivery as ad  # noqa: E402
import outbox as ob  # noqa: E402
import sync_install  # noqa: E402

NOW = datetime(2026, 11, 6, 8, 0, tzinfo=timezone.utc)
PIGI = "2b6f96c1-d0a4-49cb-8472-7a301a42b7bf"
ATLAS = "ad48897b-bd8e-4268-8ee7-e1ede97e48d8"
RESEARCH = "c5266ff4-1dce-45c2-b865-675d09a3473c"
URL_RE = re.compile(r"https?://", re.I)
BEARER_RE = re.compile(r"bearer\s+[A-Za-z0-9]", re.I)
HOOK_RE = re.compile(r"hooks\.slack\.com|xox[baprs]-", re.I)
REQUIRED_WORKSPACES = {
    "clipvibe", "yicone-brand", "storage-backup", "english-study", "aimaiai",
    "elder-oasis", "sprout-coach", "podcast-foundry", "posts-rebuild",
    "acm-lab", "news-for-me",
}


def load_routes():
    return json.loads((ROOT / "routes.json").read_text(encoding="utf-8"))


def route_for(routes, slug, project_id):
    return ob.matched_route(routes, slug, project_id)


def load_bridge():
    spec = importlib.util.spec_from_file_location(
        "multica_status_bridge_under_test", ROOT / "multica-status-bridge.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResolveDestTests(unittest.TestCase):
    def setUp(self):
        self.routes = load_routes()

    def dest(self, **change):
        return ob.resolve_dest(change, self.routes)

    def test_priority_project_then_title_then_failure_then_workspace(self):
        self.assertEqual(self.dest(
            kind="status", project_id=ATLAS, workspace_slug="acm-lab", title="调研 Atlas 术语",
        ), ("atlas-pm", "project"))
        self.assertEqual(self.dest(
            kind="status", project_id=ATLAS, workspace_slug="acm-lab", title="Bot 描述入 Git：Atlas PM",
        ), ("steward", "title"))
        self.assertEqual(self.dest(
            kind="run_failed", workspace_slug="acm-lab", title="调研 memory",
        ), ("agent-research", "title"))
        self.assertEqual(self.dest(
            kind="run_failed", workspace_slug="acm-lab", title="普通故障",
        ), ("multica-ops", "platform_failure"))
        self.assertEqual(self.dest(
            kind="status", workspace_slug="aimaiai", title="上架", new_status="in_review",
        ), ("aimaiai-pm", "workspace"))
        self.assertEqual(self.dest(
            kind="parse_failed", workspace_slug="aimaiai", title="bad json",
        ), ("steward", "platform_failure"))
        self.assertEqual(self.dest(
            kind="status", workspace_slug="unknown-space", title="x",
        ), ("steward", "fallback"))

    def test_project_prefixes_and_news_titles(self):
        self.assertEqual(self.dest(
            kind="run_failed", project_id=PIGI, workspace_slug="acm-lab", title="发送失败",
        )[0], "pigi-pm")
        self.assertEqual(self.dest(
            kind="status", project_id=RESEARCH, workspace_slug="acm-lab", title="实验",
        )[0], "agent-research")
        self.assertEqual(self.dest(
            kind="status", project_id="091a2815-1234-5678-9abc-def012345678",
            workspace_slug="acm-lab", title="开源",
        )[0], "oss-watch")
        self.assertEqual(self.dest(
            kind="status", project_id="e932a979-0000-4000-8000-000000000001",
            workspace_slug="sprout-coach", title="小程序",
        )[0], "sprout-planet-pm")
        self.assertEqual(self.dest(
            kind="status", project_id="97b619ff-aaaa-bbbb-cccc-ddddeeeeffff",
            workspace_slug="news-for-me", title="其他",
        )[0], "oss-watch")
        self.assertEqual(self.dest(
            kind="status", project_id="5d090c76-1111-2222-3333-444444444444",
            workspace_slug="news-for-me", title="其他",
        )[0], "agent-research")
        self.assertEqual(self.dest(
            kind="status", workspace_slug="news-for-me", title="AI Agent 日报 11-06",
        )[0], "agent-research")
        self.assertEqual(self.dest(
            kind="status", workspace_slug="news-for-me", title="今日 GitHub 热榜",
        )[0], "oss-watch")
        self.assertEqual(self.dest(
            kind="status", workspace_slug="news-for-me", title="普通资讯",
        ), ("info-pipeline", "workspace"))

    def test_no_project_title_fallbacks_and_workspaces(self):
        self.assertEqual(self.dest(
            kind="status", workspace_slug="acm-lab", title="Atlas 验收单",
        )[0], "atlas-pm")
        self.assertEqual(self.dest(
            kind="status", workspace_slug="acm-lab", title="Pigi 可行性",
        )[0], "pigi-pm")
        expected = {
            "clipvibe": "clipvibe-pm",
            "yicone-brand": "brand-pm",
            "storage-backup": "storage-backup",
            "english-study": "english-study",
            "elder-oasis": "eo-pm",
            "podcast-foundry": "secretary",
            "posts-rebuild": "secretary",
            "sprout-coach": "sprout-coach",
        }
        for slug, ref in expected.items():
            self.assertEqual(self.dest(kind="status", workspace_slug=slug, title="状态")[0], ref, slug)
        self.assertTrue(REQUIRED_WORKSPACES <= set(self.routes["workspaces"]))

    def test_resolve_dest_does_not_raise_on_bad_routes(self):
        self.assertEqual(ob.resolve_dest({"kind": "status"}, {"projects": ["bad"]}), ("steward", "fallback"))


class MissingCredTests(unittest.TestCase):
    def test_hold_keeps_the_item_and_does_not_burn_attempts(self):
        routes = load_routes()
        box = ob.empty_outbox()
        change = {
            "kind": "status", "issue_id": "issue-pigi", "new_status": "in_review",
            "project_id": PIGI, "workspace_slug": "acm-lab", "title": "Pigi inbox",
            "updated_at": ob.iso(NOW),
        }
        self.assertEqual(ob.enqueue(box, change, NOW, routes=routes), "added")
        item = next(iter(box["items"].values()))
        self.assertEqual(item["dest"], "pigi-pm")

        def poster_for(dest):
            def poster(_payloads):
                if dest == "pigi-pm":
                    return None, None, "no_cred"
                raise AssertionError(dest)
            return poster

        logs = ob.drain(
            box, routes=routes, now=NOW, poster=lambda *_: (200, None, None),
            poster_for=poster_for, route_for=route_for,
        )
        self.assertEqual(item["attempts"], 0)
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["hold_reason"], "no_cred")
        self.assertTrue(any("hold dest=pigi-pm reason=no_cred" in line for line in logs))
        ob.drain(
            box, routes=routes, now=NOW, poster=lambda *_: (200, None, None),
            poster_for=poster_for, route_for=route_for,
        )
        self.assertEqual(item["attempts"], 0)
        self.assertEqual(item["status"], "pending")

    def test_fallback_moves_to_a_configured_dest_without_raising(self):
        routes = {
            "fanout": "send",
            "steward_dest": "steward",
            "missing_cred": {"default": "fallback", "fallback_dest": "steward"},
            "platform_failure": {"dest": "multica-ops"},
            "workspaces": {"acm-lab": {"endpoint_ref": "steward"}},
        }
        box = ob.empty_outbox()
        change = {
            "kind": "run_failed", "issue_id": "ops-1", "workspace_slug": "acm-lab",
            "title": "daemon down", "updated_at": ob.iso(NOW),
        }
        self.assertEqual(ob.resolve_dest(change, routes), ("multica-ops", "platform_failure"))
        ob.enqueue(box, change, NOW, routes=routes)
        calls = []

        def poster_for(dest):
            def poster(_payloads):
                calls.append(dest)
                if dest == "multica-ops":
                    return None, None, "no_cred"
                if dest == "steward":
                    return 200, None, None
                raise AssertionError(dest)
            return poster

        logs = ob.drain(
            box, routes=routes, now=NOW, poster=lambda *_: (500, None, "error"),
            poster_for=poster_for, route_for=route_for,
        )
        item = next(iter(box["items"].values()))
        self.assertEqual(item["dest"], "steward")
        self.assertEqual(item["status"], "accepted")
        self.assertEqual(item["attempts"], 1)
        self.assertIn("multica-ops", calls)
        self.assertIn("steward", calls)
        self.assertTrue(any("fallback dest=multica-ops to=steward reason=no_cred" in line for line in logs))

    def test_fallback_without_cred_holds_instead_of_looping(self):
        routes = {
            "fanout": "send",
            "missing_cred": {"default": "fallback", "fallback_dest": "steward"},
            "platform_failure": {"dest": "multica-ops"},
        }
        box = ob.empty_outbox()
        ob.enqueue(box, {
            "kind": "net_alert", "issue_id": "n1", "workspace_slug": "acm-lab",
            "title": "timeout", "updated_at": ob.iso(NOW),
        }, NOW, routes=routes)

        def poster_for(_dest):
            return lambda _payloads: (None, None, "no_cred")

        ob.drain(
            box, routes=routes, now=NOW, poster=lambda *_: (None, None, "no_cred"),
            poster_for=poster_for, route_for=route_for,
        )
        item = next(iter(box["items"].values()))
        self.assertEqual(item["attempts"], 0)
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["hold_reason"], "no_cred")
        self.assertEqual(item["dest"], "steward")


class ActorAndDeliveryTests(unittest.TestCase):
    def test_only_the_latest_agent_actor_is_queued(self):
        member_latest = [
            {"id": "1", "action": "status_changed", "created_at": "2026-11-06T01:00:00Z",
             "actor_type": "agent", "details": {"to": "in_review"}},
            {"id": "2", "action": "status_changed", "created_at": "2026-11-06T02:00:00Z",
             "actor_type": "member", "details": {"to": "in_review"}},
        ]
        self.assertEqual(ob.latest_status_actor(member_latest, "in_review"), "member")
        self.assertFalse(ob.status_actor_allows("in_review", "member"))
        earlier_agent = {
            "id": "agent-early", "action": "status_changed", "actor_type": "agent",
            "created_at": "2026-11-06T03:00:00Z", "details": {"to": "in_review"},
        }
        later_member = {
            "id": "member-late", "action": "status_changed", "actor_type": "member",
            "created_at": "2026-11-06T03:00:00.100Z", "details": {"to": "in_review"},
        }
        self.assertEqual(ob.latest_status_actor([earlier_agent, later_member], "in_review"), "member")
        self.assertEqual(ob.latest_status_actor([later_member, earlier_agent], "in_review"), "member")
        offset_member = dict(later_member, created_at="2026-11-06T03:00:00.100+00:00")
        self.assertEqual(ob.latest_status_actor([earlier_agent, offset_member], "in_review"), "member")
        later_agent = dict(earlier_agent, id="agent-late", created_at="2026-11-06T03:00:00.100Z")
        earlier_member = dict(later_member, id="member-early", created_at="2026-11-06T03:00:00Z")
        self.assertEqual(ob.latest_status_actor([earlier_member, later_agent], "in_review"), "agent")
        self.assertFalse(ob.status_actor_allows("blocked", "member"))
        agent_latest = [
            {"id": "1", "action": "status_changed", "created_at": "2026-11-06T01:00:00Z",
             "actor_type": "member", "details": {"to": "blocked"}},
            {"id": "2", "action": "status_changed", "created_at": "2026-11-06T03:00:00Z",
             "actor_type": "agent", "details": {"to": "blocked"}},
        ]
        self.assertEqual(ob.latest_status_actor(agent_latest, "blocked"), "agent")
        self.assertTrue(ob.status_actor_allows("blocked", "agent"))
        self.assertTrue(ob.status_actor_allows("in_progress", "member"))
        self.assertIsNone(ob.latest_status_actor([], "in_review"))
        self.assertFalse(ob.status_actor_allows("in_review", None))

    def test_system_comment_is_not_a_delivery(self):
        base = {
            "issue_id": "issue-1", "identifier": "ACM-909", "title": "bridge",
            "status": "in_progress", "assignee_type": "agent", "assignee_id": "builder",
            "workspace_slug": "acm-lab", "comments": [{
                "id": "c-sys", "author_type": "agent", "author_id": "builder",
                "created_at": "2026-11-06T08:00:00Z", "content": "CLI exited 1",
                "is_new": True, "comment_type": "system",
            }],
            "runs": [{
                "id": "r1", "agent_id": "builder", "status": "completed",
                "completed_at": "2026-11-06T08:00:05Z", "is_new": True,
            }],
        }
        self.assertIsNone(ad.collect_for_issue(base))
        decision = ad.decide_delivery(
            status="in_progress", assignee_type="agent", assignee_id="builder",
            comments=[{
                "id": "c2", "author_type": "agent", "author_id": "builder",
                "created_at": "2026-11-06T08:00:00Z", "content": "process failed",
                "is_new": True, "type": "system",
            }],
            runs=[], transitions=[],
        )
        self.assertEqual(decision["action"], "skip")
        self.assertEqual(decision["reason"], "system")

    def test_bridge_wires_the_actor_gate_and_comment_type(self):
        text = (ROOT / "multica-status-bridge.py").read_text(encoding="utf-8")
        self.assertIn("status_actor_allows", text)
        self.assertIn("consider_status", text)
        self.assertIn("hold_pending_issues", text)
        self.assertIn("comment_type", text)
        self.assertIn("saved_cursor", text)


class TimelineCursorTests(unittest.TestCase):
    def bridge(self):
        return load_bridge()

    def test_two_timeouts_then_recovery_notifies_and_rereads_timeline(self):
        bridge = self.bridge()
        prev_row = {"s": "in_progress", "a": "2026-11-05T00:00:00Z", "w": "ws"}
        observed = {"s": "in_review", "a": "2026-11-06T03:00:00.250Z", "w": "ws"}
        stored = {"issue-1": dict(prev_row)}
        cursor = "2026-11-06T01:00:00Z"
        tick = "2026-11-06T04:00:00Z"
        calls = []

        def fetch(_wid, iid):
            calls.append(iid)
            if len(calls) < 3:
                raise subprocess.TimeoutExpired("multica", 90)
            return ({
                "items": [{
                    "action": "status_changed",
                    "actor_type": "agent",
                    "created_at": "2026-11-06T03:00:00.250Z",
                    "details": {"to": "in_review", "from": "in_progress"},
                }],
                "has_more": False,
            }, "", None)

        actions = []
        for _ in range(3):
            try:
                fetched = fetch("ws", "issue-1")
            except Exception as exc:
                fetched = (None, "", exc)
            decision = bridge.consider_status("in_review", fetched)
            actions.append(decision["action"])
            snap = {"issue-1": dict(observed)}
            pending = ["issue-1"] if decision["action"] == "pending" else []
            bridge.hold_pending_issues(snap, stored, pending)
            cursor = bridge.saved_cursor(cursor, tick, pending)
            if decision["action"] == "pending":
                self.assertEqual(snap["issue-1"]["s"], "in_progress")
                self.assertEqual(snap["issue-1"]["a"], "2026-11-05T00:00:00Z")
                self.assertEqual(cursor, "2026-11-06T01:00:00Z")
            else:
                stored = snap
        self.assertEqual(calls, ["issue-1", "issue-1", "issue-1"])
        self.assertEqual(actions, ["pending", "pending", "send"])
        self.assertEqual(stored["issue-1"]["s"], "in_review")
        self.assertEqual(cursor, tick)

    def test_empty_timeout_has_more_and_stderr_do_not_advance(self):
        bridge = self.bridge()
        prev_row = {"s": "in_progress", "a": "2026-11-05T00:00:00Z", "w": "ws"}
        observed = {"s": "blocked", "a": "2026-11-06T03:00:00Z", "w": "ws"}
        agent = {
            "action": "status_changed", "actor_type": "agent",
            "created_at": "2026-11-06T02:00:00Z", "details": {"to": "blocked"},
        }
        cases = [
            (None, "", None, "empty"),
            (None, "", subprocess.TimeoutExpired("multica", 90), "unavailable"),
            ({"items": [agent], "has_more": True}, "", None, "has_more"),
            ({"timeline": [agent], "has_more": False}, "warning: status_changed history truncated\n", None, "truncated"),
        ]
        for payload, stderr, err, reason in cases:
            decision = bridge.consider_status("blocked", (payload, stderr, err))
            self.assertEqual(decision["action"], "pending", reason)
            self.assertEqual(decision["reason"], reason)
            snap = {"issue-1": dict(observed)}
            bridge.hold_pending_issues(snap, {"issue-1": dict(prev_row)}, ["issue-1"])
            self.assertEqual(snap["issue-1"]["s"], "in_progress")
            self.assertEqual(bridge.saved_cursor("2026-11-06T01:00:00Z", "2026-11-06T04:00:00Z", ["issue-1"]),
                             "2026-11-06T01:00:00Z")

    def test_later_member_page_is_not_sent_after_a_partial_page(self):
        bridge = self.bridge()
        earlier_agent = {
            "id": "a", "action": "status_changed", "actor_type": "agent",
            "created_at": "2026-11-06T03:00:00Z", "details": {"to": "in_review"},
        }
        later_member = {
            "id": "m", "action": "status_changed", "actor_type": "member",
            "created_at": "2026-11-06T03:00:00.100Z", "details": {"to": "in_review"},
        }
        partial = bridge.judge_timeline({"items": [earlier_agent], "has_more": True}, "", "in_review")
        self.assertEqual(partial["action"], "pending")
        self.assertEqual(partial["reason"], "has_more")
        done = bridge.judge_timeline(
            {"events": [earlier_agent, later_member], "has_more": False}, "", "in_review",
        )
        self.assertEqual(done["action"], "skip")
        self.assertEqual(done["actor"], "member")
        snap = {"issue-1": {"s": "in_review", "a": "2026-11-06T03:00:00.100Z"}}
        bridge.hold_pending_issues(snap, {"issue-1": {"s": "in_progress", "a": "2026-11-05T00:00:00Z"}}, [])
        self.assertEqual(snap["issue-1"]["s"], "in_review")


class CircuitAndFirstFailureTests(unittest.TestCase):
    def _drain(self, box, routes, poster):
        return ob.drain(
            box, routes=routes, now=NOW, poster=poster, route_for=lambda *_: {},
        )

    def test_alert_starts_after_thirty_minutes_and_logs_once(self):
        box = ob.empty_outbox()
        circuit = ob.circuit_of(box, "atlas-pm")
        circuit["state"] = "open"
        circuit["opened_at"] = ob.iso(NOW - timedelta(seconds=1800))
        circuit["next_probe_at"] = ob.iso(NOW + timedelta(hours=1))
        circuit["last_http"] = 400
        routes = {"fanout": "send", "circuit_alert_after_sec": 1800}
        logs = self._drain(box, routes, lambda *_: (200, None, None))
        self.assertFalse(any("circuit alert" in line for line in logs))
        circuit["opened_at"] = ob.iso(NOW - timedelta(seconds=1801))
        circuit["alerted_over_threshold"] = None
        logs = self._drain(box, routes, lambda *_: (200, None, None))
        alerts = [line for line in logs if line.startswith("circuit alert dest=atlas-pm")]
        self.assertEqual(len(alerts), 1)
        self.assertIn("open_sec=1801", alerts[0])
        self.assertIn("threshold_sec=1800", alerts[0])
        self.assertIn("last_http=400", alerts[0])
        logs = self._drain(box, routes, lambda *_: (200, None, None))
        self.assertFalse(any("circuit alert" in line for line in logs))

    def test_first_post_failure_records_exception_type_without_the_message(self):
        box = ob.empty_outbox()
        ob.enqueue(box, {
            "kind": "status", "issue_id": "i-fail", "new_status": "in_review",
            "workspace_slug": "clipvibe", "title": "状态", "updated_at": ob.iso(NOW),
        }, NOW, dest="clipvibe-pm", dest_reason="workspace")

        def poster(_payloads):
            raise TimeoutError("synthetic-failure")

        logs = self._drain(box, {"fanout": "send"}, poster)
        item = next(iter(box["items"].values()))
        self.assertEqual(item["attempts"], 1)
        self.assertEqual(item["last_error_type"], "TimeoutError")
        self.assertEqual(item["status"], "pending")
        joined = "\n".join(logs)
        self.assertIn("first_failure dest=clipvibe-pm", joined)
        self.assertIn("error_type=TimeoutError", joined)
        self.assertNotIn("synthetic-failure", joined)
        ob.drain(box, routes={"fanout": "send"}, now=NOW + timedelta(hours=2),
                 poster=poster, route_for=lambda *_: {})
        self.assertEqual(item["last_error_type"], "TimeoutError")
        self.assertEqual(item["attempts"], 2)

    def test_transport_helper_returns_exception_type(self):
        bridge = load_bridge()
        status, _retry, kind, exc_type = bridge.http_post(
            "http" + "://127.0.0.1:1/unused", [], timeout=1,
        )
        self.assertIsNone(status)
        self.assertIn(kind, ("timeout", "network_error"))
        self.assertTrue(exc_type)
        self.assertNotIn("127.0.0.1", str(exc_type))


class SanitizedRoutesTests(unittest.TestCase):
    def test_example_matches_routes_and_tree_has_no_credentials(self):
        routes = (ROOT / "routes.json").read_bytes()
        example = (ROOT / "routes.example.json").read_bytes()
        self.assertEqual(routes, example)
        for path in ROOT.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertIsNone(URL_RE.search(text), path.name)
            self.assertIsNone(BEARER_RE.search(text), path.name)
            self.assertIsNone(HOOK_RE.search(text), path.name)
        data = json.loads(routes)
        self.assertEqual(data["fanout"], "send")
        for ref, spec in data["endpoints"].items():
            self.assertEqual(set(spec), {"bot", "bot_id", "routine"}, ref)


class InstallTests(unittest.TestCase):
    def test_dry_run_is_the_default_and_apply_backs_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bins, mods, cfg = root / "bin", root / "mod", root / "cfg"
            args = ["--bin-dir", str(bins), "--module-dir", str(mods), "--config-dir", str(cfg)]
            self.assertEqual(sync_install.main(args), 0)
            self.assertFalse(bins.exists())
            self.assertFalse(mods.exists())
            self.assertFalse(cfg.exists())
            self.assertEqual(sync_install.main(["--apply", *args]), 0)
            self.assertTrue((bins / "multica-status-bridge.py").is_file())
            self.assertTrue((mods / "outbox.py").is_file())
            self.assertTrue((mods / "agent_delivery.py").is_file())
            self.assertIn("fanout", (cfg / "routes.json").read_text(encoding="utf-8"))
            (cfg / "routes.json").write_text("{}\n", encoding="utf-8")
            self.assertEqual(sync_install.main(["--apply", *args]), 0)
            backups = list(cfg.glob("routes.json.bak-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "{}\n")
            self.assertIn("fanout", (cfg / "routes.json").read_text(encoding="utf-8"))
            self.assertFalse(list(root.rglob("*.plist")))

    def test_apply_refuses_a_routes_file_with_a_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src"
            shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns("tests", "__pycache__"))
            bad = "http" + "s://example.invalid/hook"
            (source / "routes.json").write_text(json.dumps({"fanout": "send", "gone": bad}), encoding="utf-8")
            bins = root / "bin"
            with self.assertRaises(SystemExit) as caught:
                sync_install.main([
                    "--apply", "--source", str(source),
                    "--bin-dir", str(bins),
                    "--module-dir", str(root / "mod"),
                    "--config-dir", str(root / "cfg"),
                ])
            self.assertIn("secret scan", str(caught.exception))
            self.assertNotIn(bad, str(caught.exception))
            self.assertFalse(bins.exists())


if __name__ == "__main__":
    unittest.main()
