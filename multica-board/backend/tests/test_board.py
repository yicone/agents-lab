from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_cli import FAKE_TOKEN, FakeMultica, agent, issue, runtime, task  # noqa: E402
from multica_board.changes import BadRequest, diff_issues, diff_runtimes, events_for_tasks  # noqa: E402
from multica_board.cli_runner import CliError, CliRunner, CommandNotAllowed, assert_read_only  # noqa: E402
from multica_board.collector import WORKSPACE, CollectOptions, Collector  # noqa: E402
from multica_board.redact import ERROR_KINDS, classify_error, redact  # noqa: E402
from multica_board.server import STATIC_CSP, BoardServer, listen_hosts  # noqa: E402
from multica_board.store import Store  # noqa: E402
from multica_board.views import ATTENTION_TEXT_BYTES, NotFound, Views, build_workspace_installs, render_attention_text  # noqa: E402

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
            "sk-" + "a" * 16,
            "deadbeef" * 8,
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
            "multica CLI not found: /opt/tls-network/multica": "process",
            "zsh: command not found: multica": "process",
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
            ["property", "create", "--name", "主层"],
            ["issue", "property", "set", "x", "主层", "L1"],
            ["workspace", "member", "invite", "a@b"],
            ["workspace", "switch", "x"],
            ["daemon", "stop"],
            ["auth", "token"],
            ["skill", "create", "x"],
            ["skill", "update", "x"],
            ["skill", "refresh"],
            ["skill", "import", "x"],
        ):
            with self.assertRaises(CommandNotAllowed, msg=args):
                assert_read_only(args)
        with self.assertRaises(CommandNotAllowed):
            assert_read_only(["issue", "list", "--server-url", "http://evil"])
        assert_read_only(["property", "list"])
        assert_read_only(["skill", "list"])

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

    def test_cwd_is_neutral_not_launch_dir(self):
        fake = FakeMultica(fixture())
        CliRunner(profile=SERVER, _exec=fake).run(["agent", "list"], workspace_id=WS_A)
        CliRunner(profile=SERVER, _exec=fake).run(["agent", "list"], workspace_id=WS_A)
        cwd = fake.cwds[0]
        self.assertIsNotNone(cwd)
        self.assertEqual(fake.cwds[1], cwd)  # shared across runners; the serve loop builds one per cycle
        self.assertNotEqual(os.path.realpath(cwd), os.path.realpath(os.getcwd()))
        self.assertEqual(os.listdir(cwd), [])
        for parent in Path(cwd).resolve().parents:
            self.assertFalse((parent / ".multica").exists(), parent)

    def test_real_subprocess_ignores_local_multica_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Path(tmp, "task-workdir")
            (launch / ".multica").mkdir(parents=True)
            (launch / ".multica" / "daemon_task_context.json").write_text("{}")
            stub = Path(tmp, "multica")
            stub.write_text('#!/bin/sh\nprintf \'{"cwd": "%s"}\' "$(pwd -P)"\n')
            stub.chmod(0o755)
            prev = os.getcwd()
            os.chdir(launch)
            try:
                res = CliRunner(profile=SERVER, binary=str(stub), retries=0).run(["workspace", "list"])
            finally:
                os.chdir(prev)
        child = Path(res.data["cwd"])
        self.assertNotEqual(child, launch.resolve())
        self.assertNotIn(launch.resolve(), child.parents)

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

    def test_enabled_skill_names_cached_without_bodies(self):
        data = fixture()
        data[WS_A]["agents"][0] = agent(
            WS_A,
            "ag1",
            skills=[
                {"name": "mem-distill", "enabled": True, "description": FAKE_TOKEN},
                {"name": "hermes-async-delegation", "enabled": True},
                {"name": "mem-distill", "enabled": True},
                {"name": "  spaced   name  ", "enabled": True},
                {"name": "off", "enabled": False, "content": FAKE_TOKEN},
                {"name": "", "enabled": True},
                "nope",
                {"enabled": True},
            ],
        )
        store, col = make(FakeMultica(data), self.db)
        col.collect()
        row = store.query("SELECT skills_json FROM agents WHERE id='ag1'")[0]
        self.assertEqual(
            json.loads(row["skills_json"]),
            ["hermes-async-delegation", "mem-distill", "spaced name"],
        )
        self.assertNotIn(FAKE_TOKEN.encode(), self.db.read_bytes())

    def test_instructions_redacted_before_store_and_skills_collected(self):
        import hashlib

        from multica_board.collector import instructions_sha256, normalize_instructions

        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        secret_hex = "ab" * 32
        instructions = ("word " * 80) + f"sk-abcdefghijk Bearer {FAKE_TOKEN} ghp_{'b' * 36} {jwt} {secret_hex}"
        data = fixture()
        data[WS_A]["agents"][0] = agent(
            WS_A,
            "ag1",
            description="public blurb",
            instructions=instructions,
            skills=[
                {"id": "sk1", "name": "mem-distill", "description": f"body {FAKE_TOKEN}", "enabled": True, "content": FAKE_TOKEN},
                {"id": "sk2", "name": "off", "description": "paused", "enabled": False, "content": FAKE_TOKEN},
            ],
            disabled_runtime_skills=["runtime-skill", {"id": "rs", "name": "other", "content": FAKE_TOKEN}],
        )
        data[WS_A]["skills"] = [{
            "id": "cloud1", "workspace_id": WS_A, "name": "mem-distill",
            "description": f"cloud {FAKE_TOKEN}", "labels": ["ops", {"name": "beta"}],
            "updated_at": "2026-10-20T00:00:00Z", "config": {"token": FAKE_TOKEN},
        }]
        data[WS_B]["skills"] = [{
            "id": "cloud2", "workspace_id": WS_B, "name": "mem-distill",
            "description": "beta skill", "labels": [], "updated_at": "2026-10-19T00:00:00Z",
        }]
        data[WS_B]["agents"][0] = agent(
            WS_B, "bg1", instructions="beta instructions",
            skills=[{"id": "cloud2", "name": "mem-distill", "description": "mounted", "enabled": True}],
        )
        store, col = make(FakeMultica(data), self.db)
        res = col.collect()
        self.assertTrue(res["workspaces"][WS_A]["ok"], res)
        self.assertTrue(res["workspaces"][WS_B]["ok"], res)
        row = store.query("SELECT * FROM agents WHERE id='ag1'")[0]
        self.assertEqual(row["description"], "public blurb")
        self.assertEqual(row["instructions_sha256"], instructions_sha256(instructions))
        self.assertEqual(
            row["instructions_sha256"],
            hashlib.sha256(normalize_instructions(instructions).encode()).hexdigest(),
        )
        stored = row["instructions"]
        for needle in (FAKE_TOKEN, "sk-abcdefghijk", "ghp_" + "b" * 36, jwt, secret_hex):
            self.assertNotIn(needle, stored)
        self.assertIn("[redacted]", stored)
        self.assertGreater(len(stored), 200)
        detail = json.loads(row["skills_detail_json"])
        self.assertEqual(
            [(item["id"], item["name"], item["enabled"]) for item in detail],
            [("sk1", "mem-distill", True), ("sk2", "off", False)],
        )
        self.assertNotIn(FAKE_TOKEN, json.dumps(detail))
        self.assertEqual(
            json.loads(row["disabled_runtime_skills_json"]),
            ["runtime-skill", {"id": "rs", "name": "other"}],
        )
        columns = {item[1] for item in store._conn.execute("PRAGMA table_info(agents)")}
        for forbidden in ("custom_env", "mcp_config", "runtime_config", "launch_header"):
            self.assertNotIn(forbidden, columns)
        cloud = store.query("SELECT * FROM workspace_skills WHERE id='cloud1'")[0]
        self.assertEqual(cloud["name"], "mem-distill")
        self.assertNotIn(FAKE_TOKEN, cloud["description"])
        self.assertEqual(json.loads(cloud["labels_json"]), ["ops", "beta"])
        body = Views(store, SERVER, app_url="https://app.test", collect_interval_s=60).agents()
        by_id = {item["id"]: item for item in body["items"]}
        self.assertEqual(by_id["ag1"]["instructions_sha256"], row["instructions_sha256"])
        self.assertEqual(by_id["ag1"]["skills"][0]["name"], "mem-distill")
        self.assertEqual(by_id["bg1"]["instructions"], "beta instructions")
        self.assertEqual(by_id["bg1"]["workspace_id"], WS_B)
        installs = build_workspace_installs(
            store.query("SELECT * FROM workspace_skills"),
            store.query("SELECT workspace_id, id, name, skills_json, skills_detail_json FROM agents"),
            store.query("SELECT id, name, slug FROM workspaces"),
        )["mem-distill"]
        self.assertEqual([item["id"] for item in installs], ["cloud1", "cloud2"])
        self.assertEqual(installs[1]["agents"][0]["id"], "bg1")
        document = Views(store, SERVER, app_url="https://app.test", collect_interval_s=60).skills()
        self.assertIn("items", document)
        self.assertTrue(all("workspace_installs" in item for item in document["items"] if item.get("origin") != "grok_bot"))
        named = [item for item in document["items"] if item.get("name") == "mem-distill"]
        if named:
            self.assertEqual([row["id"] for row in named[0]["workspace_installs"]], ["cloud1", "cloud2"])
        store.close()
        for path in self.db.parent.iterdir():
            blob = path.read_bytes()
            for needle in (FAKE_TOKEN.encode(), secret_hex.encode(), jwt.encode()):
                self.assertNotIn(needle, blob, path.name)

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
        data = fixture()
        data[WS_B]["skills"] = [{
            "id": "s1", "workspace_id": WS_B, "name": "kept", "description": "d",
            "labels": [], "updated_at": "2026-10-01T00:00:00Z",
        }]
        fake = FakeMultica(data)
        store, col = make(fake, self.db)
        col.collect()
        self.assertEqual(self.count(store, "workspace_skills", WS_B), 1)
        fake.fail[WS_B] = "403 forbidden: not a member"
        col.collect()
        self.assertEqual(self.count(store, "issues", WS_B), 0)
        self.assertEqual(self.count(store, "agents", WS_B), 0)
        self.assertEqual(self.count(store, "workspace_skills", WS_B), 0)
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

    def test_property_catalog_cached_without_descriptions(self):
        data = {
            WS_A: {
                "meta": {"id": WS_A, "name": "Alpha", "slug": "alpha"},
                "issues": [issue(WS_A, 1, properties={"p": "o"})],
                "properties": [{
                    "id": "p", "name": "主层", "type": "select", "archived": False,
                    "description": f"secret body {FAKE_TOKEN}",
                    "config": {"options": [{"id": "o", "name": "L3 能力", "color": "#0f8f8a"}]},
                }],
                "agents": [], "runtimes": [], "tasks": {},
            }
        }
        store, col = make(FakeMultica(data), self.db)
        self.assertTrue(col.collect()["ok"])
        row = store.query("SELECT name, type, options_json FROM property_defs WHERE id='p'")[0]
        self.assertEqual((row["name"], row["type"]), ("主层", "select"))
        self.assertEqual(json.loads(row["options_json"]), [{"id": "o", "name": "L3 能力"}])
        cached = json.loads(store.query("SELECT properties_json FROM issues WHERE identifier='T-1'")[0]["properties_json"])
        self.assertEqual(cached, {"p": "o"})
        store.close()
        self.assertNotIn(FAKE_TOKEN.encode(), self.db.read_bytes())


