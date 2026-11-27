"""ACM-930: deterministic metadata-only plans and private, read-only dry runs."""
import contextlib
import copy
import hashlib
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from support import PROJECT, REPO
from boxsync.__main__ import main
from boxsync.gitio import GitReader
from boxsync.hashing import tree_sha256
from boxsync.plan import canonical, expired, plan_sha256, plan_writeback, validate_plan
from boxsync.safe_io import UnsafeIO
from boxsync.snapshot import Context, collect, validate_snapshot
from boxsync.validation import InvalidDocument, load_schema

NOW = datetime(2026, 11, 27, 9, tzinfo=timezone.utc)
BOT = "00000000-0000-4000-8000-000000000001"
OLD = b"---\nname: Example\n---\nOld fixture prose.\n"
NEW = b"---\nname: Example\n---\nNew private fixture prose.\n"


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.tracked = self.repo / "grok-workflows/skills-user/example"
        self.tracked.mkdir(parents=True)
        (self.tracked / "SKILL.md").write_bytes(OLD)
        (self.tracked / "notes.txt").write_text("Old notes.\n")
        self.commit()
        self.sha = self.git("rev-parse", "HEAD").stdout.decode().strip()
        self.live = self.root / "home/sand-data/workflows"
        (self.live / "example").mkdir(parents=True)
        (self.live / "example/SKILL.md").write_bytes(NEW)
        (self.live / "example/notes.txt").write_text("Old notes.\n")
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.doc = self.snapshot()

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                   GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              capture_output=True, check=True, env=env)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "Create local plan fixture.")

    def snapshot(self):
        ctx = Context(roots={"workflows": self.live}, selected=("workflows",),
                      offline=True, now=NOW)
        doc, _ = collect(ctx, tool={"commit": self.sha, "dirty": False, "version": "0.2.0"})
        validate_snapshot(doc)
        return doc

    def plan(self, **kwargs):
        return plan_writeback(kwargs.pop("snapshot", self.doc), GitReader(self.repo, self.sha),
                              self.live, kwargs.pop("state_dir", self.state), now=kwargs.pop("now", NOW), **kwargs)

    def test_repeat_inputs_have_identical_hash_in_different_output_directories(self):
        other = self.root / "other-state"
        other.mkdir(mode=0o700)
        first = self.plan()
        self.assertEqual(first, self.plan())
        second = self.plan(state_dir=other)
        self.assertEqual(first, second)
        self.assertEqual(first["plan_sha256"], plan_sha256(first))
        # All scalar leaves outside the self-referential digest affect the hash.
        def visit(value, path=()):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key != "plan_sha256":
                        yield from visit(child, path + (key,))
            elif isinstance(value, list):
                for i, child in enumerate(value):
                    yield from visit(child, path + (i,))
            else:
                yield path
        for path in visit(first):
            changed = copy.deepcopy(first)
            target = changed
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = "modified value"
            self.assertNotEqual(plan_sha256(changed), first["plan_sha256"], path)

    def test_schema_is_exact_approved_schema_and_jsonschema_accepts_plan(self):
        approved = subprocess.run(["git", "-C", str(REPO), "show",
            "482432f:multica-board/docs/schemas/box-config-writeback-plan.schema.json"],
            check=True, capture_output=True).stdout
        self.assertEqual(json.loads(approved), load_schema("writeback-plan"))
        plan = self.plan()
        validate_plan(plan)
        try:
            import jsonschema
        except ImportError:
            self.skipTest("optional jsonschema not installed")
        jsonschema.Draft202012Validator(load_schema("writeback-plan"),
                                       format_checker=jsonschema.FormatChecker()).validate(plan)

    def test_git_to_box_uses_host_maintained_fingerprint_branch(self):
        fingerprint = self.live / ".library-fingerprint"
        fingerprint.write_text('{"example":"' + "a" * 64 + '"}')
        before = fingerprint.read_bytes()
        plan = self.plan(direction="git-to-box")
        item = plan["items"][0]
        self.assertEqual(item["writer"], "boxsync_fs")
        self.assertEqual(item["target_kind"], "box_workflow")
        self.assertEqual(item["expected_live_sha256"], tree_sha256(self.live / "example")["tree_sha256"])
        self.assertEqual(item["after_sha256"], tree_sha256(self.tracked)["tree_sha256"])
        self.assertEqual(fingerprint.read_bytes(), before)

    def test_w1_requires_changeset_and_cannot_be_applied_by_cli(self):
        plan = self.plan(direction="git-to-box", fingerprint_format_confirmed=False, bot_id=BOT)
        self.assertEqual((plan["action"], plan["direction"]), ("changeset-only", "changeset-only"))
        item = plan["items"][0]
        self.assertEqual((item["writer"], item["changeset"]["tool"]), ("bot_turn", "UpdateSkill"))
        self.assertTrue(item["changeset"]["source_ref"].startswith(self.sha + ":"))
        del item["changeset"]
        plan["plan_sha256"] = plan_sha256(plan)
        with self.assertRaises(InvalidDocument):
            validate_plan(plan)

    def test_create_null_and_individual_file_operations(self):
        (self.live / "new-skill").mkdir()
        (self.live / "new-skill/SKILL.md").write_bytes(NEW)
        (self.live / "example/notes.txt").unlink()
        (self.live / "example/extra.md").write_text("Added note.\n")
        plan = self.plan(snapshot=self.snapshot(), allow_create=True, allow_delete=True)
        created = next(i for i in plan["items"] if i["op"] == "create")
        self.assertIsNone(created["expected_live_sha256"])
        self.assertIsNone(created["expected_git_sha256"])
        self.assertEqual({f["op"] for i in plan["items"] for f in i["files"]}, {"create", "update", "delete"})
        validate_plan(plan)
        created["expected_live_sha256"] = "a" * 64
        plan["plan_sha256"] = plan_sha256(plan)
        with self.assertRaises(InvalidDocument):
            validate_plan(plan)

    def test_new_live_directory_since_snapshot_is_discovered(self):
        (self.live / "new-skill").mkdir()
        (self.live / "new-skill/SKILL.md").write_bytes(NEW)
        plan = self.plan(allow_create=True)
        self.assertEqual({i["target_id"] for i in plan["items"]},
                         {"grok-workflow:example", "grok-workflow:new-skill"})

    def test_selection_is_exact_and_unknown_id_is_refused(self):
        with self.assertRaises(ValueError):
            self.plan(select=["grok-workflow:does-not-exist"])
        self.assertEqual(list(self.state.iterdir()), [])
        plan = self.plan(select=["grok-workflow:example"])
        self.assertEqual([i["target_id"] for i in plan["items"]], ["grok-workflow:example"])

    def test_snapshot_digest_binds_original_bytes(self):
        raw = json.dumps(self.doc, indent=2).encode()
        plan = self.plan(snapshot_bytes=raw)
        self.assertEqual(plan["base"]["snapshot_sha256"], hashlib.sha256(raw).hexdigest())
        changed = dict(self.doc, collected_at=(NOW - timedelta(minutes=1)).isoformat())
        with self.assertRaises(InvalidDocument):
            self.plan(snapshot_bytes=canonical(changed))

    def test_blacklisted_entry_is_never_opened_or_statted(self):
        (self.live / "example/.env").write_text("api_key: " + "bait" * 8)
        real_open, real_stat = os.open, os.stat
        def opened(path, *args, **kwargs):
            self.assertNotEqual(Path(path).name, ".env")
            return real_open(path, *args, **kwargs)
        def statted(path, *args, **kwargs):
            if not isinstance(path, int):
                self.assertNotEqual(Path(path).name, ".env")
            return real_stat(path, *args, **kwargs)
        with patch("os.open", side_effect=opened), patch("os.stat", side_effect=statted):
            plan = self.plan()
        self.assertNotIn(b"bait", (self.state / plan["diff_path"]).read_bytes())

    def test_create_on_box_has_null_live_hash(self):
        import shutil
        shutil.rmtree(self.live / "example")
        plan = self.plan(snapshot=self.snapshot(), direction="git-to-box", allow_create=True)
        self.assertIsNone(plan["items"][0]["expected_live_sha256"])
        self.assertEqual(plan["items"][0]["op"], "create")

    def test_delete_has_null_after_hash(self):
        import shutil
        shutil.rmtree(self.live / "example")
        plan = self.plan(snapshot=self.snapshot(), allow_delete=True)
        self.assertEqual(plan["items"][0]["op"], "delete")
        self.assertIsNone(plan["items"][0]["after_sha256"])

    def test_default_creation_and_deletion_are_refused_without_output(self):
        (self.live / "example/new.md").write_text("New note")
        with self.assertRaises(UnsafeIO):
            self.plan()
        self.assertEqual(list(self.state.iterdir()), [])

    def test_block_record_is_readable_and_source_is_skill_guard(self):
        sys.path.insert(0, str(REPO / "skills-governance/scripts"))
        import gate_record
        plan = self.plan()
        self.assertEqual(plan["gate"]["state"], "block")
        record = gate_record.read(self.state / plan["gate"]["gate_record"])
        self.assertEqual((record["result"], record["source"]), ("block", "skill_guard"))
        self.assertEqual(record["signals"], plan["gate"]["signals"])

    def test_filesystem_unchanged_except_plans_private_artifacts_no_body_in_json(self):
        (self.repo / "untracked").write_text("Keep me")
        (self.tracked / "SKILL.md").write_text("Dirty worktree prose")
        (self.state / "sentinel").write_text("Keep state")
        def snapshot():
            return {str(p.relative_to(self.root)): (p.lstat().st_mode, p.lstat().st_mtime_ns,
                    p.lstat().st_ino, p.read_bytes() if p.is_file() else None)
                    for p in self.root.rglob("*") if p != self.state and self.state not in p.parents}
        before = snapshot()
        index = (self.repo / ".git/index").read_bytes()
        plan = self.plan()
        self.assertEqual(before, snapshot())
        self.assertEqual(index, (self.repo / ".git/index").read_bytes())
        self.assertEqual({p.name for p in self.state.iterdir()}, {"sentinel", "plans"})
        for path in (self.state / "plans").rglob("*"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
        diff = self.state / plan["diff_path"]
        self.assertIn(NEW.splitlines()[-1], diff.read_bytes())
        text = canonical(plan)
        for body in (OLD, NEW, b"Old notes.", b"Dirty worktree prose"):
            self.assertNotIn(body.splitlines()[-1], text)

    def test_ttl_default_boundary_and_tamper(self):
        plan = self.plan()
        self.assertEqual(timestamp_delta(plan), timedelta(hours=24))
        self.assertFalse(expired(plan, NOW + timedelta(hours=24) - timedelta(microseconds=1)))
        self.assertTrue(expired(plan, NOW + timedelta(hours=24)))
        changed = copy.deepcopy(plan)
        changed["expires_at"] = (NOW + timedelta(days=2)).isoformat()
        with self.assertRaises(InvalidDocument):
            validate_plan(changed)
        for ttl in (0, -1, True, 7 * 86400 + 1):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                self.plan(ttl=ttl)

    def test_instant_live_reread_not_snapshot_hash(self):
        (self.live / "example/SKILL.md").write_bytes(NEW + b"More live prose.\n")
        plan = self.plan(direction="git-to-box")
        self.assertEqual(plan["items"][0]["expected_live_sha256"], tree_sha256(self.live / "example")["tree_sha256"])
        self.assertNotEqual(plan["items"][0]["expected_live_sha256"], self.doc["domains"]["workflows"][0]["tree_sha256"])

    def test_unknown_or_stale_snapshot_is_refused(self):
        for doc in (dict(self.doc, collected_at=(NOW - timedelta(hours=37)).isoformat()),
                    dict(self.doc, domain_states={**self.doc["domain_states"], "workflows": "not_collected"})):
            with self.subTest(doc=doc["collected_at"]), self.assertRaises(UnsafeIO):
                self.plan(snapshot=doc)
        self.assertEqual(list(self.state.iterdir()), [])

    def test_symlink_fifo_and_secret_refused_before_staging(self):
        path = self.live / "example/unsafe.md"
        for kind in ("symlink", "fifo", "secret"):
            if kind == "symlink":
                path.symlink_to(self.tracked / "SKILL.md")
            elif kind == "fifo":
                os.mkfifo(path)
            else:
                path.write_text("api_key: " + "bait" * 8)
            with self.subTest(kind=kind), self.assertRaises(UnsafeIO):
                self.plan()
            path.unlink()
        self.assertEqual(list(self.state.iterdir()), [])

    def test_state_permissions_overlap_and_plans_symlink_refused(self):
        self.state.chmod(0o755)
        with self.assertRaises(UnsafeIO):
            self.plan()
        self.state.chmod(0o700)
        for root in (self.live, self.repo):
            with self.subTest(root=root), self.assertRaises(UnsafeIO):
                self.plan(state_dir=root / "state")
        outside = self.root / "outside"
        outside.mkdir()
        (self.state / "plans").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(UnsafeIO):
            self.plan()
        self.assertEqual(list(outside.iterdir()), [])

    def test_gate_failure_never_passes(self):
        real_run = subprocess.run
        def fail_gate(argv, **kwargs):
            if str(argv[1]).endswith("skill_guard.py"):
                raise subprocess.TimeoutExpired(argv, 60)
            return real_run(argv, **kwargs)
        with patch("subprocess.run", side_effect=fail_gate):
            plan = self.plan()
        self.assertEqual(plan["gate"]["state"], "error")
        validate_plan(plan)

    def test_empty_plan_runs_gate_and_is_fallback_not_capability_approval(self):
        (self.live / "example/SKILL.md").write_bytes(OLD)
        plan = self.plan()
        self.assertEqual(plan["items"], [])
        self.assertEqual(plan["gate"]["state"], "fallback")

    def cli(self, *args):
        snapshot = self.root / "snapshot.json"
        doc = self.snapshot()
        doc["collected_at"] = datetime.now(timezone.utc).isoformat()
        snapshot.write_bytes(canonical(doc))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["plan-writeback", "--snapshot", str(snapshot), "--repo", str(self.repo),
                           "--git-ref", self.sha, "--state-dir", str(self.state),
                           "--fixture-home", str(self.root / "home"), "--offline", *args])
        return result, json.loads(output.getvalue())

    def test_cli_block_is_dry_run_and_no_write_commands_called(self):
        real_run = subprocess.run
        calls = []
        def spy(argv, **kwargs):
            calls.append(argv)
            if argv[0] == "git":
                self.assertIn(argv[argv.index("-C") + 2], {"rev-parse", "ls-tree", "cat-file"})
            else:
                self.assertEqual(Path(argv[1]).name, "skill_guard.py")
            return real_run(argv, **kwargs)
        with patch("subprocess.run", side_effect=spy):
            code, plan = self.cli("--direction", "box-to-git")
        self.assertEqual(code, 4)
        self.assertEqual(plan["gate"]["state"], "block")
        self.assertTrue(calls)

    def test_cli_empty_returns_zero_and_note_drift_returns_ten(self):
        (self.live / "example/SKILL.md").write_bytes(OLD)
        self.assertEqual(self.cli("--direction", "box-to-git")[0], 0)
        (self.live / "example/notes.txt").write_text("New notes.\n")
        code, plan = self.cli("--direction", "box-to-git", "--ttl", "30m")
        self.assertEqual(code, 10)
        self.assertEqual(timestamp_delta(plan), timedelta(minutes=30))


def timestamp_delta(plan):
    return datetime.fromisoformat(plan["expires_at"]) - datetime.fromisoformat(plan["created_at"])
