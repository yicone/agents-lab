from __future__ import annotations

import http.client
import json
import logging
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_cli  # noqa: E402
from fake_cli import FAKE_TOKEN, SENTINELS, FakeMultica, agent, runtime, sentinel_agent  # noqa: E402
from multica_board.cli_runner import CliRunner, CommandNotAllowed, assert_read_only  # noqa: E402
from multica_board.collector import CollectOptions, Collector  # noqa: E402
from multica_board.mcp_summary import INVALID_MESSAGE, summarize_mcp_config, transport_of  # noqa: E402
from multica_board.mcp_support import SUPPORT, runtime_loads  # noqa: E402
from multica_board.server import BoardServer  # noqa: E402
from multica_board.store import Store  # noqa: E402
from multica_board.views import Views  # noqa: E402

WS_A, WS_B = "ws-a", "ws-b"
SERVER = "test-profile"
TS_PROVIDERS = {
    "claude", "codebuddy", "codearts", "codex", "cursor", "grok", "hermes", "kimi",
    "reasonix", "dsh", "kiro", "opencode", "openclaw", "pi", "qoder", "qoderclicn",
    "qwen", "qwenpaw", "mcode", "traecli", "dim", "omp",
}


def make(fake: FakeMultica, db: Path, **opt) -> tuple[Store, CliRunner]:
    store = Store(db)
    runner = CliRunner(profile=SERVER, _exec=fake, _sleep=lambda s: None, retries=0)
    return store, runner


def collect(store: Store, runner: CliRunner, **opt):
    # A fresh Collector per cycle, like the serve loop.
    return Collector(runner, store, SERVER, CollectOptions(all_workspaces=True, **opt)).collect()


def views(store: Store, **kw) -> Views:
    return Views(store, SERVER, app_url="https://app.test", collect_interval_s=60, **kw)


def words_of(call: list[str]) -> list[str]:
    return fake_cli._parse(call)[1]


def agent_mcp_calls(fake: FakeMultica, aid: str | None = None) -> list[list[str]]:
    calls = [c for c in fake.calls if words_of(c)[:3] == ["agent", "mcp", "list"]]
    if aid is not None:
        calls = [c for c in calls if len(words_of(c)) > 3 and words_of(c)[3] == aid]
    return calls


def mcp_fixture() -> dict:
    return {
        WS_A: {
            "meta": {"id": WS_A, "name": "Alpha", "slug": "alpha"},
            "issues": [],
            "agents": [agent(WS_A, "ag1", mcp_config=None)],
            "runtimes": [runtime(WS_A)],
            "tasks": {},
            "mcp_servers": [
                {"id": "srv-linear", "workspace_id": WS_A, "name": "linear", "transport": "http",
                 "agent_count": 1, "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-20T00:00:00Z"},
                {"id": "srv-fs", "workspace_id": WS_A, "name": "fs", "transport": "stdio",
                 "agent_count": 0, "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-02T00:00:00Z"},
            ],
            "agent_mcp": {"ag1": [{"id": "srv-linear", "name": "linear", "enabled": True}]},
        },
    }


class AllowListTests(unittest.TestCase):
    def test_mcp_list_commands_allowed(self):
        assert_read_only(["workspace", "mcp", "list", WS_A])
        assert_read_only(["agent", "mcp", "list", "ag1"])

    def test_mutating_mcp_commands_refused_before_spawn(self):
        refused = [
            ["workspace", "mcp", "add", "x", "--server-config-file", "f"],
            ["workspace", "mcp", "update", "x", "--name", "y"],
            ["workspace", "mcp", "remove", "x"],
            ["agent", "mcp", "add", "a", "s"],
            ["agent", "mcp", "enable", "a", "s"],
            ["agent", "mcp", "disable", "a", "s"],
            ["agent", "mcp", "remove", "a", "s"],
            ["agent", "update", "a", "--mcp-config-file", "f"],
            ["agent", "update", "a", "--mcp-config", "null"],
        ]
        for args in refused:
            with self.assertRaises(CommandNotAllowed, msg=args):
                assert_read_only(args)
            fake = FakeMultica({})
            with self.assertRaises(CommandNotAllowed, msg=args):
                CliRunner(profile=SERVER, _exec=fake).run(args, workspace_id=WS_A)
            self.assertEqual(fake.calls, [], args)


