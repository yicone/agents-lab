"""Narrow, non-retrying Multica write adapter. Never reuses cached Board data."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .cli_runner import neutral_cwd
from .redact import redact

FIELDS = ('runtime_id', 'model', 'thinking_level', 'service_tier', 'custom_model')
FLAGS = {'runtime_id': '--runtime-id', 'model': '--model',
         'thinking_level': '--thinking-level', 'service_tier': '--service-tier'}
TERMINAL = {'completed', 'failed', 'cancelled', 'canceled'}


class WriteError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def identifier(value, *, nullable=True):
    if nullable and value is None:
        return value
    if not isinstance(value, str) or len(value) > 160 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/+-]*|', value):
        raise WriteError('invalid_field', 400)
    if redact(value, limit=None) != value or value.startswith(('sk-', 'ghp_', 'github_pat_', 'xoxb-')) or '@' in value:
        raise WriteError('invalid_field', 400)
    return value


def custom_model(args):
    if args is None:
        args = []
    if not isinstance(args, list) or not all(isinstance(v, str) for v in args):
        raise WriteError('custom_args_unknown')
    values = []
    for i, arg in enumerate(args):
        if arg == '--model':
            if i + 1 == len(args) or args[i + 1].startswith('-'):
                raise WriteError('custom_model_ambiguous')
            values.append(identifier(args[i + 1], nullable=False))
        elif arg.startswith('--model='):
            values.append(identifier(arg[8:], nullable=False))
    if len(values) > 1 or (values and not values[0]):
        raise WriteError('custom_model_ambiguous')
    return values[0] if values else None


def project_agent(raw):
    required = ('id', 'workspace_id', 'runtime_id', 'model', 'thinking_level', 'service_tier', 'custom_args', 'updated_at')
    if not isinstance(raw, dict) or any(k not in raw for k in required):
        raise WriteError('snapshot_incomplete')
    values = {k: identifier(raw[k]) for k in FIELDS if k != 'custom_model'}
    values['custom_model'] = custom_model(raw['custom_args'])
    revision = fingerprint({'values': values, 'updated_at': raw['updated_at'], 'args': raw['custom_args']})
    return {'values': values, 'revision': revision,
            'custom_editable': not raw['custom_args'] or raw['custom_args'] == ['--model', values['custom_model']]
            or raw['custom_args'] == ['--model=' + (values['custom_model'] or '')]}


class MulticaWriteAdapter:
    def __init__(self, runner, policy):
        self.runner, self.policy = runner, policy

    def _call(self, args, ws=None):
        argv = [self.runner.binary, '--profile', self.runner.profile,
                '--server-url', self.policy['server_identity']]
        if ws:
            argv += ['--workspace-id', ws]
        argv += args + ['--output', 'json']
        try:
            proc = self.runner._exec(argv, capture_output=True, text=True,
                                     timeout=self.runner.timeout_s, cwd=neutral_cwd(),
                                     env=(self.runner._env() if self.runner.task_scoped else
                                          {k: v for k, v in self.runner._env().items() if not k.startswith('MULTICA_')}),
                                     stdin=subprocess.DEVNULL)
        except (subprocess.TimeoutExpired, OSError):
            raise WriteError('cli_result_unknown') from None
        if proc.returncode:
            raise WriteError('cli_result_unknown')
        try:
            return json.loads(proc.stdout)
        except (ValueError, TypeError):
            raise WriteError('cli_result_unknown') from None

    def authorize(self, target):
        if target not in [(t['workspace_id'], t['agent_id']) for t in self.policy['targets']]:
            raise WriteError('permission_denied', 403)
        origin = self.runner.run(['auth', 'status']).data.get('server_identity')
        if origin != self.policy['server_identity']:
            raise WriteError('identity_changed', 403)
        actor = self._call(['user', 'profile', 'get'])
        if not isinstance(actor, dict) or actor.get('id') != self.policy['actor_id']:
            raise WriteError('identity_changed', 403)
        ws, agent = target
        raw = self._call(['agent', 'get', agent], ws)
        if not isinstance(raw, dict) or raw.get('workspace_id') != ws or raw.get('id') != agent or raw.get('owner_id') != self.policy['actor_id']:
            raise WriteError('permission_denied', 403)
        if 'archived_at' not in raw or raw['archived_at'] is not None or raw.get('status') != 'idle':
            raise WriteError('active_or_unknown')
        entry = next(t for t in self.policy['targets'] if (t['workspace_id'], t['agent_id']) == target)
        issue = self._call(['issue', 'get', entry['audit_issue_id']], ws)
        if not isinstance(issue, dict) or issue.get('workspace_id') != ws:
            raise WriteError('audit_scope_invalid')
        return raw

    def read(self, target):
        return project_agent(self.authorize(target))

    def idle(self, target):
        ws, agent = target
        cursor, seen = None, set()
        for _ in range(50):
            args = ['agent', 'tasks', agent, '--limit', '200']
            if cursor:
                args += ['--before', cursor]
            res = self.runner.run(args, workspace_id=ws)
            rows = res.data
            if not isinstance(rows, list):
                raise WriteError('activity_unknown')
            for row in rows:
                if not isinstance(row, dict) or row.get('workspace_id') != ws or row.get('agent_id') != agent or row.get('status') not in TERMINAL:
                    raise WriteError('active_or_unknown')
            nxt = res.next_cursor
            if not nxt and len(rows) < 200:
                return
            if not nxt or nxt in seen:
                break
            seen.add(nxt)
            cursor = nxt
        raise WriteError('activity_unknown')

    def validate_target(self, target, after):
        rows = self._call(['runtime', 'list'], target[0])
        matching = [r for r in rows if isinstance(r, dict) and r.get('id') == after['runtime_id'] and r.get('workspace_id') == target[0]] if isinstance(rows, list) else []
        if len(matching) != 1 or matching[0].get('status') != 'online':
            raise WriteError('runtime_unavailable')
        if after['model'] and after['custom_model'] and after['model'] != after['custom_model']:
            raise WriteError('model_conflict')
        if after['custom_model'] is not None and matching[0].get('provider') in ('codex', 'openclaw'):
            raise WriteError('custom_model_unsupported')

    def apply(self, target, changes):
        args = ['agent', 'update', target[1]]
        for field, value in changes.items():
            if field == 'custom_model':
                args += ['--custom-args', json.dumps([] if value is None else ['--model', value])]
            else:
                args += [FLAGS[field], '' if value is None else value]
        self._call(args, target[0])

    def audit(self, target, record):
        entry = next(t for t in self.policy['targets'] if (t['workspace_id'], t['agent_id']) == target)
        ws, _ = target
        issue = self._call(['issue', 'get', entry['audit_issue_id']], ws)
        if not isinstance(issue, dict) or issue.get('workspace_id') != ws:
            raise WriteError('audit_scope_invalid')
        with tempfile.TemporaryDirectory(prefix='atlas-audit-') as folder:
            path = Path(folder) / 'audit.md'
            path.write_text('Atlas 配置操作审计\n' + json.dumps(record, ensure_ascii=False, sort_keys=True), encoding='utf-8')
            os.chmod(path, 0o600)
            self._call(['issue', 'comment', 'add', entry['audit_issue_id'], '--content-file', str(path), '--allow-external-file'], ws)
