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
    newest_failed = runs[0]
    if newest_failed.get("status") != "failed":
        return False, None, None
    # A terminal failure does not promise a retry merely because attempt < max.
    # Classify for routing, but even an unknown failure needs dispatcher judgement.
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
            failed_run = next((r for r in facts.get("runs") or [] if r.get("id") == run_id), {})
            add("run_failed_no_retry", run_id, error_kind=failed_run.get("error_kind") or "unknown")

    if status == "in_progress" and not assignee and entered and \
            (now - entered).total_seconds() >= config.NO_ASSIGNEE_GRACE_S:
        add("in_progress_no_assignee", facts.get("entered_status_at"))

    if status in ("todo", "backlog") and assignee:
        delivery = facts.get("delivery")
        if delivery and not active:
            add("delivery_status_not_updated", delivery["comment_id"], delivery=delivery)
        elif not facts.get("runs") and not active and not facts.get("evidence_partial"):
            assigned_at = parse_ts(facts.get("assigned_at")) or entered
            if assigned_at and (now - assigned_at).total_seconds() >= config.UNWOKEN_GRACE_S:
                add("assigned_no_task", facts.get("assigned_at") or facts.get("entered_status_at"))

    if assignee and status in ("todo", "backlog", "in_progress", "blocked"):
        queued = [r for r in facts.get("runs") or []
                  if r.get("status") in ("queued", "dispatched", "waiting_local_directory")
                  and not r.get("started_at")]
        if queued:
            oldest = min(queued, key=lambda r: r.get("created_at") or "")
            queued_at = parse_ts(oldest.get("created_at"))
            if queued_at and (now - queued_at).total_seconds() >= config.UNWOKEN_GRACE_S:
                add("queued_unclaimed", oldest.get("id"))

    if assignee and status not in ("done", "cancelled") and not active:
        newest = next((r for r in facts.get("runs") or [] if r.get("status") != "cancelled"), {})
        if newest.get("status") == "failed" and newest.get("error_kind") == "quota":
            completed = parse_ts(newest.get("completed_at"))
            if completed and (now - completed).total_seconds() >= config.FAILED_RUN_GRACE_S:
                add("quota_wait", newest.get("id"), failed_run=newest)

    pe = facts.get("provider_error")
    if pe and pe.get("error_kind") == "quota" and not active and assignee:
        add("quota_wait", pe["run_id"], failed_run={
            "id": pe["run_id"], "agent_id": pe["agent_id"],
            "recheck_at": pe.get("recheck_at"), "completed_at": pe["completed_at"]})
    elif pe:
        add("completed_provider_error", pe.get("run_id"), provider_error=pe)

    # Unwoken handoff: only agent-authored last comments count as agent
    # handoffs (member comments are human speech, not a delivery/judgement).
    lc = facts.get("last_comment")
    if status in ("blocked", "in_review") and lc and \
            lc.get("author_type") == "agent" and \
            lc.get("author_id") != config.DISPATCHER_AGENT_ID and \
            not lc.get("has_valid_mention"):
        entered_age = (now - entered).total_seconds() if entered else None
        comment_at = parse_ts(lc.get("created_at"))
        comment_age = (now - comment_at).total_seconds() if comment_at else None
        later_run = any(
            (parse_ts(r.get("started_at")) or parse_ts(r.get("dispatched_at"))
             or parse_ts(r.get("created_at")) or entered or now) >= comment_at
            for r in facts.get("runs") or [])
        if entered_age is not None and entered_age >= config.UNWOKEN_GRACE_S and \
                comment_age is not None and comment_age >= config.UNWOKEN_GRACE_S and \
                not later_run:
            add("unwoken_handoff", lc.get("id"), last_comment=lc)

    # Candidate only. The process path resolves agent + destination issue and
    # re-reads their tasks before reporting or scheduling. Never gate on the
    # source assignee: cross-issue review handoffs target someone else.
    if status == "in_progress" and assignee and lc and \
            lc.get("author_type") == "agent" and \
            lc.get("author_id") == config.DISPATCHER_AGENT_ID and \
            lc.get("dispatch_class"):
        comment_at = parse_ts(lc.get("created_at"))
        comment_age = (now - comment_at).total_seconds() if comment_at else None
        if comment_age is not None and comment_age >= config.UNWOKEN_GRACE_S:
            add("dispatch_no_wake", lc.get("id"), last_comment=lc)

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
