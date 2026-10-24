from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.metrics import build_report, iso, ts, roots, reconcile
from multica_board.metrics_history import collect_history, write_private, TaskHistoryRunner
from multica_board.metrics_cli import load_ledger
from multica_board.cli_runner import CliError, CliResult, CommandNotAllowed
from multica_board.changes import CHANGE_RETENTION_DAYS

NOW = ts('2026-10-24T12:00:00Z')
START = '2026-10-22T12:00:00Z'
END = '2026-10-23T12:00:00Z'


def event(kind, id=None, issue='p', ws='w', at=END, **kw):
    return dict(workspace_id=ws, issue_id=issue, event_id=id or kind, event_type=kind,
                at=at, source='delivery_ledger', confidence='complete', **kw)


def issue(id='p', ws='w', parent=None, status='done'):
    return dict(id=id, workspace_id=ws, parent_issue_id=parent, status=status,
                created_at=START, assignee_id='a')


def fixture():
    events = [event('status', 's1', at=START, status='in_progress', previous_status='todo'),
              event('status', 's2', at='2026-10-22T18:00:00Z', status='in_review', previous_status='in_progress'),
              event('status', 's3', status='done', previous_status='in_review'),
              event('delivery_completed', delivery_evidence='opaque-id')]
    for channel in ['attention', 'rework', 'cost']:
        events.append(event('coverage', channel, channel=channel, since=START, until=END))
    events += [event('approval', 'comment1'), event('user_reply', 'comment2', issue='c'),
               event('cost_observed', related_run_id='r1', cost_usd_ticks=120)]
    return dict(schema_version=1, complete=True, collected_at=iso(NOW), issues=[issue(), issue('c', parent='p')],
                events=events, runs=[dict(workspace_id='w', issue_id='c', id='r1', runtime_id='rt', status='failed')],
                history_complete={'w/p': True, 'w/c': True}, errors=[])


