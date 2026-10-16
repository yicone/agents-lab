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

from repository_binding import (
    BindingError,
    SESSION_LAYOUT_EXACT_ROOT,
    SESSION_LAYOUTS,
    normalize_origin,
    resolve_session_binding,
    validate_session_layout,
)


def run(argv: list[str], cwd: str | None = None) -> str:
    p = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, timeout=30)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or f"command failed: {' '.join(argv)}")
    return p.stdout.strip()


def root_for(path: str) -> pathlib.Path:
    return pathlib.Path(run(["git", "-C", path, "rev-parse", "--show-toplevel"])).resolve()


def origin_for(root: pathlib.Path) -> str:
    return normalize_origin(run(["git", "-C", str(root), "config", "--get", "remote.origin.url"]))


def common_directory(root: pathlib.Path) -> str:
    return str(pathlib.Path(run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"])).resolve())


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
    # Devin's local list is workspace-scoped; querying from the caller's cwd
    # can hide a valid session registered against the requested main workspace.
    data = json.loads(run(["devin", "list", "--format", "json"], cwd=str(session_root)))
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
    # Some Devin CLI versions omit model metadata from `list --format json`.
    # The registry remains pinned to SWE-2 High; reject an explicit mismatch,
    # but do not treat an unavailable display field as a different model.
    if model is not None and model != "SWE-2 High":
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
    parser.add_argument("--repo-root", help="canonical main Git repository")
    parser.add_argument("--worktree-parent", help="optional parent of linked worktrees")
    parser.add_argument("--registry", default=os.path.expanduser("~/.config/devin/pr-review-sessions.json"))
    parser.add_argument("--session-id", help="existing Devin session to enroll; creation is deliberately explicit")
    parser.add_argument("--session-root", help="registered main workspace root")
    parser.add_argument(
        "--session-layout",
        choices=sorted(SESSION_LAYOUTS),
        default=SESSION_LAYOUT_EXACT_ROOT,
        help="relationship between the Devin workspace and repository root",
    )
    parser.add_argument("--check-only", action="store_true", help="verify enrollment without writing state")
    args = parser.parse_args()
    if not args.repo_root:
        if not args.worktree_parent or not args.session_root:
            raise RuntimeError("--repo-root or --worktree-parent with --session-root is required")
        if args.session_layout != SESSION_LAYOUT_EXACT_ROOT:
            raise RuntimeError("--repo-root is required for parent-of-repository")
    root = root_for(args.repo_root or args.session_root)
    origin = origin_for(root)
    session_root = pathlib.Path(args.session_root).expanduser().resolve() if args.session_root else root
    enrolled_parent = pathlib.Path(args.worktree_parent).expanduser().resolve() if args.worktree_parent else None
    if not validate_session_layout(root, session_root, enrolled_parent, args.session_layout, repository_root=root):
        raise RuntimeError("requested root does not match session layout")
    main_common = common_directory(root)
    if enrolled_parent is not None:
        if not enrolled_parent.is_dir():
            raise RuntimeError("worktree parent does not exist")
        for candidate in roots_under(enrolled_parent):
            if origin_for(candidate) != origin or common_directory(candidate) != main_common:
                raise RuntimeError("worktrees under parent are not linked to the registered repository")
    registry = pathlib.Path(args.registry).expanduser()
    lock = registry.with_suffix(registry.suffix + ".lock")

    def prepare_record():
        try:
            data = json.loads(registry.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except json.JSONDecodeError as exc:
            raise RuntimeError("registry JSON is invalid") from exc
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
            existing_layout = entry.get("session_layout", SESSION_LAYOUT_EXACT_ROOT)
            if existing_layout != args.session_layout:
                raise RuntimeError("session layout mismatch; refusing migration")
            previous = resolve_session_binding(entry)
            if previous["session_root"] != str(session_root) or previous["repository_root"] != str(root):
                raise RuntimeError("session root binding mismatch; refusing migration")
            if enrolled_parent and previous["enrolled_parent"] not in (None, str(enrolled_parent)):
                raise RuntimeError("worktree parent binding mismatch; refusing migration")
            sid = existing_sid
        else:
            sid = args.session_id
        if not isinstance(sid, str) or not sid:
            raise RuntimeError("--session-id is required; automatic session creation is not implicit")
        verify_session(sid, session_root)
        record = {
            "repository": origin,
            "repository_root": str(root),
            "repository_common_dir": main_common,
            "session_id": sid,
            "session_root": str(session_root),
            "session_layout": args.session_layout,
            "model": "SWE-2 High",
        }
        if enrolled_parent:
            record["enrolled_parent"] = str(enrolled_parent)
        data[origin] = {**(entry or {}), **record}
        # Reject duplicate session references across origins.
        refs = [v.get("session_id") for k, v in data.items() if isinstance(v, dict) and k != origin]
        if sid in refs:
            raise RuntimeError("session is already referenced by another origin")
        return data, sid

    if args.check_only:
        _, sid = prepare_record()
    else:
        fd = acquire(lock)
        try:
            data, sid = prepare_record()
            write_atomic(registry, data)
        finally:
            os.close(fd)
            lock.unlink(missing_ok=True)
    print(json.dumps({"schema": "devin-session-enrollment/v1", "status": "ready" if args.check_only else "enrolled",
                      "repository": origin, "repository_root": str(root), "session_root": str(session_root),
                      "session_layout": args.session_layout, "session_id": sid}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, BindingError, subprocess.SubprocessError) as exc:
        print(json.dumps({"schema": "devin-session-enrollment/v1", "status": "failed", "error": str(exc)}))
        raise SystemExit(2)
