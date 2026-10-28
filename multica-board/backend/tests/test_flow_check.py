from __future__ import annotations
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_metrics import fixture, NOW, event, iso
from multica_board.flow_check import flow_report, read_cache
from multica_board.__main__ import main


class FlowTests(unittest.TestCase):
    def test_independent_columns_and_approved_threshold(self):
        s = fixture()
        for i in s['issues']:
            i['proxy_exclusion'] = None
        r = flow_report(s, now=NOW)
        self.assertEqual(r['threshold'], 4.5)
        self.assertIn('2026-10-24', r['threshold_source'])
        for kind in ('ledger', 'proxy'):
            self.assertEqual(r[kind]['deliveries_24h'], 1)
            self.assertTrue(r[kind]['below_threshold'])
        s['events'] = [e for e in s['events'] if e['event_type'] != 'delivery_completed']
        r = flow_report(s, now=NOW)
        self.assertIsNone(r['ledger']['deliveries_24h'])
        self.assertEqual(r['ledger']['confirmed_min'], 0)
        self.assertEqual(r['ledger']['unknown_count'], 1)
        self.assertEqual(r['proxy']['deliveries_24h'], 1)

    def test_stale_partial_missing_parent_and_exclusions(self):
        s = fixture()
        s['issues'][0]['proxy_exclusion'] = 'acceptance_or_retest'
        self.assertEqual(flow_report(s, now=NOW)['proxy']['deliveries_24h'], 0)
        for mutate in (lambda x: x.update(collected_at='2020-01-01T00:00:00Z'),
                       lambda x: x.update(complete=False, inventory_complete=False),
                       lambda x: x['issues'].append(dict(x['issues'][0], id='orphan', parent_issue_id='absent'))):
            changed = copy.deepcopy(s)
            mutate(changed)
            r = flow_report(changed, now=NOW)
            self.assertIsNone(r['ledger']['candidates'])
            self.assertIsNone(r['hints']['queued_issues'])

    def test_hints_require_evidence_and_roll_children_up(self):
        s = fixture()
        s['issues'][0]['status'] = 'in_review'
        s['issues'][1]['status'] = 'todo'
        r = flow_report(s, now=NOW)
        self.assertEqual(r['hints']['queued_issues'], 0)
        self.assertIsNone(r['hints']['waiting_approval_issues'])
        self.assertIsNone(r['hints']['reworked_deliveries_24h'])
        s['events'] += [event('wait_open', wait_reason='waiting_user'),
                        event('coverage', 'rw-24', channel='rework', since=iso(NOW.replace(day=23)), until=iso(NOW)),
                        event('rework', 'rw', at='2026-10-24T10:00:00Z', issue='c')]
        s['events'] += [s['events'][-1]]
        r = flow_report(s, now=NOW)
        self.assertEqual(r['hints']['waiting_user_issues'], 1)
        self.assertIsNone(r['hints']['waiting_approval_issues'])
        self.assertEqual(r['hints']['reworked_deliveries_24h'], 1)
        self.assertIsNone(r['hints']['dispatchable_backlog_issues'])

    def test_custom_threshold_boundary_and_invalid_values(self):
        s = fixture()
        cfg = dict(threshold=1, source='test', max_age_hours=3)
        self.assertFalse(flow_report(s, now=NOW, config=cfg)['ledger']['below_threshold'])
        for val in (-1, float('nan'), True):
            with self.assertRaises(ValueError):
                flow_report(s, now=NOW, config=dict(cfg, threshold=val))

    def test_cache_is_readonly_and_not_complete_history(self):
        from multica_board.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'board.sqlite'
            store = Store(p)
            store.close()
            conn = sqlite3.connect(p)
            conn.execute("INSERT INTO workspaces(server,id) VALUES('test','w')")
            conn.execute("INSERT INTO sync_status(server,workspace_id,resource,last_success_at,complete) VALUES('test','w','issues',?,1)", (iso(NOW),))
            conn.execute("INSERT INTO issues(server,workspace_id,id,title,status,created_at) VALUES('test','w','p','Implementation','done',?)", (iso(NOW),))
            conn.commit()
            conn.close()
            before = p.read_bytes()
            s = read_cache(p, 'test', NOW, 3)
            self.assertTrue(s['inventory_complete'])
            self.assertFalse(s['complete'])
            self.assertNotIn('title', s['issues'][0])
            self.assertIsNone(flow_report(s, now=NOW)['proxy']['deliveries_24h'])
            self.assertEqual(before, p.read_bytes())

    def test_default_cli_uses_live_collection_and_never_creates_database(self):
        from contextlib import redirect_stdout
        import io
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'missing.sqlite'
            out = io.StringIO()
            with patch('multica_board.flow_live.collect_recent', return_value=dict(issues=[], events=[], complete=False)), patch('pathlib.Path.home', return_value=Path(tmp)), redirect_stdout(out):
                self.assertEqual(main(['flow-check', '--json', '--db', str(p)]), 0)
            self.assertIsNone(json.loads(out.getvalue())['proxy']['deliveries_24h'])
            self.assertFalse(p.exists())

    def test_reconcile_window_excludes_outside_events(self):
        from contextlib import redirect_stdout
        import io
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp)/'a.json', Path(tmp)/'b.json'
            a.write_text(json.dumps({'events': [event('status', 'before', at='2026-10-20T00:00:00Z'), event('status', 'inside'), event('status', 'invalid', at='invalid')]}))
            b.write_text(json.dumps({'events': [event('status', 'inside')]}))
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(['metrics-reconcile', '--backfill', str(a), '--realtime', str(b),
                                       '--since', '2026-10-23T00:00:00Z', '--until', '2026-10-24T00:00:00Z']), 0)
            r = json.loads(out.getvalue())
            self.assertEqual(r['matched'], 1)
            self.assertEqual(r['backfill_only'], 0)
            self.assertEqual(r['status'], 'unknown')
            self.assertEqual(r['invalid_timestamps']['backfill'], 1)

    def test_reconciliation_does_not_hide_status_difference(self):
        from multica_board.metrics import reconcile
        a = event('status', 'same', status='done', previous_status='in_review')
        b = dict(a, status='in_progress')
        r = reconcile([a], [b])
        self.assertEqual(r['matched'], 0)
        self.assertEqual(r['status'], 'unknown')
        self.assertIn('status', r['differences'][0]['fields'])


