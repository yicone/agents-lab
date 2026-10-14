"""SQLite cache. Every row is keyed by (server, workspace_id, id)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = 2
TABLES = ("workspaces", "sync_status", "issues", "projects", "members", "agents", "runtimes", "tasks", "meta")
DATA_TABLES = ("issues", "projects", "members", "agents", "runtimes", "tasks")

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
  server TEXT NOT NULL, id TEXT NOT NULL,
  name TEXT, slug TEXT,
  PRIMARY KEY (server, id)
);
CREATE TABLE IF NOT EXISTS sync_status (
  server TEXT NOT NULL, workspace_id TEXT NOT NULL, resource TEXT NOT NULL,
  last_attempt_at TEXT, last_success_at TEXT, last_error TEXT, last_error_kind TEXT,
  item_count INTEGER, complete INTEGER,
  PRIMARY KEY (server, workspace_id, resource)
);
CREATE TABLE IF NOT EXISTS issues (
  server TEXT NOT NULL, workspace_id TEXT NOT NULL, id TEXT NOT NULL,
  identifier TEXT, number INTEGER, title TEXT,
  status TEXT, status_category TEXT, status_name TEXT, priority TEXT,
  assignee_type TEXT, assignee_id TEXT, creator_type TEXT, creator_id TEXT,
  parent_issue_id TEXT, project_id TEXT, stage INTEGER,
  start_date TEXT, due_date TEXT,
  created_at TEXT, updated_at TEXT, last_activity_at TEXT,
  labels_json TEXT, properties_json TEXT,
  PRIMARY KEY (server, workspace_id, id)
);
CREATE INDEX IF NOT EXISTS issues_status ON issues (status);
CREATE TABLE IF NOT EXISTS projects (
  server TEXT NOT NULL, workspace_id TEXT NOT NULL, id TEXT NOT NULL,
  title TEXT, status TEXT,
  PRIMARY KEY (server, workspace_id, id)
);
CREATE TABLE IF NOT EXISTS members (
  server TEXT NOT NULL, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
  name TEXT, role TEXT,
  PRIMARY KEY (server, workspace_id, user_id)
);
CREATE TABLE IF NOT EXISTS agents (
  server TEXT NOT NULL, workspace_id TEXT NOT NULL, id TEXT NOT NULL,
  name TEXT, status TEXT,
  runtime_id TEXT, runtime_mode TEXT, runtime_bound INTEGER,
  model TEXT, thinking_level TEXT, service_tier TEXT,
  max_concurrent_tasks INTEGER, visibility TEXT, owner_id TEXT,
  archived_at TEXT, updated_at TEXT,
  PRIMARY KEY (server, workspace_id, id)
);
CREATE TABLE IF NOT EXISTS runtimes (
  server TEXT NOT NULL, workspace_id TEXT NOT NULL, id TEXT NOT NULL,
  name TEXT, custom_name TEXT, provider TEXT, runtime_mode TEXT, status TEXT,
  last_seen_at TEXT, device_info TEXT, cli_version TEXT, visibility TEXT, owner_id TEXT,
  PRIMARY KEY (server, workspace_id, id)
);
CREATE TABLE IF NOT EXISTS tasks (
  server TEXT NOT NULL, workspace_id TEXT NOT NULL, id TEXT NOT NULL,
  agent_id TEXT, runtime_id TEXT, issue_id TEXT, kind TEXT, status TEXT,
  attempt INTEGER, max_attempts INTEGER,
  created_at TEXT, dispatched_at TEXT, started_at TEXT, completed_at TEXT,
  error_summary TEXT, error_kind TEXT,
  PRIMARY KEY (server, workspace_id, id)
);
CREATE INDEX IF NOT EXISTS tasks_issue ON tasks (server, workspace_id, issue_id, created_at);
CREATE INDEX IF NOT EXISTS tasks_agent ON tasks (server, workspace_id, agent_id, created_at);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_COLUMNS: dict[str, list[str]] = {}


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    if table not in _COLUMNS:
        _COLUMNS[table] = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return _COLUMNS[table]


class Store:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        if str(self.path) != ":memory:":
            os.chmod(self.path, 0o600)
            self._conn.execute("PRAGMA journal_mode=WAL")
        # The cache is disposable: on a schema change, rebuild instead of migrating.
        if self._conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            for t in TABLES:
                self._conn.execute(f"DROP TABLE IF EXISTS {t}")
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(params))]

    # --- writes -------------------------------------------------------------

    def upsert(self, conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
        cols = [c for c in _cols(conn, table) if c in row]
        placeholders = ",".join("?" for _ in cols)
        conn.execute(
            f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
            [row[c] for c in cols],
        )

    def replace_snapshot(self, server: str, workspace_id: str, table: str, rows: list[dict[str, Any]]) -> None:
        """Atomically replace one workspace's rows, so deletions and revoked access disappear."""
        with self.tx() as conn:
            conn.execute(f"DELETE FROM {table} WHERE server=? AND workspace_id=?", (server, workspace_id))
            for r in rows:
                self.upsert(conn, table, {**r, "server": server, "workspace_id": workspace_id})

    def upsert_many(self, server: str, workspace_id: str, table: str, rows: list[dict[str, Any]]) -> None:
        with self.tx() as conn:
            for r in rows:
                self.upsert(conn, table, {**r, "server": server, "workspace_id": workspace_id})

    def set_workspaces(self, server: str, rows: list[dict[str, Any]]) -> list[str]:
        """Replace the workspace list; purge cached data for workspaces no longer visible."""
        keep = {r["id"] for r in rows}
        with self.tx() as conn:
            existing = {r[0] for r in conn.execute("SELECT id FROM workspaces WHERE server=?", (server,))}
            gone = sorted(existing - keep)
            for ws in gone:
                self._purge(conn, server, ws)
            for r in rows:
                self.upsert(conn, "workspaces", {**r, "server": server})
        return gone

    def purge_data(self, server: str, workspace_id: str) -> None:
        """Drop a workspace's cached data but keep its row and sync status (auth failures)."""
        with self.tx() as conn:
            for table in DATA_TABLES:
                conn.execute(f"DELETE FROM {table} WHERE server=? AND workspace_id=?", (server, workspace_id))

    def _purge(self, conn: sqlite3.Connection, server: str, ws: str) -> None:
        for table in (*DATA_TABLES, "sync_status"):
            conn.execute(f"DELETE FROM {table} WHERE server=? AND workspace_id=?", (server, ws))
        conn.execute("DELETE FROM workspaces WHERE server=? AND id=?", (server, ws))

    def prune_tasks(self, server: str, workspace_id: str, before_iso: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "DELETE FROM tasks WHERE server=? AND workspace_id=? AND created_at < ? "
                "AND status IN ('completed','failed','cancelled')",
                (server, workspace_id, before_iso),
            )

    def record_sync(
        self,
        server: str,
        workspace_id: str,
        resource: str,
        *,
        at: str,
        ok: bool,
        error: str | None = None,
        error_kind: str | None = None,
        count: int | None = None,
        complete: bool = True,
        reset_success: bool = False,
    ) -> None:
        with self.tx() as conn:
            prev = conn.execute(
                "SELECT last_success_at, item_count, complete FROM sync_status WHERE server=? AND workspace_id=? AND resource=?",
                (server, workspace_id, resource),
            ).fetchone()
            last_success = at if ok else (prev["last_success_at"] if prev and not reset_success else None)
            item_count = count if ok else (prev["item_count"] if prev else None)
            comp = int(complete) if ok else (prev["complete"] if prev else None)
            self.upsert(
                conn,
                "sync_status",
                {
                    "server": server,
                    "workspace_id": workspace_id,
                    "resource": resource,
                    "last_attempt_at": at,
                    "last_success_at": last_success,
                    "last_error": None if ok else error,
                    "last_error_kind": None if ok else error_kind,
                    "item_count": item_count,
                    "complete": comp,
                },
            )

    def known_task_ids(self, server: str, workspace_id: str, agent_id: str) -> dict[str, str]:
        rows = self.query(
            "SELECT id, status FROM tasks WHERE server=? AND workspace_id=? AND agent_id=?",
            (server, workspace_id, agent_id),
        )
        return {r["id"]: r["status"] for r in rows}

    def set_meta(self, key: str, value: Any) -> None:
        with self.tx() as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def get_meta(self, key: str, default: Any = None) -> Any:
        rows = self.query("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default
