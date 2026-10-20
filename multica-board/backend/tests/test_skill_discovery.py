"""Recursive disk visibility must not change package/version ownership."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board import skills_view
from test_skills_view import NOW, skill


class RecursiveSkillsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(skills_view._CACHE.clear)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.disk = self.root / "hermes" / "skills"
        self.disk.mkdir(parents=True)
        self.provider = self.root / "hermes" / "hermes-agent"
        self.provider.mkdir()
        self.inventory = self.root / "inventory.json"
        self.inventory.write_text('{"skills": []}')
        self.tools = self.root / "roots.json"
        self.tools.write_text(json.dumps({"tools": [{"id": "hermes", "label": "Hermes"}],
                                          "roots": [{"path": str(self.disk), "tools": ["hermes"], "kind": "direct"}]}))

    def install(self, relative, name=None, root=None):
        path = (root or self.disk) / relative
        path.mkdir(parents=True, exist_ok=True)
        (path / "SKILL.md").write_text(f"---\nname: {name or path.name}\n---\nBody\n")
        return path

    def body(self, cached=False, scan_roots=None):
        kwargs = dict(repo_root=self.repo, inventory_path=self.inventory, tool_roots_path=self.tools,
                      staging_root=self.root / "staging", lock_path=self.root / "no.lock", changelog_dir=None,
                      scan_roots=scan_roots or [])
        return skills_view.skills_document(clock=lambda: NOW, **kwargs) if cached else skills_view.build_payload(now=NOW, **kwargs)

    def bundle(self):
        record = skill("hermes-builtin-skills", source_class="runtime_builtin", scope="vendor-managed",
                       visible_paths=[str(self.disk)], subject=str(self.provider / "install-stamp.json"))
        self.inventory.write_text(json.dumps({"skills": [record]}))
        return record

    def test_recursive_categories_and_support_pruning(self):
        self.install("yuanbao")
        self.install("mlops/training/peft", "peft-fine-tuning")
        self.install("parent")
        self.install("parent/scripts/example")
        self.install("parent/references/fixture")
        self.install("parent/skills/child")
        self.install("node_modules/fake")
        self.install(".archive/old")
        self.install(".system/legitimate")
        body = self.body()
        names = {item["name"] for item in body["items"]}
        self.assertEqual(names, {"yuanbao", "peft-fine-tuning", "parent", "child", "legitimate"})
        peft = next(item for item in body["items"] if item["name"] == "peft-fine-tuning")
        self.assertEqual(peft["entries"][0]["category"], "mlops/training")
        self.assertEqual(peft["entries"][0]["relative_path"], "mlops/training/peft")
        self.assertIsNone(peft["entries"][0]["runtime_loaded"])
        self.assertEqual(peft["reconciliation_status"], "unverified")
        self.assertEqual(body["meta"]["discovery"]["kind"], "disk_installations")

    def test_inventory_same_name_adds_each_entry_including_aliases(self):
        original = self.install("computer-use")
        nested = self.install("autonomous-ai-agents/computer-use")
        alias = self.disk / "alias"
        alias.symlink_to(original)
        record = skill("computer-use", visible_paths=[str(original)])
        self.inventory.write_text(json.dumps({"skills": [record]}))
        item = self.body()["items"][0]
        self.assertEqual({entry["path"] for entry in item["entries"]}, {str(original), str(nested), str(alias)})
        self.assertEqual(len(item["evidence"]), 3)
        linked = next(entry for entry in item["entries"] if entry["path"] == str(alias))
        self.assertEqual(linked["target"], str(original))
        self.assertEqual(linked["resolved"], str(original.resolve()))
        self.assertEqual(item["bucket"], "unrecorded")

    def test_package_provenance_needs_manifest_and_exact_provider_path(self):
        record = self.bundle()
        self.install("category/builtin")
        self.install("category/builtin", root=self.provider / "skills")
        self.install("user-added")
        self.install("other/builtin")  # same name is not proof
        (self.disk / ".bundled_manifest").write_text("builtin:origin-hash\n")
        body = self.body()
        package = next(item for item in body["items"] if item["name"] == record["name"])
        self.assertEqual(package["source_class"], "runtime_builtin")
        self.assertEqual(package["scope"], "vendor-managed")
        builtin = next(item for item in body["items"] if item["name"] == "builtin")
        entries = {entry["relative_path"]: entry for entry in builtin["entries"]}
        self.assertEqual(entries["category/builtin"]["registration"], "bundle_member")
        self.assertEqual(entries["other/builtin"]["registration"], "unrecorded")
        added = next(item for item in body["items"] if item["name"] == "user-added")
        self.assertEqual(added["source_class"], "unrecorded")
        self.assertEqual(added["bucket"], "unrecorded")

    def test_extra_install_does_not_hide_missing_inventory_path(self):
        self.install("category/demo")
        missing = self.disk / "gone" / "demo"
        record = skill("demo", source_class="local_custom", scope="user", visible_paths=[str(missing)],
                       authority={"canonical_path": str(missing)}, baseline={"kind": "content_hash", "subject": "SKILL.md"})
        self.inventory.write_text(json.dumps({"skills": [record]}))
        item = self.body()["items"][0]
        self.assertEqual(item["bucket"], "missing")
        self.assertIn("unrecorded", item["tags"])
        self.assertEqual(len(item["entries"]), 2)

    def test_bundle_children_do_not_duplicate_version_drift_or_baseline_debt(self):
        self.bundle()
        self.install("builtin")
        self.install("builtin", root=self.provider / "skills")
        (self.disk / ".bundled_manifest").write_text("builtin:hash\n")
        child = next(item for item in self.body()["items"] if item["name"] == "builtin")
        self.assertEqual(child["origin"], "bundle_member")
        self.assertIsNone(child["bucket"])
        self.assertFalse(child["baseline_debt"])
        self.assertIsNone(child["current_version"])

    def test_manifest_cannot_make_external_symlink_builtin(self):
        self.bundle()
        outside = self.install("builtin", root=self.root / "outside")
        (self.disk / "builtin").symlink_to(outside)
        self.install("builtin", root=self.provider / "skills")
        (self.disk / ".bundled_manifest").write_text("builtin:hash\n")
        child = next(item for item in self.body()["items"] if item["name"] == "builtin")
        self.assertEqual(child["source_class"], "unrecorded")

    def test_category_symlinks_preserve_ancestor_evidence_and_each_entry(self):
        outside = self.root / "outside"
        self.install("deep/child", root=outside)
        for alias in ("category-a", "category-b"):
            (self.disk / alias).symlink_to(outside)
        item = self.body()["items"][0]
        self.assertEqual(len(item["entries"]), 2)
        for entry in item["entries"]:
            self.assertFalse(entry["symlink"])
            self.assertEqual(entry["symlink_chain"][0]["target"], str(outside))
            self.assertEqual(entry["symlink_chain"][0]["path"], str(Path(entry["path"]).parents[1]))

    def test_cycles_permissions_and_dangling_links_leave_other_installs_visible(self):
        self.install("good")
        denied = self.disk / "denied"
        denied.mkdir()
        (self.disk / "cycle").symlink_to(self.disk)
        (self.disk / "self-cycle").symlink_to("self-cycle")
        (self.disk / "dangling").symlink_to("missing")
        original = Path.iterdir
        def iterdir(path):
            if path == denied:
                raise PermissionError("test denied")
            return original(path)
        with mock.patch.object(Path, "iterdir", iterdir):
            body = self.body()
        self.assertEqual([item["name"] for item in body["items"]], ["good"])
        codes = {issue["code"] for issue in body["meta"]["discovery"]["issues"]}
        self.assertTrue({"denied", "link_cycle", "missing"} <= codes)

    def test_cycle_at_configured_root_degrades(self):
        loop = self.root / "loop"
        loop.symlink_to("loop")
        data = json.loads(self.tools.read_text())
        data["roots"].append({"path": str(loop), "tools": ["hermes"], "kind": "direct"})
        self.tools.write_text(json.dumps(data))
        self.install("good")
        self.assertTrue(self.body()["ok"])

    def test_cache_updates_for_nested_add_delete_and_link_target_edits(self):
        self.install("category/first")
        first = self.body(cached=True)
        self.assertIs(first, self.body(cached=True))
        added = self.install("category/deeper/new")
        second = self.body(cached=True)
        self.assertIsNot(first, second)
        self.assertIn("new", {item["name"] for item in second["items"]})
        (added / "SKILL.md").unlink()
        third = self.body(cached=True)
        self.assertNotIn("new", {item["name"] for item in third["items"]})
        outside = self.root / "outside"
        a = self.install("a", root=outside)
        b = self.install("b", root=outside)
        link = self.disk / "linked"
        link.symlink_to(a)
        linked = self.body(cached=True)
        (a / "SKILL.md").write_text("---\nname: edited-outside-root\n---\n")
        edited = self.body(cached=True)
        self.assertIsNot(linked, edited)
        self.assertIn("edited-outside-root", {item["name"] for item in edited["items"]})
        link.unlink()
        link.symlink_to(b)
        retargeted = self.body(cached=True)
        self.assertNotIn("edited-outside-root", {item["name"] for item in retargeted["items"]})
        self.assertIn("b", {item["name"] for item in retargeted["items"]})
        self.install("deep/after", root=b)
        self.assertIn("after", {item["name"] for item in self.body(cached=True)["items"]})

    def test_cache_updates_manifest_and_provider_evidence(self):
        self.bundle()
        self.install("builtin")
        self.install("builtin", root=self.provider / "skills")
        first = self.body(cached=True)
        (self.disk / ".bundled_manifest").write_text("builtin:hash\n")
        second = self.body(cached=True)
        self.assertIsNot(first, second)
        self.assertEqual(next(i for i in second["items"] if i["name"] == "builtin")["origin"], "bundle_member")
        (self.provider / "skills/builtin/SKILL.md").unlink()
        third = self.body(cached=True)
        self.assertEqual(next(i for i in third["items"] if i["name"] == "builtin")["origin"], "unrecorded")

    def test_explicit_scan_roots_have_distinct_cache_keys(self):
        extra = self.root / "extra"
        self.install("extra", root=extra)
        first = self.body(cached=True)
        second = self.body(cached=True, scan_roots=[extra])
        self.assertIsNot(first, second)
        self.assertEqual([item["name"] for item in second["items"]], ["extra"])


if __name__ == "__main__":
    unittest.main()
