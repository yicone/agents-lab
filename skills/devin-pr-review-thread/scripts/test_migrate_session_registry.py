import unittest

from migrate_session_registry import migrate


class MigrationTests(unittest.TestCase):
    def test_migrates_multiple_legacy_roots(self):
        result = migrate({
            "/workspace/yr": {"repository": "yicone/yr-monorepo", "session_id": "succinct-avenue"},
            "/workspace/agents-lab": {"repository": "yicone/agents-lab", "session_id": "graceful-echinacea"},
        })
        self.assertEqual(set(result), {"yicone/yr-monorepo", "yicone/agents-lab"})
        self.assertEqual(result["yicone/yr-monorepo"]["session_root"], "/workspace/yr")

    def test_preserves_v2_and_converts_legacy(self):
        result = migrate({
            "yicone/agent-steward": {"repository": "yicone/agent-steward", "session_id": "grandiose-file"},
            "/workspace/yr": {"repository": "yicone/yr-monorepo", "session_id": "succinct-avenue"},
        })
        self.assertEqual(result["yicone/agent-steward"]["session_id"], "grandiose-file")

    def test_rejects_duplicate_session(self):
        with self.assertRaisesRegex(RuntimeError, "multiple origins"):
            migrate({
                "/workspace/one": {"repository": "one/repo", "session_id": "same"},
                "/workspace/two": {"repository": "two/repo", "session_id": "same"},
            })


if __name__ == "__main__":
    unittest.main()
