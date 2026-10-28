"""Bounded profile-only inventory + recent timeline reads; no comments/runs/writes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import time

from .cli_runner import CliRunner, CliError
from .redact import redact
from .metrics import iso, ts
from .metrics_history import _complete, _rows, proxy_exclusion

FIELDS = 'id,workspace_id,parent_issue_id,status,created_at,updated_at,last_activity_at,assignee_id,title'


def collect_recent(profile, binary, now, *, budget_seconds=110, workers=8, run=None):
    deadline = time.monotonic() + budget_seconds

    def diagnostic(args, ws, exc, started, attempt=1):
        return dict(workspace_id=ws, command=[redact(a, limit=None) for a in ([binary, '--profile', profile] +
                    (['--workspace-id', ws] if ws else []) + args + ['--output', 'json'])],
                    offset=int(args[args.index('--offset') + 1]) if '--offset' in args else None,
                    elapsed_seconds=round(time.monotonic() - started, 3),
                    error=redact(getattr(exc, "detail", str(exc)), limit=None), attempt=attempt, recovered=False)

    def read(args, ws=None):
        failures = []
        for attempt in range(3):
            started = time.monotonic()
            remaining = deadline - started
            try:
                if remaining <= 0:
                    raise CliError('flow budget exhausted')
                result = (run(args, workspace_id=ws) if run else
                          CliRunner(profile=profile, binary=binary, timeout_s=min(20, remaining),
                                    retries=0, max_parallel=1).run(args, workspace_id=ws))
                for failure in failures:
                    failure['recovered'] = True
                return result
            except CliError as exc:
                detail = diagnostic(args, ws, exc, started, attempt + 1)
                detail.update(resource='issues' if args[:2] == ['issue', 'list'] else
                              'timeline' if args[:2] == ['issue', 'timeline'] else 'workspaces',
                              reason='cli_error', transient=exc.transient)
                snapshot['errors'].append(detail)
                failures.append(detail)
                delay = 1.5 * (2 ** attempt)
                if (args[:2] not in (['issue', 'list'], ['workspace', 'list']) or
                        not exc.transient or attempt == 2 or deadline - time.monotonic() <= delay):
                    raise
                time.sleep(delay)

    snapshot = dict(profile=profile, collected_at=iso(now), issues=[], runs=[], events=[],
                    workspace_ids=[], errors=[], history_complete={}, complete=False, inventory_complete=False)
    workspace_started = time.monotonic()
    try:
        result = read(['workspace', 'list'])
        if not _complete(result):
            raise CliError('incomplete workspace list')
        spaces = _rows(result)
        if any(not w.get('id') for w in spaces) or len({w['id'] for w in spaces}) != len(spaces):
            raise CliError('invalid workspace inventory')
        snapshot['workspace_ids'] = [w['id'] for w in spaces]
    except CliError as exc:
        error = diagnostic(['workspace', 'list'], None, exc, workspace_started)
        error.update(resource='workspaces', reason='unavailable_or_deadline')
        snapshot['errors'].append(error)
        return snapshot

    def inventory(ws):
        found, offset = {}, 0
        started = time.monotonic()
        try:
            while True:
                started = time.monotonic()
                result = read(['issue', 'list', '--limit', '100', '--offset', str(offset), '--fields', FIELDS], ws)
                data = result.data
                if not isinstance(data, dict) or not isinstance(data.get('issues'), list) or not isinstance(data.get('has_more'), bool):
                    raise CliError('malformed page')
                rows = data['issues']
                for row in rows:
                    if not isinstance(row, dict) or not row.get('id') or row.get('workspace_id') not in (None, ws):
                        raise CliError('inventory scope mismatch')
                    item = {k: row.get(k) for k in FIELDS.split(',') if k != 'title'}
                    item['workspace_id'] = ws
                    item['proxy_exclusion'] = proxy_exclusion(row.get('title'))
                    found[item['id']] = item
                if not data['has_more']:
                    if data.get('total') is not None and len(found) < int(data['total']):
                        raise CliError('incomplete inventory')
                    return list(found.values()), None
                if not rows:
                    raise CliError('empty continuing page')
                offset += len(rows)
        except (CliError, ValueError, TypeError) as exc:
            error = diagnostic(['issue', 'list', '--limit', '100', '--offset', str(offset), '--fields', FIELDS],
                               ws, exc, started)
            error.update(resource='issues', reason='incomplete_or_deadline')
            return list(found.values()), error

    inventory_errors = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rows, error in pool.map(inventory, snapshot['workspace_ids']):
            snapshot['issues'].extend(rows)
            if error:
                snapshot['errors'].append(error)
                inventory_errors.append(error)
    snapshot['inventory_complete'] = bool(spaces) and not inventory_errors
    cutoff = now - timedelta(hours=48)
    changed = []
    for issue in snapshot['issues']:
        dates = [ts(issue.get(k)) for k in ('updated_at', 'last_activity_at')]
        dates = [d for d in dates if d]
        # Missing change metadata cannot justify skipping a timeline.
        if not dates or max(dates) >= cutoff:
            changed.append(issue)
        elif issue['status'] == 'done':
            # updated_at is only a conservative exclusion bound, never an end time.
            issue['outside_recent_change_window'] = True

    changed.sort(key=lambda issue: (issue['status'] != 'done', issue['workspace_id'], issue['id']))

    def timeline(issue):
        ws, iid = issue['workspace_id'], issue['id']
        events = []
        started = time.monotonic()
        try:
            result = read(['issue', 'timeline', iid, '--activity-only'], ws)
            rows = _rows(result)
            for row in rows:
                if row.get('workspace_id') not in (None, ws) or row.get('issue_id') not in (None, iid):
                    raise CliError('timeline scope mismatch')
                if row.get('action') == 'status_changed' and row.get('id') and ts(row.get('created_at')):
                    detail = row.get('details') or {}
                    if not isinstance(detail, dict):
                        raise CliError('invalid timeline details')
                    events.append(dict(workspace_id=ws, issue_id=iid, event_id=row['id'],
                                       event_type='status', at=row['created_at'], source='multica_timeline',
                                       confidence='complete', status=detail.get('to'), previous_status=detail.get('from')))
            complete = _complete(result)
            error = None
            if not complete:
                error = diagnostic(['issue', 'timeline', iid, '--activity-only'], ws,
                                   CliError('truncated timeline'), started)
                error.update(issue_id=iid, resource='timeline', reason='truncated')
            return f'{ws}/{iid}', events, complete, error
        except CliError as exc:
            error = diagnostic(['issue', 'timeline', iid, '--activity-only'], ws, exc, started)
            error.update(issue_id=iid, resource='timeline', reason='unavailable_or_deadline')
            return f'{ws}/{iid}', [], False, error

    def apply_timeline(result):
        key, events, complete, error = result
        if complete:
            ws, iid = key.split('/', 1)
            for previous in snapshot['errors']:
                argv = previous.get('command', [])
                if (previous.get('workspace_id') == ws and 'timeline' in argv and iid in argv):
                    previous['recovered'] = True
        snapshot['history_complete'][key] = complete
        # A retry replaces this issue's evidence; never count events twice.
        ws, iid = key.split('/', 1)
        snapshot['events'][:] = [e for e in snapshot['events']
                                 if (e['workspace_id'], e['issue_id']) != (ws, iid)]
        snapshot['events'].extend(events)
        if error:
            snapshot['errors'].append(error)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(timeline, changed):
            apply_timeline(result)
    retry = [i for i in changed if not snapshot['history_complete'][f"{i['workspace_id']}/{i['id']}"]]
    if deadline > time.monotonic():
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(timeline, retry):
                apply_timeline(result)
    snapshot['complete'] = snapshot['inventory_complete'] and all(snapshot['history_complete'].values())
    snapshot['collection'] = dict(mode='profile_recent_timelines', workspaces=len(spaces),
                                  issues=len(snapshot['issues']), changed_48h=len(changed),
                                  timeline_complete=sum(snapshot['history_complete'].values()),
                                  degraded=not snapshot['inventory_complete'],
                                  history_partial=not all(snapshot['history_complete'].values()),
                                  budget_seconds=budget_seconds)
    return snapshot
