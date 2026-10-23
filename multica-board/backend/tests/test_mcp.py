from __future__ import annotations

import http.client
import json
import logging
import os
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
from multica_board.mcp_local import McpContext, OwnFingerprints  # noqa: E402
from multica_board.mcp_summary import INVALID_MESSAGE, summarize_mcp_config  # noqa: E402
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


def make(fake: FakeMultica, db: Path) -> tuple[Store, CliRunner]:
    store = Store(db)
    runner = CliRunner(profile=SERVER, _exec=fake, _sleep=lambda s: None, retries=0)
    return store, runner


def collect(store: Store, runner: CliRunner, ctx: McpContext | None = None, **opt):
    return Collector(runner, store, SERVER, CollectOptions(all_workspaces=True, **opt),
                     mcp_ctx=ctx).collect()


def views(store: Store, ctx: McpContext) -> Views:
    return Views(store, SERVER, app_url="https://app.test", collect_interval_s=60, mcp_ctx=ctx)


def make_ctx(root: Path, salt: bytes | None = None, own: OwnFingerprints | None = None) -> McpContext:
    home = root / "home"
    repo = root / "repo"
    data_dir = root / "data"
    for d in (home, repo, data_dir):
        d.mkdir(parents=True, exist_ok=True)
    (data_dir).chmod(0o700)
    ctx = McpContext(home=home, repo_root=repo, data_dir=data_dir)
    if salt is not None:
        ctx.salt = salt
    if own is not None:
        ctx.own = own
    return ctx


def write_cfg(home: Path, rel: str, text: str) -> None:
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


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
    def test_states(self):
        self.assertEqual(summarize_mcp_config(None, False), {"state": "none", "servers": []})
        self.assertEqual(summarize_mcp_config({"mcpServers": {}}, True), {"state": "hidden", "servers": []})
        self.assertEqual(summarize_mcp_config({"mcpServers": {}}, False),
                         {"state": "managed", "servers": []})
        bad = summarize_mcp_config("SENT_INVALID_9f1", False)
        self.assertEqual(bad["state"], "invalid")
        self.assertEqual(bad["message"], INVALID_MESSAGE)
        self.assertNotIn("SENT_INVALID_9f1", json.dumps(bad))

    def test_transport_enum_no_verbatim_echo(self):
        out = summarize_mcp_config({"mcpServers": {"s": {"type": "SENT-WEIRD-9f1"}}}, False)
        self.assertEqual(out["servers"][0]["transport"], "unknown")
        self.assertEqual(out["servers"][0]["broken_reason"], "unknown_transport")
        self.assertNotIn("SENT-WEIRD-9f1", json.dumps(out))
        for s in out["servers"]:
            self.assertEqual(set(s), {"name", "exact", "transport", "broken_reason", "env", "headers"})

    def test_exact_flag_and_display(self):
        out = summarize_mcp_config({"mcpServers": {"a  b": {"command": "x"}, "c": {"command": "x"}}}, False)
        by_name = {s["name"]: s for s in out["servers"]}
        self.assertEqual(by_name["a b"]["exact"], False)
        self.assertEqual(by_name["c"]["exact"], True)

    def test_legacy_container_and_collision(self):
        out = summarize_mcp_config(
            {"mcp": {"a": {"url": "https://x"}, "b": {"type": "local", "command": "c"}},
             "mcpServers": {"b": {"url": "https://y"}}}, False)
        self.assertEqual(out["state"], "managed")
        self.assertEqual([(s["name"], s["transport"]) for s in out["servers"]],
                         [("a", "http"), ("b", "http")])


class CollectorMcpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "board.sqlite"
        self.ctx = make_ctx(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def ws_block(self, store: Store, ws: str = WS_A):
        return next(w for w in views(store, self.ctx).mcp()["workspaces"] if w["id"] == ws)

    def test_empty_library_makes_no_agent_calls(self):
        data = mcp_fixture()
        data[WS_A]["mcp_servers"] = []
        data[WS_A]["agent_mcp"] = {}
        fake = FakeMultica(data)
        store, runner = make(fake, self.db)
        res = collect(store, runner, self.ctx)
        self.assertTrue(res["workspaces"][WS_A]["ok"], res)
        self.assertEqual(agent_mcp_calls(fake), [])
        self.assertEqual(self.ws_block(store)["bindings_state"], "none")

    def test_fingerprint_gates_calls(self):
        fake = FakeMultica(mcp_fixture())
        store, runner = make(fake, self.db)
        collect(store, runner, self.ctx)
        self.assertEqual(len(agent_mcp_calls(fake)), 1)
        fake.calls.clear()
        collect(store, runner, self.ctx)
        self.assertEqual(agent_mcp_calls(fake), [])
        state = store.query("SELECT * FROM mcp_state WHERE workspace_id=?", (WS_A,))[0]
        self.assertEqual(state["rounds"], 1)
        fake.workspaces[WS_A]["mcp_servers"][0]["updated_at"] = "2026-10-21T00:00:00Z"
        collect(store, runner, self.ctx)
        self.assertEqual(len(agent_mcp_calls(fake)), 1)

    def test_first_round_forces_mcp_read(self):
        """§3.5/§14.9: fresh OwnFingerprints must re-read despite unchanged fingerprint."""
        fake = FakeMultica(mcp_fixture())
        store, runner = make(fake, self.db)
        collect(store, runner, self.ctx)
        self.assertEqual(len(agent_mcp_calls(fake)), 1)
        fake.calls.clear()
        # Same SQLite (fingerprint unchanged, rounds < mcp_every) but a restarted
        # serve process: new OwnFingerprints lacks the workspace -> forced read.
        ctx2 = make_ctx(Path(self.tmp.name), salt=self.ctx.salt)
        ctx2.own = OwnFingerprints()
        collect(store, runner, ctx2)
        self.assertEqual(len(agent_mcp_calls(fake)), 1)

    def test_periodic_refresh_after_mcp_every(self):
        fake = FakeMultica(mcp_fixture())
        store, runner = make(fake, self.db)
        for cycle in range(1, 7):
            fake.calls.clear()
            collect(store, runner, self.ctx, mcp_every=5)
            got = len(agent_mcp_calls(fake))
            want = 1 if cycle in (1, 6) else 0
            self.assertEqual(got, want, f"cycle {cycle}")

    def test_archived_agents_never_queried(self):
        data = mcp_fixture()
        data[WS_A]["agents"].append(agent(WS_A, "old", archived_at="2026-01-01T00:00:00Z"))
        data[WS_A]["agent_mcp"]["old"] = [{"id": "srv-linear", "name": "linear"}]
        fake = FakeMultica(data)
        store, runner = make(fake, self.db)
        collect(store, runner, self.ctx)
        self.assertEqual(agent_mcp_calls(fake, "old"), [])

    def test_partial_failure_retries_next_cycle(self):
        data = mcp_fixture()
        data[WS_A]["agents"].append(agent(WS_A, "ag2", mcp_config=None))
        data[WS_A]["agent_mcp"]["ag2"] = [{"id": "srv-linear", "name": "linear", "enabled": True}]
        data[WS_A]["mcp_servers"][0]["agent_count"] = 2
        fake = FakeMultica(data)
        fake.fail_agent_mcp.add("ag2")
        store, runner = make(fake, self.db)
        collect(store, runner, self.ctx)
        self.assertEqual(self.ws_block(store)["bindings_state"], "partial")
        fake.calls.clear()
        fake.fail_agent_mcp.clear()
        collect(store, runner, self.ctx)
        self.assertEqual(len(agent_mcp_calls(fake)), 2)
        self.assertEqual(self.ws_block(store)["bindings_state"], "ok")

    def test_library_error_keeps_rows_and_marks_stale(self):
        fake = FakeMultica(mcp_fixture())
        store, runner = make(fake, self.db)
        collect(store, runner, self.ctx)
        fake.fail_workspace_mcp[WS_A] = "unknown command workspace mcp"
        res = collect(store, runner, self.ctx)
        self.assertTrue(res["workspaces"][WS_A]["ok"], res)
        block = self.ws_block(store)
        self.assertEqual(block["library_state"], "stale")
        names = [i["display_name"] for i in views(store, self.ctx).mcp()["items"]]
        self.assertIn("linear", names)  # stale data still proves presence
        self.assertIn(WS_A, views(store, self.ctx).mcp()["unknown"]["library"])

    def test_name_exact_column_written(self):
        data = mcp_fixture()
        data[WS_A]["mcp_servers"][0]["name"] = "a  b"
        fake = FakeMultica(data)
        store, runner = make(fake, self.db)
        collect(store, runner, self.ctx)
        row = store.query("SELECT name, name_exact FROM mcp_servers WHERE id='srv-linear'")[0]
        self.assertEqual((row["name"], row["name_exact"]), ("a b", 0))

    def test_auth_error_purges_mcp_tables(self):
        fake = FakeMultica(mcp_fixture())
        store, runner = make(fake, self.db)
        collect(store, runner, self.ctx)
        fake.fail_workspace_mcp[WS_A] = "403 forbidden: not a member"
        collect(store, runner, self.ctx)
        for table in ("mcp_servers", "mcp_bindings", "mcp_agent_state", "mcp_state"):
            self.assertEqual(
                store.query(f"SELECT COUNT(*) n FROM {table} WHERE workspace_id=?", (WS_A,))[0]["n"], 0, table)


class OwnFingerprintLifecycleTests(unittest.TestCase):
    """§14.9: ready/pending lifecycle across restart, key change, updated_at."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "board.sqlite"
        self.ctx = make_ctx(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, name="linear", cmd="x"):
        data = mcp_fixture()
        data[WS_A]["agents"] = [agent(WS_A, "ag1", runtime_id="rt1",
                                      mcp_config={"mcpServers": {name: {"command": cmd}}})]
        data[WS_A]["agent_mcp"] = {"ag1": [{"id": "srv-linear", "name": "linear", "enabled": True}]}
        return data

    def own_entry(self, body, aid="ag1"):
        for i in body["items"]:
            for we in i["workspace_entries"]:
                if we["kind"] == "own" and we.get("agent_id") == aid:
                    return i, we
        return None, None

    def test_ready_then_restart_pending_then_recovered(self):
        store, runner = make(FakeMultica(self.fixture()), self.db)
        collect(store, runner, self.ctx)
        item, we = self.own_entry(views(store, self.ctx).mcp())
        self.assertEqual(we["fp_state"], "ready")
        self.assertIsNotNone(we["group"])
        own = json.loads(store.query("SELECT mcp_own_json FROM agents WHERE id='ag1'")[0]["mcp_own_json"])
        self.assertEqual(set(own), {"state", "servers"})
        self.assertTrue(all(set(s) <= {"name", "exact", "transport", "broken_reason", "env", "headers"}
                            for s in own["servers"]))
        # Simulated restart: same SQLite, new Views/OwnFingerprints -> pending.
        ctx2 = make_ctx(Path(self.tmp.name), salt=self.ctx.salt)
        body2 = views(store, ctx2).mcp()
        item2, we2 = self.own_entry(body2)
        self.assertEqual(we2["fp_state"], "pending")
        self.assertIsNone(we2["group"])
        self.assertTrue(item2["consistency"]["partial"])
        self.assertNotIn(item2["consistency"]["state"], ("same", "differs"))
        self.assertEqual(body2["attention"]["buckets"]["drift"]["partial"], True)
        fp_chip = next(c for c in body2["meta"]["sources"] if c["id"] == "fingerprints")
        self.assertEqual(fp_chip["state"], "warn")
        # One collector round restores readiness.
        collect(store, runner, ctx2)
        _, we3 = self.own_entry(views(store, ctx2).mcp())
        self.assertEqual(we3["fp_state"], "ready")

    def test_standalone_collect_leaves_pending(self):
        store, runner = make(FakeMultica(self.fixture()), self.db)
        collect(store, runner, make_ctx(Path(self.tmp.name)))  # another process: memory dies with it
        _, we = self.own_entry(views(store, self.ctx).mcp())
        self.assertEqual(we["fp_state"], "pending")

    def test_key_change_pends_local_regroups(self):
        store, runner = make(FakeMultica(self.fixture()), self.db)
        write_cfg(self.ctx.home, ".cursor/mcp.json",
                  json.dumps({"mcpServers": {"linear": {"command": "x"}}}))
        collect(store, runner, self.ctx)
        item, we = self.own_entry(views(store, self.ctx).mcp())
        self.assertEqual(we["fp_state"], "ready")
        self.assertEqual(item["consistency"]["state"], "same")  # own fp == local fp
        # Rotate the key file: own records go pending, local files regroup at once.
        key = self.ctx.data_dir / "mcp-fingerprint.key"
        key.write_bytes(b"z" * 32)
        key.chmod(0o600)
        item2, we2 = self.own_entry(views(store, self.ctx).mcp())
        self.assertEqual(we2["fp_state"], "pending")
        ent = next(e for e in item2["entries"] if e["tool"] == "cursor")
        self.assertIsNotNone(ent["group"])
        self.assertEqual(item2["consistency"]["state"], "single")

    def test_updated_at_change_pends_until_recollect(self):
        # Another process recollects the agent (new updated_at in SQLite) while
        # this serve's OwnFingerprints still holds the older record -> pending.
        fake = FakeMultica(self.fixture())
        store, runner = make(fake, self.db)
        collect(store, runner, self.ctx)
        fake.workspaces[WS_A]["agents"][0]["updated_at"] = "2026-10-30T00:00:00Z"
        collect(store, runner, make_ctx(Path(self.tmp.name)))  # the "other" process
        _, we = self.own_entry(views(store, self.ctx).mcp())
        self.assertEqual(we["fp_state"], "pending")
        collect(store, runner, self.ctx)  # this process repopulates
        _, we = self.own_entry(views(store, self.ctx).mcp())
        self.assertEqual(we["fp_state"], "ready")


class V1CompatTests(unittest.TestCase):
    """§14.6: v1 SQLite sentinel transports render as unknown, never verbatim."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "board.sqlite"
        self.ctx = make_ctx(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_v1_sentinel_transport_rows(self):
        store = Store(self.db)
        store.upsert_many(SERVER, WS_A, "workspaces", [{"id": WS_A, "name": "A", "slug": "a"}])
        store.upsert_many(SERVER, WS_A, "mcp_servers", [
            {"id": "s1", "name": "x", "transport": "SENT-TRAN-9f1", "agent_count": 0}])
        store.upsert_many(SERVER, WS_A, "agents", [
            {"id": "ag1", "name": "ag", "updated_at": "t",
             "mcp_own_json": json.dumps({"state": "managed", "servers": [
                 {"name": "x", "transport": "SENT-TRAN-9f1",
                  "env": {"set": False, "count": 0}, "headers": {"set": False, "count": 0}}]})}])
        body = views(store, self.ctx).mcp()
        blob = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("SENT-TRAN-9f1", blob)
        transports = {t for i in body["items"] for t in i["transports"]}
        self.assertEqual(transports, {"unknown"})
        store.close()


class SentinelSweepTests(unittest.TestCase):
    """§14.7: sentinel sweep over /api/*, SQLite bytes incl WAL, -v logs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "board.sqlite"
        self.ctx = make_ctx(Path(self.tmp.name))
        write_cfg(self.ctx.home, ".cursor/mcp.json", json.dumps({"mcpServers": {
            "s": {"command": f"cmd-{SENTINELS[6]}", "args": [SENTINELS[5]], "cwd": SENTINELS[7],
                  "env": {SENTINELS[1]: SENTINELS[0]}, "url": f"https://{SENTINELS[4]}.x",
                  "headers": {SENTINELS[3]: SENTINELS[2]}, "type": "stdio", "junk": SENTINELS[0]}}}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_sentinel_leaks(self):
        data = mcp_fixture()
        data[WS_A]["agents"] = [sentinel_agent(WS_A, "ag1"),
                                agent(WS_A, "ag2", mcp_config="SENT_INVALID_9f1")]
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
        old = root.level
        root.setLevel(logging.DEBUG)
        try:
            collect(store, runner, self.ctx)
        finally:
            root.removeHandler(handler)
            root.setLevel(old)

        sentinels = list(SENTINELS) + ["SENT_INVALID_9f1"]
        v = views(store, self.ctx)
        docs = {"mcp": v.mcp(), "agents": v.agents(), "meta": v.meta(), "issues": v.issues(),
                "runtimes": v.runtimes(), "attention": v.attention(), "changes": v.changes(None),
                "failure_stats": v.failure_stats(), "layers": v.layers(), "usage": v.usage()}
        for name, doc in docs.items():
            blob = json.dumps(doc, ensure_ascii=False)
            for needle in sentinels + [FAKE_TOKEN]:
                self.assertNotIn(needle, blob, f"{name}: {needle}")
        for needle in sentinels:
            self.assertFalse(any(needle in r for r in records), needle)
        store.close()
        for path in self.db.parent.iterdir():
            if not path.is_file() or path.name == "mcp-fingerprint.key":
                continue
            blob = path.read_bytes()
            for needle in sentinels + [FAKE_TOKEN]:
                self.assertNotIn(needle.encode(), blob, path.name)


class ContractShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "board.sqlite"
        cls.ctx = make_ctx(Path(cls.tmp.name))
        cls.store, runner = make(FakeMultica(mcp_fixture()), cls.db)
        collect(cls.store, runner, cls.ctx)
        cls.v = views(cls.store, cls.ctx)
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

    def test_schema2_top_level(self):
        status, raw = self.get("/api/mcp")
        self.assertEqual(status, 200)
        body = json.loads(raw)
        self.assertEqual(body["mcp_schema"], 2)
        self.assertTrue(body["ok"])
        for key in ("generated_at", "multica_collected_at", "tools", "repos", "workspaces",
                    "support", "unknown", "items", "summary", "attention", "meta", "error"):
            self.assertIn(key, body)
        self.assertEqual(len(body["tools"]), 9)
        self.assertEqual([s["id"] for s in body["summary"]],
                         ["all", "both", "local_only", "multica_only"])
        self.assertEqual({c["id"] for c in body["meta"]["sources"]},
                         {"local_files", "project_files", "fingerprints", "multica_library",
                          "agent_bindings", "multica_own_config", "runtime_support"})
        for item in body["items"]:
            self.assertRegex(item["id"], r"^m[0-9a-z]{20}(-\d+)?$")
            self.assertIn(item["consistency"]["state"],
                          ("broken", "differs", "same", "single", "incomparable"))
        att = body["attention"]
        self.assertIn(att["tone"], ("bad", "warn", "unknown", "none"))
        self.assertEqual(list(att["buckets"]), ["broken", "ignored", "shadowed", "may_fail", "drift"])
        for item in body["items"]:
            self.assertIsNotNone(item.get("id"))

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

    def test_multica_only_row_cells_not_applicable(self):
        body = self.v.mcp()
        row = next(i for i in body["items"] if i["display_name"] == "linear")
        self.assertEqual(row["source_class"], "multica_only")
        self.assertTrue(all(v == "not_applicable" for v in row["tools"].values()))
        self.assertTrue(all(v == "not_applicable" for v in row["repo_cells"].values()))
        self.assertEqual(row["consistency"]["state"], "incomparable")
        self.assertEqual(row["transports"], ["http"])
        # tidy: srv-fs has agent_count 0 -> marked tidy, never badge-counted
        fs = next(i for i in body["items"] if i["display_name"] == "fs")
        self.assertEqual(fs["tidy"], {"reason": "no_agents", "workspace_id": WS_A})
        self.assertEqual(body["attention"]["tidy"]["count"], 1)
        self.assertNotIn(fs["id"], [it["id"] for b in body["attention"]["buckets"].values()
                                    for it in b["items"]])


class RawIdentityTests(unittest.TestCase):
    """§14.10: distinct raw identities never merge; display_dup numbered."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "board.sqlite"
        self.ctx = make_ctx(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_whitespace_case_nfc_variants_are_distinct_rows(self):
        names = ["a  b", "a b", " github", "github", "a\tb", "", "   ",
                 "Github", "café", "café", "x" * 81 + "1", "x" * 81 + "2",
                 "ghp_aaaaaaaaaaaaaaaaaaaa", "ghp_bbbbbbbbbbbbbbbbbbbb"]
        cfg = {"mcpServers": {n: {"command": "x"} for n in names}}
        write_cfg(self.ctx.home, ".cursor/mcp.json", json.dumps(cfg))
        store = Store(self.db)
        body = views(store, self.ctx).mcp()
        self.assertEqual(len(body["items"]), len(names))
        self.assertEqual(len({i["id"] for i in body["items"]}), len(names))
        dup = [i for i in body["items"] if i["display_dup"]]
        self.assertTrue(all(i["display_dup"]["total"] > 1 for i in dup))
        blob = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("ghp_aaaaaaaaaaaaaaaaaaaa", blob)
        self.assertNotIn("ghp_bbbbbbbbbbbbbbbbbbbb", blob)
        store.close()

    def test_unverified_exact_false_not_merged_after_restart(self):
        data = mcp_fixture()
        data[WS_A]["agents"] = [agent(WS_A, "ag1", runtime_id="rt1",
                                      mcp_config={"mcpServers": {"a  b": {"command": "x"}}})]
        data[WS_A]["agent_mcp"] = {"ag1": []}
        write_cfg(self.ctx.home, ".cursor/mcp.json",
                  json.dumps({"mcpServers": {"a  b": {"command": "y"}}}))
        store, runner = make(FakeMultica(data), self.db)
        collect(store, runner, self.ctx)
        body = views(store, self.ctx).mcp()
        items = [i for i in body["items"] if i["display_name"] == "a b"]
        self.assertEqual(len(items), 1)  # merged via own-fingerprint row id
        # restart: own summary exact:false can't rejoin -> separate unverified row
        ctx2 = make_ctx(Path(self.tmp.name), salt=self.ctx.salt)
        body2 = views(store, ctx2).mcp()
        items2 = [i for i in body2["items"] if i["display_name"] == "a b"]
        self.assertEqual(len(items2), 2)
        unv = next(i for i in items2 if "name_unverified" in i["tags"])
        self.assertEqual(unv["consistency"]["state"], "incomparable")
        self.assertEqual(body2["attention"]["buckets"]["shadowed"]["partial"], True)


class UnknownFirstTests(unittest.TestCase):
    """§14.11: unknown sources beat absent evidence."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "board.sqlite"
        self.ctx = make_ctx(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_hermes_parse_error_no_not_applicable_uncertain_cards(self):
        data = mcp_fixture()
        write_cfg(self.ctx.home, ".hermes/config.yaml", "mcp_servers: 5\n")
        write_cfg(self.ctx.home, ".cursor/mcp.json",
                  json.dumps({"mcpServers": {"local1": {"command": "x"}}}))
        fake = FakeMultica(data)
        fake.fail_agent_mcp.add("ag1")  # ws bindings unknown
        store, runner = make(fake, self.db)
        collect(store, runner, self.ctx)
        body = views(store, self.ctx).mcp()
        hermes = next(t for t in body["tools"] if t["id"] == "hermes")
        self.assertEqual(hermes["files"][0]["state"], "parse_error")
        for i in body["items"]:
            self.assertEqual(i["tools"]["hermes"], "unknown")
            self.assertNotIn("not_applicable", i["tools"].values())
        local_row = next(i for i in body["items"] if i["display_name"] == "local1")
        self.assertEqual(local_row["source_class"], "local_only")
        self.assertTrue(local_row["source_uncertain"])
        mc_row = next(i for i in body["items"] if i["display_name"] == "linear")
        self.assertEqual(mc_row["source_class"], "multica_only")
        self.assertTrue(mc_row["source_uncertain"])
        local_card = next(s for s in body["summary"] if s["id"] == "local_only")
        self.assertEqual(local_card["uncertain"], 1)
        # bindings unknown: ws-a library entries can't be judged unused
        self.assertIsNone(next(i for i in body["items"] if i["display_name"] == "fs")["tidy"])
        self.assertTrue(body["attention"]["buckets"]["ignored"]["partial"])
        self.assertTrue(body["attention"]["tidy"]["partial"])
        self.assertIn("hermes", body["unknown"]["local_files"])
        self.assertIn(WS_A, body["unknown"]["bindings"])
        # consistency partial: a same-name entry could hide in the failed file
        self.assertTrue(local_row["consistency"]["partial"])

    def test_all_sources_failed_empty_unknown_tone(self):
        data = {WS_A: {"meta": {"id": WS_A, "name": "A", "slug": "a"}, "issues": [],
                       "agents": [agent(WS_A, "ag1")], "runtimes": [runtime(WS_A)], "tasks": {},
                       "mcp_servers": [], "agent_mcp": {}}}
        write_cfg(self.ctx.home, ".cursor/mcp.json", "{bad json")
        fake = FakeMultica(data)
        fake.fail_workspace_mcp[WS_A] = "down"
        store, runner = make(fake, self.db)
        collect(store, runner, self.ctx)
        body = views(store, self.ctx).mcp()
        self.assertEqual(body["items"], [])
        self.assertEqual(body["attention"]["tone"], "unknown")
        self.assertEqual(body["summary"][0]["count"], 0)
        card_detail = next(c for c in body["meta"]["sources"] if c["id"] == "local_files")
        self.assertEqual(card_detail["state"], "warn")  # 8 absent + 1 unreadable

    def test_all_known_empty(self):
        data = {WS_A: {"meta": {"id": WS_A, "name": "A", "slug": "a"}, "issues": [],
                       "agents": [agent(WS_A, "ag1")], "runtimes": [runtime(WS_A)], "tasks": {},
                       "mcp_servers": [], "agent_mcp": {}}}
        store, runner = make(FakeMultica(data), self.db)
        collect(store, runner, self.ctx)
        body = views(store, self.ctx).mcp()
        self.assertEqual(body["items"], [])
        self.assertEqual(body["unknown"]["local_files"], [])
        self.assertEqual(body["attention"]["tone"], "none")


class BucketToneTests(unittest.TestCase):
    """§14.12: one object one bucket, order, tags, tone states."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "board.sqlite"
        self.ctx = make_ctx(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self):
        return {
            WS_A: {
                "meta": {"id": WS_A, "name": "Alpha", "slug": "alpha"},
                "issues": [],
                "runtimes": [runtime(WS_A, "rt-anti", provider="antigravity"),
                             runtime(WS_A, "rt-oc", provider="opencode"),
                             runtime(WS_A, "rt-codex", provider="codex")],
                "agents": [
                    agent(WS_A, "a-ignored", runtime_id="rt-anti", mcp_config=None),
                    agent(WS_A, "a-shadow", runtime_id="rt-codex",
                          mcp_config={"mcpServers": {"linear": {"command": "x"}}}),
                    agent(WS_A, "a-fail", runtime_id="rt-oc", mcp_config=None),
                ],
                "tasks": {},
                "mcp_servers": [
                    {"id": "srv-linear", "workspace_id": WS_A, "name": "linear", "transport": "http",
                     "agent_count": 3, "created_at": None, "updated_at": "2026-10-20T00:00:00Z"},
                ],
                "agent_mcp": {
                    "a-ignored": [{"id": "srv-linear", "name": "linear", "enabled": True}],
                    "a-shadow": [{"id": "srv-linear", "name": "linear", "enabled": True}],
                    "a-fail": [{"id": "srv-linear", "name": "linear", "enabled": True}],
                },
            },
        }

    def test_bucket_assignment_order_and_tags(self):
        store, runner = make(FakeMultica(self.fixture()), self.db)
        collect(store, runner, self.ctx)
        body = views(store, self.ctx).mcp()
        row = next(i for i in body["items"] if i["display_name"] == "linear")
        # broken order: ignored(2) < shadowed(1) < may_fail(1) -> first hit wins
        self.assertEqual(row["bucket"], "ignored")
        self.assertIn("shadowed", row["tags"])
        self.assertIn("may_fail", row["tags"])
        att = body["attention"]
        self.assertEqual(att["counts"], {"broken": 0, "ignored": 1, "shadowed": 0, "may_fail": 0, "drift": 0})
        self.assertEqual(att["badge"], 1)
        self.assertEqual(att["tone"], "bad")
        self.assertIn("a-ignored", row["reason"])

    def test_warn_tone_only_drift(self):
        write_cfg(self.ctx.home, ".cursor/mcp.json",
                  json.dumps({"mcpServers": {"s": {"command": "x"}}}))
        write_cfg(self.ctx.home, ".claude.json",
                  json.dumps({"mcpServers": {"s": {"command": "y"}}}))
        data = {WS_A: {"meta": {"id": WS_A, "name": "A", "slug": "a"}, "issues": [],
                       "agents": [agent(WS_A, "ag1")], "runtimes": [runtime(WS_A)], "tasks": {},
                       "mcp_servers": [], "agent_mcp": {}}}
        store, runner = make(FakeMultica(data), self.db)
        collect(store, runner, self.ctx)
        body = views(store, self.ctx).mcp()
        row = next(i for i in body["items"] if i["display_name"] == "s")
        self.assertEqual(row["bucket"], "drift")
        self.assertEqual(row["consistency"]["state"], "differs")
        self.assertEqual({g["id"] for g in row["consistency"]["groups"]}, {"A", "B"})
        self.assertEqual(body["attention"]["tone"], "warn")
        self.assertEqual(body["attention"]["badge"], 1)

    def test_key_rejection_disconnects_drift(self):
        write_cfg(self.ctx.home, ".cursor/mcp.json",
                  json.dumps({"mcpServers": {"s": {"command": "x"}}}))
        write_cfg(self.ctx.home, ".claude.json",
                  json.dumps({"mcpServers": {"s": {"command": "y"}}}))
        key = self.ctx.data_dir / "mcp-fingerprint.key"
        self.ctx.data_dir.chmod(0o777)  # key_dir_unsafe
        store = Store(self.db)
        body = views(store, self.ctx).mcp()
        drift = body["attention"]["buckets"]["drift"]
        self.assertFalse(drift["connected"])
        self.assertIn("指纹密钥", drift["reason"])
        self.assertEqual(body["unknown"]["key"], "key_dir_unsafe")
        chip = next(c for c in body["meta"]["sources"] if c["id"] == "fingerprints")
        self.assertEqual(chip["state"], "bad")
        self.assertFalse(key.exists())
        store.close()


class EffectiveTests(unittest.TestCase):
    """§14.13: v1 shadowing/effective cases on raw identity."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "board.sqlite"
        self.ctx = make_ctx(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def run_case(self, mcp_config, bindings, redacted=False):
        data = mcp_fixture()
        data[WS_A]["agents"] = [agent(WS_A, "ag1", mcp_config=mcp_config,
                                      mcp_config_redacted=redacted)]
        data[WS_A]["agent_mcp"] = {"ag1": bindings}
        data[WS_A]["mcp_servers"][0]["agent_count"] = 1 if bindings else 0
        store, runner = make(FakeMultica(data), self.db)
        collect(store, runner, self.ctx)
        body = views(store, self.ctx).mcp()
        store.close()
        return body

    def lib_entry(self, body):
        for row in body["items"]:
            for w in row["workspace_entries"]:
                if w["kind"] == "library" and w["server_id"] == "srv-linear":
                    return row, w
        raise AssertionError("no library entry")

    def test_library_only(self):
        body = self.run_case(None, [{"id": "srv-linear", "name": "linear", "enabled": True}])
        row, lib = self.lib_entry(body)
        self.assertEqual(lib["agents"][0]["shadowed"], False)
        self.assertEqual(lib["agents"][0]["runtime_loads"], "yes")

    def test_own_only(self):
        body = self.run_case({"mcpServers": {"solo": {"command": "x"}}}, [])
        row = next(i for i in body["items"] if i["display_name"] == "solo")
        own = next(w for w in row["workspace_entries"] if w["kind"] == "own")
        self.assertEqual(own["fp_state"], "ready")

    def test_same_name_shadowing_raw_identity(self):
        body = self.run_case({"mcpServers": {"linear": {"command": "x"}}},
                             [{"id": "srv-linear", "name": "linear", "enabled": True}])
        row, lib = self.lib_entry(body)
        self.assertIs(lib["agents"][0]["shadowed"], True)
        self.assertEqual(row["bucket"], "shadowed")

    def test_near_name_not_shadowed(self):
        # "linear " (trailing space) is a different raw identity: no shadowing, no merge.
        body = self.run_case({"mcpServers": {"linear ": {"command": "x"}}},
                             [{"id": "srv-linear", "name": "linear", "enabled": True}])
        row, lib = self.lib_entry(body)
        self.assertIs(lib["agents"][0]["shadowed"], False)

    def test_hidden_own_shadowed_null(self):
        body = self.run_case(None, [{"id": "srv-linear", "name": "linear", "enabled": True}],
                             redacted=True)
        row, lib = self.lib_entry(body)
        self.assertIsNone(lib["agents"][0]["shadowed"])
        self.assertEqual(body["unknown"]["own_hidden"], 1)

    def test_own_in_legacy_container(self):
        body = self.run_case({"mcp": {"legacy-srv": {"url": "https://x"}}}, [])
        row = next(i for i in body["items"] if i["display_name"] == "legacy-srv")
        own = next(w for w in row["workspace_entries"] if w["kind"] == "own")
        self.assertEqual(own["transport"], "http")


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


if __name__ == "__main__":
    unittest.main()