class FlowBoundsTests(unittest.TestCase):
    def make(self, confirmed, unknown):
        s = dict(collected_at=iso(NOW), complete=False, inventory_complete=True,
                 issues=[], events=[], history_complete={}, errors=[])
        for n in range(confirmed+unknown):
            iid = f'p{n}'
            s['issues'].append(dict(id=iid, workspace_id='w', status='done',
                                    created_at='2026-10-20T00:00:00Z', proxy_exclusion=None))
            if n < confirmed:
                s['history_complete'][f'w/{iid}'] = True
                s['events'].append(event('status', iid, issue=iid, status='done'))
                s['events'].append(event('delivery_completed', f'd-{iid}', issue=iid, delivery_evidence=f'e-{iid}'))
        return s

    def test_45_confirmed_56_candidates_is_not_below(self):
        r = flow_report(self.make(45, 11), now=NOW)
        for kind in ('ledger', 'proxy'):
            self.assertEqual(r[kind]['confirmed_min'], 45)
            self.assertEqual(r[kind]['candidates'], 56)
            self.assertEqual(r[kind]['unknown_count'], 11)
            self.assertFalse(r[kind]['below_threshold'])
            self.assertIsNone(r[kind]['deliveries_24h'])

    def test_threshold_three_valued_boundary_for_both_columns(self):
        for lo, missing, expected in ((0, 4, True), (4, 0, True), (4, 1, None), (5, 1, False)):
            with self.subTest(lo=lo, missing=missing):
                r = flow_report(self.make(lo, missing), now=NOW)
                for kind in ('ledger', 'proxy'):
                    self.assertIs(r[kind]['below_threshold'], expected)

    def test_partial_inventory_cannot_prove_below_but_lower_bound_proves_not_below(self):
        for n in (0, 5):
            s = self.make(n, 0)
            s['inventory_complete'] = False
            r = flow_report(s, now=NOW)
            for kind in ('ledger', 'proxy'):
                self.assertIsNone(r[kind]['candidates'])
                self.assertIs(r[kind]['below_threshold'], False if n == 5 else None)

    def test_unknown_proxy_kind_and_cancelled_and_old_close(self):
        s = self.make(0, 1)
        s['issues'][0]['proxy_exclusion'] = 'unknown_kind'
        r = flow_report(s, now=NOW)
        self.assertEqual(r['proxy']['candidates'], 1)
        s['issues'][0]['status'] = 'cancelled'
        self.assertEqual(flow_report(s, now=NOW)['proxy']['candidates'], 0)
        s = self.make(0, 1)
        s['issues'][0]['outside_recent_change_window'] = True
        r = flow_report(s, now=NOW)
        self.assertEqual(r['proxy']['candidates'], 0)
        self.assertTrue(r['proxy']['below_threshold'])

    def test_reopened_children_roll_up_and_hint_reasons_are_present(self):
        s = self.make(0, 0)
        s['issues'] = [dict(id='p', workspace_id='w', status='backlog'),
                       dict(id='c', workspace_id='w', status='in_progress', parent_issue_id='p')]
        s['events'] = [event('status', 'reopen', issue='c', status='in_progress', previous_status='done')]*2
        r = flow_report(s, now=NOW)
        self.assertEqual(r['hints']['reworked_24h']['confirmed_min'], 1)
        self.assertEqual(r['hints']['dispatchable_backlog']['candidates'], 1)
        self.assertIsNone(r['hints']['dispatchable_backlog']['value'])
        self.assertTrue(r['hints']['dispatchable_backlog']['reason'])
        self.assertIn('todo', r['hints']['queued_definition'])

    def test_in_progress_status_does_not_prove_no_pending_approvals(self):
        s=self.make(0,0)
        s['issues']=[dict(id='p', workspace_id='w', status='in_progress')]
        r=flow_report(s,now=NOW)
        self.assertIsNone(r['hints']['waiting_approval']['value'])
        self.assertEqual(r['hints']['waiting_approval']['confirmed_min'],0)
        self.assertEqual(r['hints']['waiting_approval']['candidates'],1)

    def test_upper_equal_to_threshold_stays_unknown(self):
        r=flow_report(self.make(0,5), now=NOW, config=dict(threshold=5, max_age_hours=3, source='test'))
        self.assertIsNone(r['proxy']['below_threshold'])
        self.assertIsNone(r['ledger']['below_threshold'])


