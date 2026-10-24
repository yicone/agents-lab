"""Pure delivery metrics. No network, writes or comment-body classification.

Input is the minimized snapshot produced by metrics_history and an optional curated
ledger. A ledger coverage event attests a complete channel over [since, until).
Absence of events alone never attests zero attention, rework, waiting or cost.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

UTC = timezone.utc
WAIT = {"waiting_user", "waiting_quota", "waiting_agent", "waiting_unclassified"}
ATTENTION = {"approval", "user_reply", "manual_handling"}


def ts(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(UTC) if dt.tzinfo else None
    except (ValueError, TypeError):
        return None


def iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def roots(issues):
    """Resolve arbitrary depth within a workspace; missing/cyclic parents stay unknown."""
    index = {(i["workspace_id"], i["id"]): i for i in issues}
    resolved = {}
    for key in index:
        cur, seen = key, set()
        while cur in index and cur not in seen:
            seen.add(cur)
            parent = index[cur].get("parent_issue_id")
            if not parent:
                resolved[key] = cur
                break
            cur = (cur[0], parent)
        else:
            resolved[key] = None
    return index, resolved


def dedupe(rows, key):
    """Conflicting duplicate IDs are excluded, never arbitrarily last-write-wins."""
    by_id, conflicts = {}, set()
    for row in rows:
        identity = key(row)
        if identity in by_id and by_id[identity] != row:
            conflicts.add(identity)
        by_id[identity] = row
    return [r for k, r in by_id.items() if k not in conflicts], len(conflicts)


def measure(values, candidates, *, mode="mean", source_complete=True):
    count = len(values)
    value = None
    if count:
        value = median(values) if mode == "median" else sum(values) / count
    return {"value": value, "known": count, "candidates": candidates,
            "unknown": max(0, candidates - count),
            "coverage": count / candidates if candidates else None,
            "status": ("complete" if count == candidates and source_complete else "partial") if count else "unknown"}


def _covered(events, channel, start, end):
    return any(e.get("event_type") == "coverage" and e.get("channel") == channel
               and ts(e.get("since")) and ts(e.get("until"))
               and ts(e["since"]) <= start and ts(e["until"]) >= end
               and e.get("confidence") == "complete" for e in events)


def _waiting(events, start, end, timeline_complete):
    statuses = sorted((e for e in events if e.get("event_type") == "status"
                       and ts(e.get("at")) and start <= ts(e["at"]) <= end),
                      key=lambda e: (ts(e["at"]), e.get("event_id", "")))
    if not timeline_complete or not statuses:
        return None
    at_states = defaultdict(set)
    for event in statuses:
        at_states[event["at"]].add(event.get("status"))
    if any(len(states) > 1 for states in at_states.values()):
        return None
    # History starts at created_at; explicit previous_status establishes the initial segment.
    current = statuses[0].get("previous_status")
    if ts(statuses[0]["at"]) > start and not current:
        return None
    buckets = dict.fromkeys(WAIT, 0.0)
    cursor = start
    boundaries = sorted({start, end} | {ts(e["at"]) for e in statuses} |
                        {ts(e["at"]) for e in events if e.get("event_type") in {"wait_open", "wait_close"}
                         and ts(e.get("at")) and start <= ts(e["at"]) <= end})
    reason = None
    for boundary in boundaries:
        if current in {"blocked", "in_review"}:
            bucket = reason if reason in WAIT else ("waiting_agent" if current == "in_review" else "waiting_unclassified")
            buckets[bucket] += (boundary - cursor).total_seconds()
        cursor = boundary
        for e in statuses:
            if ts(e["at"]) == boundary:
                current, reason = e.get("status"), e.get("wait_reason")
        for e in events:
            if ts(e.get("at")) == boundary:
                if e.get("event_type") == "wait_open":
                    reason = e.get("wait_reason")
                elif e.get("event_type") == "wait_close":
                    reason = None
    seconds = (end - start).total_seconds()
    return {k: v / seconds for k, v in buckets.items()} if seconds > 0 else None


def _build_report(snapshot, ledger=(), *, now=None, days=28, threshold=None,
                 evidence="ledger",
                 wip_floor=None, rework_ceiling=None, max_age_hours=24):
    now = now or datetime.now(UTC)
    if not 1 <= days <= 90 or not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise ValueError("days must be 1..90 and max_age_hours must be positive")
    for value in (threshold, wip_floor, rework_ceiling):
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("threshold parameters must be finite and non-negative")
    start = now - timedelta(days=days)
    collected = ts(snapshot.get("collected_at"))
    fresh = bool(collected and timedelta(0) <= now - collected <= timedelta(hours=max_age_hours))
    source_complete = bool(snapshot.get("complete")) and fresh
    index, parent = roots(snapshot.get("issues", []))
    if evidence == "proxy":
        source_complete = source_complete and not any(root is None for root in parent.values()) and all(
            "proxy_exclusion" in i and i.get("proxy_exclusion") != "unknown_kind"
            for key, i in index.items() if parent.get(key) == key and i.get("status") == "done")
    raw_events = list(snapshot.get("events", []))
    # A curated classification enriches an observed comment using the SAME source ID.
    # A mismatched issue or timestamp is still a conflict, never a silent override.
    for classified in ledger:
        raw_events = [e for e in raw_events if not (
            e.get("event_type") in {"comment_observed", "attention_unclassified"}
            and classified.get("event_type") in ATTENTION | {"rework"}
            and all(e.get(f) == classified.get(f) for f in ("workspace_id", "issue_id", "event_id", "at")))]
        raw_events.append(classified)
    events, event_conflicts = dedupe(raw_events,
                                    lambda e: (e.get("workspace_id"), e.get("event_id")))
    grouped = defaultdict(list)
    unattributed_events = 0
    for e in events:
        key = (e.get("workspace_id"), e.get("issue_id") or e.get("parent_issue_id"))
        root = parent.get(key)
        if root is None:
            unattributed_events += 1
        elif e.get("confidence") != "unknown":
            grouped[root].append(e)
    runs, run_conflicts = dedupe(snapshot.get("runs", []), lambda r: (r.get("workspace_id"), r.get("id")))
    run_groups = defaultdict(list)
    unattributed_runs = 0
    for r in runs:
        root = parent.get((r.get("workspace_id"), r.get("issue_id")))
        if root is None:
            unattributed_runs += 1
        else:
            run_groups[root].append(r)
    deliveries = []
    for key, issue in index.items():
        if parent.get(key) != key:
            continue
        ev = grouped[key]
        root_events = [e for e in ev if (e.get("issue_id") or e.get("parent_issue_id")) == key[1]]
        statuses = sorted((e for e in root_events if e.get("event_type") == "status" and ts(e.get("at"))), key=lambda e: ts(e["at"]))
        starts = [ts(e["at"]) for e in statuses if e.get("status") == "in_progress"]
        history_ok = snapshot.get("history_complete", {}).get(f"{key[0]}/{key[1]}", False)
        begin = (min(starts) if starts else ts(issue.get("created_at"))) if history_ok else None
        complete_events = [e for e in root_events if e.get("event_type") == "delivery_completed"
                           and e.get("source") == "delivery_ledger" and e.get("delivery_evidence")
                           and ts(e.get("at")) and e.get("confidence") == "complete"]
        ends = {ts(e["at"]) for e in complete_events}
        end = next(iter(ends)) if len(ends) == 1 else None
        status = issue.get("status")
        if evidence == "proxy":
            # Only a complete, root-owned status timeline establishes the final close.
            # Reopen/reclose is one delivery at its latest done, never one per transition.
            eligible = "proxy_exclusion" in issue and issue["proxy_exclusion"] is None
            if not eligible:
                continue
            past = [e for e in statuses if ts(e["at"]) < now]
            ambiguous = any(len({e.get("status") for e in past if ts(e["at"]) == at}) > 1
                            for at in {ts(e["at"]) for e in past})
            end = ts(past[-1]["at"]) if history_ok and not ambiguous and past and past[-1].get("status") == "done" else None
        valid = bool(status == "done" and end and end < now and (not begin or begin <= end))
        # done-without-ledger stays in the coverage population even if updated_at changed later.
        done_times = [ts(e["at"]) for e in statuses if e.get("status") == "done" and start <= ts(e["at"]) < now]
        unknown_end_in_window = not end and (done_times or not history_ok or not any(
            e.get("status") == "done" for e in statuses))
        candidate = bool(status == "done" and ((end and start <= end < now) or unknown_end_in_window))
        row = {"delivery_id": f"{key[0]}/{key[1]}", "workspace_id": key[0], "issue_id": key[1],
               "agent_id": issue.get("assignee_id"), "status": status, "candidate": candidate, "evidence": evidence,
               "completed_at": iso(end) if valid else None,
               "start_at": iso(begin) if begin else None, "cross_window": bool(begin and begin < start),
               "cycle_hours": (end - begin).total_seconds() / 3600 if valid and begin else None,
               "attention": None, "first_pass": None, "rework_rounds": None, "waiting": None,
               "multica_cost": None, "cost_by_runtime": {},
               "automatic_recovery_warnings": sum(bool(r.get("automatic_recovery")) for r in run_groups[key])
                   if run_groups[key] and all("automatic_recovery" in r for r in run_groups[key]) else None,
               "completion_time_unknown": status == "done" and not valid}
        if valid and begin:
            attention_start = ts(issue.get("created_at")) or begin
            lifetime = [e for e in ev if ts(e.get("at")) and attention_start <= ts(e["at"]) <= end]
            unclassified_attention = any(e.get("event_type") == "attention_unclassified" or
                                         (e.get("event_type") in ATTENTION and e.get("confidence") != "complete") for e in lifetime)
            if _covered(root_events, "attention", attention_start, end) and not event_conflicts and not unclassified_attention:
                row["attention"] = sum(e.get("event_type") in ATTENTION for e in lifetime)
            if _covered(root_events, "rework", begin, end) and not event_conflicts and not unclassified_attention:
                reviews = [ts(e["at"]) for e in statuses if e.get("status") == "in_review" and ts(e["at"]) <= end]
                if reviews and history_ok:
                    reworks = [e for e in lifetime if e.get("event_type") == "rework" and ts(e["at"]) >= min(reviews)]
                    row["rework_rounds"] = len(reworks)
                    row["first_pass"] = int(not reworks)
            history_ok = snapshot.get("history_complete", {}).get(row["delivery_id"], False)
            row["waiting"] = _waiting(root_events, begin, end, history_ok and not event_conflicts)
        # Costs of cancelled and open issues are accounted for separately, never spread over deliveries.
        cancel_times = [ts(e["at"]) for e in statuses if e.get("status") in {"cancelled", "canceled"} and ts(e["at"]) <= now]
        cost_end = end if valid else max(cancel_times) if cancel_times else now
        run_source_ok = not any(e.get("workspace_id") == key[0] and e.get("resource") == "runs" for e in snapshot.get("errors", []))
        runs_since = ts(snapshot.get("since"))
        if (begin and run_source_ok and (not runs_since or begin >= runs_since)
                and _covered(root_events, "cost", begin, cost_end) and not run_conflicts and not event_conflicts):
            costs = [e for e in ev if e.get("event_type") == "cost_observed"
                     and ts(e.get("at")) and begin <= ts(e["at"]) <= cost_end]
            cost_values = [{"related_run_id": e.get("related_run_id"), "cost_usd_ticks": e.get("cost_usd_ticks")} for e in costs]
            unique, conflicts = dedupe(cost_values, lambda e: e.get("related_run_id"))
            ids = {r["id"] for r in run_groups[key]}
            if (not conflicts and all(e.get("related_run_id") in ids and
                isinstance(e.get("cost_usd_ticks"), (int, float)) and
                not isinstance(e.get("cost_usd_ticks"), bool) and math.isfinite(e["cost_usd_ticks"]) and
                e["cost_usd_ticks"] >= 0 for e in unique) and
                {e.get("related_run_id") for e in unique} == ids):
                row["multica_cost"] = sum(e["cost_usd_ticks"] for e in unique)
                for e in unique:
                    run = next(r for r in run_groups[key] if r["id"] == e["related_run_id"])
                    runtime = run.get("runtime_id") or "unknown"
                    row["cost_by_runtime"][runtime] = row["cost_by_runtime"].get(runtime, 0) + e["cost_usd_ticks"]
        deliveries.append(row)

    candidates = [d for d in deliveries if d["candidate"]]
    valid = [d for d in candidates if d["completed_at"]]
    n = len(candidates)
    def metric(field, mode="mean"):
        return measure([d[field] for d in valid if d[field] is not None], n, mode=mode, source_complete=source_complete)
    cycle = metric("cycle_hours", "median")
    cycles = sorted(d["cycle_hours"] for d in valid if d["cycle_hours"] is not None)
    cycle["p90"] = cycles[max(0, math.ceil(.9 * len(cycles)) - 1)] if cycles else None
    waiting = [d["waiting"] for d in valid if d["waiting"] is not None]
    waiting_metric = measure([sum(v.values()) for v in waiting], n, source_complete=source_complete)
    # Duration-weighted waiting share, rather than an average of per-delivery fractions.
    waiting_rows = [d for d in valid if d["waiting"] is not None and d["cycle_hours"] is not None and d["cycle_hours"] > 0]
    total_hours = sum(d["cycle_hours"] for d in waiting_rows)
    waiting_metric["value"] = sum(sum(d["waiting"].values()) * d["cycle_hours"] for d in waiting_rows) / total_hours if total_hours else None
    waiting_metric["by_reason"] = {k: sum(d["waiting"][k] * d["cycle_hours"] for d in waiting_rows) / total_hours if total_hours else None for k in sorted(WAIT)}
    metrics = {"attention_per_delivery": metric("attention"), "cycle_hours": cycle,
               "waiting_share": waiting_metric, "first_pass_rate": metric("first_pass"),
               "multica_cost_per_delivery": metric("multica_cost"),
               "quota_consumption": {"value": None, "status": "unknown", "coverage": 0 if n else None, "known": 0, "candidates": n,
                                     "reason": "window snapshots are not attributable to deliveries; never sum with multica_cost"}}
    workspace_flow = {}
    for ws in snapshot.get("workspace_ids", sorted({d["workspace_id"] for d in deliveries})):
        rows = [d for d in deliveries if d["workspace_id"] == ws]
        ws_errors = [e for e in snapshot.get("errors", []) if e.get("workspace_id") == ws]
        ws_inventory_ok = fresh and not any(e.get("resource") == "issues" for e in ws_errors)
        workspace_flow[ws] = {"source_status": "partial" if ws_errors else "complete" if fresh else "unknown",
                              "wip": sum(d["status"] in {"in_progress", "blocked", "in_review"} for d in rows) if ws_inventory_ok else None,
                              "done_candidates": sum(d["candidate"] for d in rows) if ws_inventory_ok else None,
                              "evidenced_deliveries": sum(d["candidate"] and bool(d["completed_at"]) for d in rows) if ws_inventory_ok else None}
    daily = []
    for offset in range(days):
        left = start + timedelta(days=offset)
        right = left + timedelta(days=1)
        complete_count = sum(left <= ts(d["completed_at"]) < right for d in valid)
        daily.append({"since": iso(left), "until": iso(right), "deliveries": complete_count if source_complete and n == len(valid) else None,
                      "observed_deliveries": complete_count,
                      "observed_done_signals": len({(e.get("workspace_id"), e.get("issue_id")) for e in events
                          if e.get("event_type") == "status" and e.get("status") == "done"
                          and (e.get("workspace_id"), e.get("issue_id")) in parent
                          and parent[(e.get("workspace_id"), e.get("issue_id"))] == (e.get("workspace_id"), e.get("issue_id"))
                          and ts(e.get("at")) and left <= ts(e["at"]) < right})})
    recent = [d for d in valid if now - timedelta(hours=24) <= ts(d["completed_at"]) < now]
    throughput = len(recent) if source_complete and n == len(valid) else None
    inventory_ok = fresh and snapshot.get("inventory_complete", snapshot.get("complete", False))
    wip = sum(d["status"] in {"in_progress", "blocked", "in_review"} for d in deliveries) if inventory_ok else None
    current_waiting = dict.fromkeys(sorted(WAIT), 0) if inventory_ok else dict.fromkeys(sorted(WAIT))
    if inventory_ok:
        for d in deliveries:
            if d["status"] not in {"blocked", "in_review"}:
                continue
            key = (d["workspace_id"], d["issue_id"])
            reason = None
            for e in sorted((e for e in grouped[key] if (e.get("issue_id") or e.get("parent_issue_id")) == key[1] and ts(e.get("at")) and ts(e["at"]) < now), key=lambda e: ts(e["at"])):
                if e.get("event_type") in {"status", "wait_close"}:
                    reason = None
                elif e.get("event_type") == "wait_open":
                    reason = e.get("wait_reason")
            bucket = reason if reason in WAIT else ("waiting_agent" if d["status"] == "in_review" else "waiting_unclassified")
            current_waiting[bucket] += 1
    reworks = metric("rework_rounds")
    suggestions = []
    if wip is not None and wip_floor is not None and wip < wip_floor:
        suggestions.append("review_backlog_and_roadmap")
    for reason, action in (("waiting_user", "consolidate_numbered_approvals"),
                           ("waiting_quota", "check_codexbar_then_review_capacity"),
                           ("waiting_agent", "review_agent_capacity")):
        if waiting_metric["by_reason"][reason] or current_waiting[reason]:
            suggestions.append(action)
    if rework_ceiling is not None and reworks["value"] is not None and reworks["value"] > rework_ceiling:
        suggestions.append("clarify_acceptance_criteria")
    cancelled_rows = [d for d in deliveries if d["status"] in {"cancelled", "canceled"}]
    cancelled_cost = measure([d["multica_cost"] for d in cancelled_rows if d["multica_cost"] is not None], len(cancelled_rows), source_complete=source_complete)
    if cancelled_cost["known"]:
        cancelled_cost["value"] = sum(d["multica_cost"] for d in cancelled_rows if d["multica_cost"] is not None)
    if not fresh:
        for metric in metrics.values():
            metric["observed_value"] = metric["value"]
            metric["value"], metric["status"] = None, "unknown"
        cycle["p90"] = None
        waiting_metric["by_reason"] = dict.fromkeys(sorted(WAIT))
    return {"schema_version": 2, "evidence": evidence, "since": iso(start), "until": iso(now), "collected_at": snapshot.get("collected_at"),
            "source_status": "complete" if source_complete else "unknown" if not fresh else "partial",
            "source_errors": snapshot.get("errors", []), "metrics": metrics, "daily": daily,
            "flow": {"deliveries_24h": throughput, "observed_deliveries_24h": len(recent),
                     "wip": wip, "threshold": threshold,
                     "triggered": throughput < threshold if throughput is not None and threshold is not None else None,
                     "rework": reworks, "waiting": waiting_metric, "current_waiting": current_waiting, "suggested_actions": suggestions},
            "coverage": {"done_candidates": n, "evidenced_deliveries": len(valid),
                         "completion_evidence": len(valid) / n if n else None,
                         "parent_unknown": sum(v is None for v in parent.values()),
                         "event_conflicts": event_conflicts, "run_conflicts": run_conflicts,
                         "unattributed_events": unattributed_events, "unattributed_runs": unattributed_runs},
            "cost_of_cancelled": cancelled_cost,
            "workspace_flow": workspace_flow,
            "by_agent": {agent: {"deliveries": len(rows),
                         "attention": measure([d["attention"] for d in rows if d["attention"] is not None], len(rows)),
                         "cycle_hours": measure([d["cycle_hours"] for d in rows if d["cycle_hours"] is not None], len(rows), mode="median")}
                         for agent in sorted({d["agent_id"] or "unknown" for d in valid})
                         for rows in [[d for d in valid if (d["agent_id"] or "unknown") == agent]]},
            "by_workspace": dict(Counter(d["workspace_id"] for d in valid)),
            "deliveries": deliveries}


def build_report(snapshot, ledger=(), *, now=None, days=28, threshold=None,
                 wip_floor=None, rework_ceiling=None, max_age_hours=24):
    """Keep historical proxy and formal ledger populations independent, never sum them."""
    now = now or datetime.now(UTC)
    options = dict(now=now, days=days, threshold=threshold, wip_floor=wip_floor,
                   rework_ceiling=rework_ceiling, max_age_hours=max_age_hours)
    ledger = list(ledger)
    report = _build_report(snapshot, ledger, **options)
    proxy = _build_report(snapshot, ledger, evidence="proxy", **options)
    report["proxy"] = proxy
    report["ledger_recording_since"] = "2026-10-24T00:00:00Z"
    report["proxy_exclusions"] = dict(Counter(
        i.get("proxy_exclusion", "unknown_kind") for i in snapshot.get("issues", [])
        if not i.get("parent_issue_id") and i.get("proxy_exclusion", "unknown_kind")))
    def example(item):
        counts = item["coverage"]
        complete = (item["source_status"] == "complete" and
                    counts["done_candidates"] == counts["evidenced_deliveries"] and
                    not counts["parent_unknown"])
        mean = counts["evidenced_deliveries"] / days
        item["flow"]["example_70_percent"] = {
            "daily_mean": mean if complete else None,
            "threshold": mean * .7 if complete else None,
            "deliveries_24h": item["flow"]["deliveries_24h"],
            "triggered": item["flow"]["deliveries_24h"] < mean * .7 if complete else None,
            "example_only": True}
    example(report)
    example(proxy)
    for ws, flow in report["workspace_flow"].items():
        local = dict(snapshot)
        local["workspace_ids"] = [ws]
        for field in ("issues", "runs", "events", "errors"):
            local[field] = [r for r in snapshot.get(field, []) if r.get("workspace_id") == ws]
        local["complete"] = not local["errors"] and flow["source_status"] == "complete"
        local["inventory_complete"] = not any(e["resource"] == "issues" for e in local["errors"])
        local_ledger = [e for e in ledger if e.get("workspace_id") == ws]
        flow["ledger"] = _build_report(local, local_ledger, **options)
        flow["proxy"] = _build_report(local, local_ledger, evidence="proxy", **options)
        example(flow["ledger"])
        example(flow["proxy"])
    return report


def reconcile(backfill, realtime):
    """Compare normalized event identity, timestamp and classification without overwrites."""
    def index(rows):
        return {(e.get("workspace_id"), e.get("event_id")): e for e in rows}
    left_rows, left_conflicts = dedupe(backfill, lambda e: (e.get("workspace_id"), e.get("event_id")))
    right_rows, right_conflicts = dedupe(realtime, lambda e: (e.get("workspace_id"), e.get("event_id")))
    left, right = index(left_rows), index(right_rows)
    common = left.keys() & right.keys()
    differences = []
    matched = 0
    for k in sorted(common):
        a, b = left[k], right[k]
        fields = [f for f in ("issue_id", "event_type", "wait_reason", "delivery_evidence", "confidence") if a.get(f) != b.get(f)]
        ta, tb = ts(a.get("at")), ts(b.get("at"))
        delta = (tb - ta).total_seconds() if ta and tb else None
        if fields or delta != 0:
            differences.append({"workspace_id": k[0], "event_id": k[1], "time_delta_seconds": delta, "fields": fields, "status": "unknown"})
        else:
            matched += 1
    return {"matched": matched, "conflicting_ids": left_conflicts + right_conflicts, "backfill_only": len(left.keys() - right.keys()),
            "realtime_only": len(right.keys() - left.keys()), "differences": differences,
            "status": "unknown" if differences or left.keys() != right.keys() or left_conflicts or right_conflicts else "complete"}
