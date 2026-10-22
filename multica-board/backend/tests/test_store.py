import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multica_board.store import SCHEMA_VERSION, Store  # noqa: E402


class StoreMigrationTests(unittest.TestCase):
    def test_skill_hash_is_added_without_discarding_existing_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.sqlite3"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE workspace_skills (server TEXT, workspace_id TEXT, id TEXT, name TEXT, description TEXT, labels_json TEXT, updated_at TEXT, PRIMARY KEY(server, workspace_id, id))")
            conn.execute("INSERT INTO workspace_skills (server,workspace_id,id,name) VALUES ('test','w','s','demo')")
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
            conn.close()
            for _ in range(2):
                store = Store(path)
                try:
                    row = store.query("SELECT * FROM workspace_skills")[0]
                    self.assertEqual(row["name"], "demo")
                    self.assertIsNone(row["content_hash"])
                finally:
                    store.close()

    def test_store_migrates_old_tasks_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.sqlite3"
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE tasks (
                  server TEXT NOT NULL, workspace_id TEXT NOT NULL, id TEXT NOT NULL,
                  agent_id TEXT, runtime_id TEXT, issue_id TEXT, kind TEXT, status TEXT,
                  attempt INTEGER, max_attempts INTEGER,
                  created_at TEXT, dispatched_at TEXT, started_at TEXT, completed_at TEXT,
                  error_summary TEXT, error_kind TEXT,
                  PRIMARY KEY (server, workspace_id, id)
                );
                PRAGMA user_version=5;
                """
            )
            conn.close()

            store = Store(path)
            try:
                columns = {row[1] for row in store._conn.execute("PRAGMA table_info(tasks)")}
                self.assertIn("failure_reason", columns)
                store.upsert_tasks("server", "workspace", [{"id": "task-1", "failure_reason": "quota"}])
                self.assertEqual(store.query("SELECT failure_reason FROM tasks")[0]["failure_reason"], "quota")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
