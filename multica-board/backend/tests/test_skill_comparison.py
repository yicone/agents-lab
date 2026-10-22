"""ACM-349: content fingerprints, merged counts and runtime applicability."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.cli_runner import CliRunner, CliError
from multica_board.collector import Collector, CollectOptions, norm_workspace_skill, ScopeError
from multica_board.skill_runtimes import runtime_columns
from multica_board.skills_view import _summary, _unrecorded_item
from multica_board.store import Store
from multica_board.views import Views, _cloud_only_item
from fake_cli import FakeMultica


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def test_hash_detail_cached_by_workspace_id_and_revision_without_bodies(self):
        metadata = {"id": "s", "workspace_id": "w", "name": "demo", "updated_at": "t1"}
        detail = {**metadata, "content_hash": "a" * 64, "content": "never cache this body"}
        fake = FakeMultica({"w": {"skills": [metadata], "skill_details": {"s": detail}}})
        runner = CliRunner("test", _exec=fake, retries=0)
        collector = Collector(runner, self.store, "test", CollectOptions())
        collector._workspace_skills("w")
        row = self.store.query("SELECT * FROM workspace_skills")[0]
        self.assertEqual(row["content_hash"], "a" * 64)
        self.assertNotIn("content", row)
        collector._workspace_skills("w")
        gets = lambda: [c for c in fake.calls if "get" in c]
        self.assertEqual(len(gets()), 1)
        self.assertTrue(all("--with-content" not in c for c in gets()))
        metadata["updated_at"] = detail["updated_at"] = "t2"
        detail["content_hash"] = "b" * 64
        collector._workspace_skills("w")
        self.assertEqual(len(gets()), 2)
        self.assertEqual(self.store.query("SELECT content_hash FROM workspace_skills")[0]["content_hash"], "b" * 64)

    def test_missing_hash_fallback_and_scope_failure(self):
        runner = mock.Mock()
        metadata = {"id": "s", "workspace_id": "w", "name": "demo", "updated_at": "t"}
        runner.run.side_effect = [mock.Mock(data=[metadata]), CliError("old CLI: no get")]
        collector = Collector(runner, self.store, "test", CollectOptions())
        collector._workspace_skills("w")
        self.assertIsNone(self.store.query("SELECT content_hash FROM workspace_skills")[0]["content_hash"])
        runner.run.side_effect = [mock.Mock(data=[metadata]), mock.Mock(data={**metadata, "workspace_id": "other", "content_hash": "c" * 64})]
        with self.assertRaises(ScopeError):
            collector._workspace_skills("w")
        runner.run.side_effect = [mock.Mock(data=[metadata]), CliError("403 forbidden")]
        with self.assertRaises(CliError):
            collector._workspace_skills("w")
        for bad in [None, "", "content text", "a" * 63, {"secret": "value"}]:
            self.assertIsNone(norm_workspace_skill({**metadata, "content_hash": bad})["content_hash"])

    def test_all_summary_includes_cloud_only_names_once(self):
        local = {"name": "local", "source_class": "local_custom", "reconciliation_status": "stale"}
        document = {"ok": True, "generated_at": "t", "items": [local], "tools": [], "summary": _summary([local])}
        views = Views(self.store, "test", app_url="https://example.test", collect_interval_s=60)
        installs = {"local": [{"id": "s1"}], "cloud": [{"id": "s2"}, {"id": "s3"}]}
        with mock.patch("multica_board.skill_grok_bots.grok_bot_skills", return_value=([], {"state":"ok", "detail":"test"})), mock.patch("multica_board.skills_view.skills_document", return_value=document), mock.patch.object(views, "_workspace_installs", return_value=installs), mock.patch.object(views, "runtimes", return_value={"items": []}):
            result = views.skills()
        self.assertEqual(result["summary"][0]["count"], 2)
        self.assertEqual(sum(result["summary"][0]["statuses"].values()), 2)
        self.assertEqual(document["summary"][0]["count"], 1)

    def test_unrecorded_state_and_cloud_only_applicability(self):
        with tempfile.TemporaryDirectory() as folder:
            item = _unrecorded_item({"name": "demo"}, [], Path(folder))
        self.assertEqual(item["reconciliation_status"], "unrecorded")
        self.assertEqual(next(block for block in _summary([item]) if block["id"] == "unrecorded")["statuses"], {"unrecorded": 1})
        cloud = _cloud_only_item("cloud", [{"id": "s"}])
        result = runtime_columns({"tools": [{"id": "codex", "label": "Codex"}], "items": [cloud]}, {"items": [{"provider": "future"}]}, [])
        self.assertTrue(all(e["state"] == "not_applicable" for e in result["items"][0]["runtime_evidence"].values()))

    def test_runtime_config_covers_platform_and_new_runtimes_with_evidence(self):
        config = json.loads((Path(__file__).resolve().parents[3] / "skills-governance/config/tool-roots.json").read_text())
        labels = {tool["id"]: tool["label"] for tool in config["tools"]}
        expected = {"antigravity": "Antigravity", "mcode": "Devin CLI", "omp": "Oh My Pi", "factory": "Factory Droid", "amp": "Amp"}
        for key, label in expected.items():
            self.assertEqual(labels[key], label)
            roots = [root for root in config["roots"] if key in root["tools"]]
            self.assertTrue(roots)
            self.assertTrue(all(root.get("evidence") for root in roots))
        for root in config["roots"]:
            self.assertTrue(set(root["tools"]) <= labels.keys())
            if "antigravity" in root["path"]:
                self.assertNotIn("gemini", root["tools"])


if __name__ == "__main__":
    unittest.main()
