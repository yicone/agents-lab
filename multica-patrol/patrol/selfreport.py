"""Self-failure tracking, missed-slot detection, and escalation decisions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config
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