class CollectorHealthTests(unittest.TestCase):
    """/api/meta must explain an empty or silently stale board when the CLI itself is unusable."""

    def setUp(self):
        self.store = Store(":memory:")
        self.views = Views(self.store, SERVER, app_url="https://app.test", collect_interval_s=60)

    def tearDown(self):
        self.store.close()

    def collect(self, runner, **opt):
        return Collector(runner, self.store, SERVER, CollectOptions(all_workspaces=not opt, **opt)).collect()

    def test_never_attempted(self):
        self.assertEqual(self.views.meta()["collector"], {"last_attempt_at": None, "last_success_at": None, "error": None})

    def test_cli_binary_missing(self):
        res = self.collect(CliRunner(profile=SERVER, binary="/Users/someone/no-such-dir/multica", retries=0))
        self.assertFalse(res["ok"])
        meta = self.views.meta()
        self.assertEqual(meta["workspaces"], [])
        self.assertIsNone(meta["generated_at"])
        err = meta["collector"]["error"]
        self.assertEqual(err["message"], "multica CLI not found: ~/no-such-dir/multica")  # home path redacted
        self.assertEqual(err["kind"], "process")
        self.assertIsNotNone(meta["collector"]["last_attempt_at"])
        self.assertIsNone(meta["collector"]["last_success_at"])

    def test_not_logged_in_is_auth(self):
        def unconfigured(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "", f"No server configured. Run 'multica setup' first. token={FAKE_TOKEN}")

        self.collect(CliRunner(profile=SERVER, _exec=unconfigured, retries=0))
        err = self.views.meta()["collector"]["error"]
        self.assertEqual(err["kind"], "auth")
        self.assertNotIn(FAKE_TOKEN, err["message"])

    def test_failure_after_success_keeps_last_success(self):
        fake = FakeMultica(fixture())
        runner = CliRunner(profile=SERVER, _exec=fake, retries=0)
        self.collect(runner)
        ok = self.views.meta()["collector"]
        self.assertIsNone(ok["error"])
        self.assertIsNotNone(ok["last_success_at"])
        fake.workspaces = None  # next `workspace list` raises inside the fake -> non-CliError path
        self.collect(runner)
        after = self.views.meta()["collector"]
        self.assertEqual(after["last_success_at"], ok["last_success_at"])
        self.assertEqual(after["error"]["kind"], "unknown")
        self.assertEqual(len(self.views.meta()["workspaces"]), 2)  # cached workspaces still listed

    def test_allow_list_entry_not_visible(self):
        runner = CliRunner(profile=SERVER, _exec=FakeMultica(fixture()), retries=0)
        res = self.collect(runner, workspaces=["beta", "gamma"])
        self.assertIn(WS_B, res["workspaces"])  # visible entries still collected
        err = self.views.meta()["collector"]["error"]
        self.assertEqual(err["kind"], "auth")
        self.assertIn("gamma", err["message"])

    def test_default_dir_tightened_only(self):
        import multica_board.__main__ as main

        with tempfile.TemporaryDirectory() as tmp:
            default_dir, other_dir = Path(tmp) / "board", Path(tmp) / "other"
            for d in (default_dir, other_dir):
                d.mkdir()
                os.chmod(d, 0o755)
            with mock.patch.object(main, "DEFAULT_DB", default_dir / "board.sqlite"):
                main._open_store(default_dir / "board.sqlite").close()
                main._open_store(other_dir / "x.sqlite").close()
            self.assertEqual(default_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(other_dir.stat().st_mode & 0o777, 0o755)


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
        for body in (self.v.meta(), self.v.issues(), self.v.layers(), self.v.agents(), self.v.runtimes(), self.v.attention(), self.v.changes(None), self.v.failure_stats()):
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
                    "latest_run", "last_failed_run", "last_delivery_anomaly", "layer"}
        self.assertEqual(set(child), expected)
        self.assertEqual(child["layer"]["primary"], None)
        self.assertEqual(child["layer"]["source"], {"primary": None, "involved": None, "governance": None})

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
        self.assertIsNone(ag["description"])
        self.assertIsNone(ag["instructions"])
        self.assertIsNone(ag["instructions_sha256"])
        self.assertEqual(ag["skills"], [])
        self.assertEqual(ag["disabled_runtime_skills"], [])

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

    def test_failure_stats_from_seed(self):
        body = self.v.failure_stats()
        self.assertEqual(body["windows"], {"h24": 24, "d7": 168})
        # r1 daemon_restart (5h), r3 quota (2h), r6 network (30h); r7 timeout is older than 7d.
        reasons = [
            {"failure_reason": "daemon_restart", "h24": 1, "d7": 1},
            {"failure_reason": "quota", "h24": 1, "d7": 1},
            {"failure_reason": "network", "h24": 0, "d7": 1},
        ]
        failed = {"h24": 2, "d7": 3}
        self.assertEqual(body["by_provider"], [{"provider": "cursor", "failed": failed, "top_reasons": reasons}])
        self.assertEqual(body["by_runtime"], [{
            "key": "w1:rt", "workspace_id": "w1", "id": "rt", "name": "Cursor (box)", "provider": "cursor",
            "failed": failed, "top_reasons": reasons,
        }])
        self.assertEqual(body["by_agent"], [{
            "key": "w1:ag", "workspace_id": "w1", "id": "ag", "name": "Builder", "runtime_id": "rt",
            "failed": failed, "top_reasons": reasons,
        }])
        blob = json.dumps(body)
        for secret in ("usage limit", "daemon restarted", "conn refused", "error_summary"):
            self.assertNotIn(secret, blob)


class FailureStatsTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.v = Views(self.store, SERVER, app_url="https://app.test", collect_interval_s=60)
        s = SERVER
        with self.store.tx() as c:
            self.store.upsert(c, "workspaces", {"server": s, "id": "w1", "name": "One", "slug": "one"})
            self.store.upsert(c, "workspaces", {"server": s, "id": "w2", "name": "Two", "slug": "two"})
            self.store.upsert(c, "runtimes", {"server": s, "workspace_id": "w1", "id": "rt-old", "name": "Cursor old",
                                              "provider": "cursor"})
            self.store.upsert(c, "runtimes", {"server": s, "workspace_id": "w1", "id": "rt-new", "name": "Codex",
                                              "custom_name": "Codex box", "provider": "codex"})
            self.store.upsert(c, "runtimes", {"server": s, "workspace_id": "w2", "id": "rt-b", "name": "Cursor b",
                                              "provider": "cursor"})
            self.store.upsert(c, "agents", {"server": s, "workspace_id": "w1", "id": "ag", "name": "Builder",
                                            "runtime_id": "rt-new"})
            self.store.upsert(c, "agents", {"server": s, "workspace_id": "w2", "id": "ag", "name": "Builder",
                                            "runtime_id": "rt-b"})

            def run(ws, tid, rid, status, hours, kind):
                self.store.upsert(c, "tasks", {
                    "server": s, "workspace_id": ws, "id": tid, "agent_id": "ag", "runtime_id": rid,
                    "status": status, "created_at": ago(hours), "completed_at": ago(hours), "error_kind": kind,
                    "error_summary": f"secret {FAKE_TOKEN} {kind}",
                })

            # cursor / w1: quota x3 in 24h, rate_limit x2 (one inside 24h), timeout and network once each in 7d.
            # timeout and network tie, so network wins the third top_reasons slot.
            for i, hours in enumerate((1, 2, 3)):
                run("w1", f"q{i}", "rt-old", "failed", hours, "quota")
            run("w1", "rl0", "rt-old", "failed", 4, "rate_limit")
            run("w1", "rl1", "rt-old", "failed", 30, "rate_limit")
            run("w1", "to", "rt-old", "failed", 40, "timeout")  # ties network, sorts later, dropped from top_reasons
            run("w1", "net", "rt-old", "failed", 50, "network")
            run("w2", "qb", "rt-b", "failed", 5, "quota")  # same provider, other workspace
            run("w1", "cx", "rt-new", "failed", 6, "process")
            run("w1", "ghost", "missing", "failed", 26, None)  # unknown runtime, unclassified -> unknown
            run("w1", "old", "rt-old", "failed", 24 * 8, "auth")  # outside 7d
            run("w1", "ok", "rt-old", "completed", 1, "quota")
            run("w1", "cancel", "rt-old", "cancelled", 1, "cancelled")
        self.store.set_meta("last_collect", {"finished_at": "2026-10-14T09:00:00Z"})

    def tearDown(self):
        self.store.close()

    def test_groups_windows_and_top3(self):
        body = self.v.failure_stats()
        self.assertEqual(body["generated_at"], "2026-10-14T09:00:00Z")
        providers = {row["provider"]: row for row in body["by_provider"]}
        # w1 cursor: quota 3, rate_limit 2, timeout 1, network 1 = 7; plus w2 quota 1 = 8. h24: 3+1 + w2 1 = 5.
        cursor = providers["cursor"]
        self.assertEqual(cursor["failed"], {"h24": 5, "d7": 8})
        self.assertEqual([r["failure_reason"] for r in cursor["top_reasons"]], ["quota", "rate_limit", "network"])
        self.assertEqual(cursor["top_reasons"][0], {"failure_reason": "quota", "h24": 4, "d7": 4})
        self.assertEqual(sum(r["d7"] for r in cursor["top_reasons"]), 7)  # timeout ties network and sorts later
        self.assertEqual(providers["codex"]["failed"], {"h24": 1, "d7": 1})
        self.assertEqual(providers["codex"]["top_reasons"], [{"failure_reason": "process", "h24": 1, "d7": 1}])
        self.assertEqual(providers[None]["failed"], {"h24": 0, "d7": 1})
        self.assertEqual(providers[None]["top_reasons"], [{"failure_reason": "unknown", "h24": 0, "d7": 1}])
        self.assertEqual([row["provider"] for row in body["by_provider"]], ["cursor", "codex", None])

        runtimes = {(row["workspace_id"], row["id"]): row for row in body["by_runtime"]}
        self.assertEqual(runtimes[("w1", "rt-old")]["failed"], {"h24": 4, "d7": 7})
        self.assertEqual(runtimes[("w1", "rt-old")]["name"], "Cursor old")
        self.assertEqual(runtimes[("w2", "rt-b")]["failed"], {"h24": 1, "d7": 1})
        self.assertEqual(runtimes[("w1", "rt-new")]["name"], "Codex box")
        ghost = runtimes[("w1", "missing")]
        self.assertEqual((ghost["name"], ghost["provider"], ghost["failed"]), (None, None, {"h24": 0, "d7": 1}))

        agents = {(row["workspace_id"], row["id"]): row for row in body["by_agent"]}
        # w1 agent: cursor failures 7 + codex 1 + unknown 1 = 9 in 7d; h24 is cursor 4 + codex 1 = 5.
        # runtime_id is the current binding, not the runtime each run used.
        self.assertEqual(agents[("w1", "ag")]["runtime_id"], "rt-new")
        self.assertEqual(agents[("w1", "ag")]["name"], "Builder")
        self.assertEqual(agents[("w1", "ag")]["failed"], {"h24": 5, "d7": 9})
        self.assertEqual(agents[("w2", "ag")]["key"], "w2:ag")
        self.assertNotEqual(agents[("w1", "ag")]["key"], agents[("w2", "ag")]["key"])
        blob = json.dumps(body)
        self.assertNotIn(FAKE_TOKEN, blob)
        self.assertNotIn("error_summary", blob)
        self.assertNotIn("secret", blob)


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

    def test_bind_requires_numeric_ip(self):
        for host in ("localhost", "example.com", "127.0.0.1:8787", "*"):
            with self.assertRaises(ValueError, msg=host):
                listen_hosts([host])

    def test_endpoints(self):
        for path in ("/api/meta", "/api/issues", "/api/layers", "/api/agents", "/api/bots", "/api/runtimes", "/api/issues/w1/member/runs", "/api/attention", "/api/changes", "/api/stats/failures", "/api/run-metrics"):
            r, body = self.req("GET", path)
            self.assertEqual(r.status, 200, path)
            self.assertEqual(r.getheader("Content-Type"), "application/json; charset=utf-8")
            self.assertEqual(r.getheader("Cache-Control"), "no-store")
            self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
            self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))
            json.loads(body)

    def test_removed_and_unknown_endpoints(self):
        for path in ("/api/board", "/api/workspaces", "/api/runs", "/api/health", "/api/stats",
                     "/api/issues/w1/nope/runs"):
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

    def test_cross_site_page_navigation(self):
        headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}
        # Fragments are browser-local; /#... sends the same / request.
        for url in ("/", "/#atlas"):
            for method in ("GET", "HEAD"):
                for dest in (None, "document"):
                    with self.subTest(url=url, method=method, dest=dest):
                        request_headers = dict(headers)
                        if dest is not None:
                            request_headers["Sec-Fetch-Dest"] = dest
                        r, body = self.req(method, urlsplit(url).path, request_headers)
                        self.assertEqual(r.status, 200)
                        self.assertEqual(body, b"<h1>board</h1>" if method == "GET" else b"")
                        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))

    def test_cross_site_navigation_keeps_guards(self):
        headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                   "Sec-Fetch-Dest": "document"}
        for method in ("GET", "HEAD"):
            for path in ("/api", "/api/meta", "/api/mcp", "/api/meta?x=1"):
                with self.subTest(method=method, path=path):
                    r, _ = self.req(method, path, headers)
                    self.assertEqual(r.status, 403)
                    self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))
        for overrides in ({"Sec-Fetch-Mode": "no-cors"},
                          {"Sec-Fetch-Mode": ""},
                          {"Sec-Fetch-Dest": ""},
                          {"Sec-Fetch-Dest": "iframe"},
                          {"Sec-Fetch-Dest": "script"}):
            r, _ = self.req("GET", "/app.js", {**headers, **overrides})
            self.assertEqual(r.status, 403)
        r, body = self.req("GET", "/", {**headers, "Origin": "https://attacker.test"})
        self.assertEqual(r.status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "cross_origin")
        r, body = self.req("GET", "/", {**headers, "Host": "attacker.test"})
        self.assertEqual(r.status, 421)
        self.assertEqual(json.loads(body)["error"]["code"], "bad_host")

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

    def test_changes_http(self):
        r, body = self.req("GET", "/api/changes")
        self.assertEqual(r.status, 200)
        data = json.loads(body)
        self.assertEqual(data["cursor"], "v1:0")
        self.assertEqual(data["items"], [])
        self.assertEqual(data["retained_days"], 90)
        self.assertFalse(data["reset"])
        self.assertFalse(data["has_more"])
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))

        r, body = self.req("GET", "/api/changes?since=yesterday")
        self.assertEqual(r.status, 400)
        err = json.loads(body)
        self.assertEqual(err["error"]["code"], "bad_request")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))

        r, body = self.req("GET", "/api/changes?since=2000-01-01T00:00:00Z")
        self.assertEqual(r.status, 200)
        self.assertTrue(json.loads(body)["reset"])
        self.assertEqual(json.loads(body)["items"], [])

        r, body = self.req("GET", "/api/changes?since=a&since=b")
        self.assertEqual(r.status, 400)
        r, body = self.req("GET", "/api/changes?kind=status")
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(body)["items"], [])
        r, body = self.req("GET", "/api/changes?kind=nope")
        self.assertEqual(r.status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "bad_request")
        r, body = self.req("GET", "/api/changes?kind=status&kind=run")
        self.assertEqual(r.status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "bad_request")


    def test_attention_http(self):
        r, body = self.req("GET", "/api/attention")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))
        self.assertNotIn(b": ", body)
        data = json.loads(body)
        self.assertEqual([i["identifier"] for i in data["workspaces"][0]["failed_runs"]], ["ORPHAN", "CHILD"])

        r, body = self.req("GET", "/api/attention?format=text")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Type"), "text/plain; charset=utf-8")
        self.assertEqual(r.getheader("Content-Security-Policy"), "default-src 'none'; frame-ancestors 'none'")
        text = body.decode()
        self.assertLessEqual(len(body), ATTENTION_TEXT_BYTES)
        self.assertIn("failed_runs 2", text)
        self.assertIn("ORPHAN", text)
        self.assertTrue(text.endswith("\n"))
        self.assertNotIn("daemon restarted", text)

        r, body = self.req("GET", "/api/attention/?format=TEXT")
        self.assertEqual(r.status, 200)
        self.assertTrue(r.getheader("Content-Type").startswith("text/plain"))

        r, body = self.req("GET", "/api/attention?format=json")
        self.assertEqual(json.loads(body)["window_h"], 24)

        r, body = self.req("HEAD", "/api/attention?format=text")
        self.assertEqual(r.status, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(r.getheader("Content-Length")), 0)

        for path in ("/api/attention?format=html", "/api/attention?format=text&format=json", "/api/attention?format="):
            r, body = self.req("GET", path)
            self.assertEqual(r.status, 400, path)
            self.assertEqual(json.loads(body)["error"]["code"], "bad_request")

        r, _ = self.req("GET", "/api/attention?format=text", {"Host": "attacker.test"})
        self.assertEqual(r.status, 421)
        r, body = self.req("POST", "/api/attention")
        self.assertEqual(r.status, 405)


