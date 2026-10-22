from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.skill_content import MAX_BYTES, read_content
from multica_board.skill_runtimes import runtime_columns
from multica_board.server import BoardServer
from multica_board.views import Views


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.skill = self.repo / "skills" / "demo"
        self.skill.mkdir(parents=True)
        self.raw = b"---\r\nname: demo\r\n---\r\n# Raw <html>\r\n"
        (self.skill / "SKILL.md").write_bytes(self.raw)
        (self.skill / "references").mkdir()
        (self.skill / "references" / "note.txt").write_text("hello")
        self.doc = {"items": [{"name": "demo", "entries": [{"path": str(self.skill)}],
                               "local_source": {"path": "skills/demo/SKILL.md"}}]}

    def read(self, **args):
        return read_content(self.doc, name="demo", path=str(self.skill), repo_root=self.repo, **args)

    def test_raw_markdown_and_relative_text(self):
        result = self.read()
        self.assertEqual(result["content"].encode(), self.raw)
        self.assertEqual(result["bytes"], len(self.raw))
        self.assertEqual(result["source"], "local")
        self.assertEqual(result["evidence_state"], "observed")
        self.assertEqual(self.read(file="references/note.txt")["content"], "hello")
        local = read_content(self.doc, name="demo", path="skills/demo/SKILL.md", repo_root=self.repo)
        self.assertEqual(local["state"], "ok")

    def test_empty_missing_binary_and_large(self):
        (self.skill / "empty").touch()
        self.assertEqual(self.read(file="empty")["state"], "empty")
        self.assertEqual(self.read(file="absent")["state"], "missing")
        for data in (b"\x00binary", b"\xff\xfe"):
            (self.skill / "binary").write_bytes(data)
            self.assertEqual(self.read(file="binary")["state"], "unsupported")
        (self.skill / "large").write_bytes(b"x" * (MAX_BYTES + 1))
        self.assertEqual(self.read(file="large")["state"], "too_large")
        (self.skill / "large").write_bytes(b"x" * MAX_BYTES)
        self.assertEqual(self.read(file="large")["state"], "ok")

    def test_reject_arbitrary_path_name_traversal_and_special_files(self):
        for file in ("../secret", "references/../../secret", "/etc/passwd", "a//b", "./SKILL.md", "a\\b", "x\x00"):
            self.assertEqual(self.read(file=file)["state"], "denied", file)
        for name, path in (("other", str(self.skill)), ("demo", str(self.repo)),
                           ("demo", str(self.skill / ".." / "demo"))):
            result = read_content(self.doc, name=name, path=path, repo_root=self.repo)
            self.assertEqual(result["state"], "denied")
        os.mkfifo(self.skill / "pipe")
        self.assertEqual(self.read(file="pipe")["state"], "denied")

    def test_descendant_symlinks_are_never_followed(self):
        secret = self.repo / "secret"
        secret.write_text("private")
        (self.skill / "escape").symlink_to(secret)
        (self.skill / "escape-dir").symlink_to(self.repo, target_is_directory=True)
        (self.skill / "inside").symlink_to(self.skill / "SKILL.md")
        for file in ("escape", "escape-dir/secret", "inside"):
            self.assertEqual(self.read(file=file)["state"], "denied")
        files = {item["path"]: item for item in self.read(tree=True)["files"]}
        self.assertEqual(files["escape"]["type"], "symlink")
        self.assertFalse(files["escape"]["readable"])
        self.assertNotIn("escape-dir/secret", files)

    def test_known_root_link_and_tree_bounds(self):
        link = self.repo / "adapter"
        link.symlink_to(self.skill, target_is_directory=True)
        doc = {"items": [{"name": "demo", "entries": [{"path": str(link)}]}]}
        self.assertEqual(read_content(doc, name="demo", path=str(link), repo_root=self.repo)["state"], "ok")
        tree = self.read(tree=True)
        self.assertEqual(next(f for f in tree["files"] if f["path"] == "references/note.txt")["bytes"], 5)
        with mock.patch("multica_board.skill_content.MAX_TREE_ENTRIES", 2):
            tree = self.read(tree=True)
        self.assertEqual(len(tree["files"]), 2)
        self.assertTrue(tree["truncated"])

    def test_cloud_does_not_touch_disk(self):
        with mock.patch("multica_board.skill_content._known_root", side_effect=AssertionError):
            result = self.read(source="cloud")
        self.assertEqual(result["state"], "uncollected")
        self.assertIsNone(result["collected_at"])
        self.assertNotIn("content", result)

    def test_inventory_version_pattern_and_retargeted_entry(self):
        patterned = {"items": [{"name": "demo", "entries": [
            {"path": "plugins/*/demo", "resolved": str(self.skill.resolve())}]}]}
        result = read_content(patterned, name="demo", path="plugins/*/demo", repo_root=self.repo)
        self.assertEqual(result["state"], "ok")
        link = self.repo / "adapter"
        link.symlink_to(self.skill, target_is_directory=True)
        doc = {"items": [{"name": "demo", "entries": [
            {"path": str(link), "resolved": str(self.skill.resolve())}]}]}
        link.unlink()
        link.symlink_to(self.repo, target_is_directory=True)
        self.assertEqual(read_content(doc, name="demo", path=str(link), repo_root=self.repo)["state"], "denied")

    def test_permission_and_retarget_race(self):
        original_open = os.open
        def denied(path, *args, **kwargs):
            if path == "SKILL.md":
                raise PermissionError(13, "private path must not leak")
            return original_open(path, *args, **kwargs)
        with mock.patch("multica_board.skill_content.os.open", side_effect=denied):
            result = self.read()
        self.assertEqual(result["state"], "denied")
        self.assertNotIn("private path", result["message"])
        # Swap a descendant between root authorization and open: O_NOFOLLOW
        # must reject it rather than opening a file outside the skill.
        def swap(path, *args, **kwargs):
            if path == "note.txt":
                target = self.skill / "references" / "note.txt"
                target.unlink()
                target.symlink_to(self.repo / "secret")
            return original_open(path, *args, **kwargs)
        (self.repo / "secret").write_text("private")
        with mock.patch("multica_board.skill_content.os.open", side_effect=swap):
            self.assertEqual(self.read(file="references/note.txt")["state"], "denied")

    def test_http_route_and_parameter_validation(self):
        views = mock.Mock()
        views.skills_content.side_effect = lambda **args: read_content(self.doc, repo_root=self.repo, **args)
        server = BoardServer("127.0.0.1", 0, views)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            def request(query):
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request("GET", "/api/skills/content?" + query)
                response = connection.getresponse()
                body = json.loads(response.read())
                connection.close()
                return response.status, body
            status, result = request(urlencode({"name": "demo", "path": str(self.skill)}))
            self.assertEqual((status, result["state"]), (200, "ok"))
            status, result = request(urlencode({"name": "demo", "path": str(self.skill), "tree": "1"}))
            self.assertTrue(result["files"])
            for query in ("name=demo", "name=demo&source=invalid", "name=a&name=b", "name=a&source=cloud&tree=2",
                          "name=a&source=cloud&extra=x"):
                self.assertEqual(request(query)[0], 400, query)
            self.assertEqual(request("name=demo&source=cloud")[1]["state"], "uncollected")
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


