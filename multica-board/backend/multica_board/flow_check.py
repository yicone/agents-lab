"""Bounded incremental flow inspection; read-only profile calls, no Board writes."""
from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .metrics import _build_report, _covered, dedupe, roots, iso, ts
from .metrics_history import proxy_exclusion

DEFAULT_CONFIG = Path(__file__).with_name("flow_config.json")


def read_cache(path, profile, now, max_age_hours, *, since=None, until=None):
    """A consistent read transaction; observation times are not timeline evidence."""
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    deadline = time.monotonic() + 45
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        workspaces = [r[0] for r in conn.execute("SELECT id FROM workspaces WHERE server=?", (profile,))]
        sync = {r['workspace_id']: dict(r) for r in conn.execute(
            "SELECT * FROM sync_status WHERE server=? AND resource='issues'", (profile,))}
        errors = []
        stamps = []
        for ws in workspaces:
            s = sync.get(ws, {})
            at = ts(s.get('last_success_at'))
            if (not at or not s.get('complete') or s.get('last_error') or
                    not timedelta(0) <= now - at <= timedelta(hours=max_age_hours)):
                errors.append({'workspace_id': ws, 'resource': 'issues', 'reason': 'missing_stale_or_partial'})
            if at:
                stamps.append(at)
        issues = []
        for row in conn.execute("SELECT workspace_id,id,parent_issue_id,status,created_at,assignee_id,title FROM issues WHERE server=?", (profile,)):
            item = dict(row)
            item['proxy_exclusion'] = proxy_exclusion(item.pop('title'))
            issues.append(item)
        events = []
        for row in conn.execute("SELECT seq,workspace_id,resource_id,at,payload_json FROM changes WHERE server=? AND kind='issue_status' AND at>=? AND at<? ORDER BY seq", (profile, iso(since or now - timedelta(days=2)), iso(until or now))):
            payload = json.loads(row['payload_json'])
            events.append(dict(workspace_id=row['workspace_id'], issue_id=row['resource_id'],
                               event_id=f"board-status:{row['seq']}", event_type='status', at=row['at'],
                               source='board_observation', confidence='partial',
                               status=payload.get('status'), previous_status=payload.get('previous_status')))
        return dict(schema_version=1, profile=profile, workspace_ids=workspaces,
                    collected_at=iso(min(stamps)) if stamps else None,
                    complete=False, inventory_complete=bool(workspaces) and not errors,
                    issues=issues, events=events, runs=[], history_complete={}, errors=errors,
                    limitations=['Board observations have no complete timeline or semantic ledger coverage'])
    finally:
        conn.close()


