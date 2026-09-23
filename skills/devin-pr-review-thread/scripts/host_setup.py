#!/usr/bin/env python3
"""Host-only one-time setup for the Devin PR review provider."""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parent


def run_step(script: str, args: list[str]) -> dict:
    process = subprocess.run(
        [sys.executable, str(ROOT / script), *args],
        text=True,
        capture_output=True,
        timeout=120,
    )
    try:
        result = json.loads(process.stdout)
    except json.JSONDecodeError:
        result = {"status": "failed", "error": process.stderr.strip() or "invalid step output"}
    if process.returncode != 0 or result.get("status") != "enrolled":
        raise RuntimeError(f"{script}: {result.get('error') or result.get('status') or 'failed'}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, help="canonical main workspace")
    parser.add_argument("--worktree-parent", help="parent containing sibling/nested worktrees")
    parser.add_argument("--session-id", required=True, help="existing Devin session; never created implicitly")
    parser.add_argument("--session-root", help="registered Devin workspace; defaults to --repo-root")
    parser.add_argument("--allowlist", default="~/.config/devin/provider-allowlist.json")
    parser.add_argument("--registry", default="~/.config/devin/pr-review-sessions.json")
    args = parser.parse_args()

    repo_root = str(pathlib.Path(args.repo_root).expanduser().resolve())
    session_root = str(pathlib.Path(args.session_root or repo_root).expanduser().resolve())
    allowlist = str(pathlib.Path(args.allowlist).expanduser())
    registry = str(pathlib.Path(args.registry).expanduser())
    steps = []

    # Keep exact main-root and worktree-parent enrollment explicit. The
    # wildcard covers future worktrees without re-enrolling each child.
    steps.append(run_step("host_enroll.py", ["--repo-root", repo_root, "--allowlist", allowlist]))
    if args.worktree_parent:
        parent = str(pathlib.Path(args.worktree_parent).expanduser().resolve())
        steps.append(run_step("host_enroll.py", ["--worktree-parent", parent, "--allowlist", allowlist]))
        steps.append(run_step("session_enroll.py", [
            "--worktree-parent", parent,
            "--session-root", session_root,
            "--session-id", args.session_id,
            "--registry", registry,
        ]))
    else:
        steps.append(run_step("session_enroll.py", [
            "--repo-root", repo_root,
            "--session-root", session_root,
            "--session-id", args.session_id,
            "--registry", registry,
        ]))

    print(json.dumps({
        "schema": "devin-host-setup/v1",
        "status": "enrolled",
        "repository_root": repo_root,
        "session_root": session_root,
        "worktree_parent": str(pathlib.Path(args.worktree_parent).expanduser().resolve()) if args.worktree_parent else None,
        "session_id": args.session_id,
        "steps": steps,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"schema": "devin-host-setup/v1", "status": "failed", "error": str(exc)}))
        raise SystemExit(2)
