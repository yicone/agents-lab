import contextlib
import copy
import importlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collect_support import BOT, NOW, TOOL, fixture, sidecar, TOKEN
from support import PROJECT
from boxsync.__main__ import main
from boxsync.snapshot import collect, write_snapshot, validate_snapshot
from boxsync.validation import InvalidDocument, load_schema, validate


class CollectCliTests(unittest.TestCase):
    def run_main(self, args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(args)
        return code, json.loads(out.getvalue())

    def test_offline_end_to_end_permissions_consumption_and_no_multica(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            ctx, home = fixture(tmp)
            state = Path(tmp) / "state"
            state.mkdir(mode=0o700)
            inbox = state / "inbox"
            inbox.mkdir(mode=0o700)
            source = inbox / (BOT + ".sidecar.json")
            sc = sidecar()
            from datetime import datetime, timezone
            sc["captured_at"] = datetime.now(timezone.utc).isoformat()
            source.write_text(json.dumps(sc))
            with patch("boxsync.multica_cli.subprocess.Popen", side_effect=AssertionError("offline invoked CLI")):
                # Git metadata uses subprocess.run internally, so isolate that local read.
                with patch("boxsync.snapshot.tool_metadata", return_value=TOOL):
                    code, result = self.run_main(["collect", "--offline", "--fixture-home", str(home),
                                                  "--state-dir", str(state), "--sidecar", str(source)])
            self.assertEqual(code, 3)  # offline is explicitly not_collected, not failed
            self.assertEqual(result["sources"]["multica_cli"], "not_collected")
            self.assertEqual(result["sources"]["sidecar"], "ok")
            self.assertFalse(source.exists())
            snapshot = state / "snapshots/latest.json"
            self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(snapshot.parent.stat().st_mode), 0o700)
            self.assertEqual(json.loads(snapshot.read_text()), result)

    def test_rejected_and_external_sidecars_retained_and_stdin_supported(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            _, home = fixture(tmp)
            state = Path(tmp) / "state"
            source = Path(tmp) / "external.json"
            sc = sidecar()
            source.write_text(json.dumps(sc))
            args = ["collect", "--offline", "--fixture-home", str(home), "--state-dir", str(state)]
            code, result = self.run_main(args + ["--sidecar", str(source)])
            self.assertEqual(code, 3)
            self.assertTrue(source.exists())
            source.write_text('{"prompt":"must not escape"}')
            code, result = self.run_main(args + ["--sidecar", str(source)])
            self.assertEqual(result["sources"]["sidecar"], "rejected_schema")
            self.assertTrue(source.exists())
            with patch("sys.stdin", type("Input", (), {"buffer": io.BytesIO(json.dumps(sc).encode())})()):
                code, result = self.run_main(args + ["--sidecar", "-"])
            self.assertEqual(code, 3)
            self.assertIn(result["sources"]["sidecar"], ("ok", "stale_sidecar"))

    def test_state_and_output_rejections_are_safe_before_reads(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            _, home = fixture(tmp)
            state = Path(tmp) / "state"
            state.mkdir(mode=0o755)
            args = ["collect", "--offline", "--fixture-home", str(home), "--state-dir", str(state)]
            with patch("boxsync.snapshot.collect", side_effect=AssertionError("unsafe state read source")):
                code, value = self.run_main(args)
            self.assertEqual(code, 4)
            state.chmod(0o700)
            target = Path(tmp) / "target"
            target.write_text("untouched")
            output = state / "link"
            output.symlink_to(target)
            code, value = self.run_main(args + ["--out", str(output)])
            self.assertEqual(code, 4)
            self.assertEqual(target.read_text(), "untouched")
            code, value = self.run_main(args + ["--out", str(home / "snapshot.json")])
            self.assertEqual(code, 4)
            self.assertFalse((home / "snapshot.json").exists())

    def test_selected_domains_and_entrypoint(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            _, home = fixture(tmp)
            state = Path(tmp) / "state"
            proc = subprocess.run([sys.executable, str(PROJECT / "bin/boxsync"), "collect", "--offline",
                                   "--fixture-home", str(home), "--state-dir", str(state),
                                   "--domains", "workflows"], capture_output=True, text=True, timeout=5)
            self.assertEqual(proc.returncode, 3)
            self.assertEqual(proc.stderr, "")
            result = json.loads(proc.stdout)
            self.assertTrue(result["domains"]["workflows"])
            self.assertEqual(result["domains"]["profiles"], [])
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["collect", "--domains", "unknown"])

    def test_validator_rejects_unknown_keys_bad_types_and_cloud_hash_incoherence(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            ctx, _ = fixture(tmp)
            result, _ = collect(ctx, tool=TOOL)
            for mutate in (
                lambda v: v.update(prompt="must not escape"),
                lambda v: v["domains"]["profiles"][0].update(description="must not escape"),
                lambda v: v["policy"]["skips"].update(bad=True),
                lambda v: v["tool"].update(commit="invalid"),
            ):
                bad = copy.deepcopy(result)
                mutate(bad)
                with self.assertRaises(InvalidDocument):
                    validate_snapshot(bad)
            schema = load_schema("snapshot")
            row = {"id": "multica-skill:" + BOT + "/" + BOT, "workspace_id": BOT, "skill_id": BOT,
                   "name": None, "updated_at": None, "api_content_hash": None, "body_state": "observed",
                   "body_sha256": None, "body_bytes": 1, "files": [], "files_truncated": False,
                   "collected_at": None, "agents": []}
            with self.assertRaises(InvalidDocument):
                validate(row, schema["$defs"]["cloudSkill"], schema)

    def test_import_order_does_not_change_sys_path(self):
        for imports in (("secrets_scan", "hashing"), ("hashing", "secrets_scan")):
            script = ("import sys; sys.path.insert(0, " + repr(str(PROJECT)) + "); before=list(sys.path); " +
                      "; ".join("import boxsync." + name for name in imports) +
                      "; assert sys.path == before; assert boxsync.secrets_scan.contains_secret('token=decoy')")
            proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=5)
            self.assertEqual(proc.returncode, 0, proc.stderr)