class RuntimeColumnsTests(unittest.TestCase):
    def test_union_dedup_unknown_and_provenance_without_mutating_cache(self):
        document = {"generated_at": "local-time", "tools": [{"id": "codex", "label": "Codex"}],
                    "items": [{"name": "demo", "tools": {"codex": "shared"},
                               "evidence": [{"harness": "future"}]}]}
        runtimes = {"generated_at": "cloud-time", "items": [
            {"provider": "Codex", "id": "1", "workspace_id": "a"},
            {"provider": "codex", "id": "2", "workspace_id": "b"},
            {"provider": "mcode", "id": "3"}, {"provider": "next-tool", "id": "4"},
            {"provider": None, "id": "5"}]}
        roots = [{"path": "~/.agents/skills", "kind": "shared", "tools": ["codex"]}]
        result = runtime_columns(document, runtimes, roots)
        self.assertEqual(result["tool_filter_ids"], ["codex", "mcode", "next-tool", "unknown", "future"])
        columns = {t["id"]: t for t in result["tools"]}
        self.assertEqual(len(columns["codex"]["runtime_ids"]), 2)
        self.assertEqual(columns["mcode"]["roots_state"], "unconfigured")
        self.assertEqual(columns["codex"]["sources"][1]["collected_at"], "cloud-time")
        self.assertEqual(result["items"][0]["runtime_evidence"]["mcode"]["state"], "unknown")
        self.assertIsNone(result["items"][0]["runtime_evidence"]["codex"]["runtime_loaded"])
        self.assertNotIn("runtime_evidence", document["items"][0])

    def test_views_content_uses_local_document_only(self):
        views = Views(mock.Mock(), "test", app_url="https://example.test", collect_interval_s=60)
        with mock.patch("multica_board.skills_view.skills_document", side_effect=AssertionError):
            self.assertEqual(views.skills_content(name="cloud", source="cloud")["state"], "uncollected")


if __name__ == "__main__":
    unittest.main()