class MetricsTests(unittest.TestCase):
    def report(self, s=None, **kw):
        return build_report(s or fixture(), now=NOW, **kw)

    def test_parent_rollup_and_five_metrics(self):
        r = self.report(threshold=2, wip_floor=2)
        m = r['metrics']
        self.assertEqual(len(r['deliveries']), 1)
        self.assertEqual(m['attention_per_delivery']['value'], 2)
        self.assertEqual(m['cycle_hours']['value'], 24)
        self.assertEqual(m['cycle_hours']['p90'], 24)
        self.assertEqual(m['waiting_share']['value'], .75)
        self.assertEqual(m['waiting_share']['by_reason']['waiting_agent'], .75)
        self.assertEqual(m['first_pass_rate']['value'], 1)
        self.assertEqual(m['multica_cost_per_delivery']['value'], 120)
        self.assertIsNone(m['quota_consumption']['value'])
        self.assertEqual(r['flow']['deliveries_24h'], 1)
        self.assertTrue(r['flow']['triggered'])

    def test_duplicate_comment_run_and_bridge_event_not_double_counted(self):
        s = fixture()
        s['events'] += copy.deepcopy(s['events'])
        s['runs'] *= 2
        r = self.report(s)
        self.assertEqual(r['metrics']['attention_per_delivery']['value'], 2)
        self.assertEqual(r['metrics']['multica_cost_per_delivery']['value'], 120)

    def test_done_without_ledger_never_uses_updated_at(self):
        s = fixture()
        s['events'] = [e for e in s['events'] if e['event_type'] != 'delivery_completed']
        s['issues'][0]['updated_at'] = END
        r = self.report(s)
        self.assertEqual(r['coverage']['done_candidates'], 1)
        self.assertEqual(r['coverage']['completion_evidence'], 0)
        for metric in r['metrics'].values():
            self.assertIsNone(metric['value'])
        self.assertIsNone(r['flow']['deliveries_24h'])
        self.assertTrue(all(d['deliveries'] is None for d in r['daily']))

    def test_absence_of_semantic_events_is_not_zero_or_first_pass(self):
        s = fixture()
        s['events'] = [e for e in s['events'] if e['event_type'] not in {'coverage', 'approval', 'user_reply', 'cost_observed'}]
        r = self.report(s)
        for k in ['attention_per_delivery', 'first_pass_rate', 'multica_cost_per_delivery']:
            self.assertIsNone(r['metrics'][k]['value'])

    def test_cancelled_cost_excluded_from_delivery(self):
        s = fixture()
        s['issues'][0]['status'] = 'cancelled'
        r = self.report(s)
        self.assertEqual(r['coverage']['done_candidates'], 0)
        self.assertIsNone(r['metrics']['multica_cost_per_delivery']['value'])

    def test_all_workspaces_do_not_merge_same_ids(self):
        s = fixture()
        b = copy.deepcopy(s)
        for rows in ['issues', 'runs', 'events']:
            for e in b[rows]: e['workspace_id'] = 'other'
            s[rows] += b[rows]
        s['history_complete'].update({'other/p': True})
        r = self.report(s)
        self.assertEqual(r['coverage']['evidenced_deliveries'], 2)
        self.assertEqual(r['by_workspace'], {'w': 1, 'other': 1})

    def test_missing_cross_workspace_parent_and_cycle(self):
        s = fixture()
        s['issues'] += [issue('orphan', ws='other', parent='p'), issue('loop', parent='loop')]
        self.assertEqual(self.report(s)['coverage']['parent_unknown'], 2)

    def test_quota_wait_and_approval_wait_no_overlap(self):
        s = fixture()
        s['events'] += [event('wait_open', at='2026-10-22T18:00:00Z', wait_reason='waiting_quota'),
                        event('wait_close', at='2026-10-23T00:00:00Z'),
                        event('wait_open', 'approval-wait', at='2026-10-23T00:00:00Z', wait_reason='waiting_user')]
        reasons = self.report(s)['metrics']['waiting_share']['by_reason']
        self.assertEqual(reasons['waiting_quota'], .25)
        self.assertEqual(reasons['waiting_user'], .5)
        self.assertEqual(reasons['waiting_agent'], 0)

    def test_failed_run_does_not_imply_rework_and_explicit_rework_does(self):
        s = fixture()
        s['runs'][0]['automatic_recovery'] = True
        self.assertEqual(self.report(s)['metrics']['first_pass_rate']['value'], 1)
        self.assertEqual(self.report(s)['deliveries'][0]['automatic_recovery_warnings'], 1)
        s['events'].append(event('rework', 'rework-round-1', issue='c'))
        self.assertEqual(self.report(s)['metrics']['first_pass_rate']['value'], 0)

    def test_incomplete_timeline_and_stale_cache(self):
        s = fixture()
        s['history_complete']['w/p'] = False
        self.assertIsNone(self.report(s)['metrics']['waiting_share']['value'])
        s['collected_at'] = iso(NOW - timedelta(days=2))
        r = self.report(s)
        self.assertEqual(r['source_status'], 'unknown')
        self.assertIsNone(r['flow']['wip'])
        self.assertIsNone(r['flow']['triggered'])

    def test_collection_failure_is_never_zero_flow(self):
        s = fixture()
        s['complete'] = False
        s['issues'] = []
        r = self.report(s, threshold=2)
        self.assertIsNone(r['flow']['deliveries_24h'])
        self.assertIsNone(r['flow']['wip'])
        self.assertIsNone(r['flow']['triggered'])

    def test_window_boundaries_and_cross_window(self):
        s = fixture()
        s['issues'][0]['created_at'] = '2026-09-01T00:00:00Z'
        s['events'][0]['at'] = '2026-09-01T00:00:00Z'
        r = self.report(s)
        self.assertTrue(r['deliveries'][0]['cross_window'])
        # Half-open right boundary excludes completions at now.
        for e in s['events']:
            if e['event_type'] == 'delivery_completed': e['at'] = iso(NOW)
        self.assertEqual(self.report(s)['coverage']['evidenced_deliveries'], 0)

    def test_conflicting_evidence_not_last_write_wins(self):
        s = fixture()
        e = copy.deepcopy(s['events'][-1]); e['cost_usd_ticks'] = 999
        s['events'].append(e)
        r = self.report(s)
        self.assertEqual(r['coverage']['event_conflicts'], 1)
        self.assertIsNone(r['metrics']['multica_cost_per_delivery']['value'])

    def test_thresholds_are_optional_parameters(self):
        self.assertIsNone(self.report()['flow']['triggered'])
        with self.assertRaises(ValueError): self.report(threshold=float('nan'))

    def test_9router_observability_off_not_estimated(self):
        s = fixture(); s['unavailable'] = ['9router_observability_disabled']
        r = self.report(s)
        self.assertIsNone(r['metrics']['quota_consumption']['value'])

    def test_reconcile_exposes_time_and_semantic_differences(self):
        a = fixture()['events'][:2]; b = copy.deepcopy(a)
        b[0]['at'] = END; b[1]['wait_reason'] = 'waiting_quota'
        r = reconcile(a, b)
        self.assertEqual(len(r['differences']), 2)
        self.assertEqual(r['status'], 'unknown')
        self.assertEqual(reconcile(a, a)['matched'], 2)


