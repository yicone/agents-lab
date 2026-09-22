#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from repository_binding import BindingError, load_origin_entry, normalize_origin, validate_worktree_boundary


class RepositoryBindingTests(unittest.TestCase):
    def test_normalizes_supported_github_remotes(self):
        self.assertEqual(normalize_origin("git@github.com:yicone/yr-monorepo.git"), "yicone/yr-monorepo")
        self.assertEqual(normalize_origin("https://github.com/yicone/yr-monorepo.git"), "yicone/yr-monorepo")

    def test_rejects_ambiguous_or_unsafe_remotes(self):
        for remote in (
            "https://user:secret@github.com/a/b.git",
            "https://github.com:443/a/b.git",
            "https://github.com/a/b?x=1",
            "git@gitlab.com:a/b.git",
            "git@github.com:a/b/c.git",
        ):
            with self.subTest(remote=remote):
                with self.assertRaises(BindingError):
                    normalize_origin(remote)

    def test_resolves_entry_and_rejects_duplicate_session_references(self):
        entry = {"repository": "yicone/yr-monorepo", "session_id": "s1", "session_root": "/repo"}
        self.assertIs(load_origin_entry({"yicone/yr-monorepo": entry}, "git@github.com:yicone/yr-monorepo.git"), entry)
        with self.assertRaises(BindingError):
            load_origin_entry({
                "yicone/yr-monorepo": entry,
                "other/repo": {"repository": "other/repo", "session_id": "s1", "session_root": "/other"},
            }, "yicone/yr-monorepo")

    def test_rejects_mismatched_repository_field(self):
        with self.assertRaises(BindingError):
            load_origin_entry({"yicone/yr-monorepo": {"repository": "other/repo", "session_id": "s1"}}, "yicone/yr-monorepo")

    def test_rejects_legacy_root_registry_until_migrated(self):
        with self.assertRaises(BindingError):
            load_origin_entry({"/Users/tr/Workspace/yr": {"session_id": "s1"}}, "yicone/yr-monorepo")

    def test_worktree_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = pathlib.Path(temp)
            root = parent / "main"
            nested = root / "worktree"
            outside = pathlib.Path(tempfile.mkdtemp())
            root.mkdir(); nested.mkdir()
            self.assertTrue(validate_worktree_boundary(nested, root))
            self.assertFalse(validate_worktree_boundary(parent / "sibling", root))
            self.assertFalse(validate_worktree_boundary(outside, root, parent / "main-parent"))


if __name__ == "__main__":
    unittest.main()
