import contextlib
import base64
import http.server
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
query = importlib.import_module("query_instructions")
source = query.source


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.agents = [{"workspace_id": "ws", "id": "a", "instructions": "agent body"}]
        self.bots = [{"id": "b", "description": "bot body"}]
        source.seed_sources(self.root, self.agents, self.bots)
        self.board = source.export_ui(self.root)

    def report(self, **kwargs):
        return query.matrix(self.root, self.board, self.agents, self.bots,
                            observed_at={"agent": "now", "bot": "now"},
                            freshness=kwargs.get("freshness", {"agent": "fresh", "bot": "fresh"}))

    def test_agent_and_bot_aligned_and_no_text_or_skills(self):
        report = self.report()
        self.assertEqual(report["summary"], {"aligned": 2})
        for row in report["items"]:
            self.assertTrue(row["path"].startswith("instructions-governance/sources/"))
            self.assertEqual(row["git_sha256"], row["board_sha256"])
            self.assertEqual(row["git_sha256"], row["live_sha256"])
        self.assertNotIn("body", json.dumps(report))
        self.assertNotIn("skills", json.dumps(report))

    def test_stale_board_and_three_way_split(self):
        self.board["agents"]["ws/a"].update(text="old", sha256=source.instructions_sha256("old"))
        self.assertEqual(self.report()["items"][0]["drift"], "stale")
        self.agents[0]["instructions"] = "another body"
        self.assertEqual(self.report()["items"][0]["drift"], "split")

    def test_bot_changed_and_cache_age_stale(self):
        self.bots[0]["description"] = "new"
        self.assertEqual(self.report()["items"][1]["drift"], "modified")
        row = self.report(freshness={"agent": "fresh", "bot": "stale"})["items"][1]
        self.assertEqual(row["drift"], "stale")
        self.assertEqual(row["comparison"], "modified")

    def test_missing_hash_is_not_aligned_even_all_empty(self):
        self.agents.clear()
        row = self.report()["items"][0]
        self.assertEqual(row["drift"], "stale")
        self.assertIn("live_hash_missing", row["stale_reasons"])

    def test_corrupt_board_hash_and_wrong_workspace(self):
        self.board["agents"]["ws/a"]["sha256"] = "b" * 64
        self.agents[0]["workspace_id"] = "other"
        rows = self.report()["items"]
        self.assertEqual(len(rows), 3)
        row = next(r for r in rows if r["workspace_id"] == "ws")
        self.assertIn("board_hash_mismatch", row["stale_reasons"])
        self.assertIsNone(row["live_sha256"])

    def test_withheld_never_exposes_hashes(self):
        index_path = self.root / "sources/index.json"
        index = json.loads(index_path.read_text())
        index["agents"][0]["withheld"] = True
        index_path.write_text(json.dumps(index))
        row = self.report()["items"][0]
        self.assertEqual(row["drift"], "withheld")
        self.assertTrue(all(row[k] is None for k in ("git_sha256", "board_sha256", "live_sha256")))

    def cli(self, board_args):
        for name, payload in (("agents", self.agents), ("bots", self.bots)):
            (self.root / (name + ".json")).write_text(json.dumps(payload))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(query.main(["--root", str(self.root), *board_args,
                             "--agents-json", str(self.root / "agents.json"),
                             "--bots-json", str(self.root / "bots.json")]), 0)
        return json.loads(output.getvalue())

    def test_one_shot_cli_with_http_board(self):
        payload = json.dumps(self.board).encode()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            report = self.cli(["--board-url", f"http://127.0.0.1:{server.server_port}/instruction-sources.json"])
            self.assertEqual(report["summary"], {"aligned": 2})
            self.assertEqual(report["board_source"], "http")
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_board_failure_stays_script_readable_and_stale(self):
        report = self.cli(["--board-json", str(self.root / "missing.json")])
        self.assertEqual(report["summary"], {"stale": 2})
        self.assertEqual(report["source_errors"], {"board": "unavailable_or_invalid"})

    def test_executable_cli_process(self):
        board = self.root / "board.json"
        board.write_text(json.dumps(self.board))
        self.cli(["--board-json", str(board)])
        result = subprocess.run([sys.executable, str(SCRIPTS / "query_instructions.py"),
                                 "--root", str(self.root), "--board-json", str(board),
                                 "--agents-json", str(self.root / "agents.json"),
                                 "--bots-json", str(self.root / "bots.json")],
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["summary"], {"aligned": 2})

    def test_default_collectors_and_roster_observation_age(self):
        board = self.root / "board.json"
        board.write_text(json.dumps(self.board))

        def blob(key, payload):
            filename = base64.b32encode(key.encode()).decode().rstrip("=").lower() + ".blob"
            path = self.root / filename
            path.write_text(json.dumps(payload))
            return path

        slot = blob(source.SLOT_KEY, "account")
        os.utime(slot, (1, 1))  # slot age is NOT roster age
        roster = blob(source.ACCOUNT_PREFIX + "account" + source.ROSTER_SUFFIX, {"rows": self.bots})
        for expired in (False, True):
            if expired:
                os.utime(roster, (1, 1))
            output = io.StringIO()
            with mock.patch.object(source, "collect_agents", return_value=(self.agents, [])), contextlib.redirect_stdout(output):
                query.main(["--root", str(self.root), "--board-json", str(board), "--roster-dir", str(self.root)])
            report = json.loads(output.getvalue())
            self.assertEqual(report["live_sources"]["bot"], "local-roster-cache")
            self.assertEqual(report["items"][1]["drift"], "stale" if expired else "aligned")

    def test_board_snapshot_age_and_future_age_are_stale(self):
        for stamp in (1, 9999999999):
            board = self.root / "board.json"
            board.write_text(json.dumps(self.board))
            os.utime(board, (stamp, stamp))
            report = self.cli(["--board-json", str(board)])
            self.assertEqual(report["summary"], {"stale": 2})
            self.assertIn("board_stale", report["items"][0]["stale_reasons"])

    def test_invalid_snapshot_identity_rejected(self):
        path = self.root / "bad.json"
        for rows in ([{"instructions": "x"}], [{"id": "a"}], self.agents * 2):
            path.write_text(json.dumps(rows))
            with self.assertRaises(ValueError):
                query.snapshot(path, "agents")

    def test_hash_convention_matches_backend_and_keeps_frontmatter(self):
        sys.path.insert(0, str(SCRIPTS.parents[1] / "multica-board/backend"))
        from multica_board.collector import instructions_sha256
        text = "  ---\r\nrole: cafe\u0301\r\n---\r\nbody  "
        self.assertEqual(source.instructions_sha256(text), instructions_sha256(text))
        self.assertNotEqual(source.instructions_sha256(text), instructions_sha256("body"))


if __name__ == "__main__":
    unittest.main()