class TransportTests(unittest.TestCase):
    def test_task_runner_read_only(self):
        runner = TaskHistoryRunner()
        with self.assertRaises(CommandNotAllowed): runner.run(['issue', 'status', 'x', 'done'])
        with self.assertRaises(CommandNotAllowed): runner.run(['issue', 'comment', 'add', 'x'])

    def test_snapshot_private_and_no_body_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'snapshot.json'; write_private(p, {'events': []})
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_ledger_rejects_body_as_evidence_and_discards_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'ledger.json'
            e = event('delivery_completed', delivery_evidence='contains a secret')
            p.write_text(json.dumps([e]))
            with self.assertRaises(ValueError): load_ledger(p)
            e['delivery_evidence'] = 'opaque'; e['content'] = 'never store me'
            p.write_text(json.dumps([e])); self.assertNotIn('content', load_ledger(p)[0])

    def test_retention_matches_decision(self):
        self.assertEqual(CHANGE_RETENTION_DAYS, 90)

class HistoryAdapterTests(unittest.TestCase):
    def runner(self, *, truncated=False, mismatch=False, comments_fail=False):
        runner = Mock(profile='test')
        def run(args, workspace_id=None):
            if args[:2] == ['workspace', 'list']:
                return CliResult([{'id': 'w'}])
            if args[:2] == ['issue', 'list']:
                return CliResult({'issues': [dict(issue(), title='private-title', description='secret-body')], 'total': 1, 'has_more': False})
            if args[:2] == ['agent', 'list']:
                return CliResult([])
            if args[:2] == ['issue', 'timeline']:
                return CliResult([{'id': 't1', 'action': 'status_changed', 'created_at': END,
                                   'workspace_id': 'wrong' if mismatch else 'w',
                                   'details': {'from': 'in_review', 'to': 'done'}, 'actor_name': 'private-name'}],
                                  'warning: truncated' if truncated else '')
            if args[:3] == ['issue', 'comment', 'list']:
                if comments_fail: raise CliError('network')
                return CliResult([{'id': 'c1', 'type': 'comment', 'author_type': 'member',
                                   'created_at': END, 'content': 'secret body', 'email': 'secret@example.test'}])
            raise AssertionError(args)
        runner.run.side_effect = run
        return runner

    def test_real_adapter_discards_bodies_and_does_not_infer_completion(self):
        s = collect_history(self.runner(), now=NOW)
        self.assertTrue(s['complete'])
        encoded = json.dumps(s)
        for secret in ['private-title', 'private-name', 'secret body', 'secret@example.test', 'secret-body']:
            self.assertNotIn(secret, encoded)
        self.assertEqual(s['events'][1]['event_type'], 'attention_unclassified')
        r = build_report(s, now=NOW)
        self.assertIsNone(r['flow']['deliveries_24h'])
        self.assertEqual(r['coverage']['done_candidates'], 1)

    def test_truncation_scope_mismatch_and_failure_explicit(self):
        for kwargs in [{'truncated': True}, {'mismatch': True}, {'comments_fail': True}]:
            with self.subTest(**kwargs):
                s = collect_history(self.runner(**kwargs), now=NOW)
                self.assertFalse(s['complete'])
                self.assertTrue(s['errors'])
                r = build_report(s, now=NOW)
                self.assertIsNone(r['flow']['deliveries_24h'])

    def test_curated_classification_enriches_same_comment_id(self):
        s = fixture()
        observed = event('attention_unclassified', 'real-comment')
        observed['source'] = 'multica_comments'; observed['confidence'] = 'partial'
        s['events'].append(observed)
        classified = event('approval', 'real-comment')
        r = build_report(s, [classified], now=NOW)
        self.assertEqual(r['coverage']['event_conflicts'], 0)
        self.assertEqual(r['metrics']['attention_per_delivery']['value'], 3)

    def test_live_wait_diagnosis_even_when_deliveries_unknown(self):
        s = fixture(); s['issues'][0]['status'] = 'in_review'
        s['events'] = [e for e in s['events'] if not (e.get('event_type') == 'status' and e.get('status') == 'done')]
        r = build_report(s, now=NOW)
        self.assertEqual(r['flow']['wip'], 1)
        self.assertEqual(r['flow']['current_waiting']['waiting_agent'], 1)
        self.assertIn('review_agent_capacity', r['flow']['suggested_actions'])