class FlowLiveTests(unittest.TestCase):
    def test_all_workspaces_page_inventory_only_recent_timeline_and_no_comments_or_runs(self):
        from multica_board.flow_live import collect_recent
        from multica_board.cli_runner import CliResult
        calls=[]
        def row(iid, updated='2026-10-24T10:00:00Z', ws='w'):
            return dict(id=iid, workspace_id=ws, status='done', title='Implementation',
                        updated_at=updated, created_at='2026-10-01T00:00:00Z')
        def run(args, workspace_id=None):
            calls.append((args,workspace_id))
            if args[:2] == ['workspace','list']:
                return CliResult([dict(id='w'),dict(id='v')])
            if args[:2] == ['issue','list']:
                if workspace_id == 'v':
                    return CliResult(dict(issues=[row('same',ws='v')],has_more=False,total=1))
                offset=int(args[args.index('--offset')+1])
                return CliResult(dict(issues=[row('same')] if offset == 0 else [row('old','2026-10-20T00:00:00Z')],
                                      has_more=offset == 0,total=2))
            self.assertEqual(args[:2],['issue','timeline'])
            self.assertEqual(args[2],'same')
            return CliResult([dict(id='done',action='status_changed',created_at='2026-10-24T10:00:00Z',details=dict(to='done',**{'from':'in_review'}))])
        s=collect_recent('owner','multica',NOW,run=run)
        self.assertTrue(s['inventory_complete'])
        self.assertEqual(s['collection']['changed_48h'],2)
        self.assertEqual(s['collection']['timeline_complete'],2)
        self.assertFalse(any('title' in i for i in s['issues']))
        r=flow_report(s,now=NOW)
        self.assertEqual(r['proxy']['confirmed_min'],2)
        self.assertEqual(r['proxy']['candidates'],2)
        self.assertEqual(r['ledger']['candidates'],2)
        self.assertEqual(r['ledger']['confirmed_min'],0)

    def test_failed_or_truncated_timeline_keeps_candidates(self):
        from multica_board.flow_live import collect_recent
        from multica_board.cli_runner import CliResult, CliError
        def run(args, workspace_id=None):
            if args[:2] == ['workspace','list']:
                return CliResult([dict(id='w')])
            if args[:2] == ['issue','list']:
                return CliResult(dict(issues=[dict(id='x',status='done',title='Implement', updated_at=iso(NOW))],has_more=False))
            raise CliError('timeout')
        s=collect_recent('owner','multica',NOW,run=run)
        r=flow_report(s,now=NOW)
        self.assertEqual(r['proxy']['confirmed_min'],0)
        self.assertEqual(r['proxy']['candidates'],1)
        self.assertTrue(r['proxy']['below_threshold'])
        self.assertTrue(s['errors'])

    def test_expired_budget_does_not_start_child(self):
        from multica_board.flow_live import collect_recent
        run=__import__('unittest.mock',fromlist=['Mock']).Mock()
        s=collect_recent('owner','multica',NOW,budget_seconds=0,run=run)
        run.assert_not_called()
        self.assertFalse(s['inventory_complete'])

    def test_truncated_or_foreign_scope_events_never_confirm_completion(self):
        from multica_board.flow_live import collect_recent
        from multica_board.cli_runner import CliResult
        for mismatch in (False, True):
            def run(args, workspace_id=None):
                if args[:2] == ['workspace','list']:
                    return CliResult([dict(id='w')])
                if args[:2] == ['issue','list']:
                    return CliResult(dict(issues=[dict(id='x',status='done',title='Implementation',updated_at=iso(NOW))],has_more=False))
                row=dict(id='s', action='status_changed',created_at='2026-10-24T10:00:00Z',details={'to':'done'})
                if mismatch:
                    row['workspace_id']='foreign'
                return CliResult([row],stderr='' if mismatch else 'truncated')
            s=collect_recent('owner','multica',NOW,run=run)
            self.assertFalse(s['history_complete']['w/x'])
            r=flow_report(s,now=NOW)
            self.assertEqual(r['proxy']['confirmed_min'],0)
            self.assertEqual(r['proxy']['unknown_count'],1)

    def test_profile_runner_uses_outer_retries_and_never_uses_task_mode(self):
        from multica_board.flow_live import collect_recent
        from multica_board.cli_runner import CliResult
        with patch('multica_board.flow_live.CliRunner') as runner:
            runner.return_value.run.return_value=CliResult([])
            collect_recent('read-profile','custom-multica',NOW)
            options=runner.call_args.kwargs
            self.assertEqual(options['profile'],'read-profile')
            self.assertEqual(options['binary'],'custom-multica')
            self.assertEqual(options['retries'],0)
            self.assertLessEqual(options['timeout_s'],20)
            self.assertFalse(options.get('task_scoped',False))


