"""Publisher failure recovery, dry-run isolation, and launchd contract."""

import io
import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from run_metrics import pipeline
from run_metrics.collect import default_runner


def snapshot():
    return {
        "collected_at": "2026-11-12T14:00:00Z",
        "mode": "fixture",
        "issues": [{
            "id": "fixture-issue", "created_at": "2026-11-12T13:00:00Z",
            "updated_at": "2026-11-12T13:00:00Z", "workspace_id": "fixture-workspace",
        }],
        "runs": [{
            "id": "fixture-run", "issue_id": "fixture-issue", "status": "completed",
            "started_at": "2026-11-12T13:00:00Z", "completed_at": "2026-11-12T13:01:00Z",
        }],
    }


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "snapshot.json"
        self.input.write_text(json.dumps(snapshot()))
        self.target = self.root / "board" / pipeline.REPORT_NAME

    def run_pipeline(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = pipeline.main(["--output", str(self.target), *map(str, args)])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_dry_run_does_not_create_output_directory_or_call_api(self):
        with patch.object(pipeline, "collect_snapshot", side_effect=AssertionError("API called")):
            code, output, _ = self.run_pipeline("--snapshot", self.input, "--dry-run")
        self.assertEqual(code, 0)
        summary = json.loads(output)
        self.assertFalse(summary["written"])
        self.assertEqual(summary["runs"], 1)
        self.assertFalse(self.target.parent.exists())

    def test_atomic_publication_and_permissions(self):
        code, output, _ = self.run_pipeline("--snapshot", self.input)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["written"])
        report = json.loads(self.target.read_text())
        self.assertEqual(report["schema_version"], "run-metrics.v0.1")
        self.assertEqual(set(report["windows"]), {"24h", "7d", "14d"})
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(list(self.target.parent.iterdir())), 2)  # JSON and stable lock inode

    def test_failed_replace_keeps_old_report_and_cleans_temp(self):
        self.target.parent.mkdir()
        self.target.write_text("old report")
        with patch.object(pipeline.os, "replace", side_effect=OSError("disk failure")):
            code, _, error = self.run_pipeline("--snapshot", self.input)
        self.assertEqual(code, 1)
        self.assertIn("disk failure", error)
        self.assertEqual(self.target.read_text(), "old report")
        self.assertEqual(len(list(self.target.parent.iterdir())), 2)

    def test_bad_snapshot_preserves_existing_report(self):
        self.target.parent.mkdir()
        self.target.write_text("old report")
        self.input.write_text('{"collected_at":"invalid"}')
        self.assertEqual(self.run_pipeline("--snapshot", self.input)[0], 1)
        self.assertEqual(self.target.read_text(), "old report")

    def test_dry_run_preserves_existing_report(self):
        self.target.parent.mkdir()
        self.target.write_text("old report")
        before = self.target.stat().st_mtime_ns
        self.assertEqual(self.run_pipeline("--snapshot", self.input, "--dry-run")[0], 0)
        self.assertEqual(self.target.read_text(), "old report")
        self.assertEqual(self.target.stat().st_mtime_ns, before)
        self.assertEqual(list(self.target.parent.iterdir()), [self.target])

    def test_lock_rejects_overlap_before_collection(self):
        with pipeline.publication_lock(self.target):
            with patch.object(pipeline, "collect_snapshot", side_effect=AssertionError("API called")):
                self.assertEqual(self.run_pipeline()[0], 1)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.run_pipeline("--snapshot", self.input)[0], 0)

    def test_live_collection_is_used_without_snapshot(self):
        with patch.object(pipeline, "collect_snapshot", return_value=snapshot()) as collect:
            self.assertEqual(self.run_pipeline("--dry-run", "--workers", "2")[0], 0)
        collect.assert_called_once_with(workers=2)

    def test_fatal_collection_failure_keeps_previous_report(self):
        self.target.parent.mkdir()
        self.target.write_text("old report")
        with patch.object(pipeline, "collect_snapshot", side_effect=RuntimeError("CLI unavailable")):
            code, _, error = self.run_pipeline()
        self.assertEqual(code, 1)
        self.assertIn("CLI unavailable", error)
        self.assertEqual(self.target.read_text(), "old report")

    def test_output_cannot_overwrite_snapshot(self):
        original = self.input.read_bytes()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            pipeline.main(["--snapshot", str(self.input), "--output", str(self.input)])
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(self.input.read_bytes(), original)

    def test_partial_gaps_remain_explicit(self):
        data = snapshot()
        data["collection_errors"] = ["fixture gap"]
        self.input.write_text(json.dumps(data))
        code, output, _ = self.run_pipeline("--snapshot", self.input)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["partial"])
        self.assertEqual(json.loads(self.target.read_text())["collection"]["errors"], ["fixture gap"])

    def test_cli_uses_exact_runtime_executable(self):
        executable = "/a path/Multica CLI"
        with patch.dict(os.environ, {"MULTICA_CLI_PATH": executable}):
            with patch("run_metrics.collect.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "[]", "")) as run:
                default_runner(["runtime", "list"])
        self.assertEqual(run.call_args.args[0], [executable, "runtime", "list", "--output", "json"])

    def test_launchd_template_is_one_shot_and_uses_exact_cli(self):
        path = Path(__file__).parents[1] / "launchd" / "one.yic.multica-run-metrics.plist"
        template = plistlib.loads(path.read_bytes())
        self.assertEqual(template["StartInterval"], 900)
        self.assertNotIn("KeepAlive", template)
        self.assertEqual(template["EnvironmentVariables"]["MULTICA_CLI_PATH"], "__CLI__")
        self.assertIn("run_metrics.pipeline", template["ProgramArguments"])
        self.assertTrue(template["ProgramArguments"][-1].endswith("/.multica-board/" + pipeline.REPORT_NAME))


if __name__ == "__main__":
    unittest.main()
