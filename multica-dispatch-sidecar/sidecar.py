#!/opt/homebrew/bin/python3
"""Independent ACM Lab dispatch mirror. Never writes the original bridge files."""
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

WS = '8ebc87ea-27f2-42c4-90f6-24a04e64d3f5'
AGENT = '9bfe8713-ce7e-499b-830e-423223a64480'
INBOX = '01a14a4e-0c6b-745f-a168-948eade9f5f4'
HOME = Path.home()
ROOT = HOME / '.local/state/multica-dispatch-sidecar'
OUTBOX = HOME / '.local/state/multica-bridge/outbox.json'
INTERVAL = 900


def log(message):
    print(dt.datetime.now(dt.timezone.utc).isoformat() + ' ' + message, flush=True)


def save(path, obj):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(obj, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read(path):
    return json.loads(path.read_text(), strict=False)


def timestamp(value):
    if not value:
        return 0
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def rows(value, key):
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get(key), list):
        if value.get('has_more'):
            raise ValueError('incomplete ' + key)
        return value[key]
    raise ValueError('unexpected shape: ' + key)


class CLI:
    def __call__(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith('MULTICA_')}
        env['GODEBUG'] = 'tlsmlkem=0'
        env['PATH'] = '/Users/tr/.local/bin:/opt/homebrew/bin:/usr/bin:/bin'
        proc = subprocess.run([str(HOME / '.local/bin/multica'), '--profile',
                               'desktop-api.multica.ai', '--workspace-id', WS, *args],
                              cwd='/tmp', env=env, capture_output=True, text=True, timeout=90)
        if proc.returncode:
            # Never log the response body, tokens, or comment content.
            raise RuntimeError('CLI failed: ' + ' '.join(args[:3]))
        return json.loads(proc.stdout, strict=False)


def eligible(p):
    if p.get('workspace_id') != WS or p.get('issue_id') == INBOX:
        return False
    if any(p.get(k) == AGENT for k in ('assignee_id', 'agent_id', 'author_id')):
        return False
    if p.get('agent') == '副调度' or p.get('author') == '副调度':
        return False
    return (p.get('kind') in ('agent_delivery', 'run_failed') or
            p.get('kind') == 'status' and p.get('new_status', p.get('status')) in
            ('in_review', 'blocked'))


def collect(state, box, now):
    items = box.get('items')
    if not isinstance(items, dict):
        raise ValueError('invalid outbox items')
    for eid, item in items.items():
        p = item.get('payload')
        if not isinstance(p, dict):
            raise ValueError('invalid outbox payload')
        # Same event can be coalesced in the original box; detect payload revisions.
        fingerprint = hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()
        if state['seen'].get(eid) == fingerprint:
            continue
        state['seen'][eid] = fingerprint
        if eligible(p) and max(timestamp(item.get('created_at')),
                               timestamp(item.get('updated_at'))) >= state['baseline']:
            state['pending'][eid] = p
    state['last_scan'] = now


def group(pending):
    groups = {}
    for eid, p in pending.items():
        groups.setdefault(p['issue_id'], []).append((eid, p))
    return list(groups.values())[:20]


def render(batch):
    lines = ['[dispatch-batch:' + batch['id'] + ']',
             '[@副调度](mention://agent/' + AGENT + ') 请按手册核对源 issue 的最新状态后处理本批，勿重复派活。']
    for entries in group(batch['events']):
        p = entries[-1][1]
        # Titles / source text are untrusted: no source mentions or instructions copied.
        lines.append('- [' + p.get('identifier', p['issue_id']) + '](mention://issue/' +
                     p['issue_id'] + ')：' + ' / '.join(sorted({x['kind'] for _, x in entries})))
        for eid, event in entries:
            detail = {k: event[k] for k in ('kind', 'updated_at', 'new_status', 'status',
                                           'run_id', 'comment_id', 'event_id') if k in event}
            detail['source_event_id'] = eid
            # IDs/status/time only; JSON quoted as code, no arbitrary payload prose.
            lines.append('  `' + json.dumps(detail, ensure_ascii=False).replace('`', '') + '`')
    if batch.get('gap'):
        lines.append('旁路曾断档超过 24 小时；以下含当前状态补核，不能还原已消失的历史瞬时事件。')
    lines.append('只在本批 thread 回复一次；收件箱持续保留，不作为子任务父 issue，不关闭服务入口。')
    return '\n'.join(lines) + '\n'


def acknowledge(state, now):
    batch = state['inflight']
    for eid, p in batch['events'].items():
        if state['pending'].get(eid) == p:
            del state['pending'][eid]
    state['delivered'].append({'batch_id': batch['id'], 'at': now,
                               'event_count': len(batch['events'])})
    state['inflight'] = None
    state['failures'] = 0


