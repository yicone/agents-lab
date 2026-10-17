import hashlib
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
            changelog = Path(directory) / "changelog"
            refused = self.run_guard(
                "activate",
                "--staging",
                str(staging),
                "--changelog-dir",
                str(changelog),
            )
            self.assertEqual(refused.returncode, 2, refused.stdout)
            self.assertEqual((live / "SKILL.md").read_text(encoding="utf-8"), original)
            forced = self.run_guard(
                "activate",
                "--staging",
                str(staging),
                "--force",
                "--changelog-dir",
                str(changelog),
            )
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
            activated = self.run_guard(
                "activate",
                "--staging",
                str(staging),
                "--changelog-dir",
                str(Path(directory) / "changelog"),
            )
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

    def write_fake_skil_lock(self, directory: Path, mode: str) -> Path:
        binary = directory / "skil-lock"
        binary.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import sys",
                    "from pathlib import Path",
                    f"mode = {mode!r}",
                    "args = sys.argv[1:]",
                    "if args and args[0] == 'init':",
                    "    out = args[args.index('-o') + 1]",
                    "    Path(out).write_text('skills.lock\\n', encoding='utf-8')",
                    "    sys.exit(0)",
                    "if args and args[0] == 'ci':",
                    "    if mode == 'error':",
                    "        print('error: scan failed', file=sys.stderr)",
                    "        sys.exit(2)",
                    "    root = Path(args[1])",
                    "    text = []",
                    "    for path in root.rglob('*'):",
                    "        if path.is_file():",
                    "            text.append(path.read_text(encoding='utf-8', errors='replace'))",
                    "    if 'UNAPPROVED_CAPABILITY' in '\\n'.join(text):",
                    "        print('BLOCK: 1 of 1 entries at severity >= medium')",
                    "        sys.exit(1)",
                    "    print('PASS: no capability deltas')",
                    "    sys.exit(0)",
                    "sys.exit(2)",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        binary.chmod(0o755)
        return binary

    def test_missing_skil_lock_records_risk_signal_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "skills"
            root.mkdir()
            (root / "SKILL.md").write_text("---\nname: demo\n---\nStable.\n", encoding="utf-8")
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
            report = self.run_guard(
                "diff",
                "--snapshot",
                str(snapshot),
                "--skil-lock",
                "fallback",
                "--gate",
                "--format",
                "json",
            )
            self.assertEqual(report.returncode, 0, report.stdout)
            gate = json.loads(report.stdout)["capability_gate"]
            self.assertEqual(gate["backend"], "fallback")
            self.assertEqual(gate["fallback"], "skill_guard risk signals")
            self.assertIn("disabled", gate["reason"])
            self.assertFalse(gate["blocked"])

    def test_skil_lock_blocks_capability_drift_when_risk_signals_allow(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            binary = self.write_fake_skil_lock(base, "pass")
            live = base / "live"
            candidate = base / "candidate"
            staging = base / "staging"
            snapshot = base / "snapshot"
            body = "---\nname: demo\n---\nStable.\n"
            self._write_skill(live, body, {"notes.txt": "before\n"})
            self._write_skill(
                candidate, body, {"notes.txt": "UNAPPROVED_CAPABILITY\n"}
            )
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
            blocked = self.run_guard(
                "diff",
                "--snapshot",
                str(snapshot),
                "--staging",
                str(staging),
                "--skil-lock",
                str(binary),
                "--gate",
                "--format",
                "json",
            )
            self.assertEqual(blocked.returncode, 2, blocked.stdout)
            payload = json.loads(blocked.stdout)
            self.assertEqual(payload["signals"], ["capability_drift"])
            self.assertEqual(payload["capability_gate"]["backend"], "skil-lock")
            self.assertTrue(payload["capability_gate"]["blocked"])
            self.assertIn("BLOCK:", payload["capability_gate"]["summary"])
            refused = self.run_guard(
                "activate",
                "--staging",
                str(staging),
                "--skil-lock",
                str(binary),
                "--changelog-dir",
                str(base / "changelog"),
            )
            self.assertEqual(refused.returncode, 2, refused.stdout)
            self.assertEqual((live / "notes.txt").read_text(encoding="utf-8"), "before\n")

    def test_skil_lock_failure_blocks_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            binary = self.write_fake_skil_lock(base, "error")
            root = base / "skills"
            root.mkdir()
            (root / "SKILL.md").write_text("---\nname: demo\n---\nStable.\n", encoding="utf-8")
            snapshot = base / "snapshot"
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
            report = self.run_guard(
                "diff",
                "--snapshot",
                str(snapshot),
                "--skil-lock",
                str(binary),
                "--gate",
                "--format",
                "json",
            )
            self.assertEqual(report.returncode, 2, report.stdout)
            payload = json.loads(report.stdout)
            self.assertIn("capability_gate_error", payload["signals"])
            self.assertTrue(payload["capability_gate"]["error"])
            self.assertTrue(payload["capability_gate"]["blocked"])


    def _stage_candidate(self, base: Path, live: Path, candidate: Path) -> Path:
        snapshot = base / "snapshot"
        made = self.run_guard(
            "snapshot",
            "--skill-root",
            str(live),
            "--source-class",
            "local_custom",
            "--output",
            str(snapshot),
            "--force",
        )
        self.assertEqual(made.returncode, 0, made.stderr)
        staging = base / "staging"
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
            "--force",
        )
        self.assertEqual(staged.returncode, 0, staged.stderr)
        return staging

    def test_activate_appends_changelog_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            live = base / "live"
            candidate = base / "candidate"
            changelog = base / "changelog"
            old_body = "---\nname: demo\n---\nStable.\n"
            new_body = "---\nname: demo\n---\nUpdated.\n"
            self._write_skill(live, old_body, {"notes.txt": "before\n"})
            self._write_skill(
                candidate,
                new_body,
                {"notes.txt": "after\n", "extra.txt": "new\n"},
            )
            staging = self._stage_candidate(base, live, candidate)
            activated = self.run_guard(
                "activate",
                "--staging",
                str(staging),
                "--force",
                "--changelog-dir",
                str(changelog),
                "--change-summary",
                "reviewed update",
                "--new-ref",
                "abc123",
            )
            self.assertEqual(activated.returncode, 0, activated.stderr)
            log = changelog / "demo.jsonl"
            lines = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(
                json.loads(activated.stdout)["changelog"],
                [str(log.resolve())],
            )
            entry = json.loads(lines[0])
            self.assertEqual(entry["schema"], "skill-governance-changelog/v1")
            self.assertEqual(entry["skill"], "demo")
            digest = lambda path: "sha256:" + hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            self.assertIsNone(entry["old_version"])
            self.assertIsNone(entry["new_version"])
            self.assertNotEqual(entry["old_hash"], entry["new_hash"])
            self.assertEqual(entry["new_hash"], digest(candidate / "SKILL.md"))
            self.assertEqual(entry["new_hash"], digest(live / "SKILL.md"))
            self.assertEqual(
                entry["old_hash"],
                "sha256:" + hashlib.sha256(old_body.encode()).hexdigest(),
            )
            self.assertEqual(entry["files"]["added"], ["extra.txt"])
            self.assertEqual(
                entry["files"]["modified"], ["SKILL.md", "notes.txt"]
            )
            self.assertEqual(entry["files"]["removed"], [])
            self.assertEqual(entry["summary"], "reviewed update")
            self.assertIn("modified 2, added 1, removed 0", entry["auto_summary"])
            self.assertEqual(entry["new_ref"], "abc123")
            self.assertTrue(entry["forced"])
            self.assertEqual(entry["staging"], str(staging.resolve()))

    def test_second_activation_appends_second_line(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            live = base / "live"
            candidate = base / "candidate"
            changelog = base / "changelog"
            self._write_skill(live, "---\nname: demo\n---\nv1.\n")
            self._write_skill(candidate, "---\nname: demo\n---\nv2.\n")
            staging = self._stage_candidate(base, live, candidate)
            first = self.run_guard(
                "activate",
                "--staging",
                str(staging),
                "--force",
                "--changelog-dir",
                str(changelog),
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self._write_skill(candidate, "---\nname: demo\n---\nv3.\n")
            staging = self._stage_candidate(base, live, candidate)
            second = self.run_guard(
                "activate",
                "--staging",
                str(staging),
                "--force",
                "--changelog-dir",
                str(changelog),
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            lines = (changelog / "demo.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(lines), 2)
            first_entry = json.loads(lines[0])
            second_entry = json.loads(lines[1])
            self.assertEqual(second_entry["old_hash"], first_entry["new_hash"])

    def test_multi_skill_root_logs_only_changed_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            live = base / "live"
            candidate = base / "candidate"
            changelog = base / "changelog"
            body_a = "---\nname: a\n---\nAlpha.\n"
            self._write_skill(live / "a", body_a)
            self._write_skill(live / "b", "---\nname: b\n---\nBefore.\n")
            self._write_skill(candidate / "a", body_a)
            self._write_skill(candidate / "b", "---\nname: b\n---\nAfter.\n")
            staging = self._stage_candidate(base, live, candidate)
            activated = self.run_guard(
                "activate",
                "--staging",
                str(staging),
                "--force",
                "--changelog-dir",
                str(changelog),
            )
            self.assertEqual(activated.returncode, 0, activated.stderr)
            self.assertFalse((changelog / "a.jsonl").exists())
            lines = (changelog / "b.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["skill"], "b")

    def test_force_activation_logs_forced(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            live = base / "live"
            candidate = base / "candidate"
            changelog = base / "changelog"
            body = "---\nname: demo\n---\nStable.\n"
            self._write_skill(live, body, {"notes.txt": "before\n"})
            self._write_skill(candidate, body, {"notes.txt": "after\n"})
            staging = self._stage_candidate(base, live, candidate)
            activated = self.run_guard(
                "activate",
                "--staging",
                str(staging),
                "--force",
                "--changelog-dir",
                str(changelog),
            )
            self.assertEqual(activated.returncode, 0, activated.stderr)
            entry = json.loads(
                (changelog / "demo.jsonl").read_text(encoding="utf-8").strip()
            )
            self.assertTrue(entry["forced"])
            self.assertEqual(entry["files"]["modified"], ["notes.txt"])
            self.assertEqual(entry["old_hash"], entry["new_hash"])

    def test_blocked_activation_writes_no_changelog(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            live = base / "live"
            candidate = base / "candidate"
            changelog = base / "changelog"
            self._write_skill(live, "---\nname: demo\n---\nStable.\n")
            self._write_skill(
                candidate,
                "---\nname: demo\ndescription: changed\n---\nChanged.\n",
            )
            staging = self._stage_candidate(base, live, candidate)
            refused = self.run_guard(
                "activate",
                "--staging",
                str(staging),
                "--changelog-dir",
                str(changelog),
            )
            self.assertEqual(refused.returncode, 2, refused.stdout)
            self.assertFalse((changelog / "demo.jsonl").exists())

    def write_behavior_skil_lock(self, directory: Path, lock_text: str) -> Path:
        binary = directory / "skil-lock"
        binary.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import sys",
                    "from pathlib import Path",
                    "lock = " + repr(lock_text),
                    "args = sys.argv[1:]",
                    "if args and args[0] == 'init':",
                    "    out = args[args.index('-o') + 1]",
                    "    Path(out).write_text(lock, encoding='utf-8')",
                    "    sys.exit(0)",
                    "if args and args[0] == 'ci':",
                    "    print('PASS: no capability deltas')",
                    "    sys.exit(0)",
                    "sys.exit(2)",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        binary.chmod(0o755)
        return binary

    def test_declaration_overreach_blocks_activation_and_changelog(self):
        self.assert_network_url_overreach_blocks("        - https://evil.example/hook")

    def test_same_indent_network_url_list_still_blocks_overreach(self):
        self.assert_network_url_overreach_blocks("      - https://evil.example/hook")

    def assert_network_url_overreach_blocks(self, url_item: str):
        lock = "\n".join(
            [
                'schema_version: "0.1"',
                "skills:",
                "  demo:",
                "    behavior:",
                "      shell_commands: []",
                "      network_urls:",
                url_item,
                "      file_reads: []",
                "      file_writes: []",
                "      allowed_tools: []",
                "      bundled_scripts: []",
                "",
            ]
        )
        body = "\n".join(
            [
                "---",
                "name: demo",
                "capabilities:",
                "  network: false",
                "  secrets: false",
                "  filesystem: none",
                "---",
                "Stable.",
                "",
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            binary = self.write_behavior_skil_lock(base, lock)
            live = base / "live"
            candidate = base / "candidate"
            changelog = base / "changelog"
            live.mkdir()
            candidate.mkdir()
            (live / "SKILL.md").write_text(body, encoding="utf-8")
            (candidate / "SKILL.md").write_text(body, encoding="utf-8")
            snapshot = base / "snapshot"
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
            report = self.run_guard(
                "diff",
                "--snapshot",
                str(snapshot),
                "--skil-lock",
                str(binary),
                "--gate",
                "--format",
                "json",
            )
            self.assertEqual(report.returncode, 2, report.stdout)
            payload = json.loads(report.stdout)
            self.assertEqual(payload["signals"], ["capability_overreach"])
            self.assertFalse(payload["capability_gate"]["blocked"])
            self.assertTrue(payload["capability_gate"]["behavior_measured"])
            messages = payload["capability_gate"]["declaration"]["violations"][0]["messages"]
            self.assertTrue(any("network_urls" in message for message in messages))
            staging = base / "staging"
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
            refused = self.run_guard(
                "activate",
                "--staging",
                str(staging),
                "--skil-lock",
                str(binary),
                "--changelog-dir",
                str(changelog),
            )
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
            self.assertIn("Stable.", (live / "SKILL.md").read_text(encoding="utf-8"))
            self.assertFalse(changelog.exists())