AT = "2026-10-15T00:00:00Z"


def _issue_row(iid, status="todo", assignee=None):
    return {
        "id": iid, "identifier": iid.upper(), "status": status, "status_category": status,
        "assignee_type": assignee[0] if assignee else None, "assignee_id": assignee[1] if assignee else None,
        "title": "ignored",
    }


class ChangeDiffTests(unittest.TestCase):
    def test_issues_only_status_and_assignee_of_known_rows(self):
        old = [_issue_row("a", assignee=("agent", "ag")), _issue_row("b")]
        new = [
            _issue_row("a", status="in_review", assignee=("member", "u1")),
            {**_issue_row("b"), "title": "renamed", "priority": "high"},
            _issue_row("c"),
        ]
        kinds = [(e["kind"], e["resource_id"]) for e in diff_issues(old, new, AT)]
        self.assertEqual(kinds, [("issue_status", "a"), ("issue_assignee", "a")])
        status = diff_issues(old, new, AT)[0]["payload"]
        self.assertEqual(status["previous_status"], "todo")
        self.assertEqual(status["status"], "in_review")

    def test_runtime_online_boundary(self):
        old = [
            {"id": "on", "status": "online", "name": "Box", "custom_name": None},
            {"id": "off", "status": "offline", "name": "Off", "custom_name": "Custom"},
        ]
        new = [
            {"id": "on", "status": "connecting", "name": "Box", "custom_name": None},
            {"id": "off", "status": "offline", "name": "Off", "custom_name": "Custom"},
            {"id": "new-on", "status": "online", "name": "N", "custom_name": None},
            {"id": "new-off", "status": "offline", "name": "X", "custom_name": None},
        ]
        ev = {(e["resource_id"], e["payload"]["previous_status"], e["payload"]["status"]) for e in diff_runtimes(old, new, AT)}
        self.assertEqual(ev, {("on", "online", "connecting"), ("new-on", None, "online")})
        gone = diff_runtimes(old, [], AT)
        self.assertEqual(
            [(e["resource_id"], e["payload"]["status"], e["payload"]["name"]) for e in gone],
            [("on", None, "Box")],
        )

    def test_task_events(self):
        row = {
            "id": "t", "status": "failed", "issue_id": "i", "agent_id": "a", "runtime_id": "r",
            "started_at": AT, "completed_at": AT, "error_kind": "process", "error_summary": "process exited",
        }
        self.assertEqual([e["kind"] for e in events_for_tasks([row], {}, AT)], ["run", "run_failed"])
        self.assertEqual(events_for_tasks([row], {"t": "failed"}, AT), [])
        running = {**row, "status": "running", "error_kind": None, "error_summary": None, "completed_at": None}
        self.assertEqual([e["kind"] for e in events_for_tasks([running], {}, AT)], ["run"])
        self.assertEqual([e["kind"] for e in events_for_tasks([row], {"t": "running"}, AT)], ["run_failed"])