class SummaryTests(unittest.TestCase):
    def test_transport_of(self):
        self.assertEqual(transport_of({"type": "local"}), "stdio")
        self.assertEqual(transport_of({"type": " STDIO "}), "stdio")
        self.assertEqual(transport_of({"type": "remote"}), "http")
        self.assertEqual(transport_of({"type": "streamable-http"}), "http")
        self.assertEqual(transport_of({"type": "sse"}), "sse")
        self.assertEqual(transport_of({"type": "weird-transport"}), "weird-transport")
        self.assertEqual(transport_of({"type": "x" * 40}), "x" * 32)
        self.assertEqual(transport_of({"command": "npx"}), "stdio")
        self.assertEqual(transport_of({"url": "https://x"}), "http")
        self.assertEqual(transport_of({}), "unknown")
        self.assertEqual(transport_of("nope"), "unknown")

    def test_states(self):
        self.assertEqual(summarize_mcp_config(None, False), {"state": "none", "servers": []})
        self.assertEqual(summarize_mcp_config({"mcpServers": {}}, True), {"state": "hidden", "servers": []})
        self.assertEqual(summarize_mcp_config({"mcpServers": {}}, False),
                         {"state": "managed", "servers": []})
        bad = summarize_mcp_config("SENT_INVALID_9f1", False)
        self.assertEqual(bad["state"], "invalid")
        self.assertEqual(bad["message"], INVALID_MESSAGE)
        self.assertNotIn("SENT_INVALID_9f1", json.dumps(bad))

    def test_legacy_container_and_collision(self):
        out = summarize_mcp_config(
            {
                "mcp": {"a": {"url": "https://x"}, "b": {"type": "local", "command": "c"}},
                "mcpServers": {"b": {"url": "https://y"}},
            },
            False,
        )
        self.assertEqual(out["state"], "managed")
        self.assertEqual([(s["name"], s["transport"]) for s in out["servers"]],
                         [("a", "http"), ("b", "http")])  # mcpServers wins over mcp


class CollectorMcpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "board.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def mcp_view(self, store: Store):
        return views(store).mcp()

    def ws_block(self, store: Store, ws: str = WS_A):
        return next(w for w in self.mcp_view(store)["workspaces"] if w["id"] == ws)

    def test_empty_library_makes_no_agent_calls(self):
        data = mcp_fixture()
        data[WS_A]["mcp_servers"] = []
        data[WS_A]["agent_mcp"] = {}
        fake = FakeMultica(data)
        store, runner = make(fake, self.db)
        res = collect(store, runner)
        self.assertTrue(res["workspaces"][WS_A]["ok"], res)
        self.assertEqual(agent_mcp_calls(fake), [])
        self.assertEqual(self.ws_block(store)["bindings_state"], "none")

    def test_fingerprint_gates_calls(self):
        fake = FakeMultica(mcp_fixture())
        store, runner = make(fake, self.db)
        collect(store, runner)
        self.assertEqual(len(agent_mcp_calls(fake)), 1)
        fake.calls.clear()
        # Unchanged fingerprint: only rounds increments, no agent calls.
        collect(store, runner)
        self.assertEqual(agent_mcp_calls(fake), [])
        state = store.query("SELECT * FROM mcp_state WHERE workspace_id=?", (WS_A,))[0]
        self.assertEqual(state["rounds"], 1)
        # Fingerprint change re-reads immediately.
        fake.workspaces[WS_A]["mcp_servers"][0]["updated_at"] = "2026-10-21T00:00:00Z"
        collect(store, runner)
        self.assertEqual(len(agent_mcp_calls(fake)), 1)
        self.assertEqual(
            store.query("SELECT rounds FROM mcp_state WHERE workspace_id=?", (WS_A,))[0]["rounds"], 0)

    def test_periodic_refresh_after_mcp_every(self):
        fake = FakeMultica(mcp_fixture())
        store, runner = make(fake, self.db)
        # Refresh happens on cycle 1 (no fingerprint) and again once rounds
        # reaches mcp_every — i.e. after 5 unchanged cycles, on cycle 6.
        for cycle in range(1, 7):
            fake.calls.clear()
            collect(store, runner, mcp_every=5)
            got = len(agent_mcp_calls(fake))
            want = 1 if cycle in (1, 6) else 0
            self.assertEqual(got, want, f"cycle {cycle}")

    def test_archived_agents_never_queried(self):
        data = mcp_fixture()
        data[WS_A]["agents"].append(agent(WS_A, "old", archived_at="2026-01-01T00:00:00Z"))
        data[WS_A]["agent_mcp"]["old"] = [{"id": "srv-linear", "name": "linear"}]
        fake = FakeMultica(data)
        store, runner = make(fake, self.db)
        collect(store, runner)
        self.assertEqual(agent_mcp_calls(fake, "old"), [])

    def test_partial_failure_retries_next_cycle(self):
        data = mcp_fixture()
        data[WS_A]["agents"].append(agent(WS_A, "ag2", mcp_config=None))
        data[WS_A]["agent_mcp"]["ag2"] = [{"id": "srv-linear", "name": "linear", "enabled": True}]
        data[WS_A]["mcp_servers"][0]["agent_count"] = 2
        fake = FakeMultica(data)
        fake.fail_agent_mcp.add("ag2")
        store, runner = make(fake, self.db)
        collect(store, runner)
        self.assertEqual(self.ws_block(store)["bindings_state"], "partial")
        states = {r["agent_id"]: r["state"] for r in
                  store.query("SELECT * FROM mcp_agent_state WHERE workspace_id=?", (WS_A,))}
        self.assertEqual(states, {"ag1": "ok", "ag2": "error"})
        fp = store.query("SELECT fingerprint, rounds FROM mcp_state WHERE workspace_id=?", (WS_A,))[0]
        self.assertIsNone(fp["fingerprint"])  # no fingerprint stored: next cycle retries
        self.assertEqual(fp["rounds"], 1)
        fake.calls.clear()
        fake.fail_agent_mcp.clear()
        collect(store, runner)
        self.assertEqual(len(agent_mcp_calls(fake)), 2)  # retried despite unchanged library
        self.assertEqual(self.ws_block(store)["bindings_state"], "ok")

    def test_agent_error_state_listed_in_view(self):
        data = mcp_fixture()
        data[WS_A]["agents"].append(agent(WS_A, "ag2", mcp_config=None))
        data[WS_A]["mcp_servers"][0]["agent_count"] = 2
        fake = FakeMultica(data)
        fake.fail_agent_mcp.add("ag2")
        store, runner = make(fake, self.db)
        collect(store, runner)
        item = next(a for a in self.mcp_view(store)["agents"] if a["id"] == "ag2")
        self.assertEqual(item["bindings_state"], "error")
        self.assertEqual(item["bindings"], [])
        self.assertEqual(item["effective"]["state"], "partial")

    def test_library_error_keeps_rows_and_workspace_ok(self):
        fake = FakeMultica(mcp_fixture())
        store, runner = make(fake, self.db)
        collect(store, runner)
        fake.fail_workspace_mcp[WS_A] = "unknown command workspace mcp"
        res = collect(store, runner)
        self.assertTrue(res["workspaces"][WS_A]["ok"], res)
        block = self.ws_block(store)
        self.assertEqual(block["library_state"], "error")
        self.assertEqual(len(block["servers"]), 2)  # last good rows kept
        sync = store.query(
            "SELECT last_error_kind FROM sync_status WHERE workspace_id=? AND resource='mcp_library'",
            (WS_A,))[0]
        self.assertIsNotNone(sync["last_error_kind"])
        meta = views(store).meta()
        alpha = next(w for w in meta["workspaces"] if w["id"] == WS_A)
        self.assertEqual(alpha["collect_state"], "ok")  # mcp failure never flips the workspace

    def test_never_collected(self):
        fake = FakeMultica(mcp_fixture())
        fake.fail_workspace_mcp[WS_A] = "unknown command"
        store, runner = make(fake, self.db)
        collect(store, runner)
        block = self.ws_block(store)
        self.assertEqual(block["library_state"], "never")
        self.assertEqual(block["servers"], [])
        self.assertEqual(block["bindings_state"], "never")

    def test_auth_error_purges_mcp_tables(self):
        fake = FakeMultica(mcp_fixture())
        store, runner = make(fake, self.db)
        collect(store, runner)
        for table in ("mcp_servers", "mcp_bindings", "mcp_agent_state", "mcp_state"):
            self.assertGreater(
                store.query(f"SELECT COUNT(*) n FROM {table} WHERE workspace_id=?", (WS_A,))[0]["n"], 0, table)
        fake.fail_workspace_mcp[WS_A] = "403 forbidden: not a member"
        collect(store, runner)
        for table in ("mcp_servers", "mcp_bindings", "mcp_agent_state", "mcp_state"):
            self.assertEqual(
                store.query(f"SELECT COUNT(*) n FROM {table} WHERE workspace_id=?", (WS_A,))[0]["n"], 0, table)


class SentinelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "board.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_sentinel_leaks(self):
        data = mcp_fixture()
        data[WS_A]["agents"] = [
            sentinel_agent(WS_A, "ag1"),
            agent(WS_A, "ag2", mcp_config="SENT_INVALID_9f1"),
        ]
        data[WS_A]["agent_mcp"] = {"ag1": [{"id": "srv-linear", "name": "linear", "enabled": True}]}
        fake = FakeMultica(data)
        store, runner = make(fake, self.db)

        records: list[str] = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Capture(level=logging.DEBUG)
        root = logging.getLogger()
        root.addHandler(handler)
        old_level = root.level
        root.setLevel(logging.DEBUG)
        try:
            res = collect(store, runner)
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)
        self.assertTrue(res["workspaces"][WS_A]["ok"], res)

        sentinels = list(SENTINELS) + ["SENT_INVALID_9f1"]

        # (a) raw bytes of the SQLite cache (main file, WAL, shm).
        store.close()
        for path in self.db.parent.iterdir():
            blob = path.read_bytes()
            for needle in sentinels + [FAKE_TOKEN]:
                self.assertNotIn(needle.encode(), blob, path.name)

        # (b) every API document.
        store = Store(self.db)
        v = views(store)
        docs = {
            "mcp": v.mcp(), "agents": v.agents(), "meta": v.meta(), "issues": v.issues(),
            "runtimes": v.runtimes(), "attention": v.attention(), "changes": v.changes(None),
            "failure_stats": v.failure_stats(), "layers": v.layers(), "usage": v.usage(),
        }
        for name, doc in docs.items():
            blob = json.dumps(doc)
            for needle in sentinels + [FAKE_TOKEN]:
                self.assertNotIn(needle, blob, f"{name}: {needle}")

        # (c) DEBUG log output.
        for needle in sentinels:
            self.assertFalse(any(needle in r for r in records), needle)

        # (d) invalid mcp_config: fixed message, correct summary, correct counts.
        own = json.loads(store.query("SELECT mcp_own_json FROM agents WHERE id='ag1'")[0]["mcp_own_json"])
        self.assertEqual(own["state"], "managed")
        by_name = {s["name"]: s for s in own["servers"]}
        self.assertEqual(set(by_name), {"sent-stdio", "sent-http", "sent-legacy"})
        self.assertEqual(by_name["sent-stdio"]["transport"], "stdio")
        self.assertEqual(by_name["sent-stdio"]["env"], {"set": True, "count": 1})
        self.assertEqual(by_name["sent-stdio"]["headers"], {"set": False, "count": 0})
        self.assertEqual(by_name["sent-http"]["transport"], "http")
        self.assertEqual(by_name["sent-http"]["headers"], {"set": True, "count": 1})
        bad = json.loads(store.query("SELECT mcp_own_json FROM agents WHERE id='ag2'")[0]["mcp_own_json"])
        self.assertEqual(bad, {"state": "invalid", "servers": [], "message": INVALID_MESSAGE})
        store.close()


class ContractShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "board.sqlite"
        cls.store, runner = make(FakeMultica(mcp_fixture()), cls.db)
        collect(cls.store, runner)
        cls.v = views(cls.store)
        cls.httpd = BoardServer("127.0.0.1", 0, cls.v)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.store.close()
        cls.tmp.cleanup()

    def get(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, body

    def test_top_level_and_nested_keys(self):
        status, raw = self.get("/api/mcp")
        self.assertEqual(status, 200)
        body = json.loads(raw)
        self.assertEqual(set(body), {"generated_at", "mcp_schema", "mcp_every_rounds",
                                     "support", "workspaces", "agents", "attention"})
        self.assertEqual(body["mcp_schema"], 1)
        self.assertEqual(body["mcp_every_rounds"], 5)
        self.assertEqual(set(body["support"]), {"source", "providers"})
        ws = next(w for w in body["workspaces"] if w["id"] == WS_A)
        self.assertEqual(set(ws), {"id", "slug", "name", "library_state", "library_collected_at",
                                   "library_error", "bindings_state", "bindings_collected_at", "servers"})
        for s in ws["servers"]:
            self.assertEqual(set(s), {"key", "id", "name", "transport", "agent_count",
                                      "enabled_count", "disabled_count", "config_visible",
                                      "created_at", "updated_at"})
            self.assertIs(s["config_visible"], False)
        ag = next(a for a in body["agents"] if a["id"] == "ag1")
        self.assertEqual(set(ag), {"key", "workspace_id", "id", "name", "archived", "runtime_id",
                                   "provider", "runtime_loads", "own_config", "bindings_state",
                                   "bindings", "effective"})
        self.assertEqual(set(ag["own_config"]), {"state", "servers"})
        self.assertEqual(set(ag["effective"]), {"state", "servers"})
        for b in ag["bindings"]:
            self.assertEqual(set(b), {"server_id", "name", "enabled", "shadowed"})
        self.assertEqual(set(body["attention"]), {"badge", "counts", "buckets"})
        self.assertEqual(set(body["attention"]["counts"]),
                         {"ignored", "shadowed", "may_fail", "hidden", "unused", "disabled_only"})

    def test_query_string_rejected(self):
        status, body = self.get("/api/mcp?x=1")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "bad_request")

    def test_agents_endpoint_has_no_mcp_fields(self):
        status, raw = self.get("/api/agents")
        self.assertEqual(status, 200)
        for item in json.loads(raw)["items"]:
            self.assertNotIn("mcp_config", item)
            self.assertNotIn("mcp_own_json", item)

    def test_workspace_fields(self):
        body = self.v.mcp()
        ws = next(w for w in body["workspaces"] if w["id"] == WS_A)
        self.assertEqual(ws["library_state"], "ok")
        self.assertEqual(ws["bindings_state"], "ok")
        self.assertIsNone(ws["library_error"])
        self.assertIsNotNone(ws["library_collected_at"])
        self.assertIsNotNone(ws["bindings_collected_at"])
        servers = {s["id"]: s for s in ws["servers"]}
        self.assertEqual(servers["srv-linear"]["enabled_count"], 1)
        self.assertEqual(servers["srv-linear"]["disabled_count"], 0)
        self.assertEqual(servers["srv-fs"]["enabled_count"], 0)
        self.assertEqual(servers["srv-linear"]["key"], f"{WS_A}:srv-linear")


class EffectiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "board.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def run_case(self, mcp_config, bindings, redacted=False):
        data = mcp_fixture()
        data[WS_A]["agents"] = [agent(WS_A, "ag1", mcp_config=mcp_config,
                                      mcp_config_redacted=redacted)]
        data[WS_A]["agent_mcp"] = {"ag1": bindings}
        data[WS_A]["mcp_servers"][0]["agent_count"] = 1 if bindings else 0
        store, runner = make(FakeMultica(data), self.db)
        collect(store, runner)
        body = views(store).mcp()
        item = next((a for a in body["agents"] if a["id"] == "ag1"), None)
        store.close()
        return item

    def test_library_only(self):
        item = self.run_case(None, [{"id": "srv-linear", "name": "linear", "enabled": True}])
        self.assertEqual(item["own_config"]["state"], "none")
        self.assertEqual(item["effective"]["state"], "known")
        self.assertEqual(item["effective"]["servers"],
                         [{"name": "linear", "from": "library", "transport": "http"}])
        self.assertIs(item["bindings"][0]["shadowed"], False)

    def test_own_only(self):
        item = self.run_case({"mcpServers": {"solo": {"command": "x"}}}, [])
        self.assertIsNotNone(item)  # own managed agents are listed even with zero bindings
        self.assertEqual(item["own_config"]["state"], "managed")
        self.assertEqual(item["bindings_state"], "none")
        self.assertEqual(item["bindings"], [])
        self.assertEqual(item["effective"]["state"], "known")
        self.assertEqual(item["effective"]["servers"],
                         [{"name": "solo", "from": "own", "transport": "stdio"}])

    def test_same_name_shadowing(self):
        item = self.run_case(
            {"mcpServers": {"linear": {"command": "own"}}},
            [{"id": "srv-linear", "name": "linear", "enabled": True}],
        )
        self.assertIs(item["bindings"][0]["shadowed"], True)
        self.assertEqual(item["effective"]["servers"],
                         [{"name": "linear", "from": "own", "transport": "stdio"}])

    def test_own_in_legacy_container(self):
        item = self.run_case({"mcp": {"legacy-srv": {"url": "https://x"}}}, [])
        self.assertEqual(item["own_config"]["state"], "managed")
        self.assertEqual(item["own_config"]["servers"][0]["name"], "legacy-srv")

    def test_own_empty_object_still_managed(self):
        item = self.run_case({}, [{"id": "srv-linear", "name": "linear", "enabled": True}])
        self.assertEqual(item["own_config"], {"state": "managed", "servers": []})
        self.assertEqual(item["effective"]["servers"],
                         [{"name": "linear", "from": "library", "transport": "http"}])
        self.assertIs(item["bindings"][0]["shadowed"], False)

    def test_hidden_own_shadowed_null(self):
        item = self.run_case(None, [{"id": "srv-linear", "name": "linear", "enabled": True}],
                             redacted=True)
        self.assertEqual(item["own_config"]["state"], "hidden")
        self.assertIsNone(item["bindings"][0]["shadowed"])
        self.assertEqual(item["effective"]["state"], "partial")


class SupportTests(unittest.TestCase):
    def test_table_covers_ts_set(self):
        providers = SUPPORT["providers"]
        for p in TS_PROVIDERS:
            self.assertIn(p, providers)
            self.assertNotEqual(providers[p]["loads"], "no", p)
        self.assertEqual(providers["antigravity"]["loads"], "no")
        self.assertEqual(providers["copilot"]["loads"], "no")
        self.assertIn("v0.6.5", SUPPORT["source"])

    def test_runtime_loads(self):
        self.assertEqual(runtime_loads("codex", True), "yes")
        self.assertEqual(runtime_loads("opencode", True), "conditional")
        self.assertEqual(runtime_loads("nonexistent-provider", True), "unknown")
        self.assertEqual(runtime_loads(None, True), "unknown")
        self.assertEqual(runtime_loads("codex", False), "unbound")
        self.assertEqual(runtime_loads(None, False), "unbound")


class AttentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "board.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_buckets(self):
        data = {
            WS_A: {
                "meta": {"id": WS_A, "name": "Alpha", "slug": "alpha"},
                "issues": [],
                "runtimes": [
                    runtime(WS_A, "rt-anti", provider="antigravity"),
                    runtime(WS_A, "rt-oc", provider="opencode"),
                    runtime(WS_A, "rt-codex", provider="codex"),
                ],
                "agents": [
                    agent(WS_A, "a-ignored", runtime_id="rt-anti", mcp_config=None),
                    agent(WS_A, "a-unbound", runtime_id=None, runtime_bound=False, mcp_config=None),
                    agent(WS_A, "a-shadow", runtime_id="rt-codex",
                          mcp_config={"mcpServers": {"linear": {"command": "x"}}}),
                    agent(WS_A, "a-fail", runtime_id="rt-oc", mcp_config=None),
                    agent(WS_A, "a-hidden", runtime_id="rt-codex", mcp_config=None,
                          mcp_config_redacted=True),
                    agent(WS_A, "a-off", runtime_id="rt-codex", mcp_config=None),
                ],
                "tasks": {},
                "mcp_servers": [
                    {"id": "srv-linear", "workspace_id": WS_A, "name": "linear", "transport": "http",
                     "agent_count": 4, "created_at": None, "updated_at": "2026-10-20T00:00:00Z"},
                    {"id": "srv-idle", "workspace_id": WS_A, "name": "idle", "transport": "stdio",
                     "agent_count": 0, "created_at": None, "updated_at": "2026-10-20T00:00:00Z"},
                    {"id": "srv-off", "workspace_id": WS_A, "name": "off", "transport": "stdio",
                     "agent_count": 1, "created_at": None, "updated_at": "2026-10-20T00:00:00Z"},
                ],
                "agent_mcp": {
                    "a-ignored": [{"id": "srv-linear", "name": "linear", "enabled": True}],
                    "a-unbound": [{"id": "srv-linear", "name": "linear", "enabled": True}],
                    "a-shadow": [{"id": "srv-linear", "name": "linear", "enabled": True}],
                    "a-fail": [{"id": "srv-linear", "name": "linear", "enabled": True}],
                    "a-off": [{"id": "srv-off", "name": "off", "enabled": False}],
                },
            },
        }
        store, runner = make(FakeMultica(data), self.db)
        self.assertTrue(collect(store, runner)["ok"])
        body = views(store).mcp()
        att = body["attention"]
        self.assertEqual(set(att["counts"]),
                         {"ignored", "shadowed", "may_fail", "hidden", "unused", "disabled_only"})
        self.assertEqual(att["badge"],
                         att["counts"]["ignored"] + att["counts"]["shadowed"] + att["counts"]["may_fail"])
        b = att["buckets"]
        ignored = {(i["agent_id"], i["reason"]) for i in b["ignored"]}
        self.assertEqual(ignored, {("a-ignored", "runtime_no_mcp"), ("a-unbound", "runtime_unbound")})
        self.assertEqual([i["server_id"] for i in b["ignored"]], [None, None])
        self.assertEqual(len(b["shadowed"]), 1)
        sh = b["shadowed"][0]
        self.assertEqual((sh["agent_id"], sh["server_id"], sh["reason"], sh["key"]),
                         ("a-shadow", "srv-linear", "own_config_same_name", f"{WS_A}:a-shadow:srv-linear"))
        self.assertEqual([i["agent_id"] for i in b["may_fail"]], ["a-fail"])
        self.assertEqual(b["may_fail"][0]["reason"], "opencode_v2_unsupported")
        self.assertEqual([i["agent_id"] for i in b["hidden"]], ["a-hidden"])
        self.assertEqual(b["hidden"][0]["reason"], "config_redacted")
        self.assertEqual([i["server_id"] for i in b["unused"]], ["srv-idle"])
        self.assertEqual([i["server_id"] for i in b["disabled_only"]], ["srv-off"])
        for items in b.values():
            for i in items:
                self.assertEqual(set(i), {"key", "workspace_id", "agent_id", "server_id", "name", "reason"})
        store.close()

    def test_empty_buckets_absent_from_buckets_but_counted(self):
        store, runner = make(FakeMultica(mcp_fixture()), self.db)
        collect(store, runner)
        att = views(store).mcp()["attention"]
        self.assertEqual(att["counts"]["hidden"], 0)
        self.assertNotIn("hidden", att["buckets"])
        store.close()


if __name__ == "__main__":
    unittest.main()
