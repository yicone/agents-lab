from __future__ import annotations

import http.client
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from multica_board import instruction_diff  # noqa: E402  (prepares scripts sys.path)
from multica_board.instruction_diff import GitSourceUnreadable, build_instruction_diff  # noqa: E402
from multica_board.server import BoardServer  # noqa: E402
from multica_board.store import Store  # noqa: E402
from multica_board.views import Views  # noqa: E402

import diff_instructions as src  # noqa: E402
import query_instructions as query  # noqa: E402

SERVER = "test-profile"
WS = "ws-a"
NOW = datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(text: str) -> str:
    return src.instructions_sha256(text)


def git_index(root: Path, agents: list[dict], bots: list[dict]) -> None:
    (root / "sources").mkdir(parents=True, exist_ok=True)
    (root / "sources" / "index.json").write_text(
        json.dumps({"schema": "instructions-sources/v1", "agents": agents, "bots": bots}),
        encoding="utf-8")


def git_file(root: Path, rel: str, text: str) -> None:
    path = root / "sources" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def board_file(static: Path, board: dict) -> Path:
    static.mkdir(parents=True, exist_ok=True)
    path = static / "instruction-sources.json"
    path.write_text(json.dumps(board, ensure_ascii=False), encoding="utf-8")
    return path


def board_of(*, agents: dict | None = None, bots: dict | None = None) -> dict:
    return {"schema": "instruction-sources/v1", "agents": agents or {}, "bots": bots or {}}


