import contextlib
import copy
import importlib.util
import io
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import subprocess
import sys
import threading
import unittest

BOARD = Path(__file__).resolve().parents[2]
SCRIPT = BOARD / "scripts/skills-drift-digest.py"
FIXTURE = BOARD / "web/fixtures/skills-digest.json"
spec = importlib.util.spec_from_file_location("skills_drift_digest", SCRIPT)
digest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(digest)


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(FIXTURE.read_text())

    def test_offline_cli_both_formats_and_stdin(self):
        for fmt in ("markdown", "text"):
            result = subprocess.run([sys.executable, str(SCRIPT), "--input", str(FIXTURE), "--format", fmt], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("mismatch=1, drift=1, unrecorded=1, upgradable=1", result.stdout)
            self.assertIn("last_gate=empty", result.stdout)
            self.assertIn("upgrade_source=partial", result.stdout)
            self.assertNotIn("digest-aligned:", result.stdout)
            self.assertEqual("```sh" in result.stdout, fmt == "markdown")
        result = subprocess.run([sys.executable, str(SCRIPT), "--input", "-"], input=json.dumps(self.payload), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_commands_and_unknown_upgrade_are_not_a_pass(self):
        output = digest.digest({"ok": True, "items": []})
        self.assertIn("无需命令 / 信息不足", output)
        self.assertIn("last_gate=unknown", output)
        self.assertIn("upgrade_source=unknown", output)
        self.assertNotIn("```sh", output)
        self.assertNotIn("last_gate=pass", output)

    def test_gate_states_remain_distinct(self):
        for state in ("empty", "error", "recorded", "fallback", "invented"):
            self.payload["attention"]["last_gate"] = {"state": state, "result": "pass"}
            output = digest.digest(self.payload)
            expected = state if state != "invented" else "unknown"
            self.assertIn("last_gate=" + expected, output)
            if state == "fallback":
                self.assertIn("不代表 SkilLock 通过", output)
        self.assertIn("recorded / block", digest.gate_text({"state": "recorded", "result": "block"}))

    def test_sensitive_payload_fields_and_invalid_ids_never_escape(self):
        for secret in ("person@example.test", "/Users/private/sensitive/SKILL.md", "/home/private/file", "token=abc", "sk-abcdef", "$(touch bad)", "a\nPOST /activate"):
            payload = copy.deepcopy(self.payload)
            item = payload["items"][0]
            item.update(name=secret, command=secret, local_source={"path": secret}, workspace_installs=[{"id": secret, "workspace_id": secret}])
            payload["attention"]["last_gate"] = {"state": "error", "reason": secret, "summary_json_path": secret}
            output = digest.digest(payload)
            self.assertNotIn(secret, output)
            self.assertIn("[redacted]", output)
        for payload in ({"ok": False, "items": []}, {"ok": True}, {"ok": True, "items": [None]}):
            with self.assertRaises(ValueError):
                digest.digest(payload)
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            code = digest.main(["--input", "/Users/private/secret-token.json"])
        self.assertEqual(code, 1)
        self.assertNotIn("private", stream.getvalue())

    def test_get_only_live_cli_and_redirect_rejection(self):
        calls = []
        raw = json.dumps(self.payload).encode()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append((self.command, self.path))
                if len(calls) > 1:
                    self.send_response(302)
                    self.send_header("Location", "http://example.invalid/api/skills")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(raw)
            def log_message(self, *_args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/skills"
            result = subprocess.run([sys.executable, str(SCRIPT), "--url", url], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with self.assertRaises(ValueError):
                digest.read_url(url)
            self.assertEqual(calls, [("GET", "/api/skills")] * 2)
        finally:
            server.shutdown()
            worker.join()
            server.server_close()

    def test_live_url_rejects_credentials_remote_hosts_queries_and_other_paths(self):
        for url in ("https://example.invalid/api/skills", "http://user:secret@localhost/api/skills", "http://localhost/api/skills?token=secret", "http://localhost/api/skills#secret", "http://localhost/api/activate", "file:///etc/passwd"):
            with self.assertRaises(ValueError):
                digest.read_url(url)


if __name__ == "__main__":
    unittest.main()
