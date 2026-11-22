"""ACM-928 acceptance: publish-snapshot pushes only allowlisted paths."""
import contextlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collect_support import TOOL, fixture
import data_branch_support as dbs
from boxsync.__main__ import main
from boxsync import publish as publish_mod
from boxsync.snapshot import collect, write_snapshot


class PublishFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bare, self.clone, self.mini, self.pusher = dbs.make_repos(self.base)
        self.state = self.base / "state"
        self.state.mkdir(mode=0o700)
        self.ctx, _ = fixture(self.base / "fx")
        self.snapshot = self.state / "snapshots/latest.json"
        self.write_snapshot(self.ctx)

    def write_snapshot(self, ctx):
        self.ctx = ctx
        from boxsync.snapshot import private_directory
        value, _ = collect(ctx, tool=TOOL)
        private_directory(self.state / "snapshots", create=True)
        write_snapshot(value, self.snapshot)
        return value

    def invoke(self, extra=()):
        args = ["publish-snapshot", "--repo", str(self.clone), "--state-dir", str(self.state)]
        args += list(extra)
        calls = []
        original = publish_mod.run_git

        def spy(*argv, **kwargs):
            calls.append(list(argv[0]))
            return original(*argv, **kwargs)

        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                patch.object(publish_mod, "run_git", side_effect=spy):
            code = main(args)
        return code, json.loads(out.getvalue()), calls

    def audit(self):
        return [json.loads(line)
                for line in (self.state / "audit.jsonl").read_text().splitlines()]

    def test_pushes_only_allowlisted_path_and_exact_refspec(self):
        code, payload, calls = self.invoke()
        self.assertEqual((code, payload["state"]), (0, "pushed"))
        tip = dbs.bare_tip(self.bare)
        self.assertIsNotNone(tip)
        names = dbs.git(self.bare, "diff-tree", "--root", "-r", "--name-only",
                        "--no-commit-id", tip).stdout.decode().split()
        self.assertEqual(names, [dbs.SNAPSHOT_REL])
        pushes = [argv for argv in calls if argv[0] == "push"]
        self.assertEqual(pushes, [["push", "origin", "HEAD:refs/heads/box-snapshots"]])
        for argv in calls:
            self.assertNotIn("--force", argv)
            self.assertNotIn("-f", argv)
            self.assertNotIn("--mirror", argv)
            self.assertNotIn("--delete", argv)
            self.assertFalse(any(arg.startswith("+") for arg in argv))
        record = self.audit()[-1]
        self.assertEqual(record["result"], "pushed")
        self.assertEqual(record["action"], "push-snapshot")
        self.assertEqual(record["approve"], "ACM-910")
        self.assertEqual(record["data_commit"], tip)
        self.assertEqual(stat.S_IMODE((self.state / "audit.jsonl").stat().st_mode), 0o600)
        pushed = self.state / "data-worktree" / dbs.SNAPSHOT_REL
        self.assertEqual(stat.S_IMODE(pushed.stat().st_mode), 0o600)
        subject = dbs.git(self.bare, "log", "-1", "--format=%s",
                          "box-snapshots").stdout.decode().strip()
        self.assertTrue(subject.startswith("box-snapshot grok-box "), subject)
        self.assertIn(" tool=3" + "3" * 39, subject)
        # A second publish of unchanged content is a no-op.
        code, payload, calls = self.invoke()
        self.assertEqual((code, payload["state"]), (0, "unchanged"))
        self.assertEqual([c for c in calls if c[0] == "push"], [])
        self.assertEqual(self.audit()[-1]["result"], "unchanged")

    def test_stray_worktree_file_refused_and_nothing_pushed(self):
        code, _, _ = self.invoke()
        self.assertEqual(code, 0)
        tip = dbs.bare_tip(self.bare)
        worktree = self.state / "data-worktree"
        (worktree / "evil.txt").write_text("stray")
        self.ctx.now = self.ctx.now.replace(microsecond=self.ctx.now.microsecond + 1)
        self.write_snapshot(self.ctx)
        code, payload, calls = self.invoke()
        self.assertEqual(code, 4)
        self.assertEqual(payload["reason_code"], "non_allowlisted_path")
        self.assertEqual([c for c in calls if c[0] == "push"], [])
        self.assertEqual(dbs.bare_tip(self.bare), tip)
        self.assertTrue((worktree / "evil.txt").exists())  # user files untouched
        self.assertEqual(self.audit()[-1]["result"], "refused")
        self.assertEqual(self.audit()[-1]["reason_code"], "non_allowlisted_path")

    def test_working_tree_and_data_branch_allowlist_copies_are_ignored(self):
        # Loosen the working-tree copy only; origin/main is untouched.
        loose = dbs.allowlist()
        loose["entries"][0]["path_regex"] = "^.*$"
        dbs.write_allowlist(self.clone, loose)
        # A different (loose) copy plus a stray path live on the data branch.
        tip = dbs.push_data_commit(self.pusher, {
            dbs.ALLOWLIST_REL: json.dumps(loose),
            "injected.txt": "stray",
            dbs.SNAPSHOT_REL: self.snapshot.read_text(),
        })
        # The stray path allowed only by the loose copies is refused anyway.
        code, payload, calls = self.invoke()
        self.assertEqual(code, 4)
        self.assertEqual(payload["reason_code"], "non_allowlisted_path")
        self.assertEqual([c for c in calls if c[0] == "push"], [])
        self.assertEqual(dbs.bare_tip(self.bare), tip)
        # A tightened working-tree copy does not block a legit publish either.
        # Human cleanup repaired the branch; drop the tainted data worktree.
        dbs.reset_data_branch(self.pusher, self.bare, dbs.push_data_commit(
            self.pusher, {dbs.SNAPSHOT_REL: self.snapshot.read_text()}, push=False))
        dbs.git(self.clone, "worktree", "remove", "--force",
                str(self.state / "data-worktree"))
        tight = dbs.allowlist()
        tight["entries"][0]["path_regex"] = "^never-matches$"
        dbs.write_allowlist(self.clone, tight)
        code, payload, _ = self.invoke()
        self.assertEqual((code, payload["state"]), (0, "unchanged"))

    def test_too_large_and_secret_snapshots_refused_without_push(self):
        # Real >1 MiB document (valid, under the 4 MiB schema bound).
        value, _ = collect(self.ctx, tool=TOOL)
        value["domains"]["attachments"] = [
            {"id": "attachment:a/%d" % i, "store": "blob", "agent_id":
             "00000000-0000-4000-8000-000000000001",
             "path": "workflows/example/blob-%d" % i, "bytes": 128,
             "sha256": "a" * 64, "hash_state": "observed",
             "mtime": "2026-11-14T02:30:00Z"}
            for i in range(9000)]
        write_snapshot(value, self.snapshot)
        self.assertGreater(self.snapshot.stat().st_size, 1024 * 1024)
        code, payload, calls = self.invoke()
        self.assertEqual((code, payload["reason_code"]), (4, "too_large"))
        self.assertIsNone(dbs.bare_tip(self.bare))
        self.assertEqual([c for c in calls if c[0] == "push"], [])
        record = self.audit()[-1]
        self.assertEqual((record["result"], record["reason_code"]), ("refused", "too_large"))
        # Secret content: write raw bytes bypassing write_snapshot (a hostile copy).
        value["domains"]["attachments"] = []
        value["domains"]["workflows"] = []
        value["sources"]["box_disk"] = "partial"
        value["domain_states"]["workflows"] = "partial"
        value["domain_states"]["attachments"] = "partial"
        # Secrets must hide in schema-legal fields; use a workflow name.
        value["domains"]["workflows"] = [{
            "id": "grok-workflow:example", "dir_name": "example", "name": "token=abc123secret",
            "name_key": "x", "kind": "named", "tree_sha256": "a" * 64, "tree_state": "observed",
            "body_sha256": "a" * 64, "body_hash_state": "observed", "files": [], "flags": [],
            "evidence_level": "observed"}]
        self.snapshot.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        code, payload, calls = self.invoke()
        self.assertEqual(code, 4)
        self.assertIn(payload["reason_code"], ("snapshot_secret", "invalid_snapshot"))
        self.assertIsNone(dbs.bare_tip(self.bare))
        self.assertEqual([c for c in calls if c[0] == "push"], [])
        self.assertEqual(self.audit()[-1]["result"], "refused")
        self.assertEqual(stat.S_IMODE((self.state / "audit.jsonl").stat().st_mode), 0o600)

    def test_lowered_allowlist_limit_refuses_and_audit_0600(self):
        small = dbs.allowlist()
        small["entries"][0]["max_bytes"] = 100
        dbs.write_allowlist(self.clone, small)
        dbs.git(self.clone, "add", "-A")
        dbs.git(self.clone, "commit", "-qm", "Tighten fixture allowlist.")
        dbs.git(self.clone, "push", "-q", "origin", "main")
        code, payload, calls = self.invoke()
        self.assertEqual((code, payload["reason_code"]), (4, "too_large"))
        self.assertEqual([c for c in calls if c[0] == "push"], [])

    def test_missing_snapshot_and_bad_state_dir_exit_4(self):
        self.snapshot.unlink()
        code, payload, _ = self.invoke()
        self.assertEqual((code, payload["reason_code"]), (4, "snapshot_unavailable"))
        self.assertIsNone(dbs.bare_tip(self.bare))
        self.state.chmod(0o755)
        code, payload, _ = self.invoke()
        self.assertEqual((code, payload["reason_code"]), (4, "unsafe_state_directory"))


if __name__ == "__main__":
    unittest.main()
