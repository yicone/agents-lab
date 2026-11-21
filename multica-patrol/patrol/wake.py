"""Wake the dispatcher under a posted escalation comment.

The escalation body stays a dispatcher-agent comment with no agent/member
mention. A separate subprocess, using the Steward profile and no task
credentials, replies under that comment. Read-back still uses the task CLI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path

from . import config
from .cli import CliError
from .facts import iso
from .ledger import read_back


def wake_marker_for(escalation_marker: str) -> str:
    prefix = config.ESCALATION_PREFIX
    if escalation_marker.startswith(prefix) and escalation_marker.endswith("]"):
        return config.WAKE_PREFIX + escalation_marker[len(prefix):]
    token = escalation_marker.strip("[]")
    return f"{config.WAKE_PREFIX}{token}]"


def render_wake(escalation_marker: str) -> tuple[str, str]:
    marker = wake_marker_for(escalation_marker)
    mention = f"[@副调度](mention://agent/{config.DISPATCHER_AGENT_ID})"
    return marker, f"{mention}\n{marker}\n"


def isolated_env(base: dict | None) -> dict:
    source = os.environ if base is None else base
    return {key: value for key, value in source.items() if not key.startswith("MULTICA_")}


def _reaches_user_multica(path: Path, home: Path, multica_dir: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return True
    for parent in (resolved, *resolved.parents):
        if parent == home or parent == multica_dir:
            return True
        candidate = parent / ".multica"
        if not candidate.exists():
            continue
        try:
            if candidate.resolve() == multica_dir:
                return True
        except OSError:
            return True
    return False


def isolated_cwd() -> Path:
    """A temp directory whose parents do not walk up to ~/.multica."""
    home = Path.home().resolve()
    multica_dir = (home / ".multica").resolve()
    bases: list[Path] = []
    for candidate in (Path(tempfile.gettempdir()), Path("/tmp")):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved not in bases:
            bases.append(resolved)
    for base in bases:
        if _reaches_user_multica(base, home, multica_dir):
            continue
        try:
            path = Path(tempfile.mkdtemp(prefix="patrol-steward-wake-", dir=str(base)))
        except OSError:
            continue
        if not _reaches_user_multica(path, home, multica_dir):
            return path
        shutil.rmtree(path, ignore_errors=True)
    raise OSError("no cwd isolated from ~/.multica")


def _remember_pending(state: dict, wake_marker: str, parent_id: str | None,
                      escalation_marker: str) -> None:
    state.setdefault("pending_wakes", {})[wake_marker] = {
        "parent_id": parent_id,
        "escalation_marker": escalation_marker,
    }


def _clear_pending(state: dict, wake_marker: str) -> None:
    (state.get("pending_wakes") or {}).pop(wake_marker, None)


def _judge_existing(state, comment: dict, wake_marker: str, now_iso: str) -> dict:
    author_ok = (
        comment.get("author_type") == "member"
        and comment.get("author_id") == config.STEWARD_USER_ID
    )
    if not author_ok:
        return {
            "marker": wake_marker,
            "status": "author_mismatch",
            "comment_id": comment.get("id"),
            "reason": "wake comment author_id is not the Steward user_id",
        }
    state.setdefault("steward_wakes", {})[wake_marker] = now_iso
    _clear_pending(state, wake_marker)
    return {
        "marker": wake_marker,
        "status": "posted",
        "comment_id": comment.get("id"),
        "reason": None,
    }


def post_steward_wake(store, cli, state: dict, parent_id: str | None,
                      escalation_marker: str, now) -> dict:
    """Reply under the escalation comment once. Never uses run_write."""
    wake_marker, body = render_wake(escalation_marker)
    if (state.get("steward_wakes") or {}).get(wake_marker):
        _clear_pending(state, wake_marker)
        return {
            "marker": wake_marker, "status": "skipped",
            "comment_id": None, "reason": "already_woken",
        }
    since = now - timedelta(seconds=config.DUP_LOOKBACK_S)
    try:
        existing = read_back(cli, config.INBOX_ISSUE_ID, wake_marker, since)
    except CliError as exc:
        _remember_pending(state, wake_marker, parent_id, escalation_marker)
        store.save(state)
        return {
            "marker": wake_marker, "status": "failed",
            "comment_id": None, "reason": f"read_back: {exc}",
        }
    if existing:
        outcome = _judge_existing(state, existing, wake_marker, iso(now))
        if outcome["status"] != "posted":
            _remember_pending(state, wake_marker, parent_id, escalation_marker)
        store.save(state)
        return outcome
    if not parent_id:
        _remember_pending(state, wake_marker, parent_id, escalation_marker)
        store.save(state)
        return {
            "marker": wake_marker, "status": "failed",
            "comment_id": None, "reason": "missing_parent",
        }
    cwd = None
    try:
        cwd = isolated_cwd()
        path = cwd / "wake.md"
        path.write_text(body, encoding="utf-8")
        path.chmod(0o600)
        argv = [
            cli.binary, "--profile", config.STEWARD_PROFILE,
            "--workspace-id", config.WORKSPACE_ID,
            "issue", "comment", "add", config.INBOX_ISSUE_ID,
            "--parent", parent_id,
            "--content-file", str(path),
            "--allow-external-file",
            "--output", "json",
        ]
        try:
            cli._exec(
                argv,
                capture_output=True,
                text=True,
                timeout=cli.timeout_s,
                env=isolated_env(cli.env),
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
    except OSError as exc:
        _remember_pending(state, wake_marker, parent_id, escalation_marker)
        store.save(state)
        return {
            "marker": wake_marker, "status": "failed",
            "comment_id": None, "reason": f"isolated_cwd: {exc}",
        }
    finally:
        if cwd is not None:
            shutil.rmtree(cwd, ignore_errors=True)
    try:
        found = read_back(cli, config.INBOX_ISSUE_ID, wake_marker, since)
    except CliError as exc:
        _remember_pending(state, wake_marker, parent_id, escalation_marker)
        store.save(state)
        return {
            "marker": wake_marker, "status": "failed",
            "comment_id": None, "reason": f"read_back: {exc}",
        }
    if not found:
        _remember_pending(state, wake_marker, parent_id, escalation_marker)
        store.save(state)
        return {
            "marker": wake_marker, "status": "failed",
            "comment_id": None, "reason": "wake comment not found on read-back",
        }
    outcome = _judge_existing(state, found, wake_marker, iso(now))
    if outcome["status"] != "posted":
        _remember_pending(state, wake_marker, parent_id, escalation_marker)
    store.save(state)
    return outcome


def replay_pending_wakes(store, cli, state: dict, now) -> list[dict]:
    """Retry wakes that did not verify on an earlier run. One attempt each."""
    outcomes = []
    pending = list((state.get("pending_wakes") or {}).items())
    for _marker, rec in pending:
        outcome = post_steward_wake(
            store, cli, state, rec.get("parent_id"),
            rec.get("escalation_marker") or "", now)
        if outcome.get("status") != "skipped":
            outcomes.append(outcome)
    return outcomes
