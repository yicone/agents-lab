from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.collector import norm_task, Collector, CollectOptions
from multica_board.cli_runner import CliRunner
from multica_board.store import Store
from multica_board.views import Views
from fake_cli import FakeMultica, issue, task

MESSAGE = ('[Error] An error occurred while processing your request. You can retry your request, '
           'or contact us through our help center at help.openai.com if the error persists. '
           'Please include the request ID 3d6d2ea5-cff1-47b1-a793-4ffaea0d0c47 in your message.')


class DeliveryAnomalyTests(unittest.TestCase):
    def raw(self, **kw):
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        return task('w', 'run', 'a', now, issue_id='w-issue-0001', result={'output': '\n\n'+MESSAGE}, **kw)

    def test_only_final_completed_exact_upstream_error(self):
        row = norm_task(self.raw())
        self.assertEqual(row['delivery_anomaly'], 'upstream_error')
        self.assertEqual(row['request_id'], '3d6d2ea5-cff1-47b1-a793-4ffaea0d0c47')
        self.assertEqual(row['model'], 'gpt-x')
        self.assertEqual(row['status'], 'completed')
        self.assertIsNone(row['error_kind'])
        self.assertNotIn('result', row)
        for output in (None, {}, '', 'Quoted: '+MESSAGE, '> '+MESSAGE, '```\n'+MESSAGE+'\n```',
                       MESSAGE+'\nThis is a quoted example.', '[Error] Example discussion',
                       'Successfully handled '+MESSAGE):
            raw = self.raw(); raw['result'] = {'output': output}
            self.assertIsNone(norm_task(raw)['delivery_anomaly'], repr(output))
        for status in ('failed', 'running', 'queued', 'cancelled'):
            self.assertIsNone(norm_task(self.raw(status=status))['delivery_anomaly'])
        raw = self.raw(); raw['result'] = None
        self.assertIsNone(norm_task(raw)['delivery_anomaly'])
        self.assertIsNone(norm_task(self.raw(error='quota 429'))['error_kind'])
        raw = self.raw(); raw['usage'] = None
        self.assertIsNone(norm_task(raw)['model'])
        raw['usage'] = [{'model': 'm1'}, {'model': 'm2'}]
        self.assertIsNone(norm_task(raw)['model'])

    def test_acm401_real_final_outputs(self):
        rows = json.loads((Path(__file__).parent / 'fixtures/acm409_completed_errors.json').read_text())
        self.assertEqual(len(rows), 28)
        self.assertEqual(len({r['id'] for r in rows}), 28)
        expected_models = 0
        for raw in rows:
            row = norm_task(raw)
            self.assertEqual(row['delivery_anomaly'], 'upstream_error', raw['id'])
            self.assertIsNone(row['error_kind'])
            self.assertEqual(row['status'], 'completed')
            self.assertTrue(row['request_id'])
            expected_models += bool(row['model'])
        self.assertEqual(expected_models, 20)

    def test_cache_views_keep_failed_counts_separate_and_evidence(self):
        store = Store(':memory:')
        v = Views(store, 's', app_url='https://app.test', collect_interval_s=60)
        store.upsert_many('s', 'w', 'issues', [dict(issue('w', 1), status='done', status_category='done')])
        store.upsert_many('s', 'w', 'agents', [{'id':'a','name':'Builder'}])
        store.upsert_many('s', 'w', 'workspaces', [{'id':'w','name':'Test'}])
        rows = [norm_task(self.raw()), norm_task(self.raw(status='failed', error='HTTP 429 usage limit'))]
        rows[1]['id'] = 'failed'
        store.upsert_tasks('s', 'w', rows)
        stats = v.failure_stats()
        self.assertEqual(stats['delivery_anomalies']['counts'], {'h24':1,'d7':1})
        self.assertEqual(stats['by_provider'][0]['failed'], {'h24':1,'d7':1})
        evidence = stats['delivery_anomalies']['runs'][0]
        for key in ('task_id', 'issue_id', 'agent_id', 'runtime_id', 'model', 'request_id', 'completed_at'):
            self.assertTrue(evidence[key], key)
        att = v.attention()['workspaces'][0]
        self.assertEqual(len(att['failed_runs']), 1)
        self.assertEqual(len(att['delivery_anomalies']), 1)
        self.assertIn('delivery_anomalies 1', v.attention_text())
        it = v.issues()['items'][0]
        self.assertEqual(it['last_delivery_anomaly']['task_id'], 'run')
        self.assertEqual(v.agents()['items'][0]['recent']['delivery_anomalies'], 1)
        self.assertEqual(v.agents()['items'][0]['recent']['failed'], 1)
        self.assertEqual(v.issue_runs('w','w-issue-0001')['items'][0]['model'], 'gpt-x')
        # Recollection replaces, rather than accumulating stale evidence.
        raw = self.raw(); raw['result'] = {'output':'Recovered normally'}
        store.upsert_tasks('s', 'w', [norm_task(raw)])
        self.assertEqual(v.failure_stats()['delivery_anomalies']['counts']['d7'], 0)
        store.close()

    def test_collector_rechecks_legacy_rows_across_pages(self):
        store = Store(':memory:')
        rows = [self.raw() for _ in range(201)]
        for i, raw in enumerate(rows):
            raw['id'] = f'run-{i:03d}'
            old = norm_task(raw)
            old.pop('delivery_checked')
            store.upsert_tasks('s', 'w', [old])
        fake = FakeMultica({'w': {'tasks': {'a': rows}}})
        collector = Collector(CliRunner(profile='s', _exec=fake, retries=1), store, 's', CollectOptions())
        count, complete = collector._agent_tasks('w', 'a', '2020-01-01T00:00:00Z')
        self.assertEqual((count, complete), (201, True))
        self.assertEqual(len(store.known_task_ids('s', 'w', 'a')), 201)
        store.close()

    def test_legacy_cache_is_not_incremental_stop_until_inspected(self):
        store = Store(':memory:')
        old = norm_task(self.raw()); old.pop('delivery_checked')
        store.upsert_tasks('s', 'w', [old])
        self.assertNotIn('run', store.known_task_ids('s','w','a'))
        store.upsert_tasks('s', 'w', [norm_task(self.raw())])
        self.assertEqual(store.known_task_ids('s','w','a'), {'run':'completed'})
        store.close()


if __name__ == '__main__':
    unittest.main()
