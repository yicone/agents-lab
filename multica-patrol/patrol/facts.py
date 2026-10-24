"""Per-issue fact collection and time-anchor derivation."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import config
from .cli import CliError
from .redact import classify_error

SGT = timezone(config.SGT_OFFSET)


def parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def age_s(now: datetime, ts: datetime | None) -> float | None:
    return (now - ts).total_seconds() if ts else None


def _latest(activities: list[dict], action: str, pred=None) -> datetime | None:
    best = None
    for act in activities:
        if act.get("action") != action:
            continue
        if pred and not pred(act):
            continue
        ts = parse_ts(act.get("created_at"))
        if ts and (best is None or ts > best):
            best = ts
    return best


def _is_progress_comment(comment: dict) -> bool:
    if comment.get("type") != "comment":
        return False
    if comment.get("author_type") not in ("member", "agent"):
        return False
    if comment.get("author_id") == config.DISPATCHER_AGENT_ID:
        return False
    content = (comment.get("content") or "").lstrip()
    return not content.startswith(config.SELF_COMMENT_PREFIXES)


def derive_anchors(issue: dict, activities: list[dict], comments: list[dict],
                   timeline_partial: bool) -> dict:
    """Compute entered_status_at / last_progress_at / approval_wait for one issue."""
    status = issue.get("status")
    created = parse_ts(issue.get("created_at"))
    entered = _latest(
        activities, "status_changed",
        lambda a: (a.get("details") or {}).get("to") == status,
    )
    evidence_partial = False
    if entered is None:
        entered = created
    if timeline_partial:
        evidence_partial = True
        fallback = parse_ts(issue.get("updated_at"))
        if fallback and (entered is None or fallback > entered):
            entered = fallback
    if entered is None:
        entered = parse_ts(issue.get("updated_at")) or created
        evidence_partial = True

    candidates = [t for t in (entered, _latest(activities, "assignee_changed"),
                              _latest(activities, "status_changed")) if t]
    for comment in comments:
        if _is_progress_comment(comment):
            ts = parse_ts(comment.get("created_at"))
            if ts:
                candidates.append(ts)
    last_progress = max(candidates) if candidates else created

    approval_wait_at = None
    newest_agent = None
    newest_member = None
    for comment in comments:
        ts = parse_ts(comment.get("created_at"))
        if ts is None or comment.get("type") != "comment":
            continue
        if comment.get("author_type") == "agent":
            first_line = (comment.get("content") or "").splitlines()[0] if comment.get("content") else ""
            if any(p.match(first_line) for p in config.APPROVAL_WAIT_PATTERNS):
                if newest_agent is None or ts > newest_agent:
                    newest_agent = ts
        if comment.get("author_type") == "member":
            if newest_member is None or ts > newest_member:
                newest_member = ts
    if newest_agent is not None and (newest_member is None or newest_member < newest_agent):
        approval_wait_at = newest_agent

    return {
        "entered_status_at": entered,
        "last_progress_at": last_progress,
        "approval_wait_at": approval_wait_at,
        "evidence_partial": evidence_partial,
    }


def _list_data(result, label):
    if not isinstance(result.data, list):
        raise CliError(f"{label} returned a non-list response")
    return result.data


def collect_facts(cli, issue: dict, now: datetime) -> dict:
    """Deep check for one issue: timeline, comments, wakeups, active/failed runs."""
    issue_id = issue["id"]
    timeline_res = cli.run_json(
        ["issue", "timeline", issue_id, "--activity-only", "--output", "json"])
    activities = _list_data(timeline_res, "timeline")
    timeline_partial = "truncated" in (timeline_res.stderr or "")

    since = iso(now - timedelta(seconds=config.COMMENTS_WINDOW_S))
    comments_res = cli.run_json(
        ["issue", "comment", "list", issue_id, "--since", since, "--compact",
         "--output", "json"])
    comments = _list_data(comments_res, "comments")

    wakeups_res = cli.run_json(["issue", "wakeup", "list", issue_id, "--output", "json"])
    wakeups = _list_data(wakeups_res, "wakeups")

    active_res = cli.run_json(["issue", "runs", issue_id, "--active", "--output", "json"])
    active_runs = _list_data(active_res, "active runs")

    runs = []
    if (issue.get("assignee_id") and issue.get("status") in ("todo", "backlog")) \
            or (issue.get("assignee_id") and not active_runs
            and issue.get("status") not in ("done", "cancelled")) \
            or issue.get("status") in ("blocked", "in_review") \
        or (issue.get("status") == "in_progress" and issue.get("assignee_id")):
        # blocked/in_review always need the full runs list (unwoken-handoff
        # rule); assigned in_progress too (dispatch-without-wake rule).
        runs_res = cli.run_json(["issue", "runs", issue_id, "--output", "json"])
        runs = _list_data(runs_res, "runs")

    anchors = derive_anchors(issue, activities, comments, timeline_partial)
    provider_error = _detect_provider_error(runs, comments, now)
    wakeups_small = [
        {"enabled": bool(w.get("enabled")), "expires_at": w.get("expires_at"),
         "kind": w.get("kind")}
        for w in wakeups
    ]
    return {
        "entered_status_at": iso(anchors["entered_status_at"]),
        "assigned_at": iso(_latest(activities, "assignee_changed")),
        "last_progress_at": iso(anchors["last_progress_at"]),
        "approval_wait_at": iso(anchors["approval_wait_at"]),
        "evidence_partial": anchors["evidence_partial"],
        "active_runs": [{"id": r.get("id"), "status": r.get("status")} for r in active_runs],
        "runs": [compact_run(r) for r in runs],
        "last_comment": _last_comment(comments),
        "provider_error": provider_error,
        "delivery": _delivery(runs, comments),
        "wakeups": wakeups_small,
        "checked_at": iso(now),
    }


def compact_run(r: dict) -> dict:
    return {"id": r.get("id"), "status": r.get("status"), "attempt": r.get("attempt"),
            "max_attempts": r.get("max_attempts"), "created_at": r.get("created_at"),
            "started_at": r.get("started_at"), "dispatched_at": r.get("dispatched_at"),
            "completed_at": r.get("completed_at"), "agent_id": r.get("agent_id"),
            "error_kind": r.get("error_kind") or classify_error(r.get("error"), r.get("failure_reason")),
            "recheck_at": r.get("recheck_at") or quota_recheck_at(r)}


_INFRA_PREFIXES = ("[patrol-", "[dispatch-batch:")


def _excluded_comment(comment: dict) -> bool:
    content = (comment.get("content") or "").lstrip()
    return (content.startswith(config.SELF_COMMENT_PREFIXES)
            or content.startswith(_INFRA_PREFIXES)
            or config.RETRY_PREFIX in content
            or config.UNWOKEN_PREFIX in content
            or config.NOWAKE_PREFIX in content)


def is_dispatch_class(first: str, content: str) -> bool:
    # A correction may quote the old dispatch in its evidence paragraph.
    if first.startswith("巡检误报已核对"):
        return False
    if config.PROGRESS_CLASS_RE.search(first) and \
            not config.DISPATCH_CLASS_RE.search(first):
        return False
    return bool(first.startswith((config.FIRST_LINE_NEED, config.FIRST_LINE_NEXT))
                or config.DISPATCH_CLASS_RE.search(content))


def _last_comment(comments: list[dict]) -> dict | None:
    """Newest real comment — patrol/dispatch-infra comments never count."""
    best = None
    for c in comments or []:
        if c.get("type") != "comment" or _excluded_comment(c):
            continue
        ts = parse_ts(c.get("created_at"))
        if ts is None:
            continue
        if best is None or ts > best[0]:
            best = (ts, c)
    if best is None:
        return None
    content = best[1].get("content") or ""
    first = next((ln.strip() for ln in content.splitlines() if ln.strip()), "")
    target_line = re.sub(r"\[@([^\]]+)\]\(mention://agent/[^)]+\)", r"\1", first)
    target = config.NEXT_TARGET_RE.match(target_line)
    # The handoff line names the destination; later prose often cites the
    # source issue and historical evidence (ACM-378 also names itself).
    # Explicit destination labels in the body outrank incidental references
    # to the source issue. Multiple destinations remain ambiguous.
    destinations = re.findall(r"(?:验收单|复验单|重验单)\s*[:：]?\s*(ACM-\d+)", content)
    refs = (re.findall(r"ACM-\d+", first) or destinations
            or re.findall(r"ACM-\d+", content))
    return {"id": best[1].get("id"), "author_type": best[1].get("author_type"),
            "author_id": best[1].get("author_id"), "created_at": iso(best[0]),
            "has_valid_mention": bool(config.UUID_MENTION_RE.search(content)),
            "mentioned_agent_ids": [u.lower() for u in
                                    config.AGENT_MENTION_RE.findall(content)],
            "dispatch_class": is_dispatch_class(first, content),
            "first_line_target_name": target.group("name").strip() if target else None,
            "target_issue_ref": refs[0] if len(set(refs)) == 1 else None,
            "target_issue_ambiguous": len(set(refs)) > 1,
            "is_error": content.lstrip().startswith("[Error]")}


def _detect_provider_error(runs: list[dict], comments: list[dict], now) -> dict | None:
    """Newest non-cancelled run 'completed' + an [Error] comment by its agent."""
    newest = None
    for r in runs or []:
        if r.get("status") != "cancelled":
            newest = r
            break
    if not newest or newest.get("status") != "completed":
        return None
    completed = parse_ts(newest.get("completed_at"))
    started = parse_ts(newest.get("started_at")) or completed
    if completed is None:
        return None
    window_end = completed + timedelta(seconds=120)
    agent_id = newest.get("agent_id")
    hit = None
    for c in comments or []:
        if c.get("type") != "comment" or _excluded_comment(c):
            continue
        if c.get("author_type") != "agent" or c.get("author_id") != agent_id:
            continue
        ts = parse_ts(c.get("created_at"))
        if ts is None or started is None or ts < started or ts > window_end:
            continue
        if hit is None or ts > hit[0]:
            hit = (ts, c)
    if hit is None or not (hit[1].get("content") or "").lstrip().startswith("[Error]"):
        return None
    stale = (now - completed).total_seconds() > config.ERROR_RUN_WINDOW_S
    return {"run_id": newest.get("id"), "agent_id": agent_id,
            "error_kind": classify_error(hit[1].get("content")),
            "recheck_at": quota_recheck_at({**newest, "error": hit[1].get("content")}),
            "comment_id": hit[1].get("id"), "error_at": iso(hit[0]),
            "completed_at": iso(completed), "started_at": iso(started),
            "stale": stale}


def collect_light_facts(cli, issue: dict, now: datetime) -> dict:
    """Cheap facts for todo/backlog issues (approved_unassigned; the failed-run
    rule also applies when an assignee is set, so fetch the full list then)."""
    if issue.get("assignee_id"):
        return collect_facts(cli, issue, now)
    runs = []
    active_res = cli.run_json(
        ["issue", "runs", issue["id"], "--active", "--output", "json"])
    active_runs = [{"id": r.get("id"), "status": r.get("status")}
                   for r in (_list_data(active_res, "active runs"))]
    return {
        "entered_status_at": iso(parse_ts(issue.get("updated_at")) or parse_ts(issue.get("created_at"))),
        "last_progress_at": iso(parse_ts(issue.get("last_activity_at"))
            or parse_ts(issue.get("updated_at")) or parse_ts(issue.get("created_at"))),
        "approval_wait_at": None,
        "evidence_partial": False,
        "active_runs": active_runs,
        "runs": runs,
        "last_comment": None,
        "provider_error": None,
        "wakeups": [],
        "checked_at": iso(now),
    }


def quota_recheck_at(run):
    """Provider clock hints use the patrol's configured SGT timezone.
    Unknown hints request immediate dispatcher judgement, never guessed recovery.
    """
    explicit = parse_ts(run.get("reset_at"))
    if explicit:
        return iso(explicit)
    completed = parse_ts(run.get("completed_at"))
    match = re.search(r"try again at (\d{1,2}):(\d{2})\s*(AM|PM)", run.get("error") or "", re.I)
    if not completed or not match:
        return None
    hour, minute = int(match[1]), int(match[2])
    if not 1 <= hour <= 12 or minute > 59:
        return None
    local = completed.astimezone(SGT)
    reset = local.replace(hour=hour % 12 + (12 if match[3].upper() == "PM" else 0),
                          minute=minute, second=0, microsecond=0)
    if reset < local:
        reset += timedelta(days=1)
    return iso(reset)


def _delivery(runs, comments):
    """Positive delivery evidence only; never infer delivery from exit0 alone."""
    for run in runs:
        if run.get("status") != "completed":
            continue
        started = parse_ts(run.get("started_at"))
        ended = parse_ts(run.get("completed_at"))
        if not started or not ended:
            continue
        candidates = [c for c in comments if c.get("author_type") == "agent"
                      and c.get("author_id") == run.get("agent_id")
                      and c.get("type") == "comment" and not _excluded_comment(c)
                      and started <= (parse_ts(c.get("created_at")) or started - timedelta(seconds=1))
                          <= ended + timedelta(seconds=120)]
        newest = max(candidates, key=lambda c: c["created_at"], default=None)
        if newest and not (newest.get("content") or "").lstrip().startswith("[Error]"):
            return {"run_id": run.get("id"), "comment_id": newest.get("id"),
                    "created_at": newest.get("created_at")}
    return None
