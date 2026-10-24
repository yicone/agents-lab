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
from .facts import iso, parse_ts, _last_comment, collect_facts
from .retry import _comment_list
from .redact import redact


def _run_started(r: dict):
    return (parse_ts(r.get("started_at")) or parse_ts(r.get("dispatched_at"))
            or parse_ts(r.get("created_at")))


def _resolve_target(state, issue: dict, lc: dict):
    """Unknown or ambiguous target must never fall back to the assignee."""
    name = (lc or {}).get("first_line_target_name")
    if name:
        matches = [aid for aid, aname in (state.get("agent_names") or {}).items() if aname == name]
        return matches[0] if len(matches) == 1 else None
    ids = (lc or {}).get("mentioned_agent_ids") or []
    return ids[0] if len(set(ids)) == 1 else None


def _guards(cli, issue, lc, ignored_comment_id=None) -> tuple[bool, str, list[dict], list[dict]]:
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
        if c.get("id") == ignored_comment_id:
            continue
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
    parsed = _last_comment([latest]) or {}
    if parsed.get("target_issue_ambiguous"):
        return False, "unknown_target_issue", comments, []
    target_ref = parsed.get("target_issue_ref")
    target_issue_id = issue_id
    if target_ref and target_ref != issue.get("identifier"):
        target_issue = cli.run_json(["issue", "get", target_ref, "--output", "json"]).data or {}
        if target_issue.get("workspace_id") not in (None, config.WORKSPACE_ID):
            return False, "target_workspace_changed", comments, []
        from .scan import is_shelved
        if is_shelved(target_issue):
            return False, "target_shelved", comments, []
        if target_issue.get("assignee_id") != target_id:
            return False, "target_assignee_changed", comments, []
        if target_issue.get("status") in ("done", "cancelled", "in_review"):
            return False, "target_already_delivered", comments, []
        target_issue_id = target_issue["id"]
    runs_res = cli.run_json(["issue", "runs", target_issue_id, "--output", "json"])
    runs = runs_res.data if isinstance(runs_res.data, list) else []
    for r in runs:
        if r.get("agent_id") not in (None, target_id):
            continue
        started = _run_started(r)
        if comment_at and started and started >= comment_at:
            return False, "run_started", comments, runs
    return True, "ok", comments, runs


def _quota_guards(cli, issue, rec, now):
    fresh = cli.run_json(["issue", "get", issue["id"], "--output", "json"]).data or {}
    if fresh.get("status") not in ("todo", "backlog", "in_progress", "blocked"):
        return False, "status_changed"
    if fresh.get("assignee_id") != rec.get("target_id"):
        return False, "assignee_changed"
    f = collect_facts(cli, fresh, now)
    newest = next((r for r in f["runs"] if r.get("status") != "cancelled"), {})
    if f["active_runs"] or newest.get("id") != rec["comment_id"]:
        return False, "successor_task"
    if f.get("delivery") and (parse_ts(f["delivery"].get("created_at")) or now) >= (
            parse_ts(newest.get("started_at")) or now):
        return False, "new_delivery"
    latest = f.get("last_comment") or {}
    latest_at = parse_ts(latest.get("created_at"))
    ended = parse_ts(newest.get("completed_at"))
    if latest.get("author_id") == rec.get("target_id") and not latest.get("is_error") \
            and latest_at and ended and latest_at > ended:
        return False, "new_delivery"
    pe = f.get("provider_error") or {}
    quota_error = (newest.get("status") == "failed" and newest.get("error_kind") == "quota")
    quota_error = quota_error or (newest.get("status") == "completed"
                                 and pe.get("run_id") == newest.get("id")
                                 and pe.get("error_kind") == "quota")
    if not quota_error:
        return False, "failure_changed"
    return True, "ok"


