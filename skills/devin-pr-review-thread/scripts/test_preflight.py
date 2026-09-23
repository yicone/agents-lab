import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from preflight import (
    DISCOVERY_STRATEGY,
    classify_github_error,
    listed_session,
    session_database_root,
    session_lookup_roots,
    session_state,
)


class GithubErrorClassificationTests(unittest.TestCase):
    def test_transport_errors_are_distinct(self):
        self.assertEqual(classify_github_error(1, 'Post "https://api.github.com/graphql": EOF'), "github_transport_unavailable")
        self.assertEqual(classify_github_error(1, "proxy connection refused"), "github_transport_unavailable")

    def test_non_transport_cli_failure_is_identity_failure(self):
        self.assertEqual(classify_github_error(1, "GraphQL: Could not resolve to a PullRequest"), "pr_not_found_or_forbidden")


class SessionDiscoveryTests(unittest.TestCase):
    def test_worktree_with_different_head_is_still_a_session_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            main = str(pathlib.Path(d) / "main")
            worktree = str(pathlib.Path(d) / "worktree")
            porcelain = f"worktree {main}\n\nworktree {worktree}\n\n"
            with patch("preflight.run", return_value=(0, porcelain, "")):
                roots = session_lookup_roots(main, main, str(pathlib.Path(d).resolve()))
            self.assertIn(str(pathlib.Path(worktree).resolve()), roots)
            self.assertEqual(DISCOVERY_STRATEGY, "db-hinted-sequential-worktrees-v2")

    def test_database_hint_is_read_only_and_returns_workspace(self):
        class FakeConnection:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def execute(self, *_): return self
            def fetchone(self): return ("/tmp/nested-worktree",)

        with patch("preflight.sqlite3.connect", return_value=FakeConnection()):
            self.assertEqual(session_database_root("succinct-avenue"), str(pathlib.Path("/tmp/nested-worktree").resolve()))

    def test_listed_session_is_single_workspace_call(self):
        with patch("preflight.run", return_value=(0, '[{"id":"succinct-avenue"}]', "")) as mocked:
            sessions, error = listed_session("/tmp/worktree", "succinct-avenue")
        self.assertEqual(sessions, [{"id": "succinct-avenue"}])
        self.assertIsNone(error)
        mocked.assert_called_once_with(
            ["devin", "list", "--format", "json"], cwd="/tmp/worktree", timeout=4
        )

    def test_session_state_follows_workspace_drift_without_head_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            main = base / "main"
            worktree = base / "worktree"
            main.mkdir()
            worktree.mkdir()
            registry = base / "registry.json"
            registry.write_text(json.dumps({
                "owner/repo": {
                    "repository": "owner/repo",
                    "session_id": "succinct-avenue",
                    "session_root": str(main),
                    "enrolled_parent": str(base),
                }
            }))
            worktree_list = f"worktree {main}\n\nworktree {worktree}\n\n"
            with patch("preflight.session_database_root", return_value=None), patch(
                "preflight.run",
                side_effect=[
                    (0, worktree_list, ""),
                    (0, "[]", ""),
                    (0, '[{"id":"succinct-avenue","working_directory":"%s"}]' % worktree, ""),
                ],
            ):
                state, error = session_state(str(main), "owner/repo", registry)
            self.assertIsNone(error)
            self.assertEqual(state["observed_root"], str(worktree.resolve()))
            self.assertEqual(state["registered_root"], str(main.resolve()))


if __name__ == "__main__":
    unittest.main()
