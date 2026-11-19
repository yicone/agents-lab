"""ACM-928 §8 P1-E acceptance items 1-4 as runnable local-repo tests.

Item 5 (end-to-end collect/reconcile/publish/fetch/GET) lives in
``multica-board/backend/tests/test_box_snapshot.py`` as
``test_acceptance_5_end_to_end_...``.

Run alone: ``python3 -m unittest tests.test_p1e_acceptance -v``.
"""
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/ helpers

from collect_support import TOOL, fixture
import data_branch_support as dbs
from boxsync.__main__ import main
from boxsync import publish as publish_mod
from boxsync.snapshot import collect, private_directory, write_snapshot


class Acceptance(unittest.TestCase):
    """Throwaway bare origin + working clones; never a real remote."""

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
        value, _ = collect(ctx, tool=TOOL)
        private_directory(self.state / "snapshots", create=True)
        write_snapshot(value, self.snapshot)
        return value

    def invoke_publish(self, extra=()):
        args = ["publish-snapshot", "--repo", str(self.clone),
                "--state-dir", str(self.state)] + list(extra)
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

    def invoke_fetch(self, state_dir):
        args = ["fetch-snapshot", "--repo", str(self.mini),
                "--state-dir", str(state_dir)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            return main(args), json.loads(out.getvalue())

    def audit(self):
        return [json.loads(line)
                for line in (self.state / "audit.jsonl").read_text().splitlines()]

    def test_acceptance_1_only_allowlisted_paths_no_force_refspec(self):
        # §8(1): pushes to the fixture bare repo touch only allowlisted
        # paths; a stray file in the data worktree exits 4 with no push;
        # refspec is HEAD:refs/heads/box-snapshots without --force.
        code, payload, calls = self.invoke_publish()
        self.assertEqual((code, payload["state"]), (0, "pushed"))
        tip = dbs.bare_tip(self.bare)
        names = dbs.git(self.bare, "diff-tree", "--root", "-r", "--name-only",
                        "--no-commit-id", tip).stdout.decode().split()
        self.assertEqual(names, [dbs.SNAPSHOT_REL])
        self.assertEqual([c for c in calls if c[0] == "push"],
                         [["push", "origin", "HEAD:refs/heads/box-snapshots"]])
        for argv in calls:
            self.assertNotIn("--force", argv)
            self.assertNotIn("-f", argv)
            self.assertFalse(any(arg.startswith("+") for arg in argv))

        worktree = self.state / "data-worktree"
        (worktree / "evil.txt").write_text("stray")
        self.ctx.now = self.ctx.now.replace(microsecond=self.ctx.now.microsecond + 1)
        self.write_snapshot(self.ctx)
        code, payload, calls = self.invoke_publish()
        self.assertEqual(code, 4)
        self.assertEqual(payload["reason_code"], "non_allowlisted_path")
        self.assertEqual([c for c in calls if c[0] == "push"], [])
        self.assertEqual(dbs.bare_tip(self.bare), tip)
        self.assertTrue((worktree / "evil.txt").exists())
        self.assertEqual(self.audit()[-1]["result"], "refused")

    def test_acceptance_2_allowlist_read_only_from_origin_main(self):
        # §8(2): modifying the allowlist copy in the working tree or on the
        # data branch changes nothing; only origin/main is authoritative.
        loose = dbs.allowlist()
        loose["entries"][0]["path_regex"] = "^.*$"
        dbs.write_allowlist(self.clone, loose)  # working-tree copy loosened
        # A loose allowlist copy plus a stray path live on the data branch.
        tip = dbs.push_data_commit(self.pusher, {
            dbs.ALLOWLIST_REL: json.dumps(loose),
            "injected.txt": "stray",
            dbs.SNAPSHOT_REL: self.snapshot.read_text()})
        self.ctx.now = self.ctx.now.replace(microsecond=self.ctx.now.microsecond + 1)
        self.write_snapshot(self.ctx)
        code, payload, calls = self.invoke_publish()
        self.assertEqual(code, 4)
        self.assertEqual(payload["reason_code"], "non_allowlisted_path")
        self.assertEqual([c for c in calls if c[0] == "push"], [])
        self.assertEqual(dbs.bare_tip(self.bare), tip)
        # Fetch side: loosen the Mini's working-tree copy; a stray path is
        # still rejected because the pinned origin/main allowlist governs.
        fetch_state = self.base / "fetch-state"
        dbs.write_allowlist(self.mini, loose)
        code, payload = self.invoke_fetch(fetch_state)
        self.assertEqual(code, 4)
        self.assertEqual(payload["reason_code"], "non_allowlisted_path")
        self.assertFalse((fetch_state / dbs.SNAPSHOT_REL).exists())
        # A tightened working-tree copy does not block a legit publish.
        dbs.reset_data_branch(self.pusher, self.bare, dbs.push_data_commit(
            self.pusher, {dbs.SNAPSHOT_REL: self.snapshot.read_text()},
            push=False))
        dbs.git(self.clone, "worktree", "remove", "--force",
                str(self.state / "data-worktree"))
        tight = dbs.allowlist()
        tight["entries"][0]["path_regex"] = "^never-matches$"
        dbs.write_allowlist(self.clone, tight)
        code, payload, _ = self.invoke_publish()
        self.assertEqual((code, payload["state"]), (0, "unchanged"))

    def test_acceptance_3_oversize_and_secret_snapshots_are_refused(self):
        # §8(3): snapshots over 1 MiB or containing secrets are not pushed
        # and the audit records refused.
        # A genuinely valid >1 MiB document (under the 4 MiB schema bound).
        value, _ = collect(self.ctx, tool=TOOL)
        value["domains"]["attachments"] = [
            {"id": "attachment:a/%d" % i, "store": "blob",
             "agent_id": "00000000-0000-4000-8000-000000000001",
             "path": "workflows/example/blob-%d" % i, "bytes": 128,
             "sha256": "a" * 64, "hash_state": "observed",
             "mtime": "2026-11-14T02:30:00Z"}
            for i in range(9000)]
        write_snapshot(value, self.snapshot)
        self.assertGreater(self.snapshot.stat().st_size, 1024 * 1024)
        code, payload, calls = self.invoke_publish()
        self.assertEqual((code, payload["reason_code"]), (4, "too_large"))
        self.assertIsNone(dbs.bare_tip(self.bare))
        self.assertEqual([c for c in calls if c[0] == "push"], [])
        record = self.audit()[-1]
        self.assertEqual((record["result"], record["reason_code"]),
                         ("refused", "too_large"))
        # Secret content in a schema-legal field (hostile raw bytes).
        doc = json.loads(self.snapshot.read_text())
        doc["domains"]["attachments"] = []
        doc["domains"]["hooks"]["host"] = "token=abc123secret"
        self.snapshot.write_text(
            json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n")
        code, payload, calls = self.invoke_publish()
        self.assertEqual(code, 4)
        self.assertEqual(payload["reason_code"], "snapshot_secret")
        self.assertIsNone(dbs.bare_tip(self.bare))
        self.assertEqual([c for c in calls if c[0] == "push"], [])
        self.assertEqual(self.audit()[-1]["result"], "refused")

    def test_schema_file_mismatch_refused_for_publish_and_fetch(self):
        # The allowlist validator is hard-wired to the repo's snapshot schema:
        # a different declared schema_file fails closed for both sides.
        # Seed the data branch so fetch reaches the allowlist check.
        dbs.push_data_commit(self.pusher,
                             {dbs.SNAPSHOT_REL: self.snapshot.read_text()})
        broken = dbs.allowlist()
        broken["entries"][0]["schema_file"] = "docs/schemas/other.schema.json"
        dbs.write_allowlist(self.clone, broken)
        dbs.git(self.clone, "add", "-A")
        dbs.git(self.clone, "commit", "-qm", "Break fixture allowlist.")
        dbs.git(self.clone, "push", "-q", "origin", "main")
        code, payload, calls = self.invoke_publish()
        self.assertEqual((code, payload["reason_code"]),
                         (4, "allowlist_unavailable"))
        self.assertEqual([c for c in calls if c[0] == "push"], [])
        code, payload = self.invoke_fetch(self.base / "fetch-state")
        self.assertEqual((code, payload["reason_code"]),
                         (4, "allowlist_unavailable"))

    def test_acceptance_4_fetch_rejects_bad_commits_and_installs_legal(self):
        # §8(4): fetch-snapshot rejects commits touching non-allowlisted
        # paths, wrong schema, or oversize blobs without updating the local
        # copy (state rejected); legal commits install normally.
        fetch_state = self.base / "fetch-state"
        copy = fetch_state / dbs.SNAPSHOT_REL
        status = fetch_state / "fetch-status.json"

        code, payload = self.invoke_fetch(fetch_state)
        self.assertEqual(code, 3)  # branch absent -> absent, exit 3
        code, payload, _ = self.invoke_publish()
        self.assertEqual((code, payload["state"]), (0, "pushed"))
        code, payload = self.invoke_fetch(fetch_state)
        self.assertEqual((code, payload["state"]), (0, "ok"))
        before = hashlib.sha256(copy.read_bytes()).hexdigest()

        value = self.snapshot.read_text()
        wrong_schema = json.loads(value)
        wrong_schema["schema"] = "box-config-snapshot/v0"
        # The published commit lives in clone/bare; pull it into pusher
        # before plumbing parents onto it.
        dbs.git(self.pusher, "fetch", "-q", "origin", "box-snapshots")
        for reason, files in (
                ("non_allowlisted_path", {dbs.SNAPSHOT_REL: value,
                                          "evil.txt": "x"}),
                ("schema_mismatch", {dbs.SNAPSHOT_REL:
                                     json.dumps(wrong_schema) + "\n"}),
                ("too_large", {dbs.SNAPSHOT_REL: b"x" * (1048576 + 1)})):
            with self.subTest(reason=reason):
                # Fresh legal root each case: one bad commit stays rejected
                # until human repair, so repair before the next case.
                clean = dbs.push_data_commit(
                    self.pusher, {dbs.SNAPSHOT_REL: value},
                    parents=(), push=False, message="legal " + reason)
                dbs.reset_data_branch(self.pusher, self.bare, clean)
                status.unlink(missing_ok=True)  # clear verified_commit
                dbs.push_data_commit(self.pusher, files,
                                     parents=(clean,), message="bad " + reason)
                code, payload = self.invoke_fetch(fetch_state)
                self.assertEqual(code, 4)
                self.assertEqual(payload["state"], "rejected")
                self.assertEqual(payload["reason_code"], reason)
                self.assertEqual(
                    hashlib.sha256(copy.read_bytes()).hexdigest(), before)
                self.assertEqual(json.loads(status.read_text())["state"],
                                 "rejected")
        # Human repair with a fresh legal root; fetch installs it.
        repaired = dbs.push_data_commit(self.pusher,
                                        {dbs.SNAPSHOT_REL: value},
                                        parents=(), push=False,
                                        message="repaired")
        dbs.reset_data_branch(self.pusher, self.bare, repaired)
        status.unlink(missing_ok=True)
        code, payload = self.invoke_fetch(fetch_state)
        self.assertEqual((code, payload["state"]), (0, "ok"))
        self.assertEqual(copy.read_text(), value)


if __name__ == "__main__":
    unittest.main()
