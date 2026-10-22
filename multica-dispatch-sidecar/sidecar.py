#!/opt/homebrew/bin/python3
"""Independent ACM Lab dispatch mirror. Never writes the original bridge files."""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
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
STALL_ALERT = 21600

REJECTED = re.compile(r'\b(400|401|403|404|409|422)\b|not found|unauthorized|forbidden|'
                      r'permission|invalid|not a recognized|bad request', re.I)


def redact(text):
    for pattern in (r'mat_\w+', r'Bearer\s+\S+',
                    r'(?i)(token|secret|password|authorization)\s*[=:]\s*\S+',
                    r'[\w.+-]+@[\w-]+\.[\w.-]+', r'[A-Za-z0-9_\-]{32,}'):
        text = re.sub(pattern, '<redacted>', text)
    return re.sub(r'\s+', ' ', text).strip()[:200]


class CLIError(RuntimeError):
    def __init__(self, returncode, category, summary):
        super().__init__(summary)
        self.returncode = returncode
        self.category = category
        self.summary = summary


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
        try:
            proc = subprocess.run([str(HOME / '.local/bin/multica'), '--profile',
                                   'desktop-api.multica.ai', '--workspace-id', WS, *args],
                                  cwd='/tmp', env=env, capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired:
            raise CLIError(None, 'unknown', 'timeout after 90s')
        except OSError as exc:
            raise CLIError(None, 'local', redact(str(exc)))
        if proc.returncode:
            # Never log the response body, tokens, or comment content.
            category = 'rejected' if REJECTED.search(proc.stderr) else 'unknown'
            raise CLIError(proc.returncode, category, redact(proc.stderr))
        try:
            return json.loads(proc.stdout, strict=False)
        except ValueError:
            raise CLIError(0, 'unknown', redact(proc.stderr) or 'stdout is not valid JSON')


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


def fingerprint(p):
    return hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()


def normalize(state):
    for key, default in (('audit', []), ('retired', {}), ('ledgers_applied', []),
                         ('last_stall_alert', 0), ('last_error', None)):
        state.setdefault(key, default)


def audit(root, state, record):
    state['audit'].append(record)
    with (root / 'audit.jsonl').open('a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


@contextlib.contextmanager
def service_lock(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'service.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('service.lock is held by the running service; refusing to proceed')
        yield


def backup(path):
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    for n in range(100):
        dst = path.with_name(path.name + '.bak-' + stamp + ('-' + str(n) if n else ''))
        try:
            fd = os.open(str(dst), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, 'w') as f:
            f.write(path.read_text())
        return dst
    raise RuntimeError('could not create backup of ' + str(path))


def collect(state, box, now):
    items = box.get('items')
    if not isinstance(items, dict):
        raise ValueError('invalid outbox items')
    for eid, item in items.items():
        p = item.get('payload')
        if not isinstance(p, dict):
            raise ValueError('invalid outbox payload')
        # Same event can be coalesced in the original box; detect payload revisions.
        fp = fingerprint(p)
        if state['seen'].get(eid) == fp:
            continue
        state['seen'][eid] = fp
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
    since = dt.datetime.fromtimestamp(batch['created_at'] - 3600,
                                      dt.timezone.utc).isoformat()
    comments += rows(cli('issue', 'comment', 'list', INBOX, '--since', since,
                         '--summary', '--compact', '--output', 'json'), 'comments')
    marker = '[dispatch-batch:' + batch['id'] + ']'
    if any(c.get('content', '').lstrip().startswith(marker) for c in comments):
        acknowledge(state, now)
        # An unconfirmed write can take time to become visible. Keep full 15m cooldown.
        state['last_attempt'] = now
        log('recovered batch=' + batch['id'])
        return True
    # No server idempotency key: ambiguous submission is NEVER automatically repeated.
    log('ALERT unresolved submission batch=' + batch['id'] +
        ' age=' + str(round((now - batch['created_at']) / 3600, 1)) +
        'h; held for reconciliation')
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
        normalize(state)
        # Fingerprint everything without enqueueing historical events.
        collect(state, box, now)
        state['pending'] = {}
        save(path, state)
        log('baseline established events=' + str(len(state['seen'])) + '; no delivery')
        return state
    normalize(state)
    posting = False
    try:
        if now - state['last_scan'] > 86400:
            state['resync_due'] = True
        collect(state, box, now)
        save(path, state)
        anchor = state['delivered'][-1]['at'] if state['delivered'] else state['baseline']
        if (state['pending'] and now - anchor > STALL_ALERT and
                now - state['last_stall_alert'] > 3600):
            state['last_stall_alert'] = now
            save(path, state)
            log('ALERT stalled: no delivery for ' + str(int((now - anchor) / 3600)) +
                'h pending=' + str(len(state['pending'])) +
                ' inflight=' + str(bool(state['inflight'])))
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
        posting = True
        cli('issue', 'comment', 'add', INBOX, '--content-file', str(bodyfile),
            '--allow-external-file', '--output', 'json')
        posting = False
        acknowledge(state, now)
        state['gap'] = False
        log('delivered batch=' + digest + ' events=' + str(len(selected)))
    except Exception as exc:
        if isinstance(exc, CLIError):
            category, code = exc.category, exc.returncode
            summary = redact(exc.summary)
        else:
            category, code = ('unknown' if posting else 'local'), None
            summary = redact(str(exc))
        state['failures'] += 1
        state['retry_at'] = now + min(3600, 300 * 2 ** min(state['failures'] - 1, 4))
        state['last_error'] = {'at': now, 'type': type(exc).__name__, 'category': category,
                               'exit_code': code, 'summary': summary,
                               'batch': state['inflight']['id'] if state['inflight'] else None}
        if posting and isinstance(exc, CLIError) and category in ('rejected', 'local'):
            # The server definitively refused (or the CLI never ran): release is safe.
            released = state['inflight']['id'] if state['inflight'] else None
            state['inflight'] = None
            state['last_attempt'] = now
            audit(root, state, {'at': now, 'action': 'auto_release_rejected',
                                'operator': 'sidecar', 'batch_id': released,
                                'evidence': summary, 'category': category})
        log('ALERT failure=' + type(exc).__name__ + ' category=' + category +
            ' exit=' + ('-' if code is None else str(code)) + ' summary=' + summary +
            ' consecutive=' + str(state['failures']) +
            ' inflight=' + str(bool(state['inflight'])))
        save(path, state)
        raise
    save(path, state)
    return state


def common_parser(prog):
    ap = argparse.ArgumentParser(prog=prog)
    ap.add_argument('--root', default=str(ROOT))
    ap.add_argument('--apply', action='store_true')
    return ap


def adjudicate(args):
    if args.verdict == 'delivered' and not args.comment_id:
        raise SystemExit('adjudicate delivered requires --comment-id')
    root = Path(args.root)
    with service_lock(root):
        path = root / 'state.json'
        state = read(path)
        normalize(state)
        batch = state['inflight']
        if not batch or batch['id'] != args.batch:
            raise SystemExit('no inflight batch with id ' + args.batch)
        now = time.time()
        plan = ('batch=' + batch['id'] + ' verdict=' + args.verdict +
                ' events=' + str(len(batch['events'])))
        if not args.apply:
            print('dry-run adjudicate ' + plan)
            return
        backup(path)
        if args.verdict == 'delivered':
            acknowledge(state, now)
        else:
            if args.verdict == 'abandon':
                for eid, p in list(batch['events'].items()):
                    if state['pending'].get(eid) == p:
                        state['retired'][eid] = dict(
                            payload=p, retired_at=now,
                            ledger_id='adjudicate:' + batch['id'],
                            issue_id=p.get('issue_id'), identifier=p.get('identifier'),
                            reason='abandon:' + args.evidence, evidence=args.evidence)
                        del state['pending'][eid]
            state['inflight'] = None
            state['failures'] = 0
            state['retry_at'] = 0
            state['last_attempt'] = now
        audit(root, state, {'at': now, 'action': 'adjudicate', 'operator': args.operator,
                            'batch_id': batch['id'], 'evidence': args.evidence,
                            'verdict': args.verdict, 'comment_id': args.comment_id})
        save(path, state)
        print('applied adjudicate ' + plan)


def retire(args):
    root = Path(args.root)
    with service_lock(root):
        path = root / 'state.json'
        state = read(path)
        normalize(state)
        ledger = read(Path(args.ledger))
        if ledger['id'] in state['ledgers_applied']:
            raise SystemExit('ledger ' + ledger['id'] + ' already applied')
        now = time.time()
        plan = {'retired': [], 'missing': [], 'changed': []}
        for entry in ledger['entries']:
            for eid in entry['event_ids']:
                p = state['pending'].get(eid)
                if p is None:
                    plan['missing'].append(eid)
                elif fingerprint(p) != entry.get('fingerprints', {}).get(eid):
                    plan['changed'].append(eid)
                else:
                    plan['retired'].append((eid, entry))
        counts = {k: len(v) for k, v in plan.items()}
        print('retire ledger=' + ledger['id'] + ' retired=' + str(counts['retired']) +
              ' missing=' + str(counts['missing']) + ' changed=' + str(counts['changed']))
        if not args.apply:
            return
        backup(path)
        for eid, entry in plan['retired']:
            state['retired'][eid] = dict(
                payload=state['pending'].pop(eid), retired_at=now,
                ledger_id=ledger['id'], issue_id=entry.get('issue_id'),
                identifier=entry.get('identifier'), reason=entry.get('reason'),
                evidence=entry.get('evidence'))
        state['ledgers_applied'].append(ledger['id'])
        audit(root, state, {'at': now, 'action': 'retire',
                            'operator': ledger.get('operator'), 'batch_id': None,
                            'evidence': ledger['id'], 'counts': counts,
                            'event_ids': [e for e, _ in plan['retired']]})
        save(path, state)
        print('applied retire ledger=' + ledger['id'])


def rollback(args):
    root = Path(args.root)
    with service_lock(root):
        path = root / 'state.json'
        state = read(path)
        normalize(state)
        now = time.time()
        eids = [eid for eid, rec in state['retired'].items()
                if rec.get('ledger_id') == args.ledger_id]
        print('rollback ledger=' + args.ledger_id + ' restoring=' + str(len(eids)))
        if not args.apply:
            return
        backup(path)
        for eid in eids:
            rec = state['retired'].pop(eid)
            if eid not in state['pending']:
                state['pending'][eid] = rec['payload']
        if args.ledger_id in state['ledgers_applied']:
            state['ledgers_applied'].remove(args.ledger_id)
        audit(root, state, {'at': now, 'action': 'rollback', 'operator': args.operator,
                            'batch_id': None, 'evidence': args.ledger_id,
                            'event_ids': eids})
        save(path, state)
        print('applied rollback ledger=' + args.ledger_id)


def command(argv):
    verb, rest = argv[0], argv[1:]
    if verb == 'adjudicate':
        ap = common_parser('sidecar adjudicate')
        ap.add_argument('--batch', required=True)
        ap.add_argument('--verdict', required=True,
                        choices=('delivered', 'not-delivered', 'abandon'))
        ap.add_argument('--operator', required=True)
        ap.add_argument('--evidence', required=True)
        ap.add_argument('--comment-id')
        adjudicate(ap.parse_args(rest))
    elif verb == 'retire':
        ap = common_parser('sidecar retire')
        ap.add_argument('--ledger', required=True)
        retire(ap.parse_args(rest))
    elif verb == 'rollback':
        ap = common_parser('sidecar rollback')
        ap.add_argument('--ledger-id', required=True)
        ap.add_argument('--operator', default='operator')
        rollback(ap.parse_args(rest))
    else:
        raise SystemExit('unknown command: ' + verb)


def main():
    os.umask(0o077)
    if len(sys.argv) > 1:
        command(sys.argv[1:])
        return
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
