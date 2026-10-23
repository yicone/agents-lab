"""Unwoken supplements: rule 9 (blocked/in_review agent last comment with no
valid UUID mention, no run started after it) and rule 10 (in_progress issue
whose last comment is a dispatch-class dispatcher comment with no valid @ of
the target agent and no target run started within 15 min). One supplement per
issue per 24h, shared cap per run."""

from __future__ import annotations

from datetime import timedelta

from . import config
from .cli import CliError
from .facts import iso, parse_ts
from .retry import _comment_list, _mention


def _run_started(r: dict):
    return (parse_ts(r.get("started_at")) or parse_ts(r.get("dispatched_at"))
            or parse_ts(r.get("created_at")))


def _body(state, issue, lc) -> tuple[str, str]:
    agent_id = lc.get("author_id") or "?"
    agent_name = (state.get("agent_names") or {}).get(agent_id, agent_id)
    marker = f"{config.UNWOKEN_PREFIX}{issue['id']}:{lc['id']}]"
    disp = f"[@副调度](mention://agent/{config.DISPATCHER_AGENT_ID})"
    first = (f"{config.FIRST_LINE_NEXT} 副调度 做：决定复验、返工或发起顶层批准请求。")
    body = (
        first + "\n" + marker + "\n"
        + disp + " " + agent_name + " 于 " + (lc.get("created_at") or "?")
        + " 交付/判定了一条评论，但没有有效 UUID @ 唤醒下一环。"
        + "由副调度决定复验、返工或发起顶层批准请求。" + "\n")
    return marker, body


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


def _nowake_body(state, issue, lc, target_id) -> tuple[str, str]:
    target_name = (state.get("agent_names") or {}).get(target_id, target_id)
    marker = f"{config.NOWAKE_PREFIX}{issue['id']}:{lc['id']}]"
    disp = f"[@副调度](mention://agent/{config.DISPATCHER_AGENT_ID})"
    first = (f"{config.FIRST_LINE_NEXT} 副调度 做：确认派活是否已唤醒目标 agent，"
             "必要时补发有效 UUID @ 或重新派活。")
    body = (
        first + "\n" + marker + "\n"
        + disp + " 派活评论 " + str(lc.get("id")) + "（"
        + (lc.get("created_at") or "?") + "）以 " + str(target_name)
        + " 为目标，但没有该 agent 的有效 UUID @，且 15 分钟内没有它的 run"
        " 启动，疑似未唤醒。" + "\n")
    return marker, body


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
        if content.startswith(("[patrol-", "[dispatch-batch:")):
            continue
        if latest is None or (parse_ts(c.get("created_at")) or comment_at) > \
                (parse_ts(latest.get("created_at")) or comment_at):
            latest = c
    if latest is None or latest.get("id") != lc.get("id"):
        return False, "newer_comment", comments, []
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
        if content.startswith(("[patrol-", "[dispatch-batch:")):
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


class _Result:
    def __init__(self):
        self.actions = []
        self.sent = 0
        self.would_send = 0
        self.identity_violation = None


def process_unwoken(store, cli, state, per_issue, now, dry_run, sleep) -> _Result:
    res = _Result()
    records = state.setdefault("unwoken", {})
    if dry_run:
        records = dict(records)  # dry-run never mutates state

    for issue_id, entry in per_issue.items():
        issue = entry["issue"]
        if issue_id in config.PERSISTENT_IDS or \
                issue.get("identifier") in config.UNRESOLVED_RULE:
            continue  # ledger/inbox/ACM-215 are never written to by this path
        f = entry.get("facts") or {}
        finding = next((x for x in (entry.get("findings") or [])
                        if x["reason"] in ("unwoken_handoff", "dispatch_no_wake")
                        and not x.get("exempt")), None)
        reason = (finding or {}).get("reason")
        lc = (finding or {}).get("last_comment") or f.get("last_comment")
        target = _resolve_target(state, issue, lc) \
            if reason == "dispatch_no_wake" else None
        rec = records.get(issue_id)

        def action(name):
            res.actions.append({
                "issue": issue, "action": name, "reason": reason,
                "agent_name": (state.get("agent_names") or {}).get(
                    (lc or {}).get("author_id"), "?"),
                "comment_at": (lc or {}).get("created_at"),
            })

        if rec:
            sent_at = parse_ts(rec.get("sent_at"))
            if sent_at and (now - sent_at).total_seconds() < config.UNWOKEN_DEDUPE_S:
                action("deduped")
                continue
            if rec.get("state") == "pending":
                try:
                    comments = _comment_list(
                        cli, issue_id, now - timedelta(seconds=config.DUP_LOOKBACK_S))
                except CliError:
                    action("pending_uncertain")
                    continue
                if any((rec.get("marker") or "") in (c.get("content") or "")
                       for c in comments):
                    rec["state"] = "sent"
                    rec["sent_at"] = rec.get("sent_at") or iso(now)
                    action("sent")
                    continue
                # Not delivered: fall through to a fresh guarded send.

        if not finding:
            continue
        if dry_run:
            res.would_send += 1
            action("would_send")
            continue
        if res.sent >= config.MAX_UNWOKEN_PER_RUN:
            action("unwoken_deferred")
            continue
        try:
            if reason == "dispatch_no_wake":
                ok, why, comments, _runs = _nowake_guards(cli, issue, lc, target)
            else:
                ok, why, comments, _runs = _guards(cli, issue, lc)
        except CliError:
            action("unwoken_skipped:read_error")
            continue
        if not ok:
            action(f"unwoken_skipped:{why}")
            continue
        if reason == "dispatch_no_wake":
            marker, body = _nowake_body(state, issue, lc, target)
            marker_prefix = config.NOWAKE_PREFIX
        else:
            marker, body = _body(state, issue, lc)
            marker_prefix = config.UNWOKEN_PREFIX
        if any(marker in (c.get("content") or "") for c in comments):
            records[issue_id] = {"comment_id": lc["id"], "marker": marker,
                                 "reason": reason,
                                 "state": "sent", "sent_at": iso(now)}
            action("sent")
            continue
        records[issue_id] = {"comment_id": lc["id"], "marker": marker,
                             "reason": reason,
                             "state": "pending", "sent_at": None}
        store.save(state)
        path = store.write_body(f"unwoken-{issue_id}.md", body)
        cli.permit_unwoken(issue_id)
        try:
            out = cli.run_unwoken_write(issue_id, path, marker_prefix=marker_prefix)
            comment_id = (out.data or {}).get("id") if isinstance(out.data, dict) else None
            from .ledger import verify_post_author
            since = now - timedelta(seconds=config.DUP_LOOKBACK_S)
            ok2, why2 = verify_post_author(cli, issue_id, comment_id, since)
            if not ok2:
                res.identity_violation = why2
                action("identity_violation")
                continue
            rec2 = records[issue_id]
            rec2["state"] = "sent"
            rec2["sent_at"] = iso(now)
            res.sent += 1
            action("sent")
        except CliError:
            sleep(2)
            try:
                after = _comment_list(cli, issue_id,
                                      now - timedelta(seconds=config.DUP_LOOKBACK_S))
                if any(marker in (c.get("content") or "") for c in after):
                    records[issue_id]["state"] = "sent"
                    records[issue_id]["sent_at"] = iso(now)
                    res.sent += 1
                    action("sent")
                else:
                    action("uncertain")
            except CliError:
                action("uncertain")
    return res