class FlowRetryTests(unittest.TestCase):
    def collect(self, fail, *, budget=110, clock=None):
        from multica_board.flow_live import collect_recent
        from multica_board.cli_runner import CliResult
        self.calls = []
        def run(args, workspace_id=None):
            self.calls.append(args)
            if args[:2] == ['workspace', 'list']:
                return CliResult([dict(id='w')])
            if args[:2] == ['issue', 'list']:
                offset = int(args[args.index('--offset') + 1])
                fail(offset)
                return CliResult(dict(issues=[dict(id=str(offset), status='done', updated_at=iso(NOW))],
                                      has_more=offset == 0, total=2))
            return CliResult([])
        with patch('multica_board.flow_live.time.sleep') as sleep:
            self.sleep = sleep
            return collect_recent('test', 'multica', NOW, run=run, workers=1, budget_seconds=budget)

    def test_second_page_timeout_recovers_before_any_timeline(self):
        from multica_board.cli_runner import CliError
        attempts = []
        def fail(offset):
            attempts.append(offset)
            if attempts == [0, 1]:
                raise CliError('TLS handshake timeout', transient=True)
        s = self.collect(fail)
        self.assertEqual(attempts, [0, 1, 1])
        self.sleep.assert_called_once_with(1.5)
        self.assertTrue(s['inventory_complete'])
        self.assertTrue(s['complete'])
        self.assertTrue(s['errors'][0]['recovered'])
        self.assertEqual([c[:2] for c in self.calls][-2:], [['issue', 'timeline']] * 2)

    def test_retry_exhaustion_keeps_full_redacted_error_and_page(self):
        from multica_board.cli_runner import CliError
        message = 'TLS timeout ' + 'context ' * 40 + 'token=private-value'
        def fail(offset):
            if offset == 1:
                raise CliError(message, transient=True)
        s = self.collect(fail)
        self.assertFalse(s['inventory_complete'])
        self.assertEqual(len(s['issues']), 1)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [1.5, 3])
        error = next(e for e in s['errors'] if e.get('attempt') == 3)
        self.assertEqual(error['offset'], 1)
        self.assertGreater(len(error['error']), 200)
        self.assertNotIn('private-value', json.dumps(s))
        self.assertIn('[redacted]', error['error'])
        self.assertIn('--offset', error['command'])
        self.assertGreaterEqual(error['elapsed_seconds'], 0)

    def test_nontransient_failure_does_not_retry(self):
        from multica_board.cli_runner import CliError
        def fail(offset):
            raise CliError('permission denied')
        self.assertFalse(self.collect(fail)['inventory_complete'])
        self.sleep.assert_not_called()

    def test_retry_backoff_cannot_exceed_remaining_budget(self):
        from multica_board.cli_runner import CliError
        def fail(offset):
            raise CliError('timeout', transient=True)
        s = self.collect(fail, budget=1)
        self.sleep.assert_not_called()
        self.assertFalse(s['inventory_complete'])

    def test_timeline_retry_preserves_unknown_without_degrading_inventory(self):
        from multica_board.flow_live import collect_recent
        from multica_board.cli_runner import CliResult, CliError
        for recover in (False, True):
            calls = []
            def run(args, workspace_id=None):
                if args[:2] == ['workspace', 'list']:
                    return CliResult([dict(id='w')])
                if args[:2] == ['issue', 'list']:
                    return CliResult(dict(issues=[dict(id='x', status='done')], has_more=False))
                calls.append(args)
                if len(calls) == 1 or not recover:
                    raise CliError('TLS failure', transient=True)
                return CliResult([])
            s = collect_recent('test', 'multica', NOW, run=run, workers=1)
            self.assertEqual(len(calls), 2)
            self.assertTrue(s['inventory_complete'])
            self.assertFalse(s['collection']['degraded'])
            self.assertEqual(s['history_complete']['w/x'], recover)
            self.assertEqual(s['complete'], recover)

    def test_runner_classifies_retryable_transport_errors_and_preserves_detail(self):
        import subprocess
        from multica_board.cli_runner import CliRunner, CliError
        for message in ('TLS handshake failed', 'HTTP 500', 'HTTP 599', 'HTTP 429', 'timeout'):
            runner = CliRunner('test', retries=0, _exec=lambda *a, **k:
                               subprocess.CompletedProcess(a, 1, '', message + ' detail' * 50))
            with self.assertRaises(CliError) as caught:
                runner.run(['issue', 'list'])
            self.assertTrue(caught.exception.transient)
            self.assertGreater(len(caught.exception.detail), 200)
            self.assertLessEqual(len(str(caught.exception)), 200)

    def test_attempt_timeout_shrinks_to_shared_deadline(self):
        from multica_board.flow_live import collect_recent
        from multica_board.cli_runner import CliResult, CliError
        clock = [0.0]
        timeouts = []
        def execute(args, workspace_id=None):
            if args[:2] == ['workspace', 'list']:
                return CliResult([dict(id='w')])
            if args[:2] == ['issue', 'list']:
                clock[0] += min(20, 45 - clock[0])
                raise CliError('timeout', transient=True)
            return CliResult([])
        def runner(**options):
            from unittest.mock import Mock
            timeouts.append(options['timeout_s'])
            return Mock(run=execute)
        def sleep(delay):
            clock[0] += delay
        with patch('multica_board.flow_live.time.monotonic', side_effect=lambda: clock[0]), patch(
                'multica_board.flow_live.time.sleep', side_effect=sleep), patch(
                'multica_board.flow_live.CliRunner', side_effect=runner):
            s = collect_recent('test', 'multica', NOW, budget_seconds=45, workers=1)
        self.assertEqual(timeouts, [20, 20, 20, 0.5])
        self.assertEqual(clock[0], 45)
        self.assertFalse(s['inventory_complete'])

    def test_live_cli_saves_a_private_distinct_report_each_round(self):
        from contextlib import redirect_stdout
        import io
        with tempfile.TemporaryDirectory() as tmp:
            with patch('pathlib.Path.home', return_value=Path(tmp)), patch(
                    'multica_board.flow_live.collect_recent', return_value=fixture()), redirect_stdout(io.StringIO()):
                for _ in range(2):
                    self.assertEqual(main(['flow-check', '--now', iso(NOW)]), 0)
            files = list((Path(tmp) / '.multica-board' / 'flow-reports').glob('*.json'))
            self.assertEqual(len(files), 2)
            for path in files:
                saved = json.loads(path.read_text())
                self.assertIn('snapshot', saved)
                self.assertIn('report', saved)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class FlowRunMetricsTests(unittest.TestCase):
    def _report(self):
        ratio = lambda n, d: {"numerator": n, "denominator": d}
        window = {
            "issue_count": 10,
            "run_count": 4,
            "success_rate": {"including_cancelled": ratio(7, 11), "excluding_cancelled": ratio(7, 10)},
            "rework": {
                "rework_rate_claim": "lower_bound_only",
                "rework_rate": None,
                "rework_rate_lower_bound": ratio(2, 10),
            },
            "failure_class_counts": {"quota": 1},
        }
        return {
            "schema_version": "run-metrics.v0.1",
            "generated_at": "2026-10-26T00:00:00Z",
            "default_window": "14d",
            "windows": {"24h": window, "7d": window, "14d": window},
            "scheduling": {},
            "failure_rules": [{"condition": "do not copy this rule"}],
        }

    def test_cli_keeps_flow_alerts_and_attaches_report(self):
        from contextlib import redirect_stdout
        import io
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "run-metrics.v0.1.json"
            report_path.write_text(json.dumps(self._report()), encoding="utf-8")
            before = report_path.read_bytes()
            snapshot = Path(tmp) / "snapshot.json"
            snapshot.write_text(json.dumps(fixture()), encoding="utf-8")
            out = io.StringIO()
            argv = ["flow-check", "--json", "--snapshot", str(snapshot), "--run-metrics", str(report_path), "--now", iso(NOW)]
            with redirect_stdout(out):
                self.assertEqual(main(argv), 0)
            body = json.loads(out.getvalue())
            self.assertEqual(report_path.read_bytes(), before)
            self.assertTrue(body["ledger"]["below_threshold"])
            self.assertEqual(body["ledger"]["deliveries_24h"], 1)
            self.assertEqual(body["run_metrics"]["status"], "ok")
            self.assertEqual(body["run_metrics"]["writes"], [])
            self.assertFalse(body["run_metrics"]["dispatch_changed"])
            self.assertFalse(body["run_metrics"]["alert_delta"]["unified_counts_promoted_to_alerts"])
            self.assertEqual(body["run_metrics"]["alert_delta"]["own_alerts"]["ledger_below_threshold"], True)
            self.assertEqual(body["run_metrics"]["windows"]["14d"]["success_including_cancelled"]["numerator"], 7)
            self.assertNotIn("do not copy", out.getvalue())
            self.assertEqual(body["run_metrics"]["scheduling_fields"]["historical_quota_excludes_agent"], "missing")

    def test_missing_report_does_not_change_threshold_result(self):
        from contextlib import redirect_stdout
        import io
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "snapshot.json"
            snapshot.write_text(json.dumps(fixture()), encoding="utf-8")
            missing = Path(tmp) / "absent.json"
            out = io.StringIO()
            argv = ["flow-check", "--json", "--snapshot", str(snapshot), "--run-metrics", str(missing), "--now", iso(NOW)]
            with redirect_stdout(out):
                self.assertEqual(main(argv), 0)
            body = json.loads(out.getvalue())
            self.assertEqual(body["threshold"], 4.5)
            self.assertTrue(body["ledger"]["below_threshold"])
            self.assertEqual(body["run_metrics"]["status"], "missing")
            self.assertEqual(body["run_metrics"]["windows"], "missing")
