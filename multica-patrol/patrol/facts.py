"""Per-issue fact collection and time-anchor derivation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config

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


def collect_facts(cli, issue: dict, now: datetime) -> dict:
    """Deep check for one issue: timeline, comments, wakeups, active/failed runs."""
    issue_id = issue["id"]
    timeline_res = cli.run_json(
        ["issue", "timeline", issue_id, "--activity-only", "--output", "json"])
    activities = timeline_res.data if isinstance(timeline_res.data, list) else []
    timeline_partial = "truncated" in (timeline_res.stderr or "")

    since = iso(now - timedelta(seconds=config.COMMENTS_WINDOW_S))
    comments_res = cli.run_json(
        ["issue", "comment", "list", issue_id, "--since", since, "--compact",
         "--output", "json"])
    comments = comments_res.data if isinstance(comments_res.data, list) else []

    wakeups_res = cli.run_json(["issue", "wakeup", "list", issue_id, "--output", "json"])
    wakeups = wakeups_res.data if isinstance(wakeups_res.data, list) else []

    active_res = cli.run_json(["issue", "runs", issue_id, "--active", "--output", "json"])
    active_runs = active_res.data if isinstance(active_res.data, list) else []

    runs = []
    if (issue.get("assignee_id") and not active_runs
            and issue.get("status") not in ("done", "cancelled")) \
            or issue.get("status") in ("blocked", "in_review") \
        or (issue.get("status") == "in_progress" and issue.get("assignee_id")):
        # blocked/in_review always need the full runs list (unwoken-handoff
        # rule); assigned in_progress too (dispatch-without-wake rule).
        runs_res = cli.run_json(["issue", "runs", issue_id, "--output", "json"])
        runs = runs_res.data if isinstance(runs_res.data, list) else []

    anchors = derive_anchors(issue, activities, comments, timeline_partial)
    provider_error = _detect_provider_error(runs, comments, now)
    wakeups_small = [
        {"enabled": bool(w.get("enabled")), "expires_at": w.get("expires_at"),
         "kind": w.get("kind")}
        for w in wakeups
    ]
    return {
        "entered_status_at": iso(anchors["entered_status_at"]),
        "last_progress_at": iso(anchors["last_progress_at"]),
        "approval_wait_at": iso(anchors["approval_wait_at"]),
        "evidence_partial": anchors["evidence_partial"],
        "active_runs": [{"id": r.get("id"), "status": r.get("status")} for r in active_runs],
        "runs": [compact_run(r) for r in runs],
        "last_comment": _last_comment(comments),
        "provider_error": provider_error,
        "wakeups": wakeups_small,
        "checked_at": iso(now),
    }


def compact_run(r: dict) -> dict:
    return {"id": r.get("id"), "status": r.get("status"), "attempt": r.get("attempt"),
            "max_attempts": r.get("max_attempts"), "created_at": r.get("created_at"),
            "started_at": r.get("started_at"), "dispatched_at": r.get("dispatched_at"),
            "completed_at": r.get("completed_at"), "agent_id": r.get("agent_id")}


_INFRA_PREFIXES = ("[patrol-", "[dispatch-batch:")


def _excluded_comment(comment: dict) -> bool:
    content = (comment.get("content") or "").lstrip()
    return (content.startswith(config.SELF_COMMENT_PREFIXES)
            or content.startswith(_INFRA_PREFIXES)
            or config.RETRY_PREFIX in content
            or config.UNWOKEN_PREFIX in content
            or config.NOWAKE_PREFIX in content)


def is_dispatch_class(first: str, content: str) -> bool:
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
    target = config.NEXT_TARGET_RE.match(first)
    return {"id": best[1].get("id"), "author_type": best[1].get("author_type"),
            "author_id": best[1].get("author_id"), "created_at": iso(best[0]),
            "has_valid_mention": bool(config.UUID_MENTION_RE.search(content)),
            "mentioned_agent_ids": [u.lower() for u in
                                    config.AGENT_MENTION_RE.findall(content)],
            "dispatch_class": is_dispatch_class(first, content),
            "first_line_target_name": target.group("name") if target else None}


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
        if c.get("author_type") != "agent" or c.get("author_id") != agent_id:
            continue
        if not (c.get("content") or "").lstrip().startswith("[Error]"):
            continue
        ts = parse_ts(c.get("created_at"))
        if ts is None or started is None or ts < started or ts > window_end:
            continue
        if hit is None or ts > hit[0]:
            hit = (ts, c)
    if hit is None:
        return None
    stale = (now - completed).total_seconds() > config.ERROR_RUN_WINDOW_S
    return {"run_id": newest.get("id"), "agent_id": agent_id,
            "comment_id": hit[1].get("id"), "error_at": iso(hit[0]),
            "completed_at": iso(completed), "started_at": iso(started),
            "stale": stale}


def collect_light_facts(cli, issue: dict, now: datetime) -> dict:
    """Cheap facts for todo/backlog issues (approved_unassigned; the failed-run
    rule also applies when an assignee is set, so fetch the full list then)."""
    if issue.get("assignee_id"):
        runs_res = cli.run_json(["issue", "runs", issue["id"], "--output", "json"])
        runs = [compact_run(r)
                for r in (runs_res.data if isinstance(runs_res.data, list) else [])]
        active_runs = [{"id": r["id"], "status": r["status"]}
                       for r in runs if r["status"] in config.ACTIVE_RUN_STATUSES]
    else:
        runs = []
        active_res = cli.run_json(
            ["issue", "runs", issue["id"], "--active", "--output", "json"])
        active_runs = [{"id": r.get("id"), "status": r.get("status")}
                       for r in (active_res.data if isinstance(active_res.data, list) else [])]
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
