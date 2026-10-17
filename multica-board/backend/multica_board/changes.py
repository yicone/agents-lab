"""Incremental change log policy for GET /api/changes.

The first successful snapshot of a resource is a baseline and emits nothing.
Later collects append only the deltas the contract lists. Retention is 7 days.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

CHANGE_RETENTION_DAYS = 7
CHANGES_PAGE = 500
SINCE_ERROR = "since must be an ISO-8601 UTC timestamp or a cursor"
KIND_ERROR = "kind must be status, issue_status, assignee, issue_assignee, run, run_failed, or runtime"
# `status` / `assignee` are the short names the decide page uses. The stored kinds stay explicit.
KIND_FILTERS = {
    "status": "issue_status",
    "issue_status": "issue_status",
    "assignee": "issue_assignee",
    "issue_assignee": "issue_assignee",
    "run": "run",
    "run_failed": "run_failed",
    "runtime": "runtime",
}
_CURSOR = re.compile(r"^v1:(0|[1-9][0-9]{0,17})$")


class BadRequest(Exception):
    pass


def format_cursor(seq: int) -> str:
    return f"v1:{seq}"


def parse_kind(raw: str | None) -> str | None:
    """Map `?kind=` onto a stored change kind. Omitted means every kind."""
    if raw is None:
        return None
    stored = KIND_FILTERS.get(raw)
    if stored is None:
        raise BadRequest(KIND_ERROR)
    return stored


def parse_since(raw: str | None) -> tuple[str, Any]:
    """Return ("none", None), ("seq", int), or ("time", UTC second stamp)."""
    if raw is None:
        return "none", None
    if not isinstance(raw, str) or not raw or raw.strip() != raw or len(raw) > 40:
        raise BadRequest(SINCE_ERROR)
    matched = _CURSOR.match(raw)
    if matched:
        return "seq", int(matched.group(1))
    stamp = _parse_utc(raw)
    if stamp is None:
        raise BadRequest(SINCE_ERROR)
    return "time", stamp


def diff_issues(old_rows: list[dict[str, Any]], new_rows: list[dict[str, Any]], at: str) -> list[dict[str, Any]]:
    """Status and assignee changes for issues already in the previous snapshot."""
    old = {r["id"]: r for r in old_rows if r.get("id")}
    new = {r["id"]: r for r in new_rows if r.get("id")}
    events: list[dict[str, Any]] = []
    for iid in sorted(new):
        prev = old.get(iid)
        if prev is None:
            continue
        row = new[iid]
        if (prev.get("status"), prev.get("status_category")) != (row.get("status"), row.get("status_category")):
            events.append(
                _event(
                    at,
                    "issue_status",
                    iid,
                    {
                        "identifier": row.get("identifier"),
                        "status": row.get("status"),
                        "status_category": row.get("status_category"),
                        "previous_status": prev.get("status"),
                        "previous_status_category": prev.get("status_category"),
                    },
                )
            )
        if (prev.get("assignee_type"), prev.get("assignee_id")) != (row.get("assignee_type"), row.get("assignee_id")):
            events.append(
                _event(
                    at,
                    "issue_assignee",
                    iid,
                    {
                        "identifier": row.get("identifier"),
                        "assignee_type": row.get("assignee_type"),
                        "assignee_id": row.get("assignee_id"),
                        "previous_assignee_type": prev.get("assignee_type"),
                        "previous_assignee_id": prev.get("assignee_id"),
                    },
                )
            )
    return events


def diff_runtimes(old_rows: list[dict[str, Any]], new_rows: list[dict[str, Any]], at: str) -> list[dict[str, Any]]:
    """Online/offline transitions. `online` is the only online value; leaving the snapshot is null."""
    old = {r["id"]: r for r in old_rows if r.get("id")}
    new = {r["id"]: r for r in new_rows if r.get("id")}
    events: list[dict[str, Any]] = []
    for rid in sorted(new):
        row = new[rid]
        prev = old.get(rid)
        if prev is None:
            if row.get("status") == "online":
                events.append(_runtime_event(at, rid, row, status=row.get("status"), previous=None))
            continue
        if (prev.get("status") == "online") != (row.get("status") == "online"):
            events.append(_runtime_event(at, rid, row, status=row.get("status"), previous=prev.get("status")))
    for rid in sorted(set(old) - set(new)):
        prev = old[rid]
        if prev.get("status") == "online":
            events.append(_runtime_event(at, rid, prev, status=None, previous=prev.get("status")))
    return events


def events_for_tasks(rows: list[dict[str, Any]], existing: dict[str, str | None], at: str) -> list[dict[str, Any]]:
    """New task ids emit `run` (and `run_failed` if already failed). A later transition into failed emits `run_failed`."""
    seen = dict(existing)
    events: list[dict[str, Any]] = []
    for row in rows:
        tid = row.get("id")
        if not tid:
            continue
        status = row.get("status")
        if tid not in seen:
            events.append(_event(at, "run", tid, _task_payload(row)))
            if status == "failed":
                events.append(_event(at, "run_failed", tid, _task_payload(row)))
        elif seen[tid] != "failed" and status == "failed":
            events.append(_event(at, "run_failed", tid, _task_payload(row)))
        seen[tid] = status
    return events


def _event(at: str, kind: str, resource_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"at": at, "kind": kind, "resource_id": resource_id, "payload": payload}


def _runtime_event(at: str, rid: str, row: dict[str, Any], *, status: str | None, previous: str | None) -> dict[str, Any]:
    return _event(
        at,
        "runtime",
        rid,
        {"name": row.get("custom_name") or row.get("name"), "status": status, "previous_status": previous},
    )


def _task_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue_id": row.get("issue_id"),
        "agent_id": row.get("agent_id"),
        "runtime_id": row.get("runtime_id"),
        "status": row.get("status"),
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
        "error_kind": row.get("error_kind"),
        "error_summary": row.get("error_summary"),
    }


def _parse_utc(raw: str) -> str | None:
    text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
