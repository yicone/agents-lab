"""Unwoken supplements: rule 9 (blocked/in_review agent last comment with no
valid UUID mention, no run started after it) and rule 10 (in_progress issue
whose last comment is a dispatch-class dispatcher comment with no valid @ of
the target agent and no target run started within 15 min). One supplement per
issue per 24h, shared cap per run."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from . import config
from .cli import CliError
from .facts import iso, parse_ts
from .retry import _comment_list
from .redact import redact


def _run_started(r: dict):
    return (parse_ts(r.get("started_at")) or parse_ts(r.get("dispatched_at"))
            or parse_ts(r.get("created_at")))


def _resolve_target(state, issue: dict, lc: dict):
    """Dispatch target agent id for rule 10: the agent named on the comment's
    `下一步由 <name> 做` first line when it resolves via state["agent_names"],
    else the issue's assignee."""
    name = (lc or {}).get("first_line_target_name")
    if name:
        for aid, aname in (state.get("agent_names") or {}).items():
            if aname == name:
                return aid
    return issue.get("assignee_id")


def _guards(cli, issue, lc) -> tuple[bool, str, list[dict], list[dict]]:
    issue_id = issue["id"]
    fresh = cli.run_json(["issue", "get", issue_id, "--output", "json"]).data or {}
    if fresh.get("status") not in ("blocked", "in_review"):
        return False, "status_changed", [], []
    comment_at = parse_ts(lc.get("created_at"))
    if comment_at is None:
        return False, "no_comment_time", [], []
    comments = _comment_list(cli, issue_id, comment_at - timedelta(seconds=1))
    # Newest non-infra comment must still be the same id, still no valid mention.
    latest = None
    for c in comments:
        if c.get("type") != "comment":
            continue
        content = (c.get("content") or "").lstrip()
        if "[patrol-" in content or content.startswith("[dispatch-batch:"):
            continue
        if latest is None or (parse_ts(c.get("created_at")) or comment_at) > \
                (parse_ts(latest.get("created_at")) or comment_at):
            latest = c
    if latest is None or latest.get("id") != lc.get("id"):
        return False, "newer_comment", comments, []
    if latest.get("author_id") == config.DISPATCHER_AGENT_ID:
        return False, "dispatcher_author", comments, []
    if config.UUID_MENTION_RE.search(latest.get("content") or ""):
        return False, "now_has_mention", comments, []
    runs_res = cli.run_json(["issue", "runs", issue_id, "--output", "json"])
    runs = runs_res.data if isinstance(runs_res.data, list) else []
    for r in runs:
        started = _run_started(r)
        if comment_at and started and started >= comment_at:
            return False, "run_started", comments, runs
    return True, "ok", comments, runs


def _nowake_guards(cli, issue, lc, target_id) -> tuple[bool, str, list[dict], list[dict]]:
    """Pre-write re-read for rule 10: status/assignee unchanged, same last
    comment still by the dispatcher, still no valid @ of target, no target
    run started since the comment."""
    issue_id = issue["id"]
    fresh = cli.run_json(["issue", "get", issue_id, "--output", "json"]).data or {}
    if fresh.get("status") != "in_progress":
        return False, "status_changed", [], []
    if fresh.get("assignee_id") != issue.get("assignee_id"):
        return False, "assignee_changed", [], []
    comment_at = parse_ts(lc.get("created_at"))
    if comment_at is None:
        return False, "no_comment_time", [], []
    comments = _comment_list(cli, issue_id, comment_at - timedelta(seconds=1))
    latest = None
    for c in comments:
        if c.get("type") != "comment":
            continue
        content = (c.get("content") or "").lstrip()
        if "[patrol-" in content or content.startswith("[dispatch-batch:"):
            continue
        if latest is None or (parse_ts(c.get("created_at")) or comment_at) > \
                (parse_ts(latest.get("created_at")) or comment_at):
            latest = c
    if latest is None or latest.get("id") != lc.get("id"):
        return False, "newer_comment", comments, []
    if latest.get("author_id") != config.DISPATCHER_AGENT_ID:
        return False, "author_changed", comments, []
    if target_id and str(target_id).lower() in \
            {u.lower() for u in config.AGENT_MENTION_RE.findall(
                latest.get("content") or "")}:
        return False, "now_has_mention", comments, []
    runs_res = cli.run_json(["issue", "runs", issue_id, "--output", "json"])
    runs = runs_res.data if isinstance(runs_res.data, list) else []
    for r in runs:
        if r.get("agent_id") not in (None, target_id):
            continue
        started = _run_started(r)
        if comment_at and started and started >= comment_at:
            return False, "run_started", comments, runs
    return True, "ok", comments, runs


def _schedule_wakeup(store, cli, issue_id, rec, now):
    """Reconcile by durable intent before retrying an uncertain create."""
    wakeups = cli.run_json(["issue", "wakeup", "list", issue_id,
                           "--output", "json"]).data
    if not isinstance(wakeups, list):
        raise CliError("wakeup list returned a non-list response")
    source_line = f"源交付/派活评论 id：{rec['comment_id']}"
    intent_at = parse_ts(rec.get("intent_at"))
    existing = next((w for w in wakeups
                     if rec["marker"] in (w.get("instruction") or "").splitlines()
                     and source_line in (w.get("instruction") or "").splitlines()
                     and w.get("agent_id") == config.DISPATCHER_AGENT_ID
                     and (not intent_at or
                          (parse_ts(w.get("created_at")) or now) >= intent_at)), None)
    if existing is None:
        decision = ("确认派活是否已唤醒目标 agent，必要时重新派活。"
                    if rec.get("reason") == "dispatch_no_wake" else
                    "决定复验、返工或发起顶层批准请求，并推进下一步。")
        instruction = (f"{rec['marker']}\n{source_line}\n"
                       f"源评论时间：{rec.get('comment_at') or '?'}\n"
                       f"判定依据：{rec.get('evidence') or rec['reason']}\n"
                       f"目标 agent id：{rec.get('target_id') or config.DISPATCHER_AGENT_ID}\n"
                       f"下一步：{decision}\n请在 issue 顶层交付结果。\n")
        rec["instruction"] = instruction
        cli.permit_unwoken(issue_id)
        existing = cli.run_unwoken_wakeup(issue_id, instruction).data
    if not isinstance(existing, dict) or not existing.get("id"):
        raise CliError("wakeup create returned no id", uncertain=True)
    rec["wakeup_id"] = existing["id"]
    rec["wakeup_result"] = existing
    rec["last_error"] = None
    rec["state"] = "sent"
    rec["sent_at"] = iso(now)


