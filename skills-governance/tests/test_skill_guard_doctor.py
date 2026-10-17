"""Integration fixtures isolate both HOME and the canonical inventory."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/skill_guard.py'


class DoctorTests(unittest.TestCase):
    def test_discover_repair_and_preserve_real_home(self):
        real = Path.home() / '.agents/skills'
        before = self.state(real)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / 'home'
            project = base / 'project'
            canonical = project / 'skills/group/demo'
            canonical.mkdir(parents=True)
            (canonical / 'SKILL.md').write_text('demo', encoding='utf-8')
            roots = [home / '.agents/skills', project / '.codex/skills']
            for root in roots:
                root.mkdir(parents=True)
                (root / 'demo').symlink_to(base / 'old/demo')
            (roots[1] / 'retired').symlink_to(base / 'old/retired')
            (roots[1] / 'unknown').symlink_to(base / 'old/unknown')
            inventory = base / 'inventory.json'
            inventory.write_text(json.dumps({'skills': [
                {'name': 'demo', 'status': 'active', 'authority': {
                    'canonical_path': 'skills/group/demo'}, 'adapters': []},
                {'name': 'retired', 'status': 'retired', 'adapters': []},
            ]}), encoding='utf-8')
            command = [sys.executable, str(SCRIPT), 'doctor', '--inventory',
                       str(inventory), '--repo-root', str(project)]

            def run(*options):
                return subprocess.run(command + list(options), cwd=project,
                                      env=dict(os.environ, HOME=str(home)),
                                      text=True, capture_output=True)

            checked = run('--format', 'json')
            self.assertEqual(checked.returncode, 1, checked.stderr)
            self.assertEqual(json.loads(checked.stdout)['count'], 4)
            self.assertFalse((roots[0] / 'demo').exists())
            fixed = run('--repair')
            self.assertEqual(fixed.returncode, 1, fixed.stderr)
            self.assertIn('1 unresolved, 3 repaired', fixed.stdout)
            self.assertIn('[removed]', fixed.stdout)
            for root in roots:
                self.assertEqual((root / 'demo').resolve(), canonical.resolve())
            self.assertFalse((roots[1] / 'retired').is_symlink())
            self.assertTrue((roots[1] / 'unknown').is_symlink())
            (roots[1] / 'unknown').unlink()
            clean = run('--format', 'json')
            self.assertEqual(clean.returncode, 0, clean.stderr)
            self.assertEqual(json.loads(clean.stdout)['count'], 0)
        self.assertEqual(self.state(real), before, 'real ~/.agents/skills changed')

    @staticmethod
    def state(root):
        if not root.exists():
            return None
        return sorted((path.name, path.lstat().st_mtime_ns,
                       os.readlink(path) if path.is_symlink() else None)
                      for path in root.iterdir())