def flow_report(snapshot, ledger=(), *, now=None, config=None):
    now = now or datetime.now(timezone.utc)
    ledger = list(ledger)
    config = config or json.loads(DEFAULT_CONFIG.read_text())
    threshold, max_age = config['threshold'], config['max_age_hours']
    if (isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or
            not math.isfinite(threshold) or threshold < 0):
        raise ValueError('threshold must be finite and non-negative')
    # Reuse the established parent/completion/exclusion/conflict rules, with no
    # four-week or per-workspace expansion on this frequent path.
    reports = {kind: _build_report(snapshot, ledger, now=now, days=1, threshold=threshold,
                                  evidence=kind, max_age_hours=max_age)
               for kind in ('ledger', 'proxy')}
    index, parents = roots(snapshot.get('issues', []))
    fresh = ts(snapshot.get('collected_at'))
    inventory_ok = bool(fresh and timedelta(0) <= now - fresh <= timedelta(hours=max_age)
                        and snapshot.get('inventory_complete', snapshot.get('complete'))
                        and all(root is not None for root in parents.values()))
    if not inventory_ok:
        for report in reports.values():
            report['flow']['deliveries_24h'] = None
            report['flow']['triggered'] = None
    top = [item for key, item in index.items() if parents.get(key) == key]
    hints = {name: sum(i.get('status') == status for i in top) if inventory_ok else None
             for name, status in [('in_progress_issues', 'in_progress'), ('queued_issues', 'todo'),
                                  ('backlog_issues', 'backlog')]}
    # A status/assignee alone cannot prove approval, dispatch readiness or no rework.
    hints.update(waiting_user_issues=None, waiting_approval_issues=None, dispatchable_backlog_issues=None, reworked_deliveries_24h=None)
    combined, conflicts = dedupe(list(snapshot.get('events', [])) + list(ledger),
                                 lambda e: (e.get('workspace_id'), e.get('event_id')))
    grouped = {}
    for e in combined:
        root = parents.get((e.get('workspace_id'), e.get('issue_id') or e.get('parent_issue_id')))
        grouped.setdefault(root, []).append(e)
    if inventory_ok and not conflicts:
        waiting_known = True
        approval_count = 0
        for key, item in index.items():
            if parents.get(key) != key or item.get('status') not in {'blocked', 'in_review'}:
                continue
            reason = None
            for e in sorted((e for e in grouped.get(key, []) if ts(e.get('at')) and ts(e['at']) < now
                             and (e.get('issue_id') or e.get('parent_issue_id')) == key[1]), key=lambda e: ts(e['at'])):
                if e.get('event_type') in {'status', 'wait_close'}:
                    reason = None
                elif e.get('event_type') == 'wait_open' and e.get('confidence') == 'complete':
                    reason = e.get('wait_reason')
            if reason not in {'waiting_user', 'waiting_agent', 'waiting_quota'}:
                waiting_known = False
            approval_count += reason == 'waiting_user'
        hints['waiting_user_issues'] = approval_count if waiting_known else None
        # waiting_user also covers questions/manual action; it is not an approval card.
        hints['waiting_approval_issues'] = 0 if waiting_known and approval_count == 0 else None
        start = now - timedelta(hours=24)
        if not any(e.get('event_type') == 'rework' and e.get('confidence') != 'complete' for e in combined) and all(_covered([e for e in grouped.get(key, [])
                         if (e.get('issue_id') or e.get('parent_issue_id')) == key[1]],
                        'rework', start, now)
               for key in index if parents.get(key) == key):
            hints['reworked_deliveries_24h'] = sum(any(e.get('event_type') == 'rework'
                and e.get('confidence') == 'complete' and ts(e.get('at'))
                and start <= ts(e['at']) < now for e in grouped.get(key, []))
                for key in index if parents.get(key) == key)
    columns = {}
    start = now - timedelta(hours=24)
    for kind, r in reports.items():
        confirmed = 0
        uncertain = 0
        for row in r['deliveries']:
            if row['status'] != 'done':
                continue
            key = (row['workspace_id'], row['issue_id'])
            original = index[key]
            end = ts(row.get('completed_at'))
            if end and start <= end < now:
                confirmed += 1
            elif not end and not original.get('outside_recent_change_window'):
                # A last close outside the 24h window excludes this candidate,
                # even if the formal ledger itself has not been connected.
                statuses = [e for e in grouped.get(key, []) if e.get('event_type') == 'status'
                            and e.get('issue_id') == key[1] and ts(e.get('at')) and ts(e['at']) < now]
                latest = max(statuses, key=lambda e: ts(e['at']), default=None)
                old_close = (snapshot.get('history_complete', {}).get('/'.join(key)) and latest
                             and latest.get('status') == 'done' and ts(latest['at']) < start)
                if not old_close:
                    uncertain += 1
        if kind == 'proxy':
            uncertain += sum(item.get('status') == 'done' and
                             item.get('proxy_exclusion', 'unknown_kind') == 'unknown_kind' and
                             not item.get('outside_recent_change_window')
                             for key, item in index.items() if parents.get(key) == key)
        # Missing inventory/parents can hide arbitrarily many deliveries. The
        # observed lower bound still proves a non-trigger when already >= threshold.
        upper = confirmed + uncertain if inventory_ok else None
        below = False if confirmed >= threshold else True if upper is not None and upper < threshold else None
        columns[kind] = dict(confirmed_min=confirmed if fresh and timedelta(0) <= now-fresh <= timedelta(hours=max_age) else 0,
                             candidates=upper, unknown_count=uncertain if upper is not None else None,
                             below_threshold=below, deliveries_24h=confirmed if upper == confirmed else None)
        if not fresh or not timedelta(0) <= now-fresh <= timedelta(hours=max_age):
            columns[kind].update(candidates=None, unknown_count=None, below_threshold=None, deliveries_24h=None)
    # Explicitly recorded rework is a lower bound, not an assertion of complete coverage.
    reworked = {key for key, events in grouped.items() if key is not None and any(
        e.get('event_type') == 'rework' and e.get('confidence') == 'complete' and ts(e.get('at'))
        and start <= ts(e['at']) < now for e in events)}
    reopened = {parents.get((e.get('workspace_id'), e.get('issue_id'))) for e in combined
                if e.get('event_type') == 'status' and e.get('previous_status') == 'done'
                and e.get('status') not in {'done', 'cancelled', 'canceled'} and ts(e.get('at'))
                and start <= ts(e['at']) < now}
    reopened.discard(None)
    if hints['reworked_deliveries_24h'] is None:
        hints['reworked_24h_confirmed_min'] = len(reworked | reopened)
    # Missing semantic classifications get useful bounds and an explicit reason,
    # rather than silently claiming that every backlog/todo item is dispatchable.
    active = sum(i.get('status') not in {'done', 'cancelled', 'canceled'} for i in top) if inventory_ok else None
    def hint_bounds(value, lower, candidates, reason):
        if value is not None:
            lower = candidates = value
        return dict(value=value, confirmed_min=lower, candidates=candidates,
                    unknown_count=candidates-lower if candidates is not None else None,
                    reason=reason if value is None else None)
    hints['waiting_approval_issues'] = 0 if active == 0 else None
    hints['waiting_approval'] = hint_bounds(hints['waiting_approval_issues'], 0, active,
        'No structured pending approval-card events; waiting_user does not distinguish approvals from questions/manual actions.')
    backlog = hints['backlog_issues']
    hints['dispatchable_backlog'] = hint_bounds(0 if backlog == 0 else None, 0, backlog,
        'No complete authorization, dependency and current capacity evidence. Backlog inventory is only a candidate upper bound.')
    rework_min = len(reworked | reopened) if inventory_ok or fresh else 0
    if hints['reworked_deliveries_24h'] is not None:
        hints['reworked_deliveries_24h'] = len(reworked | reopened)
    hints['reworked_24h'] = hint_bounds(hints['reworked_deliveries_24h'],
        hints['reworked_deliveries_24h'] if hints['reworked_deliveries_24h'] is not None else rework_min,
        len(top) if inventory_ok else None,
        'Confirmed includes explicit classified rework or done reopens; semantic rework coverage is incomplete.')
    hints['queued_definition'] = 'parent issues currently todo; not active runtime queue, not proof of dispatch readiness'
    hints['reasons'] = dict(
        waiting_approval='No structured pending approval-card classification; waiting_user also includes questions/manual action.' if hints['waiting_approval_issues'] is None else None,
        dispatchable_backlog='No complete authorization/dependency/capacity evidence; backlog count is inventory only.',
        reworked_24h='Count explicit rework + done reopens by parent; total unknown without complete semantic rework coverage.' if hints['reworked_deliveries_24h'] is None else None)
    return dict(since=iso(start), until=iso(now), collected_at=snapshot.get('collected_at'),
                threshold=threshold, threshold_source=config['source'], **columns, hints=hints,
                collection=snapshot.get('collection'), source_errors=snapshot.get('errors', []))



