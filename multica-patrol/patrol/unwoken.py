"""Unwoken-handoff supplement: blocked/in_review, agent last comment with no
valid UUID mention, no run started after it -> one supplement per issue per 24h."""

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
        f = entry.get("facts") or {}
        finding = next((x for x in (entry.get("findings") or [])
                        if x["reason"] == "unwoken_handoff" and not x.get("exempt")), None)
        lc = (finding or {}).get("last_comment") or f.get("last_comment")
        rec = records.get(issue_id)

        def action(name):
            res.actions.append({
                "issue": issue, "action": name,
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
            ok, why, comments, _runs = _guards(cli, issue, lc)
        except CliError:
            action("unwoken_skipped:read_error")
            continue
        if not ok:
            action(f"unwoken_skipped:{why}")
            continue
        marker, body = _body(state, issue, lc)
        if any(marker in (c.get("content") or "") for c in comments):
            records[issue_id] = {"comment_id": lc["id"], "marker": marker,
                                 "state": "sent", "sent_at": iso(now)}
            action("sent")
            continue
        records[issue_id] = {"comment_id": lc["id"], "marker": marker,
                             "state": "pending", "sent_at": None}
        store.save(state)
        path = store.write_body(f"unwoken-{issue_id}.md", body)
        cli.permit_unwoken(issue_id)
        try:
            out = cli.run_unwoken_write(issue_id, path)
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
