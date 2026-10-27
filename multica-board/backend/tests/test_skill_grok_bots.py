"""Read-only Grok Bot declarations stay distinct from local/Workspace skills."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.skill_grok_bots import MANIFEST_PATH, grok_bot_skills
from multica_board.skill_runtimes import runtime_columns
from multica_board.store import Store
from multica_board.views import Views


class GrokBotSkillTests(unittest.TestCase):
    def test_manifest_names_and_evidence_are_read_only(self):
        before = hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()
        expected_bot_count = len(json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))['bots'])
        with mock.patch('subprocess.run', side_effect=AssertionError('no CLI/RPC allowed')):
            items, source = grok_bot_skills()
        self.assertEqual(source['state'], 'ok')
        self.assertEqual(len(items), 8)
        self.assertEqual(len({row['key'] for row in items}), 8)
        for item in items:
            self.assertEqual(len(item['grok_bots']), expected_bot_count)
            observed = [bot['name'] for bot in item['grok_bots'] if bot['evidence_state'] == 'observed']
            self.assertEqual(set(observed), {'Agent Steward · Agent 基建总管', 'EO-PM · Elder Oasis PM'})
            self.assertEqual(sum(bot['evidence_state'] == 'inferred' for bot in item['grok_bots']), expected_bot_count - len(observed))
            self.assertIn('未逐一实测', item['evidence_detail'])
            self.assertEqual(item['content']['state'], 'not_applicable')
            self.assertNotIn('local_source', item)
            self.assertNotIn('workspace_installs', item)
            self.assertFalse(item['entries'])
            self.assertIsNone(item['current_version'])
        self.assertEqual(hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest(), before)
        doc = runtime_columns({'items':items, 'tools':[{'id':'codex','label':'Codex'}]}, {'items':[{'provider':'future'}]}, [])
        for item in doc['items']:
            self.assertTrue(all(value['state'] == 'not_applicable' for value in item['runtime_evidence'].values()))

    def test_bad_or_missing_manifest_is_explicit_and_never_partially_trusted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'manifest.json'
            items, source = grok_bot_skills(path)
            self.assertFalse(items)
            self.assertEqual(source['state'], 'absent')
            for data in ['{', '[]', '{"schema":"unknown","bots":[]}', '{"schema":"grok-bots-manifest/v1","bots":[{"id":"x","name":"x","skills":[null]}]}']:
                path.write_text(data)
                items, source = grok_bot_skills(path)
                self.assertEqual(items, [])
                self.assertEqual(source['state'], 'bad')
            # A new skill, even on an observed Bot, has no ACM-298 measurement.
            path.write_text(json.dumps({'schema':'grok-bots-manifest/v1','bots':[{'id':'9cb8bf94-4ca4-4e1e-81f9-b174b5e0a985','name':'Agent Steward','skills':['new skill','new skill']}]}))
            items, source = grok_bot_skills(path)
            self.assertEqual(len(items),1)
            self.assertEqual(len(items[0]['grok_bots']),1)
            self.assertEqual(items[0]['grok_bots'][0]['evidence_state'],'inferred')

    def test_views_preserve_same_named_sources_and_totals_with_or_without_workspace_cache(self):
        store = Store(':memory:')
        self.addCleanup(store.close)
        views = Views(store,'test',app_url='https://example.test',collect_interval_s=60)
        local = {'name':'ego-browser','origin':'inventory','source_class':'local_custom','entries':[],'tools':{}}
        document = {'ok':True,'generated_at':'t','items':[local],'tools':[],'attention':{'badge':0},'meta':{}}
        for installs in [None, {'ego-browser':[{'id':'workspace-skill'}]}]:
            with mock.patch('multica_board.skill_grok_export.grok_export_skills', return_value=([], {'id': 'grok_bot_export', 'state': 'absent', 'detail': 'test'})), mock.patch('multica_board.skills_view.skills_document',return_value=document), mock.patch.object(views,'_workspace_installs',return_value=installs), mock.patch.object(views,'runtimes',return_value={'items':[]}):
                result=views.skills()
            named = [item for item in result['items'] if item['name']=='ego-browser']
            self.assertEqual(len(named),2)
            bot=next(item for item in named if item['origin']=='grok_bot')
            self.assertEqual(bot['related_skill_name'],'ego-browser')
            self.assertEqual(len(result['items']),9)
            self.assertEqual(result['summary'][0]['count'],9)
            self.assertEqual(next(block for block in result['summary'] if block['id']=='grok_bot')['count'],8)
            self.assertNotIn('runtime_evidence',document['items'][0])
            self.assertNotIn('workspace_installs',bot)
        self.assertEqual(len(document['items']),1)


if __name__ == '__main__':
    unittest.main()
