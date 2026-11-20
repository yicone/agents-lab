"""Self-failure tracking, missed-slot detection, and escalation decisions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config
from .cli import CliError
from .facts import parse_ts
from .redact import redact

SGT = timezone(config.SGT_OFFSET)


def slot_to_dt(slot: str) -> datetime:
    return datetime.strptime(slot, "%Y%m%d%H").replace(tzinfo=SGT)


def dt_to_slot(ts: datetime) -> str:
    return ts.astimezone(SGT).strftime("%Y%m%d%H")


def detect_missed_slots(state: dict, current_slot: str) -> list[str]:
    """Slots with no run record between last recorded slot and the current one."""
    last = state.get("last_slot")
    if not last:
        return []
    missed = []
    cursor = slot_to_dt(last) + timedelta(hours=1)
    end = slot_to_dt(current_slot)
    while cursor < end:
        missed.append(dt_to_slot(cursor))
        cursor += timedelta(hours=1)
    return missed


def record_failure(state: dict, slot: str, kind: str, message: str, at: str | None = None) -> None:
    state.setdefault("failures", []).append(
        {"slot": slot, "kind": kind, "message": redact(message, 200), "at": at})
    # The streak counts failed SLOTS, not failure records: several kinds in one
    # slot still count once.
    if slot != state.get("last_failed_slot"):
        state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
        state["last_failed_slot"] = slot


def clear_streak(state: dict) -> None:
    state["consecutive_failures"] = 0
    state["last_failed_slot"] = None


def note_missed_slots(state: dict, missed: list[str], at: str | None = None) -> None:
    for slot in missed:
        record_failure(state, slot, "missed_slot", "no run record for this slot", at)


def pending_self_escalation(state: dict, now) -> bool:
    """True when the consecutive-failure streak warrants an escalation now."""
    if int(state.get("consecutive_failures") or 0) < config.SELF_ESCALATE_MIN_STREAK:
        return False
    last = parse_ts(state.get("self_escalated_at"))
    if last is None:
        return True
    return (now - last).total_seconds() >= config.SELF_ESCALATE_REPEAT_S


def key_escalations(state: dict, now) -> list[str]:
    """Standing alerted keys old enough to escalate once each."""
    out = []
    for key, rec in (state.get("alerted") or {}).items():
        first = parse_ts(rec.get("first_at"))
        if first is None:
            continue
        if (now - first).total_seconds() >= config.ESCALATE_AFTER_S and not rec.get("escalated_at"):
            out.append(key)
    return out


def mark_key_escalated(state: dict, key: str, now_iso: str) -> None:
    rec = (state.get("alerted") or {}).get(key)
    if rec is not None:
        rec["escalated_at"] = now_iso


def issue_id_of_key(key: str) -> str:
    return key.split("|", 1)[0]


def escalation_recent(state: dict, issue_id: str, now) -> bool:
    """True when this issue was successfully escalated inside the 24h window."""
    stamp = (state.get("escalation_sent") or {}).get(issue_id)
    last = parse_ts(stamp)
    if last is None:
        return False
    return (now - last).total_seconds() < config.DEDUPE_WINDOW_S


def record_escalation_sent(state: dict, issue_id: str, now_iso: str) -> None:
    if issue_id:
        state.setdefault("escalation_sent", {})[issue_id] = now_iso


def drop_alerted_issue(state: dict, issue_id: str) -> None:
    alerted = state.get("alerted") or {}
    for key in list(alerted):
        if issue_id_of_key(key) == issue_id:
            alerted.pop(key, None)


def escalation_issue_gate(cli, state: dict, issue_id: str, now) -> str:
    """Re-read live status before any escalation of this issue.

    Returns ok, closed, unread, or recent. Closed issues are removed from
    alerted. Unread and recent issues are left untouched, including
    escalated_at.
    """
    try:
        data = cli.run_json(["issue", "get", issue_id, "--output", "json"]).data
    except CliError:
        return "unread"
    status = data.get("status") if isinstance(data, dict) else None
    if not status:
        return "unread"
    if status in ("done", "cancelled"):
        drop_alerted_issue(state, issue_id)
        return "closed"
    if escalation_recent(state, issue_id, now):
        return "recent"
    return "ok"


def select_escalation_keys(cli, state: dict, keys: list[str], now) -> list[str]:
    """Keep standing keys whose issue is still open and outside the 24h window."""
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for key in keys:
        issue_id = issue_id_of_key(key)
        if issue_id not in grouped:
            order.append(issue_id)
            grouped[issue_id] = []
        grouped[issue_id].append(key)
    kept: list[str] = []
    for issue_id in order:
        if escalation_issue_gate(cli, state, issue_id, now) == "ok":
            kept.extend(grouped[issue_id])
    return kept


def dispatcher_name(state: dict) -> str:
    return (state.get("agent_names") or {}).get(config.DISPATCHER_AGENT_ID, "副调度")


FIRST_LINES = {
    "provider": "核对 {ident} 的 provider 报错并决定换 agent 或人工处理。",
    "keys": "核对下列巡检升级事项并处理；处理不了会再向你升级。",
    "self": "检查巡检为什么连续失败并恢复。",
}


def escalation_first_line(state: dict, kind: str, ident: str = "") -> str:
    return (f"{config.FIRST_LINE_NEXT} {dispatcher_name(state)} 做："
            + FIRST_LINES[kind].format(ident=ident))


def render_escalation(marker: str, keys: list[str], state: dict,
                      self_escalate: bool, now, first_line: str) -> str:
    """Human-readable escalation body. Keys are `uuid|reason|anchor`.

    Line 1 is plain Chinese for the user; line 2 is the dedupe marker.
    """
    index = state.get("issue_index") or {}
    lines = [first_line, marker]
    for key in keys:
        issue_id, reason, _anchor = (key.split("|") + [""])[:3]
        ident = (index.get(issue_id) or {}).get("identifier") or issue_id
        rec = (state.get("alerted") or {}).get(key) or {}
        first = parse_ts(rec.get("first_at"))
        age = ""
        if first:
            secs = max(0, int((now - first).total_seconds()))
            age = f"{secs // 86400}d{(secs % 86400) // 3600}h" if secs >= 86400 \
                else f"{secs // 3600}h{(secs % 3600) // 60}m"
        lines.append(
            f"- [{ident}](mention://issue/{issue_id}) reason={reason} "
            f"first_alerted={rec.get('first_at') or '?'} age={age or '?'}")
    if self_escalate:
        recent = (state.get("failures") or [])[-5:]
        kinds = ", ".join(f"{f.get('slot')}:{f.get('kind')}" for f in recent)
        lines.append(
            f"- self: {state.get('consecutive_failures') or 0} consecutive failed slots"
            + (f" — recent: {kinds}" if kinds else ""))
    return "\n".join(lines) + "\n"


def prune_state(state: dict, now, standing_keys: set | None = None) -> None:
    """Bound state growth; called before saving at the end of a non-dry run."""
    standing_keys = standing_keys or set()
    cutoff_slots = dt_to_slot(now - timedelta(days=14))
    state["slots"] = {s: v for s, v in (state.get("slots") or {}).items() if s >= cutoff_slots}
    alerted = state.get("alerted") or {}
    for key in list(alerted):
        rec = alerted[key]
        last = parse_ts(rec.get("last_at"))
        if key not in standing_keys and (last is None or
                                         (now - last).total_seconds() > 7 * 86400):
            del alerted[key]
    state["alerted"] = alerted
    state["failures"] = (state.get("failures") or [])[-50:]
    cutoff_daily = (now - timedelta(days=30)).strftime("%Y%m%d")
    state["daily"] = {d: v for d, v in (state.get("daily") or {}).items() if d >= cutoff_daily}
    keep_after = now - timedelta(days=14)
    state["escalation_sent"] = {
        issue_id: stamp for issue_id, stamp in (state.get("escalation_sent") or {}).items()
        if (ts := parse_ts(stamp)) is not None and ts >= keep_after
    }
    state["steward_wakes"] = {
        marker: stamp for marker, stamp in (state.get("steward_wakes") or {}).items()
        if (ts := parse_ts(stamp)) is not None and ts >= keep_after
    }
