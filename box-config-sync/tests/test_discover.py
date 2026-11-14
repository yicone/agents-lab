import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import PROJECT, make_fixture
from boxsync.__main__ import discover, main
from boxsync.policy import Policy
from boxsync.roots import BOX_ROOTS


class DiscoverTests(unittest.TestCase):
    def test_unmeasured_roots_are_not_collected(self):
        self.assertTrue(all(root is None for root in BOX_ROOTS.values()))
        with patch("os.open", side_effect=AssertionError("unmeasured root accessed")):
            result = discover()
        self.assertTrue(all(row["state"] == "not_collected" for row in result["domains"].values()))

    def test_blacklist_has_zero_filesystem_calls_and_no_names_in_output(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            home = make_fixture(tmp)
            data = home / "agent-data"
            for name in ("BOX-SECRETS", ".env.local", "private.key", "auth-token", "cache.sqlite", ".config"):
                (data / name).mkdir()
            (data / ".config/gh").mkdir()
            policy = Policy()
            forbidden_calls = []
            # Track descriptor paths so relative operations cannot hide a violation.
            fd_paths = {}
            real_open, real_close = os.open, os.close
            real_stat, real_lstat, real_scandir = os.stat, os.lstat, os.scandir

            def checked_path(path, dir_fd=None):
                if isinstance(path, int):
                    path = fd_paths.get(path, "/")
                elif dir_fd is not None:
                    path = os.path.join(fd_paths.get(dir_fd, "/"), os.fspath(path))
                if policy.denied_path(path):
                    forbidden_calls.append(os.fspath(path))
                return os.path.abspath(path)

            def open_spy(path, flags, *args, **kwargs):
                absolute = checked_path(path, kwargs.get("dir_fd"))
                fd = real_open(path, flags, *args, **kwargs)
                fd_paths[fd] = absolute
                return fd

            def close_spy(fd):
                fd_paths.pop(fd, None)
                return real_close(fd)

            def stat_spy(path, *args, **kwargs):
                checked_path(path, kwargs.get("dir_fd"))
                return real_stat(path, *args, **kwargs)

            def lstat_spy(path, *args, **kwargs):
                checked_path(path, kwargs.get("dir_fd"))
                return real_lstat(path, *args, **kwargs)

            def scandir_spy(path):
                checked_path(path)
                return real_scandir(path)

            # DirEntry.stat/is_dir/is_symlink can bypass os.stat mocks: wrap them too.
            class GuardedEntry:
                def __init__(self, entry, parent):
                    self.entry, self.name, self.parent = entry, entry.name, parent

                def stat(self, **kwargs):
                    checked_path(os.path.join(self.parent, self.name))
                    return self.entry.stat(**kwargs)

            @contextlib.contextmanager
            def guarded_scandir(fd):
                with scandir_spy(fd) as iterator:
                    yield (GuardedEntry(entry, fd_paths[fd]) for entry in iterator)

            with patch("os.open", open_spy), patch("os.close", close_spy), \
                 patch("os.stat", stat_spy), patch("os.lstat", lstat_spy), \
                 patch("os.scandir", guarded_scandir), \
                 patch("os.read", side_effect=AssertionError("discover read content")), \
                 patch("subprocess.run", side_effect=AssertionError("discover started subprocess")):
                result = discover({"fixture": data}, policy)
            self.assertEqual(forbidden_calls, [])
            self.assertEqual(result["domains"]["fixture"]["state"], "ok")
            self.assertEqual(result["domains"]["fixture"]["counts"]["unsupported"], 2)
            self.assertEqual(policy.skips, {"credential_directory": 3, "environment_key": 2,
                                             "sensitive_name": 1, "database_session": 1})
            output = json.dumps(result)
            for name in ("connector-secrets", "BOX-SECRETS", ".env.local", "private.key", "auth-token", "cache.sqlite", "SKILL.md", "decoy"):
                self.assertNotIn(name, output)

    def test_absent_denied_and_unknown(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            self.assertEqual(discover({"workflows": Path(tmp) / "missing"})["domains"]["workflows"]["state"], "absent")
            link = Path(tmp) / "link"
            link.symlink_to(tmp)
            self.assertEqual(discover({"workflows": link})["domains"]["workflows"]["state"], "denied")
            with patch("os.scandir", side_effect=OSError("private text")):
                result = discover({"workflows": tmp})
            self.assertEqual(result["domains"]["workflows"]["state"], "unknown")
            self.assertNotIn("private text", json.dumps(result))

    def test_cli_and_no_state_writes(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            home = make_fixture(tmp)
            state = Path(tmp) / "state"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["discover", "--fixture-home", str(home), "--roots", "workflows",
                             "--state-dir", str(state), "--offline"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["domains"]["workflows"]["state"], "ok")
            self.assertFalse(state.exists())
            state.mkdir(mode=0o755)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["discover", "--state-dir", str(state)]), 4)
            state.chmod(0o700)
            with patch("os.fstat", return_value=type("Info", (), {"st_uid": os.getuid() + 1, "st_mode": 0o40700})()):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["discover", "--state-dir", str(state)]), 4)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["discover", "--state-dir", str(state)]), 3)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main(["discover", "--roots", "private-name", "--state-dir", str(state)])
            self.assertEqual(caught.exception.code, 2)

    def test_executable_entrypoint(self):
        result = subprocess.run([sys.executable, str(PROJECT / "bin/boxsync"), "discover", "--offline",
                                 "--state-dir", str(PROJECT / "tests/nonexistent-state")],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["schema"], "box-config-discovery/v1")
        self.assertEqual(result.stderr, "")
