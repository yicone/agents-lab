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




class CloudContentTests(unittest.TestCase):
    WS = "00000000-0000-4000-8000-000000000003"
    SK = "00000000-0000-4000-8000-000000000002"

    def setUp(self):
        from multica_board.cloud_skill_content import CloudSkillReader
        from boxsync.snapshot import Context
        self.Context = Context
        self.now = "2026-11-16T12:00:00Z"

    def row(self, payload=None, reason=None):
        from boxsync.domains.cloud_skills import collect
        from boxsync.multica_cli import CliError
        calls = []
        ws, sk = self.WS, self.SK
        class Fake:
            def run(self, args, workspace_id=None):
                calls.append((args, workspace_id))
                if args == ["skill", "list"]:
                    return [{"id": sk, "name": "demo", "updated_at": "2026-11-16T12:00:00Z"}]
                if reason:
                    raise CliError(reason)
                return {"id": sk, "workspace_id": ws, **(payload or {})}
        ctx = self.Context(cli=Fake(), workspaces=(ws,))
        rows, _ = collect(ctx)
        return rows[0], ctx, calls

    def test_seven_states_schema_and_s1_matrix(self):
        import hashlib
        from boxsync.validation import load_schema, validate
        from multica_board.skill_registration import registration_fields
        schema = load_schema("snapshot")
        cases = [({"content": "hello\r\n"}, None, "observed", "observed", 7),
                 ({"content": ""}, None, "empty", "observed", 0),
                 ({"content": "password=fictional"}, None, "withheld_secret", "observed", 18),
                 ({"content": "x" * (MAX_BYTES + 1)}, None, "too_large", "too_large", MAX_BYTES + 1),
                 ({}, "auth", "denied", "denied", None),
                 ({}, "error", "error", "error", None),
                 ({}, None, "uncollected", "uncollected", None)]
        for payload, reason, state, s1, size in cases:
            with self.subTest(state=state):
                row, ctx, _ = self.row(payload, reason)
                validate(row, schema["$defs"]["cloudSkill"], schema)
                self.assertEqual(row["body_state"], state)
                self.assertEqual(row["body_bytes"], size)
                expected = hashlib.sha256(payload["content"].encode()).hexdigest() if s1 == "observed" else None
                self.assertEqual(row["body_sha256"], expected)
                install = {"workspace_id": self.WS, "id": self.SK, "content": row,
                           "agents": [{"id": "agent", "enabled": True}]}
                item = {"name": "demo", "origin": "cloud", "workspace_installs": [install]}
                out = registration_fields({"items": [item]}, repo_root=Path("/unused"))["items"][0]
                self.assertEqual(out["body_hash_state"], s1)
                self.assertEqual(out["body_sha256"], expected)
                self.assertEqual(out["loaders"][0]["load_state"], "unverified")
                self.assertIsNone(out["loaders"][0]["runtime_loaded"])
                self.assertNotIn("content", row)
                from boxsync.snapshot import collect as snapshot_collect
                ctx.selected = ("cloud_skills",)
                ctx.inaccessible_ids.clear()
                snapshot, _ = snapshot_collect(ctx, tool={"commit": "a" * 40, "dirty": False, "version": "0.2.0"})
                self.assertNotIn("content", snapshot["domains"]["cloud_skills"][0])
                if reason == "auth":
                    self.assertEqual(ctx.inaccessible_ids, {self.WS: "auth"})
                if state == "withheld_secret":
                    self.assertNotIn("fictional", json.dumps(row))

    def test_updated_at_reuses_only_valid_prior_hash(self):
        from boxsync.domains.cloud_skills import collect
        row, ctx, calls = self.row({"content": "hello"})
        ctx.previous_cloud_skills = (row,)
        calls.clear()
        again, _ = collect(ctx)
        self.assertEqual(again[0], row)
        self.assertEqual(sum("--with-content" in args for args, _ in calls), 0)
        row["updated_at"] = "2026-11-15T12:00:00Z"
        collect(ctx)
        self.assertEqual(sum("--with-content" in args for args, _ in calls), 1)
        row["updated_at"] = "2026-11-16T12:00:00Z"
        row["body_sha256"] = "bad"
        collect(ctx)
        self.assertEqual(sum("--with-content" in args for args, _ in calls), 2)

    def test_tree_limit_unicode_boundary_and_malformed_scope(self):
        row, _, _ = self.row({"content": "# hi\n" * (MAX_BYTES // 5) + " " * (MAX_BYTES % 5), "files": [
            {"path": f"refs/{i}.md", "content": "hello"} for i in range(2001)]})
        self.assertEqual(row["body_state"], "observed")
        self.assertEqual(len(row["files"]), 2000)
        self.assertTrue(row["files_truncated"])
        row, _, _ = self.row({"content": "中" * (MAX_BYTES // 3 + 1)})
        self.assertEqual(row["body_state"], "too_large")
        for payload in ({"id": self.WS, "content": "foreign"}, {"workspace_id": self.SK, "content": "foreign"},
                        {"files": [{"path": "../escape", "content": "x"}]}):
            row, _, _ = self.row(payload)
            self.assertEqual(row["body_state"], "error")

    def test_fake_executable_api_and_sqlite_never_store_body(self):
        from multica_board.cli_runner import CliRunner
        from multica_board.store import Store
        from multica_board.cloud_skill_content import CloudSkillReader
        from boxsync.multica_cli import MAX_OUTPUT
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            binary = path / "fake-cli"
            def install(code):
                binary.write_text("#!/usr/bin/env python3\n" + code)
                binary.chmod(0o700)
            runner = CliRunner(profile="fixture", binary=str(binary))
            store = Store(path / "board.sqlite")
            self.addCleanup(store.close)
            views = Views(store, "fixture", app_url="http://localhost", collect_interval_s=60, cloud_runner=runner)
            views._workspace_installs = lambda: {"demo": [{"workspace_id": self.WS, "id": self.SK}]}
            args = dict(name="demo", source="cloud", workspace_id=self.WS, skill_id=self.SK)
            marker = "Unique ephemeral cloud body sentinel."
            payload = {"id": self.SK, "workspace_id": self.WS, "content": marker}
            install("import json,sys\nassert '--with-content' in sys.argv\nassert sys.argv[sys.argv.index('--workspace-id')+1]==" + repr(self.WS) + "\nprint(" + repr(json.dumps(payload)) + ")\n")
            before = list(store._conn.iterdump())
            result = views.skills_content(**args)
            self.assertEqual(result["content"], marker)
            self.assertEqual(result["state"], "ok")
            server = BoardServer("127.0.0.1", 0, views)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                query = urlencode({"source": "cloud", "workspace_id": self.WS, "skill_id": self.SK})
                conn = http.client.HTTPConnection(*server.server_address)
                conn.request("GET", "/api/skills/content?" + query)
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("Cache-Control"), "no-store")
                self.assertEqual(json.loads(response.read())["content"], marker)
                conn.close()
                for patch in ({"workspace_id": "--profile=owner"}, {"skill_id": "../file"}):
                    conn = http.client.HTTPConnection(*server.server_address)
                    conn.request("GET", "/api/skills/content?" + urlencode({**args, **patch}))
                    response = conn.getresponse()
                    self.assertEqual(response.status, 400)
                    response.read()
                    conn.close()
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
            self.assertEqual(list(store._conn.iterdump()), before)
            self.assertNotIn(marker, "\n".join(store._conn.iterdump()))
            self.assertNotIn(marker.encode(), (path / "board.sqlite").read_bytes())
            # No retained body on the reader, even after success.
            self.assertNotIn(marker, repr(views._cloud_reader.__dict__))
            self.assertEqual(views.skills_content(name="demo", source="cloud")["state"], "uncollected")
            denied = views.skills_content(**{**args, "skill_id": self.WS})
            self.assertEqual(denied["state"], "denied")
            for content, expected in (("", "empty"), ("password=fictional", "withheld_secret"), ("x" * (MAX_BYTES + 1), "too_large")):
                install("print(" + repr(json.dumps({**payload, "content": content})) + ")\n")
                result = views.skills_content(**args)
                self.assertEqual(result["state"], expected)
                if expected == "withheld_secret":
                    self.assertNotIn("content", result)
            install("import sys\nsys.stdout.write('x' * " + str(MAX_OUTPUT + 1) + ")\n")
            result = views.skills_content(**args)
            self.assertEqual(result["state"], "too_large")
            self.assertIsNone(result["body_bytes"])
            self.assertIsNone(result["body_sha256"])
            install("import sys\nprint('unauthorized password=fictional', file=sys.stderr)\nsys.exit(1)\n")
            result = views.skills_content(**args)
            self.assertEqual(result["state"], "denied")
            self.assertNotIn("fictional", json.dumps(result))


    def test_same_workspace_serializes_reads_and_keeps_only_transport_state(self):
        import time
        from concurrent.futures import ThreadPoolExecutor
        from multica_board.cloud_skill_content import CloudSkillReader
        from multica_board.cli_runner import CliRunner
        from boxsync.multica_cli import CliRunner as BoundedRunner
        active = 0
        max_active = 0
        guard = threading.Lock()
        def fake_run(runner, args, workspace_id=None):
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(active, max_active)
            time.sleep(0.01)
            with guard:
                active -= 1
            return {"id": self.SK, "content": "ordinary prose"}
        reader = CloudSkillReader(CliRunner(profile="fixture"))
        with mock.patch.object(BoundedRunner, "run", fake_run):
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(lambda _: reader.read(self.WS, self.SK), range(3)))
        self.assertEqual(max_active, 1)
        self.assertTrue(all(r["state"] == "ok" for r in results))
        cli = reader._workspaces[self.WS][1]
        self.assertEqual(cli.interval, 1)
        self.assertEqual(cli.timeout, 30)
        self.assertNotIn("ordinary prose", repr(reader.__dict__))


if __name__ == "__main__":
    unittest.main()
