"""Manifest-bound reads, integrity failures, refresh and isolation."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.skill_grok_export import grok_export_skills, read_export_content
from multica_board.store import Store
from multica_board.views import Views


class GrokExportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve()
        self.main = self.root / 'skills-user/demo/SKILL.md'
        self.main.parent.mkdir(parents=True)
        self.main.write_text('---\nname: demo\n---\n# Demo body\n')
        self.note = self.main.parent / 'references/note.md'
        self.note.parent.mkdir()
        self.note.write_text('# Attachment\n')
        self.rows = [{'path': str(p.relative_to(self.root)), 'skill': 'demo',
                      'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size}
                     for p in [self.main, self.note]]
        self.doc = {'hash_algorithm': 'sha256', 'file_count': 2, 'exported_at': '2026-10-27T00:51:49+08:00',
                    'bot_name': 'Agent Steward', 'bot_id': 'steward', 'files': self.rows,
                    'skills_user': {'count': 1, 'file_count': 2, 'skills': [
                        {'name': 'demo', 'slug': 'demo', 'sha256': self.rows[0]['sha256'], 'bytes': self.rows[0]['bytes'],
                         'file_count': 2, 'total_bytes': sum(r['bytes'] for r in self.rows)}]}}
        self.write_manifest()

    def write_manifest(self):
        (self.root / 'manifest.json').write_text(json.dumps(self.doc))

    def read(self, **kwargs):
        return read_export_content(name='demo', path='grok-export:demo', root=self.root, **kwargs)

    def test_reads_body_and_attachments_without_writes_or_memory(self):
        (self.root / 'steward-memory.jsonl').write_text('must never read')
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with mock.patch('subprocess.run', side_effect=AssertionError('no RPC/CLI')):
            items, source = grok_export_skills(self.root)
            self.assertEqual(source['state'], 'ok')
            self.assertEqual(source['exported_at'], self.doc['exported_at'])
            self.assertEqual(items[0]['export']['failed_count'], 0)
            self.assertNotIn('local_source', items[0])
            self.assertEqual(self.read()['content'], self.main.read_text())
            self.assertEqual(self.read(file='references/note.md')['content'], self.note.read_text())
            self.assertEqual(len(self.read(tree=True)['files']), 2)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_changed_and_missing_files_are_visible_and_never_serve_body(self):
        self.main.write_text('tampered body')
        self.note.unlink()
        items, source = grok_export_skills(self.root)
        self.assertEqual(source['state'], 'bad')
        self.assertEqual(items[0]['export']['failed_count'], 2)
        self.assertEqual([f['state'] for f in items[0]['export']['files']], ['hash_mismatch', 'missing'])
        self.assertEqual(self.read()['state'], 'hash_mismatch')
        self.assertNotIn('content', self.read())
        self.assertEqual(self.read(file='references/note.md')['state'], 'missing')
        # Refresh also re-verifies every file; restoring it needs no restart/cache clear.
        self.main.write_text('---\nname: demo\n---\n# Demo body\n')
        self.assertTrue(self.read()['ok'])

    def test_symlinks_and_unlisted_files_cannot_read_outside_export(self):
        self.note.unlink()
        self.note.symlink_to(self.root / 'manifest.json')
        self.assertEqual(self.read(file='references/note.md')['state'], 'denied')
        self.main.parent.joinpath('unlisted.md').write_text('unlisted')
        for file in ['../manifest.json', '/etc/passwd', 'references/../../steward-memory.jsonl', 'unlisted.md']:
            self.assertEqual(self.read(file=file)['state'], 'denied')
        self.assertEqual(read_export_content(name='other', path='grok-export:demo', root=self.root)['state'], 'denied')
        self.note.parent.rename(self.note.parent.with_name('old-references'))
        self.note.parent.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.read(file='references/note.md')['state'], 'denied')

    def test_invalid_manifest_never_partially_authorizes_reads(self):
        for change in ['missing_hash', 'traversal', 'duplicate', 'count', 'memory', 'skill_hash']:
            doc = json.loads(json.dumps(self.doc))
            if change == 'missing_hash': del doc['files'][1]['sha256']
            if change == 'traversal': doc['files'][1]['path'] = 'skills-user/demo/../../secret'
            if change == 'duplicate': doc['files'][1] = doc['files'][0]
            if change == 'count': doc['file_count'] = 3
            if change == 'memory': doc['files'][1]['path'] = 'steward-memory.jsonl'
            if change == 'skill_hash': doc['skills_user']['skills'][0]['sha256'] = '0' * 64
            (self.root / 'manifest.json').write_text(json.dumps(doc))
            self.assertEqual(grok_export_skills(self.root)[0], [], change)
            self.assertEqual(self.read()['state'], 'denied', change)
        (self.root / 'manifest.json').unlink()
        self.assertEqual(grok_export_skills(self.root)[1]['state'], 'absent')

    def test_views_route_export_without_using_local_inventory(self):
        store = Store(':memory:')
        self.addCleanup(store.close)
        views = Views(store, 'test', app_url='https://example.test', collect_interval_s=60)
        with mock.patch('multica_board.skill_grok_export.EXPORT_ROOT', self.root), mock.patch(
                'multica_board.skills_view.skills_document', side_effect=AssertionError('not a local install')):
            self.assertEqual(views.skills_content(name='demo', path='grok-export:demo')['content'], self.main.read_text())
            self.assertEqual(views.skills_content(name='demo', path='grok-export:demo', source='cloud')['state'], 'uncollected')


if __name__ == '__main__':
    unittest.main()
