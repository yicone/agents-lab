"""completed+[Error] provider-error detection -> one retry -> escalate."""

from __future__ import annotations

from datetime import timedelta

from . import config
from .cli import CliError
from .facts import iso, parse_ts
from .identity import verify_comment_author
from .ledger import publish, verify_post_author
from .selfreport import escalation_first_line


def _mention(issue: dict) -> str:
    return f"[{issue.get('identifier', issue['id'])}](mention://issue/{issue['id']})"


def _newest_live(runs: list[dict]) -> dict | None:
    for r in runs or []:
        if r.get("status") != "cancelled":
            return r
    return None


def _error_comments(comments: list[dict], agent_id: str, started, window_end):
    hits = []
    for c in comments or []:
        if c.get("author_type") != "agent" or c.get("author_id") != agent_id:
            continue
        if not (c.get("content") or "").lstrip().startswith("[Error]"):
            continue
        ts = parse_ts(c.get("created_at"))
        if ts is None or (started and ts < started) or ts > window_end:
            continue
        hits.append((ts, c))
    hits.sort(key=lambda x: x[0])
    return hits


def _retry_body(issue: dict, agent_name: str, agent_id: str, run_id: str) -> tuple[str, str]:
    marker = f"{config.RETRY_PREFIX}{issue['id']}:{run_id}]"
    first = (f"{config.FIRST_LINE_NEXT} {agent_name} 做："
             "重试上一轮以 [Error] 结束的原任务。")
    body = (first + "\n" + marker + "\n"
            + f"[@{agent_name}](mention://agent/{agent_id}) "
            + "上一轮 run 以 provider [Error] 结束，原任务要求不变，"
              "请继续/重试原任务（retry 1 of 1）。" + "\n")
    return marker, body


def _provider_escalation_body(state, issue, rec, reason, new_run_id, error_run_id) -> tuple[str, str]:
    ident = issue.get("identifier", issue["id"])
    marker = f"[patrol-escalate:ERR-{issue['id']}-{new_run_id}]"
    first = escalation_first_line(state, "provider", ident)
    agent_name = (state.get("agent_names") or {}).get(rec.get("agent_id") or "", "?")
    lines = [first, marker,
             f"- {_mention(issue)} agent={agent_name} "
             f"error_run={error_run_id} new_run={new_run_id} reason={reason}"]
    return marker, "\n".join(lines) + "\n"


def _comment_list(cli, issue_id: str, since) -> list[dict]:
    res = cli.run_json(["issue", "comment", "list", issue_id,
                        "--since", iso(since), "--compact", "--output", "json"])
    return res.data if isinstance(res.data, list) else []


def _still_latest_error(comments: list[dict], pe: dict) -> tuple[bool, str]:
    """No member comment and no other agent comment after the error comment."""
    error_at = parse_ts(pe.get("error_at"))
    for c in comments:
        ts = parse_ts(c.get("created_at"))
        if ts is None or error_at is None or ts <= error_at:
            continue
        content = (c.get("content") or "").lstrip()
        if config.RETRY_PREFIX in content or \
                content.startswith(("[patrol-", "[dispatch-batch:")):
            continue  # patrol/dispatch infrastructure comments are not a reply
        if c.get("author_type") == "member":
            return False, "member_replied"
        if c.get("author_type") == "agent" and c.get("author_id") != pe.get("agent_id"):
            return False, "other_agent_replied"
        if c.get("id") != pe.get("comment_id") and c.get("author_id") == pe.get("agent_id") \
                and not (c.get("content") or "").lstrip().startswith("[patrol-retry:"):
            return False, "agent_continued"
    return True, "ok"


