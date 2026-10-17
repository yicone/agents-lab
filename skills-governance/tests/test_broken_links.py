import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import doctor
from broken_links import repair, scan


class BrokenLinksTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.adapters = self.root / 'adapters'
        self.adapters.mkdir()
        self.target = self.root / 'target'
        self.target.mkdir()
        self.inventory = self.root / 'inventory.json'

    def run_doctor(self, adapters, *options):
        self.inventory.write_text(json.dumps({'skills': [{'name': 'one', 'adapters': adapters}]}))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = doctor.main(['--inventory', str(self.inventory), '--repo-root',
                                  str(self.root), *options])
        return status, output.getvalue()

    def adapter(self, target='../target'):
        return dict(path='adapters/one', kind='symlink', target=target)

    def test_healthy_link_and_orphan_fail(self):
        (self.adapters / 'one').symlink_to('../target')
        (self.adapters / 'orphan').symlink_to('removed-canonical')
        status, output = self.run_doctor([self.adapter()], '--format', 'text')
        self.assertEqual(status, 1)
        self.assertIn('orphan_broken_symlink', output)
        self.assertIn('removed-canonical', output)
        self.assertNotIn('None', output)

    def test_missing_adapter_fails(self):
        status, output = self.run_doctor([self.adapter()])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output)['broken'][0]['kind'], 'adapter_missing')

    def test_broken_link_without_repair_fails(self):
        (self.adapters / 'one').symlink_to('missing')
        status, _ = self.run_doctor([self.adapter()])
        self.assertEqual(status, 1)

    def test_clean_returns_zero(self):
        (self.adapters / 'one').symlink_to('../target')
        status, output = self.run_doctor([self.adapter()], '--format', 'text')
        self.assertEqual(status, 0)
        self.assertIn('No adapter link problems', output)

    def test_relative_target_repairs_from_link_parent(self):
        link = self.adapters / 'one'
        link.symlink_to('old-canonical')
        status, output = self.run_doctor([self.adapter()], '--repair')
        self.assertEqual(status, 0)
        self.assertEqual(link.resolve(), self.target)
        self.assertEqual(json.loads(output)['repaired'], 1)

    def test_remaining_broken_link_after_repair_fails(self):
        (self.adapters / 'one').symlink_to('old-canonical')
        (self.adapters / 'orphan').symlink_to('removed-canonical')
        status, output = self.run_doctor([self.adapter()], '--repair')
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output)['count'], 1)

    def test_unrepairable_link_fails(self):
        (self.adapters / 'one').symlink_to('missing')
        status, _ = self.run_doctor([self.adapter('still-missing')], '--repair')
        self.assertEqual(status, 1)

    def test_declared_target_bound_and_placeholder(self):
        adapter = dict(path='adapters/one', kind='declared_target', target='missing')
        status, _ = self.run_doctor([adapter])
        self.assertEqual(status, 1)
        adapter['target'] = 'target'
        status, _ = self.run_doctor([adapter])
        self.assertEqual(status, 0)
        adapter['target'] = None
        status, output = self.run_doctor([adapter], '--format', 'text')
        self.assertEqual(status, 0)
        self.assertIn('unchecked', output)

    def test_scan_recursive_and_broken_root(self):
        nested = self.adapters / 'nested'
        nested.mkdir()
        link = nested / 'broken'
        link.symlink_to('missing')
        self.assertEqual(scan(self.adapters), [link])
        self.assertEqual(scan(link), [link])
        healthy = self.adapters / 'healthy'
        healthy.symlink_to('../target')
        self.assertEqual(scan(healthy), [])

    def test_repair_direct_and_refuses_live_paths(self):
        link = self.adapters / 'broken'
        link.symlink_to('missing')
        repair(link, '../target')
        self.assertEqual(link.resolve(), self.target)
        with self.assertRaises(ValueError):
            repair(link, 'another')
        with self.assertRaises(ValueError):
            repair(self.target, 'another')


if __name__ == '__main__':
    unittest.main()