def _small_board() -> dict:
    live = task(WS_A, "run-live", "ag1", ago(1), status="running", issue_id=f"{WS_A}-issue-0002", completed_at=None)
    old = task(WS_A, "run-old", "ag1", ago(2), status="completed", issue_id=f"{WS_A}-issue-0002")
    return {
        WS_A: {
            "meta": {"id": WS_A, "name": "Alpha", "slug": "alpha"},
            "issues": [
                issue(WS_A, 1, status="todo", status_category="todo", assignee_type="agent", assignee_id="ag1"),
                issue(WS_A, 2, status="in_progress", status_category="in_progress"),
            ],
            "agents": [agent(WS_A, "ag1", name="Builder")],
            "runtimes": [runtime(WS_A)],
            "members": [{"user_id": "u1", "name": "Yi", "role": "owner", "workspace_id": WS_A}],
            "tasks": {"ag1": [live, old]},
        }
    }


def _nchanges(store: Store) -> int:
    return store.query("SELECT COUNT(*) AS n FROM changes")[0]["n"]


class ChangeFeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "board.sqlite"
        self.data = _small_board()
        self.fake = FakeMultica(self.data)
        self.store, self.col = make(self.fake, self.db)
        self.views = Views(self.store, SERVER, app_url="https://app.test", collect_interval_s=60)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_baseline_then_deltas(self):
        self.assertTrue(self.col.collect()["ok"])
        self.assertEqual(_nchanges(self.store), 0)
        first = self.views.changes("v1:0")
        self.assertEqual(first["items"], [])
        self.assertFalse(first["reset"])
        self.assertEqual(first["cursor"], "v1:0")
        self.assertTrue(self.store.has_baseline(SERVER, WS_A, "issues"))
        self.assertTrue(self.store.has_baseline(SERVER, WS_A, "runtimes"))
        self.assertTrue(self.store.has_baseline(SERVER, WS_A, "tasks:ag1"))

        issue1 = self.data[WS_A]["issues"][0]
        issue1["status"] = "in_review"
        issue1["status_category"] = "in_review"
        issue1["assignee_type"] = "member"
        issue1["assignee_id"] = "u1"
        self.data[WS_A]["issues"][1]["title"] = "renamed"
        self.data[WS_A]["issues"][1]["priority"] = "high"
        self.data[WS_A]["issues"].append(issue(WS_A, 3))
        self.data[WS_A]["runtimes"][0]["status"] = "offline"
        self.data[WS_A]["runtimes"].append(runtime(WS_A, "rt2", name="Extra"))
        failed = task(
            WS_A, "run-live", "ag1", ago(1), status="failed", issue_id=f"{WS_A}-issue-0002",
            error=f"process exited with code 1; Authorization: Bearer {FAKE_TOKEN}",
        )
        new_run = task(WS_A, "run-new", "ag1", ago(0.05), status="running", issue_id=f"{WS_A}-issue-0001", completed_at=None)
        old = task(WS_A, "run-old", "ag1", ago(2), status="completed", issue_id=f"{WS_A}-issue-0002")
        self.data[WS_A]["tasks"]["ag1"] = [new_run, failed, old]

        self.assertTrue(self.col.collect()["ok"])
        self.assertEqual(_nchanges(self.store), 6)
        full = self.views.changes("v1:0")
        self.assertFalse(full["reset"])
        self.assertFalse(full["has_more"])
        self.assertEqual(full["retained_days"], 90)
        self.assertEqual(full["generated_at"][:4], "2026")
        blob = json.dumps(full)
        self.assertNotIn(FAKE_TOKEN, blob)
        self.assertNotIn("secret body", blob)
        self.assertNotIn(FAKE_TOKEN, json.dumps(self.store.query("SELECT payload_json FROM changes")))

        by_key = {}
        for item in full["items"]:
            by_key.setdefault((item["kind"], item.get("id") or item["task_id"]), item)
        self.assertEqual(set(by_key), {
            ("issue_status", f"{WS_A}-issue-0001"),
            ("issue_assignee", f"{WS_A}-issue-0001"),
            ("runtime", "rt1"),
            ("runtime", "rt2"),
            ("run", "run-new"),
            ("run_failed", "run-live"),
        })
        st = by_key[("issue_status", f"{WS_A}-issue-0001")]
        self.assertEqual(st["identifier"], "T-1")
        self.assertEqual(st["status"], "in_review")
        self.assertEqual(st["previous_status"], "todo")
        self.assertEqual(st["status_category"], "in_review")
        self.assertEqual(st["previous_status_category"], "todo")
        self.assertEqual(st["key"], f"{WS_A}:{WS_A}-issue-0001")
        ag = by_key[("issue_assignee", f"{WS_A}-issue-0001")]
        self.assertEqual(ag["assignee"], {"type": "member", "id": "u1", "name": "Yi"})
        self.assertEqual(ag["previous_assignee"], {"type": "agent", "id": "ag1", "name": "Builder"})
        self.assertEqual(by_key[("runtime", "rt1")]["status"], "offline")
        self.assertEqual(by_key[("runtime", "rt1")]["previous_status"], "online")
        self.assertEqual(by_key[("runtime", "rt1")]["name"], "Codex (box)")
        self.assertEqual(by_key[("runtime", "rt2")]["previous_status"], None)
        self.assertEqual(by_key[("runtime", "rt2")]["name"], "Extra")
        run = by_key[("run", "run-new")]
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["identifier"], "T-1")
        self.assertEqual(run["agent_name"], "Builder")
        self.assertIsNone(run["error_kind"])
        self.assertNotIn("agent_id", run)
        failed_ev = by_key[("run_failed", "run-live")]
        self.assertEqual(failed_ev["error_kind"], "process")
        self.assertIn("[redacted]", failed_ev["error_summary"])
        self.assertIn("process exited", failed_ev["error_summary"])
        self.assertEqual(failed_ev["identifier"], "T-2")

        hand = self.views.changes(None)
        self.assertEqual(hand["items"], [])
        self.assertEqual(hand["cursor"], full["cursor"])
        self.assertEqual(self.views.changes(hand["cursor"])["items"], [])

        drained = []
        since = "v1:0"
        pages = 0
        while True:
            page = self.views.changes(since, limit=2)
            self.assertFalse(page["reset"])
            drained.extend(page["items"])
            pages += 1
            if not page["has_more"]:
                self.assertEqual(page["cursor"], full["cursor"])
                break
            since = page["cursor"]
            self.assertLess(pages, 10)
        self.assertEqual(drained, full["items"])
        self.assertGreater(pages, 1)

        latest = max(i["at"] for i in full["items"])
        self.assertEqual(self.views.changes(latest)["items"], [])
        self.assertFalse(self.views.changes(ago(1))["reset"])
        self.assertEqual(len(self.views.changes(ago(1))["items"]), 6)
        self.assertTrue(self.views.changes("2000-01-01T00:00:00Z")["reset"])
        self.assertEqual(self.views.changes("2999-01-01T00:00:00Z")["items"], [])
        with self.assertRaises(BadRequest):
            self.views.changes("yesterday")
        with self.assertRaises(BadRequest):
            self.views.changes("2026-10-15T00:00:00")

        self.col.collect()
        self.assertEqual(_nchanges(self.store), 6)

    def test_kind_status_skips_runs(self):
        self.assertTrue(self.col.collect()["ok"])
        issue1 = self.data[WS_A]["issues"][0]
        issue1["status"] = "in_review"
        issue1["status_category"] = "in_review"
        self.assertTrue(self.col.collect()["ok"])
        stamp = ago(0.01)
        self.store.append_changes(SERVER, WS_A, [
            {
                "at": stamp, "kind": "run", "resource_id": f"run-pad-{n}",
                "payload": {
                    "issue_id": f"{WS_A}-issue-0001", "agent_id": "ag1", "runtime_id": "rt1",
                    "status": "running", "started_at": stamp, "completed_at": None,
                    "error_kind": None, "error_summary": None,
                },
            }
            for n in range(3)
        ] + [{
            "at": stamp, "kind": "issue_status", "resource_id": f"{WS_A}-issue-0002",
            "payload": {
                "identifier": "T-2", "status": "blocked", "status_category": "blocked",
                "previous_status": "in_progress", "previous_status_category": "in_progress",
            },
        }])
        unfiltered = self.views.changes("v1:0", limit=1)
        self.assertEqual(unfiltered["items"][0]["kind"], "issue_status")
        self.assertTrue(unfiltered["has_more"])
        first_status = self.views.changes("v1:0", "status", limit=1)
        self.assertEqual([i["id"] for i in first_status["items"]], [f"{WS_A}-issue-0001"])
        self.assertEqual(first_status["items"][0]["previous_status"], "todo")
        self.assertTrue(first_status["has_more"])
        second_status = self.views.changes(first_status["cursor"], "status", limit=1)
        self.assertEqual([i["status"] for i in second_status["items"]], ["blocked"])
        self.assertFalse(second_status["has_more"])
        self.assertEqual(
            self.views.changes("v1:0", "issue_status")["items"],
            self.views.changes("v1:0", "status")["items"],
        )
        self.assertGreater(len(self.views.changes("v1:0")["items"]), len(self.views.changes("v1:0", "status")["items"]))
        with self.assertRaises(BadRequest):
            self.views.changes("v1:0", "nope")
        self.assertEqual(self.views.changes(None, "status")["items"], [])
        names = {row["name"] for row in self.store.query("PRAGMA index_list(changes)")}
        self.assertIn("changes_server_kind_seq", names)
        self.assertIn("changes_server_kind_at", names)

    def test_purge_rebaseline_does_not_replay(self):
        self.col.collect()
        self.data[WS_A]["runtimes"][0]["status"] = "offline"
        self.col.collect()
        self.assertEqual(_nchanges(self.store), 1)
        self.fake.fail[WS_A] = "403 forbidden: not a member"
        self.col.collect()
        self.assertEqual(_nchanges(self.store), 0)
        self.assertFalse(self.store.has_baseline(SERVER, WS_A, "runtimes"))
        del self.fake.fail[WS_A]
        self.data[WS_A]["runtimes"][0]["status"] = "online"
        self.col.collect()
        self.assertEqual(_nchanges(self.store), 0)
        self.data[WS_A]["runtimes"][0]["status"] = "offline"
        self.col.collect()
        body = self.views.changes("v1:1")
        self.assertFalse(body["reset"])
        self.assertEqual([(i["kind"], i["id"], i["status"]) for i in body["items"]], [("runtime", "rt1", "offline")])
        self.assertTrue(self.views.changes("v1:0")["reset"])


class ChangeRetentionTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.views = Views(self.store, SERVER, app_url="https://app.test", collect_interval_s=60)

    def tearDown(self):
        self.store.close()

    def _ev(self, at: str, rid: str, name: str) -> dict:
        return {
            "at": at, "kind": "runtime", "resource_id": rid,
            "payload": {"name": name, "status": "offline", "previous_status": "online"},
        }

    def test_prune_gap_and_server_scope(self):
        self.store.append_changes(SERVER, WS_A, [self._ev(ago(91 * 24), "old", "Old")])
        self.store.append_changes(SERVER, WS_A, [self._ev(ago(1), "fresh", "Fresh")])
        self.store.append_changes("other", WS_A, [self._ev(ago(30 * 24), "foreign", "FOREIGN-MARKER")])

        recent = self.views.changes(ago(2))
        self.assertFalse(recent["reset"])
        self.assertEqual([i["id"] for i in recent["items"]], ["fresh"])
        self.assertNotIn("FOREIGN-MARKER", json.dumps(recent))
        self.assertEqual(self.store.query("SELECT COUNT(*) AS n FROM changes WHERE resource_id='old'")[0]["n"], 0)
        self.assertEqual(self.store.query("SELECT COUNT(*) AS n FROM changes WHERE server='other'")[0]["n"], 1)

        self.assertTrue(self.views.changes("v1:0")["reset"])
        self.assertEqual(self.views.changes("v1:0")["items"], [])
        kept = self.views.changes("v1:1")
        self.assertFalse(kept["reset"])
        self.assertEqual([i["id"] for i in kept["items"]], ["fresh"])
        self.assertTrue(self.views.changes("v1:999999")["reset"])
        hand = self.views.changes(None)
        self.assertEqual(hand["items"], [])
        self.assertEqual(self.views.changes(hand["cursor"])["items"], [])


class AttentionTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.v = Views(self.store, SERVER, app_url="https://app.test", collect_interval_s=60)
        s = SERVER
        long_title = "测" * 81
        with self.store.tx() as c:
            def ws(i, name, slug):
                self.store.upsert(c, "workspaces", {"server": s, "id": i, "name": name, "slug": slug})

            def issue(ws_id, i, **kw):
                base = {"server": s, "workspace_id": ws_id, "id": i, "identifier": i, "title": i,
                        "status": "todo", "status_category": "todo", "created_at": "2026-10-01T00:00:00Z"}
                self.store.upsert(c, "issues", {**base, **kw})

            ws("wa", "Alpha", "alpha")
            ws("wb", "Beta", "beta")
            ws("wz", "Zed", "zed")
            issue("wa", "ACM-1", title=long_title, status="in_review", status_category="in_review",
                  assignee_type="agent", assignee_id="ag1", last_activity_at="2026-10-14T09:00:00Z")
            issue("wa", "ACM-2", title="hello\nworld\t!", status="in_review", status_category="in_review",
                  assignee_type="member", assignee_id="u1", last_activity_at="2026-10-14T07:00:00Z")
            issue("wa", "ACM-3", title="waiting review", status="waiting", status_category="in_review",
                  assignee_type="agent", assignee_id="missing", last_activity_at="2026-10-14T10:00:00Z")
            issue("wa", "ACM-4", title="status wins", status="in_review", status_category="blocked",
                  assignee_type="squad", assignee_id="sq", last_activity_at="2026-10-14T08:00:00Z")
            issue("wa", "ACM-5", title="no time", status="in_review", status_category="in_review",
                  last_activity_at=None, updated_at=None, created_at=None)
            issue("wa", "ACM-8", title="blocked two", status="blocked", status_category="blocked",
                  last_activity_at="2026-10-14T06:00:00Z")
            issue("wa", "ACM-9", title="blocked one", status="todo", status_category="blocked",
                  last_activity_at="2026-10-14T06:00:00Z")
            issue("wa", "ACM-OPEN", title="still open", status="in_progress", status_category="in_progress")
            issue("wa", "ACM-DONE", title="closed", status="done", status_category="done")
            issue("wb", "ACM-B", title="beta todo", status="todo", status_category="todo")
            issue("wz", "ACM-7", title="zed blocked", status="blocked", status_category="blocked",
                  assignee_type="agent", assignee_id="agz", last_activity_at="2026-10-14T01:00:00Z")
            self.store.upsert(c, "agents", {"server": s, "workspace_id": "wa", "id": "ag1", "name": "Builder",
                                            "status": "working", "runtime_id": "rt-off", "runtime_bound": 1})
            self.store.upsert(c, "agents", {"server": s, "workspace_id": "wa", "id": "agz2", "name": "Zed Agent",
                                            "status": "idle", "runtime_id": "rt-off", "runtime_bound": 0})
            self.store.upsert(c, "agents", {"server": s, "workspace_id": "wa", "id": "ada", "name": "Ada",
                                            "status": "idle", "runtime_id": "rt-off", "archived_at": "2026-01-01T00:00:00Z"})
            self.store.upsert(c, "agents", {"server": s, "workspace_id": "wa", "id": "only-old", "name": "Old",
                                            "status": "idle", "runtime_id": "rt-dead", "archived_at": "2026-01-01T00:00:00Z"})
            self.store.upsert(c, "agents", {"server": s, "workspace_id": "wa", "id": "live", "name": "Live",
                                            "status": "idle", "runtime_id": "rt-on"})
            self.store.upsert(c, "agents", {"server": s, "workspace_id": "wz", "id": "agz", "name": "Zed",
                                            "status": "idle", "runtime_id": None})
            for rid, status, custom in (
                ("rt-off", "offline", "Cursor box"),
                ("rt-dead", "Offline", None),
                ("rt-on", "online", None),
                ("rt-empty", "offline", "nobody"),
            ):
                self.store.upsert(c, "runtimes", {
                    "server": s, "workspace_id": "wa", "id": rid, "name": "plain-" + rid, "custom_name": custom,
                    "status": status, "last_seen_at": "2026-10-14T04:00:00Z", "device_info": "secret-device",
                })

            def run(tid, issue_id, status, hours, err=None):
                self.store.upsert(c, "tasks", {
                    "server": s, "workspace_id": "wa", "id": tid, "agent_id": "ag1", "issue_id": issue_id,
                    "status": status, "created_at": ago(hours), "completed_at": ago(hours),
                    "error_summary": err,
                })

            run("t-acm", "ACM-1", "failed", 3, "Bearer secret-token")
            run("t-old", "ACM-1", "failed", 30, "too old")
            run("t-ok", "ACM-1", "completed", 0.5, None)
            run("t-gone", "gone-issue", "failed", 2, None)
            self.store.upsert(c, "tasks", {
                "server": s, "workspace_id": "wa", "id": "t-none", "agent_id": None, "issue_id": None,
                "status": "FAILED", "created_at": ago(1), "completed_at": None, "error_summary": "no issue",
            })
        self.store.set_meta("last_collect", {"finished_at": "2026-10-14T09:00:00Z"})
        completed = self.store.query(
            "SELECT completed_at FROM tasks WHERE server=? AND id=?", (SERVER, "t-ok"),
        )[0]["completed_at"]
        self.store.append_changes(SERVER, "wa", [{
            "at": completed,
            "kind": "issue_status",
            "resource_id": "ACM-1",
            "payload": {"status": "in_review", "previous_status": "in_progress"},
        }])

    def tearDown(self):
        self.store.close()

    def test_groups_fields_and_order(self):
        body = self.v.attention()
        self.assertEqual([w["id"] for w in body["workspaces"]], ["wa", "wz"])
        alpha, zed = body["workspaces"]
        self.assertEqual(list(alpha), ["id", "slug", "name", "in_review", "blocked", "failed_runs", "offline_runtimes"])
        self.assertEqual([i["identifier"] for i in alpha["in_review"]], ["ACM-3", "ACM-1", "ACM-4", "ACM-2", "ACM-5"])
        acm1 = alpha["in_review"][1]
        self.assertEqual(acm1["title"], "测" * 80)
        self.assertEqual(len(acm1["title"]), 80)
        self.assertEqual(acm1["agent_name"], "Builder")
        self.assertEqual(alpha["in_review"][0]["status"], "waiting")
        self.assertIsNone(alpha["in_review"][0]["agent_name"])
        self.assertEqual(alpha["in_review"][2]["status"], "in_review")
        self.assertIsNone(alpha["in_review"][2]["agent_name"])
        self.assertEqual(alpha["in_review"][3]["title"], "hello world !")
        self.assertIsNone(alpha["in_review"][3]["agent_name"])
        self.assertIsNone(alpha["in_review"][4]["time"])
        self.assertEqual([i["identifier"] for i in alpha["blocked"]], ["ACM-8", "ACM-9"])
        self.assertEqual(alpha["blocked"][1]["status"], "todo")
        self.assertEqual([i["identifier"] for i in alpha["failed_runs"]], ["t-none", "gone-issue", "ACM-1"])
        self.assertIsNone(alpha["failed_runs"][0]["title"])
        self.assertIsNone(alpha["failed_runs"][0]["agent_name"])
        self.assertEqual(alpha["failed_runs"][0]["status"], "failed")
        self.assertIsNone(alpha["failed_runs"][1]["title"])
        self.assertEqual(alpha["failed_runs"][2]["title"], "测" * 80)
        self.assertEqual(alpha["failed_runs"][2]["agent_name"], "Builder")
        self.assertEqual(alpha["offline_runtimes"], [{
            "identifier": "rt-off",
            "title": "Cursor box",
            "status": "offline",
            "time": "2026-10-14T04:00:00Z",
            "agent_name": "Builder, Zed Agent",
        }])
        self.assertEqual(zed["blocked"][0]["agent_name"], "Zed")
        blob = json.dumps(body)
        self.assertNotIn("secret-token", blob)
        self.assertNotIn("secret-device", blob)
        self.assertNotIn("too old", blob)
        self.assertNotIn("ACM-OPEN", blob)
        self.assertNotIn("ACM-DONE", blob)
        self.assertNotIn("ACM-B", blob)
        self.assertNotIn("rt-on", blob)
        self.assertNotIn("rt-dead", blob)
        self.assertNotIn("rt-empty", blob)
        text = render_attention_text(body)
        self.assertLessEqual(len(text.encode()), ATTENTION_TEXT_BYTES)
        self.assertIn("# alpha Alpha", text)
        self.assertIn("offline_runtimes 1", text)
        self.assertNotIn("\n…\n", text)
        row = next(line for line in text.splitlines() if line.startswith("ACM-2\t"))
        self.assertNotIn("\n", row)
        self.assertEqual(row.split("\t")[4], "hello world !")

    def test_hermes_agents_missing_async_delegation_skill(self):
        s = SERVER
        with self.store.tx() as c:
            self.store.upsert(c, "workspaces", {
                "server": s, "id": "wh", "name": "Hermes Lab", "slug": "hermes-lab",
            })
            for rid, provider in (("rt-h", "Hermes"), ("rt-c", "codex")):
                self.store.upsert(c, "runtimes", {
                    "server": s, "workspace_id": "wh", "id": rid, "name": provider,
                    "provider": provider, "status": "online",
                })

            def ag(aid, name, runtime, skills, updated, archived=None):
                self.store.upsert(c, "agents", {
                    "server": s, "workspace_id": "wh", "id": aid, "name": name,
                    "runtime_id": runtime, "archived_at": archived, "updated_at": updated,
                    "skills_json": None if skills is None else json.dumps(skills),
                })

            ag("m-new", "Vega\nLab", "rt-h", ["knowledge-routing"], "2026-10-16T02:00:00Z")
            ag("m-b", "Reviewer", "rt-h", [], "2026-10-16T01:00:00Z")
            ag("m-a", "Archivist", "rt-h", None, "2026-10-16T01:00:00Z")
            ag("has-skill", "存储", "rt-h", ["hermes-async-delegation"], "2026-10-16T03:00:00Z")
            ag("archived", "Old", "rt-h", [], "2026-10-16T04:00:00Z", archived="2026-01-01T00:00:00Z")
            ag("codex", "Builder", "rt-c", [], "2026-10-16T05:00:00Z")
            ag("ghost", "Ghost", "rt-missing", [], "2026-10-16T06:00:00Z")

        hermes = next(w for w in self.v.attention()["workspaces"] if w["id"] == "wh")
        self.assertEqual(
            list(hermes),
            ["id", "slug", "name", "missing_hermes_async_delegation"],
        )
        self.assertEqual(
            [(i["identifier"], i["title"], i["status"], i["agent_name"]) for i in hermes["missing_hermes_async_delegation"]],
            [
                ("m-new", "Vega Lab", "missing_skill", "Vega Lab"),
                ("m-a", "Archivist", "missing_skill", "Archivist"),
                ("m-b", "Reviewer", "missing_skill", "Reviewer"),
            ],
        )
        blob = json.dumps(hermes)
        self.assertNotIn("has-skill", blob)
        self.assertNotIn("archived", blob)
        self.assertNotIn("codex", blob)
        self.assertNotIn("ghost", blob)
        text = render_attention_text({"generated_at": "t", "window_h": 24, "workspaces": [hermes]})
        self.assertIn("missing_hermes_async_delegation 3", text)
        self.assertIn("m-new\tmissing_skill\t2026-10-16T02:00:00Z\tVega Lab\tVega Lab", text)

    def test_text_budget(self):
        items = [
            {"identifier": f"ACM-{i:04d}", "title": "标" * 80, "status": "in_review",
             "time": "2026-10-14T09:00:00Z", "agent_name": "Builder"}
            for i in range(80)
        ]
        body = {
            "generated_at": "2026-10-14T09:00:00Z",
            "window_h": 24,
            "workspaces": [{"id": "w", "slug": "lab", "name": "Lab", "in_review": items}],
        }
        text = render_attention_text(body)
        self.assertLessEqual(len(text.encode()), ATTENTION_TEXT_BYTES)
        self.assertTrue(text.endswith("…\n"))
        self.assertIn("in_review 80", text)
        self.assertLess(text.count("\t"), 80)
        self.assertEqual(text.splitlines()[-1], "…")


