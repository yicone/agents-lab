"""Execute the dispatch playbook command without dispatching or writing Atlas."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1] / 'docs/playbook-quota.patch'


def command():
    added = '\n'.join(line[1:] for line in PATCH.read_text().splitlines()
                      if line.startswith('+') and not line.startswith('+++'))
    return added.split('```bash\n', 1)[1].split('\n```', 1)[0]


class QuotaPlaybookTests(unittest.TestCase):
    def run_check(self, exit_code, payload):
        with tempfile.TemporaryDirectory() as tmp:
            curl = Path(tmp) / 'curl'
            curl.write_text('''#!/bin/sh
printf '%s\\n' "$@" > "$ARGS_FILE"
printf '%s' "$PAYLOAD"
exit "$CURL_EXIT"
''')
            curl.chmod(0o755)
            args_file = Path(tmp) / 'args'
            env = dict(os.environ, PATH=tmp + ':' + os.environ['PATH'],
                       ARGS_FILE=str(args_file), PAYLOAD=payload, CURL_EXIT=str(exit_code))
            result = subprocess.run(['bash', '-c',
                "ATLAS_ATTENTION='old quota';\n" + command() +
                '\nprintf "\\nFINAL=%s\\n" "$ATLAS_ATTENTION"'],
                env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = args_file.read_text().splitlines()
            self.assertEqual(args, ['--noproxy', '*', '--fail', '--silent', '--show-error',
                '--connect-timeout', '1', '--max-time', '3', '--request', 'GET',
                'http://127.0.0.1:8765/api/attention'])
            return result.stdout

    def test_success_preserves_existing_alerts(self):
        payload = '{"quota_alerts":[{"provider":"cursor","stale":true,"band":"unknown"}]}'
        self.assertIn('FINAL=' + payload, self.run_check(0, payload))

    def test_empty_array_is_preserved_without_health_claim(self):
        output = self.run_check(0, '{"quota_alerts":[]}')
        self.assertIn('FINAL={"quota_alerts":[]}', output)
        self.assertNotIn('额度充足', output)

    def test_connection_timeout_http_failures_clear_partial_and_old_results(self):
        for code in (7, 22, 28):
            with self.subTest(curl_exit=code):
                output = self.run_check(code, 'partial quota data')
                self.assertIn('Atlas 额度未知', output)
                self.assertTrue(output.endswith('FINAL=\n'))
                self.assertNotIn('partial quota data', output)
                self.assertNotIn('old quota', output)
