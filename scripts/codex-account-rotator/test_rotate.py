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
    def test_idle_requires_known_zero_counts_and_no_codex_processes(self):
        status = dict(status='running', active_task_count=0, running_task_count=0, resource_wait_task_count=0)
        with patch.object(r, 'read_json', return_value=status), patch.object(r, 'command', return_value='/usr/bin/init\n/Applications/CodexBar'):
            self.assertTrue(r.idle('multica')[0])
        for proc in ['/path/codex', '/path/codex-acp', '/path/Codex (Renderer)']:
            with patch.object(r, 'read_json', return_value=status), patch.object(r, 'command', return_value=proc):
                self.assertFalse(r.idle('multica')[0])
        with patch.object(r, 'read_json', return_value=dict(status, active_task_count=1)):
            self.assertFalse(r.idle('multica')[0])
        for bad in [dict(status='running'), dict(status, running_task_count=None)]:
            with patch.object(r, 'read_json', return_value=bad), self.assertRaises(r.Unavailable):
                r.idle('multica')

    def test_profile_is_forwarded_without_shell_interpolation(self):
        status = dict(status='running', active_task_count=0,
                      running_task_count=0, resource_wait_task_count=0)
        with patch.object(r, 'read_json', return_value=status) as read, \
             patch.object(r, 'command', return_value='/usr/bin/init'):
            self.assertTrue(r.idle('multica', 'desktop-api.multica.ai')[0])
            read.assert_called_once_with(['multica', '--profile', 'desktop-api.multica.ai',
                                          'daemon', 'status', '--output', 'json'])

    def test_unclassified_active_tasks_fail_closed(self):
        for key in ('active_task_count', 'running_task_count', 'resource_wait_task_count'):
            status = dict(status='running', active_task_count=0,
                          running_task_count=0, resource_wait_task_count=0)
            status[key] = 1
            with patch.object(r, 'read_json', return_value=status), \
                 patch.object(r, 'command') as command:
                self.assertEqual(r.idle('multica'), (False, 'task_runtime_unknown'))
                command.assert_not_called()

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


if __name__ == '__main__':
    unittest.main()
