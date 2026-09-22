import unittest

from preflight import classify_github_error


class GithubErrorClassificationTests(unittest.TestCase):
    def test_transport_errors_are_distinct(self):
        self.assertEqual(classify_github_error(1, 'Post "https://api.github.com/graphql": EOF'), "github_transport_unavailable")
        self.assertEqual(classify_github_error(1, "proxy connection refused"), "github_transport_unavailable")

    def test_non_transport_cli_failure_is_identity_failure(self):
        self.assertEqual(classify_github_error(1, "GraphQL: Could not resolve to a PullRequest"), "pr_not_found_or_forbidden")


if __name__ == "__main__":
    unittest.main()