def build(root, board_path, agents, agents_at, bots=None, bots_at=None, bot_error=False, now=None):
    return build_instruction_diff(
        root=root, board_path=board_path, agents=agents, agents_observed_at=agents_at,
        bots=bots or [], bots_observed_at=bots_at, bot_error=bot_error,
        now=now or datetime.now(timezone.utc))


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "instructions-governance"
        self.static = self.base / "static"

    def aligned_fixture(self):
        src.seed_sources(
            self.root,
            agents=[{"workspace_id": WS, "id": "a1", "name": "A1", "instructions": "agent body"}],
            bots=[{"id": "b1", "name": "B1", "description": "bot body"}],
        )
        board = src.export_ui(self.root)
        path = board_file(self.static, board)
        agents = [{"workspace_id": WS, "id": "a1", "name": "A1", "instructions_sha256": sha("agent body")}]
        bots = [{"id": "b1", "name": "B1", "description_sha256": sha("bot body")}]
        return path, agents, bots

    def test_aligned_three_way(self):
        board_path, agents, bots = self.aligned_fixture()
        body = build(self.root, board_path, agents, NOW, bots, NOW)
        self.assertEqual(body["schema"], "instruction-diff/v1")
        self.assertEqual(body["items"], [])
        self.assertEqual(body["summary"]["total"], 2)
        self.assertEqual(body["summary"]["aligned"], 2)
        self.assertEqual(body["freshness"], {"board": "fresh", "agent": "fresh", "bot": "fresh"})
        self.assertEqual(body["source_errors"], {})
        self.assertEqual(body["inaccessible_workspaces"], [])
        self.assertEqual(body["live_sources"], {"agent": "board-cache", "bot": "local-roster-cache"})

    def test_live_hash_change_is_modified(self):
        board_path, agents, bots = self.aligned_fixture()
        agents[0]["instructions_sha256"] = sha("edited live body")
        body = build(self.root, board_path, agents, NOW, bots, NOW)
        row = next(i for i in body["items"] if i["id"] == "a1")
        self.assertEqual(row["drift"], "modified")
        self.assertEqual(row["comparison"], "modified")
        self.assertEqual(row["name"], "A1")
        self.assertEqual(body["summary"]["modified"], 1)

    def test_split_survives_stale_freshness(self):
        git_index(self.root,
                  [{"workspace_id": WS, "id": "a1", "name": "A1", "path": "agents/ws/a1.md"}],
                  [])
        git_file(self.root, "agents/ws/a1.md", "git body")
        board_path = board_file(self.static, board_of(agents={
            f"{WS}/a1": {"name": "A1", "sha256": sha("board body"), "text": "board body"}}))
        agents = [{"workspace_id": WS, "id": "a1", "name": "A1", "instructions_sha256": sha("live body")}]
        old = NOW - timedelta(seconds=400)
        body = build(self.root, board_path, agents, old)
        row = body["items"][0]
        self.assertEqual(row["comparison"], "split")
        self.assertEqual(row["drift"], "stale")
        self.assertIn("live_stale", row["stale_reasons"])
        self.assertEqual(body["freshness"]["agent"], "stale")

    def test_board_missing_marks_source_error_and_stale(self):
        board_path, agents, bots = self.aligned_fixture()
        body = build(self.root, None, agents, NOW, bots, NOW)
        self.assertEqual(body["source_errors"], {"board": "unavailable_or_invalid"})
        self.assertEqual(body["freshness"]["board"], "stale")
        self.assertTrue(body["items"])
        self.assertTrue(all(i["drift"] == "stale" for i in body["items"]))
        self.assertEqual(body["summary"]["aligned"], 0)

    def test_board_bad_schema_same_as_missing(self):
        board_path, agents, bots = self.aligned_fixture()
        board_path.write_text(json.dumps({"schema": "bogus", "agents": {}, "bots": {}}))
        body = build(self.root, board_path, agents, NOW, bots, NOW)
        self.assertEqual(body["source_errors"]["board"], "unavailable_or_invalid")
        self.assertTrue(all(i["drift"] == "stale" for i in body["items"]))

    def test_agent_cache_over_300s_is_stale(self):
        board_path, agents, bots = self.aligned_fixture()
        body = build(self.root, board_path, agents, NOW - timedelta(seconds=301), bots, NOW)
        self.assertEqual(body["freshness"]["agent"], "stale")
        a1 = next(i for i in body["items"] if i["id"] == "a1")
        self.assertEqual(a1["drift"], "stale")
        self.assertIn("live_stale", a1["stale_reasons"])

    def test_agent_error_when_cache_empty_and_unobserved(self):
        board_path, _, bots = self.aligned_fixture()
        body = build(self.root, board_path, [], None, bots, NOW)
        self.assertEqual(body["source_errors"]["agent"], "unavailable_or_invalid")
        self.assertEqual(body["freshness"]["agent"], "stale")

    def test_bot_error_marks_source_error(self):
        board_path, agents, _ = self.aligned_fixture()
        body = build(self.root, board_path, agents, NOW, bot_error=True)
        self.assertEqual(body["source_errors"]["bot"], "unavailable_or_invalid")
        b1 = next(i for i in body["items"] if i["id"] == "b1")
        self.assertEqual(b1["drift"], "stale")
        self.assertIsNone(b1["live_sha256"])

    def test_withheld_row_has_no_hashes(self):
        git_index(self.root,
                  [{"workspace_id": WS, "id": "w1", "name": "W1", "path": None, "withheld": "secret-pattern"}],
                  [])
        body = build(self.root, None, [], NOW)
        row = body["items"][0]
        self.assertEqual(row["drift"], "withheld")
        self.assertIsNone(row["git_sha256"])
        self.assertIsNone(row["board_sha256"])
        self.assertIsNone(row["live_sha256"])

    def test_sorting_split_modified_stale_withheld_agent_before_bot(self):
        git_index(self.root,
                  [{"workspace_id": WS, "id": "a-split", "name": "AS", "path": "agents/ws/a-split.md"},
                   {"workspace_id": WS, "id": "a-mod", "name": "AM", "path": "agents/ws/a-mod.md"},
                   {"workspace_id": WS, "id": "a-held", "name": "AH", "path": None, "withheld": "x"}],
                  [{"id": "b-mod", "name": "BM", "path": "bots/b-mod.md"},
                   {"id": "b-stale", "name": "BS", "path": "bots/b-stale.md"}])
        git_file(self.root, "agents/ws/a-split.md", "git one")
        git_file(self.root, "agents/ws/a-mod.md", "same")
        git_file(self.root, "bots/b-mod.md", "bot same")
        git_file(self.root, "bots/b-stale.md", "bot stale")
        board = board_of(
            agents={f"{WS}/a-split": {"name": "AS", "sha256": sha("board one"), "text": "board one"},
                    f"{WS}/a-mod": {"name": "AM", "sha256": sha("same"), "text": "same"},
                    f"{WS}/a-held": {"name": "AH", "sha256": None, "text": "", "withheld": True}},
            bots={"b-mod": {"name": "BM", "sha256": sha("bot same"), "text": "bot same"},
                  "b-stale": {"name": "BS", "sha256": sha("bot stale"), "text": "bot stale"}})
        path = board_file(self.static, board)
        agents = [{"workspace_id": WS, "id": "a-split", "name": "AS", "instructions_sha256": sha("live one")},
                  {"workspace_id": WS, "id": "a-mod", "name": "AM", "instructions_sha256": sha("other")}]
        bots = [{"id": "b-mod", "name": "BM", "description_sha256": sha("bot other")},
                {"id": "b-stale", "name": "BS", "description_sha256": None}]
        body = build(self.root, path, agents, NOW, bots, NOW)
        order = [(i["id"], i["drift"]) for i in body["items"]]
        self.assertEqual(order, [("a-split", "split"), ("a-mod", "modified"),
                                 ("b-mod", "modified"), ("b-stale", "stale"), ("a-held", "withheld")])
        s = body["summary"]
        self.assertEqual(s["total"], s["aligned"] + s["modified"] + s["stale"] + s["split"] + s["withheld"])
        self.assertEqual(len(body["items"]), s["total"] - s["aligned"])

    def test_no_bodies_or_forbidden_keys(self):
        board_path, agents, bots = self.aligned_fixture()
        agents[0]["instructions_sha256"] = sha("changed")
        body = build(self.root, board_path, agents, NOW, bots, NOW)
        blob = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("agent body", blob)
        self.assertNotIn("bot body", blob)

        def keys(node):
            out = set()
            if isinstance(node, dict):
                for k, v in node.items():
                    out.add(k)
                    out |= keys(v)
            elif isinstance(node, list):
                for v in node:
                    out |= keys(v)
            return out

        self.assertFalse(keys(body) & {"text", "instructions", "description", "skills"})

    def test_no_side_effects_when_subprocess_and_http_blocked(self):
        board_path, agents, bots = self.aligned_fixture()
        boom = AssertionError("must not be called")
        with mock.patch.object(subprocess, "run", side_effect=boom), \
             mock.patch.object(urllib.request, "urlopen", side_effect=boom), \
             mock.patch.object(query, "urlopen", side_effect=boom):
            body = build(self.root, board_path, agents, NOW, bots, NOW)
        self.assertEqual(body["summary"]["total"], 2)

    def test_empty_git_root_is_unreadable_and_writes_nothing(self):
        empty = self.base / "empty-root"
        empty.mkdir()
        before = sorted(p.name for p in empty.iterdir())
        with self.assertRaises(GitSourceUnreadable) as ctx:
            build(empty, None, [], NOW)
        self.assertIn("instructions-governance", str(ctx.exception))
        self.assertEqual(sorted(p.name for p in empty.iterdir()), before)

    def test_missing_git_dir_is_unreadable(self):
        with self.assertRaises(GitSourceUnreadable) as ctx:
            build(self.base / "no-such-dir", None, [], NOW)
        self.assertIn("instructions-governance", str(ctx.exception))

    def test_index_listed_file_missing_is_unreadable(self):
        git_index(self.root,
                  [{"workspace_id": WS, "id": "a1", "name": "A1", "path": "agents/ws/a1.md"}], [])
        with self.assertRaises(GitSourceUnreadable) as ctx:
            build(self.root, None, [], NOW)
        self.assertIn("sources/agents/ws/a1.md", str(ctx.exception))
        self.assertIn("instructions-governance", str(ctx.exception))


