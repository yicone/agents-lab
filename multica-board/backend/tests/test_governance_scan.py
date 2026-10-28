import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.governance_scan import inspect, collect, read_cli, load_for_atlas, snapshot_path
from multica_board.store import Store
from multica_board.views import Views

AT = '2026-10-26T12:00:00Z'
DEFS = [
    {'id': 'p', 'name': '主层', 'type': 'select', 'config': {'options': [{'id': 'l1', 'name': 'L1 资源'}]}},
    {'id': 'i', 'name': '涉及层', 'type': 'multi_select', 'config': {'options': [{'id': 'l2', 'name': 'L2 执行'}]}},
    {'id': 'g', 'name': '治理线', 'type': 'checkbox'},
]
PROJECTS = [{'id': 'atlas', 'title': 'Agent Atlas', 'status': 'in_progress', 'workspace_id': 'ws'},
            {'id': 'old', 'title': 'Old 已迁出', 'status': 'completed', 'workspace_id': 'ws'},
            {'id': 'start', 'title': 'Getting Started', 'status': 'completed', 'workspace_id': 'ws'}]


def issue(**kw):
    return dict({'id': 'x', 'workspace_id': 'ws', 'identifier': 'ACM-1', 'title': 'Agent Atlas',
                 'status': 'todo', 'updated_at': AT, 'properties': {}}, **kw)


def scan(row=None, **kw):
    return inspect([row or issue()], PROJECTS, DEFS, workspace_id='ws', detected_at=AT, **kw)


def rules(report):
    return {f['rule'] for f in report['findings']}


