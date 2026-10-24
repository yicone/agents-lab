"""PID-free serve lifecycle, with no access to the installed launchd service."""
from __future__ import annotations

import os
import http.client
from pathlib import Path
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import unittest


BACKEND = Path(__file__).resolve().parents[1]
BOARD = BACKEND.parent


class PidLifecycleTests(unittest.TestCase):
    def test_runtime_and_packaging_do_not_reference_legacy_files(self):
        # Include future launch/install/package scripts under the Board tree.
        for path in BOARD.rglob("*"):
            if not path.is_file() or "tests" in path.parts:
                continue
            if path.suffix not in {".py", ".sh", ".plist", ".toml", ".json"}:
                continue
            with self.subTest(path=str(path.relative_to(BOARD))):
                source = path.read_text()
                self.assertNotIn("board.pid", source)
                self.assertNotIn("serve.log", source)

    def _exercise(self, stale: bool, crash: bool, hosts: list[str] | None = None):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            state = home / ".multica-board"
            state.mkdir()
            legacy = {state / "board.pid": b"42222\n", state / "serve.log": b"old manual run\n"}
            if stale:
                for path, content in legacy.items():
                    path.write_bytes(content)
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in legacy if p.exists()}
            env = {"HOME": tmp, "PATH": os.defpath, "PYTHONPATH": str(BACKEND),
                   "PYTHONDONTWRITEBYTECODE": "1"}
            # Start twice to cover recovery after both graceful exit and crash.
            for cycle in range(2):
                proc = subprocess.Popen(
                    [sys.executable, "-m", "multica_board", "serve", "--all-workspaces",
                     "--refresh", "0", "--port", "0", "--plan-dir", str(home / "plans"),
                     "--multica-bin", str(home / "nonexistent-cli")] + (hosts or []),
                    cwd=tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(proc.stdout, selectors.EVENT_READ)
                        self.assertTrue(selector.select(timeout=10), "serve startup timed out")
                    line = proc.stdout.readline().decode()
                    self.assertIn("listening on http://127.0.0.1:", line)
                    if hosts:
                        second = proc.stdout.readline().decode()
                        self.assertIn("listening on http://[::1]:", second)
                        port = int(line.strip().rsplit(":", 1)[1])
                        for host in ("127.0.0.1", "::1"):
                            conn = http.client.HTTPConnection(host, port, timeout=3)
                            conn.request("GET", "/api/meta", headers={"Host": f"atlas.test:{port}"})
                            response = conn.getresponse()
                            response.read()
                            self.assertEqual(response.status, 200)
                            conn.close()
                    for path in legacy:
                        if stale:
                            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before[path])
                        else:
                            self.assertFalse(path.exists())
                    proc.send_signal(signal.SIGKILL if crash and cycle == 0 else signal.SIGTERM)
                    _, stderr = proc.communicate(timeout=10)
                    self.assertEqual(proc.returncode, -signal.SIGKILL if crash and cycle == 0 else 0,
                                     stderr.decode())
                finally:
                    if proc.poll() is None:
                        proc.kill()  # Only the exact isolated child started above.
                    proc.communicate(timeout=10)
                for path in legacy:
                    if stale:
                        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before[path])
                    else:
                        self.assertFalse(path.exists())

    def test_clean_home_graceful_restart(self):
        self._exercise(stale=False, crash=False)

    def test_stale_files_graceful_restart(self):
        self._exercise(stale=True, crash=False)

    def test_clean_home_crash_recovery(self):
        self._exercise(stale=False, crash=True)

    def test_stale_files_crash_recovery(self):
        self._exercise(stale=True, crash=True)

    def test_multiple_listeners_graceful_restart(self):
        try:
            with socket.socket(socket.AF_INET6) as probe:
                probe.bind(("::1", 0))
        except OSError as exc:
            self.skipTest(f"IPv6 unavailable: {exc}")
        self._exercise(stale=False, crash=False, hosts=["--host", "127.0.0.1,::1",
                                                        "--host", "127.0.0.1",
                                                        "--allowed-host", "atlas.test,other.test",
                                                        "--allowed-host", "third.test"])
