"""Owner collection uses fake CLI output only: never the owner's credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.owner_agents import collect, overlay, snapshot_path, PROFILE, WORKSPACES
from multica_board.store import Store
from multica_board.views import Views


class OwnerAgentsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "board.sqlite"
        self.path = snapshot_path(self.db)
        self.ws = next(iter(WORKSPACES))
        self.calls = []

    def fake(self, argv, **kw):
        self.calls.append((argv, kw))
        ws = argv[argv.index("--workspace-id") + 1]
        return subprocess.CompletedProcess(argv, 0, json.dumps([{
            "id": "agent-test", "workspace_id": ws, "name": "Test Owner Agent",
            "instructions": "你好\r\nsecret=unsafe-value", "runtime_id": "runtime-test",
            "skills": [{"id": "s", "name": "safe", "enabled": True}],
            "env": {"TOKEN": "not-to-be-stored"}, "mcp_config": "not-to-be-stored",
        }]), "token=not-to-be-stored")

    def test_exact_read_only_calls_clean_env_minimal_private_atomic_snapshot(self):
        with patch.dict(os.environ, {"MULTICA_TOKEN": "task-token", "MULTICA_UNKNOWN": "override"}):
            result = collect(self.path, execute=self.fake)
        self.assertEqual(len(self.calls), 9)
        for argv, kw in self.calls:
            self.assertEqual(argv[:3], ["multica", "--profile", PROFILE])
            self.assertEqual(argv[5:], ["agent", "list", "--include-archived", "--output", "json"])
            self.assertIn(argv[4], WORKSPACES)
            self.assertFalse(any(k.startswith("MULTICA_") for k in kw["env"]))
            self.assertEqual(kw["env"]["GODEBUG"], "tlsmlkem=0")
            self.assertNotEqual(kw["cwd"], os.getcwd())
        text = self.path.read_text()
        self.assertNotIn("not-to-be-stored", text)
        self.assertNotIn("unsafe-value", text)
        self.assertIn("你好", text)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(all(e["status"] == "ok" for e in result["workspaces"].values()))

    def test_failure_timeout_scope_malformed_isolation_and_removal_of_old_success(self):
        collect(self.path, execute=self.fake)
        count = 0
        def broken(argv, **kw):
            nonlocal count
            count += 1
            if count == 1:
                return subprocess.CompletedProcess(argv, 1, "", "secret=never-log")
            if count == 2:
                raise subprocess.TimeoutExpired(argv, 60)
            if count == 3:
                return subprocess.CompletedProcess(argv, 0, '{"unexpected": []}', "")
            if count == 4:
                return subprocess.CompletedProcess(argv, 0, '[{"id":"a","workspace_id":"wrong"}]', "")
            return self.fake(argv, **kw)
        data = collect(self.path, execute=broken)
        self.assertEqual(count, 9)
        self.assertEqual(sum(e["status"] == "error" for e in data["workspaces"].values()), 4)
        self.assertEqual(data["workspaces"][self.ws]["agents"], [])
        self.assertNotIn("never-log", self.path.read_text())

    def test_api_reads_overlay_without_mutating_database_and_profile_isolation(self):
        collect(self.path, execute=self.fake)
        store = Store(self.db)
        self.addCleanup(store.close)
        store.upsert_many(PROFILE, self.ws, "agents", [{"id": "obsolete", "name": "Old"}])
        views = Views(store, PROFILE, app_url="https://multica.ai", collect_interval_s=60)
        body = views.agents()
        self.assertEqual(len(body["items"]), 9)
        self.assertEqual(body["items"][0]["runtime_id"], "runtime-test")
        self.assertEqual(body["items"][0]["skills"][0]["name"], "safe")
        self.assertEqual(body["items"][0]["inventory_source"], "user-cli:" + PROFILE)
        self.assertTrue(body["items"][0]["inventory_collected_at"])
        self.assertEqual(len(views.meta()["workspaces"]), 9)
        self.assertIn("/podcast-foundry/agents/", next(a["url"] for a in body["items"] if a["workspace_id"] == self.ws))
        self.assertEqual(store.query("SELECT id FROM agents"), [{"id": "obsolete"}])
        self.assertEqual(overlay([], self.path, "other-profile"), ([], {}))

    def test_empty_success_replaces_old_rows_missing_corrupt_file_is_safe(self):
        base = [{"id": "old", "workspace_id": self.ws, "name": "Old"}]
        self.assertEqual(overlay(base, self.path, PROFILE)[0], base)
        collect(self.path, execute=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "[]", ""))
        self.assertEqual(overlay(base, self.path, PROFILE)[0], [])
        self.path.write_text("{broken")
        self.assertEqual(overlay(base, self.path, PROFILE)[0], base)


if __name__ == "__main__":
    unittest.main()
