import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "tools" / "repair_session_workspace.py"
SPEC = importlib.util.spec_from_file_location("repair_session_workspace", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


TARGET = "target-session"
OTHER = "other-session"
CWD = "/workspace/elder-oasis"
EXTRA = ["/workspace/yr"]


class RepairSessionWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.cli_db = root / "sessions.db"
        self.desktop_db = root / "state.vscdb"
        with sqlite3.connect(self.cli_db) as db:
            db.execute(
                "CREATE TABLE sessions ("
                "id TEXT PRIMARY KEY, working_directory TEXT NOT NULL, "
                "workspace_dirs TEXT NOT NULL)"
            )
            db.executemany(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                [
                    (TARGET, CWD, json.dumps(EXTRA)),
                    (OTHER, "/workspace/other", json.dumps(["/workspace/other-extra"])),
                ],
            )
        with sqlite3.connect(self.desktop_db) as db:
            db.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            for session_id, cwd, extra in [
                (TARGET, CWD, EXTRA),
                (OTHER, "/workspace/other", ["/workspace/other-extra"]),
            ]:
                payload = {
                    "info": {
                        "cwd": cwd,
                        "_meta": {MODULE.DESKTOP_META_KEY: extra},
                    }
                }
                db.execute(
                    "INSERT INTO ItemTable VALUES (?, ?)",
                    (
                        MODULE.DESKTOP_KEY_PREFIX + session_id,
                        json.dumps(payload).encode("utf-8") if session_id == TARGET else json.dumps(payload),
                    ),
                )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_inspect_reads_source_and_mirror_without_mutation(self):
        result = MODULE.inspect(self.cli_db, self.desktop_db, [TARGET])
        self.assertEqual(result[TARGET]["cli"]["workspace_dirs"], EXTRA)
        self.assertEqual(result[TARGET]["desktop"]["additional_workspace_dirs"], EXTRA)

    def test_repair_without_apply_is_a_plan_only(self):
        result = MODULE.repair(
            self.cli_db,
            self.desktop_db,
            [TARGET],
            CWD,
            EXTRA,
            [],
            apply=False,
        )
        self.assertFalse(result["plan"]["apply"])
        self.assertEqual(MODULE.inspect(self.cli_db, self.desktop_db, [TARGET])[TARGET]["cli"]["workspace_dirs"], EXTRA)

    def test_repair_rejects_wrong_old_value(self):
        with self.assertRaises(MODULE.RepairError):
            MODULE.repair(
                self.cli_db,
                self.desktop_db,
                [TARGET],
                CWD,
                ["/workspace/not-yr"],
                [],
                apply=False,
            )

    def test_apply_updates_only_target_and_verifies_backup(self):
        backup_dir = Path(self.tempdir.name) / "backup"
        result = MODULE.repair(
            self.cli_db,
            self.desktop_db,
            [TARGET],
            CWD,
            EXTRA,
            [],
            apply=True,
            backup_dir=backup_dir,
            check_processes=False,
        )
        self.assertEqual(result["state"][TARGET]["cli"]["workspace_dirs"], [])
        self.assertEqual(result["state"][TARGET]["desktop"]["additional_workspace_dirs"], [])
        self.assertFalse(result["state"][TARGET]["desktop"]["has_additional_field"])
        with sqlite3.connect(self.desktop_db) as db:
            value_type = db.execute(
                "SELECT typeof(value) FROM ItemTable WHERE key = ?",
                (MODULE.DESKTOP_KEY_PREFIX + TARGET,),
            ).fetchone()[0]
        self.assertEqual(value_type, "blob")
        other = MODULE.inspect(self.cli_db, self.desktop_db, [OTHER])[OTHER]
        self.assertEqual(other["cli"]["workspace_dirs"], ["/workspace/other-extra"])
        self.assertTrue((backup_dir / "manifest.json").is_file())
        manifest = json.loads((backup_dir / "manifest.json").read_text())
        self.assertTrue(manifest["files"])
        self.assertTrue(all(item["sha256"] == item["backup_sha256"] for item in manifest["files"]))


if __name__ == "__main__":
    unittest.main()