def register(sub, common):
    p = sub.add_parser('flow-check', help='read all workspace inventories and recent timelines within two minutes; no writes')
    common(p)
    p.add_argument('--snapshot', type=Path, help='offline normalized snapshot; default live profile inventory and recent timelines')
    p.add_argument('--ledger', type=Path)
    p.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    p.add_argument('--json', action='store_true', help='JSON is the default; null means unknown')
    p.add_argument('--now')
    p.add_argument('--budget-seconds', type=float, default=110)
    r = sub.add_parser('metrics-realtime', help='export existing Board observations for reconciliation; never changes the Board')
    common(r)
    r.add_argument('--snapshot', type=Path, required=True)
    r.add_argument('--since', help='export observation interval, inclusive; default last 48h')
    r.add_argument('--until', help='export observation interval, exclusive; default now')


def command(args):
    from .metrics_cli import load_ledger
    from .metrics_history import write_private
    now = ts(args.now) if getattr(args, 'now', None) else datetime.now(timezone.utc)
    if now is None:
        raise ValueError('--now requires an ISO timestamp with timezone')
    config = json.loads((getattr(args, 'config', None) or DEFAULT_CONFIG).read_text())
    since = ts(getattr(args, 'since', None))
    until = ts(getattr(args, 'until', None))
    if ((getattr(args, 'since', None) and since is None) or
            (getattr(args, 'until', None) and until is None) or
            (since and until and since >= until)):
        raise ValueError('invalid observation window')
    if args.cmd == 'flow-check' and not args.snapshot:
        from .flow_live import collect_recent
        if not 1 <= args.budget_seconds <= 115:
            raise ValueError('budget seconds must be 1..115')
        snapshot = collect_recent(args.profile, args.multica_bin, now, budget_seconds=args.budget_seconds)
    else:
        try:
            snapshot = (json.loads(args.snapshot.read_text()) if args.cmd == 'flow-check' and args.snapshot
                        else read_cache(args.db, args.profile, now, config['max_age_hours'], since=since, until=until))
        except (OSError, sqlite3.Error):
            snapshot = dict(complete=False, inventory_complete=False, collected_at=None, issues=[], events=[],
                            errors=[dict(resource='cache', reason='unavailable')])
    if args.cmd == 'metrics-realtime':
        write_private(args.snapshot, snapshot)
        print(json.dumps(dict(events=len(snapshot['events']), collected_at=snapshot['collected_at'],
                              inventory_complete=snapshot['inventory_complete'],
                              status='partial' if snapshot.get('collected_at') else 'unknown')))
    else:
        print(json.dumps(flow_report(snapshot, load_ledger(args.ledger), now=now, config=config),
                         ensure_ascii=False, indent=2, allow_nan=False))
    return 0
