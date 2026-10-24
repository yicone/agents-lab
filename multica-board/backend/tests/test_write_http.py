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

    def test_same_origin_confirmed_flow(self):
        status, p = self.request('preview', dict(workspace_id=WS, agent_id=AGENT, changes={'model':'next'}))
        self.assertEqual(status, 200)
        self.assertFalse(self.adapter.writes)
        status, result = self.request('execute', {'id':p['id'], 'confirmation':p['confirmation']})
        self.assertEqual((status, result['state']), (200, 'applied'))
        self.assertEqual(self.request('execute', {'id':p['id'], 'confirmation':p['confirmation']})[0], 409)
        self.assertEqual(len(self.adapter.writes), 1)

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
