#!/usr/bin/env python3
"""Inspect and explicitly repair local Devin session extra directories.

The default command is read-only. Mutation requires ``repair --apply`` plus
explicit session IDs and exact old-value guards. This tool intentionally does
not modify Space membership or project files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CLI_DB = Path.home() / ".local/share/devin/cli/sessions.db"
DEFAULT_DESKTOP_DB = (
    Path.home()
    / "Library/Application Support/Devin/User/globalStorage/state.vscdb"
)
DESKTOP_KEY_PREFIX = "windsurf.acp.sessioninfo.session.acp/devin-cli/"
DESKTOP_META_KEY = "cognition.ai/additionalWorkspaceDirs"


class RepairError(RuntimeError):
    """Expected, user-actionable validation or repair failure."""


def _json_array(value: str, option: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RepairError(f"{option} must be a JSON array of strings") from exc
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise RepairError(f"{option} must be a JSON array of strings")
    if any(not item for item in parsed):
        raise RepairError(f"{option} cannot contain empty paths")
    return parsed


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _decode_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if not isinstance(value, str):
        raise RepairError("Desktop state value is not UTF-8 text")
    return value


def _encode_like(value: str, original: Any) -> str | bytes:
    """Keep the SQLite storage type used by the existing ItemTable row."""
    return value.encode("utf-8") if isinstance(original, bytes) else value


def _open_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise RepairError(f"SQLite database not found: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def read_cli_records(path: Path, session_ids: list[str]) -> dict[str, dict[str, Any]]:
    connection = _open_readonly(path)
    try:
        rows = connection.execute(
            "SELECT id, working_directory, workspace_dirs "
            "FROM sessions WHERE id IN (%s)"
            % ",".join("?" for _ in session_ids),
            session_ids,
        ).fetchall()
    finally:
        connection.close()
    records: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            workspace_dirs = json.loads(row["workspace_dirs"] or "[]")
        except json.JSONDecodeError as exc:
            raise RepairError(f"Invalid workspace_dirs JSON for {row['id']}") from exc
        if not isinstance(workspace_dirs, list) or any(
            not isinstance(item, str) for item in workspace_dirs
        ):
            raise RepairError(f"Invalid workspace_dirs value for {row['id']}")
        records[row["id"]] = {
            "id": row["id"],
            "working_directory": row["working_directory"],
            "workspace_dirs": _unique(workspace_dirs),
        }
    missing = [session_id for session_id in session_ids if session_id not in records]
    if missing:
        raise RepairError(f"CLI session(s) not found: {', '.join(missing)}")
    return records


def read_desktop_records(path: Path, session_ids: list[str]) -> dict[str, dict[str, Any]]:
    keys = [DESKTOP_KEY_PREFIX + session_id for session_id in session_ids]
    connection = _open_readonly(path)
    try:
        rows = connection.execute(
            "SELECT key, value FROM ItemTable WHERE key IN (%s)"
            % ",".join("?" for _ in keys),
            keys,
        ).fetchall()
    finally:
        connection.close()
    records: dict[str, dict[str, Any]] = {}
    for row in rows:
        session_id = row["key"][len(DESKTOP_KEY_PREFIX) :]
        try:
            payload = json.loads(_decode_value(row["value"]))
        except json.JSONDecodeError as exc:
            raise RepairError(f"Invalid Desktop JSON for {session_id}") from exc
        info = payload.get("info")
        if not isinstance(info, dict):
            raise RepairError(f"Desktop record has no info object for {session_id}")
        metadata = info.get("_meta")
        if not isinstance(metadata, dict):
            metadata = {}
        extras = metadata.get(DESKTOP_META_KEY)
        if extras is None:
            extras = []
        if not isinstance(extras, list) or any(not isinstance(item, str) for item in extras):
            raise RepairError(f"Invalid Desktop extra directories for {session_id}")
        records[session_id] = {
            "id": session_id,
            "cwd": info.get("cwd"),
            "additional_workspace_dirs": _unique(extras),
            "has_additional_field": DESKTOP_META_KEY in metadata,
        }
    missing = [session_id for session_id in session_ids if session_id not in records]
    if missing:
        raise RepairError(f"Desktop session record(s) not found: {', '.join(missing)}")
    return records


def inspect(
    cli_db: Path, desktop_db: Path, session_ids: list[str]
) -> dict[str, dict[str, Any]]:
    cli = read_cli_records(cli_db, session_ids)
    desktop = read_desktop_records(desktop_db, session_ids)
    return {
        session_id: {"cli": cli[session_id], "desktop": desktop[session_id]}
        for session_id in session_ids
    }


def _database_sidecars(path: Path) -> list[Path]:
    return [candidate for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")) if candidate.exists()]


def _assert_no_open_writers(paths: list[Path]) -> None:
    lsof = shutil.which("lsof")
    if not lsof:
        raise RepairError("lsof is required to verify that Devin is stopped")
    open_paths: list[str] = []
    for path in paths:
        result = subprocess.run(
            [lsof, "-t", str(path)], capture_output=True, text=True, check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            open_paths.append(f"{path}: {result.stdout.strip().replace(chr(10), ', ')}")
    if open_paths:
        raise RepairError(
            "Devin still has database files open; quit Devin Desktop and CLI first: "
            + "; ".join(open_paths)
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_databases(cli_db: Path, desktop_db: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=False)
    manifest: list[dict[str, Any]] = []
    for source in _database_sidecars(cli_db) + _database_sidecars(desktop_db):
        destination = backup_dir / ("cli-" if source.parent == cli_db.parent else "desktop-") / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        manifest.append(
            {
                "source": str(source),
                "backup": str(destination),
                "size": source.stat().st_size,
                "sha256": _sha256(source),
                "backup_sha256": _sha256(destination),
            }
        )
    (backup_dir / "manifest.json").write_text(
        json.dumps({"files": manifest}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if any(item["sha256"] != item["backup_sha256"] for item in manifest):
        raise RepairError(f"Backup hash verification failed: {backup_dir}")
    return backup_dir


def _set_desktop_extras(payload: dict[str, Any], extras: list[str]) -> str:
    info = payload.setdefault("info", {})
    metadata = info.setdefault("_meta", {})
    if extras:
        metadata[DESKTOP_META_KEY] = extras
    else:
        metadata.pop(DESKTOP_META_KEY, None)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def repair(
    cli_db: Path,
    desktop_db: Path,
    session_ids: list[str],
    expect_cwd: str,
    expect_extra: list[str],
    set_extra: list[str],
    apply: bool,
    backup_dir: Path | None = None,
    check_processes: bool = True,
) -> dict[str, Any]:
    state = inspect(cli_db, desktop_db, session_ids)
    expected_extra = _unique(expect_extra)
    for session_id, record in state.items():
        if record["cli"]["working_directory"] != expect_cwd:
            raise RepairError(f"Unexpected cwd for {session_id}")
        if record["cli"]["workspace_dirs"] != expected_extra:
            raise RepairError(f"Unexpected CLI extra directories for {session_id}")
        if record["desktop"]["additional_workspace_dirs"] != expected_extra:
            raise RepairError(f"Unexpected Desktop extra directories for {session_id}")
        if record["desktop"]["cwd"] != expect_cwd:
            raise RepairError(f"Unexpected Desktop cwd for {session_id}")
    plan = {
        "sessions": session_ids,
        "expect_cwd": expect_cwd,
        "from_extra": expected_extra,
        "to_extra": _unique(set_extra),
        "apply": apply,
    }
    if not apply:
        return {"plan": plan, "state": state}
    db_paths = _database_sidecars(cli_db) + _database_sidecars(desktop_db)
    if check_processes:
        _assert_no_open_writers(db_paths)
    if backup_dir is None:
        stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
        backup_dir = cli_db.parent / f"repair-backup-{stamp}"
    backup_databases(cli_db, desktop_db, backup_dir)
    new_extra = _unique(set_extra)
    try:
        with sqlite3.connect(cli_db) as connection:
            connection.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in session_ids)
            params: list[Any] = [json.dumps(new_extra, ensure_ascii=False), expect_cwd, json.dumps(expected_extra), *session_ids]
            cursor = connection.execute(
                f"UPDATE sessions SET workspace_dirs = ? "
                f"WHERE working_directory = ? AND workspace_dirs = ? AND id IN ({placeholders})",
                params,
            )
            if cursor.rowcount != len(session_ids):
                raise RepairError(f"CLI guarded update changed {cursor.rowcount} rows, expected {len(session_ids)}")
            connection.commit()
        with sqlite3.connect(desktop_db) as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = 0
            for session_id in session_ids:
                key = DESKTOP_KEY_PREFIX + session_id
                row = connection.execute("SELECT value FROM ItemTable WHERE key = ?", (key,)).fetchone()
                if row is None:
                    raise RepairError(f"Desktop record disappeared during repair: {session_id}")
                payload = json.loads(_decode_value(row[0]))
                metadata = payload.get("info", {}).get("_meta", {})
                current = metadata.get(DESKTOP_META_KEY, [])
                if _unique(current) != expected_extra:
                    raise RepairError(f"Desktop old-value guard failed during repair: {session_id}")
                connection.execute(
                    "UPDATE ItemTable SET value = ? WHERE key = ?",
                    (_encode_like(_set_desktop_extras(payload, new_extra), row[0]), key),
                )
                updated += 1
            if updated != len(session_ids):
                raise RepairError(f"Desktop guarded update changed {updated} rows")
            connection.commit()
    except Exception:
        raise
    final_state = inspect(cli_db, desktop_db, session_ids)
    for session_id, record in final_state.items():
        if record["cli"]["workspace_dirs"] != new_extra:
            raise RepairError(f"CLI post-write verification failed: {session_id}")
        if record["desktop"]["additional_workspace_dirs"] != new_extra:
            raise RepairError(f"Desktop post-write verification failed: {session_id}")
    return {"plan": plan, "backup_dir": str(backup_dir), "state": final_state}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli-db", type=Path, default=DEFAULT_CLI_DB)
    parser.add_argument("--desktop-db", type=Path, default=DEFAULT_DESKTOP_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "repair"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--session", dest="session_ids", action="append", required=True)
        if command == "repair":
            subparser.add_argument("--expect-cwd", required=True)
            subparser.add_argument("--expect-extra-json", required=True)
            subparser.add_argument("--set-extra-json", required=True)
            subparser.add_argument("--backup-dir", type=Path)
            subparser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect(args.cli_db, args.desktop_db, args.session_ids)
        else:
            result = repair(
                args.cli_db,
                args.desktop_db,
                args.session_ids,
                args.expect_cwd,
                _json_array(args.expect_extra_json, "--expect-extra-json"),
                _json_array(args.set_extra_json, "--set-extra-json"),
                args.apply,
                args.backup_dir,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (RepairError, sqlite3.Error, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
