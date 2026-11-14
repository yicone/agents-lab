import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from support import REPO, make_fixture
from boxsync.policy import Policy
from boxsync.safe_io import read_file
from boxsync.secrets_scan import contains_secret, scan
from diff_instructions import SECRET_PATTERNS


class PolicyTests(unittest.TestCase):
    def test_all_hard_exclusions_case_insensitive_and_segment_based(self):
        policy = Policy()
        names = ("connector-secrets", "box-secrets", "host-secrets", "app-secrets", "secrets", ".ssh", ".gnupg", ".aws",
                 ".env", ".env.prod", "a.pem", "a.key", "a.p12", "a.pfx", "a.keystore", "id_rsa.pub", "id_ed25519",
                 ".netrc", ".git-credentials", ".npmrc", ".pypirc", "x-token", "x-secret", "credential.json",
                 "passwords", "cookies.json", "keychain", "a.sqlite3", "a.db", "a.db-wal", "a.db-shm", "transcript", "composer-draft")
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(policy.skip(name.upper()))
        self.assertTrue(policy.skip("GH", ".CONFIG"))
        self.assertIsNone(policy.category("gh"))
        self.assertTrue(policy.denied_path("x/.config/gh/data"))
        self.assertFalse(policy.denied_path("x/normal/SKILL.md"))
        self.assertTrue(Policy(["*.private"]).skip("test.private"))
        self.assertTrue(Policy(["*.private"]).skip(".env"))

    def test_safe_read_special_fixtures(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            home = make_fixture(tmp)
            root = home / "agent-data/workflows/example"
            started = time.monotonic()
            self.assertEqual(read_file(root, "pipe").state, "not_regular")
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(read_file(root, "link").state, "denied")
            self.assertEqual(read_file(root, "huge").state, "too_large")
            self.assertEqual(read_file(root, "missing").state, "absent")
            self.assertEqual(read_file(root, "SKILL.md").state, "observed")
            # Ancestor symlinks and traversal are refused before content access.
            (Path(tmp) / "ancestor").symlink_to(root, target_is_directory=True)
            self.assertEqual(read_file(Path(tmp) / "ancestor", "SKILL.md").state, "denied")
            for path in ("../SKILL.md", "/etc/passwd", "x/../SKILL.md", "connector-secrets/decoy.txt"):
                self.assertEqual(read_file(root, path).state, "denied")

    def test_forbidden_root_and_file_never_opened(self):
        with patch("os.open", side_effect=AssertionError("forbidden access")):
            self.assertEqual(read_file("/missing/connector-secrets", "data").state, "denied")
            self.assertEqual(read_file("/missing", ".env").state, "denied")

    def test_changed_and_replaced_during_read(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            path = Path(tmp) / "file"
            path.write_bytes(b"original")
            original_read = os.read
            mutated = False

            def mutate(fd, size):
                nonlocal mutated
                chunk = original_read(fd, size)
                if not mutated:
                    mutated = True
                    path.write_bytes(b"updated longer")
                return chunk

            with patch("os.read", mutate):
                self.assertEqual(read_file(tmp, "file").state, "changed_during_read")
            path.write_bytes(b"original")
            mutated = False

            def replace(fd, size):
                nonlocal mutated
                chunk = original_read(fd, size)
                if not mutated:
                    mutated = True
                    replacement = Path(tmp) / "replacement"
                    replacement.write_bytes(b"original")
                    replacement.replace(path)
                return chunk

            with patch("os.read", replace):
                self.assertEqual(read_file(tmp, "file").state, "changed_during_read")

    def test_exact_limit_growth_and_fd_cleanup(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            path = Path(tmp) / "file"
            path.write_bytes(b"x" * (512 * 1024))
            self.assertEqual(read_file(tmp, "file").state, "observed")
            original_read = os.read
            mutated = False

            def grow(fd, size):
                nonlocal mutated
                chunk = original_read(fd, size)
                if not mutated:
                    mutated = True
                    with path.open("ab") as stream:
                        stream.write(b"extra")
                return chunk

            with patch("os.read", grow):
                self.assertEqual(read_file(tmp, "file").state, "changed_during_read")
            # Every descriptor opened by the helper is closed even on rejection.
            opened, closed = [], []
            original_open, original_close = os.open, os.close

            def track_open(*args, **kwargs):
                fd = original_open(*args, **kwargs)
                opened.append(fd)
                return fd

            def track_close(fd):
                closed.append(fd)
                return original_close(fd)

            with patch("os.open", track_open), patch("os.close", track_close):
                self.assertEqual(read_file(tmp, "file").state, "too_large")
            self.assertCountEqual(opened, closed)

    def test_secret_contract_with_explicit_digest_exemption(self):
        spec = importlib.util.spec_from_file_location("board_redact_contract", REPO / "multica-board/backend/multica_board/redact.py")
        board = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(board)
        samples = ["Bearer abcdefghijklmnop", "sk-abcdefghijk", "ghp_" + "z" * 24,
                   "github_pat_" + "z" * 24, "xoxb-abcdefghijk", "AKIA" + "A" * 16,
                   "eyJabcdefghijk.abcdefghijk.abcdefghijk", "-----BEGIN OPENSSH PRIVATE KEY-----",
                   "mca_" + "q" * 20, 'token="syntheticvalue"', "api_key:syntheticvalue", "secret=x",
                   "password=x", "passwd=x", "authorization=x", "cookie=x", "a" * 48,
                   "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789+/AbCdEfGhIjKlMnOp"]
        patterns = list(SECRET_PATTERNS) + board._SECRET_PATTERNS
        for sample in samples:
            with self.subTest(sample_index=samples.index(sample)):
                self.assertTrue(any(pattern.search(sample) for pattern in patterns))
                findings = scan(sample)
                self.assertTrue(findings)
                self.assertNotIn(sample, json.dumps(findings))
                self.assertEqual(set(findings[0]), {"rule", "line", "finding_id"})
        for digest in ("482432fce3056acfd1cef5c4666d1510ed3ee7bb", "0123456789abcdef" * 4):
            self.assertFalse(contains_secret(digest))
            self.assertFalse(contains_secret("sha=" + digest))
            self.assertTrue(contains_secret("token=" + digest))
        self.assertTrue(contains_secret("aZ8kP1qX3vN6tW9rY2uI5oE7sD0fG4hJ"))
        self.assertEqual(scan("normal\nBearer abcdefghijklmnop")[0]["line"], 2)
