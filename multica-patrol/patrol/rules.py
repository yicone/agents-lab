"""Pure finding rules: (issue, facts, now) -> findings. No I/O."""

from __future__ import annotations

from . import config
from .facts import parse_ts


def _labels(issue: dict) -> set[str]:
    out = set()
    for label in issue.get("labels") or []:
        if isinstance(label, dict):
            out.add(str(label.get("name", "")))
        else:
            out.add(str(label))
    return out


def _failed_run_finding(facts: dict, now) -> tuple[bool, str | None, str | None]:
    """Trailing-failed-run analysis over newest-first runs (cancelled ignored).

    Returns (fires, anchor_run_id, newest_failed_completed_iso).
    """
    runs = [r for r in facts.get("runs") or [] if r.get("status") != "cancelled"]
    if not runs:
        return False, None, None
    streak = 0
    newest_failed = None
    for run in runs:
        if run.get("status") == "failed":
            if newest_failed is None:
                newest_failed = run
            streak += 1
        else:
            break
    if newest_failed is None:
        return False, None, None
    newest = runs[0]
    fired = streak >= config.FAILED_RUN_MIN_STREAK or (
        newest.get("status") == "failed"
        and (newest.get("attempt") or 0) >= (newest.get("max_attempts") or 1)
    )
    if not fired:
        return False, None, iso_completed(newest_failed)
    completed = parse_ts(newest_failed.get("completed_at"))
    if completed is None or (now - completed).total_seconds() < config.FAILED_RUN_GRACE_S:
        return False, None, iso_completed(newest_failed)
    return True, newest_failed.get("id"), iso_completed(newest_failed)


def iso_completed(run) -> str | None:
    return run.get("completed_at")


def evaluate(issue: dict, facts: dict, now) -> list[dict]:
    """Return raw findings for one issue. facts fields are ISO strings."""
    findings = []
    status = issue.get("status")
    assignee = issue.get("assignee_id")
    entered = parse_ts(facts.get("entered_status_at"))
    last_progress = parse_ts(facts.get("last_progress_at"))
    active = bool(facts.get("active_runs"))

    def add(reason, anchor, **extra):
        findings.append({"reason": reason, "anchor": anchor, **extra})

    if status == "in_progress" and assignee and not active and last_progress and \
            (now - last_progress).total_seconds() >= config.PROGRESS_STALE_S:
        add("in_progress_no_active_run", facts.get("last_progress_at"))

    if status == "in_review" and entered and \
            (now - entered).total_seconds() >= config.REVIEW_STALE_S:
        add("in_review_stale", facts.get("entered_status_at"))

    if status == "blocked" and entered and \
            (now - entered).total_seconds() >= config.BLOCKED_STALE_S:
        add("blocked_stale", facts.get("entered_status_at"))

    approval_at = parse_ts(facts.get("approval_wait_at"))
    if status in ("in_progress", "in_review") and approval_at and \
            (now - approval_at).total_seconds() >= config.APPROVAL_WAIT_S:
        add("approval_not_blocked", facts.get("last_progress_at"))

    if status in ("todo", "backlog") and not assignee and not active and \
            _labels(issue) & config.APPROVED_LABELS:
        add("approved_unassigned", facts.get("last_progress_at"),
            note="label-based only; natural-language approval judgement belongs to the daily analysis, not the machine")

    if assignee and status not in ("done", "cancelled") and not active:
        fired, run_id, _ = _failed_run_finding(facts, now)
        if fired:
            add("run_failed_no_retry", run_id)

    if status == "in_progress" and not assignee and entered and \
            (now - entered).total_seconds() >= config.NO_ASSIGNEE_GRACE_S:
        add("in_progress_no_assignee", facts.get("entered_status_at"))

    return findings


def exemption_for(issue: dict, findings: list[dict], facts: dict, now) -> list[dict]:
    """Annotate each finding with exemption info. Returns the same list."""
    identifier = issue.get("identifier", "")
    for finding in findings:
        finding["exempt"] = None
    if issue.get("id") in config.PERSISTENT_IDS:
        for finding in findings:
            finding["exempt"] = {"kind": "persistent"}
        return findings
    if identifier in config.SHELVED:
        for finding in findings:
            finding["exempt"] = {"kind": "shelved"}
        return findings
    for finding in findings:
        if finding["reason"] in ("in_progress_no_active_run", "in_review_stale", "blocked_stale"):
            wakeup = _enabled_wakeup(facts, now)
            if wakeup:
                finding["exempt"] = {"kind": "awaiting_wakeup", "expires_at": wakeup.get("expires_at")}
    return findings


def _enabled_wakeup(facts: dict, now) -> dict | None:
    for wakeup in facts.get("wakeups") or []:
        if not wakeup.get("enabled"):
            continue
        expires = parse_ts(wakeup.get("expires_at"))
        if expires is None or expires > now:
            return wakeup
    return None