class _Result:
    def __init__(self):
        self.actions = []
        self.sent = 0
        self.would_send = 0
        self.identity_violation = None


def process_unwoken(store, cli, state, per_issue, now, dry_run, sleep) -> _Result:
    res = _Result()
    records = state.setdefault("unwoken", {})
    for issue_id, entry in per_issue.items():
        issue = entry["issue"]
        if issue_id in config.PERSISTENT_IDS or issue.get("identifier") in config.UNRESOLVED_RULE:
            continue
        finding = next((x for x in entry.get("findings", [])
                        if x["reason"] in ("unwoken_handoff", "dispatch_no_wake")
                        and not x.get("exempt")), None)
        rec = records.get(issue_id)
        pending = rec and rec.get("state") in ("pending", "wakeup_pending")
        reason = rec.get("reason") if pending else (finding or {}).get("reason")
        lc = (finding or {}).get("last_comment") or (entry.get("facts") or {}).get("last_comment")

        def action(name):
            row = {"issue": issue, "action": name, "reason": reason,
                   "comment_id": (rec or {}).get("comment_id") or (lc or {}).get("id"),
                   "comment_at": (rec or {}).get("comment_at") or (lc or {}).get("created_at"),
                   "wakeup_id": (rec or {}).get("wakeup_id"),
                   "error": (rec or {}).get("last_error")}
            res.actions.append(row)
            log_row = {"issue_id": issue_id,
                       **{key: value for key, value in row.items() if key != "issue"}}
            logging.getLogger(__name__).info("unwoken %s", json.dumps(log_row, ensure_ascii=False))

        sent_at = parse_ts((rec or {}).get("sent_at"))
        if rec and rec.get("state") == "sent" and sent_at and (now - sent_at).total_seconds() < config.UNWOKEN_DEDUPE_S:
            action("deduped")
            continue
        if not finding and not pending:
            continue
        if dry_run:
            res.would_send += 1
            action("would_create_wakeup")
            continue
        if res.sent >= config.MAX_UNWOKEN_PER_RUN:
            action("unwoken_deferred")
            continue
        if not pending:
            if reason == "unwoken_handoff" and (lc or {}).get("author_id") == config.DISPATCHER_AGENT_ID:
                continue
            target = _resolve_target(state, issue, lc) if reason == "dispatch_no_wake" else None
            try:
                if reason == "dispatch_no_wake":
                    ok, why, _, _ = _nowake_guards(cli, issue, lc, target)
                else:
                    ok, why, _, _ = _guards(cli, issue, lc)
            except CliError:
                action("unwoken_skipped:read_error")
                continue
            if not ok:
                action(f"unwoken_skipped:{why}")
                continue
            prefix = config.NOWAKE_PREFIX if reason == "dispatch_no_wake" else config.UNWOKEN_PREFIX
            rec = {"comment_id": lc["id"], "comment_at": lc.get("created_at"),
                   "marker": f"{prefix}{issue_id}:{lc['id']}]", "reason": reason, "target_id": target,
                   "evidence": ("派活评论缺少目标 agent 的有效 UUID 唤醒，15 分钟内无目标 run 启动。"
                                if reason == "dispatch_no_wake" else
                                "交付评论缺少有效 UUID 唤醒，15 分钟内无后续 run 启动。"),
                   "state": "wakeup_pending", "intent_at": iso(now), "sent_at": None}
            records[issue_id] = rec
        # Old comment-first records resume here without writing another comment.
        if not rec.get("evidence"):
            rec["evidence"] = (
                "派活评论缺少目标 agent 的有效 UUID 唤醒，15 分钟内无目标 run 启动。"
                if reason == "dispatch_no_wake" else
                "交付评论缺少有效 UUID 唤醒，15 分钟内无后续 run 启动。")
        if not rec.get("comment_at"):
            try:
                comments = _comment_list(cli, issue_id,
                                        now - timedelta(seconds=config.DUP_LOOKBACK_S))
                source = next((c for c in comments if c.get("id") == rec["comment_id"]), {})
                rec["comment_at"] = source.get("created_at")
            except CliError as exc:
                rec["last_error"] = redact(str(exc))
                store.save(state)
                action("pending_uncertain")
                continue
        rec["state"] = "wakeup_pending"
        rec["last_attempt_at"] = iso(now)
        rec["attempts"] = rec.get("attempts", 0) + 1
        store.save(state)
        try:
            _schedule_wakeup(store, cli, issue_id, rec, now)
            res.sent += 1
            action("sent")
        except CliError as exc:
            rec["last_error"] = redact(str(exc))
            rec["uncertain"] = exc.uncertain
            action("wakeup_uncertain" if exc.uncertain else "wakeup_failed")
        store.save(state)
    return res
