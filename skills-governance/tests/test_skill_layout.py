import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import skill_layout


class PinnedBinaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.binary = Path(self.temp.name) / "skil-lock"
        self.binary.write_text("#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o755)
        self.version = (
            f"skil-lock {skill_layout.EXPECTED_SKIL_LOCK_VERSION} "
            f"(commit {skill_layout.EXPECTED_SKIL_LOCK_COMMIT}, built test)\n"
        )

    def result(self, output=None, code=0):
        return subprocess.CompletedProcess([], code, self.version if output is None else output, "")

    def test_default_checks_pinned_version(self):
        with patch.dict(os.environ, {"SKIL_LOCK_BINARY": ""}), patch.object(
            skill_layout, "default_skil_lock", return_value=self.binary
        ), patch.object(skill_layout.subprocess, "run", return_value=self.result()) as run:
            self.assertEqual(skill_layout.resolve_skil_lock(None), (str(self.binary), None))
            self.assertEqual(run.call_args.args[0], [str(self.binary), "version"])

    def test_cli_overrides_environment(self):
        with patch.dict(os.environ, {"SKIL_LOCK_BINARY": "/missing"}), patch.object(
            skill_layout.subprocess, "run", return_value=self.result()
        ):
            self.assertEqual(skill_layout.resolve_skil_lock(str(self.binary)), (str(self.binary), None))

    def test_environment_selects_binary(self):
        with patch.dict(os.environ, {"SKIL_LOCK_BINARY": str(self.binary)}), patch.object(
            skill_layout.subprocess, "run", return_value=self.result()
        ):
            self.assertEqual(skill_layout.resolve_skil_lock(None), (str(self.binary), None))

    def test_missing_default_never_uses_path(self):
        with patch.dict(os.environ, {"SKIL_LOCK_BINARY": "", "PATH": self.temp.name}), patch.object(
            skill_layout, "default_skil_lock", return_value=Path(self.temp.name) / "missing"
        ):
            binary, preset = skill_layout.resolve_skil_lock(None)
            self.assertIsNone(binary)
            self.assertEqual(preset["backend"], "fallback")
            self.assertIn("not executable", preset["reason"])

    def test_invalid_provenance_falls_back(self):
        for result in [self.result("skil-lock dev (commit none, built unknown)"),
                       self.result(self.version.replace("2fe41285", "9fe41285")),
                       self.result(code=1)]:
            with self.subTest(result=result), patch.object(skill_layout.subprocess, "run", return_value=result):
                binary, preset = skill_layout.resolve_skil_lock(str(self.binary))
                self.assertIsNone(binary)
                self.assertIn("mismatch", preset["reason"])
                self.assertFalse(preset["blocked"])

    def test_check_timeout_falls_back(self):
        with patch.object(skill_layout.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 5)):
            binary, preset = skill_layout.resolve_skil_lock(str(self.binary))
            self.assertIsNone(binary)
            self.assertIn("TimeoutExpired", preset["reason"])

    def test_disabled_and_relative_paths_do_not_execute(self):
        with patch.object(skill_layout.subprocess, "run") as run:
            for value in ["fallback", "skil-lock"]:
                binary, preset = skill_layout.resolve_skil_lock(value)
                self.assertIsNone(binary)
                self.assertEqual(preset["backend"], "fallback")
            run.assert_not_called()

    def test_contract_cli_records_mismatch_without_measuring(self):
        import json
        script = Path(skill_layout.__file__).with_name("capability_contract.py")
        inventory = Path(self.temp.name) / "inventory.json"
        inventory.write_text('{"skills": []}')
        result = subprocess.run(
            [sys.executable, str(script), "--inventory", str(inventory),
             "--repo-root", self.temp.name, "--skil-lock", str(self.binary),
             "--format", "json"], text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["observation"], "unmeasured")
        self.assertEqual(report["backend"]["backend"], "fallback")
        self.assertIn("mismatch", report["backend"]["reason"])