def _guarded_recheck(cli, issue: dict, pe: dict) -> tuple[bool, str, list[dict]]:
    issue_id = issue["id"]
    fresh = cli.run_json(["issue", "get", issue_id, "--output", "json"]).data or {}
    if fresh.get("assignee_id") != pe.get("agent_id"):
        return False, "assignee_changed", []
    if fresh.get("status") in ("done", "cancelled"):
        return False, "issue_closed", []
    active = cli.run_json(["issue", "runs", issue_id, "--active", "--output", "json"]).data
    if isinstance(active, list) and active:
        return False, "active_run", []
    error_at = parse_ts(pe.get("error_at")) or parse_ts(pe.get("completed_at"))
    if error_at is None:
        return False, "no_error_time", []
    comments = _comment_list(cli, issue_id, error_at - timedelta(seconds=1))
    ok, why = _still_latest_error(comments, pe)
    return ok, why, comments


class _Result:
    def __init__(self):
        self.actions = []           # rows for the ledger provider section
        self.retries_sent = 0
        self.retries_would_send = 0
        self.error_escalations = 0
        self.identity_violation = None


def _record(state: dict, issue_id: str) -> dict:
    return state.setdefault("error_retry", {}).setdefault(issue_id, {})


def _drop(state: dict, issue_id: str) -> None:
    (state.get("error_retry") or {}).pop(issue_id, None)


def _escalate(store, cli, state, issue, rec, reason, new_run_id, error_run_id,
              now, sleep, res: _Result) -> str:
    marker, body = _provider_escalation_body(state, issue, rec, reason,
                                             new_run_id, error_run_id)
    pub = publish(store, cli, state, config.INBOX_ISSUE_ID, marker, body,
                  f"ERR-{issue['id']}-{new_run_id}", now, sleep)
    if pub["status"] in ("posted", "duplicate"):
        res.error_escalations += 1
        return "escalated"
    return f"escalation_{pub['status']}"


