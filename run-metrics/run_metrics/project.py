"""Project one raw Multica run into run-metrics.v0.1."""

from __future__ import annotations

from . import RULE_VERSION, SCHEMA_VERSION
from .redact import redact
from .rules import candidate_ids, classify_failure, recovery_at_from_text
from .timeutil import iso, parse_ts

_STATUS_MAP = {
    "queued": "queued",
    "waiting_local_directory": "queued",
    "dispatched": "running",
    "running": "running",
    "completed": "succeeded",
    "succeeded": "succeeded",
    "success": "succeeded",
    "failed": "failed",
    "error": "failed",
    "blocked": "blocked",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}
_PARENT_KEYS = ("parent_run_id", "previous_run_id", "retried_from_run_id")


def map_status(raw_status: str | None) -> str:
    if raw_status is None:
        return "unknown"
    return _STATUS_MAP.get(str(raw_status).strip().lower(), "unknown")


def parent_run_id(raw: dict) -> str | None:
    for key in _PARENT_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def project_run(raw: dict) -> dict:
    raw_status = raw.get("status")
    mapped = map_status(raw_status if isinstance(raw_status, str) else None)
    error = raw.get("error") if isinstance(raw.get("error"), str) else None
    reason = raw.get("failure_reason") if isinstance(raw.get("failure_reason"), str) else None
    classification = classify_failure(mapped, raw_status if isinstance(raw_status, str) else None, error, reason)
    started = parse_ts(raw.get("started_at"))
    ended = parse_ts(raw.get("completed_at"))
    duration = None
    if started is not None and ended is not None and ended >= started:
        duration = int((ended - started).total_seconds() * 1000)
    failure_class = classification["failure_class"] if classification else None
    anchor = started or parse_ts(raw.get("created_at"))
    attempt = raw.get("attempt")
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": raw.get("id"),
        "agent_id": raw.get("agent_id"),
        "runtime_id": raw.get("runtime_id"),
        "workspace_id": raw.get("workspace_id"),
        "issue_id": raw.get("issue_id"),
        "created_at": iso(parse_ts(raw.get("created_at"))),
        "started_at": iso(started),
        "ended_at": iso(ended),
        "anchor_at": iso(anchor),
        "duration_ms": duration,
        "status": mapped,
        "raw_status": raw_status if isinstance(raw_status, str) else None,
        "failure_class": failure_class,
        "failure_rule_id": classification["failure_rule_id"] if classification else None,
        "failure_rule_version": RULE_VERSION if classification else None,
        "failure_evidence": classification["failure_evidence"] if classification else None,
        "failure_recoverable": classification["failure_recoverable"] if classification else None,
        "recovery_at": recovery_at_from_text(failure_class, error, reason),
        "raw_reason_redacted": redact(reason if reason else error, limit=180),
        "source": "multica issue runs",
        "attempt_no": attempt if isinstance(attempt, int) else None,
        "parent_run_id": parent_run_id(raw),
        "provider_candidate_ids": candidate_ids(error, reason),
    }
