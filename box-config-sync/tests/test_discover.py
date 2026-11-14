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
from boxsync import roots as registration
from boxsync.roots import BOX_ROOTS, fixture_roots


class DiscoverTests(unittest.TestCase):
    def test_unmeasured_roots_are_not_collected(self):
        unmeasured = {alias: root for alias, root in BOX_ROOTS.items() if root is None}
        self.assertEqual(set(unmeasured), {"mcps", "memory", "routines", "attachments"})
        with patch("os.open", side_effect=AssertionError("unmeasured root accessed")):
            result = discover(unmeasured)
        self.assertTrue(all(row["state"] == "not_collected" for row in result["domains"].values()))

    def test_measured_registry_and_capabilities(self):
        self.assertEqual(registration.SAND_DATA_PARENT, Path("/home/box/sand-data"))
        self.assertEqual(registration.CONNECTOR_SECRETS_PARENT, registration.SAND_DATA_PARENT)
        self.assertEqual(registration.SETTINGS_PATH, Path("/home/box/sand-data/settings.json"))
        expected = {"profiles": "agents", "workflows": "workflows",
                    "managed_skills": "managed-skills", "plugin_skills": "plugin-skills"}
        for alias, directory in expected.items():
            self.assertEqual(BOX_ROOTS[alias], registration.SAND_DATA_PARENT / directory)
        self.assertEqual(registration.BOX_PYTHON_VERSION, "3.13.5")
        self.assertEqual(registration.BOX_MULTICA_VERSION, "0.8.0")
        self.assertEqual(registration.BOX_MULTICA_COMMIT, "9b1ab4f9e")
        self.assertEqual(registration.NO_START_FLAG, "unsupported")
        self.assertEqual(registration.ACCESSIBLE_WORKSPACES, ("8ebc87ea-27f2-42c4-90f6-24a04e64d3f5",))
        self.assertEqual(registration.BOX_MULTICA_PROFILE, "atlas-pm")
        self.assertEqual(registration.BOX_MULTICA_IDENTITY, "yadhiel+atlas-pm@mail.grokbot.com")
        self.assertEqual(registration.BOX_GITHUB_IDENTITY, "yicone")
        self.assertTrue(registration.SSH_Y_VERIFY)
        self.assertEqual(registration.LIBRARY_FINGERPRINT_FORMAT, "json")
        self.assertEqual(registration.LIBRARY_FINGERPRINT_ENCODING, "utf-8")
        self.assertFalse(registration.LIBRARY_FINGERPRINT_BOM)
        self.assertFalse(registration.LIBRARY_FINGERPRINT_NEWLINE)
        self.assertEqual(registration.LIBRARY_FINGERPRINT_KEY, "skill_slug")
        self.assertEqual(registration.LIBRARY_FINGERPRINT_VALUE_PATTERN, r"[0-9a-f]{64}")
        self.assertIsNone(registration.LIBRARY_FINGERPRINT_HASH_SCOPE)

    def test_canonical_fixture_root_works_but_alias_is_denied(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            home = make_fixture(tmp)
            roots = fixture_roots(home)
            self.assertEqual(roots["workflows"], home / "sand-data/workflows")
            with patch("os.read", side_effect=AssertionError("content read")), \
                 patch("subprocess.run", side_effect=AssertionError("CLI called")):
                result = discover({"workflows": roots["workflows"]})
                aliased = discover({"workflows": home / "agent-data/workflows"})
            self.assertEqual(result["domains"]["workflows"]["state"], "ok")
            self.assertEqual(aliased["domains"]["workflows"]["state"], "denied")

    def test_blacklist_has_zero_filesystem_calls_and_no_names_in_output(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            home = make_fixture(tmp)
            data = home / "sand-data"
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
