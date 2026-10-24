"""Opt-in runtime actions: preview, single-use approval, readback, reconciliation.

Multica CLI has no conditional update or server-side task fence. We re-read and
compare immediately before submitting, but another writer can still change the
agent between that read and submission. Post-write readback detects a mismatch
and locks the target as unknown until reconciliation; it cannot detect every
interleaving (for example, another writer's value overwritten by this submit).
The process lock is not atomic protection from external writers. This is an
accepted limitation for ACM-458 (user decision, 2026-10-24).
"""
from __future__ import annotations

import copy
import fcntl
import hmac
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time
from pathlib import Path
from uuid import UUID, uuid4

from .plan_contract import server_identity
from .write_adapter import FIELDS, WriteError, identifier


def uuid_text(value):
    try:
        if str(UUID(value)) == value:
            return value
    except (ValueError, TypeError, AttributeError):
        pass
    raise WriteError('invalid_identity', 400)


def load_policy(path, profile):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('write policy must be an owner-only regular file')
    value = json.loads(path.read_text(encoding='utf-8'))
    if set(value) != {'profile', 'actor_id', 'server_identity', 'token_sha256', 'targets'} or value['profile'] != profile:
        raise ValueError('invalid write policy or profile mismatch')
    uuid_text(value['actor_id'])
    if server_identity(value['server_identity']) != value['server_identity'] or not re.fullmatch('[0-9a-f]{64}', value['token_sha256']):
        raise ValueError('invalid write policy binding')
    if not isinstance(value['targets'], list) or not 1 <= len(value['targets']) <= 100:
        raise ValueError('write policy requires explicit targets')
    pairs = set()
    for row in value['targets']:
        if set(row) != {'workspace_id', 'agent_id', 'audit_issue_id'}:
            raise ValueError('invalid write target')
        for key in row:
            uuid_text(row[key])
        pair = (row['workspace_id'], row['agent_id'])
        if pair in pairs:
            raise ValueError('duplicate write target')
        pairs.add(pair)
    return value


def validate_task_scope(policy, workspaces, all_workspaces):
    """A task-scoped writer may use only the injected workspace, never Owner fallback."""
    task_workspace = os.environ.get("MULTICA_WORKSPACE_ID")
    if (not os.environ.get("MULTICA_TASK_CONFIG_ROOT") or not task_workspace
            or all_workspaces or workspaces != [task_workspace]
            or any(t["workspace_id"] != task_workspace for t in policy["targets"])):
        raise ValueError("task-scoped writes require exactly the current workspace")