class HttpFixture(unittest.TestCase):
    """One aligned workspace served over HTTP."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "instructions-governance"
        self.static = base / "static"
        src.seed_sources(
            self.root,
            agents=[{"workspace_id": WS, "id": "a1", "name": "A1", "instructions": "agent body"}],
            bots=[{"id": "b1", "name": "B1", "description": "bot body"}],
        )
        board_file(self.static, src.export_ui(self.root))
        self.store = Store(base / "board.sqlite")
        self.store.set_workspaces(SERVER, [{"id": WS, "name": "Alpha", "slug": "alpha"}])
        self.store.upsert_many(SERVER, WS, "agents", [
            {"id": "a1", "name": "A1", "instructions_sha256": sha("agent body")}])
        self.store.set_meta("last_collect", {"finished_at": iso(NOW)})
        self.views = Views(self.store, SERVER, app_url="https://app.test",
                           collect_interval_s=60, instructions_root=self.root)
        self.views.bots = lambda: {
            "ok": True, "observed_at": iso(NOW),
            "items": [{"id": "b1", "name": "B1", "description_sha256": sha("bot body")}],
        }
        self.httpd = BoardServer("127.0.0.1", 0, self.views, static_dir=self.static)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.store.close()

    def request(self, method: str, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, body

    def test_get_200_contract(self):
        status, raw = self.request("GET", "/api/instruction-diff")
        self.assertEqual(status, 200, raw)
        body = json.loads(raw)
        self.assertEqual(body["schema"], "instruction-diff/v1")
        s = body["summary"]
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["aligned"], 2)
        self.assertEqual(s["total"], s["aligned"] + s["modified"] + s["stale"] + s["split"] + s["withheld"])
        self.assertEqual(len(body["items"]), s["total"] - s["aligned"])
        self.assertEqual(body["inaccessible_workspaces"], [])
        for key in ("generated_at", "freshness", "live_sources", "source_errors"):
            self.assertIn(key, body)
        blob = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("agent body", blob)
        self.assertNotIn("bot body", blob)

    def test_query_rejected_400(self):
        status, raw = self.request("GET", "/api/instruction-diff?x=1")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["error"]["code"], "bad_request")

    def test_methods_rejected_405(self):
        for method in ("POST", "PUT", "DELETE"):
            status, _ = self.request(method, "/api/instruction-diff")
            self.assertEqual(status, 405, method)

    def test_head_200_no_body(self):
        status, body = self.request("HEAD", "/api/instruction-diff")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")

    def test_endpoint_never_spawns_or_fetches(self):
        boom = AssertionError("must not be called")
        with mock.patch.object(subprocess, "run", side_effect=boom), \
             mock.patch.object(urllib.request, "urlopen", side_effect=boom), \
             mock.patch.object(query, "urlopen", side_effect=boom):
            status, raw = self.request("GET", "/api/instruction-diff")
        self.assertEqual(status, 200, raw)


class GitSourceUnreadableHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.store = Store(self.base / "board.sqlite")
        self.store.set_meta("last_collect", {"finished_at": iso(NOW)})

    def tearDown(self):
        self.store.close()

    def serve(self, root: Path) -> int:
        views = Views(self.store, SERVER, app_url="https://app.test",
                      collect_interval_s=60, instructions_root=root)
        httpd = BoardServer("127.0.0.1", 0, views)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def get(self, port: int):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/instruction-diff")
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, body

    def test_empty_dir_returns_500_readable_message(self):
        empty = self.base / "empty-root"
        empty.mkdir()
        before = sorted(p.name for p in empty.iterdir())
        port = self.serve(empty)
        status, raw = self.get(port)
        self.assertEqual(status, 500, raw)
        err = json.loads(raw)["error"]
        self.assertEqual(err["code"], "git_source_unreadable")
        self.assertTrue(err["message"])
        self.assertIn("instructions-governance", err["message"])
        self.assertNotIn(str(self.base), err["message"])  # no absolute path leak
        self.assertEqual(sorted(p.name for p in empty.iterdir()), before)

    def test_index_referenced_file_missing_returns_500(self):
        root = self.base / "instructions-governance"
        git_index(root, [{"workspace_id": WS, "id": "a1", "name": "A1", "path": "agents/ws/a1.md"}], [])
        port = self.serve(root)
        status, raw = self.get(port)
        self.assertEqual(status, 500, raw)
        err = json.loads(raw)["error"]
        self.assertEqual(err["code"], "git_source_unreadable")
        self.assertIn("instructions-governance", err["message"])
        self.assertIn("sources/agents/ws/a1.md", err["message"])

    def test_index_non_list_kinds_return_500(self):
        root = self.base / "instructions-governance"
        git_index(root, {}, [])
        port = self.serve(root)
        status, raw = self.get(port)
        self.assertEqual(status, 500, raw)
        self.assertEqual(json.loads(raw)["error"]["code"], "git_source_unreadable")

    def test_missing_query_instructions_module_returns_500(self):
        root = self.base / "instructions-governance"
        git_index(root, [], [])
        port = self.serve(root)
        with mock.patch.dict(sys.modules, {"query_instructions": None}):
            status, raw = self.get(port)
        self.assertEqual(status, 500, raw)
        err = json.loads(raw)["error"]
        self.assertEqual(err["code"], "git_source_unreadable")
        self.assertNotEqual(err["code"], "internal")
        self.assertIn("query_instructions.py", err["message"])
        self.assertIn("由用户另批", err["message"])
        self.assertNotIn(str(self.base), err["message"])


if __name__ == "__main__":
    unittest.main()