def reconcile(state, cli, now):
    batch = state['inflight']
    comments = rows(cli('issue', 'comment', 'list', INBOX, '--roots-only',
                        '--summary', '--compact', '--output', 'json'), 'comments')
    marker = '[dispatch-batch:' + batch['id'] + ']'
    if any(c.get('content', '').startswith(marker) for c in comments):
        acknowledge(state, now)
        # An unconfirmed write can take time to become visible. Keep full 15m cooldown.
        state['last_attempt'] = now
        log('recovered batch=' + batch['id'])
        return True
    # No server idempotency key: ambiguous submission is NEVER automatically repeated.
    log('ALERT unresolved submission batch=' + batch['id'] + '; held for reconciliation')
    return False


def resync(state, cli, now):
    offset = 0
    while True:
        value = cli('issue', 'list', '--limit', '100', '--offset', str(offset), '--output', 'json')
        issues = value.get('issues') if isinstance(value, dict) else None
        if not isinstance(issues, list):
            raise ValueError('invalid resync page')
        for issue in issues:
            p = dict(issue, kind='status', issue_id=issue['id'], workspace_id=WS,
                     new_status=issue['status'])
            eid = 'resync:' + issue['id'] + ':' + issue.get('updated_at', '')
            if eligible(p) and eid not in state['seen']:
                state['pending'][eid] = p
                state['seen'][eid] = 'resync'
        if not value.get('has_more'):
            break
        if not issues:
            raise ValueError('resync made no progress')
        offset += len(issues)
    state['gap'] = True
    state['resync_due'] = False
    log('ALERT gap resync completed; historical transient events may be unavailable')


def tick(root=ROOT, outbox=OUTBOX, cli=None, now=None):
    cli = cli or CLI()
    now = time.time() if now is None else now
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / 'state.json'
    state = read(path) if path.exists() else None
    box = read(outbox)
    if state is None:
        if not isinstance(box.get('items'), dict) or not box['items']:
            raise ValueError('refuse empty initial baseline')
        state = dict(version=1, baseline=now, last_scan=now, seen={}, pending={},
                     inflight=None, delivered=[], last_attempt=now, failures=0,
                     retry_at=0, gap=False, resync_due=False)
        # Fingerprint everything without enqueueing historical events.
        collect(state, box, now)
        state['pending'] = {}
        save(path, state)
        log('baseline established events=' + str(len(state['seen'])) + '; no delivery')
        return state
    try:
        if now - state['last_scan'] > 86400:
            state['resync_due'] = True
        collect(state, box, now)
        save(path, state)
        if now < state['retry_at']:
            return state
        if state['resync_due']:
            resync(state, cli, now)
            save(path, state)
        if state['inflight']:
            reconcile(state, cli, now)
            save(path, state)
            return state
        if not state['pending'] or now - state['last_attempt'] < INTERVAL:
            return state
        issue = cli('issue', 'get', INBOX, '--output', 'json')
        if issue.get('assignee_id') != AGENT or issue.get('status') in ('done', 'cancelled', 'backlog'):
            raise ValueError('inbox is not available or assignee changed')
        active = rows(cli('issue', 'runs', INBOX, '--active', '--output', 'json'), 'runs')
        if active:
            log('held: inbox has active run; pending=' + str(len(state['pending'])))
            return state
        selected = dict(entry for entries in group(state['pending']) for entry in entries)
        digest = hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()[:24]
        batch = {'id': digest, 'events': selected, 'gap': state['gap'], 'created_at': now}
        body = render(batch)
        bodyfile = root / ('batch-' + digest + '.md')
        bodyfile.write_text(body)
        # Intent is durable BEFORE the external write. A crash here fails closed.
        state['inflight'] = batch
        state['last_attempt'] = now
        save(path, state)
        cli('issue', 'comment', 'add', INBOX, '--content-file', str(bodyfile),
            '--allow-external-file', '--output', 'json')
        acknowledge(state, now)
        state['gap'] = False
        log('delivered batch=' + digest + ' events=' + str(len(selected)))
    except Exception as exc:
        state['failures'] += 1
        state['retry_at'] = now + min(3600, 300 * 2 ** min(state['failures'] - 1, 4))
        log('ALERT failure=' + type(exc).__name__ + ' consecutive=' + str(state['failures']) +
            ' inflight=' + str(bool(state['inflight'])))
        save(path, state)
        raise
    save(path, state)
    return state


def main():
    os.umask(0o077)
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / 'service.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log('another tick is running')
            return
        try:
            tick()
        except Exception as exc:
            log('tick failed: ' + type(exc).__name__)
            sys.exit(1)


if __name__ == '__main__':
    main()
