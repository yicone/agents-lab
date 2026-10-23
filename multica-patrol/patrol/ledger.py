"""Ledger batch rendering, dedupe, and the idempotent publish protocol."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

from . import config
from .cli import CliError
from .facts import iso, parse_ts
from .identity import verify_comment_author
from .redact import redact


def dedupe_key(issue: dict, finding: dict) -> str:
    return f"{issue['id']}|{finding['reason']}|{finding.get('anchor')}"


def marker_for(batch_id: str) -> str:
    return f"[patrol-batch:{batch_id}"


def _mention(issue: dict) -> str:
    return f"[{issue.get('identifier', issue['id'])}](mention://issue/{issue['id']})"


def _age(now, anchor_iso) -> str:
    ts = parse_ts(anchor_iso)
    if ts is None:
        return "?"
    secs = max(0, int((now - ts).total_seconds()))
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs / 3600:.1f}h"
    return f"{secs / 86400:.1f}d"


SUGGESTIONS = {
    "in_progress_no_active_run": "no active run and no progress; check whether the run died or the issue should be re-dispatched",
    "in_review_stale": "in review over 24h; needs a human/agent decision or status move",
    "blocked_stale": "blocked over 48h; re-check whether the blocker still holds",
    "approval_not_blocked": "agent is waiting for approval but the issue is not marked blocked",
    "approved_unassigned": "approved-labelled issue is unassigned and idle",
    "run_failed_no_retry": "latest runs failed with no retry in flight",
    "in_progress_no_assignee": "in progress without an assignee",
    "unwoken_handoff": "给最后一条交付评论补上有效 UUID @ 唤醒下一环，由副调度决定复验/返工/顶层批准请求。",
    "dispatch_no_wake": "派活评论后 15 分钟内无目标 agent 的 run 且无有效 UUID @，疑似未唤醒；由副调度补发 @ 或重新派活。",
}


def render_batch(batch_id: str, mode_tag: str, slot: str, scan_kind: str, coverage: dict,
                 rows: list[dict], exempt_rows: list[dict], provider_rows: list[dict],
                 suppressed: int, suppressed_keys: int, unresolved: list[dict],
                 prior_failures: list[dict], escalations: list[str],
                 duration_s: float, now_sgt, unwoken_rows: list[dict] | None = None) -> str:
    lines = [f"[patrol-batch:{batch_id} v2 {mode_tag}]"]
    lines.append(
        f"Patrol {slot[:4]}-{slot[4:6]}-{slot[6:8]} {slot[8:]}:00 SGT · "
        f"scan={scan_kind} · discovered={coverage['issues_discovered']} "
        f"deep={coverage['deep_checked']} cache={coverage['from_cache']} "
        f"errors={len(coverage['errors'])} duration={duration_s:.0f}s")
    if not coverage["complete"]:
        lines.append("⚠ 覆盖不完整：本轮不能断言“无异常”；以下仅为已核到部分的发现。")
        for err in coverage["errors"][:10]:
            lines.append(f"  - {redact(err, 160)}")
        if len(coverage["errors"]) > 10:
            lines.append(f"  - ...and {len(coverage['errors']) - 10} more")
    if prior_failures:
        lines.append("")
        lines.append("## 上一轮自报")
        for fail in prior_failures[:10]:
            lines.append(f"- slot {fail.get('slot')} [{fail.get('kind')}] {fail.get('message', '')}")
        if len(prior_failures) > 10:
            lines.append(f"- ...and {len(prior_failures) - 10} more")
    lines.append("")
    lines.append(f"## 候选异常（{len(rows)}）")
    if not rows:
        lines.append("无" if coverage["complete"] else "未知（覆盖不完整）")
    for row in rows:
        issue = row["issue"]
        reasons = "/".join(row["reasons"])
        flag = " ⚠证据不全" if row.get("evidence_partial") else ""
        suggestion = SUGGESTIONS.get(row["reasons"][0], "")
        lines.append(
            f"- {_mention(issue)} `{reasons}` age={row['age']} "
            f"last_progress={row['last_progress']}{flag} — {suggestion}")
    if exempt_rows:
        lines.append("")
        lines.append(f"## 豁免（{len(exempt_rows)}）")
        for row in exempt_rows:
            nxt = f" next_review={row['next_review']}" if row.get("next_review") else ""
            lines.append(f"- {_mention(row['issue'])} `{row['reasons']}` {row['kind']}{nxt}")
    if provider_rows:
        lines.append("")
        lines.append("## provider 报错重试")
        for row in provider_rows:
            lines.append(
                f"- {_mention(row['issue'])} agent={row.get('agent_name') or '?'} "
                f"run={row.get('run_id')} action={row['action']}")
    if unwoken_rows:
        lines.append("")
        lines.append("## 未唤醒的交付补发")
        for row in unwoken_rows:
            tag = (" reason=" + row["reason"]
                   if row.get("reason") == "dispatch_no_wake" else "")
            lines.append(
                f"- {_mention(row['issue'])} agent={row.get('agent_name') or '?'} "
                f"comment_at={row.get('comment_at')} action={row['action']}{tag}")
    lines.append("")
    lines.append(f"dedupe 抑制：{suppressed}（涉及 {suppressed_keys} 个 key）")
    if unresolved:
        lines.append("")
        lines.append("## 待用户口径（证据不足，阻塞）")
        for issue in unresolved:
            lines.append(
                f"- {_mention(issue)} 当前状态 `{issue.get('status')}`："
                "需要用户裁定归类为 shelved / persistent / normal。")
    if escalations:
        lines.append("")
        lines.append(f"## 升级：{len(escalations)} 项已写入收件箱")
        for item in escalations[:10]:
            lines.append(f"- {item}")
    body = "\n".join(lines) + "\n"
    if len(body) > config.BODY_MAX_CHARS:
        body = body[: config.BODY_MAX_CHARS - 40] + "\n...and more (truncated)\n"
    return body


def find_marker(comments: list[dict], marker: str) -> dict | None:
    for comment in comments:
        if marker in (comment.get("content") or ""):
            return comment
    return None


def read_back(cli, target_id: str, marker: str, since) -> dict | None:
    res = cli.run_json(["issue", "comment", "list", target_id,
                        "--since", iso(since), "--compact", "--output", "json"])
    comments = res.data if isinstance(res.data, list) else []
    return find_marker(comments, marker)


def verify_post_author(cli, target_id: str, comment_id: str, since) -> tuple[bool, str]:
    res = cli.run_json(["issue", "comment", "list", target_id,
                        "--since", iso(since), "--compact", "--output", "json"])
    comments = res.data if isinstance(res.data, list) else []
    for comment in comments:
        if comment.get("id") == comment_id:
            if verify_comment_author(comment):
                return True, "ok"
            return False, "comment author is not the dispatcher agent"
    return False, "posted comment not found on read-back"


class IdentityViolation(Exception):
    pass


def publish(store, cli, state: dict, target_id: str, batch_marker: str, body: str,
            batch_id: str, now, sleep) -> dict:
    """Idempotent publish: durable intent -> pre-check -> post -> read-back.

    Returns {status: posted|duplicate|held|uncertain|identity_violation|ledger_assigned,
             comment_id}.
    """
    sha = hashlib.sha256(body.encode()).hexdigest()
    body_path = store.write_body(f"body-{batch_id.replace(':', '_')}.md", body)
    state["inflight"] = {
        "batch_id": batch_id, "target": target_id, "marker": batch_marker,
        "body_sha256": sha, "body_file": str(body_path), "intent_at": iso(now),
    }
    store.save(state)

    if target_id == config.LEDGER_ISSUE_ID:
        try:
            ledger = cli.run_json(["issue", "get", target_id, "--output", "json"]).data
        except CliError as exc:
            return {"status": "held", "comment_id": None, "reason": f"ledger get: {exc}"}
        if ledger.get("assignee_id") or ledger.get("parent_issue_id"):
            return {"status": "held", "comment_id": None, "reason": "ledger_assigned"}

    since = now - timedelta(seconds=config.DUP_LOOKBACK_S)
    try:
        existing = read_back(cli, target_id, batch_marker, since)
    except CliError as exc:
        return {"status": "uncertain", "comment_id": None, "reason": f"pre-check: {exc}"}
    if existing:
        state["inflight"] = None
        store.save(state)
        return {"status": "duplicate", "comment_id": existing.get("id")}

    try:
        result = cli.run_write(["issue", "comment", "add", target_id,
                                "--content-file", str(body_path),
                                "--allow-external-file", "--output", "json"])
    except CliError:
        # Uncertain: read back once, never blind resend.
        sleep(2)
        try:
            found = read_back(cli, target_id, batch_marker, since)
        except CliError:
            return {"status": "uncertain", "comment_id": None, "reason": "post failed; read-back failed"}
        if found:
            ok, why = verify_post_author(cli, target_id, found.get("id"), since)
            if not ok:
                return {"status": "identity_violation", "comment_id": found.get("id"), "reason": why}
            state["inflight"] = None
            store.save(state)
            return {"status": "posted", "comment_id": found.get("id")}
        return {"status": "uncertain", "comment_id": None, "reason": "post failed; not found on read-back"}

    comment_id = (result.data or {}).get("id") if isinstance(result.data, dict) else None
    ok, why = verify_post_author(cli, target_id, comment_id, since)
    if not ok:
        return {"status": "identity_violation", "comment_id": comment_id, "reason": why}
    state["inflight"] = None
    store.save(state)
    return {"status": "posted", "comment_id": comment_id}


def reconcile_inflight(store, cli, state: dict, now, sleep) -> dict | None:
    """Handle a durable inflight intent from a previous crashed run.

    Returns a result dict when the inflight was resolved/retried, else None.
    """
    inflight = state.get("inflight")
    if not inflight:
        return None
    marker = inflight["marker"]
    target_id = inflight["target"]
    since = now - timedelta(seconds=config.DUP_LOOKBACK_S)
    try:
        found = read_back(cli, target_id, marker, since)
    except CliError:
        return {"status": "held", "comment_id": None, "reason": "inflight read-back failed"}
    if found:
        state["inflight"] = None
        store.save(state)
        return {"status": "posted", "comment_id": found.get("id"), "recovered": True}
    intent_at = parse_ts(inflight.get("intent_at"))
    age = (now - intent_at).total_seconds() if intent_at else config.INFLIGHT_RESEND_AGE_S + 1
    if age < config.INFLIGHT_RESEND_AGE_S:
        return {"status": "held", "comment_id": None, "reason": "inflight intent too fresh"}
    # Old unfound intent: resend once with the same batch id, then read back.
    try:
        body = Path(inflight["body_file"]).read_text() if inflight.get("body_file") else ""
    except OSError:
        body = ""
    if not body:
        return {"status": "held", "comment_id": None, "reason": "inflight body file missing"}
    result = cli.run_write(["issue", "comment", "add", target_id,
                            "--content-file", inflight["body_file"],
                            "--allow-external-file", "--output", "json"])
    found = read_back(cli, target_id, marker, since)
    if not found:
        return {"status": "uncertain", "comment_id": None, "reason": "resend not visible"}
    state["inflight"] = None
    store.save(state)
    return {"status": "posted", "comment_id": (result.data or {}).get("id") or found.get("id"),
            "recovered": True, "resent": True}
