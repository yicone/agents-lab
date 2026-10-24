import copy
import datetime as dt
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import rotate as r

NOW = dt.datetime(2026, 10, 24, tzinfo=dt.timezone.utc)


def accounts():
    return [dict(id='a', email='current@example.com', isSystemAccount=True),
            dict(id='b', email='target@example.com', isSystemAccount=False)]


def usage(email, session=90, weekly=90, reset=1, method='plus'):
    def window(remaining, minutes, hours):
        return dict(usedPercent=100-remaining, windowMinutes=minutes,
                    resetsAt=(NOW + dt.timedelta(hours=hours)).isoformat())
    return dict(provider='codex', usage=dict(accountEmail=email, loginMethod=method,
                updatedAt=NOW.isoformat(), dataConfidence='exact',
                primary=window(session, 300, reset), secondary=window(weekly, 10080, 24)))


def views(session=10, weekly=90):
    return r.snapshot(accounts(), [usage('current@example.com', session, weekly),
                                  usage('target@example.com')], NOW)


class PolicyTests(unittest.TestCase):
    def test_either_window_triggers_and_threshold_is_strict(self):
        for session, weekly in [(14, 90), (90, 14)]:
            self.assertEqual(r.choose('a', views(session, weekly))[0]['id'], 'b')
        self.assertEqual(r.choose('a', views(15, 15))[1], 'current_above_threshold')

    def test_hysteresis_checks_both_windows(self):
        for session, weekly in [(39, 90), (90, 39)]:
            v = r.snapshot(accounts(), [usage('current@example.com', 1),
                           usage('target@example.com', session, weekly)], NOW)
            self.assertIsNone(r.choose('a', v)[0])
        v = r.snapshot(accounts(), [usage('current@example.com', 1), usage('target@example.com', 40, 40)], NOW)
        self.assertIsNotNone(r.choose('a', v)[0])

    def test_ranking_headroom_band_then_reset(self):
        v = views()
        v[1].update(headroom=81, reset_seconds=100)
        v.append(dict(v[1], id='c', headroom=89, reset_seconds=200))
        self.assertEqual(r.choose('a', v)[0]['id'], 'b')
        v[2]['headroom'] = 90
        self.assertEqual(r.choose('a', v)[0]['id'], 'c')

    def test_free_never_treated_as_session_and_is_opt_in_last(self):
        free = usage('target@example.com', method='free')
        free['credits'] = dict(balanceReadSucceeded=True, remaining=100)
        free['usage']['secondary'] = None
        v = r.snapshot(accounts(), [usage('current@example.com', 1), free], NOW)
        self.assertEqual(v[1]['kind'], 'credits')
        self.assertIsNone(r.choose('a', v)[0])
        self.assertEqual(r.choose('a', v, allow_credits=True)[0]['id'], 'b')
        v.append(dict(v[0], id='c', headroom=40))
        self.assertEqual(r.choose('a', v, allow_credits=True)[0]['id'], 'c')

    def test_unknown_data_fails_closed(self):
        original = usage('target@example.com')
        for field, value in [('updatedAt', 'bad'), ('updatedAt', (NOW-dt.timedelta(minutes=6)).isoformat()),
                             ('dataConfidence', 'estimated'), ('secondary', None),
                             ('primary', dict(usedPercent=float('nan')))]:
            candidate = copy.deepcopy(original)
            candidate['usage'][field] = value
            v = r.snapshot(accounts(), [usage('current@example.com', 1), candidate], NOW)
            self.assertEqual(v[1]['kind'], 'unavailable')
            self.assertIsNone(r.choose('a', v)[0])
        for payload in [[], [dict(provider='codex', error=dict(code=69))], [original, original]]:
            v = r.snapshot(accounts(), payload, NOW)
            self.assertEqual(r.choose('a', v)[1], 'current_quota_unknown')

    def test_inventory_ambiguity_fails_closed(self):
        for rows in [[], accounts()*2, [dict(accounts()[0], isSystemAccount=False)],
                     [dict(accounts()[0], isSystemAccount='yes')]]:
            with self.assertRaises(r.Unavailable):
                r.inventory(rows)


