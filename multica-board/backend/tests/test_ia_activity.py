"""Read-only IA activity projections use workspace-scoped identities."""
import unittest
import tempfile
from pathlib import Path
from test_board import make, _small_board, SERVER, WS_A, ago
from fake_cli import FakeMultica, task
from multica_board.views import Views, _mcp_own_state

class ActivityTests(unittest.TestCase):
 def test_current_tasks_latest_and_binding_unknown(self):
  with tempfile.TemporaryDirectory() as tmp:
   data=_small_board()
   # Latest activity is deliberately older than the statistics window.
   data[WS_A]['tasks']['ag1'][0]['created_at']=ago(50)
   data[WS_A]['tasks']['ag1'][1]['created_at']=ago(60)
   store,col=make(FakeMultica(data),Path(tmp)/'board.sqlite')
   try:
    self.assertTrue(col.collect()['ok'])
    view=Views(store,SERVER,app_url='https://app.test',collect_interval_s=60)
    row=view.agents()['items'][0]
    self.assertEqual(row['latest_run']['status'],'running')
    self.assertEqual(len(row['current_tasks']),1)
    self.assertEqual(row['current_tasks'][0]['identifier'],data[WS_A]['issues'][1]['identifier'])
    self.assertTrue(row['current_tasks'][0]['title'])
    self.assertEqual(row['tasks']['running'],1)
    self.assertIsNone(row['mcp_bindings'])
    store.replace_snapshot(SERVER,WS_A,'mcp_agent_state',[{'agent_id':'ag1','state':'ok'}])
    store.replace_snapshot(SERVER,WS_A,'mcp_bindings',[{'agent_id':'ag1','server_id':'m1','name':'search','enabled':0}])
    binding=view.agents()['items'][0]['mcp_bindings'][0]
    self.assertEqual(binding,{'server_id':'m1','name':'search','enabled':False})
    runs=[it['latest_run'] for it in view.issues()['items'] if it['latest_run']]
    self.assertTrue(runs[0]['created_at'])
   finally:store.close()

 def test_own_config_summary_does_not_mistake_empty_or_hidden_for_present(self):
  for raw,state in [(None,'unknown'),('{}','unknown'),('{"state":"none","servers":[]}', 'none'),('{"state":"hidden"}','hidden'),('{"state":"managed"}','managed'),('invalid','unknown')]:
   self.assertEqual(_mcp_own_state(raw),state)
