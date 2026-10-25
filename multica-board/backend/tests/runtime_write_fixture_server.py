"""Isolated UI fixture; its adapter writes only Python dictionaries, never CLI."""
import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.server import BoardServer
from multica_board.write_actions import Journal, RuntimeActions
from test_write_actions import FixtureAdapter, POLICY, WS, AGENT, RT, TOKEN

WEB = Path(__file__).resolve().parents[2] / 'web'

def fixture(name):
    path = WEB / 'fixtures' / (name + '.json')
    return json.loads(path.read_text()) if path.exists() else {'items':[]}

class FixtureViews:
    def __init__(self, adapter):
        self.adapter = adapter
    def meta(self):
        payload = fixture('meta')
        payload['workspaces'] = [dict(payload['workspaces'][0], id=WS, name='Fixture Workspace', slug='fixture')]
        return payload
    def agents(self):
        row = fixture('agents')['items'][0]
        row.update(id=AGENT, workspace_id=WS, name='Fixture Builder', runtime_id=RT, status='idle', tasks={'running':0,'queued':0})
        row.update(copy.deepcopy(self.adapter.values))
        return {'items':[row], 'recent_window_h':24}
    def runtimes(self):
        row = fixture('runtimes')['items'][0]
        row.update(id=RT, workspace_id=WS, name='Fixture Runtime', status='online', provider='claude')
        return {'items':[row]}
    def issues(self): return {'items':[]}
    def bots(self): return {'items':[]}
    def usage(self): return fixture('usage')
    def skills(self,*_): return fixture('skills')
    def layers(self): return fixture('layers')
    def mcp(self): return fixture('mcp')
    def changes(self,*_): return fixture('changes')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8798)
    parser.add_argument('--scenario', choices=['normal','unknown','empty','disabled','available','unavailable'], default='normal')
    args = parser.parse_args()
    adapter = FixtureAdapter()
    if args.scenario in ('available', 'unavailable'):
        from time import time
        adapter.model_availability = lambda target, after: dict(
            workspace_id=target[0], runtime_id=after['runtime_id'],
            model=after['custom_model'] or after['model'], status=args.scenario,
            source='account_model_list', account_bound=True, complete=True,
            context_revision='fixture-account-v1', observed_at=time())
    adapter.mismatch = args.scenario == 'unknown'
    policy = copy.deepcopy(POLICY)
    if args.scenario == 'empty': policy['targets'] = []
    with tempfile.TemporaryDirectory(prefix='atlas-write-fixture-') as folder:
        journal = Journal(Path(folder) / 'audit.jsonl')
        actions = RuntimeActions(adapter, policy, journal)
        server = BoardServer('127.0.0.1', args.port, FixtureViews(adapter), static_dir=WEB,
                             runtime_actions=None if args.scenario == 'disabled' else actions)
        print(f'Fixture ready http://127.0.0.1:{server.server_port}/#/configure/runtime', flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally:
            server.server_close()
            journal.close()