class SafetyTests(unittest.TestCase):
    def test_idle_requires_task_proof_and_no_codex_processes(self):
        status = dict(status='running', active_task_count=0, running_task_count=0, resource_wait_task_count=0)
        with patch.object(r, 'read_json', return_value=status), patch.object(r, 'classified_tasks', return_value=0), patch.object(r, 'command', return_value='/usr/bin/init\n/Applications/CodexBar'):
            self.assertTrue(r.idle('multica')[0])
        for proc in ['/path/codex', '/path/codex-acp', '/path/Codex (Renderer)']:
            with patch.object(r, 'read_json', return_value=status), patch.object(r, 'classified_tasks', return_value=0), patch.object(r, 'command', return_value=proc):
                self.assertFalse(r.idle('multica')[0])
        with patch.object(r, 'read_json', return_value=dict(status, active_task_count=1)), \
             patch.object(r, 'classified_tasks', side_effect=r.Unavailable('task_runtime_unknown')):
            self.assertFalse(r.idle('multica')[0])
        for bad in [dict(status='running'), dict(status, running_task_count=None)]:
            with patch.object(r, 'read_json', return_value=bad):
                self.assertEqual(r.idle('multica'), (False, 'task_runtime_unknown'))

    def test_profile_is_forwarded_without_shell_interpolation(self):
        status = dict(status='running', active_task_count=0,
                      running_task_count=0, resource_wait_task_count=0)
        with patch.object(r, 'read_json', return_value=status) as read, \
             patch.object(r, 'classified_tasks', return_value=0), \
             patch.object(r, 'command', return_value='/usr/bin/init'):
            self.assertTrue(r.idle('multica', 'desktop-api.multica.ai')[0])
            read.assert_called_once_with(['multica', '--profile', 'desktop-api.multica.ai',
                                          'daemon', 'status', '--output', 'json'])

    def execute(self, apply=False, idle_result=(True, 'idle'), changed=False, late_busy=False, expired=False):
        payload = [usage('current@example.com', 1), usage('target@example.com')]
        args = SimpleNamespace(apply=apply, codexbar='codexbar', multica='multica', multica_profile='desktop-api.multica.ai',
                               low=15, target=40, allow_credits=False, log=None)
        fresh = accounts()
        if changed:
            fresh[0]['email'] = 'changed@example.com'
        with patch.object(r, 'read_json', side_effect=[accounts(), payload, fresh]), \
             patch.object(r, 'snapshot', side_effect=[r.snapshot(accounts(), payload, NOW),
                                                    r.snapshot(accounts(), payload, NOW+dt.timedelta(minutes=6) if expired else NOW)]), \
             patch.object(r, 'idle', side_effect=[idle_result, (False, 'busy') if late_busy else idle_result]), \
             patch.object(r, 'command') as cmd, patch.object(r, 'emit') as emit:
            result = r.run(args)
            return result, cmd.call_args_list, emit.call_args

    def test_default_dry_run_never_promotes_and_redacts(self):
        result, calls, emitted = self.execute()
        self.assertEqual(result['action'], 'would_promote')
        self.assertEqual(calls, [])
        self.assertNotIn('@', json.dumps(result))
        self.assertNotIn('example.com', str(emitted))

    def test_busy_never_promotes_even_apply(self):
        result, calls, _ = self.execute(True, (False, 'codex_process_present'))
        self.assertEqual(result['action'], 'skip')
        self.assertEqual(calls, [])

    def test_apply_rechecks_inventory(self):
        result, calls, _ = self.execute(True, changed=True)
        self.assertEqual(result['reason'], 'inventory_changed')
        self.assertEqual(calls, [])
        result, calls, _ = self.execute(True)
        self.assertEqual(result['action'], 'promoted')
        self.assertEqual(calls[0].args[0], ['codexbar', 'codex-accounts', 'promote', 'b', '--json'])

    def test_late_task_or_expired_reading_blocks_apply(self):
        for kwargs, reason in [({'late_busy': True}, 'became_busy'),
                               ({'expired': True}, 'quota_snapshot_expired')]:
            result, calls, _ = self.execute(True, **kwargs)
            self.assertEqual(result['reason'], reason)
            self.assertEqual(calls, [])

    def test_failure_never_echoes_upstream_output(self):
        with patch.object(r.subprocess, 'run', return_value=SimpleNamespace(returncode=69, stdout='secret', stderr='secret')):
            with self.assertRaisesRegex(r.Unavailable, '^command_failed$'):
                r.command(['anything'])


