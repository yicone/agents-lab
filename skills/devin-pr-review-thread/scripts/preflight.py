#!/usr/bin/env python3
"""Read-only, machine-readable preflight for the Devin review adapter."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
from typing import Any

from repository_binding import BindingError, load_origin_entry, normalize_origin, validate_worktree_boundary

SCHEMA = "devin-pr-review/preflight-v1"
MODEL_UID = "swe-2-high"
MODEL_LABEL = "SWE-2 High"
EXPIRY = dt.date(2026, 10, 26)


def run(argv: list[str], timeout: float = 12, cwd: str | None = None) -> tuple[int, str, str]:
    try:
        p = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, "", type(exc).__name__
    return p.returncode, p.stdout, p.stderr


def command_json(argv: list[str], cwd: str | None = None) -> tuple[Any | None, str | None]:
    code, out, err = run(argv, cwd=cwd)
    if code != 0:
        return None, f"{argv[0]} exit={code}: {err.strip()[-500:]}"
    try:
        value = json.loads(out)
    except json.JSONDecodeError:
        return None, f"{argv[0]} invalid_json: {out[-500:]!r} {err[-300:]!r}"
    return value, None


def canonical_root(repo_root: str) -> tuple[str | None, str | None]:
    code, out, err = run(["git", "-C", repo_root, "rev-parse", "--show-toplevel"])
    if code:
        return None, f"git root failed: {err.strip()[-500:]}"
    return str(pathlib.Path(out.strip()).resolve()), None


def origin_name(root: str) -> str | None:
    code, out, _ = run(["git", "-C", root, "config", "--get", "remote.origin.url"])
    if code or not out.strip():
        return None
    try:
        return normalize_origin(out.strip())
    except BindingError:
        return None


def classify_github_error(code: int, stderr: str) -> str:
    text = stderr.lower()
    transport_markers = ("eof", "timeout", "timed out", "connection", "tls", "ssl", "proxy", "network", "no such host", "temporary failure")
    if code == 127 or any(marker in text for marker in transport_markers):
        return "github_transport_unavailable"
    return "pr_not_found_or_forbidden"


def pr_identity(root: str, number: int, origin: str | None) -> tuple[dict[str, Any], str | None, str | None]:
    result = {"number": number, "head_sha": None}
    if not origin:
        return result, "origin_identity_failed", None
    code, out, err = run(["gh", "pr", "view", str(number), "--repo", origin, "--json", "headRefOid"])
    if code != 0:
        return result, classify_github_error(code, err), err.strip()[-500:]
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return result, "github_response_invalid", out[-500:]
    if not isinstance(data, dict) or not data.get("headRefOid"):
        return result, "github_response_invalid", repr(data)[-500:]
    result["head_sha"] = data["headRefOid"]
    return result, None, None


def model_state() -> tuple[dict[str, Any], str | None]:
    data, error = command_json(["devin", "models", "list", "--format", "json"])
    result = {"label": None, "model_uid": None, "expires_on": EXPIRY.isoformat(), "available": False}
    if error:
        return result, "model_output_invalid"
    if not isinstance(data, dict) or not isinstance(data.get("families"), list):
        return result, "model_output_invalid"
    matches = [v for family in data["families"] if isinstance(family, dict) and isinstance(family.get("variants"), list)
               for v in family["variants"] if isinstance(v, dict) and v.get("model_uid") == MODEL_UID]
    exact = [v for v in matches if v.get("label") == MODEL_LABEL]
    if exact:
        result.update(label=MODEL_LABEL, model_uid=MODEL_UID, available=dt.date.today() <= EXPIRY)
        return result, None if result["available"] else "model_expired"
    return result, "model_unavailable"


def trust_state(root: str, trust_file: pathlib.Path, additional_paths: set[str] | None = None) -> dict[str, Any]:
    result = {"state": "unknown", "source": str(trust_file)}
    try:
        data = json.loads(trust_file.read_text())
        paths = {str(pathlib.Path(p).resolve()) for p in data.get("trusted_paths", [])}
    except (OSError, json.JSONDecodeError, AttributeError):
        return result
    paths |= {str(pathlib.Path(path).resolve()) for path in (additional_paths or set())}
    trusted = root in paths
    if not trusted and pathlib.Path(root, ".git").exists():
        # Devin stores trust for the main workspace. A Git worktree below a
        # trusted workspace inherits that trust, while ordinary subdirectories
        # do not qualify because they lack a .git marker.
        trusted = any(pathlib.Path(root).is_relative_to(pathlib.Path(path)) for path in paths)
    result["state"] = "trusted" if trusted else "untrusted"
    return result


def session_state(root: str, origin: str | None, registry: pathlib.Path) -> tuple[dict[str, Any], str | None]:
    empty = {"id": None, "registry_state": "missing", "list_state": "unknown", "root": None}
    try:
        data = json.loads(registry.read_text())
    except (OSError, json.JSONDecodeError):
        return empty, "session_registry_missing"
    if not isinstance(data, dict):
        return empty, "session_output_invalid"
    if not origin:
        return empty, "session_registry_missing"
    try:
        entry = load_origin_entry(data, origin)
    except BindingError as exc:
        return empty, "session_registry_missing" if "missing" in str(exc) else "session_output_invalid"
    sid = entry.get("session_id")
    result = {"id": sid, "registry_state": "present", "list_state": "unknown", "root": None, "enrolled_parent": None}
    registered_root = entry.get("session_root")
    if not isinstance(registered_root, str) or not registered_root:
        return result, "session_root_mismatch"
    registered_root = str(pathlib.Path(registered_root).resolve())
    sessions, error = command_json(["devin", "list", "--format", "json"], cwd=registered_root)
    if error:
        return result, "session_output_invalid"
    if not isinstance(sessions, list) or any(not isinstance(x, dict) for x in sessions):
        return result, "session_output_invalid"
    found = next((x for x in sessions if x.get("id") == sid), None)
    if not found:
        result["list_state"] = "missing"
        return result, "session_missing"
    result["list_state"] = "present"
    result["root"] = found.get("working_directory")
    if result["root"] and str(pathlib.Path(result["root"]).resolve()) != registered_root:
        return result, "session_root_mismatch"
    result["root"] = registered_root
    enrolled_parent = entry.get("enrolled_parent")
    if enrolled_parent is not None and not isinstance(enrolled_parent, str):
        return result, "session_root_mismatch"
    if not validate_worktree_boundary(root, registered_root, enrolled_parent):
        return result, "session_root_mismatch"
    result["enrolled_parent"] = enrolled_parent
    return result, None


def lock_state(session_id: str | None, lock_dir: pathlib.Path) -> dict[str, Any]:
    result = {"state": "none", "path": None, "pid": None, "holder_identity": None}
    if not session_id:
        return result
    path = lock_dir / f"{session_id}.lock"
    result["path"] = str(path)
    if not path.exists():
        return result
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        result["state"] = "ambiguous"
        return result
    result["pid"] = pid
    code, _, _ = run(["kill", "-0", str(pid)])
    # PID existence alone is deliberately not enough to prove ownership.
    result["state"] = "live_or_ambiguous" if code == 0 else "stale"
    result["holder_identity"] = "unverified"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Devin PR review preflight")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--registry", default=os.path.expanduser("~/.config/devin/pr-review-sessions.json"))
    parser.add_argument("--trusted-workspaces", default=os.path.expanduser("~/.local/share/devin/cli/trusted_workspaces.json"))
    parser.add_argument("--session-lock-dir", default=os.path.expanduser("~/.local/share/devin/cli/session_locks"))
    args = parser.parse_args()
    root, root_error = canonical_root(args.repo_root)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    out: dict[str, Any] = {
        "schema": SCHEMA, "status": "ok", "checked_at": now,
        "repository": {"canonical_root": root, "origin": None},
        "pr": {"number": args.pr, "head_sha": None},
        "model": {"label": None, "model_uid": None, "expires_on": EXPIRY.isoformat(), "available": None},
        "workspace_trust": {"state": "unknown", "source": args.trusted_workspaces},
        "session": {"id": None, "registry_state": "unknown", "list_state": "unknown", "root": None},
        "lock": {"state": "none", "path": None, "pid": None, "holder_identity": None},
        "runtime": {"desktop_or_acp_processes": []}, "capabilities": [], "diagnostics": [],
    }
    if root_error or not root:
        out["status"] = "repo_identity_failed"; out["diagnostics"].append(root_error or "missing root")
    else:
        out["repository"]["origin"] = origin_name(root)
        out["pr"], pr_error, pr_detail = pr_identity(root, args.pr, out["repository"]["origin"])
        if pr_error:
            out["status"] = pr_error
            out["diagnostics"].append({"code": pr_error, "detail": pr_detail})
        out["model"], model_error = model_state()
        if model_error and out["status"] == "ok": out["status"] = model_error
        out["session"], session_error = session_state(root, out["repository"]["origin"], pathlib.Path(args.registry))
        extra_trust = set()
        if isinstance(out["session"].get("enrolled_parent"), str):
            extra_trust.add(out["session"]["enrolled_parent"])
        out["workspace_trust"] = trust_state(root, pathlib.Path(args.trusted_workspaces), extra_trust)
        if session_error and out["status"] == "ok": out["status"] = session_error
        out["lock"] = lock_state(out["session"].get("id"), pathlib.Path(args.session_lock_dir))
        if out["workspace_trust"]["state"] != "trusted" and out["status"] == "ok": out["status"] = "workspace_untrusted"
        if out["lock"]["state"] == "live_or_ambiguous" and out["status"] == "ok": out["status"] = "session_lock_ambiguous"
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0 if out["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
