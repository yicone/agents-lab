from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multica_board.skill_registration import registration_fields
from multica_board.skill_runtimes import runtime_columns
from multica_board.views import Views


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = b"---\r\nname: demo\r\n---\r\n# Demo\r\n"
        self.skill = self.root / "demo"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_bytes(self.raw)
        self.item = {"name": "demo", "source_class": "local_custom", "origin": "inventory",
                     "canonical_path": str(self.skill), "entries": [{"path": str(self.skill), "present": True}],
                     "tools": {"codex": "shared"}}

    def project(self, item=None):
        doc = runtime_columns({"items": [item or self.item], "tools": [{"id": "codex"}]}, {}, [])
        return registration_fields(doc, repo_root=self.root)["items"][0]

    def test_raw_hash_shared_entry_and_explicit_opt_out(self):
        row = self.project()
        self.assertEqual(row["id"], "demo")
        self.assertEqual(row["body_sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(row["body_hash_state"], "observed")
        self.assertTrue(row["loaders"][0]["shared_auto_load"])
        self.assertIsNone(row["loaders"][0]["runtime_loaded"])
        self.assertEqual(row["loaders"][0]["load_state"], "unverified")
        self.assertNotIn("body_sha256", self.item)
        row["runtime_evidence"]["codex"]["runtime_loaded"] = False
        registration_fields({"items": [row]}, repo_root=self.root)
        self.assertEqual(row["loaders"][0]["load_state"], "not_loaded")
        (self.skill / "SKILL.md").write_bytes(b"changed")
        self.assertEqual(self.project()["body_sha256"], hashlib.sha256(b"changed").hexdigest())

    def test_unrecorded_path_and_missing_body(self):
        self.item.update(canonical_path=None, origin="unrecorded", source_class="unrecorded")
        self.assertEqual(self.project()["canonical_path"], str(self.skill))
        (self.skill / "SKILL.md").unlink()
        self.assertIsNone(self.project()["body_sha256"])
        self.assertNotEqual(self.project()["body_hash_state"], "observed")

    def test_shared_entry_is_preserved_when_direct_mark_wins(self):
        self.item["tools"] = {"codex": "direct"}
        self.item["evidence"] = [{"harness": "codex", "attribution": "shared", "state": "present"}]
        loader = self.project()["loaders"][0]
        self.assertEqual(loader["mark"], "direct")
        self.assertTrue(loader["shared_auto_load"])
        self.assertIsNone(loader["runtime_loaded"])

    def test_export_hash_uses_verified_reader_without_leaking_body(self):
        item = {"name": "demo", "key": "grok_bot_export:demo", "origin": "grok_bot",
                "source_class": "grok_bot_export", "export": {"path": "grok_bot_export:demo"}}
        with mock.patch("multica_board.skill_grok_export.read_export_content", return_value={
            "ok": True, "state": "ok", "sha256": "a" * 64, "content": "BODY_SENTINEL"
        }) as reader:
            row = self.project(item)
        reader.assert_called_once_with(name="demo", path="grok_bot_export:demo")
        self.assertEqual(row["body_sha256"], "a" * 64)
        self.assertIsNone(row["canonical_path"])
        self.assertNotIn("BODY_SENTINEL", json.dumps(row))

    def test_grok_identity_stays_separate_and_workspace_is_declaration(self):
        row = self.project({"name": "demo", "key": "grok_bot:demo", "origin": "grok_bot",
                            "source_class": "grok_bot", "grok_bots": [{"id": "bot"}]})
        self.assertEqual(row["id"], "grok_bot:demo")
        self.assertIsNone(row["canonical_path"])
        self.assertIsNone(row["body_sha256"])
        self.assertEqual(row["body_hash_state"], "not_applicable")
        self.assertEqual(row["loaders"][-1]["id"], "bot")
        self.item["workspace_installs"] = [{"workspace_id": "ws", "id": "skill",
                                           "agents": [{"id": "agent", "enabled": True}]}]
        agent = self.project()["loaders"][-1]
        self.assertEqual(agent["id"], "agent")
        self.assertIsNone(agent["runtime_loaded"])

    def test_views_exposes_contract_after_runtime_enrichment(self):
        views = Views(mock.Mock(), "test", app_url="https://example.test", collect_interval_s=60)
        document = {"items": [self.item], "tools": [{"id": "codex"}], "meta": {}}
        bot = {"name": "demo", "key": "grok_bot:demo", "origin": "grok_bot", "source_class": "grok_bot"}
        with mock.patch("multica_board.skills_view.skills_document", return_value=document), \
             mock.patch("multica_board.skills_view.REPO_ROOT", self.root), \
             mock.patch("multica_board.skills_view._load_tool_roots", return_value=([], [])), \
             mock.patch.object(views, "_workspace_installs", return_value={}), \
             mock.patch.object(views, "runtimes", return_value={}), \
             mock.patch("multica_board.runtime_load.collect_runtime_load", return_value={}), \
             mock.patch("multica_board.skill_grok_bots.grok_bot_skills", return_value=([bot], {"state": "ok", "detail": "fixture"})), \
             mock.patch("multica_board.skill_grok_export.grok_export_skills", return_value=([], {"detail": "fixture"})):
            result = views.skills()
        rows = json.loads(json.dumps(result))["items"]
        self.assertEqual([r["id"] for r in rows], ["demo", "grok_bot:demo"])
        self.assertTrue(all(set(("id", "canonical_path", "body_sha256", "source_class", "loaders")) <= r.keys()
                            for r in rows))
        self.assertIsNone(rows[0]["loaders"][0]["runtime_loaded"])


if __name__ == "__main__":
    unittest.main()
