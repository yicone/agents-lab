"""Real HTTP boundary; only the in-memory fixture adapter can mutate."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.server import BoardServer
from multica_board.write_actions import Journal, RuntimeActions
from test_write_actions import FixtureAdapter, POLICY, WS, AGENT, TOKEN


class WriteHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Journal(Path(self.tmp.name) / 'audit.jsonl')
        self.adapter = FixtureAdapter()
        self.actions = RuntimeActions(self.adapter, POLICY, self.journal)
        self.server = BoardServer('127.0.0.1', 0, SimpleNamespace(), runtime_actions=self.actions)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.host = f'127.0.0.1:{self.server.server_port}'
        self.origin = 'http://' + self.host

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.journal.close()
        self.tmp.cleanup()

    def request(self, op='catalog', data=None, headers=None, raw=None, method='POST', path=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        default = {'Origin': self.origin, 'Content-Type': 'application/json', 'X-Atlas-Write-Token': TOKEN}
        default.update(headers or {})
        default = {k:v for k,v in default.items() if v is not None}
        conn.request(method, path or '/api/runtime-actions/' + op, body=raw if raw is not None else json.dumps(data or {}), headers=default)
        res = conn.getresponse()
        payload = json.loads(res.read())
        conn.close()
        return res.status, payload

    def test_feature_status_matches_enabled_token_mode(self):
        status, payload = self.request(method='GET', path='/api/features')
        self.assertEqual((status, payload), (200, self.actions.features))

    def test_same_origin_confirmed_flow(self):
        status, p = self.request('preview', dict(workspace_id=WS, agent_id=AGENT, changes={'model':'next'}))
        self.assertEqual(status, 200)
        self.assertFalse(self.adapter.writes)
        status, result = self.request('execute', {'id':p['id'], 'confirmation':p['confirmation']})
        self.assertEqual((status, result['state']), (200, 'applied'))
        self.assertEqual(self.request('execute', {'id':p['id'], 'confirmation':p['confirmation']})[0], 409)
        self.assertEqual(len(self.adapter.writes), 1)

    def test_availability_ack_is_checked_by_http_backend(self):
        import time
        from test_model_availability import evidence
        self.adapter.model_availability = lambda *_: evidence('unavailable', now=time.time(), model='next')
        def preview():
            status, p = self.request('preview', dict(workspace_id=WS, agent_id=AGENT, changes={'model': 'next'}))
            self.assertEqual(status, 200)
            self.assertEqual(p['availability']['status'], 'unavailable')
            return p
        p = preview()
        status, error = self.request('execute', {'id': p['id'], 'confirmation': p['confirmation']})
        self.assertEqual(status, 409)
        self.assertEqual(error['error']['code'], 'availability_confirmation_required')
        self.assertFalse(self.adapter.writes)
        p = preview()
        status, result = self.request('execute', {'id': p['id'], 'confirmation': p['confirmation'],
                                                 'availability_confirmation': p['id']})
        self.assertEqual((status, result['state']), (200, 'applied'))

    def test_token_origin_fetch_site_and_content_type(self):
        for headers in ({'Origin':None}, {'Origin':'null'}, {'Origin':'http://elsewhere.invalid'},
                        {'Origin':self.origin + '/path'}, {'X-Atlas-Write-Token':None},
                        {'X-Atlas-Write-Token':'wrong'}, {'Sec-Fetch-Site':'cross-site'},
                        {'Content-Type':'text/plain'}):
            with self.subTest(headers=headers):
                self.assertIn(self.request(headers=headers)[0], (400,403))
        self.assertFalse(self.adapter.writes)

    def test_malformed_duplicate_extra_large_bodies(self):
        for body in ('{"id":1,"id":2}', '{"extra":1}', '[{}]', 'NaN', 'x'*16385):
            with self.subTest(body=body[:20]):
                self.assertEqual(self.request(raw=body)[0], 400)
        self.assertFalse(self.adapter.writes)

    def test_default_read_only_and_unrelated_post_stays_denied(self):
        self.assertEqual(self.request(path='/api/agents')[0], 405)
        self.server.runtime_actions = None
        self.assertEqual(self.request()[0], 405)
        status, payload = self.request(path='/api/runtime-actions', method='GET')
        self.assertEqual((status,payload), (200,{'enabled':False}))

    def test_remote_binding_rejected_with_writer(self):
        with self.assertRaises(ValueError):
            BoardServer('0.0.0.0', 0, SimpleNamespace(), runtime_actions=self.actions)


class TokenOffHttpTests(WriteHttpTests):
    def setUp(self):
        super().setUp()
        policy = dict(POLICY)
        policy.pop('features')
        policy.pop('token_sha256')
        self.actions = RuntimeActions(self.adapter, policy, self.journal)
        self.server.runtime_actions = self.actions

    def request(self, *args, headers=None, **kwargs):
        return super().request(*args, headers={'X-Atlas-Write-Token': None, **(headers or {})}, **kwargs)

    def test_token_origin_fetch_site_and_content_type(self):
        for headers in ({'Origin':None}, {'Origin':'null'}, {'Origin':'http://elsewhere.invalid'},
                        {'Origin':self.origin + '/path'}, {'Sec-Fetch-Site':'cross-site'},
                        {'Content-Type':'text/plain'}):
            with self.subTest(headers=headers):
                self.assertIn(self.request(headers=headers)[0], (400,403))
        self.assertEqual(self.request(headers={'X-Atlas-Write-Token':'ignored'})[0], 200)
        self.assertFalse(self.adapter.writes)

    def test_read_only_features_and_audit_mode(self):
        status, payload = self.request(method='GET', path='/api/features')
        self.assertEqual((status, payload), (200, {'write_token_required': False}))
        self.test_same_origin_confirmed_flow()
        self.assertTrue(self.journal.events)
        self.assertTrue(all(e['auth_mode'] == 'token_off' for e in self.journal.events))
        self.assertEqual(self.adapter.comments[0]['auth_mode'], 'token_off')
        self.server.runtime_actions = None
        self.assertEqual(self.request(method='GET', path='/api/features')[1], {'write_token_required': False})
