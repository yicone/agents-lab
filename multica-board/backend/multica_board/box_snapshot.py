"""Read the Mini-local box snapshot copy for ``GET /api/box-config``.

No subprocess, no network (design §5.6.6): the board only reads private
files written by ``boxsync fetch-snapshot`` — the snapshot copy,
``fetch-status.json`` and ``git-leg.json`` (the frozen ``origin/main``
workflows leg from the last successful fetch) — then reconciles in-process
with the pure ``boxsync.reconcile.reconcile``. When box-config-sync is not
deployed alongside the board, the endpoint degrades to
``source_state: absent``, never 500.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Same repository bridge as cloud_skill_content; deployment may omit box-config-sync.
_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "box-config-sync"
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

BOX_ID = "grok-box"
MAX_AGE = 36 * 3600
GIT_LEG_MAX_BYTES = 4 * 1024 * 1024
MAX_LEG_ITEMS = 2000
_BOX_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _fetch_status(state_dir: Path) -> dict[str, Any] | None:
    """Tolerant read of fetch-status.json; a bad status is ignored, not fatal."""
    try:
        from boxsync.safe_io import read_file
    except ImportError:
        return None
    result = read_file(state_dir, "fetch-status.json", max_bytes=64 * 1024)
    if result.state != "observed":
        return None
    try:
        doc = json.loads(result.data)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != "box-config-fetch-status/v1":
        return None
    return {key: doc.get(key) for key in ("state", "reason_code", "checked_at", "verified_commit")}


def _git_leg(state_dir: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return ``(leg, summary)`` for reconcile plus the API's ``git_leg`` block.

    Missing or unreadable -> ``not_collected``; a present-but-invalid document
    -> ``error``. Both make every workflow row ``unknown`` inside reconcile —
    never a fabricated ``missing_*``/``aligned``.
    """
    from boxsync.safe_io import read_file
    from boxsync.validation import InvalidDocument, parse_json

    absent = ({"state": "not_collected", "items": []}, None)
    result = read_file(state_dir, "git-leg.json", max_bytes=GIT_LEG_MAX_BYTES)
    if result.state != "observed":
        return absent
    try:
        doc = parse_json(result.data)
    except InvalidDocument:
        return ({"state": "error", "items": []},
                {"state": "error", "commit": None, "observed_at": None})
    if (not isinstance(doc, dict) or doc.get("schema") != "box-config-git-leg/v1"
            or not isinstance(doc.get("state"), str)
            or not isinstance(doc.get("items"), list)
            or len(doc["items"]) > MAX_LEG_ITEMS
            or any(not isinstance(row, dict) for row in doc["items"])):
        return ({"state": "error", "items": []},
                {"state": "error", "commit": None, "observed_at": None})
    leg = {"state": doc["state"], "items": doc["items"]}
    commit = doc.get("commit")
    summary = {"state": doc["state"],
               "commit": commit if isinstance(commit, str) else None,
               "observed_at": doc.get("observed_at")}
    return leg, summary


def read_box_snapshot(state_dir, box_id, now, max_age=MAX_AGE) -> tuple[Any, dict[str, Any]]:
    """Return ``(doc, info)``; ``doc`` is the validated copy or ``None``.

    ``info`` carries ``source_state`` (``rejected`` | ``absent`` | ``bad`` |
    ``stale`` | ``ok``), ``reason_code``, ``collected_at``, ``age_seconds``
    and the fetch-status summary under ``fetch``.
    """
    from boxsync.safe_io import UnsafeIO, read_file
    from boxsync.snapshot import MAX_SNAPSHOT_BYTES, validate_snapshot
    from boxsync.validation import InvalidDocument, parse_json

    state_dir = Path(state_dir)
    info: dict[str, Any] = {"source_state": "absent", "reason_code": None,
                            "collected_at": None, "age_seconds": None,
                            "fetch": _fetch_status(state_dir)}
    doc = None
    copy_state, reason = "absent", None
    if _BOX_ID.fullmatch(box_id or ""):
        result = read_file(state_dir / "box-snapshots" / box_id, "snapshot.json",
                           max_bytes=MAX_SNAPSHOT_BYTES)
        if result.state == "observed":
            try:
                candidate = parse_json(result.data)
                validate_snapshot(candidate)
                doc = candidate
                copy_state = "ok"
            except UnsafeIO:
                copy_state, reason = "bad", "snapshot_secret"
            except (InvalidDocument, ValueError, UnicodeError):
                copy_state, reason = "bad", "invalid_snapshot"
        elif result.state != "absent":
            copy_state, reason = "bad", "snapshot_" + result.state
    if doc is not None:
        info["collected_at"] = doc.get("collected_at")
        stamp = _stamp(info["collected_at"])
        if stamp is not None:
            info["age_seconds"] = (now - stamp).total_seconds()
    fetch = info["fetch"]
    if fetch is not None and fetch.get("state") == "rejected":
        info["source_state"] = "rejected"
        info["reason_code"] = fetch.get("reason_code")
    elif copy_state == "absent":
        info["source_state"] = "absent"
    elif copy_state != "ok":
        info["source_state"] = "bad"
        info["reason_code"] = reason
    elif info["age_seconds"] is not None and info["age_seconds"] > max_age:
        info["source_state"] = "stale"
    else:
        info["source_state"] = "ok"
    return doc, info


