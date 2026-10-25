"""No real agent writes: in-memory fixture adapter and isolated private journals."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multica_board.write_actions import Journal, RuntimeActions, load_policy, validate_task_scope
from multica_board.write_adapter import MulticaWriteAdapter, WriteError, custom_model, project_agent, fingerprint
from multica_board.cli_runner import CliRunner, CliResult

WS = '11111111-1111-4111-8111-111111111111'
AGENT = '22222222-2222-4222-8222-222222222222'
RT = '33333333-3333-4333-8333-333333333333'
ACTOR = '44444444-4444-4444-8444-444444444444'
ISSUE = '55555555-5555-4555-8555-555555555555'
OTHER = '66666666-6666-4666-8666-666666666666'
TOKEN = 'fixture authorization'
POLICY = {'profile': 'fixture', 'actor_id': ACTOR, 'server_identity': 'https://fixture.invalid',
          'features': {'write_token_required': True},
          'token_sha256': hashlib.sha256(TOKEN.encode()).hexdigest(),
          'targets': [{'workspace_id': WS, 'agent_id': AGENT, 'audit_issue_id': ISSUE}]}
VALUES = dict(runtime_id=RT, model='old-model', thinking_level=None, service_tier=None, custom_model=None)


class FixtureAdapter:
    def __init__(self):
        self.values = copy.deepcopy(VALUES)
        self.version = 0
        self.writes = []
        self.comments = []
        self.active = False
        self.denied = False
        self.fail_apply = False
        self.mismatch = False
        self.custom_editable = True
        self.fail_audit = False
        self.fail_read = False

    def read(self, target):
        if self.denied:
            raise WriteError('permission_denied', 403)
        if self.fail_read:
            raise WriteError('snapshot_incomplete')
        return {'values': copy.deepcopy(self.values), 'revision': str(self.version), 'custom_editable': self.custom_editable}

    def idle(self, target):
        if self.active:
            raise WriteError('active_or_unknown')

    def validate_target(self, target, after):
        if after['runtime_id'] not in (RT, OTHER):
            raise WriteError('runtime_unavailable')

    def apply(self, target, changes):
        self.writes.append((target, changes))
        self.values.update(changes)
        self.version += 1
        if self.mismatch:
            self.values['model'] = 'unexpected'
        if self.fail_apply:
            raise WriteError('cli_result_unknown')

    def audit(self, target, record):
        self.comments.append(copy.deepcopy(record))
        if self.fail_audit:
            raise WriteError('cli_result_unknown')


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'audit.jsonl'
        self.journal = Journal(self.path)
        self.adapter = FixtureAdapter()
        self.now = 100
        self.actions = RuntimeActions(self.adapter, POLICY, self.journal, clock=lambda: self.now)

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def preview(self, changes=None):
        return self.actions.handle('preview', dict(workspace_id=WS, agent_id=AGENT, changes=changes or {'model': 'new-model'}), TOKEN)

    def execute(self, plan):
        return self.actions.handle('execute', {'id': plan['id'], 'confirmation': plan['confirmation']}, TOKEN)

    def assertCode(self, code, fn):
        with self.assertRaises(WriteError) as ctx:
            fn()
        self.assertEqual(ctx.exception.code, code)

    def test_permission_and_scope_denied_without_reads_or_writes(self):
        self.assertCode('permission_denied', lambda: self.actions.handle('catalog', {}, 'wrong'))
        self.assertCode('permission_denied', lambda: self.actions.preview(OTHER, AGENT, {'model': 'x'}))
        self.adapter.denied = True
        self.assertCode('permission_denied', self.preview)
        self.assertFalse(self.adapter.writes)

    def test_unconfirmed_and_wrong_confirmation_never_write(self):
        p = self.preview()
        self.assertFalse(self.adapter.writes)
        self.assertCode('approval_invalid', lambda: self.actions.execute(p['id'], 'wrong'))
        self.assertEqual(self.execute(p)['state'], 'applied')

    def test_approval_expiry_and_consumption(self):
        p = self.preview()
        self.now += 301
        self.assertCode('approval_expired', lambda: self.execute(p))
        self.assertCode('approval_invalid', lambda: self.execute(p))
        self.assertFalse(self.adapter.writes)

    def test_expiry_during_preflight(self):
        p = self.preview()
        self.adapter.idle = lambda _: setattr(self, 'now', 500)
        self.assertCode('approval_expired', lambda: self.execute(p))
        self.assertFalse(self.adapter.writes)

    def test_cas_conflict_and_consumption(self):
        p = self.preview()
        self.adapter.version += 1
        self.assertCode('cas_conflict', lambda: self.execute(p))
        self.assertCode('approval_invalid', lambda: self.execute(p))
        self.assertFalse(self.adapter.writes)

    def test_permissions_rechecked_after_preview(self):
        p = self.preview()
        self.adapter.denied = True
        self.assertCode('permission_denied', lambda: self.execute(p))
        self.assertFalse(self.adapter.writes)

    def test_active_at_preview_and_at_submit(self):
        self.adapter.active = True
        self.assertCode('active_or_unknown', self.preview)
        self.adapter.active = False
        p = self.preview()
        self.adapter.active = True
        self.assertCode('active_or_unknown', lambda: self.execute(p))
        self.assertFalse(self.adapter.writes)

    def test_config_changed_during_activity_scan(self):
        p = self.preview()
        self.adapter.idle = lambda _: setattr(self.adapter, 'version', 2)
        self.assertCode('cas_conflict', lambda: self.execute(p))
        self.assertFalse(self.adapter.writes)

    def test_readback_mismatch_locks_target_until_reconciliation(self):
        p = self.preview()
        self.adapter.mismatch = True
        result = self.execute(p)
        self.assertEqual((result['state'], result['code']), ('unknown', 'readback_mismatch'))
        self.assertCode('reconciliation_required', self.preview)
        self.assertEqual(self.actions.reconcile(WS, AGENT)['state'], 'unknown')
        self.adapter.values['model'] = 'new-model'
        self.assertEqual(self.actions.reconcile(WS, AGENT)['state'], 'reconciled_applied')
        self.assertEqual(self.preview({'model': 'next'})['state'], 'awaiting_confirmation')

    def test_timeout_is_not_retried_and_reconcile_is_read_only(self):
        p = self.preview()
        self.adapter.fail_apply = True
        self.assertEqual(self.execute(p)['state'], 'unknown')
        self.assertEqual(len(self.adapter.writes), 1)
        self.assertCode('reconciliation_required', self.preview)
        result = self.actions.reconcile(WS, AGENT)
        self.assertEqual(result['state'], 'reconciled_applied')
        self.assertEqual(result['actual_effect'], 'unknown')
        self.assertEqual(len(self.adapter.writes), 1)

    def test_no_commit_reconciles_unapplied(self):
        p = self.preview()
        self.adapter.apply = lambda *_: (_ for _ in ()).throw(WriteError('cli_result_unknown'))
        self.assertEqual(self.execute(p)['state'], 'unknown')
        self.assertEqual(self.actions.reconcile(WS, AGENT)['state'], 'reconciled_unapplied')

    def test_readback_failure_unknown(self):
        original = self.adapter.apply
        def apply(*args):
            original(*args)
            self.adapter.fail_read = True
        self.adapter.apply = apply
        self.assertEqual(self.execute(self.preview())['state'], 'unknown')

    def test_single_runtime_switch_and_custom_model_switch(self):
        p = self.preview({'runtime_id': OTHER})
        self.assertEqual(p['before']['runtime_id'], RT)
        self.assertEqual(p['after']['runtime_id'], OTHER)
        self.assertEqual(self.execute(p)['state'], 'applied')
        self.assertEqual(self.adapter.values['runtime_id'], OTHER)
        p = self.preview({'custom_model': 'custom-model'})
        self.assertEqual(self.execute(p)['state'], 'applied')
        self.assertEqual(self.adapter.values['custom_model'], 'custom-model')

    def test_durable_restart_loads_disk_and_binds_identity(self):
        p = self.preview()
        self.adapter.fail_apply = True
        self.execute(p)
        self.journal.close()
        self.journal = Journal(self.path)
        restarted = RuntimeActions(self.adapter, POLICY, self.journal)
        self.assertCode('reconciliation_required', lambda: restarted.preview(WS, AGENT, {'model': 'next'}))
        self.assertEqual(restarted.handle('status', {'id': p['id']}, TOKEN)['state'], 'unknown')
        changed_policy = dict(POLICY, server_identity='https://other.invalid')
        with self.assertRaises(ValueError): RuntimeActions(self.adapter, changed_policy, self.journal)

    def test_status_after_restart_excludes_confirmations_and_keeps_scope(self):
        p = self.preview()
        result = self.execute(p)
        restarted = RuntimeActions(self.adapter, POLICY, self.journal)
        result = restarted.handle('status', {'id': result['id']}, TOKEN)
        self.assertEqual(result['state'], 'applied')
        self.assertNotIn('confirmation', result)
        self.assertEqual(restarted.catalog()['recent_actions'][0]['id'], p['id'])

    def test_rollback_needs_separate_preview_and_confirmation(self):
        result = self.execute(self.preview())
        rollback = self.actions.rollback(result['id'])
        self.assertEqual(len(self.adapter.writes), 1)
        self.assertEqual(rollback['rollback_of'], result['id'])
        self.assertEqual(self.execute(rollback)['state'], 'applied')
        self.assertEqual(self.adapter.values, VALUES)

    def test_rollback_after_activity_timestamp_change_preserves_config_guard(self):
        original = self.adapter.read
        def read(target):
            snap = original(target)
            snap['config_revision'] = fingerprint(snap['values'])
            return snap
        self.adapter.read = read
        result = self.execute(self.preview())
        self.adapter.version += 1  # Agent status timestamps also change when a run ends.
        rollback = self.actions.rollback(result['id'])
        self.assertEqual(rollback['after']['model'], 'old-model')
        self.adapter.values['custom_model'] = 'external-change'
        with self.assertRaises(WriteError):
            self.actions.rollback(result['id'])

    def test_rollback_rejects_later_changes_even_same_values(self):
        result = self.execute(self.preview())
        self.adapter.version += 1
        self.assertCode('cas_conflict', lambda: self.actions.rollback(result['id']))

    def test_restart_drops_tokens_but_keeps_unknown_and_rollback(self):
        plan = self.preview()
        self.adapter.fail_apply = True
        self.execute(plan)
        restarted = RuntimeActions(self.adapter, POLICY, self.journal, clock=lambda: self.now)
        self.assertCode('approval_invalid', lambda: restarted.execute(plan['id'], plan['confirmation']))
        self.assertCode('reconciliation_required', lambda: restarted.preview(WS, AGENT, {'model': 'next'}))
        result = restarted.reconcile(WS, AGENT)
        rollback = restarted.rollback(result['id'])
        self.assertEqual(rollback['after']['model'], 'old-model')

    def test_crash_after_durable_executing_locks_after_restart(self):
        p = self.preview()
        self.actions._event(p, 'executing')
        restarted = RuntimeActions(self.adapter, POLICY, self.journal)
        self.assertCode('reconciliation_required', lambda: restarted.preview(WS, AGENT, {'model': 'next'}))

    def test_audit_append_only_safe_fields_and_failed_mirror(self):
        self.adapter.fail_audit = True
        p = self.preview()
        first = self.path.read_bytes()
        result = self.execute(p)
        self.assertTrue(self.path.read_bytes().startswith(first))
        self.assertEqual(result['audit'], 'unknown')
        raw = self.path.read_text()
        self.assertNotIn(p['confirmation'], raw)
        self.assertNotIn(TOKEN, raw)
        self.assertNotIn('new-model', json.dumps(self.adapter.comments))
        self.assertEqual(self.adapter.comments[-1]['fields'], ['model'])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_second_journal_writer_refused(self):
        with self.assertRaises(BlockingIOError):
            Journal(self.path)

    def test_protected_custom_args_and_secret_fields_rejected(self):
        self.adapter.custom_editable = False
        self.assertCode('custom_args_protected', lambda: self.preview({'custom_model': 'x'}))
        self.assertCode('invalid_fields', lambda: self.preview({'custom_env': 'x'}))
        self.assertCode('invalid_field', lambda: self.preview({'model': 'sk-fixture-secret'}))

    def test_null_and_empty_remain_distinct_in_preview(self):
        p = self.preview({'thinking_level': None})
        self.assertIsNone(p['before']['thinking_level'])
        self.assertEqual(p['after']['thinking_level'], '')
        self.assertEqual(self.execute(p)['state'], 'applied')

    def test_batch_is_explicitly_rejected_before_any_target(self):
        self.assertFalse(self.actions.catalog()['batch_enabled'])
        self.assertCode('invalid_request', lambda: self.actions.handle('preview', {'targets': [AGENT, OTHER], 'changes': {'model': 'x'}}, TOKEN))
        self.assertFalse(self.adapter.writes)

    def test_parallel_double_submit_has_exactly_one_write(self):
        p = self.preview()
        results = []
        def execute():
            try:
                results.append(self.execute(p)['state'])
            except WriteError as exc:
                results.append(exc.code)
        threads = [threading.Thread(target=execute) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertCountEqual(results, ['applied', 'approval_invalid'])
        self.assertEqual(len(self.adapter.writes), 1)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.raw = dict(VALUES, id=AGENT, workspace_id=WS, owner_id=ACTOR,
                        custom_args=[], updated_at='v1', status='idle', archived_at=None)
        self.runner = Mock(spec=CliRunner)
        self.runner.profile = 'fixture'
        self.runner.run.return_value = CliResult({'server_identity': POLICY['server_identity']})
        self.adapter = MulticaWriteAdapter(self.runner, POLICY)
        def call(args, ws=None):
            if args[:3] == ['user', 'profile', 'get']: return {'id': ACTOR}
            if args[:2] == ['agent', 'get']: return copy.deepcopy(self.raw)
            if args[:2] == ['issue', 'get']: return {'id': ISSUE, 'workspace_id': WS}
            if args[:2] == ['runtime', 'list']: return [{'id': RT, 'workspace_id': WS, 'status': 'online', 'provider': 'claude'}]
            return {}
        self.adapter._call = Mock(side_effect=call)

    def test_actor_owner_and_server_are_each_verified(self):
        self.assertEqual(self.adapter.read((WS, AGENT))['values'], VALUES)
        self.raw['owner_id'] = OTHER
        with self.assertRaises(WriteError): self.adapter.read((WS, AGENT))
        self.raw['owner_id'] = ACTOR
        self.runner.run.return_value = CliResult({'server_identity': 'https://other.invalid'})
        with self.assertRaises(WriteError): self.adapter.read((WS, AGENT))

    def test_task_pages_unknown_and_pending_deny(self):
        def row(status='completed'):
            return {'workspace_id': WS, 'agent_id': AGENT, 'status': status}
        self.runner.run.side_effect = [CliResult([row()] * 200, '--before "next"'), CliResult([row('running')])]
        with self.assertRaises(WriteError): self.adapter.idle((WS, AGENT))
        self.assertEqual(self.runner.run.call_count, 2)
        self.runner.run.side_effect = [CliResult([row()] * 200)]
        with self.assertRaises(WriteError): self.adapter.idle((WS, AGENT))
        self.runner.run.side_effect = [CliResult([])]
        self.adapter.idle((WS, AGENT))
        self.runner.run.side_effect = [CliResult([row('queued')])]
        with self.assertRaises(WriteError): self.adapter.idle((WS, AGENT))

    def test_scope_missing_and_malformed_tasks_fail_closed(self):
        for payload in ({'items': []}, [{'status': 'completed'}], None):
            self.runner.run.side_effect = None
            self.runner.run.return_value = CliResult(payload)
            with self.assertRaises(WriteError): self.adapter.idle((WS, AGENT))

    def test_custom_model_projection_never_exposes_other_args(self):
        self.raw['custom_args'] = ['--model=claude-sonnet', '--private-key', 'fixture-private']
        snap = project_agent(self.raw)
        self.assertEqual(snap['values']['custom_model'], 'claude-sonnet')
        self.assertFalse(snap['custom_editable'])
        self.assertNotIn('fixture-private', json.dumps(snap))
        self.raw['custom_args'][2] = 'changed-private'
        self.assertNotEqual(snap['revision'], project_agent(self.raw)['revision'])
        with self.assertRaises(WriteError): custom_model(['--model', 'a', '--model=b'])

    def test_model_conflict_and_provider_custom_model_rejection(self):
        with self.assertRaises(WriteError):
            self.adapter.validate_target((WS, AGENT), dict(VALUES, custom_model='different'))
        self.adapter._call.return_value = [{'id': RT, 'workspace_id': WS, 'status': 'online', 'provider': 'codex'}]
        self.adapter._call.side_effect = None
        with self.assertRaises(WriteError):
            self.adapter.validate_target((WS, AGENT), dict(VALUES, custom_model='old-model'))

    def test_audit_file_comment_has_no_model_values_or_secrets(self):
        self.adapter.audit((WS, AGENT), {'action_id': 'fixture-action', 'fields': ['model'], 'state': 'applied'})
        args, ws = self.adapter._call.call_args.args
        self.assertEqual(args[:4], ['issue', 'comment', 'add', ISSUE])
        self.assertIn('--content-file', args)
        self.assertNotIn('--content', args)
        self.assertEqual(ws, WS)

    def test_flags_are_allowlisted_and_clear_never_touches_secrets(self):
        self.adapter.apply((WS, AGENT), {'model': None, 'custom_model': 'claude-sonnet'})
        args, ws = self.adapter._call.call_args.args
        self.assertEqual(ws, WS)
        self.assertEqual(args[:5], ['agent', 'update', AGENT, '--model', ''])
        self.assertEqual(json.loads(args[-1]), ['--model', 'claude-sonnet'])

    def test_task_scoped_env_keeps_only_current_task_identity_without_owner_fallback(self):
        import subprocess
        env = {'MULTICA_TASK_CONFIG_ROOT': '/fixture/task', 'MULTICA_TOKEN': 'fixture-task',
               'MULTICA_WORKSPACE_ID': WS, 'PATH': '/fixture/bin'}
        with patch.dict(os.environ, env, clear=True):
            runner = CliRunner('fixture', task_scoped=True)
            runner._exec = Mock(return_value=subprocess.CompletedProcess([], 0, '{}', ''))
            MulticaWriteAdapter(runner, POLICY).apply((WS, AGENT), {'model': 'next'})
            child = runner._exec.call_args.kwargs['env']
            self.assertEqual(child['MULTICA_TASK_CONFIG_ROOT'], '/fixture/task')
            self.assertEqual(child['MULTICA_WORKSPACE_ID'], WS)
            self.assertEqual(child['MULTICA_TOKEN'], 'fixture-task')
        with patch.dict(os.environ, {}, clear=True):
            from multica_board.cli_runner import CliError
            with self.assertRaises(CliError):
                CliRunner('fixture', task_scoped=True)._env()

    def test_subprocess_write_no_retries_env_or_secret_error(self):
        import subprocess
        runner = CliRunner('fixture', retries=10)
        runner._exec = Mock(side_effect=subprocess.TimeoutExpired('fixture', 1))
        adapter = MulticaWriteAdapter(runner, POLICY)
        with self.assertRaises(WriteError) as ctx: adapter.apply((WS, AGENT), {'model': 'next'})
        self.assertEqual(str(ctx.exception), 'cli_result_unknown')
        self.assertEqual(runner._exec.call_count, 1)
        env = runner._exec.call_args.kwargs['env']
        self.assertFalse(any(k.startswith('MULTICA_') for k in env))


class PolicyTests(unittest.TestCase):
    def test_task_scope_rejects_cross_workspace_or_owner_fallback(self):
        with patch.dict(os.environ, {'MULTICA_TASK_CONFIG_ROOT':'/fixture/task','MULTICA_WORKSPACE_ID':WS}, clear=True):
            validate_task_scope(POLICY, [WS], False)
            for workspaces, all_workspaces in (([OTHER],False),([WS,OTHER],False),([WS],True)):
                with self.assertRaises(ValueError): validate_task_scope(POLICY,workspaces,all_workspaces)
            changed = dict(POLICY,targets=[dict(POLICY['targets'][0],workspace_id=OTHER)])
            with self.assertRaises(ValueError): validate_task_scope(changed,[WS],False)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError): validate_task_scope(POLICY,[WS],False)

    def test_private_policy_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'policy.json'
            path.write_text(json.dumps(POLICY))
            path.chmod(0o600)
            self.assertEqual(load_policy(path, 'fixture'), POLICY)
            with self.assertRaises(ValueError): load_policy(path, 'wrong-profile')
            path.chmod(0o644)
            with self.assertRaises(ValueError): load_policy(path, 'fixture')


class FeatureTests(unittest.TestCase):
    def test_defaults_overrides_and_unknown(self):
        from multica_board.features import resolve_features
        self.assertEqual(resolve_features(), {'write_token_required': False})
        self.assertEqual(resolve_features({'write_token_required': True}), {'write_token_required': True})
        for overrides in ({'typo': False}, {'write_token_required': 'false'}, [], False):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                resolve_features(overrides)

    def test_policy_optional_token_and_required_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'policy.json'
            policy = copy.deepcopy(POLICY)
            policy.pop('features')
            policy.pop('token_sha256')
            def load():
                path.write_text(json.dumps(policy))
                path.chmod(0o600)
                return load_policy(path, 'fixture')
            self.assertEqual(load(), policy)
            policy['features'] = {'write_token_required': True}
            with self.assertRaises(ValueError): load()
            policy['token_sha256'] = POLICY['token_sha256']
            self.assertEqual(load(), policy)
            policy['features'] = {'typo': False}
            with self.assertRaises(ValueError): load()
            policy['features'] = {'write_token_required': False}
            policy['token_sha256'] = 123
            with self.assertRaises(ValueError): load()
            policy.pop('token_sha256')
            load()
            link = Path(tmp) / 'link.json'
            link.symlink_to(path)
            with self.assertRaises(ValueError): load_policy(link, 'fixture')


class TokenOffActionTests(ActionTests):
    def setUp(self):
        super().setUp()
        policy = copy.deepcopy(POLICY)
        policy.pop('features')
        policy.pop('token_sha256')
        self.actions = RuntimeActions(self.adapter, policy, self.journal, clock=lambda: self.now)

    def preview(self, changes=None):
        return self.actions.handle('preview', dict(workspace_id=WS, agent_id=AGENT,
                                  changes=changes or {'model': 'new-model'}), None)

    def execute(self, plan):
        return self.actions.handle('execute', {'id':plan['id'], 'confirmation':plan['confirmation']}, None)

    def test_permission_and_scope_denied_without_reads_or_writes(self):
        self.actions.handle('catalog', {}, None)
        self.assertCode('permission_denied', lambda: self.actions.preview(OTHER, AGENT, {'model':'x'}))
        self.adapter.denied = True
        self.assertCode('permission_denied', self.preview)
        self.assertFalse(self.adapter.writes)