class BoundaryTests(unittest.TestCase):
    def test_child_state_and_wait_do_not_change_parent_clock(self):
        s = fixture()
        s['events'] += [event('status', 'child-start', issue='c', at='2026-10-20T00:00:00Z', status='in_progress'),
                        event('status', 'child-blocked', issue='c', at='2026-10-22T15:00:00Z', status='blocked'),
                        event('wait_open', 'child-wait', issue='c', at='2026-10-22T19:00:00Z', wait_reason='waiting_quota')]
        r = build_report(s, now=NOW)
        self.assertEqual(r['metrics']['cycle_hours']['value'], 24)
        self.assertEqual(r['metrics']['waiting_share']['by_reason']['waiting_agent'], .75)
        self.assertEqual(r['metrics']['waiting_share']['by_reason']['waiting_quota'], 0)

    def test_child_completion_does_not_complete_parent(self):
        s = fixture()
        for e in s['events']:
            if e['event_type'] == 'delivery_completed': e['issue_id'] = 'c'
        self.assertIsNone(build_report(s, now=NOW)['metrics']['cycle_hours']['value'])

    def test_unclassified_user_comment_cannot_be_hidden_by_coverage(self):
        s = fixture(); s['events'].append(event('attention_unclassified', 'unclassified'))
        r = build_report(s, now=NOW)
        self.assertIsNone(r['metrics']['attention_per_delivery']['value'])
        self.assertIsNone(r['metrics']['first_pass_rate']['value'])

    def test_repeated_cost_for_same_run_counted_once(self):
        s = fixture()
        s['events'].append(event('cost_observed', 'cost-copy', related_run_id='r1', cost_usd_ticks=120))
        self.assertEqual(build_report(s, now=NOW)['metrics']['multica_cost_per_delivery']['value'], 120)

    def test_resume_uses_successful_reads(self):
        helper = HistoryAdapterTests()
        runner = helper.runner()
        previous = collect_history(runner, now=NOW)
        runner.run.reset_mock()
        recovered = collect_history(runner, now=NOW, previous=previous)
        self.assertTrue(recovered['complete'])
        self.assertEqual(recovered['events'], previous['events'])
        self.assertEqual(runner.run.call_count, 1)  # revalidate visible scope only

class RetentionTests(unittest.TestCase):
    def test_local_event_retention_is_90_days_and_loss_is_explicit(self):
        from multica_board.metrics_history import retain_events
        s = fixture(); s['comments_complete'] = {'w/p': True}
        s['events'].append(event('status', 'ancient', at=iso(NOW - timedelta(days=91)), status='todo'))
        retain_events(s)
        self.assertEqual(s['retention_days'], 90)
        self.assertFalse(s['history_complete']['w/p'])
        self.assertIn('w/p', s['retention_gap_issues'])
        self.assertNotIn('ancient', [e['event_id'] for e in s['events']])

    def test_p90_uses_nearest_rank_and_waiting_uses_duration(self):
        s = fixture()
        b = copy.deepcopy(s)
        for rows in ['issues', 'runs', 'events']:
            for e in b[rows]: e['workspace_id'] = 'other'
            s[rows] += b[rows]
        for e in s['events']:
            if e['workspace_id']=='other' and e.get('event_id')=='s1': e['at']='2026-10-21T12:00:00Z'
        s['history_complete']['other/p']=True
        r=build_report(s,now=NOW)
        self.assertEqual(r['metrics']['cycle_hours']['value'],36)
        self.assertEqual(r['metrics']['cycle_hours']['p90'],48)
        self.assertEqual(r['metrics']['waiting_share']['value'],.5)

class CommandTests(unittest.TestCase):
    def test_local_command_json_markdown_and_reconcile(self):
        from multica_board.__main__ import main
        from contextlib import redirect_stdout
        from io import StringIO
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'snapshot.json'; p.write_text(json.dumps(fixture()))
            for fmt in ['json','markdown']:
                out=StringIO()
                with redirect_stdout(out):
                    rc=main(['metrics','--snapshot',str(p),'--now',iso(NOW),'--format',fmt])
                self.assertEqual(rc,0)
                if fmt=='json': self.assertEqual(json.loads(out.getvalue())['metrics']['cycle_hours']['value'],24)
                else: self.assertIn('每日交付',out.getvalue())
            out=StringIO()
            with redirect_stdout(out): rc=main(['metrics-reconcile','--backfill',str(p),'--realtime',str(p)])
            self.assertEqual(rc,0)
            self.assertEqual(json.loads(out.getvalue())['matched'],len(fixture()['events']))

