import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collect_support import BOT, OTHER, WORKSPACE, TOOL, TOKEN, CONFIG_VALUE, fixture
from boxsync.multica_cli import CliError, CliRunner, CommandNotAllowed, assert_read_only
from boxsync.snapshot import collect


class FakeCli:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def run(self, args, workspace_id=None):
        assert_read_only(args)
        self.calls.append((args, workspace_id))
        if workspace_id == self.fail:
            raise CliError("auth")
        if args == ["agent", "list"]:
            return [{"id": BOT}]
        if args[:2] == ["agent", "get"]:
            return {"id": BOT, "name": "Agent", "instructions": "Private instructions never exported.",
                    "mcp_config": {"mcpServers": {"Server": {"key": TOKEN}}}, "custom_env": {"A": CONFIG_VALUE},
                    "runtime_config": {"B": CONFIG_VALUE}}
        if args[:3] == ["agent", "mcp", "list"]:
            return [{"id": "server", "name": "Server", "enabled": True, "mcp_config": {"key": TOKEN}}]
        if args == ["skill", "list"]:
            return [{"id": OTHER, "name": "Cloud Skill", "content": "Private cloud content"}]
        raise AssertionError("unexpected CLI call")


class MulticaTests(unittest.TestCase):
    def test_fake_cli_drops_values_and_leaves_cloud_body_uncollected(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            ctx, _ = fixture(tmp)
            ctx.offline, ctx.cli, ctx.workspaces = False, FakeCli(), (WORKSPACE,)
            result, _ = collect(ctx, tool=TOOL)
            text = json.dumps(result)
            for private in (TOKEN, CONFIG_VALUE, "Private instructions never exported.", "Private cloud content",
                            "mcp_config", "custom_env", "runtime_config"):
                self.assertNotIn(private, text)
            self.assertEqual(result["sources"]["multica_cli"], "ok")
            self.assertEqual(result["domains"]["cloud_skills"][0]["body_state"], "uncollected")
            self.assertFalse(any("--with-content" in args for args, _ in ctx.cli.calls))

    def test_auth_error_marks_inaccessible_and_skips_skill_collection(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            ctx, _ = fixture(tmp)
            ctx.offline, ctx.cli, ctx.workspaces = False, FakeCli(fail=WORKSPACE), (WORKSPACE,)
            result, _ = collect(ctx, tool=TOOL)
            self.assertEqual(result["inaccessible_workspaces"], [{"workspace_id": WORKSPACE, "reason": "auth"}])
            self.assertEqual(result["sources"]["multica_cli"], "partial")
            self.assertEqual(result["domains"]["cloud_skills"], [])
            self.assertEqual(ctx.cli.calls, [(["agent", "list"], WORKSPACE)])

    def test_foreign_workspace_and_incomplete_pages_are_not_success(self):
        class BadCli(FakeCli):
            def run(self, args, workspace_id=None):
                if args == ["agent", "list"]:
                    return [{"id": BOT, "workspace_id": OTHER}]
                return super().run(args, workspace_id)
        from boxsync.domains.multica_agents import records
        with self.assertRaises(CliError):
            records({"agents": [], "has_more": True}, "agents")
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            ctx, _ = fixture(tmp)
            ctx.offline, ctx.cli, ctx.workspaces = False, BadCli(), (WORKSPACE,)
            result, _ = collect(ctx, tool=TOOL)
            self.assertEqual(result["sources"]["multica_cli"], "partial")
            self.assertEqual(result["domains"]["multica_agents"], [])
            self.assertEqual(result["inaccessible_workspaces"][0]["reason"], "error")

    def test_full_argv_grammar_rejects_before_spawn(self):
        runner = CliRunner(interval=0)
        bad = [["agent", "update", BOT], ["skill", "list", "--delete"], ["agent", "list", "--profile=x"],
               ["--profile", "owner", "agent", "list"], ["skill", "get", BOT, "--content-file", "file"],
               ["agent", "get", BOT, "update"], ["agent", "list", "--help"], ["auth", "status"]]
        with patch("subprocess.Popen", side_effect=AssertionError("spawned")):
            for args in bad:
                with self.subTest(args=args), self.assertRaises(CommandNotAllowed):
                    runner.run(args)
            with self.assertRaises(CommandNotAllowed):
                runner.run(["agent", "list"], "--profile=owner")
            with self.assertRaises(CliError):
                CliRunner(offline=True).run(["agent", "list"])

    def test_real_fake_binary_bounds_env_cwd_auth_timeout_and_json(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            binary = Path(tmp) / "fake-cli"
            def install(body):
                binary.write_text("#!/usr/bin/env python3\n" + body)
                binary.chmod(0o700)
            runner = CliRunner(binary=str(binary), interval=0)
            install("import os,json,sys\nassert not any(k.startswith('MULTICA_') for k in os.environ)\n"
                    "assert not os.listdir('.')\nassert sys.argv[-2:]==['--output','json']\nprint('[]')\n")
            with patch.dict(os.environ, {"MULTICA_TOKEN": TOKEN, "MULTICA_TASK_ID": "private"}):
                self.assertEqual(runner.run(["agent", "list"], WORKSPACE), [])
            install("import sys\nprint('unauthorized ' + " + repr(TOKEN) + ", file=sys.stderr)\nsys.exit(1)\n")
            with self.assertRaises(CliError) as caught:
                runner.run(["agent", "list"])
            self.assertEqual(str(caught.exception), "auth")
            install("print('x' * 10000)\n")
            with patch("boxsync.multica_cli.MAX_OUTPUT", 100), self.assertRaises(CliError) as caught:
                runner.run(["agent", "list"])
            self.assertEqual(str(caught.exception), "too_large")
            install("import time\ntime.sleep(2)\n")
            runner.timeout = 0.1
            with self.assertRaises(CliError) as caught:
                runner.run(["agent", "list"])
            self.assertEqual(str(caught.exception), "timeout")
            runner.timeout = 5
            install("print('not json')\n")
            with self.assertRaises(CliError):
                runner.run(["agent", "list"])
