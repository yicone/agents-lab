import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "skill_guard.py"


class SkillGuardTests(unittest.TestCase):
    def run_guard(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            text=True,
            capture_output=True,
        )

    def test_snapshot_diff_gate_and_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            adapter = Path(directory) / "adapters"
            skill = root / "demo"
            skill.mkdir(parents=True)
            adapter.mkdir()
            (skill / "SKILL.md").write_text(
                "---\nname: demo\ndescription: safe\n---\nUse locally.\n",
                encoding="utf-8",
            )
            (skill / "references").mkdir()
            (skill / "references" / "guide.md").write_text(
                "baseline\n", encoding="utf-8"
            )
            (adapter / "demo").symlink_to(skill, target_is_directory=True)
            snapshot = Path(directory) / "snapshot"
            made = self.run_guard(
                "snapshot",
                "--skill-root",
                str(root),
                "--adapter-root",
                str(adapter),
                "--source-class",
                "third_party_unmanaged",
                "--output",
                str(snapshot),
            )
            self.assertEqual(made.returncode, 0, made.stderr)
            self.assertIn('"adapters": 1', made.stdout)
            (skill / "SKILL.md").write_text(
                "---\nname: demo\ndescription: changed\n---\nUse curl https://example.test.\n",
                encoding="utf-8",
            )
            blocked = self.run_guard(
                "diff", "--snapshot", str(snapshot), "--gate", "--format", "json"
            )
            self.assertEqual(blocked.returncode, 2, blocked.stdout)
            report = json.loads(blocked.stdout)
            self.assertTrue(report["blocked"])
            self.assertIn("network_behavior", report["signals"])
            restored = self.run_guard(
                "rollback", "--snapshot", str(snapshot), "--force"
            )
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertIn(
                "Use locally.",
                (skill / "SKILL.md").read_text(encoding="utf-8"),
            )
            self.assertTrue((adapter / "demo").is_symlink())

            (adapter / "demo").unlink()
            (adapter / "demo").symlink_to(root / "other", target_is_directory=True)
            adapter_diff = self.run_guard(
                "diff", "--snapshot", str(snapshot), "--gate", "--format", "json"
            )
            self.assertEqual(adapter_diff.returncode, 2, adapter_diff.stdout)
            self.assertIn(
                "adapter_paths", json.loads(adapter_diff.stdout)["signals"]
            )

    def test_clean_diff_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            root.mkdir()
            (root / "SKILL.md").write_text(
                "---\nname: demo\n---\n", encoding="utf-8"
            )
            snapshot = Path(directory) / "snapshot"
            made = self.run_guard(
                "snapshot",
                "--skill-root",
                str(root),
                "--source-class",
                "local_custom",
                "--output",
                str(snapshot),
            )
            self.assertEqual(made.returncode, 0, made.stderr)
            clean = self.run_guard("diff", "--snapshot", str(snapshot), "--gate")
            self.assertEqual(clean.returncode, 0, clean.stdout)
            self.assertIn("Decision: ALLOW", clean.stdout)

    def test_body_change_does_not_fake_trigger_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            root.mkdir()
            skill = root / "SKILL.md"
            skill.write_text(
                "---\nname: demo\ndescription: use when needed\n---\nFirst body.\n",
                encoding="utf-8",
            )
            snapshot = Path(directory) / "snapshot"
            made = self.run_guard(
                "snapshot",
                "--skill-root",
                str(root),
                "--source-class",
                "local_custom",
                "--output",
                str(snapshot),
            )
            self.assertEqual(made.returncode, 0, made.stderr)
            skill.write_text(
                "---\nname: demo\ndescription: use when needed\n---\nSecond body.\n",
                encoding="utf-8",
            )
            report = self.run_guard(
                "diff", "--snapshot", str(snapshot), "--format", "json"
            )
            self.assertEqual(report.returncode, 0, report.stdout)
            signals = json.loads(report.stdout)["changes"][0]["signals"]
            self.assertNotIn("trigger_changes", signals)
