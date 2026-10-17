"""All doctor commands use fixture inventories and an isolated HOME."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import doctor
from broken_links import repair


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.real = Path.home() / '.agents/skills'
        self.before = self.state(self.real)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.home = self.base / 'home'
        self.project = self.base / 'project'
        self.canonical = self.project / 'skills/group/demo'
        self.canonical.mkdir(parents=True)
        (self.canonical / 'SKILL.md').write_text('demo', encoding='utf-8')
        self.roots = [self.home / '.agents/skills', self.project / '.codex/skills']
        for root in self.roots:
            root.mkdir(parents=True)
        self.skills = [{'name': 'demo', 'status': 'active', 'authority': {
            'canonical_path': 'skills/group/demo'}, 'adapters': []}]
        self.inventory = self.base / 'inventory.json'

    def tearDown(self):
        self.assertEqual(self.state(self.real), self.before,
                         'doctor must not change real ~/.agents/skills')

    def run_doctor(self, *options, home=None, project=None, script='skill_guard.py'):
        self.inventory.write_text(json.dumps({'skills': self.skills}), encoding='utf-8')
        command = [sys.executable, str(SCRIPTS / script)]
        if script == 'skill_guard.py':
            command.append('doctor')
        command.extend(['--inventory', str(self.inventory), '--repo-root',
                        str(project or self.project), *options])
        return subprocess.run(command, cwd=project or self.project,
                              env=dict(os.environ, HOME=str(home or self.home)),
                              text=True, capture_output=True)

    def broken(self, root, name='demo'):
        path = root / name
        path.symlink_to(self.base / 'old' / name)
        return path

    def test_read_only_discovery_and_json(self):
        links = [self.broken(root) for root in self.roots]
        result = self.run_doctor('--format', 'json')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)['count'], 2)
        self.assertTrue(all(not link.exists() for link in links))

    def test_canonical_repair_and_text_new_target(self):
        links = [self.broken(root) for root in self.roots]
        result = self.run_doctor('--repair')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('0 unresolved, 2 repaired', result.stdout)
        self.assertIn(f'-> {self.canonical}', result.stdout)
        self.assertNotIn(str(self.base / 'old'), result.stdout)
        self.assertTrue(all(link.resolve() == self.canonical for link in links))

    def test_retired_cleanup(self):
        self.skills[0]['status'] = 'retired'
        link = self.broken(self.roots[0])
        result = self.run_doctor('--repair')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('[removed]', result.stdout)
        self.assertFalse(link.is_symlink())

    def test_unknown_orphan_is_preserved(self):
        link = self.broken(self.roots[0], 'unrecorded')
        result = self.run_doctor('--repair')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('(orphan)', result.stdout)
        self.assertTrue(link.is_symlink())

    def test_nested_name_is_not_matched(self):
        nested = self.roots[0] / 'pkg'
        nested.mkdir()
        link = self.broken(nested)
        result = self.run_doctor('--repair', '--format', 'json')
        self.assertEqual(result.returncode, 1, result.stderr)
        finding = json.loads(result.stdout)['broken'][0]
        self.assertEqual(finding['kind'], 'orphan_broken_symlink')
        self.assertEqual(finding['reason'], 'orphan')
        self.assertFalse(link.exists())

    def test_quarantined_and_unknown_stay_unresolved(self):
        for status in ('quarantined', 'unknown', None):
            with self.subTest(status=status):
                if status is None:
                    self.skills[0].pop('status', None)
                else:
                    self.skills[0]['status'] = status
                link = self.broken(self.roots[0])
                result = self.run_doctor('--repair')
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(f'non active status: {status or "unknown"}', result.stdout)
                self.assertFalse(link.exists())
                link.unlink()

    def test_staged_can_be_repaired(self):
        self.skills[0]['status'] = 'staged'
        link = self.broken(self.roots[0])
        result = self.run_doctor('--repair')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(link.resolve(), self.canonical)

    def test_alias_home_cwd_and_inventory_paths_are_deduplicated(self):
        alias = self.base / 'alias'
        alias.symlink_to(self.project, target_is_directory=True)
        adapters = self.project / '.agents/skills'
        adapters.mkdir(parents=True)
        for index in range(6):
            name = f'skill-{index}'
            self.broken(adapters, name)
            self.skills.append({'name': name, 'status': 'active', 'authority': {
                'canonical_path': 'skills/group/demo'}, 'adapters': [
                    {'kind': 'symlink', 'path': str(alias / '.agents/skills' / name)}]})
        checked = self.run_doctor('--format', 'json', home=alias, project=alias)
        self.assertEqual(json.loads(checked.stdout)['count'], 6)
        fixed = self.run_doctor('--repair', '--format', 'json', home=alias, project=alias)
        self.assertEqual(fixed.returncode, 0, fixed.stderr)
        report = json.loads(fixed.stdout)
        self.assertEqual(report['repaired'], 6)
        self.assertEqual(len(report['broken']), 6)
        self.assertTrue(all(path.exists() for path in adapters.iterdir()))

    def test_doctor_recorded_target_fallback_without_canonical_skill_md(self):
        (self.canonical / 'SKILL.md').unlink()
        target = self.project / 'recorded'
        target.mkdir()
        link = self.broken(self.roots[0])
        self.skills[0]['adapters'] = [dict(path=str(link), kind='symlink', target=str(target))]
        result = self.run_doctor('--repair', script='doctor.py')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(link.resolve(), target)
        self.assertEqual(json.loads(result.stdout)['broken'][0]['target'], str(target))

    def test_missing_targets_explain_unresolved(self):
        self.broken(self.roots[0])
        (self.canonical / 'SKILL.md').unlink()
        result = self.run_doctor('--repair')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('no canonical directory', result.stdout)

    def test_placeholder_text_and_clean_result(self):
        self.skills[0]['adapters'] = [dict(path='placeholder/demo', kind='declared_target', target=None)]
        result = self.run_doctor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('(not bound; unchecked)', result.stdout)
        self.assertNotIn('-> None', result.stdout)

    def test_one_repair_failure_does_not_stop_other_links(self):
        first = self.broken(self.roots[0])
        second = self.broken(self.roots[1])

        def fail_first(path, target):
            if path == first:
                raise OSError('fixture permission denied')
            return repair(path, target)

        with patch.object(doctor, 'repair', side_effect=fail_first):
            findings = doctor.inspect({'skills': self.skills}, self.project, True, self.roots)
        failed = next(item for item in findings if item['path'] == str(first))
        self.assertFalse(failed['repaired'])
        self.assertIn('repair failed', failed['reason'])
        self.assertEqual(second.resolve(), self.canonical)

    def test_registered_nested_parent_does_not_enable_name_matching(self):
        nested = self.roots[0] / 'pkg'
        nested.mkdir()
        registered = nested / 'registered'
        registered.symlink_to(self.canonical)
        self.skills.append({
            'name': 'registered', 'status': 'active',
            'adapters': [{'kind': 'symlink', 'path': str(registered),
                          'target': str(self.canonical)}],
        })
        orphan = self.broken(nested)
        original_target = os.readlink(orphan)
        result = self.run_doctor('--repair', '--format', 'json')
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['count'], 1)
        self.assertEqual(report['repaired'], 0)
        self.assertEqual(report['broken'][0]['kind'], 'orphan_broken_symlink')
        self.assertEqual(report['broken'][0]['reason'], 'orphan')
        self.assertEqual(os.readlink(orphan), original_target)
        self.assertFalse(orphan.exists())

    def test_missing_adapter_and_declared_target_have_reasons(self):
        self.skills[0]['adapters'] = [
            {'kind': 'symlink', 'path': str(self.roots[0] / 'demo'),
             'target': str(self.canonical)},
            {'kind': 'declared_target', 'path': 'placeholder/demo',
             'target': 'missing-target'},
        ]
        expected = {
            'adapter_missing': 'adapter path does not exist',
            'declared_target_missing': 'declared target does not exist',
        }
        for script in ('skill_guard.py', 'doctor.py'):
            with self.subTest(script=script):
                result = self.run_doctor('--format', 'json', script=script)
                self.assertEqual(result.returncode, 1, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report['count'], 2)
                self.assertEqual({item['kind']: item.get('reason')
                                  for item in report['broken']}, expected)
                text = self.run_doctor('--format', 'text', script=script)
                self.assertEqual(text.returncode, 1, text.stderr)
                for reason in expected.values():
                    self.assertIn(f'[unresolved] ({reason})', text.stdout)

    @staticmethod
    def state(root):
        if not root.exists():
            return None
        return sorted((path.name, path.lstat().st_mtime_ns,
                       os.readlink(path) if path.is_symlink() else None)
                      for path in root.iterdir())
