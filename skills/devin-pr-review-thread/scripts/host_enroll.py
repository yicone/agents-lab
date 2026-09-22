#!/usr/bin/env python3
"""Host-only enrollment for a repository allowed to use the Devin provider."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import tempfile

from repository_binding import BindingError, normalize_origin


def run(argv):
    process = subprocess.run(argv, text=True, capture_output=True, timeout=15)
    if process.returncode:
        raise RuntimeError(process.stderr.strip() or "command failed")
    return process.stdout.strip()


def origin_for(root):
    try:
        return normalize_origin(run(["git", "-C", str(root), "config", "--get", "remote.origin.url"]))
    except BindingError as exc:
        raise RuntimeError(str(exc)) from exc


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--repo-root")
    group.add_argument("--worktree-parent")
    parser.add_argument("--allowlist", default="~/.config/devin/provider-allowlist.json")
    args = parser.parse_args()

    if args.worktree_parent:
        parent = pathlib.Path(args.worktree_parent).expanduser().resolve()
        roots = []
        for current, dirs, _ in os.walk(parent):
            dirs[:] = [d for d in dirs if d != ".git"]
            try:
                candidate = pathlib.Path(current)
                discovered = pathlib.Path(run(["git", "-C", str(candidate), "rev-parse", "--show-toplevel"])).resolve()
                if discovered not in roots:
                    roots.append(discovered)
                    if discovered == candidate:
                        dirs[:] = []
                    else:
                        dirs[:] = [d for d in dirs if pathlib.Path(current, d).resolve() != discovered]
            except (OSError, RuntimeError, subprocess.SubprocessError):
                pass
        if not roots:
            raise RuntimeError("no Git worktree found under parent")
        origins = {origin_for(root) for root in roots}
        if len(origins) != 1:
            raise RuntimeError("worktrees under parent have different origins")
        root = roots[0]
    else:
        root = pathlib.Path(run(["git", "-C", args.repo_root, "rev-parse", "--show-toplevel"])).resolve()

    origin = origin_for(root)
    if origin.count("/") != 1:
        raise RuntimeError("origin identity is ambiguous")

    allowlist_path = pathlib.Path(args.allowlist).expanduser()
    allowlist_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        data = json.loads(allowlist_path.read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        raise RuntimeError("allowlist is not an object")

    key = (
        str(pathlib.Path(args.worktree_parent).expanduser().resolve()) + "/*"
        if args.worktree_parent
        else str(root)
    )
    existing = data.get(key)
    if existing and existing != origin:
        raise RuntimeError("repository is already bound to a different origin")
    data[key] = origin

    descriptor, temporary = tempfile.mkstemp(prefix=f".{allowlist_path.name}.", dir=allowlist_path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, allowlist_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    print(json.dumps({
        "schema": "devin-host-enrollment/v1",
        "status": "enrolled",
        "repository_root": key,
        "origin": origin,
    }))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({
            "schema": "devin-host-enrollment/v1",
            "status": "failed",
            "error": str(exc),
        }))
        raise SystemExit(2)
