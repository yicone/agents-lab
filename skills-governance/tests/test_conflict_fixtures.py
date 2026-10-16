import importlib.util
import json
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills-governance" / "scripts" / "conflict_report.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "conflicts"
INVENTORY = REPO / "skills-governance" / "config" / "skills-inventory.json"


def load_report():
    spec = importlib.util.spec_from_file_location("conflict_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RepresentativeConflictFixtures(unittest.TestCase):
    def setUp(self):
        self.module = load_report()
        document = json.loads(INVENTORY.read_text(encoding="utf-8"))
        self.report = self.module.build_report(document, REPO, [])
        self.fixtures = sorted(FIXTURES.glob("*.json"))

    def findings_for(self, names):
        wanted = set(names)
        return [finding for finding in self.report["findings"] if set(finding["skills"]) == wanted]

    def test_fixture_files_cover_the_representative_pairs(self):
        ids = []
        for path in self.fixtures:
            fixture = json.loads(path.read_text(encoding="utf-8"))
            ids.append(fixture["id"])
        self.assertEqual(
            ids,
            ["governance-manager-overlap", "logseq-acns-vault-vs-write"],
        )

    def test_fixtures_match_the_live_conflict_report(self):
        self.assertTrue(self.report["read_only"])
        before = INVENTORY.read_bytes()
        for path in self.fixtures:
            fixture = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(fixture=fixture["id"]):
                self.assertEqual(len(fixture["skills"]), 2)
                self.assertTrue(fixture["summary"].strip())
                self.assertTrue(fixture["boundary"].strip())
                self.assertIn("ACM-136", fixture["summary"])
                for relative, snippets in fixture["sources"].items():
                    text = (REPO / relative).read_text(encoding="utf-8")
                    for snippet in snippets:
                        self.assertIn(snippet, text)
                self.assertEqual(self.findings_for(fixture["skills"]), fixture["findings"])
        self.assertEqual(INVENTORY.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