class MalformedResponseTests(unittest.TestCase):
    def test_malformed_inventory_does_not_become_empty_success(self):
        from multica_board.metrics_history import CheckedHistoryReads
        runner=Mock()
        for response in [None, [], {}, {'issues': [], 'has_more': True}]:
            runner.run.return_value=CliResult(response)
            with self.assertRaises(CliError): CheckedHistoryReads(runner).run(['issue','list'])
        for args in [['agent','list'],['agent','tasks','a']]:
            runner.run.return_value=CliResult(None)
            with self.assertRaises(CliError): CheckedHistoryReads(runner).run(args)


class ProxyTests(unittest.TestCase):
    def snapshot(self):
        s = fixture()
        for i in s['issues']:
            i['proxy_exclusion'] = None
        return s

    def test_proxy_and_ledger_are_independent(self):
        s = self.snapshot()
        s['events'] = [e for e in s['events'] if e['event_type'] != 'delivery_completed']
        r = build_report(s, now=NOW)
        self.assertEqual(r['coverage']['evidenced_deliveries'], 0)
        self.assertEqual(r['proxy']['coverage']['evidenced_deliveries'], 1)
        self.assertEqual(r['proxy']['metrics']['cycle_hours']['value'], 24)
        self.assertEqual(r['proxy']['deliveries'][0]['evidence'], 'proxy')
        self.assertEqual(r['proxy']['flow']['example_70_percent']['threshold'], .7 / 28)
        self.assertEqual(r['proxy']['flow']['deliveries_24h'], 1)
        self.assertEqual(r['workspace_flow']['w']['proxy']['coverage']['completion_evidence'], 1)

    def test_proxy_excludes_cancelled_child_acceptance_and_operations(self):
        for reason in ['acceptance_or_retest', 'push_merge_deploy_only']:
            s = self.snapshot(); s['issues'][0]['proxy_exclusion'] = reason
            self.assertEqual(build_report(s, now=NOW)['proxy']['coverage']['evidenced_deliveries'], 0)
        s = self.snapshot(); s['issues'][0]['status'] = 'cancelled'
        self.assertEqual(build_report(s, now=NOW)['proxy']['coverage']['evidenced_deliveries'], 0)
        s = self.snapshot()
        s['events'].append(event('status', 'child-done', issue='c', status='done'))
        self.assertEqual(build_report(s, now=NOW)['proxy']['coverage']['evidenced_deliveries'], 1)

    def test_proxy_reclose_once_at_last_done(self):
        s = self.snapshot()
        s['events'] += [event('status', 'reopen', at='2026-10-23T15:00:00Z', status='in_progress'),
                        event('status', 'reclose', at='2026-10-24T00:00:00Z', status='done')]
        r = build_report(s, now=NOW)['proxy']
        self.assertEqual(r['coverage']['evidenced_deliveries'], 1)
        self.assertEqual(r['deliveries'][0]['completed_at'], '2026-10-24T00:00:00Z')
        self.assertEqual(sum(d['observed_deliveries'] for d in r['daily']), 1)

    def test_proxy_missing_kind_timeline_or_workspace_is_unknown(self):
        for mode in ['kind', 'timeline', 'workspace', 'stale']:
            s = self.snapshot()
            if mode == 'kind': del s['issues'][0]['proxy_exclusion']
            if mode == 'timeline': s['history_complete']['w/p'] = False
            if mode == 'workspace':
                s['complete'] = False
                s['errors'] = [dict(workspace_id='w', resource='issues', reason='permission_denied')]
            if mode == 'stale': s['collected_at'] = iso(NOW - timedelta(days=2))
            r = build_report(s, now=NOW)['proxy']
            self.assertIsNone(r['flow']['deliveries_24h'], mode)
            self.assertIsNone(r['flow']['example_70_percent']['triggered'], mode)

    def test_kind_rules_are_minimized_and_feature_work_is_preserved(self):
        from multica_board.metrics_history import proxy_exclusion
        for title in ['独立验收 Atlas', '复验：ACM-1', 'Acceptance check', 'Retest', '推送 PR #12',
                      'Atlas：合并 PR', '部署 Atlas', 'Deploy atlas', '纯推送/合并/部署']:
            self.assertIsNotNone(proxy_exclusion(title), title)
        for title in ['修复 CLI 读取', '实现指标并部署', 'Atlas：实现权限读取', '修复 ACM-429 验收阻断', '实现验收标准']:
            self.assertIsNone(proxy_exclusion(title), title)
        self.assertEqual(proxy_exclusion(None), 'unknown_kind')

    def test_profile_environment_removes_agent_identity_without_config_changes(self):
        from unittest.mock import patch
        from multica_board.cli_runner import CliRunner
        with patch.dict(os.environ, {'MULTICA_AGENT_ID':'agent', 'MULTICA_AGENT_NAME':'name',
                                    'MULTICA_DAEMON_PORT':'123', 'MULTICA_TASK_CONFIG_ROOT':'private',
                                    'MULTICA_TOKEN':'secret', 'MULTICA_SERVER_URL':'private'}):
            env = CliRunner('desktop-api.multica.ai')._env()
        self.assertFalse(any(k.startswith('MULTICA_TASK_') for k in env))
        for key in ['MULTICA_AGENT_ID','MULTICA_AGENT_NAME','MULTICA_DAEMON_PORT','MULTICA_TOKEN','MULTICA_SERVER_URL']:
            self.assertNotIn(key, env)


