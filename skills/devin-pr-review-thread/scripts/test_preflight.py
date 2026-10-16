import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from preflight import (
    DISCOVERY_STRATEGY,
    MODEL_COMMAND_TIMEOUT_SECONDS,
    SESSION_LIST_COMMAND_TIMEOUT_SECONDS,
    classify_github_error,
    pr_identity,
    GITHUB_IDENTITY_TIMEOUT_SECONDS,
    listed_session,
    session_database_root,
    session_lookup_roots,
    session_state,
)


class GithubErrorClassificationTests(unittest.TestCase):
    def test_identity_recovers_from_transport_timeout(self):
        with patch("preflight.run", side_effect=[(127, "", "TimeoutExpired"), (0, '{"headRefOid":"abc"}', "")]) as command:
            result, error, _ = pr_identity("/repo", 263, "owner/repo")
        self.assertIsNone(error)
        self.assertEqual(result["head_sha"], "abc")
        self.assertEqual(command.call_count, 2)
        self.assertEqual(command.call_args.kwargs, {"cwd": "/repo", "timeout": GITHUB_IDENTITY_TIMEOUT_SECONDS})

    def test_identity_transport_retry_is_bounded(self):
        with patch("preflight.run", return_value=(127, "", "TimeoutExpired")) as command:
            _, error, detail = pr_identity("/repo", 263, "owner/repo")
        self.assertEqual(error, "github_transport_unavailable")
        self.assertEqual(command.call_count, 2)
        self.assertIn("attempts=2", detail)

    def test_identity_does_not_retry_auth_or_invalid_json(self):
        for value in [(1, "", "HTTP 403 forbidden via proxy"), (127, "", "FileNotFoundError"), (0, "not-json", "")]:
            with patch("preflight.run", return_value=value) as command:
                _, error, _ = pr_identity("/repo", 263, "owner/repo")
            self.assertIsNotNone(error)
            self.assertEqual(command.call_count, 1)

    def test_transport_errors_are_distinct(self):
        self.assertEqual(classify_github_error(1, 'Post "https://api.github.com/graphql": EOF'), "github_transport_unavailable")
        self.assertEqual(classify_github_error(1, "proxy connection refused"), "github_transport_unavailable")

    def test_non_transport_cli_failure_is_identity_failure(self):
        self.assertEqual(classify_github_error(1, "GraphQL: Could not resolve to a PullRequest"), "pr_not_found_or_forbidden")

    def test_model_catalog_has_a_longer_timeout_than_default_commands(self):
        self.assertGreaterEqual(MODEL_COMMAND_TIMEOUT_SECONDS, 60)

    def test_session_list_has_a_host_sized_timeout(self):
        self.assertGreaterEqual(SESSION_LIST_COMMAND_TIMEOUT_SECONDS, 30)


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
            ["devin", "list", "--format", "json"],
            cwd="/tmp/worktree",
            timeout=SESSION_LIST_COMMAND_TIMEOUT_SECONDS,
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
                "preflight.git_common_dir", return_value="/common"), patch(
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

    def test_parent_layout_is_reported_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            session = base / "project"
            repo = session / "repo"
            worktrees = session / ".worktrees"
            worktree = worktrees / "feature"
            for path in (session, repo, worktrees, worktree):
                path.mkdir()
            registry = base / "registry.json"
            registry.write_text(json.dumps({
                "owner/repo": {
                    "repository": "owner/repo",
                    "repository_root": str(repo),
                    "repository_common_dir": "/common",
                    "session_id": "rounded-brochure",
                    "session_root": str(session),
                    "session_layout": "parent-of-repository",
                    "enrolled_parent": str(worktrees),
                }
            }))
            worktree_list = f"worktree {repo}\n\nworktree {worktree}\n\n"
            with patch("preflight.session_database_root", return_value=None), patch(
                "preflight.git_common_dir", return_value="/common"), patch(
                "preflight.run",
                side_effect=[
                    (0, worktree_list, ""),
                    (0, "[]", ""),
                    (0, '[{"id":"rounded-brochure","working_directory":"%s"}]' % session, ""),
                ],
            ):
                state, error = session_state(str(repo), "owner/repo", registry)
            self.assertIsNone(error)
            self.assertEqual(state["session_layout"], "parent-of-repository")
            self.assertEqual(state["registered_root"], str(session.resolve()))

    def test_parent_layout_database_hint_drift_is_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            session = base / "project"; repo = session / "repo"; worktrees = session / ".worktrees"
            session.mkdir(); repo.mkdir(); worktrees.mkdir()
            registry = base / "registry.json"
            registry.write_text(json.dumps({"owner/repo": {
                "repository": "owner/repo", "repository_root": str(repo),
                "repository_common_dir": "/common", "session_id": "s",
                "session_root": str(session), "session_layout": "parent-of-repository",
                "enrolled_parent": str(worktrees)}}))
            with patch("preflight.session_database_root", return_value=str(worktrees / "moved")), patch(
                "preflight.git_common_dir", return_value="/common"), patch("preflight.run"):
                state, error = session_state(str(repo), "owner/repo", registry)
            self.assertEqual(error, "session_working_directory_drift")

    def test_repository_common_dir_mismatch_stops_before_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            repo = base / "repo"; repo.mkdir()
            registry = base / "registry.json"
            registry.write_text(json.dumps({"owner/repo": {
                "repository": "owner/repo", "repository_root": str(repo),
                "repository_common_dir": str(base / "registered-common"),
                "session_id": "s", "session_root": str(repo),
                "session_layout": "exact-root"}}))
            with patch("preflight.git_common_dir", return_value=str(base / "different-common")), patch(
                "preflight.session_database_root", return_value=None), patch("preflight.run") as command:
                state, error = session_state(str(repo), "owner/repo", registry)
            self.assertEqual(error, "repository_boundary_mismatch")
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
