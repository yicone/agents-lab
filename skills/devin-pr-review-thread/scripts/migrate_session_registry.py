#!/usr/bin/env python3
"""Migrate legacy path-keyed Devin session metadata to the v2 origin-keyed form."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Any

from repository_binding import BindingError, normalize_origin


def read_object(path: pathlib.Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"registry JSON is invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("registry must be an object")
    return data


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, raw in data.items():
        if not isinstance(key, str) or not isinstance(raw, dict):
            raise RuntimeError("registry entries must be objects")
        if key.startswith("/"):
            origin = raw.get("repository")
            if not isinstance(origin, str):
                raise RuntimeError(f"legacy entry {key} is missing repository")
            try:
                origin = normalize_origin(origin)
            except BindingError as exc:
                raise RuntimeError(f"legacy entry {key} has invalid repository: {exc}") from exc
            entry = dict(raw)
            entry.setdefault("session_root", key)
            entry["repository"] = origin
        else:
            try:
                origin = normalize_origin(key)
            except BindingError as exc:
                raise RuntimeError(f"invalid v2 registry key {key}: {exc}") from exc
            entry = dict(raw)
            entry["repository"] = normalize_origin(str(entry.get("repository", origin)))
        sid = entry.get("session_id")
        if not isinstance(sid, str) or not sid:
            raise RuntimeError(f"registry entry {key} is missing session_id")
        if origin in result and result[origin] != entry:
            raise RuntimeError(f"duplicate repository binding for {origin}")
        result[origin] = entry

    sessions: dict[str, str] = {}
    for origin, entry in result.items():
        sid = entry["session_id"]
        previous = sessions.get(sid)
        if previous is not None and previous != origin:
            raise RuntimeError(f"session {sid} is referenced by multiple origins")
        sessions[sid] = origin
    return result


def write_atomic(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default=os.path.expanduser("~/.config/devin/pr-review-sessions.json"))
    parser.add_argument("--backup", help="backup path; defaults to a timestamped sibling")
    args = parser.parse_args()
    path = pathlib.Path(args.registry).expanduser()
    data = read_object(path)
    migrated = migrate(data)
    if not any(isinstance(key, str) and key.startswith("/") for key in data):
        print(json.dumps({"schema": "devin-session-migration/v1", "status": "already-v2", "registry": str(path)}))
        return 0
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = pathlib.Path(args.backup).expanduser() if args.backup else path.with_name(f"{path.name}.legacy-{stamp}.bak")
        if backup.exists():
            raise RuntimeError(f"backup already exists: {backup}")
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
    write_atomic(path, migrated)
    print(json.dumps({"schema": "devin-session-migration/v1", "status": "migrated", "registry": str(path), "entries": len(migrated), "backup": str(backup) if path.exists() else None}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, BindingError) as exc:
        print(json.dumps({"schema": "devin-session-migration/v1", "status": "failed", "error": str(exc)}))
        raise SystemExit(2)