class TaskClassificationTests(unittest.TestCase):
    def check_gate(self, tasks, providers=None, count=None, failure=None,
                   after=None, profile=None, process='/usr/bin/init', available=None):
        providers = providers or {'r1': 'hermes', 'r2': 'codex'}
        count = len(tasks) if count is None else count
        status = dict(status='running', active_task_count=count, running_task_count=count,
                      resource_wait_task_count=0,
                      workspaces=[dict(id='w1', runtimes=['r1', 'r2']),
                                  dict(id='w2', runtimes=['r3'])])
        def read(argv):
            if failure and failure in argv:
                raise r.Unavailable('secret upstream error')
            if 'daemon' in argv:
                return status if read.calls == 0 or after is None else after
            if 'workspace' in argv:
                return [dict(id='w1'), dict(id='w2')] if available is None else available
            wid = argv[argv.index('--workspace-id')+1]
            if 'runtime' in argv:
                return ([dict(id=k, provider=v) for k, v in providers.items()]
                        if wid == 'w1' else [dict(id='r3', provider='cursor')])
            if 'agent' in argv:
                return [dict(id='a1', runtime_id='r1')] if wid == 'w1' else [dict(id='a2', runtime_id='r3')]
            raise AssertionError(argv)
        read.calls = 0
        def read_counted(argv):
            result = read(argv)
            if 'daemon' in argv:
                read.calls += 1
            return result
        def pages(argv, aid):
            if failure == 'tasks':
                raise r.Unavailable('secret upstream error')
            return iter(t for t in tasks if t.get('agent_id') == aid)
        with patch.object(r, 'read_json', side_effect=read_counted) as reads, \
             patch.object(r, 'task_pages', side_effect=pages) as paged, \
             patch.object(r, 'command', return_value=process):
            result = r.idle('multica', profile)
            for call in reads.call_args_list:
                argv = call.args[0]
                if 'agent' in argv or 'runtime' in argv:
                    self.assertIn('--workspace-id', argv)
                if profile:
                    self.assertEqual(argv[:3], ['multica', '--profile', profile])
            for call in paged.call_args_list:
                self.assertIn('--workspace-id', call.args[0])
            return result

    def task(self, rid='r1', state='running', wid='w1', aid='a1', tid='t1'):
        return dict(id=tid, runtime_id=rid, status=state, workspace_id=wid, agent_id=aid)

    def test_all_non_codex_across_workspaces_passes(self):
        tasks = [self.task(), self.task('r3', 'resource_wait', 'w2', 'a2', 't2')]
        self.assertEqual(self.check_gate(tasks, profile='desktop-api.multica.ai'), (True, 'idle'))

    def test_unused_daemon_runtime_need_not_appear_in_runtime_list(self):
        # r2 is still registered by daemon but has no active runs or API row.
        self.assertEqual(self.check_gate([self.task()], providers={'r1': 'hermes'}),
                         (True, 'idle'))
        self.assertEqual(self.check_gate([self.task('r2')], providers={'r1': 'hermes'}),
                         (False, 'task_runtime_unknown'))

    def test_second_workspace_read_failure_cannot_pass_partial_total(self):
        for stage in ('agent', 'runtime'):
            status = dict(status='running', active_task_count=0, running_task_count=0,
                          resource_wait_task_count=0, workspaces=[
                              dict(id='w1', runtimes=['r1']), dict(id='w2', runtimes=['r2'])])
            visited = []
            def read(argv):
                if 'daemon' in argv:
                    return status
                if 'workspace' in argv:
                    return [dict(id='w1'), dict(id='w2')]
                wid = argv[argv.index('--workspace-id') + 1]
                visited.append((wid, 'agent' if 'agent' in argv else 'runtime'))
                if wid == 'w2' and stage in argv:
                    raise r.Unavailable('command_failed')
                return []
            with patch.object(r, 'read_json', side_effect=read), patch.object(r, 'command') as cmd:
                self.assertEqual(r.idle('multica'), (False, 'task_runtime_unknown'))
                self.assertIn(('w2', stage), visited)
                cmd.assert_not_called()

    def test_missing_daemon_workspace_fails_closed(self):
        self.assertEqual(self.check_gate([], available=[dict(id='w1')]),
                         (False, 'task_runtime_unknown'))

    def test_codex_in_other_workspace_blocks(self):
        # Move the Codex binding into workspace 2, proving this isn't a
        # current-workspace-only check.
        status = dict(status='running', active_task_count=1, running_task_count=1,
                      resource_wait_task_count=0,
                      workspaces=[dict(id='w1', runtimes=['r1']), dict(id='w2', runtimes=['r2'])])
        def read(argv):
            if 'daemon' in argv:
                return status
            if 'workspace' in argv:
                return [dict(id='w1'), dict(id='w2')]
            wid = argv[argv.index('--workspace-id')+1]
            if 'runtime' in argv:
                return [dict(id='r1', provider='hermes')] if wid == 'w1' else [dict(id='r2', provider='codex')]
            return [dict(id='a1')] if wid == 'w1' else [dict(id='a2')]
        with patch.object(r, 'read_json', side_effect=read), \
             patch.object(r, 'task_pages', side_effect=lambda argv, aid:
                          iter([] if aid == 'a1' else [self.task('r2', wid='w2', aid='a2')])):
            self.assertEqual(r.idle('multica'), (False, 'codex_task_present'))

    def test_mixed_runtime_and_codex_states_block(self):
        for state in r.ACTIVE:
            tasks = [self.task(), self.task('r2', state, tid='t2')]
            self.assertEqual(self.check_gate(tasks), (False, 'codex_task_present'))

    def test_mismatched_total_fails_closed_even_with_zero_daemon_count(self):
        for count in (0, 2):
            self.assertEqual(self.check_gate([self.task()], count=count),
                             (False, 'task_runtime_unknown'))

    def test_any_read_failure_fails_closed(self):
        for failure in ('daemon', 'workspace', 'runtime', 'agent', 'tasks'):
            self.assertEqual(self.check_gate([self.task()], failure=failure),
                             (False, 'task_runtime_unknown'))

    def test_unknown_runtime_provider_state_and_duplicate_fail_closed(self):
        for tasks, providers in [([self.task('missing')], None),
                                 ([self.task()], {'r1': 'new-wrapper', 'r2': 'codex'}),
                                 ([self.task(state='new_state')], None),
                                 ([self.task(), self.task()], None)]:
            self.assertEqual(self.check_gate(tasks, providers=providers),
                             (False, 'task_runtime_unknown'))

    def test_queued_uses_binding_and_terminal_history_is_ignored(self):
        self.assertEqual(self.check_gate([self.task(None, 'queued'),
                                        self.task('r2', 'completed', tid='t2')], count=1),
                         (True, 'idle'))
        self.assertEqual(self.check_gate([self.task(None)]), (False, 'task_runtime_unknown'))

    def test_changed_daemon_snapshot_fails_closed(self):
        self.assertEqual(self.check_gate([self.task()], after=dict(status='running', active_task_count=0,
                         running_task_count=0, resource_wait_task_count=0, workspaces=[])),
                         (False, 'task_runtime_unknown'))

    def test_reordered_daemon_workspaces_passes(self):
        after = dict(status='running', active_task_count=2, running_task_count=2,
                     resource_wait_task_count=0, workspaces=[
                         dict(id='w2', runtimes=['r3']),
                         dict(id='w1', runtimes=['r1', 'r2'])])
        tasks = [self.task(), self.task('r3', 'resource_wait', 'w2', 'a2', 't2')]
        self.assertEqual(self.check_gate(tasks, after=after), (True, 'idle'))

    def test_changed_or_invalid_workspace_recheck_fails_closed(self):
        original = [dict(id='w1', runtimes=['r1', 'r2']),
                    dict(id='w2', runtimes=['r3'])]
        for workspaces in (original[:1], original + [original[0]],
                           [original[0], dict(id='w2', runtimes=['other'])],
                           [original[0], dict(runtimes=['r3'])], None):
            with self.subTest(workspaces=workspaces):
                after = dict(status='running', active_task_count=1, running_task_count=1,
                             resource_wait_task_count=0, workspaces=workspaces)
                self.assertEqual(self.check_gate([self.task()], after=after),
                                 (False, 'task_runtime_unknown'))

    def test_non_codex_does_not_bypass_local_codex_process(self):
        self.assertEqual(self.check_gate([self.task()], process='/path/codex'),
                         (False, 'codex_process_present'))

    def test_remote_run_is_not_counted_as_local(self):
        providers = {'r1': 'hermes', 'r2': 'codex', 'remote': 'codex'}
        self.assertEqual(self.check_gate([self.task('remote')], providers=providers, count=0), (True, 'idle'))

    def test_pagination_uses_exact_cli_cursor_and_reads_terminal_history(self):
        cursor = '2026-10-24T06:15:21.348458Z|t1'
        first = SimpleNamespace(returncode=0, stdout=json.dumps([self.task()]),
            stderr=f'More runs available; use --before "{cursor}" to fetch the next page.\n')
        last = SimpleNamespace(returncode=0, stdout=json.dumps([self.task(tid='t2')]), stderr='')
        with patch.object(r, 'command_result', side_effect=[first, last]) as cmd:
            self.assertEqual(len(list(r.task_pages(['multica'], 'a1'))), 2)
            self.assertEqual(cmd.call_args.args[0][-2:], ['--before', cursor])

    def test_bad_pagination_and_schema_fail_closed(self):
        for stdout, stderr in [('{}', ''), ('bad', ''), ('[]', 'unknown pagination'),
                               (json.dumps([dict(id=str(i)) for i in range(200)]), '')]:
            with patch.object(r, 'command_result', return_value=SimpleNamespace(stdout=stdout, stderr=stderr)), \
                 self.assertRaises(r.Unavailable):
                list(r.task_pages(['multica'], 'a1'))
        repeated = SimpleNamespace(stdout=json.dumps([self.task()]),
            stderr='More runs available; use --before "cursor" to fetch the next page.')
        with patch.object(r, 'command_result', return_value=repeated), self.assertRaises(r.Unavailable):
            list(r.task_pages(['multica'], 'a1'))


if __name__ == '__main__':
    unittest.main()