def process_error_retries(store, cli, state, per_issue: dict, now,
                          dry_run: bool, sleep) -> _Result:
    """Run the provider-error state machine for every scanned issue."""
    res = _Result()
    records = state.setdefault("error_retry", {})
    if dry_run:
        records = dict(records)  # never persist retry records under --dry-run
    names = state.get("agent_names") or {}

    for issue_id, entry in per_issue.items():
        issue = entry["issue"]
        if issue_id in config.PERSISTENT_IDS or \
                issue.get("identifier") in config.UNRESOLVED_RULE:
            continue  # never retry/escalate-write on ledger, inbox, or ACM-215
        f = entry.get("facts") or {}
        pe = f.get("provider_error")
        rec = records.get(issue_id)
        ident = issue.get("identifier", issue_id)

        def action(name):
            res.actions.append({
                "issue": issue, "action": name,
                "agent_name": names.get((pe or rec or {}).get("agent_id"), "?"),
                "run_id": (pe or rec or {}).get("run_id"),
            })

        if rec:
            # Drop silently when the issue left scope, closed, or changed owner.
            if (issue.get("status") in ("done", "cancelled")
                    or issue.get("assignee_id") != rec.get("agent_id")):
                if not dry_run:
                    _drop(state, issue_id)
                continue
            if rec.get("state") == "escalated":
                continue
            if rec.get("state") == "pending":
                marker = rec.get("retry_marker", "")
                try:
                    comments = _comment_list(
                        cli, issue_id, now - timedelta(seconds=config.DUP_LOOKBACK_S))
                except CliError:
                    action("pending_uncertain")
                    continue
                if any(marker in (c.get("content") or "") for c in comments):
                    rec["state"] = "retried"
                    rec["retried_at"] = rec.get("retried_at") or iso(now)
                    action("retried")
                    continue
                # Intent was never delivered: fall through to fresh-send logic.
                pe = pe or rec.get("provider_error")
            if rec.get("state") == "retried" and (not f or f.get("active_runs")):
                action("waiting")
                continue
            if rec.get("state") == "retried":
                retried_at = parse_ts(rec.get("retried_at"))
                newest = _newest_live(f.get("runs") or [])
                if newest is None or newest.get("id") == rec.get("run_id"):
                    if retried_at and (now - retried_at).total_seconds() \
                            >= config.RETRY_NO_RUN_ESCALATE_S:
                        outcome = _escalate(store, cli, state, issue, rec,
                                            "retry_not_started", rec.get("run_id"),
                                            rec.get("run_id"), now, sleep, res) \
                            if not dry_run else "would_escalate"
                        rec["state"] = "escalated"
                        action(outcome)
                    else:
                        action("waiting")
                    continue
                # A newer run exists.
                if pe and pe.get("run_id") == newest.get("id"):
                    outcome = _escalate(store, cli, state, issue, rec,
                                        "error_again", newest.get("id"),
                                        rec.get("run_id"), now, sleep, res) \
                        if not dry_run else "would_escalate"
                    rec["state"] = "escalated"
                    action(outcome)
                else:
                    if not dry_run:
                        _drop(state, issue_id)
                    action("recovered")
                continue
            if not pe:
                action("waiting")
                continue

        if not pe:
            continue
        if pe.get("stale"):
            action("retry_skipped_stale")
            continue
        if pe.get("agent_id") == config.DISPATCHER_AGENT_ID:
            outcome = _escalate(store, cli, state, issue, {"agent_id": pe["agent_id"]},
                                "dispatcher_error", pe["run_id"], pe["run_id"],
                                now, sleep, res) if not dry_run else "would_escalate"
            if not dry_run:
                records[issue_id] = {"run_id": pe["run_id"], "agent_id": pe["agent_id"],
                                     "state": "escalated", "retried_at": None}
            action(outcome)
            continue
        # Fresh detection: re-read guards before any write.
        try:
            ok, why, comments = _guarded_recheck(cli, issue, pe)
        except CliError as exc:
            action(f"retry_skipped:read_error")
            continue
        if not ok:
            action(f"retry_skipped:{why}")
            continue
        marker, body = _retry_body(issue, names.get(pe["agent_id"], "?"),
                                   pe["agent_id"], pe["run_id"])
        if any(marker in (c.get("content") or "") for c in comments):
            if not dry_run:
                records[issue_id] = {"run_id": pe["run_id"], "agent_id": pe["agent_id"],
                                     "state": "retried", "retried_at": iso(now),
                                     "retry_marker": marker}
            action("retried")
            continue
        if dry_run:
            res.retries_would_send += 1
            action("would_retry")
            continue
        if res.retries_sent >= config.MAX_RETRIES_PER_RUN:
            action("retry_deferred")
            continue
        # Durable intent before the write.
        records[issue_id] = {"run_id": pe["run_id"], "agent_id": pe["agent_id"],
                             "state": "pending", "retried_at": None,
                             "retry_marker": marker, "provider_error": pe}
        store.save(state)
        path = store.write_body(f"retry-{issue_id}.md", body)
        cli.permit_retry(issue_id)
        try:
            out = cli.run_retry_write(issue_id, path)
            comment_id = (out.data or {}).get("id") if isinstance(out.data, dict) else None
            since = now - timedelta(seconds=config.DUP_LOOKBACK_S)
            ok, why2 = verify_post_author(cli, issue_id, comment_id, since)
            if not ok:
                res.identity_violation = why2
                action("identity_violation")
                continue
            rec2 = records[issue_id]
            rec2["state"] = "retried"
            rec2["retried_at"] = iso(now)
            rec2["newest_run_at_retry"] = (_newest_live(f.get("runs") or []) or {}).get("id")
            res.retries_sent += 1
            action("retried")
        except CliError:
            sleep(2)
            try:
                after = _comment_list(cli, issue_id,
                                      now - timedelta(seconds=config.DUP_LOOKBACK_S))
                if any(marker in (c.get("content") or "") for c in after):
                    records[issue_id]["state"] = "retried"
                    records[issue_id]["retried_at"] = iso(now)
                    res.retries_sent += 1
                    action("retried")
                else:
                    action("uncertain")  # pending record reconciled next run
            except CliError:
                action("uncertain")
    return res
