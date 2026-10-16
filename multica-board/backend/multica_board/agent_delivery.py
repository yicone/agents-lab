"""Uncovered agent delivery: one decision for the status bridge and Board attention.

A delivery is the assignee agent's new comment. A completed run is only the
fallback when that agent wrote no comment and no in_review/blocked transition
landed within 15 minutes of the signal. done/cancelled/backlog and pure
acknowledgements or duplicate replies are not deliveries.

The launchd copy of multica-status-bridge.py stays unchanged until this is
approved. After approval, copy this file to ~/.local/share/multica-bridge/
(the bridge already inserts that directory on sys.path) and, once the status
pass has run and the workspace is not a seed snapshot:

    import agent_delivery as ad
    change = ad.collect_for_issue(issue, route)
    if change and change["delivery"] == "send":
        changes.append(change)          # existing outbox enqueue, event_id set
    elif change:
        ad.remember_shadow(box, change, now_iso)

ACM Lab defaults to send. Every other workspace defaults to shadow: the event
is stored as status=shadow_record, which the existing drain ignores because it
only posts pending items. A route may set agent_delivery to send, shadow, or off.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

COVER_SEC = 900
QUIET_STATUSES = frozenset({"done", "cancelled", "canceled", "backlog"})
COVER_STATUSES = frozenset({"in_review", "blocked"})
ACM_LAB_SLUG = "acm-lab"
SHADOW_STATUS = "shadow_record"
_MENTION = re.compile(r"\[@[^\]]*\]\(mention://[^)]*\)")
_PUNCT = re.compile(r"^[\s\.\,\!\?\;\:，。！？；：、~～…]+|[\s\.\,\!\?\;\:，。！？；：、~～…]+$")
ACK_PHRASES = frozenset({
    "收到", "确认", "好的", "好", "ok", "okay", "noted", "acknowledged",
    "已确认", "知道了", "看到了", "收到了",
})


def parse_time(value):
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    if "." in text:
        head, rest = text.split(".", 1)
        frac = "".join(ch for ch in rest if ch.isdigit())
        tz = rest[len(frac):]
        text = f"{head}.{frac[:6].ljust(6, '0')}{tz}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_quiet(status, status_category=None) -> bool:
    named = (status or "").lower()
    if named:
        return named in QUIET_STATUSES
    return (status_category or "").lower() in QUIET_STATUSES


def _strip_mentions(text: str) -> str:
    return _MENTION.sub(" ", text or "")


def normalize_text(text) -> str:
    flat = " ".join(_strip_mentions(str(text or "")).split())
    return flat.casefold()


def is_pure_ack(text) -> bool:
    """True only when the whole comment is an acknowledgement. Uncertain text stays."""
    flat = " ".join(_strip_mentions(str(text or "")).split())
    flat = _PUNCT.sub("", flat).strip()
    if not flat:
        return True
    return flat.casefold() in ACK_PHRASES


def route_policy(slug, route=None) -> str:
    """send, shadow, or off. ACM Lab sends unless the route says otherwise."""
    explicit = (route or {}).get("agent_delivery")
    if explicit in ("off", "send", "shadow"):
        return explicit
    if slug == ACM_LAB_SLUG:
        return "send"
    return "shadow"


def _covered(signal_at, transitions, same_tick: bool) -> bool:
    if same_tick:
        return True
    moment = parse_time(signal_at)
    if moment is None:
        return False
    for transition in transitions or []:
        target = (transition.get("to") or transition.get("status") or "").lower()
        if target not in COVER_STATUSES:
            continue
        at = parse_time(transition.get("at"))
        if at is None:
            continue
        if abs((at - moment).total_seconds()) <= COVER_SEC:
            return True
    return False


def _nearby(moment, rows, time_key) -> bool:
    parsed = parse_time(moment)
    if parsed is None:
        return False
    for row in rows:
        other = parse_time(row.get(time_key))
        if other is None:
            continue
        if abs((other - parsed).total_seconds()) <= COVER_SEC:
            return True
    return False


def decide_delivery(
    *,
    status,
    status_category=None,
    assignee_type,
    assignee_id,
    comments,
    runs,
    transitions,
    same_tick_covered=False,
) -> dict:
    """Return {action: notify|skip, reason, comment_id?, run_id?, at?, content?}.

    comments/runs carry is_new for signals since the bridge cursor. Board passes
    the window's runs as new and leaves comments empty; it has no comment text.
    """
    if assignee_type != "agent" or not assignee_id:
        return {"action": "skip", "reason": "not_assignee"}
    if is_quiet(status, status_category):
        return {"action": "skip", "reason": "quiet"}

    own = [
        c for c in (comments or [])
        if c.get("author_type") == "agent" and c.get("author_id") == assignee_id
    ]
    own.sort(key=lambda c: (c.get("created_at") or "", c.get("id") or ""))
    seen: list[str] = []
    chosen = None
    filtered = None
    for comment in own:
        norm = normalize_text(comment.get("content"))
        duplicate = bool(norm) and norm in seen
        if norm:
            seen.append(norm)
        if not comment.get("is_new"):
            continue
        if duplicate or is_pure_ack(comment.get("content")):
            filtered = "duplicate" if duplicate else "ack"
            continue
        if _covered(comment.get("created_at"), transitions, same_tick_covered):
            filtered = "covered"
            continue
        run_id = None
        for run in runs or []:
            if run.get("agent_id") != assignee_id or (run.get("status") or "").lower() != "completed":
                continue
            if _nearby(comment.get("created_at"), [run], "completed_at"):
                run_id = run.get("id")
                break
        chosen = {
            "action": "notify",
            "reason": "assignee_comment",
            "comment_id": comment.get("id"),
            "run_id": run_id,
            "at": comment.get("created_at"),
            "content": comment.get("content"),
        }
    if chosen:
        return chosen
    if filtered:
        return {"action": "skip", "reason": filtered}

    fresh = [
        run for run in (runs or [])
        if run.get("is_new")
        and run.get("agent_id") == assignee_id
        and (run.get("status") or "").lower() == "completed"
    ]
    fresh.sort(key=lambda run: (run.get("completed_at") or "", run.get("id") or ""))
    if not fresh:
        return {"action": "skip", "reason": "none"}
    run = fresh[-1]
    if _covered(run.get("completed_at"), transitions, same_tick_covered):
        return {"action": "skip", "reason": "covered"}
    if _nearby(run.get("completed_at"), own, "created_at"):
        return {"action": "skip", "reason": "has_comment"}
    return {
        "action": "notify",
        "reason": "run_completed",
        "comment_id": None,
        "run_id": run.get("id"),
        "at": run.get("completed_at"),
        "content": None,
    }


def _clip(text, limit=900):
    if text is None:
        return None
    flat = str(text).strip()
    if not flat:
        return None
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def build_change(issue: dict, decision: dict, policy: str) -> dict:
    if decision.get("comment_id"):
        event_id = f"agent_delivery:{decision['comment_id']}"
    else:
        event_id = f"agent_delivery:run:{decision.get('run_id')}"
    return {
        "kind": "agent_delivery",
        "event_id": event_id,
        "reason": decision.get("reason"),
        "comment_id": decision.get("comment_id"),
        "run_id": decision.get("run_id"),
        "created_at": decision.get("at"),
        "content": _clip(decision.get("content")),
        "delivery": policy,
        "issue_id": issue.get("issue_id"),
        "identifier": issue.get("identifier"),
        "title": issue.get("title"),
        "status": issue.get("status"),
        "assignee_id": issue.get("assignee_id"),
        "workspace": issue.get("workspace"),
        "workspace_slug": issue.get("workspace_slug"),
        "workspace_id": issue.get("workspace_id"),
        "project_id": issue.get("project_id"),
        "notify_only": True,
    }


def collect_for_issue(issue: dict, route=None):
    """One outbox change, or None. Does not read the network or the outbox."""
    policy = route_policy(issue.get("workspace_slug"), route)
    if policy == "off":
        return None
    decision = decide_delivery(
        status=issue.get("status"),
        status_category=issue.get("status_category"),
        assignee_type=issue.get("assignee_type"),
        assignee_id=issue.get("assignee_id"),
        comments=issue.get("comments") or [],
        runs=issue.get("runs") or [],
        transitions=issue.get("transitions") or [],
        same_tick_covered=bool(issue.get("same_tick_covered")),
    )
    if decision["action"] != "notify":
        return None
    return build_change(issue, decision, policy)


def remember_shadow(box: dict, change: dict, now_iso: str) -> str:
    """Keep a non-ACM delivery without posting it. Existing drain skips this status."""
    items = box.setdefault("items", {})
    event_id = change["event_id"]
    if event_id in items:
        return "duplicate"
    items[event_id] = {
        "event_id": event_id,
        "kind": "agent_delivery",
        "status": SHADOW_STATUS,
        "payload": dict(change),
        "created_at": now_iso,
        "issue_id": change.get("issue_id"),
        "identifier": change.get("identifier"),
        "workspace_slug": change.get("workspace_slug"),
        "delivery": "shadow",
    }
    return "shadowed"