class ProxyBoundaryTests(unittest.TestCase):
    def test_conflicting_status_time_cannot_establish_proxy_close(self):
        s = ProxyTests().snapshot()
        s['events'].append(event('status', 'conflict-status', status='in_progress'))
        r = build_report(s, now=NOW)['proxy']
        self.assertEqual(r['coverage']['evidenced_deliveries'], 0)
        self.assertIsNone(r['flow']['deliveries_24h'])

    def test_adapter_saves_kind_only_not_title(self):
        s = collect_history(HistoryAdapterTests().runner(), now=NOW)
        self.assertIn('proxy_exclusion', s['issues'][0])
        self.assertNotIn('title', s['issues'][0])

    def test_one_denied_workspace_does_not_hide_healthy_workspace(self):
        s = ProxyTests().snapshot()
        s['workspace_ids'] = ['w','denied']
        s['complete'] = False
        s['inventory_complete'] = False
        s['errors'] = [dict(workspace_id='denied',resource='issues',reason='permission_denied')]
        r = build_report(s, now=NOW)
        self.assertIsNone(r['proxy']['flow']['deliveries_24h'])
        self.assertEqual(r['workspace_flow']['w']['proxy']['flow']['deliveries_24h'],1)
        self.assertIsNone(r['workspace_flow']['denied']['proxy']['flow']['deliveries_24h'])


class ProxyRetentionTests(unittest.TestCase):
    def test_old_close_stays_outside_window_after_retention(self):
        from multica_board.metrics_history import retain_events
        s=ProxyTests().snapshot(); s['comments_complete']={}
        for e in s['events']:
            if e['event_type']=='status': e['at']=iso(NOW-timedelta(days=100))
        s['events']=[e for e in s['events'] if e['event_type']!='delivery_completed']
        # Single unambiguous ancient close, no current-window lifecycle.
        s['events']=[e for e in s['events'] if e['event_type']!='status' or e.get('status')=='done']
        retain_events(s)
        self.assertFalse(s['history_complete']['w/p'])
        self.assertEqual(s['issues'][0]['proxy_completion_source'],'complete_timeline')
        report=build_report(s,now=NOW)
        self.assertEqual(report['coverage']['done_candidates'],0)
        r=report['proxy']
        self.assertEqual(r['coverage']['done_candidates'],0)
        self.assertEqual(r['flow']['deliveries_24h'],0)

    def test_recent_close_with_retained_start_gap_has_unknown_cycle(self):
        from multica_board.metrics_history import retain_events
        s=ProxyTests().snapshot();s['comments_complete']={}
        s['events'][0]['at']=iso(NOW-timedelta(days=100))
        retain_events(s)
        r=build_report(s,now=NOW)['proxy']
        self.assertEqual(r['coverage']['evidenced_deliveries'],1)
        self.assertEqual(r['flow']['deliveries_24h'],1)
        self.assertIsNone(r['metrics']['cycle_hours']['value'])
