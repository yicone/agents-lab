"""ACM-934 §8 P3-B acceptance: Routine template order, boundaries and plist.

Item 1: in the fixture environment the template's commands are executed in
order; exit codes are only 0, 3 or 10; apply-writeback is never invoked and
publish-snapshot is invoked exactly once.
Item 2: a failed push (exit 3) still lets the Routine report; push attempts
are bounded to two by the spy.
Item 3: the README states the Routine boundaries and the D6 coverage; the
launchd plist lints clean and only calls fetch-snapshot.
"""
import contextlib
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/ helpers

from support import PROJECT, REPO
from collect_support import BOT, SKILL_BODY, fixture
import data_branch_support as dbs
from boxsync.__main__ import main
from boxsync import publish as publish_mod

README = PROJECT / "README.md"
PLIST = REPO / "multica-board" / "launchd" / "one.yic.box-config-fetch.plist"
SUBCOMMANDS = {"discover", "collect", "reconcile", "publish-snapshot",
               "fetch-snapshot", "plan-writeback", "apply-writeback", "rollback"}


def template_commands():
    """Executable step-3 commands of the README Routine template, in order."""
    text = README.read_text(encoding="utf-8")
    section = text.split("### 提示词模板", 1)[1].split("###", 1)[0]
    block = re.search(r"```text\n(.*?)```", section, re.S).group(1)
    return re.findall(r"python3 box-config-sync/bin/boxsync ([a-z-]+)", block)


