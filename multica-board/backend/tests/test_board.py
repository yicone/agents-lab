from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_cli import FAKE_TOKEN, FakeMultica, agent, issue, runtime, task  # noqa: E402
from multica_board.cli_runner import CliError, CliRunner, CommandNotAllowed, assert_read_only  # noqa: E402
from multica_board.collector import WORKSPACE, CollectOptions, Collector  # noqa: E402
from multica_board.redact import ERROR_KINDS, classify_error, redact  # noqa: E402
from multica_board.server import STATIC_CSP, BoardServer, assert_loopback  # noqa: E402
from multica_board.store import Store  # noqa: E402
from multica_board.views import NotFound, Views  # noqa: E402

WS_A, WS_B = "ws-a", "ws-b"
SERVER = "test-profile"
CONTRACT_KINDS = {"quota", "rate_limit", "auth", "network", "timeout", "model_unavailable", "daemon_restart", "cancelled", "unknown"}


def ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def fixture() -> dict:
    a_tasks = [task(WS_A, f"t{i:03d}", "ag1", ago(i // 2 + 1), issue_id=f"{WS_A}-issue-0001") for i in range(1, 451)]
    a_tasks[0] = task(WS_A, "t001", "ag1", ago(1), status="failed", issue_id=f"{WS_A}-issue-0001",
                      error=f"HTTP 429: usage limit has been reached; Authorization: Bearer {FAKE_TOKEN}")
    return {
        WS_A: {
            "meta": {"id": WS_A, "name": "Alpha", "slug": "alpha"},
            "issues": [issue(WS_A, n, status="in_review", status_category="in_review") if n % 5 == 0 else issue(WS_A, n)
                       for n in range(1, 251)],
            "offset_drift": 3,
            "agents": [agent(WS_A, "ag1"), agent(WS_A, "ag2", archived_at="2026-10-01T00:00:00Z")],
            "runtimes": [runtime(WS_A)],
            "members": [{"user_id": "u1", "name": "Yicone", "email": "y@example.com", "role": "owner", "workspace_id": WS_A}],
            "projects": [{"id": "p1", "title": "Proj", "workspace_id": WS_A}],
            "tasks": {"ag1": a_tasks},
        },
        WS_B: {
            "meta": {"id": WS_B, "name": "Beta", "slug": "beta"},
            "issues": [issue(WS_B, 1, status="blocked", status_category="blocked")],
            "agents": [agent(WS_B, "bg1")],
            "runtimes": [runtime(WS_B, status="offline")],
            "tasks": {},
        },
    }


def make(fake: FakeMultica, db: Path, **opt) -> tuple[Store, Collector]:
    store = Store(db)
    runner = CliRunner(profile=SERVER, _exec=fake, _sleep=lambda s: None, retries=1)
    return store, Collector(runner, store, SERVER, CollectOptions(all_workspaces=True, **opt))


def ws_state(store: Store, ws: str) -> dict:
    meta = Views(store, SERVER, app_url="https://app.test", collect_interval_s=60).meta()
    return next(w for w in meta["workspaces"] if w["id"] == ws)


class RedactTests(unittest.TestCase):
    def test_tokens_removed(self):
        for s in [
            f"Bearer {FAKE_TOKEN}",
            "mat_abcdefghijklmnopqrstuvwxyz0123456789",
            "token=abc123secret",
            '"api_key": "sk-abcdefghijk"',
            "ghp_" + "a" * 36,
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        ]:
            out = redact(s)
            self.assertIn("[redacted]", out, s)
            self.assertNotIn(FAKE_TOKEN, out)
        self.assertEqual(redact("/Users/tr/x"), "~/x")

    def test_truncates_to_200(self):
        self.assertEqual(len(redact("error word " * 100)), 200)

    def test_classify(self):
        cases = {
            "HTTP 503: [codex] [429]: The usage limit has been reached (reset after 15m)": "quota",
            "429 Too Many Requests": "rate_limit",
            "UNAUTHENTICATED (code 401): missing credential": "auth",
            "model gpt-9 not found": "model_unavailable",
            "daemon restarted while task was in flight": "daemon_restart",
            "daemon restarted: connection reset, timed out": "daemon_restart",  # ahead of timeout/network
            "context deadline exceeded": "timeout",
            "[Errno 61] Connection refused": "network",
            "task cancelled by server": "cancelled",
            "hermes process exited": "process",
            "something odd": "unknown",
        }
        for msg, want in cases.items():
            self.assertEqual(classify_error(msg), want, msg)
        self.assertIsNone(classify_error(None))

    def test_enum_is_contract_plus_process(self):
        self.assertEqual(set(ERROR_KINDS), CONTRACT_KINDS | {"process"})


class RunnerTests(unittest.TestCase):
    def test_write_commands_refused(self):
        for args in (
            ["issue", "update", "x", "--status", "done"],
            ["issue", "status", "x", "done"],
            ["issue", "comment", "add", "x"],
            ["issue", "create", "--title", "t"],
            ["issue", "assign", "x"],
            ["agent", "update", "x", "--runtime-id", "y"],
            ["runtime", "delete", "x"],
            ["workspace", "member", "invite", "a@b"],
            ["workspace", "switch", "x"],
            ["daemon", "stop"],
            ["auth", "token"],
        ):
            with self.assertRaises(CommandNotAllowed, msg=args):
                assert_read_only(args)
        with self.assertRaises(CommandNotAllowed):
            assert_read_only(["issue", "list", "--server-url", "http://evil"])

    def test_refused_before_spawn(self):
        fake = FakeMultica({})
        with self.assertRaises(CommandNotAllowed):
            CliRunner(profile=SERVER, _exec=fake).run(["issue", "update", "x"])
        self.assertEqual(fake.calls, [])

    def test_argv_and_env_scope(self):
        fake = FakeMultica(fixture())
        leaked = {
            "MULTICA_WORKSPACE_ID": "leak", "MULTICA_TOKEN": FAKE_TOKEN, "MULTICA_SERVER_URL": "http://x",
            "MULTICA_TASK_ID": "t", "MULTICA_TASK_CONFIG_ROOT": "/x", "GODEBUG": "",
        }
        with mock.patch.dict(os.environ, leaked):
            CliRunner(profile=SERVER, _exec=fake).run(["agent", "list"], workspace_id=WS_A)
        self.assertEqual(fake.calls[0][:5], ["multica", "--profile", SERVER, "--workspace-id", WS_A])
        self.assertEqual(fake.calls[0][-2:], ["--output", "json"])
        env = fake.envs[0]
        for k in ("MULTICA_WORKSPACE_ID", "MULTICA_TOKEN", "MULTICA_SERVER_URL", "MULTICA_TASK_ID", "MULTICA_TASK_CONFIG_ROOT"):
            self.assertNotIn(k, env)
        self.assertEqual(env["GODEBUG"], "tlsmlkem=0")

    def test_error_redacted_and_retry(self):
        fake = FakeMultica(fixture())
        fake.fail[WS_A] = f"connection reset; token={FAKE_TOKEN}"
        with self.assertRaises(CliError) as cm:
            CliRunner(profile=SERVER, _exec=fake, _sleep=lambda s: None, retries=2).run(["agent", "list"], workspace_id=WS_A)
        self.assertNotIn(FAKE_TOKEN, str(cm.exception))
        self.assertEqual(len(fake.calls), 3)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "c" / "board.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def count(self, store, table, ws):
        return store.query(f"SELECT COUNT(*) AS n FROM {table} WHERE workspace_id=?", (ws,))[0]["n"]

    def test_full_collect(self):
        store, col = make(FakeMultica(fixture()), self.db)
        res = col.collect()
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.count(store, "issues", WS_A), 250)  # >100 with offset drift, deduped
        self.assertEqual(self.count(store, "tasks", WS_A), 450)  # followed the stderr cursor
        self.assertEqual(oct(os.stat(self.db).st_mode & 0o777), "0o600")
        self.assertEqual(ws_state(store, WS_A)["collect_state"], "ok")

    def test_empty_strings_become_null(self):
        store, col = make(FakeMultica(fixture()), self.db)
        col.collect()
        a = store.query("SELECT model, thinking_level, service_tier FROM agents WHERE id='ag1'")[0]
        self.assertEqual(a, {"model": None, "thinking_level": None, "service_tier": None})
        self.assertIsNone(store.query("SELECT status_name FROM issues LIMIT 1")[0]["status_name"])

    def test_run_window_and_page_cap(self):
        fake = FakeMultica(fixture())
        store, col = make(fake, self.db, run_days=5)
        col.collect()
        self.assertEqual(self.count(store, "tasks", WS_A), 400)  # page 2 crosses the cutoff
        store2, col2 = make(fake, Path(self.tmp.name) / "cap.sqlite", max_task_pages=1)
        col2.collect()
        sync = store2.query("SELECT complete FROM sync_status WHERE workspace_id=? AND resource='tasks'", (WS_A,))
        self.assertEqual(sync[0]["complete"], 0)

    def test_no_secrets_or_pii_cached(self):
        store, col = make(FakeMultica(fixture()), self.db)
        col.collect()
        store.close()
        for f in self.db.parent.iterdir():
            blob = f.read_bytes()
            for needle in (FAKE_TOKEN.encode(), b"y@example.com", b"secret body", b"/Users/someone"):
                self.assertNotIn(needle, blob, f.name)

    def test_incremental_stops_at_cached(self):
        fake = FakeMultica(fixture())
        store, col = make(fake, self.db)
        col.collect()
        fake.calls.clear()
        col.collect()
        self.assertEqual(len([c for c in fake.calls if "tasks" in c and "ag1" in c]), 1)

    def test_failure_keeps_cache_and_marks_error(self):
        fake = FakeMultica(fixture())
        store, col = make(fake, self.db)
        col.collect()
        fake.fail[WS_B] = "HTTP 503 upstream"
        res = col.collect()
        self.assertTrue(res["workspaces"][WS_A]["ok"])
        self.assertEqual(self.count(store, "issues", WS_B), 1)
        beta = ws_state(store, WS_B)
        self.assertEqual(beta["collect_state"], "error")
        self.assertEqual(beta["error"]["kind"], "network")
        self.assertEqual(beta["counts"]["open_issues"], 1)

    def test_never_succeeded(self):
        fake = FakeMultica(fixture())
        fake.fail[WS_B] = "context deadline exceeded"
        store, col = make(fake, self.db)
        col.collect()
        beta = ws_state(store, WS_B)
        self.assertEqual(beta["collect_state"], "never")
        self.assertIsNone(beta["counts"])
        self.assertEqual(beta["error"]["kind"], "timeout")

    def test_auth_error_purges(self):
        fake = FakeMultica(fixture())
        store, col = make(fake, self.db)
        col.collect()
        fake.fail[WS_B] = "403 forbidden: not a member"
        col.collect()
        self.assertEqual(self.count(store, "issues", WS_B), 0)
        self.assertEqual(self.count(store, "agents", WS_B), 0)
        beta = ws_state(store, WS_B)
        self.assertEqual((beta["collect_state"], beta["error"]["kind"], beta["counts"]), ("error", "auth", None))

    def test_scope_guard_rejects_foreign_rows(self):
        data = fixture()
        # Simulate a CLI pinned to WS_A: every WS_B call answers with WS_A rows.
        data[WS_B] = {**data[WS_A], "meta": data[WS_B]["meta"]}
        store, col = make(FakeMultica(data), self.db)
        col.collect()
        for table in ("issues", "agents", "runtimes", "tasks", "projects"):
            self.assertEqual(self.count(store, table, WS_B), 0, table)
        beta = ws_state(store, WS_B)
        self.assertEqual(beta["collect_state"], "never")
        self.assertEqual(beta["error"]["kind"], "auth")
        self.assertIn("CLI identity scoped to another workspace", beta["error"]["message"])
        self.assertEqual(ws_state(store, WS_A)["collect_state"], "ok")

    def test_revoked_workspace_removed(self):
        data = fixture()
        store, col = make(FakeMultica(data), self.db)
        col.collect()
        del data[WS_B]
        self.assertEqual(col.collect()["purged"], [WS_B])
        self.assertEqual(self.count(store, "issues", WS_B), 0)

    def test_allow_list(self):
        fake = FakeMultica(fixture())
        store = Store(self.db)
        Collector(CliRunner(profile=SERVER, _exec=fake), store, SERVER, CollectOptions(workspaces=["beta"])).collect()
        self.assertEqual({r["id"] for r in store.query("SELECT id FROM workspaces")}, {WS_B})
        self.assertFalse(any(WS_A in c for c in fake.calls))


def seed(store: Store) -> None:
    """Seed the cache directly (no collector) with one workspace's worth of contract cases."""
    s, w = SERVER, "w1"
    with store.tx() as c:
        store.upsert(c, "workspaces", {"server": s, "id": w, "name": "One", "slug": "one"})
        store.upsert(c, "workspaces", {"server": s, "id": "w2", "name": "Two", "slug": "two"})
        def iss(i, **kw):
            base = {"server": s, "workspace_id": w, "id": i, "identifier": i.upper(), "title": i, "status": "todo",
                    "status_category": "todo", "priority": "none", "last_activity_at": ago(2), "created_at": ago(100)}
            store.upsert(c, "issues", {**base, **kw})
        iss("parent", status="done", status_category="done")
        iss("child", parent_issue_id="parent", assignee_type="agent", assignee_id="ag")
        iss("orphan", parent_issue_id="gone", assignee_type="squad", assignee_id="sq")
        iss("member", assignee_type="member", assignee_id="u1", project_id="p1")
        iss("closed_failed", status="done", status_category="done")
        iss("closed_old", status="cancelled", status_category="cancelled")
        store.upsert(c, "members", {"server": s, "workspace_id": w, "user_id": "u1", "name": "Yi"})
        store.upsert(c, "projects", {"server": s, "workspace_id": w, "id": "p1", "title": "Proj"})
        store.upsert(c, "agents", {"server": s, "workspace_id": w, "id": "ag", "name": "Builder", "status": "working",
                                   "runtime_id": "rt", "model": None, "max_concurrent_tasks": 6})
        store.upsert(c, "agents", {"server": s, "workspace_id": w, "id": "old", "name": "Old", "status": "idle",
                                   "runtime_id": None, "archived_at": ago(500)})
        store.upsert(c, "runtimes", {"server": s, "workspace_id": w, "id": "rt", "name": "Cursor (box)", "custom_name": None,
                                     "provider": "cursor", "runtime_mode": "local", "status": "online",
                                     "last_seen_at": ago(0), "device_info": "box", "cli_version": "0.6.2"})
        def run(tid, issue_id, status, hours, kind=None, err=None):
            store.upsert(c, "tasks", {"server": s, "workspace_id": w, "id": tid, "agent_id": "ag", "runtime_id": "rt",
                                      "issue_id": issue_id, "status": status, "created_at": ago(hours),
                                      "started_at": ago(hours), "completed_at": None if status in ("running", "queued") else ago(hours),
                                      "error_kind": kind, "error_summary": err})
        run("r1", "child", "failed", 5, "daemon_restart", "daemon restarted while task was in flight")
        run("r2", "child", "completed", 3)
        run("r3", "orphan", "failed", 2, "quota", "usage limit")
        run("r4", "member", "running", 1)
        run("r5", None, "queued", 0.5)
        run("r6", "closed_failed", "failed", 30, "network", "conn refused")
        run("r7", "member", "failed", 24 * 9, "timeout", "old")
        for i in range(25):
            run(f"h{i:02d}", "member", "completed", 40 + i)
        store.upsert(c, "sync_status", {"server": s, "workspace_id": w, "resource": WORKSPACE,
                                        "last_attempt_at": ago(0), "last_success_at": ago(0)})
        store.upsert(c, "sync_status", {"server": s, "workspace_id": "w2", "resource": WORKSPACE,
                                        "last_attempt_at": ago(0), "last_success_at": ago(1),
                                        "last_error": "issues: 403", "last_error_kind": "auth"})
    store.set_meta("last_collect", {"started_at": "2026-10-14T08:59:00Z", "finished_at": "2026-10-14T09:00:00Z"})


class ViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store(":memory:")
        seed(cls.store)
        cls.v = Views(cls.store, SERVER, app_url="https://app.test/", collect_interval_s=60)

    def by_id(self):
        return {i["id"]: i for i in self.v.issues()["items"]}

    def test_generated_at_is_collect_finish(self):
        for body in (self.v.meta(), self.v.issues(), self.v.agents(), self.v.runtimes()):
            self.assertEqual(body["generated_at"], "2026-10-14T09:00:00Z")

    def test_meta(self):
        m = self.v.meta()
        self.assertEqual((m["api_version"], m["collect_interval_s"], m["multica_web_base"]), ("0", 60, "https://app.test"))
        one, two = m["workspaces"]
        self.assertEqual(one["collect_state"], "ok")
        self.assertEqual(one["counts"], {"open_issues": 3, "agents": 1, "runtimes": 1})
        self.assertEqual((two["collect_state"], two["error"]["kind"], two["counts"]), ("error", "auth", None))

    def test_issue_scope(self):
        ids = set(self.by_id())
        self.assertEqual(ids, {"child", "orphan", "member", "closed_failed"})  # open + closed with a 7d failure

    def test_issue_fields(self):
        it = self.by_id()
        child = it["child"]
        self.assertEqual(child["key"], "w1:child")
        self.assertEqual(child["parent_identifier"], "PARENT")
        self.assertEqual(child["assignee"], {"type": "agent", "id": "ag", "name": "Builder"})
        self.assertEqual(child["url"], "https://app.test/one/issues/child")
        self.assertEqual(child["latest_run"]["task_id"], "r2")
        self.assertEqual(child["latest_run"]["agent_name"], "Builder")
        self.assertEqual(child["last_failed_run"], {"task_id": "r1", "completed_at": child["last_failed_run"]["completed_at"],
                                                    "error_kind": "daemon_restart"})
        self.assertIsNone(it["orphan"]["parent_identifier"])
        self.assertEqual(it["orphan"]["assignee"]["name"], None)  # squads not collected in v0
        self.assertEqual(it["member"]["assignee"]["name"], "Yi")
        self.assertEqual(it["member"]["project"], {"id": "p1", "title": "Proj"})
        self.assertIsNone(it["member"]["last_failed_run"])  # r7 is older than 7 days
        self.assertEqual(it["member"]["latest_run"]["status"], "running")
        expected = {"key", "workspace_id", "id", "identifier", "title", "status", "status_category", "priority",
                    "assignee", "project", "parent_identifier", "due_date", "created_at", "last_activity_at", "url",
                    "latest_run", "last_failed_run"}
        self.assertEqual(set(child), expected)

    def test_agents(self):
        body = self.v.agents()
        self.assertEqual(body["recent_window_h"], 24)
        a = {x["id"]: x for x in body["items"]}
        self.assertTrue(a["old"]["archived"])
        self.assertIsNone(a["old"]["runtime_id"])
        ag = a["ag"]
        self.assertEqual(ag["tasks"], {"running": 1, "queued": 1})
        self.assertEqual(ag["recent"]["completed"], 1)
        self.assertEqual(ag["recent"]["failed"], 2)
        self.assertEqual(ag["recent"]["last_error_kind"], "quota")
        self.assertEqual(ag["recent"]["last_failed_issue_identifier"], "ORPHAN")
        self.assertEqual(ag["url"], "https://app.test/one/agents/ag")
        self.assertIsNone(ag["model"])

    def test_runtimes(self):
        rt = self.v.runtimes()["items"][0]
        self.assertEqual(set(rt), {"key", "workspace_id", "id", "name", "provider", "runtime_mode", "status", "last_seen_at", "device_info"})
        self.assertEqual(rt["key"], "w1:rt")

    def test_issue_runs(self):
        runs = self.v.issue_runs("w1", "member")["items"]
        self.assertEqual(len(runs), 20)
        self.assertEqual(runs[0]["task_id"], "r4")
        with self.assertRaises(NotFound):
            self.v.issue_runs("w1", "nope")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        static = Path(cls.tmp.name) / "web"
        static.mkdir()
        (static / "index.html").write_text("<h1>board</h1>")
        (static / "app.js").write_text("export {}")
        (Path(cls.tmp.name) / "secret.txt").write_text("nope")
        cls.store = Store(":memory:")
        seed(cls.store)
        views = Views(cls.store, SERVER, app_url="https://app.test", collect_interval_s=60)
        cls.httpd = BoardServer("127.0.0.1", 0, views, static_dir=static)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.store.close()
        cls.tmp.cleanup()

    def req(self, method: str, path: str, headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, headers=headers or {})
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r, body

    def test_bind_refuses_non_loopback(self):
        for host in ("0.0.0.0", "::", "192.168.1.10", "localhost", "example.com"):
            with self.assertRaises(ValueError, msg=host):
                assert_loopback(host)

    def test_endpoints(self):
        for path in ("/api/meta", "/api/issues", "/api/agents", "/api/runtimes", "/api/issues/w1/member/runs"):
            r, body = self.req("GET", path)
            self.assertEqual(r.status, 200, path)
            self.assertEqual(r.getheader("Content-Type"), "application/json; charset=utf-8")
            self.assertEqual(r.getheader("Cache-Control"), "no-store")
            self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
            self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))
            json.loads(body)

    def test_removed_and_unknown_endpoints(self):
        for path in ("/api/board", "/api/workspaces", "/api/runs", "/api/health", "/api/issues/w1/nope/runs"):
            r, body = self.req("GET", path)
            self.assertEqual(r.status, 404, path)
            self.assertEqual(json.loads(body)["error"]["code"], "not_found")

    def test_write_methods_rejected(self):
        for m in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            r, body = self.req(m, "/api/issues")
            self.assertEqual(r.status, 405, m)
            self.assertEqual(r.getheader("Allow"), "GET, HEAD")
            self.assertEqual(json.loads(body)["error"]["code"], "method_not_allowed")

    def test_host_allow_list(self):
        for host in ("attacker.test", f"localhost:{self.port}", f"[::1]:{self.port}", "127.0.0.1:1"):
            r, _ = self.req("GET", "/api/meta", {"Host": host})
            self.assertEqual(r.status, 421, host)
        r, _ = self.req("GET", "/api/meta", {"Origin": "https://attacker.test"})
        self.assertEqual(r.status, 403)
        r, _ = self.req("GET", "/api/meta", {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status, 403)
        r, _ = self.req("GET", "/api/meta", {"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(r.status, 200)

    def test_static(self):
        r, body = self.req("GET", "/?mock=1")
        self.assertEqual(body, b"<h1>board</h1>")
        self.assertEqual(r.getheader("Content-Security-Policy"), STATIC_CSP)
        self.assertIn("base-uri 'none'", STATIC_CSP)
        self.assertIn("form-action 'none'", STATIC_CSP)
        r, _ = self.req("GET", "/app.js")
        self.assertTrue(r.getheader("Content-Type").endswith("javascript; charset=utf-8"))
        for p in ("/../secret.txt", "/%2e%2e/secret.txt", "/a%00b", "/missing.js"):
            r, body = self.req("GET", p)
            self.assertEqual(r.status, 404, p)
            self.assertNotIn(b"nope", body)


if __name__ == "__main__":
    unittest.main()
