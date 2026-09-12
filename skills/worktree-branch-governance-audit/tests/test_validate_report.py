import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
FIXTURES = SKILL_DIR / "tests" / "fixtures"
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from validate_report import validate_report  # noqa: E402


class ValidateReportFixturesTest(unittest.TestCase):
    def diagnostics_for(self, name):
        return validate_report(FIXTURES / name)

    def test_valid_fixtures(self):
        for name in (
            "valid-core.md", "valid-deployment.md", "valid-no-findings.md",
            "valid-fork-only.md", "valid-runtime-only.md",
        ):
            with self.subTest(name=name):
                self.assertEqual([], self.diagnostics_for(name))

    def test_invalid_fixtures_report_specific_codes(self):
        expected = {
            "invalid-legacy-main-role.md": "profile.unknown-key",
            "invalid-literal-branch-role.md": "profile.invalid-role",
            "invalid-wildcard-source.md": "source.wildcard",
            "invalid-unknown-high-confidence.md": "profile.unknown-confidence",
            "invalid-finding-destination.md": "finding.missing-field",
            "invalid-missing-task-constraints.md": "section.missing",
            "invalid-incomplete-fork-extension.md": "profile.incomplete-extension",
            "invalid-deployment-binding-confidence.md": "profile.unknown-key",
            "invalid-enforcement-gap-fields.md": "finding.missing-field",
            "invalid-compound-source.md": "source.compound",
        }
        for name, code in expected.items():
            with self.subTest(name=name):
                self.assertIn(code, {diagnostic.code for diagnostic in self.diagnostics_for(name)})


if __name__ == "__main__":
    unittest.main()