class RoutineFlow(unittest.TestCase):
    """Simulates a Bot faithfully executing the README Routine template."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        # The git leg matches the fixture's live tree byte for byte, so the
        # workflows domain can reach `aligned` (reconcile exit 0).
        workflows = {"example": {"SKILL.md": SKILL_BODY,
                                 "assets.txt": "Private auxiliary body."}}
        self.bare, self.clone, _mini, self.pusher = dbs.make_repos(self.base, workflows)
        self.ctx, self.home = fixture(self.base / "fx")
        # No bot profiles keeps the instructions leg empty (board is never
        # visible on Box), so a clean run is not dragged to exit 3.
        for path in (self.home / "sand-data/agents").iterdir():
            shutil.rmtree(path)
        self.state = self.base / "state"
        self.state.mkdir(mode=0o700)
        self.inbox = self.state / "inbox"
        self.inbox.mkdir(mode=0o700)
        self.snapshot = self.state / "snapshots/latest.json"
        self.sidecar = self.inbox / (BOT + ".sidecar.json")
        self.calls = []   # boxsync subcommands the routine turn invoked
        self.pushes = []  # git push argv publish attempted this turn

    def write_sidecar(self):
        doc = {"schema": "box-config-sidecar/v1", "bot_id": BOT,
               "captured_at": datetime.now(timezone.utc).isoformat(),
               "mcp_servers": [{"name": "github", "status": "connected",
                                "tool_count": 42, "origin": "plugin"}],
               "routines": [{"id": "daily-config-sync", "name": "每日配置巡检",
                             "enabled": True, "prompt_sha256": "a" * 64}]}
        self.sidecar.write_text(json.dumps(doc), encoding="utf-8")

    def invoke(self, name):
        args = {"collect": ["collect", "--offline",
                            "--fixture-home", os.fspath(self.home),
                            "--state-dir", os.fspath(self.state),
                            "--sidecar", os.fspath(self.sidecar)],
                "reconcile": ["reconcile", "--snapshot", os.fspath(self.snapshot),
                              "--repo", os.fspath(self.clone),
                              "--state-dir", os.fspath(self.state)],
                "publish-snapshot": ["publish-snapshot",
                                     "--repo", os.fspath(self.clone),
                                     "--state-dir", os.fspath(self.state)],
                "plan-writeback": ["plan-writeback",
                                   "--snapshot", os.fspath(self.snapshot),
                                   "--repo", os.fspath(self.clone),
                                   "--state-dir", os.fspath(self.state)]}[name]
        self.calls.append(name)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = main(args)
        except SystemExit as exc:  # argparse rejects an unknown subcommand
            code = exc.code if isinstance(exc.code, int) else 2
        return code, out.getvalue()

    def routine_turn(self, allow_push=True):
        """Steps 2-4 of the template as one Bot turn; returns the report."""
        original = publish_mod.run_git

        def spy(argv, **kwargs):
            if argv[0] == "push":
                self.pushes.append(list(argv))
                if not allow_push:
                    raise publish_mod.GitCommandError("error")
            return original(argv, **kwargs)

        self.pushes.clear()
        self.write_sidecar()
        report = {}
        with patch.object(publish_mod, "run_git", side_effect=spy):
            for name in template_commands():
                code, out = self.invoke(name)
                report[name] = {"exit": code, "payload": json.loads(out)}
                # §5.4 step 4: exit 10 -> plan-writeback (lands with P2-A).
                if name == "reconcile" and code == 10:
                    report["plan-writeback"] = {"exit": self.invoke("plan-writeback")[0]}
        return report

    def audit(self):
        return [json.loads(line)
                for line in (self.state / "audit.jsonl").read_text().splitlines()]

    def test_template_order_exit_codes_and_single_publish(self):
        # §8(1): fixture run exits only 0/3/10, apply-writeback is never
        # invoked, publish-snapshot is invoked exactly once.
        self.assertEqual(template_commands(),
                         ["collect", "reconcile", "publish-snapshot"])
        report = self.routine_turn()
        self.assertEqual(list(report), ["collect", "reconcile", "publish-snapshot"])
        for name, row in report.items():
            self.assertIn(row["exit"], (0, 3, 10), name)
        # Offline fixture: multica_cli is not_collected, so collect is 3;
        # the aligned legs reconcile to 0 and the snapshot is pushed.
        self.assertEqual([row["exit"] for row in report.values()], [3, 0, 0])
        self.assertEqual(self.calls.count("publish-snapshot"), 1)
        for forbidden in ("apply-writeback", "rollback"):
            self.assertNotIn(forbidden, self.calls)
        self.assertEqual(report["publish-snapshot"]["payload"]["state"], "pushed")
        self.assertIsNotNone(dbs.bare_tip(self.bare))
        self.assertFalse(self.sidecar.exists())  # our inbox consumed it

    def test_push_failure_still_reports_and_retries_once(self):
        # §8(2): push failure (exit 3) does not stop the report; the spy sees
        # at most two pushes (publish retries a non-fast-forward once).
        report = self.routine_turn()
        self.assertEqual(report["publish-snapshot"]["payload"]["state"], "pushed")
        report = self.routine_turn(allow_push=False)
        self.assertEqual(list(report), ["collect", "reconcile", "publish-snapshot"])
        self.assertEqual(report["publish-snapshot"]["exit"], 3)
        self.assertEqual(report["publish-snapshot"]["payload"]["state"], "push_failed")
        self.assertEqual(len(self.pushes), 2)
        self.assertEqual(self.audit()[-1]["result"], "push_failed")


class DeliverableDocs(unittest.TestCase):
    """§8(3): README boundaries/D6 coverage; plist lints, fetch-snapshot only."""

    def test_readme_states_routine_boundaries_and_d6(self):
        text = README.read_text(encoding="utf-8")
        for phrase in ("apply-writeback", "rollback", "git push", "gh pr",
                       "multica", "唯一的推送", "publish-snapshot"):
            self.assertIn(phrase, text)
        for phrase in ("Agent Steward", "not_collected", "未采集"):
            self.assertIn(phrase, text)

    def test_plist_lints_and_only_calls_fetch_snapshot(self):
        doc = plistlib.loads(PLIST.read_bytes())
        self.assertEqual(doc["Label"], "one.yic.box-config-fetch")
        argv = doc["ProgramArguments"]
        self.assertEqual(set(argv) & SUBCOMMANDS, {"fetch-snapshot"})
        self.assertTrue(argv[0].endswith("python3"))
        self.assertTrue(argv[1].endswith("box-config-sync/bin/boxsync"))
        plutil = shutil.which("plutil")
        if plutil is None:
            self.skipTest("plutil is only available on macOS")
        result = subprocess.run([plutil, "-lint", os.fspath(PLIST)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
