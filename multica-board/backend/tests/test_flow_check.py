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
            with patch('multica_board.flow_live.collect_recent', return_value=dict(issues=[], events=[], complete=False)), redirect_stdout(out):
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

    def test_profile_runner_has_no_retries_and_never_uses_task_mode(self):
        from multica_board.flow_live import collect_recent
        from multica_board.cli_runner import CliResult
        with patch('multica_board.flow_live.CliRunner') as runner:
            runner.return_value.run.return_value=CliResult([])
            collect_recent('read-profile','custom-multica',NOW)
            options=runner.call_args.kwargs
            self.assertEqual(options['profile'],'read-profile')
            self.assertEqual(options['binary'],'custom-multica')
            self.assertEqual(options['retries'],0)
            self.assertLessEqual(options['timeout_s'],12)
            self.assertFalse(options.get('task_scoped',False))
