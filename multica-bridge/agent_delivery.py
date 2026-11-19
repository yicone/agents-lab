"""Uncovered agent delivery: one decision for the status bridge and Board attention.

A delivery is the assignee agent's new comment. A completed run is only the
fallback when that agent wrote no comment and no in_review/blocked transition
landed within 15 minutes of the signal. done/cancelled/backlog, pure
acknowledgements, duplicate replies, and comment_type system are not deliveries.

A separate approval_request covers “需要你做：批准…” comments from the
dispatcher (副调度) or from an agent previously @-mentioned on the same
issue for 首次选派 / 独立验收, even when the issue has no assignee. It does
not notify issue creation, member comments, or ordinary agent comments.
System comments (for example a CLI non-zero exit notice) stay out of this path
so they are not reported as a normal hand-off.

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

A separate acceptance_result covers a non-assignee agent's new comment that
is an acceptance verdict (standalone word READY, or 验收通过 / 复验通过 /
验收未通过 / 验收失败) or that comes from an agent previously @-mentioned
on the same issue for 独立验收. The current assignee's own comments still
use the delivery path above, so the same comment is not emitted twice.
System comments and pure acknowledgements stay out. Generic comment events
are not opened.

agent_delivery defaults to send for every workspace. A route may set it to
send, shadow, or off. Shadow events are stored as status=shadow_record, which
the existing drain ignores because it only posts pending items.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

COVER_SEC = 900
QUIET_STATUSES = frozenset({"done", "cancelled", "canceled", "backlog"})
COVER_STATUSES = frozenset({"in_review", "blocked"})
ACM_LAB_SLUG = "acm-lab"
SHADOW_STATUS = "shadow_record"
# 副调度 (ACM Lab dispatcher). First-time selection and independent acceptance
# requests are posted by this agent, usually before anyone is assigned.
DISPATCHER_ID = "9bfe8713-ce7e-499b-830e-423223a64480"
APPROVAL_MARK = "需要你做：批准"
REQUEST_HINTS = ("首次选派", "独立验收")
ACCEPTANCE_HINTS = ("独立验收",)
ACCEPTANCE_PHRASES = ("验收通过", "复验通过", "验收未通过", "验收失败")
_READY_WORD = re.compile(r"\bREADY\b")
_MENTION = re.compile(r"\[@[^\]]*\]\(mention://[^)]*\)")
_AGENT_MENTION = re.compile(r"mention://agent/([0-9a-fA-F-]{36})")
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


def is_system_comment(comment) -> bool:
    """True for bridge/CLI system notices. comment_type wins, then type."""
    if not isinstance(comment, dict):
        return False
    kind = comment.get("comment_type")
    if not kind:
        kind = comment.get("type")
    return str(kind or "").strip().lower() == "system"


def is_pure_ack(text) -> bool:
    """True only when the whole comment is an acknowledgement. Uncertain text stays."""
    flat = " ".join(_strip_mentions(str(text or "")).split())
    flat = _PUNCT.sub("", flat).strip()
    if not flat:
        return True
    return flat.casefold() in ACK_PHRASES


def is_approval_request_text(text) -> bool:
    return APPROVAL_MARK in (text or "")


def mentioned_agent_ids(text) -> set[str]:
    return {m.group(1).lower() for m in _AGENT_MENTION.finditer(text or "")}


def author_was_requested(author_id, comments, before=None, hints=REQUEST_HINTS) -> bool:
    """True if an earlier comment @-mentioned this agent with one of the hints."""
    aid = (author_id or "").lower()
    if not aid:
        return False
    limit = parse_time(before) if before else None
    for prior in comments or []:
        created = parse_time(prior.get("created_at"))
        if limit is not None and created is not None and created > limit:
            continue
        body = prior.get("content") or ""
        if aid not in mentioned_agent_ids(body):
            continue
        if any(hint in body for hint in hints):
            return True
    return False


def is_eligible_approval_author(comment, comments) -> bool:
    if (comment or {}).get("author_type") != "agent" or not (comment or {}).get("author_id"):
        return False
    if comment.get("author_id") == DISPATCHER_ID:
        return True
    return author_was_requested(comment.get("author_id"), comments, comment.get("created_at"))


def decide_approval_request(*, status, status_category=None, comments) -> dict:
    """Notify a new “需要你做：批准…” from an eligible agent, even if unassigned."""
    approval_seen = False
    for comment in comments or []:
        if comment.get("is_new") and is_approval_request_text(comment.get("content")):
            approval_seen = True
            break
    if is_quiet(status, status_category) and approval_seen:
        return {"action": "skip", "reason": "quiet"}
    if is_quiet(status, status_category):
        return {"action": "skip", "reason": "none"}

    chosen = None
    filtered = None
    ordered = sorted(
        comments or [],
        key=lambda c: (c.get("created_at") or "", c.get("id") or ""),
    )
    for comment in ordered:
        if not comment.get("is_new"):
            continue
        if not is_approval_request_text(comment.get("content")):
            continue
        if is_system_comment(comment):
            filtered = "system"
            continue
        if comment.get("author_type") != "agent":
            filtered = "member_author"
            continue
        if not is_eligible_approval_author(comment, comments):
            filtered = "author_not_requested"
            continue
        chosen = {
            "action": "notify",
            "reason": "approval_request",
            "comment_id": comment.get("id"),
            "run_id": None,
            "at": comment.get("created_at"),
            "content": comment.get("content"),
        }
    if chosen:
        return chosen
    if filtered:
        return {"action": "skip", "reason": filtered}
    return {"action": "skip", "reason": "none"}


def collect_approval_request(issue: dict, route=None):
    """One outbox change for an approval request, or None."""
    policy = route_policy(issue.get("workspace_slug"), route)
    if policy == "off":
        return None
    decision = decide_approval_request(
        status=issue.get("status"),
        status_category=issue.get("status_category"),
        comments=issue.get("comments") or [],
    )
    if decision["action"] != "notify":
        return None
    return build_change(issue, decision, policy)


def skip_reason_for_unassigned(comments) -> str | None:
    """Log-worthy reason when an unassigned issue has new comments but no notify."""
    new_comments = [c for c in (comments or []) if c.get("is_new")]
    if not new_comments:
        return None
    if any(c.get("author_type") == "agent" for c in new_comments):
        return "not_assignee"
    return "member_comment_only"


def is_acceptance_result_text(text) -> bool:
    """True for a standalone READY token or an explicit acceptance phrase."""
    body = text or ""
    if _READY_WORD.search(body):
        return True
    return any(phrase in body for phrase in ACCEPTANCE_PHRASES)


def author_was_requested_for_acceptance(author_id, comments, before=None) -> bool:
    """True if an earlier comment @-mentioned this agent for 独立验收."""
    return author_was_requested(author_id, comments, before=before, hints=ACCEPTANCE_HINTS)


def decide_acceptance_result(
    *,
    status,
    status_category=None,
    assignee_id=None,
    comments,
    transitions=None,
    same_tick_covered=False,
) -> dict:
    """Notify a new acceptance comment from an agent who is not the assignee."""
    def _eligible(comment) -> bool:
        if is_acceptance_result_text(comment.get("content")):
            return True
        return author_was_requested_for_acceptance(
            comment.get("author_id"), comments, comment.get("created_at")
        )

    acceptance_seen = False
    for comment in comments or []:
        if comment.get("is_new") and _eligible(comment):
            acceptance_seen = True
            break
    if is_quiet(status, status_category) and acceptance_seen:
        return {"action": "skip", "reason": "quiet"}
    if is_quiet(status, status_category):
        return {"action": "skip", "reason": "none"}

    chosen = None
    filtered = None
    assignee = assignee_id or None
    ordered = sorted(
        comments or [],
        key=lambda c: (c.get("created_at") or "", c.get("id") or ""),
    )
    for comment in ordered:
        if not comment.get("is_new"):
            continue
        if not _eligible(comment):
            continue
        if is_system_comment(comment):
            filtered = "system"
            continue
        if comment.get("author_type") != "agent":
            filtered = "member_author"
            continue
        if not comment.get("author_id"):
            filtered = "member_author"
            continue
        if assignee and comment.get("author_id") == assignee:
            filtered = "assignee_own"
            continue
        if is_pure_ack(comment.get("content")):
            filtered = "ack"
            continue
        if _covered(comment.get("created_at"), transitions, same_tick_covered):
            filtered = "covered"
            continue
        chosen = {
            "action": "notify",
            "reason": "acceptance_result",
            "comment_id": comment.get("id"),
            "run_id": None,
            "at": comment.get("created_at"),
            "content": comment.get("content"),
        }
    if chosen:
        return chosen
    if filtered:
        return {"action": "skip", "reason": filtered}
    return {"action": "skip", "reason": "none"}


def collect_acceptance_result(issue: dict, route=None):
    """One outbox change for an acceptance result, or None."""
    policy = route_policy(issue.get("workspace_slug"), route)
    if policy == "off":
        return None
    decision = decide_acceptance_result(
        status=issue.get("status"),
        status_category=issue.get("status_category"),
        assignee_id=issue.get("assignee_id"),
        comments=issue.get("comments") or [],
        transitions=issue.get("transitions") or [],
        same_tick_covered=bool(issue.get("same_tick_covered")),
    )
    if decision["action"] != "notify":
        return None
    return build_change(issue, decision, policy)


def route_policy(slug, route=None) -> str:
    """send, shadow, or off. Defaults to send unless the route says otherwise."""
    explicit = (route or {}).get("agent_delivery")
    if explicit in ("off", "send", "shadow"):
        return explicit
    return "send"


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
        if is_system_comment(comment):
            if comment.get("is_new"):
                filtered = "system"
            continue
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