class GovernanceScanTests(unittest.TestCase):
    def test_missing_project_and_properties_evidence(self):
        report = scan()
        self.assertEqual(rules(report), {'P1', 'P4'})
        self.assertEqual(report['atlas_decisions'][0]['candidate'][0]['project_id'], 'atlas')
        for finding in report['findings']:
            self.assertTrue(finding['evidence'])
            self.assertEqual(finding['detected_at'], AT)
            self.assertEqual(finding['issue_id'], 'x')
            self.assertIn('whitelist', finding)

    def test_description_only_project_match_is_low_confidence(self):
        report = scan(issue(title='Research', description='Agent Atlas'))
        finding = next(f for f in report['findings'] if f['rule'] == 'P1')
        self.assertEqual(finding['confidence'], 'low')
        self.assertEqual(finding['candidate'][0]['project_id'], 'atlas')

    def test_wrong_project_is_candidate_only(self):
        projects = PROJECTS + [{'id': 'business', 'title': '业务站迁入 Multica', 'status': 'planned'}]
        report = inspect([issue(project_id='atlas', title='业务站迁入 Multica')], projects, DEFS, workspace_id='ws', detected_at=AT)
        finding = next(f for f in report['findings'] if f['rule'] == 'P2')
        self.assertEqual(finding['confidence'], 'medium')
        self.assertNotIn(finding, report['atlas_decisions'])

    def test_completed_migrated_project_and_archive(self):
        self.assertTrue({'P3', 'P9'} <= rules(scan(issue(project_id='old'))))
        self.assertEqual(rules(scan(issue(project_id='old', status='done'))), set())
        self.assertIn('P9', rules(scan(issue(status='done', metadata={'current_execution': True}))))
        self.assertNotIn('P9', rules(scan(issue(status='done', project_id='atlas'))))

    def test_false_is_present_empty_multiselect_is_missing(self):
        self.assertIn('P5', rules(scan(issue(properties={'g': False, 'i': []}))))
        self.assertNotIn('P6', rules(scan(issue(properties={'g': False}))))
        self.assertEqual(rules(scan(issue(project_id='atlas', properties={'p': 'l1', 'i': ['l2'], 'g': False}))), set())

    def test_invalid_enums_types_and_boolean_are_not_coerced(self):
        for props in ({'p': 'L1 invented'}, {'p': ['l1']}, {'i': ['unknown']}, {'i': 'l2'}, {'g': 1}, {'g': 'false'}):
            with self.subTest(props=props):
                self.assertIn('P6', rules(scan(issue(properties=props))))

    def test_outside_value_requires_approved_exception(self):
        report = scan(issue(properties={'p': '不在地图'}))
        self.assertIn('P8', rules(report))
        self.assertNotIn('P5', rules(report))

    def test_keywords_never_grant_exception(self):
        report = scan(issue(title='副调度收件箱'))
        self.assertTrue({'P7', 'P8'} <= rules(report))
        self.assertFalse(report['findings'][0]['whitelist']['valid'])

    def test_approved_exception_expiry_content_and_scope(self):
        row = issue(title='纯运维壳', project_id=None, properties={'g': True})
        entry = {'category': 'operations_shell', 'reason': 'approved shell', 'owner': 'owner',
                 'review_at': '2026-11-01T00:00:00Z', 'allowed_statuses': ['todo'],
                 'allowed_projects': [None], 'content_sha256': hashlib.sha256(b'\xe7\xba\xaf\xe8\xbf\x90\xe7\xbb\xb4\xe5\xa3\xb3\n').hexdigest()}
        self.assertEqual(rules(scan(row, exceptions={'x': entry})), {'P7'})
        for key, value in [('review_at', AT), ('content_sha256', 'changed'), ('allowed_projects', ['atlas']), ('category', 'invented')]:
            self.assertIn('P8', rules(scan(row, exceptions={'x': {**entry, key: value}})))

    def test_malformed_exception_is_reported_not_silently_accepted(self):
        for entry in (42, 'bad', [], {}):
            self.assertIn('P8', rules(scan(exceptions={'x': entry})))

    def test_frozen_getting_started_and_historical_noise(self):
        self.assertEqual(rules(scan(issue(project_id='start'))), set())
        report = scan(issue(status='done'))
        self.assertEqual(report['atlas_decisions'], [])
        self.assertIn('P4', rules(report))

    def test_determinism_no_mutation_and_missing_catalog(self):
        row = issue()
        original = copy.deepcopy(row)
        self.assertEqual(scan(row), scan(row))
        self.assertEqual(row, original)
        with self.assertRaises(ValueError):
            inspect([row], PROJECTS, [], workspace_id='ws')
        with self.assertRaises(ValueError):
            scan(issue(workspace_id='other'))

    @patch('multica_board.governance_scan.read_cli')
    def test_full_pagination_and_duplicate_or_empty_rejection(self, cli):
        cli.side_effect = [{'issues': [issue()], 'has_more': True, 'total': 2},
                           {'issues': [issue(id='y')], 'has_more': False, 'total': 2}, PROJECTS, DEFS]
        self.assertEqual(len(collect('ws')[0]), 2)
        self.assertIn('1', cli.call_args_list[1].args)
        cli.side_effect = [{'issues': [issue()], 'has_more': False, 'total': 2}]
        with self.assertRaises(ValueError): collect('ws')
        cli.side_effect = [{'issues': [], 'has_more': True, 'total': 2}]
        with self.assertRaises(ValueError): collect('ws')
        cli.side_effect = [{'issues': [issue()], 'has_more': True, 'total': 2}, {'issues': [issue()], 'has_more': False, 'total': 2}]
        with self.assertRaises(ValueError): collect('ws')

    @patch('multica_board.governance_scan.subprocess.run')
    def test_transport_only_fixed_list_commands(self, run):
        with self.assertRaises(ValueError): read_cli('update', 'ws')
        run.assert_not_called()
        run.return_value.returncode = 0
        run.return_value.stdout = '[]'
        read_cli('issue', 'ws', '--limit', '100')
        self.assertEqual(run.call_args.args[0][:5], ['multica', '--workspace-id', 'ws', 'issue', 'list'])

    def test_atlas_scope_freshness_corruption_and_update_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'board.db'
            self.assertEqual(load_for_atlas(db, 'profile', 'ws')['state'], 'not_collected')
            report = scan()
            report['source_profile'] = 'profile'
            snapshot_path(db).write_text(json.dumps(report))
            from datetime import datetime
            now = datetime.fromisoformat('2026-10-26T13:00:00+00:00')
            self.assertEqual(load_for_atlas(db, 'profile', 'ws', now)['state'], 'ok')
            self.assertEqual(load_for_atlas(db, 'other', 'ws', now)['state'], 'incompatible')
            self.assertEqual(load_for_atlas(db, 'profile', 'other', now)['state'], 'incompatible')
            later = datetime.fromisoformat('2026-10-28T13:00:00+00:00')
            self.assertEqual(load_for_atlas(db, 'profile', 'ws', later)['state'], 'stale')
            store = Store(db)
            store.set_workspaces('profile', [{'id': 'ws', 'slug': 'acm-lab', 'name': 'ACM Lab'}])
            store.replace_snapshot('profile', 'ws', 'issues', [{k: v for k, v in issue().items() if k != 'properties' and k != 'workspace_id'}])
            views = Views(store, 'profile', app_url='https://multica.ai', collect_interval_s=60)
            with patch('multica_board.governance_scan.datetime') as clock:
                clock.now.return_value = now
                clock.fromisoformat.side_effect = datetime.fromisoformat
                self.assertEqual(len(views.issues()['items'][0]['governance_findings']), 2)
            for field, value in [('detected_at', 42), ('findings', [{}])]:
                snapshot_path(db).write_text(json.dumps({**report, field: value}))
                self.assertEqual(load_for_atlas(db, 'profile', 'ws', now)['state'], 'invalid')
            snapshot_path(db).write_text('{bad')
            self.assertEqual(load_for_atlas(db, 'profile', 'ws')['state'], 'invalid')
            store.close()
