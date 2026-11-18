"""Only temporary local repositories; no GitHub or production Live inputs."""
import hashlib
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from support import PROJECT
from boxsync.gitio import ALLOWED_COMMANDS, GitError, GitReader
from boxsync.hashing import tree_sha256
from boxsync.reconcile import reconcile_workflows
from test_reconcile import NOW, STAMP, leg, skill


class GitIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve() / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.directory = self.repo / "grok-workflows/skills-user/skill-1793256615882"
        self.directory.mkdir(parents=True)
        (self.directory / "SKILL.md").write_text("---\nname: '中文 技能'\n---\nfixture body\n")
        (self.directory / "references").mkdir()
        (self.directory / "references/a.md").write_text("fixture reference\n")
        self.commit()
        self.head = self.git("rev-parse", "HEAD").stdout.decode().strip()

    def git(self, *args):
        # Identity is per invocation, never a git config mutation.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                   GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        return subprocess.run(["git", "-C", str(self.repo), *args], env=env, check=True, capture_output=True)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "Create local test fixture.")

    def workspace(self):
        return {p.relative_to(self.repo).as_posix(): (p.lstat().st_mode,
                os.readlink(p) if p.is_symlink() else p.read_bytes())
                for p in self.repo.rglob("*") if not p.is_dir() and ".git" not in p.relative_to(self.repo).parts}

    def test_pinned_bytes_tree_hash_and_command_spy_leave_workspace_untouched(self):
        (self.directory / "SKILL.md").write_text("dirty uncommitted body")
        (self.repo / "untracked").write_text("leave me")
        before = self.workspace()
        index_before = (self.repo / ".git/index").read_bytes()
        status_before = self.git("status", "--porcelain").stdout
        calls = []
        run = subprocess.run

        def spy(argv, **kwargs):
            command = argv[argv.index("-C") + 2]
            calls.append(command)
            self.assertIn(command, ALLOWED_COMMANDS)
            self.assertNotIn("GIT_DIR", kwargs["env"])
            self.assertNotIn("GIT_WORK_TREE", kwargs["env"])
            return run(argv, **kwargs)

        with patch.dict(os.environ, {"GIT_DIR": "/invalid", "GIT_WORK_TREE": "/invalid"}), patch("subprocess.run", side_effect=spy):
            result = GitReader(self.repo, self.head).workflows()
        self.assertEqual(result["state"], "ok")
        row = result["items"][0]
        self.assertEqual(row["name"], "中文 技能")
        body = self.git("show", self.head + ":grok-workflows/skills-user/skill-1793256615882/SKILL.md").stdout
        self.assertEqual(row["body_sha256"], hashlib.sha256(body).hexdigest())
        with tempfile.TemporaryDirectory() as checkout:
            root = Path(checkout).resolve()
            for item in row["files"]:
                data = self.git("show", self.head + ":grok-workflows/skills-user/skill-1793256615882/" + item["path"]).stdout
                dest = root / item["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            self.assertEqual(row["tree_sha256"], tree_sha256(root)["tree_sha256"])
        self.assertEqual(self.workspace(), before)
        self.assertEqual((self.repo / ".git/index").read_bytes(), index_before)
        self.assertEqual(self.git("status", "--porcelain").stdout, status_before)
        self.assertEqual(set(calls), {"rev-parse", "ls-tree", "cat-file"})

    def test_fetch_only_from_local_fixture_keeps_workspace(self):
        self.git("remote", "add", "origin", str(self.repo))
        before = self.workspace()
        index = (self.repo / ".git/index").read_bytes()
        original = subprocess.run
        commands = []

        def spy(argv, **kwargs):
            commands.append(argv[argv.index("-C") + 2])
            return original(argv, **kwargs)

        with patch("subprocess.run", side_effect=spy):
            GitReader(self.repo, "HEAD").fetch(ref=self.head)
        self.assertEqual(commands, ["fetch"])
        self.assertEqual(before, self.workspace())
        self.assertEqual(index, (self.repo / ".git/index").read_bytes())
        self.assertFalse((self.repo / ".git/FETCH_HEAD").exists())

    def test_reject_write_commands_and_unsafe_refs_before_process(self):
        reader = GitReader(self.repo, "HEAD")
        with patch("subprocess.run", side_effect=AssertionError("must not spawn")):
            for command in ("checkout", "reset", "commit", "push", "config", "worktree"):
                with self.assertRaises(ValueError):
                    reader._run(command)
            for ref in ("--help", "HEAD:path", "../escape", "HEAD^{tree}", "a\nb", ""):
                with self.assertRaises(ValueError):
                    GitReader(self.repo, ref)
            with self.assertRaises(ValueError):
                reader.fetch(remote="--upload-pack=bad")

    def test_failures_unknown_not_absent(self):
        for error in (GitError("denied"), GitError("too_large"), GitError("timeout"), GitError("error")):
            with self.subTest(state=error.state), patch.object(GitReader, "_run", side_effect=error):
                result = GitReader(self.repo, "HEAD").workflows()
                rows = reconcile_workflows(leg(skill()), result, collected_at=STAMP, now=NOW)
                self.assertEqual(rows[0]["state"], "unknown")
        self.assertEqual(GitReader(self.repo, "no-such-ref").workflows()["state"], "error")

    def test_proven_missing_root_is_empty_success(self):
        self.git("rm", "-qr", "grok-workflows")
        self.git("commit", "-qm", "Remove fixture workflows.")
        value = GitReader(self.repo, "HEAD").workflows()
        self.assertEqual((value["state"], value["items"]), ("ok", []))

    def test_symlink_large_file_and_missing_body_are_unknown(self):
        for mode in ("symlink", "large", "missing"):
            with self.subTest(mode=mode):
                directory = self.repo / "grok-workflows/skills-user" / mode
                directory.mkdir()
                if mode == "symlink":
                    (directory / "SKILL.md").symlink_to("../../../../outside")
                elif mode == "large":
                    (directory / "SKILL.md").write_bytes(b"x" * (512 * 1024 + 1))
                else:
                    (directory / "reference.txt").write_text("no skill body")
        self.commit()
        value = GitReader(self.repo, "HEAD").workflows()
        rows = reconcile_workflows(leg(), value, collected_at=STAMP, now=NOW)
        by = {r["dir_name"]: r for r in rows}
        for name in ("symlink", "large", "missing"):
            self.assertEqual(by[name]["state"], "unknown")

    def test_blacklist_files_not_read_and_modes_do_not_affect_hash(self):
        (self.directory / ".DS_Store").write_bytes(b"ignored")
        (self.directory / ".env").write_bytes(b"do not open")
        before = GitReader(self.repo, "HEAD").workflows()["items"][0]["tree_sha256"]
        (self.directory / "SKILL.md").chmod(0o755)
        self.commit()
        value = GitReader(self.repo, "HEAD").workflows()
        self.assertEqual(before, value["items"][0]["tree_sha256"])
        self.assertEqual([f["path"] for f in value["items"][0]["files"]], ["SKILL.md", "references/a.md"])

    def test_subprocess_timeout_and_output_limit(self):
        reader = GitReader(self.repo, "HEAD")
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 15)):
            self.assertEqual(reader.workflows()["state"], "timeout")
        with self.assertRaises(GitError) as raised:
            reader._run("ls-tree", "-r", "HEAD", limit=1)
        self.assertEqual(raised.exception.state, "too_large")

    def test_offline_cli_collect_to_reconcile_and_exit_codes(self):
        from boxsync.__main__ import main
        from boxsync.snapshot import Context, collect, write_snapshot
        from collect_support import TOOL

        state = self.repo.parent / "state"
        state.mkdir(mode=0o700)
        snapshot = state / "snapshot.json"
        ctx = Context(roots={"workflows": self.directory.parent}, offline=True,
                      selected=("workflows",), now=datetime.now(timezone.utc))
        value, _ = collect(ctx, tool=TOOL)
        write_snapshot(value, snapshot)
        args = ["reconcile", "--offline", "--snapshot", str(snapshot), "--repo", str(self.repo),
                "--git-ref", self.head, "--state-dir", str(state)]
        before = self.workspace()

        def invoke(extra=()):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), patch.object(GitReader, "fetch", side_effect=AssertionError("offline fetched")):
                code = main(args + list(extra))
            return code, json.loads(out.getvalue())

        code, report = invoke()
        self.assertEqual((code, report["summary"]), (0, {"aligned": 1}))
        self.assertEqual(self.workspace(), before)
        board = self.repo.parent / "board"
        board.mkdir()
        exported = value["domains"]["workflows"][0]
        (board / "manifest.json").write_text(json.dumps({"skills_user": {"skills": [{
            "slug": exported["dir_name"], "name": exported["name"],
            "tree_sha256": exported["tree_sha256"]}]}}))
        code, report = invoke(["--board", str(board)])
        self.assertEqual(code, 0)
        self.assertEqual(report["domains"]["workflows"][0]["join_basis"], "dir_name")
        (board / "manifest.json").write_text("bad manifest")
        code, report = invoke(["--board", str(board)])
        self.assertEqual((code, report["summary"]), (3, {"unknown": 1}))
        # Actual executable entrypoint, still pinned and offline.
        proc = subprocess.run([sys.executable, str(PROJECT / "bin/boxsync"), *args], capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["summary"], {"aligned": 1})
        value["domains"]["workflows"][0]["tree_sha256"] = "b" * 64
        write_snapshot(value, snapshot)
        self.assertEqual(invoke()[0], 10)
        value["collected_at"] = (datetime.now(timezone.utc) - timedelta(hours=37)).isoformat()
        write_snapshot(value, snapshot)
        code, report = invoke()
        self.assertEqual((code, report["summary"]), (3, {"unknown": 1}))
        self.assertIn("snapshot_stale", report["domains"]["workflows"][0]["reason_codes"])
        snapshot.write_text("{}")
        self.assertEqual(invoke()[0], 3)
        self.assertEqual(invoke(["--snapshot", str(state / ".env")])[0], 4)
        state.chmod(0o755)
        self.assertEqual(invoke()[0], 4)

    def test_hermes_profiles_only_cli_is_unknown_not_missing(self):
        from boxsync.__main__ import main
        from boxsync.snapshot import collect, write_snapshot
        from collect_support import TOOL, fixture
        ctx, _ = fixture(self.repo.parent)
        ctx.selected = ("profiles",)
        ctx.now = datetime.now(timezone.utc)
        snapshot, _ = collect(ctx, tool=TOOL)
        self.assertEqual(snapshot["sources"]["box_disk"], "ok")
        self.assertEqual(snapshot["domains"]["workflows"], [])
        state = self.repo.parent / "state"
        state.mkdir(mode=0o700)
        path = state / "snapshot.json"
        write_snapshot(snapshot, path)
        args = ["reconcile", "--offline", "--repo", str(self.repo), "--git-ref", self.head,
                "--snapshot", str(path), "--state-dir", str(state)]
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                if legacy:
                    snapshot.pop("domain_states")
                    write_snapshot(snapshot, path)
                out = io.StringIO()
                with contextlib.redirect_stdout(out), patch.object(GitReader, "fetch", side_effect=AssertionError("network")):
                    code = main(args)
                report = json.loads(out.getvalue())
                self.assertEqual(code, 3)
                self.assertEqual(report["summary"], {"unknown": 1})
                self.assertIn("live_not_collected", report["domains"]["workflows"][0]["reason_codes"])

    def test_instructions_cli_matches_matrix_and_keeps_git_read_only(self):
        from boxsync.__main__ import main
        from boxsync.snapshot import collect, write_snapshot
        from boxsync.reconcile import _query
        from collect_support import TOOL, BOT, OTHER, WORKSPACE, PROFILE_BODY, fixture
        from test_collect_multica import FakeCli
        source = _query.source
        ctx, _ = fixture(self.repo.parent)
        ctx.roots["workflows"] = self.directory.parent
        ctx.selected = ("workflows", "profiles", "multica_agents")
        ctx.offline, ctx.cli, ctx.workspaces = False, FakeCli(), (WORKSPACE,)
        ctx.now = datetime.now(timezone.utc)
        snapshot, _ = collect(ctx, tool=TOOL)
        agents = [{"id": BOT, "workspace_id": WORKSPACE, "instructions": "Private instructions never exported."}]
        bots = [{"id": bot, "description": PROFILE_BODY} for bot in (BOT, OTHER)]
        root = self.repo / "instructions-governance"
        source.seed_sources(root, agents, bots)
        board_doc = source.export_ui(root)
        self.commit()
        head = self.git("rev-parse", "HEAD").stdout.decode().strip()
        state = self.repo.parent / "state"
        state.mkdir(mode=0o700)
        path = state / "snapshot.json"
        write_snapshot(snapshot, path)
        board = self.repo.parent / "board"
        board.mkdir()
        workflow = snapshot["domains"]["workflows"][0]
        (board / "manifest.json").write_text(json.dumps({"skills_user": {"skills": [{
            "slug": workflow["dir_name"], "tree_sha256": workflow["tree_sha256"]}]}}))
        (board / "instruction-sources.json").write_text(json.dumps(board_doc))
        args = ["reconcile", "--offline", "--repo", str(self.repo), "--git-ref", head,
                "--snapshot", str(path), "--state-dir", str(state), "--board", str(board)]
        stamp = snapshot["collected_at"]
        expected = _query.matrix(root, board_doc, agents, bots,
                                  observed_at={"agent": stamp, "bot": stamp},
                                  freshness={"agent": "fresh", "bot": "fresh", "board": "fresh"})
        # Change uncommitted sources to prove that CLI reads the pinned Git objects.
        (root / "sources/index.json").write_text("dirty index must not be read")
        before, index = self.workspace(), (self.repo / ".git/index").read_bytes()
        original = subprocess.run
        commands = []

        def spy(argv, **kwargs):
            command = argv[argv.index("-C") + 2]
            self.assertIn(command, ALLOWED_COMMANDS)
            commands.append(command)
            return original(argv, **kwargs)

        def invoke():
            out = io.StringIO()
            with contextlib.redirect_stdout(out), patch("subprocess.run", side_effect=spy), patch.object(
                    GitReader, "fetch", side_effect=AssertionError("network forbidden")):
                code = main(args)
            return code, json.loads(out.getvalue())

        code, report = invoke()
        self.assertEqual(code, 0)
        self.assertEqual(report["domains"]["instructions"], expected["items"])
        self.assertEqual(report["domain_summaries"], {"workflows": {"aligned": 1}, "instructions": {"aligned": 3}})
        self.assertEqual(commands.count("rev-parse"), 1)  # Both domains use the same commit.
        self.assertEqual(before, self.workspace())
        self.assertEqual(index, (self.repo / ".git/index").read_bytes())
        for body in (PROFILE_BODY, agents[0]["instructions"]):
            self.assertNotIn(body, json.dumps(report))
        snapshot["domains"]["multica_agents"][0]["instructions_sha256"] = "b" * 64
        write_snapshot(snapshot, path)
        code, report = invoke()
        self.assertEqual(code, 10)
        self.assertEqual(report["domain_summaries"]["instructions"], {"modified": 1, "aligned": 2})
        snapshot["domain_states"]["profiles"] = "not_collected"
        write_snapshot(snapshot, path)
        code, report = invoke()
        self.assertEqual(code, 3)
        self.assertEqual(report["domain_summaries"]["instructions"], {"modified": 1, "stale": 2})
        (board / "instruction-sources.json").write_text("bad JSON")
        self.assertEqual(invoke()[0], 3)
        board_doc["bots"][BOT]["text"] = 123
        (board / "instruction-sources.json").write_text(json.dumps(board_doc))
        self.assertEqual(invoke()[0], 3)
        with patch.object(GitReader, "instructions", return_value={"state": "denied", "agents": [], "bots": []}):
            self.assertEqual(invoke()[0], 3)

    def test_instruction_sources_failure_is_not_absence_and_withheld_not_read(self):
        root = self.repo / "instructions-governance/sources"
        root.mkdir(parents=True)
        index = {"agents": [], "bots": [
            {"id": "missing", "path": "missing.md"},
            {"id": "withheld", "path": ".env", "withheld": True},
            {"id": "traversal", "path": "../../../outside"},
            {"id": "symlink", "path": "link.md"}]}
        (root / "index.json").write_text(json.dumps(index))
        (root / "link.md").symlink_to("../../../../outside")
        self.commit()
        reader = GitReader(self.repo, "HEAD")
        result = reader.instructions()
        self.assertEqual(result["state"], "partial")
        self.assertEqual(len(result["bots"]), 4)
        self.assertTrue(all(row["sha256"] is None for row in result["bots"]))
        with patch.object(reader, "_run", side_effect=GitError("denied")):
            self.assertEqual(reader.instructions()["state"], "denied")


if __name__ == "__main__":
    unittest.main()
