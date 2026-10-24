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
        self.assertEqual(r['ledger']['evidence_status'], 'unknown')
        self.assertEqual(r['ledger']['coverage']['evidenced_deliveries'], 0)
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
            self.assertIsNone(r['ledger']['deliveries_24h'])
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

    def test_cli_missing_cache_unknown_no_network_or_created_database(self):
        from contextlib import redirect_stdout
        import io
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'missing.sqlite'
            out = io.StringIO()
            with patch('multica_board.__main__._runner', side_effect=AssertionError('network')), redirect_stdout(out):
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
