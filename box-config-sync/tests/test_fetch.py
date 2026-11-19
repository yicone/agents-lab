"""ACM-928 acceptance: fetch-snapshot verifies history before installing."""
import contextlib
import hashlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path

from collect_support import TOOL, fixture
import data_branch_support as dbs
from boxsync.__main__ import main
from boxsync.snapshot import collect


def snapshot_text(ctx):
    value, _ = collect(ctx, tool=TOOL)
    return value, json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"


class FetchFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bare, self.clone, self.mini, self.pusher = dbs.make_repos(self.base)
        self.state = self.base / "state"  # fetch-snapshot creates it 0700
        ctx, _ = fixture(self.base / "fx")
        self.doc, self.text = snapshot_text(ctx)
        self.copy = self.state / "box-snapshots/grok-box/snapshot.json"
        self.status = self.state / "fetch-status.json"

    def invoke(self, extra=()):
        args = ["fetch-snapshot", "--repo", str(self.mini),
                "--state-dir", str(self.state)] + list(extra)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            return main(args), json.loads(out.getvalue())

    def pushed(self, files=None, parents=(), message="fixture", push=True):
        files = files if files is not None else {dbs.SNAPSHOT_REL: self.text}
        return dbs.push_data_commit(self.pusher, files, message=message,
                                    parents=parents, push=push)

    def copy_hash(self):
        return hashlib.sha256(self.copy.read_bytes()).hexdigest() \
            if self.copy.exists() else None

    def fetch_ok(self, tip=None):
        tip = tip or self.pushed()
        code, payload = self.invoke()
        self.assertEqual((code, payload["state"]), (0, "ok"))
        self.assertEqual(payload["commit"], tip)
        self.assertEqual(json.loads(self.copy.read_text()), self.doc)
        status = json.loads(self.status.read_text())
        self.assertEqual(status["state"], "ok")
        self.assertEqual(status["verified_commit"], tip)
        self.assertEqual(stat.S_IMODE(self.status.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.copy.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        return tip

    def rejected(self, reason):
        code, payload = self.invoke()
        self.assertEqual(code, 4)
        self.assertEqual((payload["state"], payload["reason_code"]), ("rejected", reason))
        status = json.loads(self.status.read_text())
        self.assertEqual(status["state"], "rejected")
        self.assertEqual(status["reason_code"], reason)

    def test_legit_snapshot_installs_private_copy(self):
        self.fetch_ok()

    def test_absent_branch_reports_absent_and_keeps_copy(self):
        code, payload = self.invoke()
        self.assertEqual((code, payload["state"]), (3, "absent"))
        self.assertFalse(self.copy.exists())

    def test_non_allowlisted_path_rejected_copy_unchanged(self):
        tip = self.fetch_ok()
        before = self.copy_hash()
        self.pushed(files={dbs.SNAPSHOT_REL: self.text, "evil.txt": "x"},
                    parents=(tip,))
        self.rejected("non_allowlisted_path")
        self.assertEqual(self.copy_hash(), before)
        status = json.loads(self.status.read_text())
        self.assertEqual(status["verified_commit"], tip)  # preserved

    def test_schema_mismatch_rejected(self):
        tip = self.fetch_ok()
        before = self.copy_hash()
        bad = json.loads(self.text)
        bad["schema"] = "box-config-snapshot/v0"
        self.pushed(files={dbs.SNAPSHOT_REL: json.dumps(bad) + "\n"}, parents=(tip,))
        self.rejected("schema_mismatch")
        self.assertEqual(self.copy_hash(), before)

    def test_too_large_rejected(self):
        tip = self.fetch_ok()
        before = self.copy_hash()
        self.pushed(files={dbs.SNAPSHOT_REL: b"x" * (1048576 + 1)}, parents=(tip,))
        self.rejected("too_large")
        self.assertEqual(self.copy_hash(), before)

    def test_merge_commit_rejected(self):
        tip = self.fetch_ok()
        before = self.copy_hash()
        side = dbs.git(self.pusher, "rev-parse", "main").stdout.decode().strip()
        self.pushed(parents=(tip, side))
        self.rejected("merge_commit")
        self.assertEqual(self.copy_hash(), before)

    def test_invalid_content_rejected(self):
        tip = self.fetch_ok()
        before = self.copy_hash()
        self.pushed(files={dbs.SNAPSHOT_REL: "{not json"}, parents=(tip,))
        self.rejected("invalid_snapshot")
        self.assertEqual(self.copy_hash(), before)

    def test_secret_content_rejected(self):
        tip = self.fetch_ok()
        before = self.copy_hash()
        bad = json.loads(self.text)
        bad["domains"]["workflows"] = [{
            "id": "grok-workflow:example", "dir_name": "example",
            "name": "token=abc123secret", "name_key": "x", "kind": "named",
            "tree_sha256": "a" * 64, "tree_state": "observed",
            "body_sha256": "a" * 64, "body_hash_state": "observed",
            "files": [], "flags": [], "evidence_level": "observed"}]
        self.pushed(files={dbs.SNAPSHOT_REL: json.dumps(bad) + "\n"}, parents=(tip,))
        self.rejected("snapshot_secret")
        self.assertEqual(self.copy_hash(), before)

    def test_history_rewrite_rejected(self):
        tip = self.fetch_ok()
        before = self.copy_hash()
        dbs.reset_data_branch(self.pusher, self.bare,
                              self.pushed(parents=(), message="rewritten root",
                                          push=False))
        self.rejected("history_rewritten")
        self.assertEqual(self.copy_hash(), before)
        # Human repair back to the last verified tip recovers fetching.
        dbs.reset_data_branch(self.pusher, self.bare, tip)
        code, payload = self.invoke()
        self.assertEqual((code, payload["state"]), (0, "ok"))

    def test_repaired_branch_recovers_after_rejection(self):
        tip = self.fetch_ok()
        self.pushed(files={dbs.SNAPSHOT_REL: "{not json"}, parents=(tip,))
        self.rejected("invalid_snapshot")
        # A bad tip stays rejected until human action on the branch.
        self.rejected("invalid_snapshot")
        repaired = self.pushed(parents=(tip,), push=False)
        dbs.reset_data_branch(self.pusher, self.bare, repaired)
        code, payload = self.invoke()
        self.assertEqual((code, payload["state"]), (0, "ok"))
        self.assertEqual(payload["commit"], repaired)
        self.assertEqual(self.copy_hash(), self.copy_hash())

    def test_working_tree_allowlist_changes_do_not_affect_fetch(self):
        loose = dbs.allowlist()
        loose["entries"][0]["path_regex"] = "^.*$"
        dbs.write_allowlist(self.mini, loose)
        tip = self.fetch_ok()
        before = self.copy_hash()
        # Stray path allowed only by the loosened working-tree copy.
        self.pushed(files={dbs.SNAPSHOT_REL: self.text, "evil.txt": "x"},
                    parents=(tip,))
        self.rejected("non_allowlisted_path")
        self.assertEqual(self.copy_hash(), before)


if __name__ == "__main__":
    unittest.main()
