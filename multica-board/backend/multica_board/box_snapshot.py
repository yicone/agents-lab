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
    payload.update(sources=doc.get("sources"), domain_states=doc.get("domain_states"),
                   domain_counts={name: len(rows) if isinstance(rows, list)
                                  else rows.get("state") if isinstance(rows, dict) else None
                                  for name, rows in domains.items()})
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
