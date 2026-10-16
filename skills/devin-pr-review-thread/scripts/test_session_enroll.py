import contextlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import session_enroll


class SessionEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = pathlib.Path(self.temporary.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("remote", "add", "origin", "git@github.com:owner/repo.git")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "initial")
        self.worktrees = self.base / "worktrees"
        self.worktrees.mkdir()
        self.worktree = self.worktrees / "nested" / "feature"
        self.worktree.parent.mkdir()
        self.git("worktree", "add", "-qb", "feature", str(self.worktree))
        self.registry = self.base / "registry.json"

    def git(self, *arguments):
        return subprocess.run(["git", "-C", str(self.repo), *arguments], check=True, capture_output=True, text=True).stdout

    def enroll(self, *extra):
        argv = ["session_enroll.py", "--repo-root", str(self.repo),
                "--session-root", str(self.base), "--session-layout", "parent-of-repository",
                "--session-id", "fixed-session", "--registry", str(self.registry), *extra]
        with patch.object(sys, "argv", argv), patch.object(session_enroll, "verify_session") as verify, contextlib.redirect_stdout(io.StringIO()) as output:
            result = session_enroll.main()
        return result, json.loads(output.getvalue()), verify

    def test_parent_layout_records_distinct_roots_and_nested_worktrees(self):
        result, data, verify = self.enroll("--worktree-parent", str(self.worktrees))
        self.assertEqual(result, 0)
        record = json.loads(self.registry.read_text())["owner/repo"]
        self.assertEqual(record["repository_root"], str(self.repo))
        self.assertEqual(record["session_root"], str(self.base))
        self.assertEqual(record["session_layout"], "parent-of-repository")
        verify.assert_called_once_with("fixed-session", self.base)
        self.assertEqual(data["status"], "enrolled")

    def test_check_only_does_not_create_registry(self):
        result, data, _ = self.enroll("--check-only", "--worktree-parent", str(self.worktrees))
        self.assertEqual(result, 0)
        self.assertEqual(data["status"], "ready")
        self.assertFalse(self.registry.exists())
        self.assertFalse(self.registry.with_suffix(".json.lock").exists())

    def test_exact_root_without_worktree_parent_records_common_dir(self):
        argv = ["session_enroll.py", "--repo-root", str(self.repo),
                "--session-root", str(self.repo), "--session-id", "fixed-session",
                "--registry", str(self.registry)]
        with patch.object(sys, "argv", argv), patch.object(session_enroll, "verify_session"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(session_enroll.main(), 0)
        record = json.loads(self.registry.read_text())["owner/repo"]
        self.assertTrue(record["repository_common_dir"])

    def test_existing_registry_is_not_overwritten_on_invalid_json(self):
        self.registry.write_text("invalid registry")
        with self.assertRaisesRegex(RuntimeError, "registry JSON is invalid"):
            self.enroll()
        self.assertEqual(self.registry.read_text(), "invalid registry")

    def test_existing_binding_cannot_silently_move_to_parent_layout(self):
        self.registry.write_text(json.dumps({"owner/repo": {
            "repository": "owner/repo", "session_id": "fixed-session", "session_root": str(self.repo)
        }}))
        with self.assertRaisesRegex(RuntimeError, "binding.*mismatch|layout.*mismatch"):
            self.enroll()

    def test_same_origin_independent_clone_is_not_a_worktree(self):
        outsider = self.worktrees / "clone"
        subprocess.run(["git", "clone", "-q", str(self.repo), str(outsider)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(outsider), "remote", "set-url", "origin", "git@github.com:owner/repo.git"], check=True)
        with self.assertRaisesRegex(RuntimeError, "worktrees.*repository"):
            self.enroll("--worktree-parent", str(self.worktrees))

    def test_exact_root_still_accepts_external_sibling_worktree_parent(self):
        argv = ["session_enroll.py", "--worktree-parent", str(self.worktrees),
                "--session-root", str(self.repo), "--session-id", "fixed-session",
                "--registry", str(self.registry)]
        with patch.object(sys, "argv", argv), patch.object(session_enroll, "verify_session"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(session_enroll.main(), 0)


if __name__ == "__main__":
    unittest.main()