class Journal:
    """One writer per append-only owner-private journal, across listener processes."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('journal must be owner-only')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.file = os.fdopen(fd, 'a+', encoding='utf-8')
        except Exception:
            os.close(fd)
            raise
        self.file.seek(0)
        try:
            self.events = [json.loads(line) for line in self.file if line.strip()]
        except Exception:
            self.close()
            raise ValueError('journal is unreadable; reconcile before restarting') from None

    def append(self, event):
        line = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        self.file.write(line + '\n')
        self.file.flush()
        os.fsync(self.file.fileno())
        self.events.append(copy.deepcopy(event))

    def close(self):
        self.file.close()


class RuntimeActions:
    def __init__(self, adapter, policy, journal, *, clock=time.time, ttl=300):
        self.adapter, self.policy, self.journal = adapter, copy.deepcopy(policy), journal
        self.clock, self.ttl = clock, min(300, max(1, ttl))
        self.lock = threading.RLock()
        self.previews = {}
        self.results = {}
        self.unresolved = {}
        # Restart never restores approval tokens. In-flight records become unknown.
        for event in journal.events:
            if (event.get('server_identity') != self.policy['server_identity']
                    or event.get('actor_id') != self.policy['actor_id']):
                raise ValueError('journal identity mismatch')
            target = tuple(event['target'])
            if event['state'] in ('executing', 'unknown'):
                self.unresolved[target] = event
            elif event['state'] in ('applied', 'reconciled_applied', 'reconciled_unapplied'):
                self.unresolved.pop(target, None)
                self.results[event['id']] = event

    def authenticate(self, token):
        if not isinstance(token, str) or not hmac.compare_digest(
                self.policy['token_sha256'], hashlib.sha256(token.encode()).hexdigest()):
            raise WriteError('permission_denied', 403)

    def _target(self, ws, agent):
        target = (uuid_text(ws), uuid_text(agent))
        if target not in [(t['workspace_id'], t['agent_id']) for t in self.policy['targets']]:
            raise WriteError('permission_denied', 403)
        return target

    def catalog(self):
        allowed = {(t['workspace_id'], t['agent_id']) for t in self.policy['targets']}
        recent = {**self.results, **{e['id']: e for e in self.unresolved.values()}}
        history = [{k: e.get(k) for k in ('id', 'target', 'state', 'at', 'rollback_of')}
                   for e in recent.values() if tuple(e['target']) in allowed]
        return {'recent_actions': sorted(history, key=lambda e: e['at'], reverse=True)[:20],
                'targets': self.policy['targets'], 'actor_id': self.policy['actor_id'],
                'server_identity': self.policy['server_identity'], 'batch_enabled': False,
                'concurrency': 'pre_submit_check_only', 'ttl_seconds': self.ttl}

    def _event(self, plan, state, **extra):
        event = {k: copy.deepcopy(plan[k]) for k in ('id', 'target', 'before', 'after', 'changes', 'revision', 'rollback_of')}
        event.update(state=state, at=self.clock(), actor_id=self.policy['actor_id'],
                     server_identity=self.policy['server_identity'], **extra)
        self.journal.append(event)
        return event

    def _mirror(self, plan, state):
        record = {'action_id': plan['id'], 'actor_id': self.policy['actor_id'],
                  'server_identity': self.policy['server_identity'], 'target': plan['target'],
                  'fields': sorted(plan['changes']), 'state': state,
                  'rollback_of': plan['rollback_of'], 'at': self.clock()}
        try:
            self.adapter.audit(tuple(plan['target']), record)
            status = 'recorded'
        except Exception:
            status = 'unknown'  # Never automatically retry a comment whose receipt was lost.
        self.journal.append(dict(record, state='audit_' + status))
        return status

    def preview(self, ws, agent, changes, *, rollback_of=None, expected=None):
        with self.lock:
            target = self._target(ws, agent)
            if target in self.unresolved:
                raise WriteError('reconciliation_required')
            if not isinstance(changes, dict) or not changes or set(changes) - set(FIELDS):
                raise WriteError('invalid_fields', 400)
            clean = {k: identifier(v) for k, v in changes.items()}
            if clean.get('custom_model') == '':
                raise WriteError('invalid_field', 400)
            if 'runtime_id' in clean:
                uuid_text(clean['runtime_id'])
            # CLI clear for these fields is an explicit empty string.
            clean = {k: ('' if v is None and k != 'custom_model' else v) for k, v in clean.items()}
            before = self.adapter.read(target)
            if expected is not None and before['revision'] != expected:
                raise WriteError('cas_conflict')
            if 'custom_model' in clean and not before['custom_editable']:
                raise WriteError('custom_args_protected')
            after = {**before['values'], **clean}
            self.adapter.validate_target(target, after)
            self.adapter.idle(target)
            clean = {k: v for k, v in clean.items() if before['values'][k] != v}
            if not clean:
                raise WriteError('no_changes', 400)
            now = self.clock()
            self.previews = {k: p for k, p in self.previews.items() if p['expires_at'] > now}
            if len(self.previews) >= 100:
                raise WriteError('too_many_previews', 429)
            plan = {'id': str(uuid4()), 'target': list(target), 'before': before['values'],
                    'after': after, 'changes': clean, 'revision': before['revision'],
                    'rollback_of': rollback_of, 'expires_at': now + self.ttl,
                    'confirmation': secrets.token_urlsafe(32)}
            self.previews[plan['id']] = plan
            self._event(plan, 'previewed')
            return copy.deepcopy(dict(plan, state='awaiting_confirmation', actual_effect='unknown'))

    def execute(self, action_id, confirmation):
        with self.lock:
            plan = self.previews.get(action_id)
            if not plan or not isinstance(confirmation, str) or not hmac.compare_digest(plan['confirmation'], confirmation):
                raise WriteError('approval_invalid')
            # Consume before any calls, including failures. A rejected preview must be rebuilt.
            self.previews.pop(action_id)
            if plan['expires_at'] <= self.clock():
                self._event(plan, 'rejected', code='approval_expired')
                raise WriteError('approval_expired')
            target = tuple(plan['target'])
            try:
                if target in self.unresolved:
                    raise WriteError('reconciliation_required')
                current = self.adapter.read(target)
                if current['revision'] != plan['revision']:
                    raise WriteError('cas_conflict')
                self.adapter.validate_target(target, plan['after'])
                self.adapter.idle(target)
                # Re-read after the potentially paginated activity scan.
                if self.adapter.read(target)['revision'] != plan['revision']:
                    raise WriteError('cas_conflict')
            except Exception as exc:
                code = exc.code if isinstance(exc, WriteError) else 'preflight_failed'
                self._event(plan, 'rejected', code=code)
                self._mirror(plan, 'rejected')
                raise WriteError(code) from None
            if plan['expires_at'] <= self.clock():
                self._event(plan, 'rejected', code='approval_expired')
                raise WriteError('approval_expired')
            # No CLI conditional update exists: the pre-submit read above does not
            # prevent a concurrent write before apply. A readback mismatch below
            # becomes unknown and keeps this target locked until reconciliation.
            self._event(plan, 'executing')  # fsync BEFORE CLI submit; restart locks target.
            self.unresolved[target] = plan
            try:
                self.adapter.apply(target, plan['changes'])
                observed = self.adapter.read(target)
                if observed['values'] != plan['after']:
                    raise WriteError('readback_mismatch')
                event = self._event(plan, 'applied', readback_revision=observed['revision'])
                self.unresolved.pop(target, None)
            except Exception as exc:
                event = self._event(plan, 'unknown', code=exc.code if isinstance(exc, WriteError) else 'readback_failed')
                self.unresolved[target] = event
            self.results[event['id']] = event
            audit = self._mirror(plan, event['state'])
            return dict(event, audit=audit, actual_effect='unknown')

    def rollback(self, action_id):
        with self.lock:
            prior = self.results.get(action_id)
            if not prior or prior['state'] not in ('applied', 'reconciled_applied'):
                raise WriteError('rollback_unavailable')
            changes = {k: prior['before'][k] for k in prior['changes']}
            return self.preview(*prior['target'], changes, rollback_of=action_id, expected=prior['readback_revision'])

    def reconcile(self, ws, agent):
        with self.lock:
            target = self._target(ws, agent)
            prior = self.unresolved.get(target)
            if not prior:
                raise WriteError('no_unknown_action')
            current = self.adapter.read(target)
            if current['values'] == prior['after']:
                state = 'reconciled_applied'
            elif current['values'] == prior['before']:
                state = 'reconciled_unapplied'
            else:
                return {'state': 'unknown', 'code': 'reconciliation_conflict', 'actual_effect': 'unknown'}
            event = self._event(prior, state, readback_revision=current['revision'])
            self.unresolved.pop(target)
            self.results[event['id']] = event
            return dict(event, audit=self._mirror(prior, state), actual_effect='unknown')

    def handle(self, operation, data, token):
        with self.lock:
            return self._handle(operation, data, token)

    def _handle(self, operation, data, token):
        self.authenticate(token)
        expected = {'catalog': set(), 'status': {'id'}, 'preview': {'workspace_id', 'agent_id', 'changes'},
                    'execute': {'id', 'confirmation'}, 'rollback': {'id'},
                    'reconcile': {'workspace_id', 'agent_id'}}
        if operation not in expected or not isinstance(data, dict) or set(data) != expected[operation]:
            raise WriteError('invalid_request', 400)
        if operation == 'catalog':
            return self.catalog()
        if operation == 'status':
            if not isinstance(data['id'], str):
                raise WriteError('invalid_request', 400)
            event = self.results.get(data['id']) or next(
                (e for e in self.unresolved.values() if e['id'] == data['id']), None)
            if not event:
                raise WriteError('action_not_found', 404)
            self._target(*event['target'])
            unresolved = self.unresolved.get(tuple(event['target']))
            pending = unresolved is not None and unresolved['id'] == event['id']
            mirror = next((e for e in reversed(self.journal.events)
                           if e.get('action_id') == event['id'] and e['state'].startswith('audit_')), None)
            return dict(event, state='unknown' if pending else event['state'], actual_effect='unknown',
                        audit='recorded' if mirror and mirror['state'] == 'audit_recorded' else 'unknown')
        if operation == 'preview':
            return self.preview(data['workspace_id'], data['agent_id'], data['changes'])
        if operation == 'execute':
            if not isinstance(data['id'], str):
                raise WriteError('invalid_request', 400)
            return self.execute(data['id'], data['confirmation'])
        if operation == 'rollback':
            if not isinstance(data['id'], str):
                raise WriteError('invalid_request', 400)
            return self.rollback(data['id'])
        return self.reconcile(data['workspace_id'], data['agent_id'])
