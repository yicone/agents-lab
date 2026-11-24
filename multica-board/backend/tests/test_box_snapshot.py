"""ACM-928: GET /api/box-config reads the Mini snapshot copy, nothing else."""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
SYNC = REPO / "box-config-sync"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SYNC))
sys.path.insert(0, str(SYNC / "tests"))

from multica_board.server import BoardServer  # noqa: E402
from multica_board.store import Store  # noqa: E402
from multica_board.views import Views  # noqa: E402
from multica_board import box_snapshot  # noqa: E402

from collect_support import fixture  # noqa: E402
import data_branch_support as dbs  # noqa: E402
from boxsync.__main__ import main as boxsync_main  # noqa: E402
from boxsync.snapshot import private_directory, write_snapshot  # noqa: E402

SERVER = "test-profile"
WORKFLOWS = {
    "example": {"SKILL.md": "---\nname: Example\n---\nPrivate skill prose never exported.\n",
                "assets.txt": "Private auxiliary body."},
    "only-in-git": {"SKILL.md": "---\nname: GitOnly\n---\nx\n"},
}


def boxsync(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = boxsync_main(argv)
    return code, json.loads(out.getvalue())


class TransportFixture(unittest.TestCase):
    """Full transport: collect -> reconcile -> publish -> fetch -> /api/box-config."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        base = Path(cls.tmp.name)
        cls.bare, cls.clone, cls.mini, cls.pusher = dbs.make_repos(base, workflows=WORKFLOWS)
        _, cls.home = fixture(base / "fx")
        cls.box_state = base / "box-state"
        cls.box_state.mkdir(mode=0o700)
        cls.mini_state = base / "mini-state"
        cls.mini_state.mkdir(mode=0o700)
        code, _ = boxsync(["collect", "--offline", "--fixture-home", str(cls.home),
                           "--state-dir", str(cls.box_state)])
        cls.assertIn(cls, code, (0, 3))
        cls.snapshot = cls.box_state / "snapshots/latest.json"
        code, cls.reconcile = boxsync(["reconcile", "--offline", "--repo", str(cls.clone),
                                       "--git-ref", "origin/main", "--snapshot", str(cls.snapshot),
                                       "--state-dir", str(cls.box_state)])
        cls.assertIn(cls, code, (0, 3, 10))
        static = base / "web"
        static.mkdir()
        (static / "index.html").write_text("<h1>board</h1>")
        cls.store = Store(":memory:")
        cls.views = Views(cls.store, SERVER, app_url="https://app.test",
                          collect_interval_s=60, box_state_dir=cls.mini_state)
        cls.httpd = BoardServer("127.0.0.1", 0, cls.views, static_dir=static)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.store.close()
        cls.tmp.cleanup()

    def req(self, method, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        conn.request(method, path)
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response, body

    def test_acceptance_5_end_to_end_collect_reconcile_publish_fetch_route(self):
        # §8 P1-E: collect -> reconcile -> publish -> fetch -> GET /api/box-config;
        # API workflow states/summary equal CLI reconcile output; absent before copy.
        response, body = self.req("GET", "/api/box-config")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        data = json.loads(body)
        self.assertEqual(data["schema"], "box-config-api/v1")
        self.assertEqual(data["source_state"], "absent")
        self.assertIsNone(data["domain_counts"])
        self.assertIsNone(data["workflows"])
        self.assertIsNone(data["sources"])
        self.assertIsNone(data["git_leg"])

        def skills_body():
            fixed = datetime(2026, 11, 19, tzinfo=timezone.utc)
            with mock.patch("multica_board.runtime_load.collect_runtime_load",
                            return_value={"codex": None, "cursor": None, "hermes": None}), \
                 mock.patch("multica_board.skill_grok_bots.grok_bot_skills",
                            return_value=([], {"id": "grok_bot", "state": "absent", "detail": "x"})), \
                 mock.patch("multica_board.skill_grok_export.grok_export_skills",
                            return_value=([], {"id": "grok_bot_export", "state": "absent",
                                              "detail": "x"})), \
                 mock.patch("multica_board.skills_view._now", return_value=fixed):
                return self.req("GET", "/api/skills")

        response, skills_before = skills_body()
        self.assertEqual(response.status, 200)

        code, published = boxsync(["publish-snapshot", "--repo", str(self.clone),
                                   "--state-dir", str(self.box_state)])
        self.assertEqual((code, published["state"]), (0, "pushed"))
        code, fetched = boxsync(["fetch-snapshot", "--repo", str(self.mini),
                                 "--state-dir", str(self.mini_state)])
        self.assertEqual((code, fetched["state"]), (0, "ok"))
        self.assertTrue((self.mini_state / "git-leg.json").is_file())
        self.assertEqual(stat.S_IMODE((self.mini_state / "git-leg.json").stat().st_mode), 0o600)

        response, body = self.req("GET", "/api/box-config")
        self.assertEqual(response.status, 200)
        data = json.loads(body)
        self.assertEqual(data["source_state"], "ok")
        self.assertEqual(data["box_id"], "grok-box")
        self.assertEqual(data["fetch"]["state"], "ok")
        self.assertEqual(data["fetch"]["verified_commit"], fetched["commit"])
        self.assertIsNotNone(data["age_seconds"])
        self.assertEqual(data["git_leg"]["state"], "ok")
        self.assertEqual(data["domain_counts"]["workflows"],
                         len(json.loads(self.snapshot.read_text())["domains"]["workflows"]))
        # API workflow projection equals the CLI reconcile report exactly.
        states = {row["id"]: row["state"] for row in data["workflows"]["items"]}
        cli = {row["id"]: row["state"] for row in self.reconcile["domains"]["workflows"]}
        self.assertEqual(states, cli)
        self.assertEqual(data["workflows"]["summary"], self.reconcile["summary"])
        self.assertGreaterEqual(len(set(states.values())), 2)
        for row in data["workflows"]["items"]:
            self.assertNotIn("file_differences", row)
            self.assertNotIn("live_tree_sha256", row)

        response, body = self.req("HEAD", "/api/box-config")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"")
        response, body = self.req("GET", "/api/box-config?fresh=1")
        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "bad_request")

        # /api/skills bytes are identical with and without the fetched copy.
        response, skills_after = skills_body()
        self.assertEqual(response.status, 200)
        self.assertEqual(skills_after, skills_before)

    def test_no_subprocess_while_serving_box_config(self):
        # §5.6.6: the board never spawns a process serving /api/box-config.
        calls = []

        def blocked(*a, **kw):
            calls.append(a)
            raise AssertionError("subprocess during /api/box-config")

        with mock.patch.object(subprocess, "run", blocked), \
             mock.patch.object(subprocess, "Popen", blocked), \
             mock.patch.object(os, "system", blocked), \
             mock.patch.object(os, "posix_spawn", blocked, create=True), \
             mock.patch.object(os, "fork", blocked, create=True):
            payload = self.views.box_config()
            self.assertIn(payload["source_state"], ("absent", "ok"))
            response, body = self.req("GET", "/api/box-config")
            self.assertEqual(response.status, 200)
            json.loads(body)
        self.assertEqual(calls, [])

    def test_box_snapshot_module_imports_no_subprocess_or_gitreader(self):
        self.assertNotIn("subprocess", vars(box_snapshot))
        self.assertNotIn("GitReader", vars(box_snapshot))
        source = Path(box_snapshot.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import subprocess", source)
        self.assertNotIn("GitReader", source)


class ReaderStateTests(unittest.TestCase):
    """read_box_snapshot source_state: absent/bad/stale/rejected/ok."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.bare, self.clone, self.mini, self.pusher = dbs.make_repos(base, workflows=WORKFLOWS)
        _, self.home = fixture(base / "fx")
        self.box_state = base / "box-state"
        self.box_state.mkdir(mode=0o700)
        code, _ = boxsync(["collect", "--offline", "--fixture-home", str(self.home),
                           "--state-dir", str(self.box_state)])
        self.assertIn(code, (0, 3))
        self.snapshot = self.box_state / "snapshots/latest.json"
        self.mini_state = base / "mini-state"
        private_directory(self.mini_state, create=True)

    def install(self, status=None, doc=None, git_leg=None):
        target = self.mini_state / "box-snapshots/grok-box"
        private_directory(target, create=True)
        write_snapshot(doc or json.loads(self.snapshot.read_text()),
                       target / "snapshot.json")
        if git_leg is not None:
            path = self.mini_state / "git-leg.json"
            path.write_text(git_leg if isinstance(git_leg, str) else json.dumps(git_leg))
            path.chmod(0o600)
        if status is not None:
            private_directory(self.mini_state, create=True)
            path = self.mini_state / "fetch-status.json"
            path.write_text(json.dumps(status))
            path.chmod(0o600)

    def git_leg(self):
        return {"schema": "box-config-git-leg/v1", "ref": "origin/main",
                "commit": "a" * 40, "observed_at": "2026-11-19T00:00:00Z",
                "state": "ok", "items": [
                    {"dir_name": "only-in-git", "id": "grok-workflow:only-in-git",
                     "name": "GitOnly", "body_hash_state": "observed",
                     "body_sha256": "a" * 64, "tree_state": "observed",
                     "tree_sha256": "a" * 64, "files": []}]}

    def read(self, now=None):
        return box_snapshot.read_box_snapshot(
            self.mini_state, "grok-box", now or datetime.now(timezone.utc))

    def test_absent(self):
        doc, info = self.read()
        self.assertIsNone(doc)
        self.assertEqual(info["source_state"], "absent")
        payload = box_snapshot.build_box_config(state_dir=self.mini_state)
        self.assertEqual(payload["source_state"], "absent")
        self.assertIsNone(payload["workflows"])

    def test_ok(self):
        self.install()
        doc, info = self.read()
        self.assertIsNotNone(doc)
        self.assertEqual(info["source_state"], "ok")
        self.assertGreaterEqual(info["age_seconds"], 0)

    def test_bad_copy(self):
        target = self.mini_state / "box-snapshots/grok-box"
        private_directory(target, create=True)
        path = target / "snapshot.json"
        path.write_text("{not json")
        path.chmod(0o600)
        doc, info = self.read()
        self.assertIsNone(doc)
        self.assertEqual((info["source_state"], info["reason_code"]),
                         ("bad", "invalid_snapshot"))

    def test_stale(self):
        self.install()
        now = datetime.now(timezone.utc) + timedelta(hours=37)
        doc, info = self.read(now)
        self.assertEqual(info["source_state"], "stale")

    def test_rejected_keeps_previous_copy(self):
        self.install(status={"schema": "box-config-fetch-status/v1", "state": "rejected",
                             "reason_code": "non_allowlisted_path",
                             "checked_at": "2026-11-19T00:00:00Z",
                             "verified_commit": "a" * 40})
        doc, info = self.read()
        self.assertIsNotNone(doc)  # previous valid copy still reported
        self.assertEqual(info["source_state"], "rejected")
        self.assertEqual(info["reason_code"], "non_allowlisted_path")
        payload = box_snapshot.build_box_config(state_dir=self.mini_state)
        self.assertEqual(payload["source_state"], "rejected")
        self.assertEqual(payload["fetch"]["reason_code"], "non_allowlisted_path")
        self.assertIsNotNone(payload["domain_counts"])  # copy still summarized

    def test_snapshot_without_domain_states_still_serves(self):
        # domain_states is optional in the schema; a legacy copy stays servable.
        doc = json.loads(self.snapshot.read_text())
        del doc["domain_states"]
        self.install(doc=doc, git_leg=self.git_leg())
        payload = box_snapshot.build_box_config(state_dir=self.mini_state)
        self.assertEqual(payload["source_state"], "ok")
        self.assertIsNone(payload["domain_states"])
        self.assertIsNotNone(payload["domain_counts"])
        self.assertIsNone(payload["domain_counts"]["hooks"])
        self.assertEqual(payload["domain_counts"]["workflows"],
                         len(doc["domains"]["workflows"]))
        self.assertEqual(payload["domain_counts"]["mcps"], len(doc["domains"]["mcps"]))
        states = {row["state"] for row in payload["workflows"]["items"]}
        self.assertEqual(states, {"unknown"})  # live leg not_collected -> unknown

    def test_missing_git_leg_yields_unknown_rows_not_missing(self):
        self.install()
        payload = box_snapshot.build_box_config(state_dir=self.mini_state)
        self.assertEqual(payload["source_state"], "ok")
        self.assertIsNone(payload["git_leg"])
        states = {row["state"] for row in payload["workflows"]["items"]}
        self.assertEqual(states, {"unknown"})
        reasons = {reason for row in payload["workflows"]["items"]
                   for reason in row["reason_codes"]}
        self.assertEqual(reasons, {"git_not_collected"})

    def test_corrupt_git_leg_yields_unknown_rows_not_missing(self):
        self.install(git_leg="{not json")
        payload = box_snapshot.build_box_config(state_dir=self.mini_state)
        self.assertEqual(payload["source_state"], "ok")
        self.assertEqual(payload["git_leg"]["state"], "error")
        states = {row["state"] for row in payload["workflows"]["items"]}
        self.assertEqual(states, {"unknown"})

    def test_git_leg_summary_is_exposed(self):
        self.install(git_leg=self.git_leg())
        payload = box_snapshot.build_box_config(state_dir=self.mini_state)
        self.assertEqual(payload["git_leg"]["state"], "ok")
        self.assertEqual(payload["git_leg"]["commit"], "a" * 40)
        rows = {row["id"]: row["state"] for row in payload["workflows"]["items"]}
        self.assertEqual(rows["grok-workflow:only-in-git"], "missing_on_box")

    def test_uncollected_domains_are_not_numbered(self):
        # ACM-1005: not_collected/absent/denied/error are not a number, and a
        # stale hooks=ok snapshot must not report the not_collected string.
        doc = json.loads(self.snapshot.read_text())
        doc["domain_states"]["memory"] = "not_collected"
        doc["domain_states"]["routines"] = "not_collected"
        doc["domain_states"]["attachments"] = "not_collected"
        doc["domain_states"]["profiles"] = "absent"
        doc["domain_states"]["managed_skills"] = "denied"
        doc["domain_states"]["cloud_skills"] = "error"
        doc["domain_states"]["hooks"] = "ok"
        doc["domains"]["attachments"] = []
        self.install(doc=doc, git_leg=self.git_leg())
        payload = box_snapshot.build_box_config(state_dir=self.mini_state)
        counts = payload["domain_counts"]
        for name in ("memory", "routines", "attachments", "hooks",
                     "profiles", "managed_skills", "cloud_skills"):
            self.assertIsNone(counts[name], name)
        self.assertNotIsInstance(counts["hooks"], (int, str))
        self.assertEqual(payload["domain_states"]["hooks"], "not_collected")
        self.assertEqual(counts["workflows"], len(doc["domains"]["workflows"]))
        # ACM-1008: this fixture mixes one observed box_setting with turn
        # placeholders. Placeholders are not counted, and ok is not claimed.
        collected = [row for row in doc["domains"]["mcps"]
                     if row.get("evidence_level") != "not_collected"]
        self.assertGreater(len(doc["domains"]["mcps"]), len(collected))
        self.assertGreater(len(collected), 0)
        self.assertEqual(payload["domain_states"]["mcps"], "partial")
        self.assertEqual(counts["mcps"], len(collected))
        stored = json.loads((self.mini_state / "box-snapshots/grok-box/snapshot.json").read_text())
        self.assertEqual(stored["domain_states"]["hooks"], "ok")
        self.assertEqual(stored["domain_states"]["mcps"], "ok")

    def test_mcps_display_splits_placeholders_from_collected_rows(self):
        # ACM-1008: response only. Snapshot domain_states.mcps stays as collected.
        doc = json.loads(self.snapshot.read_text())
        placeholders = [row for row in doc["domains"]["mcps"]
                        if row.get("evidence_level") == "not_collected"]
        settings = [row for row in doc["domains"]["mcps"] if row.get("kind") == "box_setting"]
        self.assertGreater(len(placeholders), 0)
        self.assertEqual(len(settings), 1)
        self.assertEqual(doc["domain_states"]["mcps"], "ok")

        def present(rows, state):
            sample = json.loads(self.snapshot.read_text())
            sample["domains"]["mcps"] = rows
            sample["domain_states"]["mcps"] = state
            self.install(doc=sample, git_leg=self.git_leg())
            payload = box_snapshot.build_box_config(state_dir=self.mini_state)
            stored = json.loads(
                (self.mini_state / "box-snapshots/grok-box/snapshot.json").read_text())
            self.assertEqual(stored["domain_states"]["mcps"], state)
            self.assertEqual(stored["domains"]["mcps"], rows)
            self.assertEqual(payload["domain_counts"]["workflows"],
                             len(sample["domains"]["workflows"]))
            self.assertIsNone(payload["domain_counts"]["hooks"])
            return payload

        only_placeholders = present(placeholders, "ok")
        self.assertEqual(only_placeholders["domain_states"]["mcps"], "not_collected")
        self.assertIsNone(only_placeholders["domain_counts"]["mcps"])
        self.assertNotEqual(only_placeholders["domain_counts"]["mcps"], len(placeholders))

        only_settings = present(settings, "ok")
        self.assertEqual(only_settings["domain_states"]["mcps"], "ok")
        self.assertEqual(only_settings["domain_counts"]["mcps"], 1)

        mixed = present(settings + placeholders, "ok")
        self.assertEqual(mixed["domain_states"]["mcps"], "partial")
        self.assertEqual(mixed["domain_counts"]["mcps"], 1)

        failed_read = present(placeholders, "partial")
        self.assertEqual(failed_read["domain_states"]["mcps"], "partial")
        self.assertIsNone(failed_read["domain_counts"]["mcps"])

        failed_with_settings = present(settings + placeholders, "partial")
        self.assertEqual(failed_with_settings["domain_states"]["mcps"], "partial")
        self.assertEqual(failed_with_settings["domain_counts"]["mcps"], 1)

        for state in ("not_collected", "absent", "denied", "error"):
            held = present(settings + placeholders, state)
            self.assertEqual(held["domain_states"]["mcps"], state)
            self.assertIsNone(held["domain_counts"]["mcps"])

        empty_ok = present([], "ok")
        self.assertEqual(empty_ok["domain_states"]["mcps"], "ok")
        self.assertEqual(empty_ok["domain_counts"]["mcps"], 0)

        missing = json.loads(self.snapshot.read_text())
        del missing["domain_states"]["mcps"]
        self.install(doc=missing, git_leg=self.git_leg())
        payload = box_snapshot.build_box_config(state_dir=self.mini_state)
        self.assertNotIn("mcps", payload["domain_states"])
        self.assertIsNone(payload["domain_counts"]["mcps"])

    def test_boxsync_unavailable_degrades_to_absent(self):
        with mock.patch.dict(sys.modules, {"boxsync": None}):
            payload = box_snapshot.build_box_config(state_dir=self.mini_state)
        self.assertEqual(payload["source_state"], "absent")
        self.assertIsNone(payload["workflows"])


if __name__ == "__main__":
    unittest.main()
