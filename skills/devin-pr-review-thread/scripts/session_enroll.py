#!/usr/bin/env python3
"""Host-only enrollment of one Devin session per normalized repository origin."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import tempfile
from typing import Any

from repository_binding import BindingError, normalize_origin


def run(argv: list[str], cwd: str | None = None) -> str:
    p = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, timeout=30)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or f"command failed: {' '.join(argv)}")
    return p.stdout.strip()


def root_for(path: str) -> pathlib.Path:
    return pathlib.Path(run(["git", "-C", path, "rev-parse", "--show-toplevel"])).resolve()


def origin_for(root: pathlib.Path) -> str:
    return normalize_origin(run(["git", "-C", str(root), "config", "--get", "remote.origin.url"]))


def roots_under(parent: pathlib.Path) -> list[pathlib.Path]:
    roots: list[pathlib.Path] = []
    for current, dirs, _ in os.walk(parent):
        dirs[:] = [d for d in dirs if d != ".git"]
        child = pathlib.Path(current)
        # Worktrees expose a .git file while regular repositories expose a
        # .git directory.  Avoid spawning git once per ordinary directory;
        # nested worktrees remain discoverable because traversal continues.
        if not (child / ".git").exists():
            continue
        try:
            candidate = root_for(str(child))
        except (OSError, RuntimeError, subprocess.SubprocessError):
            continue
        if candidate not in roots:
            roots.append(candidate)
    return roots


def verify_session(session_id: str, session_root: pathlib.Path) -> None:
    data = json.loads(run(["devin", "list", "--format", "json"]))
    if not isinstance(data, list):
        raise RuntimeError("devin session list is not an array")
    matches = [item for item in data if isinstance(item, dict) and item.get("id") == session_id]
    if len(matches) != 1:
        raise RuntimeError("session is missing or duplicated")
    item = matches[0]
    actual_root = item.get("working_directory")
    if not isinstance(actual_root, str) or pathlib.Path(actual_root).resolve() != session_root:
        raise RuntimeError("session root mismatch")
    model = item.get("model") or item.get("model_label")
    if model != "SWE-2 High":
        raise RuntimeError("session model mismatch")


def acquire(path: pathlib.Path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("session enrollment lock already exists") from exc
    return fd


def write_atomic(path: pathlib.Path, data: dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--repo-root")
    group.add_argument("--worktree-parent")
    parser.add_argument("--registry", default=os.path.expanduser("~/.config/devin/pr-review-sessions.json"))
    parser.add_argument("--session-id", help="existing Devin session to enroll; creation is deliberately explicit")
    parser.add_argument("--session-root", help="registered main workspace root")
    args = parser.parse_args()
    if args.worktree_parent:
        parent = pathlib.Path(args.worktree_parent).expanduser().resolve()
        if not isinstance(args.session_root, str) or not args.session_root:
            raise RuntimeError("--session-root is required with --worktree-parent")
        roots = roots_under(parent)
        if not roots:
            raise RuntimeError("no Git worktrees found under parent")
        root = pathlib.Path(args.session_root).expanduser().resolve()
        origins = {origin_for(candidate) for candidate in roots}
        if len(origins) != 1:
            raise RuntimeError("worktrees under parent have different origins")
        # The registered main workspace may live beside (not below) the
        # worktree parent; only its origin must match the enrolled worktrees.
        if origin_for(root) not in origins:
            raise RuntimeError("--session-root origin differs from worktree parent")
    else:
        root = root_for(args.repo_root)
    origin = origin_for(root)
    session_root = pathlib.Path(args.session_root).expanduser().resolve() if args.session_root else root
    if session_root != root and root != session_root and not str(root).startswith(str(session_root) + os.sep):
        raise RuntimeError("requested root is outside session root")
    enrolled_parent = pathlib.Path(args.worktree_parent).expanduser().resolve() if args.worktree_parent else None
    registry = pathlib.Path(args.registry).expanduser()
    lock = registry.with_suffix(registry.suffix + ".lock")
    fd = acquire(lock)
    try:
        try:
            data = json.loads(registry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            raise RuntimeError("registry must be an object")
        legacy_keys = [key for key in data if isinstance(key, str) and key.startswith("/")]
        if any(key != str(root) for key in legacy_keys):
            raise RuntimeError("mixed legacy registry requires explicit migration")
        # Legacy root-keyed records are migration input only; do not review
        # until this host-only command rewrites the v2 origin-keyed registry.
        entry = data.get(origin)
        if entry is None:
            legacy = data.get(str(root))
            if isinstance(legacy, dict):
                entry = dict(legacy)
                data.pop(str(root), None)
        if entry is not None:
            if not isinstance(entry, dict):
                raise RuntimeError("registry entry is ambiguous")
            existing_sid = entry.get("session_id")
            if args.session_id and existing_sid != args.session_id:
                raise RuntimeError("session binding mismatch; refusing rotation")
            sid = existing_sid
        else:
            sid = args.session_id
        if not isinstance(sid, str) or not sid:
            raise RuntimeError("--session-id is required; automatic session creation is not implicit")
        verify_session(sid, session_root)
        record = {"repository": origin, "session_id": sid, "session_root": str(session_root), "model": "SWE-2 High"}
        if enrolled_parent:
            record["enrolled_parent"] = str(enrolled_parent)
        data[origin] = {**(entry or {}), **record}
        # Reject duplicate session references across origins.
        refs = [v.get("session_id") for k, v in data.items() if isinstance(v, dict) and k != origin]
        if sid in refs:
            raise RuntimeError("session is already referenced by another origin")
        write_atomic(registry, data)
    finally:
        os.close(fd)
        try: lock.unlink()
        except FileNotFoundError: pass
    print(json.dumps({"schema": "devin-session-enrollment/v1", "status": "enrolled", "repository": origin, "session_id": sid}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, BindingError, subprocess.SubprocessError) as exc:
        print(json.dumps({"schema": "devin-session-enrollment/v1", "status": "failed", "error": str(exc)}))
        raise SystemExit(2)
