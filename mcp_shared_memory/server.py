#!/usr/bin/env python3
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlparse

sys.dont_write_bytecode = True


PROTOCOL_VERSION = "2025-06-18"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

class _StdioTransport:
    """
    Codex MCP stdio transport has historically existed in two shapes:
    - JSONL: one JSON-RPC message per line
    - LSP-style framing: Content-Length headers + raw JSON payload

    To reduce version/client mismatch issues, we auto-detect the incoming format
    on the first successfully parsed message and respond using the same format.
    """

    def __init__(self, stdin: Any, stdout: Any):
        self._stdin = stdin
        self._stdout = stdout
        self._mode: Optional[str] = None  # "jsonl" | "content-length"

    def read(self) -> Optional[Dict[str, Any]]:
        line = self._stdin.readline()
        if not line:
            return None

        # Skip empty lines (both modes may include them).
        while line in (b"\n", b"\r\n"):
            line = self._stdin.readline()
            if not line:
                return None

        stripped = line.strip()
        if not stripped:
            return {}

        # First try JSONL when the line looks like JSON.
        if stripped[:1] in (b"{", b"["):
            try:
                msg = json.loads(stripped.decode("utf-8"))
                self._mode = self._mode or "jsonl"
                return msg
            except Exception:
                # Fall through to header framing parsing.
                pass

        # Otherwise parse as LSP-style framed message (Content-Length).
        headers: Dict[str, str] = {}
        cur = line
        while cur not in (b"\n", b"\r\n"):
            try:
                k, v = cur.decode("utf-8").split(":", 1)
            except ValueError as e:
                raise RuntimeError(f"Invalid header line: {cur!r}") from e
            headers[k.strip().lower()] = v.strip()
            cur = self._stdin.readline()
            if not cur:
                return None

        if "content-length" not in headers:
            raise RuntimeError(f"Missing Content-Length header. Headers={headers!r}")
        length = int(headers["content-length"])
        body = self._stdin.read(length)
        if not body:
            return None
        self._mode = self._mode or "content-length"
        return json.loads(body.decode("utf-8"))

    def write(self, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        mode = self._mode or "content-length"  # conservative default
        if mode == "jsonl":
            self._stdout.write(data + b"\n")
        else:
            self._stdout.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
            self._stdout.write(data)
        self._stdout.flush()


def _jsonrpc_error(code: int, message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return err


@dataclass
class MemoryRecord:
    id: str
    text: str
    tags: List[str]
    source: Optional[str]
    created_at: str
    updated_at: str


class MemoryStore:
    def __init__(self, db_path: str):
        dir_path = os.path.dirname(db_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        # Reduce "database is locked" risk when multiple processes touch the same DB
        # (e.g., Codex runs the MCP server while a one-off seeding script runs).
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._init_db()

    def _has_fts5(self) -> bool:
        try:
            self._conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS __fts_test USING fts5(x)")
            self._conn.execute("DROP TABLE __fts_test")
            return True
        except sqlite3.OperationalError:
            return False

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
              rowid INTEGER PRIMARY KEY AUTOINCREMENT,
              id TEXT NOT NULL UNIQUE,
              text TEXT NOT NULL,
              tags_json TEXT NOT NULL DEFAULT '[]',
              source TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )

        if self._has_fts5():
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(text, content='memories', content_rowid='rowid')
                """
            )
            self._conn.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                  INSERT INTO memories_fts(rowid, text) VALUES (new.rowid, new.text);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                  INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.rowid, old.text);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                  INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.rowid, old.text);
                  INSERT INTO memories_fts(rowid, text) VALUES (new.rowid, new.text);
                END;
                """
            )
        self._conn.commit()

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            text=row["text"],
            tags=json.loads(row["tags_json"]),
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def add(self, text: str, tags: Optional[List[str]] = None, source: Optional[str] = None) -> MemoryRecord:
        now = _utc_now_iso()
        mem_id = str(uuid.uuid4())
        tags = [t.strip() for t in (tags or []) if t and t.strip()]
        self._conn.execute(
            "INSERT INTO memories(id, text, tags_json, source, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            (mem_id, text, json.dumps(tags, ensure_ascii=False), source, now, now),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
        if row is None:
            raise RuntimeError("Failed to read inserted memory")
        return self._row_to_record(row)

    def get(self, mem_id: str) -> Optional[MemoryRecord]:
        row = self._conn.execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def list_recent(self, limit: int = 20) -> List[MemoryRecord]:
        rows = self._conn.execute(
            "SELECT * FROM memories ORDER BY datetime(created_at) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def update(
        self,
        mem_id: str,
        text: Optional[str] = None,
        tags: Optional[List[str]] = None,
        source: Optional[str] = None,
    ) -> Optional[MemoryRecord]:
        existing = self.get(mem_id)
        if existing is None:
            return None
        new_text = text if text is not None else existing.text
        new_tags = tags if tags is not None else existing.tags
        new_source = source if source is not None else existing.source
        now = _utc_now_iso()
        self._conn.execute(
            "UPDATE memories SET text = ?, tags_json = ?, source = ?, updated_at = ? WHERE id = ?",
            (new_text, json.dumps(new_tags, ensure_ascii=False), new_source, now, mem_id),
        )
        self._conn.commit()
        return self.get(mem_id)

    def forget(self, mem_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def search(
        self, query: str, limit: int = 10, tags: Optional[List[str]] = None
    ) -> List[Tuple[MemoryRecord, float]]:
        tags = [t.strip() for t in (tags or []) if t and t.strip()]
        if not query or not query.strip():
            recs = self.list_recent(limit=limit * 5)
            out: List[Tuple[MemoryRecord, float]] = []
            for r in recs:
                if tags and not set(tags).issubset(set(r.tags)):
                    continue
                out.append((r, 0.0))
                if len(out) >= limit:
                    break
            return out

        has_fts = False
        try:
            self._conn.execute("SELECT 1 FROM memories_fts LIMIT 1")
            has_fts = True
        except sqlite3.OperationalError:
            has_fts = False

        records_with_score: List[Tuple[MemoryRecord, float]] = []
        if has_fts:
            rows = self._conn.execute(
                """
                SELECT m.*, bm25(memories_fts) AS score
                FROM memories_fts
                JOIN memories m ON m.rowid = memories_fts.rowid
                WHERE memories_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (query, limit * 5),
            ).fetchall()
            for r in rows:
                rec = self._row_to_record(r)
                if tags and not set(tags).issubset(set(rec.tags)):
                    continue
                # bm25: lower is better. Convert to descending-is-better.
                score = float(-r["score"])
                records_with_score.append((rec, score))
                if len(records_with_score) >= limit:
                    break
        else:
            like = f"%{query}%"
            rows = self._conn.execute(
                """
                SELECT * FROM memories
                WHERE text LIKE ?
                ORDER BY datetime(created_at) DESC
                LIMIT ?
                """,
                (like, limit * 5),
            ).fetchall()
            for r in rows:
                rec = self._row_to_record(r)
                if tags and not set(tags).issubset(set(rec.tags)):
                    continue
                records_with_score.append((rec, 0.0))
                if len(records_with_score) >= limit:
                    break

        return records_with_score

    def stats(self) -> Dict[str, Any]:
        count = int(self._conn.execute("SELECT COUNT(1) AS c FROM memories").fetchone()["c"])
        return {"count": count}


def _tool_schema(
    name: str,
    description: str,
    properties: Dict[str, Any],
    required: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
    }


def _as_text_content(obj: Any) -> List[Dict[str, Any]]:
    return [{"type": "text", "text": json.dumps(obj, ensure_ascii=False, indent=2)}]


def _resource_entry(
    uri: str,
    name: str,
    title: str,
    description: str,
    mime_type: str = "application/json",
) -> Dict[str, Any]:
    return {
        "uri": uri,
        "name": name,
        "title": title,
        "description": description,
        "mimeType": mime_type,
    }


def _resource_template(
    uri_template: str,
    name: str,
    title: str,
    description: str,
) -> Dict[str, Any]:
    return {
        "uriTemplate": uri_template,
        "name": name,
        "title": title,
        "description": description,
    }


def _title_from_text(text: str) -> Optional[str]:
    for line in (text or "").splitlines():
        if line.lower().startswith("title:"):
            return line.split(":", 1)[1].strip() or None
        if line.strip():
            break
    return None


def _tags_from_query_param(qs: Dict[str, List[str]]) -> List[str]:
    raw = qs.get("tags") or qs.get("tag") or []
    tags: List[str] = []
    for item in raw:
        for part in item.split(","):
            part = part.strip()
            if part:
                tags.append(part)
    # de-dupe while preserving order
    seen: set[str] = set()
    out: List[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _memory_resource_uri(mem_id: str) -> str:
    return f"shared-memory://memory/{mem_id}"


def _tag_resource_uri(tag: str, limit: int = 20) -> str:
    return f"shared-memory://tag/{quote(tag, safe='')}?limit={limit}"


def main() -> int:
    db_path = os.environ.get("MCP_MEMORY_DB_PATH") or "~/.codex/mcp/shared-memory/memory.sqlite3"
    db_path = os.path.expandvars(os.path.expanduser(db_path))
    store = MemoryStore(db_path)

    tools = [
        _tool_schema(
            "memory_add",
            "Add a durable memory entry for cross-session recall.",
            {
                "text": {"type": "string", "minLength": 1},
                "tags": {"type": "array", "items": {"type": "string"}},
                "source": {"type": "string"},
            },
            required=["text"],
        ),
        _tool_schema(
            "memory_search",
            "Search memories (full-text if available). If query is omitted/empty, returns recent memories filtered by tags.",
            {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            required=[],
        ),
        _tool_schema(
            "memory_get",
            "Get a memory by id.",
            {"id": {"type": "string", "minLength": 1}},
            required=["id"],
        ),
        _tool_schema(
            "memory_list",
            "List most recent memories.",
            {"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}},
        ),
        _tool_schema(
            "memory_update",
            "Update an existing memory (text/tags/source).",
            {
                "id": {"type": "string", "minLength": 1},
                "text": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "source": {"type": "string"},
            },
            required=["id"],
        ),
        _tool_schema(
            "memory_forget",
            "Delete a memory by id.",
            {"id": {"type": "string", "minLength": 1}},
            required=["id"],
        ),
        _tool_schema("memory_stats", "Get simple memory store stats.", {}),
    ]

    resource_templates = [
        _resource_template(
            "shared-memory://memory/{id}",
            "memory-by-id",
            "Shared Memory: by id",
            "Read a memory record by id.",
        ),
        _resource_template(
            "shared-memory://search?query={query}&tags={tags}&limit={limit}",
            "memory-search",
            "Shared Memory: search",
            "Search memories. tags supports comma-separated values or repeated params.",
        ),
        _resource_template(
            "shared-memory://tag/{tag}?limit={limit}",
            "memory-by-tag",
            "Shared Memory: by tag",
            "List recent memories filtered by a tag. Tag must be URL-encoded if it contains '/'.",
        ),
        _resource_template(
            "shared-memory://recent?limit={limit}",
            "memory-recent",
            "Shared Memory: recent",
            "List recent memories.",
        ),
        _resource_template(
            "shared-memory://stats",
            "memory-stats",
            "Shared Memory: stats",
            "Get simple memory store stats.",
        ),
    ]

    transport = _StdioTransport(sys.stdin.buffer, sys.stdout.buffer)

    while True:
        try:
            msg = transport.read()
        except Exception as e:
            # If framing is broken, fail fast.
            sys.stderr.write(f"[mcp_shared_memory] read error: {e}\n")
            sys.stderr.flush()
            return 2
        if msg is None:
            return 0

        if "method" not in msg:
            continue

        method = msg["method"]
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        # Notifications have no id – ignore silently.
        if method in ("initialized", "notifications/initialized"):
            continue

        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": "mcp-shared-memory", "version": "0.1.0"},
                "capabilities": {"tools": {}, "resources": {}},
            }
            transport.write({"jsonrpc": "2.0", "id": msg_id, "result": result})
            continue

        if method == "tools/list":
            transport.write({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}})
            continue

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            try:
                if name == "memory_add":
                    rec = store.add(
                        text=str(arguments["text"]),
                        tags=arguments.get("tags"),
                        source=arguments.get("source"),
                    )
                    result = {
                        "content": _as_text_content({"memory": rec.__dict__}),
                        "isError": False,
                    }
                elif name == "memory_search":
                    pairs = store.search(
                        query=str(arguments.get("query", "")),
                        limit=int(arguments.get("limit", 10)),
                        tags=arguments.get("tags"),
                    )
                    result = {
                        "content": _as_text_content(
                            {
                                "matches": [
                                    {"score": score, "memory": rec.__dict__} for (rec, score) in pairs
                                ]
                            }
                        ),
                        "isError": False,
                    }
                elif name == "memory_get":
                    rec = store.get(str(arguments["id"]))
                    result = {"content": _as_text_content({"memory": rec.__dict__ if rec else None}), "isError": False}
                elif name == "memory_list":
                    recs = store.list_recent(limit=int(arguments.get("limit", 20)))
                    result = {"content": _as_text_content({"memories": [r.__dict__ for r in recs]}), "isError": False}
                elif name == "memory_update":
                    rec = store.update(
                        mem_id=str(arguments["id"]),
                        text=arguments.get("text"),
                        tags=arguments.get("tags"),
                        source=arguments.get("source"),
                    )
                    result = {"content": _as_text_content({"memory": rec.__dict__ if rec else None}), "isError": False}
                elif name == "memory_forget":
                    ok = store.forget(str(arguments["id"]))
                    result = {"content": _as_text_content({"deleted": ok}), "isError": False}
                elif name == "memory_stats":
                    result = {"content": _as_text_content(store.stats()), "isError": False}
                else:
                    raise KeyError(f"Unknown tool: {name}")
            except KeyError as e:
                transport.write(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": _jsonrpc_error(-32602, f"Invalid params: {e}"),
                    },
                )
                continue
            except Exception as e:
                transport.write(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {"content": _as_text_content({"error": str(e)}), "isError": True},
                    },
                )
                continue

            transport.write({"jsonrpc": "2.0", "id": msg_id, "result": result})
            continue

        if method == "resources/templates/list":
            transport.write(
                {"jsonrpc": "2.0", "id": msg_id, "result": {"resourceTemplates": resource_templates}},
            )
            continue

        if method == "resources/list":
            # Keep this list small and high-signal: seed memories + a short recent tail + a few entrypoints.
            resources: List[Dict[str, Any]] = []

            # Entry resources
            resources.append(
                _resource_entry(
                    "shared-memory://stats",
                    "stats",
                    "Shared Memory stats",
                    "Memory store statistics.",
                )
            )
            resources.append(
                _resource_entry(
                    "shared-memory://recent?limit=20",
                    "recent",
                    "Recent memories (20)",
                    "Most recent 20 memories.",
                )
            )

            seed_tag = "seed/2026-02-27"
            resources.append(
                _resource_entry(
                    _tag_resource_uri(seed_tag, limit=20),
                    "seed-2026-02-27",
                    "Seed memories (2026-02-27)",
                    "Seed memories used to bootstrap research context.",
                )
            )

            # Individual seed memories
            try:
                pairs = store.search(query="", tags=[seed_tag], limit=50)
                for rec, _score in pairs:
                    title = _title_from_text(rec.text) or rec.id
                    resources.append(
                        _resource_entry(
                            _memory_resource_uri(rec.id),
                            rec.id,
                            title,
                            f"created_at={rec.created_at} tags={','.join(rec.tags)}",
                        )
                    )
            except Exception:
                pass

            # Recent tail (no tags), de-duped by uri
            try:
                recent = store.list_recent(limit=20)
                for rec in recent:
                    title = _title_from_text(rec.text) or rec.id
                    resources.append(
                        _resource_entry(
                            _memory_resource_uri(rec.id),
                            rec.id,
                            title,
                            f"created_at={rec.created_at} tags={','.join(rec.tags)}",
                        )
                    )
            except Exception:
                pass

            deduped: List[Dict[str, Any]] = []
            seen_uris: set[str] = set()
            for r in resources:
                uri = r.get("uri")
                if not uri or uri in seen_uris:
                    continue
                seen_uris.add(uri)
                deduped.append(r)

            transport.write({"jsonrpc": "2.0", "id": msg_id, "result": {"resources": deduped}})
            continue

        if method == "resources/read":
            uri = (params or {}).get("uri")
            if not uri:
                transport.write(
                    {"jsonrpc": "2.0", "id": msg_id, "error": _jsonrpc_error(-32602, "Invalid params: missing uri")},
                )
                continue

            try:
                parsed = urlparse(uri)
                kind = parsed.netloc
                path = parsed.path.lstrip("/")
                qs = parse_qs(parsed.query)

                if kind == "stats":
                    payload = store.stats()
                elif kind == "recent":
                    limit = int((qs.get("limit") or ["20"])[0])
                    recs = store.list_recent(limit=limit)
                    payload = {"memories": [r.__dict__ for r in recs]}
                elif kind == "memory":
                    mem_id = path
                    rec = store.get(mem_id)
                    payload = {"memory": rec.__dict__ if rec else None}
                elif kind == "tag":
                    tag = unquote(path)
                    limit = int((qs.get("limit") or ["20"])[0])
                    pairs = store.search(query="", tags=[tag], limit=limit)
                    payload = {"matches": [{"score": s, "memory": r.__dict__} for (r, s) in pairs]}
                elif kind == "search":
                    query = (qs.get("query") or [""])[0]
                    limit = int((qs.get("limit") or ["10"])[0])
                    tags = _tags_from_query_param(qs)
                    pairs = store.search(query=query, tags=tags or None, limit=limit)
                    payload = {"matches": [{"score": s, "memory": r.__dict__} for (r, s) in pairs]}
                else:
                    raise ValueError(f"Unknown resource kind: {kind}")

                transport.write(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "contents": [
                                {
                                    "uri": uri,
                                    "mimeType": "application/json",
                                    "text": json.dumps(payload, ensure_ascii=False, indent=2),
                                }
                            ]
                        },
                    },
                )
            except Exception as e:
                transport.write(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": _jsonrpc_error(-32603, "Resource read failed", {"error": str(e)}),
                    },
                )
            continue

        if msg_id is not None:
            transport.write(
                {"jsonrpc": "2.0", "id": msg_id, "error": _jsonrpc_error(-32601, f"Method not found: {method}")},
            )


if __name__ == "__main__":
    raise SystemExit(main())