class AgentUpdateAttentionTests(unittest.TestCase):
    """ACM-129 / ACM-139 / ACM-140: a finished assignee run with no covering transition."""

    def setUp(self):
        self.store = Store(":memory:")
        self.v = Views(self.store, SERVER, app_url="https://app.test", collect_interval_s=60)
        s = SERVER
        with self.store.tx() as c:
            self.store.upsert(c, "workspaces", {
                "server": s, "id": "wa", "name": "ACM Lab", "slug": "acm-lab",
            })
            self.store.upsert(c, "agents", {
                "server": s, "workspace_id": "wa", "id": "builder", "name": "Builder",
            })

            def issue(iid, status, category=None):
                self.store.upsert(c, "issues", {
                    "server": s, "workspace_id": "wa", "id": iid, "identifier": iid,
                    "title": iid, "status": status, "status_category": category or status,
                    "assignee_type": "agent", "assignee_id": "builder",
                    "created_at": ago(48),
                })

            def run(tid, iid, when, status="completed", agent="builder"):
                self.store.upsert(c, "tasks", {
                    "server": s, "workspace_id": "wa", "id": tid, "agent_id": agent,
                    "issue_id": iid, "status": status, "created_at": when, "completed_at": when,
                })

            issue("ACM-129", "in_progress")
            run("run-129", "ACM-129", ago(1))
            issue("ACM-139", "in_review")
            run("run-139", "ACM-139", ago(0.2))
            issue("ACM-140", "in_review")
            run("run-140", "ACM-140", ago(0.3))
            issue("ACM-COVERED", "in_review")
            run("run-covered", "ACM-COVERED", ago(0.5))
            issue("ACM-DONE", "done")
            run("run-done", "ACM-DONE", ago(0.4))
            issue("ACM-BACKLOG", "backlog")
            run("run-backlog", "ACM-BACKLOG", ago(0.4))
            issue("ACM-OTHER", "in_progress")
            run("run-other", "ACM-OTHER", ago(0.4), agent="someone-else")
        self.store.append_changes(s, "wa", [
            {"at": ago(2), "kind": "issue_status", "resource_id": "ACM-139",
             "payload": {"status": "in_review", "previous_status": "in_progress"}},
            {"at": ago(3), "kind": "issue_status", "resource_id": "ACM-140",
             "payload": {"status": "in_review", "previous_status": "in_progress"}},
            {"at": ago(0.5), "kind": "issue_status", "resource_id": "ACM-COVERED",
             "payload": {"status": "in_review", "previous_status": "in_progress"}},
        ])

    def tearDown(self):
        self.store.close()

    def test_uncovered_assignee_runs(self):
        body = self.v.attention()
        ws = body["workspaces"][0]
        updates = {item["identifier"]: item for item in ws["agent_updates"]}
        self.assertEqual(set(updates), {"ACM-129", "ACM-139", "ACM-140"})
        self.assertEqual(updates["ACM-129"]["status"], "in_progress")
        self.assertEqual(updates["ACM-129"]["agent_name"], "Builder")
        self.assertEqual(updates["ACM-139"]["status"], "in_review")
        self.assertEqual(updates["ACM-140"]["status"], "in_review")
        review = {item["identifier"] for item in ws["in_review"]}
        self.assertIn("ACM-139", review)
        self.assertIn("ACM-140", review)
        self.assertIn("ACM-COVERED", review)
        self.assertNotIn("ACM-129", review)
        self.assertNotIn("ACM-DONE", json.dumps(body))
        self.assertNotIn("ACM-BACKLOG", json.dumps(body))
        self.assertNotIn("ACM-OTHER", json.dumps(ws.get("agent_updates")))
        text = render_attention_text(body)
        self.assertIn("agent_updates 3", text)
        self.assertIn("ACM-129\tin_progress\t", text)


if __name__ == "__main__":
    unittest.main()
