from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_cli import FAKE_TOKEN, FakeMultica, agent, issue, runtime, task  # noqa: E402
from multica_board.cli_runner import CliError, CliRunner, CommandNotAllowed, assert_read_only  # noqa: E402
from multica_board.collector import CollectOptions, Collector  # noqa: E402
from multica_board.redact import classify_error, redact  # noqa: E402
from multica_board.server import BoardServer, assert_loopback  # noqa: E402
from multica_board.store import Store  # noqa: E402
from multica_board.views import Views  # noqa: E402

WS_A, WS_B = "ws-a", "ws-b"
SERVER = "test-profile"


def recent(hours_ago: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def fixture() -> dict:
    a_tasks = [task(WS_A, f"t{i:03d}", "ag1", recent(i // 2 + 1), issue_id=f"{WS_A}-issue-0001") for i in range(1, 451)]
    a_tasks[0] = task(WS_A, "t001", "ag1", recent(1), status="failed", issue_id=f"{WS_A}-issue-0001",
                      error=f"HTTP 429: usage limit has been reached; Authorization: Bearer {FAKE_TOKEN}")
    return {
        WS_A: {
            "meta": {"id": WS_A, "name": "Alpha", "slug": "alpha"},
            "issues": [issue(WS_A, n, status="in_review" if n % 5 == 0 else "todo") for n in range(1, 251)],
            "offset_drift": 3,
            "agents": [agent(WS_A, "ag1"), agent(WS_A, "ag2", archived_at="2026-10-01T00:00:00Z")],
            "runtimes": [runtime(WS_A)],
            "members": [{"user_id": "u1", "name": "Yicone", "email": "y@example.com", "role": "owner"}],
            "projects": [{"id": "p1", "title": "Proj"}],
            "tasks": {"ag1": a_tasks},
        },
        WS_B: {
            "meta": {"id": WS_B, "name": "Beta", "slug": "beta"},
            "issues": [issue(WS_B, 1, status="blocked")],
            "agents": [agent(WS_B, "bg1")],
            "runtimes": [runtime(WS_B, status="offline")],
            "tasks": {},
        },
    }


def make(fake: FakeMultica, db: Path, **opt) -> tuple[Store, Collector]:
    store = Store(db)
    runner = CliRunner(profile=SERVER, _exec=fake, _sleep=lambda s: None, retries=1)
    opts = CollectOptions(all_workspaces=True, **opt)
    return store, Collector(runner, store, SERVER, opts)


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

    def test_classify(self):
        cases = {
            "HTTP 503: [codex] [429]: The usage limit has been reached (reset after 15m)": "quota",
            "429 Too Many Requests": "rate_limit",
            "UNAUTHENTICATED (code 401): missing credential": "auth",
            "[Errno 61] Connection refused": "network",
            "context deadline exceeded": "timeout",
            "model gpt-9 not found": "model_unavailable",
            "hermes process exited": "process",
            "daemon restarted while task was in flight": "process",
            "task cancelled by server": "cancelled",
            "something odd": "unknown",
        }
        for msg, want in cases.items():
            self.assertEqual(classify_error(msg), want, msg)
        self.assertIsNone(classify_error(None))


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

    def test_global_flag_injection_refused(self):
        with self.assertRaises(CommandNotAllowed):
            assert_read_only(["issue", "list", "--server-url", "http://evil"])

    def test_refused_before_spawn(self):
        fake = FakeMultica({})
        runner = CliRunner(profile=SERVER, _exec=fake)
        with self.assertRaises(CommandNotAllowed):
            runner.run(["issue", "update", "x"])
        self.assertEqual(fake.calls, [])

    def test_argv_and_env(self):
        fake = FakeMultica(fixture())
        runner = CliRunner(profile=SERVER, _exec=fake)
        with mock.patch.dict(os.environ, {"MULTICA_WORKSPACE_ID": "leak", "GODEBUG": ""}):
            runner.run(["agent", "list"], workspace_id=WS_A)
        argv = fake.calls[0]
        self.assertEqual(argv[:5], ["multica", "--profile", SERVER, "--workspace-id", WS_A])
        self.assertEqual(argv[-2:], ["--output", "json"])
        self.assertNotIn("MULTICA_WORKSPACE_ID", fake.envs[0])
        self.assertEqual(fake.envs[0]["GODEBUG"], "tlsmlkem=0")

    def test_error_redacted_and_retry(self):
        fake = FakeMultica(fixture())
        fake.fail[WS_A] = f"connection reset; token={FAKE_TOKEN}"
        runner = CliRunner(profile=SERVER, _exec=fake, _sleep=lambda s: None, retries=2)
        with self.assertRaises(CliError) as cm:
            runner.run(["agent", "list"], workspace_id=WS_A)
        self.assertNotIn(FAKE_TOKEN, str(cm.exception))
        self.assertEqual(len(fake.calls), 3)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "c" / "board.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_collect(self):
        fake = FakeMultica(fixture())
        store, col = make(fake, self.db)
        res = col.collect()
        self.assertTrue(res["ok"], res)
        n = store.query("SELECT COUNT(*) AS n FROM issues WHERE workspace_id=?", (WS_A,))[0]["n"]
        self.assertEqual(n, 250)  # paged past 100 with drift duplicates, deduped
        sync = store.query("SELECT complete FROM sync_status WHERE workspace_id=? AND resource='issues'", (WS_A,))
        self.assertEqual(sync[0]["complete"], 1)
        tasks = store.query("SELECT COUNT(*) AS n FROM tasks WHERE agent_id='ag1'")[0]["n"]
        self.assertEqual(tasks, 450)  # followed the stderr cursor across 3 pages
        self.assertEqual(oct(os.stat(self.db).st_mode & 0o777), "0o600")

    def test_run_window_and_page_cap(self):
        fake = FakeMultica(fixture())
        store, col = make(fake, self.db, run_days=5)
        col.collect()
        n = store.query("SELECT COUNT(*) AS n FROM tasks WHERE agent_id='ag1'")[0]["n"]
        self.assertEqual(n, 400)  # page 2 crosses the 5-day cutoff, so page 3 is never fetched
        store2, col2 = make(fake, Path(self.tmp.name) / "cap.sqlite", max_task_pages=1)
        col2.collect()
        sync = store2.query("SELECT complete FROM sync_status WHERE workspace_id=? AND resource='tasks'", (WS_A,))
        self.assertEqual(sync[0]["complete"], 0)

    def test_no_secrets_or_pii_cached(self):
        fake = FakeMultica(fixture())
        store, col = make(fake, self.db)
        col.collect()
        store.close()
        for f in self.db.parent.iterdir():
            blob = f.read_bytes()
            self.assertNotIn(FAKE_TOKEN.encode(), blob, f.name)
            self.assertNotIn(b"y@example.com", blob, f.name)
            self.assertNotIn(b"secret body", blob, f.name)
            self.assertNotIn(b"/Users/someone", blob, f.name)

    def test_incremental_stops_at_cached(self):
        fake = FakeMultica(fixture())
        store, col = make(fake, self.db)
        col.collect()
        fake.calls.clear()
        col.collect()
        task_calls = [c for c in fake.calls if "tasks" in c and "ag1" in c]
        self.assertEqual(len(task_calls), 1)

    def test_failure_isolation_and_stale(self):
        fake = FakeMultica(fixture())
        store, col = make(fake, self.db)
        col.collect()
        fake.fail[WS_B] = "HTTP 503 upstream"
        res = col.collect()
        self.assertFalse(res["ok"])
        self.assertTrue(res["workspaces"][WS_A]["ok"])
        # cached data for the failing workspace survives, flagged stale
        self.assertEqual(store.query("SELECT COUNT(*) AS n FROM issues WHERE workspace_id=?", (WS_B,))[0]["n"], 1)
        views = Views(store, SERVER, app_url="https://app.test", stale_days=7, refresh_s=60, run_days=14)
        beta = next(w for w in views.workspaces() if w["id"] == WS_B)
        self.assertTrue(beta["sync"]["stale"])
        self.assertIn("issues", beta["sync"]["errors"])

    def test_revoked_workspace_purged(self):
        data = fixture()
        fake = FakeMultica(data)
        store, col = make(fake, self.db)
        col.collect()
        del data[WS_B]
        res = col.collect()
        self.assertEqual(res["purged"], [WS_B])
        self.assertEqual(store.query("SELECT COUNT(*) AS n FROM issues WHERE workspace_id=?", (WS_B,))[0]["n"], 0)

    def test_allow_list(self):
        fake = FakeMultica(fixture())
        store = Store(self.db)
        runner = CliRunner(profile=SERVER, _exec=fake)
        Collector(runner, store, SERVER, CollectOptions(workspaces=["beta"])).collect()
        self.assertEqual({r["id"] for r in store.query("SELECT id FROM workspaces")}, {WS_B})
        self.assertFalse(any(f"--workspace-id" in c and WS_A in c for c in fake.calls))


class ViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        fake = FakeMultica(fixture())
        cls.store, col = make(fake, Path(cls.tmp.name) / "v.sqlite")
        col.collect()
        cls.views = Views(cls.store, SERVER, app_url="https://app.test", stale_days=7, refresh_s=60, run_days=14)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.tmp.cleanup()

    def test_board_columns(self):
        b = self.views.board()
        self.assertEqual(b["counts"]["in_review"], 50)
        self.assertEqual(b["counts"]["blocked"], 1)
        self.assertEqual(b["counts"]["run_failed"], 1)
        failed = b["columns"]["run_failed"][0]
        self.assertEqual(failed["latest_run"]["error_class"], "quota")
        self.assertNotIn(FAKE_TOKEN, json.dumps(failed))
        self.assertEqual(failed["url"], f"https://app.test/alpha/issues/{WS_A}-issue-0001")

    def test_agents_matrix(self):
        rows = self.views.agents()
        self.assertEqual({r["id"] for r in rows}, {"ag1", "bg1"})
        ag1 = next(r for r in rows if r["id"] == "ag1")
        self.assertTrue(ag1["model"]["inherits_runtime_default"])
        self.assertEqual(ag1["model"]["observed"], ["gpt-x"])
        self.assertEqual(ag1["failures"]["last"]["error_class"], "quota")
        self.assertTrue(ag1["runtime"]["online"])
        bg1 = next(r for r in rows if r["id"] == "bg1")
        self.assertFalse(bg1["runtime"]["online"])
        self.assertEqual(len(self.views.agents(include_archived=True)), 3)
        blob = json.dumps(rows)
        for forbidden in ("instructions", "custom_env", "custom_args", "mcp_config", "runtime_config", FAKE_TOKEN):
            self.assertNotIn(forbidden, blob)

    def test_issue_filters(self):
        self.assertEqual(self.views.issues(view="blocked")["total"], 1)
        self.assertEqual(self.views.issues(view="in_review", workspace="alpha", limit=5)["total"], 50)
        self.assertEqual(len(self.views.issues(view="in_review", limit=5)["issues"]), 5)
        with self.assertRaises(ValueError):
            self.views.issues(view="nope")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        static = Path(cls.tmp.name) / "dist"
        static.mkdir()
        (static / "index.html").write_text("<h1>board</h1>")
        (Path(cls.tmp.name) / "secret.txt").write_text("nope")
        fake = FakeMultica(fixture())
        cls.store, col = make(fake, Path(cls.tmp.name) / "s.sqlite")
        col.collect()
        views = Views(cls.store, SERVER, app_url="https://app.test", stale_days=7, refresh_s=60, run_days=14)
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
        assert_loopback("127.0.0.1")
        assert_loopback("::1")

    def test_endpoints(self):
        for path in ("/api/health", "/api/meta", "/api/workspaces", "/api/board", "/api/issues?view=all&limit=3",
                     "/api/agents", "/api/runs?status=failed"):
            r, body = self.req("GET", path)
            self.assertEqual(r.status, 200, path)
            self.assertEqual(r.getheader("Content-Type"), "application/json; charset=utf-8")
            self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))
            self.assertNotIn(FAKE_TOKEN.encode(), body)
            json.loads(body)

    def test_write_methods_rejected(self):
        for m in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            r, _ = self.req(m, "/api/issues")
            self.assertEqual(r.status, 405, m)
            self.assertEqual(r.getheader("Allow"), "GET, HEAD")

    def test_host_and_origin_guard(self):
        r, _ = self.req("GET", "/api/health", {"Host": "attacker.test"})
        self.assertEqual(r.status, 421)
        r, _ = self.req("GET", "/api/health", {"Origin": "https://attacker.test"})
        self.assertEqual(r.status, 403)
        r, _ = self.req("GET", "/api/health", {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status, 403)
        r, _ = self.req("GET", "/api/health", {"Origin": f"http://localhost:{self.port}"})
        self.assertEqual(r.status, 200)

    def test_static_and_traversal(self):
        r, body = self.req("GET", "/")
        self.assertEqual(body, b"<h1>board</h1>")
        r, body = self.req("GET", "/../secret.txt")
        self.assertNotIn(b"nope", body)
        r, body = self.req("GET", "/%2e%2e/secret.txt")
        self.assertNotIn(b"nope", body)

    def test_bad_params(self):
        r, _ = self.req("GET", "/api/issues?view=bogus")
        self.assertEqual(r.status, 400)
        r, _ = self.req("GET", "/api/issues?limit=abc")
        self.assertEqual(r.status, 400)
        r, _ = self.req("GET", "/api/nope")
        self.assertEqual(r.status, 404)


if __name__ == "__main__":
    unittest.main()
