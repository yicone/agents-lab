"""Bounded profile-only inventory + recent timeline reads; no comments/runs/writes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import time

from .cli_runner import CliRunner, CliError
from .metrics import iso, ts
from .metrics_history import _complete, _rows, proxy_exclusion

FIELDS = 'id,workspace_id,parent_issue_id,status,created_at,updated_at,last_activity_at,assignee_id,title'


def collect_recent(profile, binary, now, *, budget_seconds=110, workers=8, run=None):
    deadline = time.monotonic() + budget_seconds

    def read(args, ws=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CliError('flow budget exhausted')
        if run:
            return run(args, workspace_id=ws)
        # Each worker owns one child at a time. No retries can extend the deadline.
        return CliRunner(profile=profile, binary=binary, timeout_s=min(12, remaining),
                         retries=0, max_parallel=1).run(args, workspace_id=ws)

    snapshot = dict(profile=profile, collected_at=iso(now), issues=[], runs=[], events=[],
                    workspace_ids=[], errors=[], history_complete={}, complete=False, inventory_complete=False)
    try:
        result = read(['workspace', 'list'])
        if not _complete(result):
            raise CliError('incomplete workspace list')
        spaces = _rows(result)
        if any(not w.get('id') for w in spaces) or len({w['id'] for w in spaces}) != len(spaces):
            raise CliError('invalid workspace inventory')
        snapshot['workspace_ids'] = [w['id'] for w in spaces]
    except CliError:
        snapshot['errors'].append(dict(resource='workspaces', reason='unavailable_or_deadline'))
        return snapshot

    def inventory(ws):
        found, offset = {}, 0
        try:
            while True:
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
        except (CliError, ValueError, TypeError):
            return list(found.values()), dict(workspace_id=ws, resource='issues', reason='incomplete_or_deadline')

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rows, error in pool.map(inventory, snapshot['workspace_ids']):
            snapshot['issues'].extend(rows)
            if error:
                snapshot['errors'].append(error)
    snapshot['inventory_complete'] = bool(spaces) and not snapshot['errors']
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
            error = None if complete else dict(workspace_id=ws, issue_id=iid, resource='timeline', reason='truncated')
            return f'{ws}/{iid}', events, complete, error
        except CliError:
            return f'{ws}/{iid}', [], False, dict(workspace_id=ws, issue_id=iid, resource='timeline', reason='unavailable_or_deadline')

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, events, complete, error in pool.map(timeline, changed):
            snapshot['history_complete'][key] = complete
            snapshot['events'].extend(events)
            if error:
                snapshot['errors'].append(error)
    snapshot['complete'] = snapshot['inventory_complete'] and not snapshot['errors']
    snapshot['collection'] = dict(mode='profile_recent_timelines', workspaces=len(spaces),
                                  issues=len(snapshot['issues']), changed_48h=len(changed),
                                  timeline_complete=sum(snapshot['history_complete'].values()),
                                  budget_seconds=budget_seconds)
    return snapshot
