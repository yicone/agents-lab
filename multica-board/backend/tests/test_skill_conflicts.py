import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board import skill_conflicts


class ConflictAdapterTests(unittest.TestCase):
    def setUp(self):
        skill_conflicts._CACHE.clear()

    def check(self, stamp=()):
        return skill_conflicts.check_conflicts(Path(__file__), Path('/repo'), Path('/inventory'), [Path('/skills')], stamp)

    def test_local_command_cache_expiry_and_install_invalidation(self):
        output = json.dumps({'report': 'skills-conflict/v1', 'read_only': True, 'findings': []})
        with mock.patch.object(skill_conflicts.subprocess, 'run', return_value=mock.Mock(stdout=output)) as run, mock.patch.object(skill_conflicts.time, 'monotonic', return_value=0) as clock:
            self.assertEqual(self.check()['state'], 'ok')
            self.check()
            self.assertEqual(run.call_count, 1)
            command = run.call_args.args[0]
            self.assertIn('--no-default-roots', command)
            self.assertEqual(command[-2:], ['--scan-root', '/skills'])
            self.assertEqual(command[2:4], ['--format', 'json'])
            clock.return_value = 61
            self.check()
            self.assertEqual(run.call_count, 2)
            self.check(('new-install',))
            self.assertEqual(run.call_count, 3)

    def test_failure_and_malformed_json_are_cached_fallbacks(self):
        cases = [OSError('missing'), subprocess.TimeoutExpired('report', 20), subprocess.CalledProcessError(1, 'report'), 'broken', '{}', '{"report":"skills-conflict/v1","read_only":true,"findings":[{}]}']
        for case in cases:
            with self.subTest(case=case):
                skill_conflicts._CACHE.clear()
                kwargs = {'side_effect': case} if isinstance(case, Exception) else {'return_value': mock.Mock(stdout=case)}
                with mock.patch.object(skill_conflicts.subprocess, 'run', **kwargs) as run:
                    result = self.check()
                    self.assertEqual(result['state'], 'fallback')
                    self.assertEqual(result['findings'], [])
                    self.check()
                    self.assertEqual(run.call_count, 1)
