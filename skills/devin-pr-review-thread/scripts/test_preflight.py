import pathlib
import tempfile
import unittest
from unittest.mock import patch

from preflight import DISCOVERY_STRATEGY, classify_github_error, session_lookup_roots


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


if __name__ == "__main__":
    unittest.main()
