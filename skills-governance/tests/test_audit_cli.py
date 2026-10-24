"""Audit CLI regression coverage for explicit, repeated scan roots."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class AuditCliTests(unittest.TestCase):
    def test_repeated_external_scan_roots_are_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            home = root / "home"
            home.mkdir()
            inventory = root / "inventory.json"
            inventory.write_text(json.dumps({"schema": "skills-inventory/v1", "skills": []}))
            lock = root / "lock.json"
            lock.write_text(json.dumps({"version": 1, "skills": {}}))
            roots = [root / "external-a", root / "external-b"]
            names = ["external-fixture", "external-fixture"]
            inputs = [inventory, lock]
            for scan_root, name in zip(roots, names):
                skill = scan_root / name
                skill.mkdir(parents=True)
                markdown = skill / "SKILL.md"
                markdown.write_text(f"---\nname: {name}\ndescription: Use when inspecting {name}.\n---\nFixture {scan_root.name}.\n")
                inputs.append(markdown)
            before = {path: path.read_bytes() for path in inputs}
            for script in ("drift_report.py", "conflict_report.py", "token_footprint.py"):
                with self.subTest(script=script):
                    command = [sys.executable, str(SCRIPTS / script),
                               "--inventory", str(inventory), "--repo-root", str(repo),
                               "--no-default-roots", "--format", "json"]
                    for scan_root in roots:
                        command.extend(["--scan-root", str(scan_root)])
                    if script == "drift_report.py":
                        command.extend(["--lock", str(lock)])
                    result = subprocess.run(command, capture_output=True, text=True,
                                            env={**os.environ, "HOME": str(home)})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    report = json.loads(result.stdout)
                    self.assertTrue(report["read_only"])
                    if script == "conflict_report.py":
                        self.assertEqual(report["summary"]["name_collision"], 1)
                    rendered = json.dumps(report)
                    for name in names:
                        self.assertIn(name, rendered)
                    self.assertEqual({path: path.read_bytes() for path in inputs}, before)


if __name__ == "__main__":
    unittest.main()
