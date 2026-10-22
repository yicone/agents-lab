"""Entry point: `python3 -m patrol <run|daily-pack|daily-post|clear-identity-violation>`."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, daily, ledger, retry, rules, scan, selfreport, unwoken
from .cli import CliError, PatrolCLI
from .facts import SGT, iso, parse_ts
from .identity import check_dispatcher_identity
from .state import Store


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def determine_slot(now_utc: datetime, slot_arg: str | None) -> str:
    if slot_arg:
        return slot_arg
    sgt = now_utc.astimezone(SGT)
    if sgt.minute >= 55:
        sgt = (sgt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    else:
        sgt = sgt.replace(minute=0, second=0, microsecond=0)
    return sgt.strftime("%Y%m%d%H")


def should_full_scan(state: dict, slot: str, now: datetime, force: bool) -> bool:
    if force:
        return True
    if len(slot) == 10 and slot[8:] == f"{config.FULL_SCAN_SLOT_HOUR:02d}":
        return True
    if not state.get("cache") or state.get("last_full_scan_at") is None:
        return True
    if not state.get("previous_run_complete", True):
        return True
    last = parse_ts(state.get("last_full_scan_at"))
    if last is None or (now - last).total_seconds() > config.FULL_SCAN_MAX_AGE_S:
        return True
    return False


def _err(out_err, message: str) -> None:
    print(f"[Error] {message}", file=out_err)


def _result_line(out, result: dict) -> None:
    print("PATROL_RESULT " + json.dumps(result, ensure_ascii=False, separators=(",", ":")),
          file=out)


def _base_result(batch_id, slot, mode, scan_kind="?", duration=0.0, writes=0):
    return {
        "batch_id": batch_id, "slot_sgt": slot, "mode": mode, "scan": scan_kind,
        "coverage": {"issues_discovered": 0, "deep_checked": 0, "from_cache": 0,
                     "errors": 0, "complete": False, "deadline_hit": False},
        "findings_count": 0, "suppressed": 0, "escalations": 0,
        "ledger": {"status": "unknown", "comment_id": None},
        "self_failures_consecutive": 0, "duration_s": round(duration, 1),
        "writes_attempted": writes,
    }


def run_hourly(args, ctx) -> int:
    out, err = ctx["out"], ctx["err"]
    cli, store = ctx["cli"], ctx["store"]
    now = ctx["now"]()
    started = ctx["monotonic"]()
    dry_run = args.dry_run
    slot = determine_slot(now, args.slot)
    batch_id = f"patrol:SGT{slot}"
    mode = "manual" if args.manual else "scheduled"
    mode_tag = f"manual:{args.manual}" if args.manual else "scheduled"
    result = _base_result(batch_id, slot, mode)

    def finish(code, ledger_status=None, comment_id=None):
        result["duration_s"] = round(ctx["monotonic"]() - started, 1)
        result["writes_attempted"] = cli.writes_attempted
        if ledger_status:
            result["ledger"] = {"status": ledger_status, "comment_id": comment_id}
        _result_line(out, result)
        return code

    if not dry_run:
        ok, reason = check_dispatcher_identity(ctx["env"])
        if not ok:
            _err(err, f"identity_refused: {reason}")
            return finish(3, "skipped_identity")

    if not store.acquire():
        return finish(0, "skipped_lock")
    try:
        state = store.load()
        if state.get("identity_violation") and not dry_run:
            _err(err, "identity_violation: "
                 + str(state["identity_violation"].get("detail", "latched")))
            result["self_failures_consecutive"] = state.get("consecutive_failures") or 0
            return finish(3, "skipped_identity")

        if not dry_run:
            inflight_batch = (state.get("inflight") or {}).get("batch_id")
            recovered = ledger.reconcile_inflight(store, cli, state, now, ctx["sleep"])
            if recovered:
                if recovered["status"] in ("held", "uncertain"):
                    selfreport.record_failure(state, slot, "inflight_hold",
                                              recovered.get("reason", "inflight unresolved"),
                                              iso(now))
                    store.save(state)
                    _err(err, f"inflight unresolved: {recovered.get('reason')}")
                    result["self_failures_consecutive"] = state["consecutive_failures"]
                    return finish(2, recovered["status"], recovered.get("comment_id"))
                if inflight_batch == batch_id:
                    state["slots"][slot] = {"status": recovered["status"], "at": iso(now),
                                            "comment_id": recovered.get("comment_id")}
                    state["last_slot"] = slot
                    store.save(state)
                    return finish(0, "duplicate", recovered.get("comment_id"))

        delivered = (state.get("slots") or {}).get(slot)
        if delivered and delivered.get("status") in ("posted", "duplicate") and not dry_run:
            return finish(0, "duplicate", delivered.get("comment_id"))

        missed = selfreport.detect_missed_slots(state, slot) if not dry_run else []
        if missed:
            selfreport.note_missed_slots(state, missed, iso(now))
        if not dry_run:
            # Record that THIS slot ran before any scan can crash: a slot that
            # raised is not "missed" (it has a run record and a failure entry).
            state["last_slot"] = slot
            store.save(state)
        prior_failures = list(state.get("failures") or [])
        run_failed = {"flag": False}
        standing_keys = set()

        def fail(kind, message):
            run_failed["flag"] = True
            selfreport.record_failure(state, slot, kind, message, iso(now))

        full = should_full_scan(state, slot, now, args.full)
        result["scan"] = "full" if full else "incremental"
        try:
            scanned = scan.run_scan(cli, state, full, now, ctx["monotonic"])
        except Exception as exc:
            if not dry_run:
                selfreport.record_failure(state, slot, "exception", str(exc), iso(now))
                selfreport.prune_state(state, now)
                store.save(state)
            _err(err, f"scan failed: {exc}")
            result["self_failures_consecutive"] = state.get("consecutive_failures") or 0
            return finish(2, "skipped_error")

        coverage = scanned["coverage"]
        result["coverage"] = {
            "issues_discovered": coverage["issues_discovered"],
            "deep_checked": coverage["deep_checked"],
            "from_cache": coverage["from_cache"],
            "errors": len(coverage["errors"]),
            "complete": coverage["complete"],
            "deadline_hit": coverage["deadline_hit"],
        }
        for issue in scanned["issues"]:
            state.setdefault("issue_index", {})[issue["id"]] = {
                "identifier": issue.get("identifier"),
                "parent_issue_id": issue.get("parent_issue_id"),
                "assignee_id": issue.get("assignee_id"),
                "status": issue.get("status"),
            }

        rows, exempt_rows, unresolved = [], [], []
        suppressed = 0
        suppressed_keys = set()
        active_keys = []
        alerted = state.setdefault("alerted", {})
        unresolved_due = True
        last_unresolved = parse_ts(state.get("unresolved_reported_at"))
        if last_unresolved and (now - last_unresolved).total_seconds() < 86400:
            unresolved_due = False

        for issue_id, entry in scanned["per_issue"].items():
            issue = entry["issue"]
            identifier = issue.get("identifier", "")
            if identifier in config.UNRESOLVED_RULE:
                if unresolved_due:
                    unresolved.append(issue)
                continue
            f = entry.get("facts") or {}
            findings = rules.exemption_for(issue, entry.get("findings") or [], f, now)
            if not findings:
                continue
            normal, exempted = [], []
            for finding in findings:
                (exempted if finding.get("exempt") else normal).append(finding)
            if exempted:
                next_review = None
                exps = [f["exempt"].get("expires_at") for f in exempted
                        if f.get("exempt") and f["exempt"].get("expires_at")]
                if exps:
                    next_review = min(exps)
                exempt_rows.append({
                    "issue": issue, "reasons": "/".join(f["reason"] for f in exempted),
                    "kind": exempted[0]["exempt"]["kind"], "next_review": next_review,
                })
            for finding in normal:
                key = ledger.dedupe_key(issue, finding)
                standing_keys.add(key)
                rec = alerted.get(key)
                last_at = parse_ts(rec.get("last_at")) if rec else None
                if last_at and (now - last_at).total_seconds() < config.DEDUPE_WINDOW_S:
                    suppressed += 1
                    suppressed_keys.add(key)
                    continue
                active_keys.append(key)
            if normal:
                fresh = [f for f in normal if ledger.dedupe_key(issue, f) in active_keys]
                if fresh:
                    rows.append({
                        "issue": issue,
                        "reasons": [f["reason"] for f in fresh],
                        "age": ledger._age(now, fresh[0].get("anchor")),
                        "last_progress": f.get("last_progress_at"),
                        "evidence_partial": f.get("evidence_partial"),
                    })
        result["findings_count"] = len(rows)
        result["suppressed"] = suppressed

        escalate_keys = selfreport.key_escalations(state, now) if not dry_run else []
        self_escalate = (not dry_run) and selfreport.pending_self_escalation(state, now)
        prior_pending = None if dry_run else state.get("pending_escalation")
        escalations_sent = []

        # Provider-error retry machine: re-reads and (non-dry) retry/escalation writes.
        err_res = retry.process_error_retries(
            store, cli, state, scanned["per_issue"], now, dry_run, ctx["sleep"])
        result["retries_sent"] = err_res.retries_sent
        result["retries_would_send"] = err_res.retries_would_send
        result["error_escalations"] = err_res.error_escalations
        unw_res = unwoken.process_unwoken(
            store, cli, state, scanned["per_issue"], now, dry_run, ctx["sleep"])
        result["unwoken_sent"] = unw_res.sent
        result["unwoken_would_send"] = unw_res.would_send
        violation = err_res.identity_violation or unw_res.identity_violation
        if violation and not dry_run:
            state["identity_violation"] = {"at": iso(now), "detail": violation}
            fail("identity_violation", violation)
            selfreport.prune_state(state, now)
            store.save(state)
            _err(err, f"identity_violation: {violation}")
            result["self_failures_consecutive"] = state["consecutive_failures"]
            return finish(3, "held")

        duration = ctx["monotonic"]() - started
        now_sgt = now.astimezone(SGT)
        body = ledger.render_batch(
            batch_id, mode_tag, slot, result["scan"], coverage, rows, exempt_rows,
            err_res.actions, suppressed, len(suppressed_keys), unresolved,
            prior_failures,
            escalate_keys + (["self"] if self_escalate else []),
            duration, now_sgt, unwoken_rows=unw_res.actions)

        if dry_run:
            print(body, file=out)
            # Dry-run persists only the refreshed read cache/issue index; nothing the
            # run mutated (alerted/dedupe/inflight/failures) was touched in this path.
            state["last_coverage"] = coverage
            store.save(state)
            return finish(0, "dry_run")

        marker = ledger.marker_for(batch_id)
        pub = ledger.publish(store, cli, state, config.LEDGER_ISSUE_ID, marker,
                             body, batch_id, now, ctx["sleep"])

        if pub["status"] == "identity_violation":
            state["identity_violation"] = {"at": iso(now), "detail": pub.get("reason")}
            fail("identity_violation", pub.get("reason", ""))
            store.save(state)
            _err(err, f"identity_violation: {pub.get('reason')}")
            result["self_failures_consecutive"] = state["consecutive_failures"]
            return finish(3, "held", pub.get("comment_id"))

        if pub["status"] == "held" and pub.get("reason") == "ledger_assigned":
            fail("ledger_assigned", "ledger issue has assignee/parent")
            store.save(state)
            _err(err, "ledger_assigned: ACM-229 has an assignee or parent; refusing to write")
            result["self_failures_consecutive"] = state["consecutive_failures"]
            return finish(2, "held")

        if pub["status"] not in ("posted", "duplicate"):
            fail("write_" + pub["status"], pub.get("reason", "ledger publish failed"))
            state["previous_run_complete"] = False
            selfreport.prune_state(state, now)
            store.save(state)
            _err(err, f"ledger publish {pub['status']}: {pub.get('reason')}")
            result["self_failures_consecutive"] = state["consecutive_failures"]
            return finish(2, pub["status"], pub.get("comment_id"))

        # Confirmed delivery: only now persist dedupe/alerted and clear self-report.
        for key in active_keys:
            rec = alerted.setdefault(key, {"first_at": iso(now), "escalated_at": None})
            if not rec.get("first_at"):
                rec["first_at"] = iso(now)
            rec["last_at"] = iso(now)

        if prior_pending:
            repub = ledger.publish(store, cli, state, config.INBOX_ISSUE_ID,
                                   prior_pending["marker"], prior_pending["body"],
                                   f"ESC-retry-{slot}", now, ctx["sleep"])
            if repub["status"] in ("posted", "duplicate"):
                for key in prior_pending.get("keys") or []:
                    selfreport.mark_key_escalated(state, key, iso(now))
                if prior_pending.get("self"):
                    state["self_escalated_at"] = iso(now)
                state["pending_escalation"] = None
            else:
                fail("escalation_write", f"pending escalation {repub['status']}")

        if escalate_keys or self_escalate:
            esc_marker = f"[patrol-escalate:ESC-{slot}]"
            esc_kind = "keys" if escalate_keys else "self"
            esc_body = selfreport.render_escalation(
                esc_marker, escalate_keys, state, self_escalate, now,
                selfreport.escalation_first_line(state, esc_kind))
            esc = ledger.publish(store, cli, state, config.INBOX_ISSUE_ID, esc_marker,
                                 esc_body, f"ESC-{slot}", now, ctx["sleep"])
            if esc["status"] in ("posted", "duplicate"):
                for key in escalate_keys:
                    selfreport.mark_key_escalated(state, key, iso(now))
                if self_escalate:
                    state["self_escalated_at"] = iso(now)
                state["pending_escalation"] = None
                escalations_sent = escalate_keys + (["self"] if self_escalate else [])
            else:
                state["pending_escalation"] = {"marker": esc_marker, "body": esc_body,
                                               "keys": escalate_keys, "self": self_escalate}
                fail("escalation_write", f"escalation {esc['status']}: {esc.get('reason')}")
        result["escalations"] = len(escalations_sent)

        if not coverage["complete"]:
            fail("coverage_incomplete", "; ".join(coverage["errors"][:5]))
        # Prior failures were reported in this delivered batch; drop exactly those.
        reported = set(map(id, prior_failures))
        state["failures"] = [f for f in state.get("failures") or []
                             if id(f) not in reported]
        if not run_failed["flag"]:
            selfreport.clear_streak(state)
        state["slots"][slot] = {"status": "posted", "at": iso(now),
                                "comment_id": pub.get("comment_id")}
        state["previous_run_complete"] = coverage["complete"]
        if full:
            state["last_full_scan_at"] = iso(now)
        if unresolved:
            state["unresolved_reported_at"] = iso(now)
        state["last_coverage"] = coverage
        selfreport.prune_state(state, now, standing_keys)
        store.save(state)

        result["self_failures_consecutive"] = state.get("consecutive_failures") or 0
        if not coverage["complete"]:
            _err(err, f"coverage incomplete: {len(coverage['errors'])} error(s)")
            return finish(2, pub["status"], pub.get("comment_id"))
        return finish(0, pub["status"], pub.get("comment_id"))
    except Exception as exc:
        try:
            state = store.load()
            selfreport.record_failure(state, slot, "exception", str(exc), iso(now))
            selfreport.prune_state(state, now)
            store.save(state)
        except Exception:
            pass
        _err(err, f"unexpected: {exc}")
        return finish(2, "skipped_error")
    finally:
        store.release()


def cmd_daily_pack(args, ctx) -> int:
    out = ctx["out"]
    cli, store = ctx["cli"], ctx["store"]
    now = ctx["now"]()
    state = store.load()
    names = dict(state.get("agent_names") or {})
    try:
        res = cli.run_json(["agent", "list", "--output", "json"])
        agents = res.data if isinstance(res.data, list) else (res.data or {}).get("agents") or []
        for agent in agents:
            names[agent.get("id")] = agent.get("name")
        state["agent_names"] = names
        store.save(state)
    except Exception:
        pass
    pack = daily.build_pack(state, names, state.get("last_coverage"), now)
    if args.format == "md":
        print(daily.render_md(pack), file=out)
    else:
        print(json.dumps(pack, ensure_ascii=False, indent=2), file=out)
    return 0


def cmd_daily_post(args, ctx) -> int:
    out, err = ctx["out"], ctx["err"]
    cli, store = ctx["cli"], ctx["store"]
    now = ctx["now"]()
    date = args.slot or now.astimezone(SGT).strftime("%Y%m%d")
    result = {"daily": f"SGT{date}", "status": "unknown", "comment_id": None}

    ok, reason = check_dispatcher_identity(ctx["env"])
    if not ok:
        _err(err, f"identity_refused: {reason}")
        result["status"] = "identity_refused"
        _result_line(out, result)
        return 3
    if not store.acquire():
        result["status"] = "skipped_lock"
        _result_line(out, result)
        return 0
    try:
        state = store.load()
        if state.get("identity_violation"):
            _err(err, "identity_violation: latched")
            result["status"] = "identity_violation"
            _result_line(out, result)
            return 3
        try:
            body = Path(args.file).read_text()
        except OSError as exc:
            _err(err, f"daily_body_invalid: {exc}")
            result["status"] = "daily_body_invalid"
            _result_line(out, result)
            return 2
        why = daily.validate_daily_body(body)
        if why:
            _err(err, f"daily_body_invalid: {why}")
            result["status"] = "daily_body_invalid"
            _result_line(out, result)
            return 2
        existing = (state.get("daily") or {}).get(date)
        if existing and existing.get("status") == "posted":
            result.update(status="duplicate", comment_id=existing.get("comment_id"))
            _result_line(out, result)
            return 0
        try:
            inbox = cli.run_json(["issue", "get", config.INBOX_ISSUE_ID,
                                  "--output", "json"]).data
        except CliError as exc:
            _err(err, f"inbox get: {exc}")
            result["status"] = "uncertain"
            _result_line(out, result)
            return 2
        if inbox.get("status") in ("done", "cancelled"):
            _err(err, f"inbox {inbox.get('status')}: refusing daily post")
            result["status"] = "inbox_closed"
            _result_line(out, result)
            return 2
        batch_id = f"daily:SGT{date}"
        marker = f"[patrol-daily:{batch_id}"
        # Marker goes on line 2; line 1 is the user-facing Chinese first line.
        lines = body.splitlines()
        idx = next((i for i, ln in enumerate(lines) if ln.strip()), 0)
        lines.insert(idx + 1, f"[patrol-daily:{batch_id} v2]")
        full_body = "\n".join(lines) + "\n"
        pub = ledger.publish(store, cli, state, config.INBOX_ISSUE_ID, marker,
                             full_body, batch_id, now, ctx["sleep"])
        if pub["status"] == "identity_violation":
            state["identity_violation"] = {"at": iso(now), "detail": pub.get("reason")}
            store.save(state)
            _err(err, f"identity_violation: {pub.get('reason')}")
            result["status"] = "identity_violation"
            _result_line(out, result)
            return 3
        if pub["status"] in ("posted", "duplicate"):
            state.setdefault("daily", {})[date] = {
                "status": "posted", "comment_id": pub.get("comment_id"), "at": iso(now)}
            store.save(state)
            result.update(status=pub["status"], comment_id=pub.get("comment_id"))
            _result_line(out, result)
            return 0
        _err(err, f"daily publish {pub['status']}: {pub.get('reason')}")
        result["status"] = pub["status"]
        _result_line(out, result)
        return 2
    finally:
        store.release()


def cmd_clear_identity_violation(args, ctx) -> int:
    store = ctx["store"]
    state = store.load()
    violation = state.get("identity_violation")
    if not violation:
        print("no identity_violation latched", file=ctx["out"])
        return 0
    state["identity_violation"] = None
    store.save(state)
    print(f"cleared identity_violation: {json.dumps(violation, ensure_ascii=False)}",
          file=ctx["out"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="patrol")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--slot")
    run.add_argument("--manual")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--full", action="store_true")
    pack = sub.add_parser("daily-pack")
    pack.add_argument("--format", choices=["json", "md"], default="json")
    post = sub.add_parser("daily-post")
    post.add_argument("--file", required=True)
    post.add_argument("--slot")
    sub.add_parser("clear-identity-violation")
    return parser


def main(argv=None, *, env=None, exec_fn=None, sleep_fn=None, now_fn=None,
         monotonic_fn=None, out=None, err=None, state_dir=None) -> int:
    args = build_parser().parse_args(argv)
    env = dict(os.environ if env is None else env)
    out = out or sys.stdout
    err = err or sys.stderr
    cli = PatrolCLI(
        allowed_write_ids=frozenset({config.LEDGER_ISSUE_ID, config.INBOX_ISSUE_ID}),
        env=env,
        _exec=exec_fn or __import__("subprocess").run,
        _sleep=sleep_fn or time.sleep,
    )
    ctx = {
        "cli": cli, "store": Store(state_dir), "env": env,
        "now": now_fn or _now_utc, "monotonic": monotonic_fn or time.monotonic,
        "sleep": sleep_fn or time.sleep, "out": out, "err": err,
    }
    if args.command == "run":
        return run_hourly(args, ctx)
    if args.command == "daily-pack":
        return cmd_daily_pack(args, ctx)
    if args.command == "daily-post":
        return cmd_daily_post(args, ctx)
    if args.command == "clear-identity-violation":
        return cmd_clear_identity_violation(args, ctx)
    return 2


if __name__ == "__main__":
    sys.exit(main())
