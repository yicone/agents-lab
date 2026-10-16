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

    def _write_skill(self, root: Path, body: str, extra=None):
        root.mkdir(parents=True, exist_ok=True)
        (root / "SKILL.md").write_text(body, encoding="utf-8")
        for relative, content in (extra or {}).items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def test_stage_then_gate_does_not_load_live_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            live = Path(directory) / "live"
            candidate = Path(directory) / "candidate"
            staging = Path(directory) / "staging"
            snapshot = Path(directory) / "snapshot"
            original = "---\nname: demo\ndescription: safe\n---\nUse locally.\n"
            self._write_skill(live, original, {"notes.txt": "baseline\n"})
            self._write_skill(
                candidate,
                "---\nname: demo\ndescription: changed\n---\nUse curl https://example.test.\n",
                {"notes.txt": "baseline\n"},
            )
            made = self.run_guard(
                "snapshot",
                "--skill-root",
                str(live),
                "--source-class",
                "skills_cli_managed",
                "--output",
                str(snapshot),
            )
            self.assertEqual(made.returncode, 0, made.stderr)
            staged = self.run_guard(
                "stage",
                "--skill-root",
                str(candidate),
                "--live-root",
                str(live),
                "--snapshot",
                str(snapshot),
                "--output",
                str(staging),
            )
            self.assertEqual(staged.returncode, 0, staged.stderr)
            self.assertEqual((live / "SKILL.md").read_text(encoding="utf-8"), original)
            blocked = self.run_guard(
                "diff",
                "--snapshot",
                str(snapshot),
                "--staging",
                str(staging),
                "--gate",
                "--format",
                "json",
            )
            self.assertEqual(blocked.returncode, 2, blocked.stdout)
            self.assertIn("network_behavior", json.loads(blocked.stdout)["signals"])
            self.assertEqual((live / "SKILL.md").read_text(encoding="utf-8"), original)
            refused = self.run_guard("activate", "--staging", str(staging))
            self.assertEqual(refused.returncode, 2, refused.stdout)
            self.assertEqual((live / "SKILL.md").read_text(encoding="utf-8"), original)
            forced = self.run_guard("activate", "--staging", str(staging), "--force")
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertIn(
                "Use curl https://example.test.",
                (live / "SKILL.md").read_text(encoding="utf-8"),
            )

    def test_activate_after_clean_staging_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            live = Path(directory) / "live"
            candidate = Path(directory) / "candidate"
            staging = Path(directory) / "staging"
            snapshot = Path(directory) / "snapshot"
            body = "---\nname: demo\n---\nStable.\n"
            self._write_skill(live, body, {"notes.txt": "before\n"})
            self._write_skill(candidate, body, {"notes.txt": "after\n"})
            made = self.run_guard(
                "snapshot",
                "--skill-root",
                str(live),
                "--source-class",
                "local_custom",
                "--output",
                str(snapshot),
            )
            self.assertEqual(made.returncode, 0, made.stderr)
            staged = self.run_guard(
                "stage",
                "--skill-root",
                str(candidate),
                "--live-root",
                str(live),
                "--snapshot",
                str(snapshot),
                "--output",
                str(staging),
            )
            self.assertEqual(staged.returncode, 0, staged.stderr)
            allowed = self.run_guard(
                "diff",
                "--snapshot",
                str(snapshot),
                "--staging",
                str(staging),
                "--gate",
            )
            self.assertEqual(allowed.returncode, 0, allowed.stdout)
            self.assertIn("Decision: ALLOW", allowed.stdout)
            self.assertEqual((live / "notes.txt").read_text(encoding="utf-8"), "before\n")
            activated = self.run_guard("activate", "--staging", str(staging))
            self.assertEqual(activated.returncode, 0, activated.stderr)
            self.assertEqual((live / "notes.txt").read_text(encoding="utf-8"), "after\n")
            self.assertEqual((live / "SKILL.md").read_text(encoding="utf-8"), body)

    def test_stage_refuses_live_path_as_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            live = Path(directory) / "live"
            live.mkdir()
            (live / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
            refused = self.run_guard(
                "stage",
                "--skill-root",
                str(live),
                "--live-root",
                str(live),
                "--output",
                str(Path(directory) / "staging"),
            )
            self.assertEqual(refused.returncode, 1, refused.stdout)
            self.assertIn("never the runtime directory", refused.stderr)