# Uncollected, missing, and failed inventories are not a number (including not 0).
_NO_NUMBER_STATES = frozenset({"not_collected", "absent", "denied", "error"})


def _presented_domain_states(domains: dict[str, Any], domain_states):
    """Copy snapshot states for the response.

    A snapshot collected before hooks was marked not_collected can still say
    ok while domains.hooks.state is not_collected_on_box. The response uses
    not_collected so the state matches the count. The snapshot file is not
    rewritten.
    """
    if not isinstance(domain_states, dict):
        return domain_states
    states = dict(domain_states)
    hooks = domains.get("hooks") if isinstance(domains, dict) else None
    if (isinstance(hooks, dict) and hooks.get("state") == "not_collected_on_box"
            and states.get("hooks") == "ok"):
        states["hooks"] = "not_collected"
    if isinstance(domains, dict) and "mcps" in states:
        states["mcps"], _ = _present_mcps(domains.get("mcps"), states.get("mcps"))
    return states


def _present_mcps(rows, state):
    """Response-only mcps state and count. The snapshot file is not rewritten.

    Turn placeholders use evidence_level not_collected and are not an
    inventory. not_collected/absent/denied/error stay unnumbered. Mixed
    evidence counts only collected rows; ok becomes partial while any
    placeholder remains. A partial snapshot with no collected row (settings
    read failed, or mcpBoxServers unusable) stays partial and counts null.
    """
    if state in _NO_NUMBER_STATES or state is None:
        return state, None
    if not isinstance(rows, list) or state not in ("ok", "partial"):
        return state, None
    collected = sum(1 for row in rows
                    if not (isinstance(row, dict) and row.get("evidence_level") == "not_collected"))
    placeholders = len(rows) - collected
    if collected == 0:
        if placeholders and state == "ok":
            return "not_collected", None
        if state == "partial":
            return "partial", None
        return "ok", 0
    if state == "ok" and placeholders:
        return "partial", collected
    return state, collected


def _domain_count(rows, state, *, states_present: bool, name: str | None = None):
    if name == "mcps" and states_present:
        _, count = _present_mcps(rows, state)
        return count
    if isinstance(rows, dict) and str(rows.get("state", "")).startswith("not_collected"):
        return None
    if not states_present:
        return len(rows) if isinstance(rows, list) else None
    if state in _NO_NUMBER_STATES or state is None:
        return None
    if state in ("ok", "partial") and isinstance(rows, list):
        return len(rows)
    return None


def _domain_counts(domains: dict[str, Any], domain_states) -> dict[str, Any]:
    states_present = isinstance(domain_states, dict)
    return {name: _domain_count(rows, domain_states.get(name) if states_present else None,
                                states_present=states_present, name=name)
            for name, rows in domains.items()}


def _workflow_item(row: dict[str, Any]) -> dict[str, Any]:
    """Pass through summary keys; large per-file diff lists are dropped."""
    keys = ("id", "dir_name", "kind", "name", "state", "reason_codes",
            "join_basis", "duplicate_name", "name_changed",
            "export_state", "export_side")
    return {key: row.get(key) for key in keys}


def build_box_config(*, state_dir=None, box_id=BOX_ID, now=None,
                     max_age=MAX_AGE) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    payload: dict[str, Any] = {"schema": "box-config-api/v1",
                               "generated_at": _iso(now), "box_id": box_id}
    try:
        from boxsync.reconcile import reconcile
        from boxsync.roots import MINI_STATE_DIR
    except ImportError:
        payload.update(source_state="absent", reason_code=None, collected_at=None,
                       age_seconds=None, fetch=None, sources=None, domain_states=None,
                       domain_counts=None, workflows=None, git_leg=None)
        return payload
    state_dir = Path(state_dir) if state_dir is not None else MINI_STATE_DIR
    doc, info = read_box_snapshot(state_dir, box_id, now, max_age)
    payload.update(source_state=info["source_state"], reason_code=info["reason_code"],
                   collected_at=info["collected_at"], age_seconds=info["age_seconds"],
                   fetch=info["fetch"])
    if doc is None:
        payload.update(sources=None, domain_states=None, domain_counts=None,
                       workflows=None, git_leg=None)
        return payload
    domains = doc.get("domains") or {}
    domain_states = _presented_domain_states(domains, doc.get("domain_states"))
    payload.update(sources=doc.get("sources"), domain_states=domain_states,
                   domain_counts=_domain_counts(domains, domain_states))
    leg, leg_summary = _git_leg(state_dir)
    payload["git_leg"] = leg_summary
    try:
        report = reconcile(doc, leg, None, now=now, max_age=max_age)
        payload["workflows"] = {
            "summary": report["summary"],
            "items": [_workflow_item(row) for row in report["domains"]["workflows"]]}
    except Exception:
        # Reconcile is pure; still never 500 on an unexpected document.
        payload["workflows"] = {"summary": {"unknown": 1}, "items": []}
    return payload