def _schedule_wakeup(store, cli, issue_id, rec, now):
    """Reconcile by durable intent before retrying an uncertain create."""
    wakeups = cli.run_json(["issue", "wakeup", "list", issue_id,
                           "--output", "json"]).data
    if not isinstance(wakeups, list):
        raise CliError("wakeup list returned a non-list response")
    source_line = (f"原失败 run id：{rec['comment_id']}" if rec.get("reason") == "quota_wait"
                   else f"源交付/派活评论 id：{rec['comment_id']}")
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
        if rec.get("reason") == "quota_wait":
            instruction = (
                f"{rec['marker']}\n{source_line}\n"
                f"目标 agent id：{rec['target_id']}\n"
                f"复查时间：{rec['recheck_at']}\n"
                "下一步：先读取该目标 runtime/账号的新鲜额度数据，记录读取时间、来源与可用判断；"
                "提示的重置时间不能证明额度已恢复。重读本单最新描述、交付评论、负责人和全部活动任务。"
                "仅当额度确认可用、负责人仍是目标 agent、原失败 run 仍为最新且没有新交付或 queued/"
                "dispatched/running 任务，才按原授权恢复一次。恢复前再次查活动任务，"
                "恢复后读回新 run id 并记录它与原失败 run 的关联。"
                "若已有后续任务/交付或负责人变化，结束等待，禁止补派。"
                "任一事实无法确认就由副调度判断并明确缺失证据，不自动重跑、不安装或切换账号，"
                "不归入 ACM-121，不设置定时机械补派。此等待只触发一次。\n")
        rec["instruction"] = instruction
        cli.permit_unwoken(issue_id)
        schedule_at = iso(max(parse_ts(rec.get("recheck_at")) or now, now + timedelta(minutes=1)))
        rec["scheduled_at"] = schedule_at
        existing = (cli.run_unwoken_wakeup(issue_id, instruction, at=schedule_at)
                    if rec.get("reason") == "quota_wait" else
                    cli.run_unwoken_wakeup(issue_id, instruction)).data
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
        candidates = [x for x in entry.get("findings", [])
                      if x["reason"] in ("unwoken_handoff", "dispatch_no_wake", "quota_wait")
                      and not x.get("exempt")]
        finding = next((x for x in candidates if x["reason"] == "quota_wait"),
                       candidates[0] if candidates else None)
        quota = finding and finding["reason"] == "quota_wait"
        record_key = f"quota:{issue_id}:{finding['anchor']}" if quota else issue_id
        rec = records.get(record_key)
        pending = rec and rec.get("state") in ("pending", "wakeup_pending")
        reason = rec.get("reason") if pending else (finding or {}).get("reason")
        lc = (finding or {}).get("last_comment") or (entry.get("facts") or {}).get("last_comment")
        if pending and not quota and (not lc or lc.get("id") != rec.get("comment_id")):
            try:
                comments = _comment_list(cli, issue_id, now - timedelta(seconds=config.DUP_LOOKBACK_S))
                lc = _last_comment([c for c in comments if c.get("id") != rec.get("posted_comment_id")])
            except CliError as exc:
                res.actions.append({"issue": issue, "action": "pending_uncertain",
                                    "error": redact(str(exc))})
                continue

        def action(name):
            row = {"issue": issue, "action": name, "reason": reason,
                   "comment_id": (rec or {}).get("comment_id") or (lc or {}).get("id"),
                   "comment_at": (rec.get("comment_at") if rec else (lc or {}).get("created_at")),
                   "wakeup_id": (rec or {}).get("wakeup_id"),
                   "failed_run_id": (rec or {}).get("failed_run_id"),
                   "target_id": (rec or {}).get("target_id"),
                   "recheck_at": (rec or {}).get("recheck_at"),
                   "error": (rec or {}).get("last_error")}
            res.actions.append(row)
            log_row = {"issue_id": issue_id,
                       **{key: value for key, value in row.items() if key != "issue"}}
            logging.getLogger(__name__).info("unwoken %s", json.dumps(log_row, ensure_ascii=False))

        # Retire an obsolete intent before resolving a target from a different
        # comment. Otherwise an unparseable follow-up traps legacy pending
        # records here forever, before the freshness guards can run.
        if pending and not quota and lc and lc.get("id") != rec.get("comment_id"):
            if not dry_run:
                rec["state"] = "superseded"
                store.save(state)
            action("unwoken_skipped:newer_comment")
            continue

        sent_at = parse_ts((rec or {}).get("sent_at"))
        if rec and rec.get("state") == "sent" and (quota or (sent_at and (now - sent_at).total_seconds() < config.UNWOKEN_DEDUPE_S)):
            action("deduped")
            continue
        if not finding and not pending:
            continue
        target = (_resolve_target(state, issue, lc) if reason == "dispatch_no_wake"
                  else (finding or {}).get("failed_run", {}).get("agent_id") if quota else None)
        if reason == "dispatch_no_wake" and not target:
            action("unknown_target")
            continue
        if dry_run:
            if quota and not rec:
                rec = _new_quota_record(issue_id, finding["failed_run"], target, now)
            try:
                if quota:
                    failed = finding["failed_run"]
                    ok, why = _quota_guards(cli, issue, {
                        "target_id": target, "comment_id": failed["id"]}, now)
                elif lc and reason == "dispatch_no_wake":
                    ok, why, _, _ = _nowake_guards(cli, issue, lc, target)
                elif lc:
                    ok, why, _, _ = _guards(cli, issue, lc, (rec or {}).get("posted_comment_id"))
                else:
                    ok, why = False, "unknown_source"
            except CliError:
                ok, why = False, "read_error"
            if ok:
                res.would_send += 1
                action("would_create_wakeup")
            else:
                action(f"unwoken_skipped:{why}")
            continue
        if res.sent >= config.MAX_UNWOKEN_PER_RUN:
            action("unwoken_deferred")
            continue
        if not pending:
            if reason == "unwoken_handoff" and (lc or {}).get("author_id") == config.DISPATCHER_AGENT_ID:
                continue
            try:
                if quota:
                    failed = finding["failed_run"]
                    ok, why = _quota_guards(cli, issue, {
                        "target_id": target, "comment_id": failed["id"]}, now)
                elif reason == "dispatch_no_wake":
                    ok, why, _, _ = _nowake_guards(cli, issue, lc, target)
                else:
                    ok, why, _, _ = _guards(cli, issue, lc)
            except CliError:
                action("unwoken_skipped:read_error")
                continue
            if not ok:
                action(f"unwoken_skipped:{why}")
                continue
            if quota:
                rec = _new_quota_record(issue_id, failed, target, now)
                records[record_key] = rec
            else:
                rec = _new_handoff_record(issue_id, lc, target, reason, now)
                records[record_key] = rec
        if pending:
            # An unresolved write may have landed: reconcile its marker first,
            # but never create a stale wakeup after a later delivery or reassignment.
            try:
                if reason == "quota_wait":
                    ok, why = _quota_guards(cli, issue, rec, now)
                elif lc and lc.get("id") == rec.get("comment_id"):
                    if reason == "dispatch_no_wake":
                        ok, why, _, _ = _nowake_guards(cli, issue, lc, target)
                    else:
                        ok, why, _, _ = _guards(cli, issue, lc, (rec or {}).get("posted_comment_id"))
                else:
                    ok, why = False, "newer_comment"
            except CliError as exc:
                rec["last_error"] = redact(str(exc))
                action("pending_uncertain")
                continue
            if not ok:
                rec["state"] = "superseded"
                store.save(state)
                action(f"unwoken_skipped:{why}")
                continue
        # Old comment-first records resume here without writing another comment.
        if not rec.get("evidence"):
            rec["evidence"] = reason
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


def _new_handoff_record(issue_id, lc, target, reason, now):
    prefix = config.NOWAKE_PREFIX if reason == "dispatch_no_wake" else config.UNWOKEN_PREFIX
    return {"comment_id": lc["id"], "comment_at": lc.get("created_at"),
            "marker": f"{prefix}{issue_id}:{lc['id']}]", "reason": reason,
            "target_id": target, "state": "wakeup_pending", "intent_at": iso(now),
            "evidence": f"{reason}; target_issue={lc.get('target_issue_ref') or issue_id}",
            "sent_at": None}


def _new_quota_record(issue_id, failed, target, now):
    reset = parse_ts(failed.get("recheck_at"))
    return {"comment_id": failed["id"], "failed_run_id": failed["id"],
            "comment_at": failed.get("completed_at"),
            "marker": f"[patrol-quota:{issue_id}:{failed['id']}]",
            "reason": "quota_wait", "target_id": target,
            "recheck_at": iso(max(reset or now, now + timedelta(minutes=1))),
            "recheck_source": "provider_hint_sgt" if reset else "unknown_dispatcher_judgement",
            "state": "wakeup_pending", "intent_at": iso(now), "sent_at": None}
