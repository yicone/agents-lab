import contextlib
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills-governance" / "scripts" / "sync_skills_inventory.py"
SOURCE_TYPES = REPO / "skills-governance" / "config" / "source-types.yaml"


def load_sync():
    spec = importlib.util.spec_from_file_location("sync_skills_inventory", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_skill(root: Path, relative: str, name: str, body: str = "body") -> Path:
    skill_dir = root / relative
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\n---\n\n{body}\n", encoding="utf-8")
    return skill_dir


class SyncInventoryTests(unittest.TestCase):
    def setUp(self):
        self.module = load_sync()
        self.source_classes = self.module.inventory.load_source_classes(SOURCE_TYPES)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def plan(self, skills, skill_roots=None, plugin_roots=None, lock=None):
        document = {
            "schema": "skills-inventory/v1",
            "updated_at": "2026-10-16T00:00:00Z",
            "skills": skills,
        }
        return self.module.plan_sync(
            document,
            self.root,
            skill_roots=skill_roots or [],
            plugin_roots=plugin_roots or [],
            lock_skills=lock or {},
            source_classes=self.source_classes,
            now="2026-10-23T01:00:00Z",
        )

    def record(self, **overrides):
        skill_dir = write_skill(self.root, "skills/demo-skill", "demo-skill")
        digest = "sha256:" + self.module.inventory.sha256_file(skill_dir / "SKILL.md")
        row = {
            "name": "demo-skill",
            "source_class": "local_custom",
            "authority": {
                "ownership": "local",
                "source_authority": "canonical_git",
                "update_authority": "repository_maintainer",
                "canonical_path": "skills/demo-skill",
            },
            "baseline": {"kind": "content_hash", "subject": "SKILL.md"},
            "adapters": [],
            "status": "active",
            "review_time": None,
            "baseline_ref": digest,
            "current_version": None,
            "scope": "repo",
            "install_shape": "canonical_directory",
            "local_customization": "none",
            "customization_mode": "none",
            "compatibility_constraints": [{"kind": "policy", "value": "keep"}],
            "conflict_status": "unreviewed",
            "visible_paths": ["skills/demo-skill"],
        }
        row.update(overrides)
        return row

    def names(self, document):
        return [item["name"] for item in document["skills"]]

    def test_new_skill_records_hash_without_review_fields(self):
        write_skill(self.root, "installed/fresh-skill", "fresh-skill")
        updated, summary = self.plan([], skill_roots=[self.root / "installed"])
        self.assertEqual(summary["counts"]["added"], 1)
        row = updated["skills"][0]
        self.assertEqual(row["name"], "fresh-skill")
        self.assertEqual(row["source_class"], "unknown")
        self.assertTrue(row["baseline_ref"].startswith("sha256:"))
        self.assertIsNone(row["review_time"])
        self.assertEqual(row["customization_mode"], "unknown")
        self.assertEqual(row["compatibility_constraints"], [])
        self.assertTrue(summary["added"][0]["path"].endswith("fresh-skill"))
        errors = self.module.inventory.validate_document(updated, self.source_classes, self.root)
        self.assertEqual(errors, [])

    def test_repo_skill_uses_canonical_path(self):
        write_skill(self.root, "skills/repo-skill", "repo-skill")
        updated, summary = self.plan([], skill_roots=[self.root / "skills"])
        row = updated["skills"][0]
        self.assertEqual(row["source_class"], "local_custom")
        self.assertEqual(row["authority"]["canonical_path"], "skills/repo-skill")
        self.assertIsNone(row["current_version"])
        self.assertIsNone(row["review_time"])
        self.assertEqual(summary["counts"]["added"], 1)

    def test_dry_run_does_not_write_inventory(self):
        write_skill(self.root, "installed/fresh-skill", "fresh-skill")
        inventory = self.root / "inventory.json"
        inventory.write_text(
            json.dumps({"schema": "skills-inventory/v1", "updated_at": "2026-10-16T00:00:00Z", "skills": []}),
            encoding="utf-8",
        )
        before = inventory.read_bytes()
        roots = self.root / "tool-roots.json"
        roots.write_text(
            json.dumps({"roots": [{"path": str(self.root / "installed"), "tools": ["cursor"], "kind": "direct"}]}),
            encoding="utf-8",
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            status = self.module.main(
                [
                    "--inventory",
                    str(inventory),
                    "--repo-root",
                    str(self.root),
                    "--tool-roots",
                    str(roots),
                    "--format",
                    "json",
                ]
            )
        self.assertEqual(status, 0)
        self.assertEqual(inventory.read_bytes(), before)
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["counts"]["added"], 1)
        self.assertFalse(payload["counts"]["applied"])

    def test_apply_writes_valid_inventory(self):
        write_skill(self.root, "installed/fresh-skill", "fresh-skill")
        inventory = self.root / "inventory.json"
        inventory.write_text(
            json.dumps({"schema": "skills-inventory/v1", "updated_at": "2026-10-16T00:00:00Z", "skills": []}),
            encoding="utf-8",
        )
        os.chmod(inventory, 0o640)
        roots = self.root / "tool-roots.json"
        roots.write_text(
            json.dumps({"roots": [{"path": str(self.root / "installed"), "tools": ["cursor"], "kind": "direct"}]}),
            encoding="utf-8",
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            status = self.module.main(
                [
                    "--inventory",
                    str(inventory),
                    "--repo-root",
                    str(self.root),
                    "--tool-roots",
                    str(roots),
                    "--apply",
                    "--format",
                    "json",
                ]
            )
        self.assertEqual(status, 0)
        self.assertEqual(stat.S_IMODE(inventory.stat().st_mode), 0o640)
        written = json.loads(inventory.read_text(encoding="utf-8"))
        self.assertEqual(written["skills"][0]["name"], "fresh-skill")
        self.assertIsNone(written["skills"][0]["review_time"])
        self.assertNotEqual(written["updated_at"], "2026-10-16T00:00:00Z")
        self.assertTrue(json.loads(buffer.getvalue())["counts"]["applied"])

    def test_interrupted_apply_keeps_inventory_and_removes_temp(self):
        write_skill(self.root, "installed/fresh-skill", "fresh-skill")
        inventory = self.root / "inventory.json"
        inventory.write_text(
            json.dumps({"schema": "skills-inventory/v1", "updated_at": "2026-10-16T00:00:00Z", "skills": []}),
            encoding="utf-8",
        )
        os.chmod(inventory, 0o640)
        original = inventory.read_bytes()
        roots = self.root / "tool-roots.json"
        roots.write_text(
            json.dumps({"roots": [{"path": str(self.root / "installed"), "tools": ["cursor"], "kind": "direct"}]}),
            encoding="utf-8",
        )
        real_fdopen = self.module.os.fdopen

        def failing_fdopen(fd, mode="r", **kwargs):
            handle = real_fdopen(fd, mode, **kwargs)

            class Partial:
                def write(self, data):
                    if data:
                        handle.write(data[:1])
                    raise OSError("interrupted")

                def flush(self):
                    handle.flush()

                def fileno(self):
                    return handle.fileno()

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    handle.close()
                    return False

            return Partial()

        self.module.os.fdopen = failing_fdopen
        try:
            with self.assertRaises(OSError):
                self.module.main(
                    [
                        "--inventory",
                        str(inventory),
                        "--repo-root",
                        str(self.root),
                        "--tool-roots",
                        str(roots),
                        "--apply",
                        "--format",
                        "json",
                    ]
                )
        finally:
            self.module.os.fdopen = real_fdopen
        self.assertEqual(inventory.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(inventory.stat().st_mode), 0o640)
        leftovers = [path.name for path in inventory.parent.iterdir() if path.name.startswith(".inventory.json.")]
        self.assertEqual(leftovers, [])

    def test_missing_install_is_marked_unknown_and_kept(self):
        row = self.record()
        row["authority"]["canonical_path"] = "skills/missing-skill"
        row["visible_paths"] = ["skills/missing-skill"]
        row["name"] = "missing-skill"
        updated, summary = self.plan([row])
        self.assertEqual(self.names(updated), ["missing-skill"])
        self.assertEqual(updated["skills"][0]["status"], "unknown")
        self.assertEqual(summary["uninstalled"][0]["status_before"], "active")
        self.assertEqual(row["status"], "active")

    def test_hash_change_keeps_baseline_and_clears_review_time(self):
        row = self.record(review_time="2026-10-01T00:00:00Z")
        (self.root / "skills/demo-skill/SKILL.md").write_text("---\nname: demo-skill\n---\n\nchanged\n", encoding="utf-8")
        updated, summary = self.plan([row])
        current = updated["skills"][0]
        self.assertEqual(current["baseline_ref"], row["baseline_ref"])
        self.assertIsNone(current["review_time"])
        self.assertEqual(current["customization_mode"], "none")
        self.assertEqual(current["compatibility_constraints"], [{"kind": "policy", "value": "keep"}])
        self.assertEqual(summary["needs_review"][0]["reason"], "content_hash_mismatch")
        self.assertTrue(summary["needs_review"][0]["review_time_cleared"])
        self.assertEqual(row["review_time"], "2026-10-01T00:00:00Z")
        self.assertEqual(summary["backfill"], [])

    def test_matching_hash_does_not_invent_version_or_review_time(self):
        row = self.record()
        updated, summary = self.plan([row])
        current = updated["skills"][0]
        self.assertIsNone(current["current_version"])
        self.assertIsNone(current["review_time"])
        self.assertEqual(current["customization_mode"], "none")
        self.assertEqual(summary["needs_review"], [])
        self.assertEqual(summary["backfill"], [])
        self.assertEqual(summary["left_unset"][0]["reason"], "git history is the version authority for a content hash")
        self.assertFalse(summary["counts"]["file_changed"])

    def test_plugin_package_is_one_row(self):
        package = self.root / "plugins/cache/acme/demo/1.2.3"
        (package / ".codex-plugin").mkdir(parents=True)
        (package / ".codex-plugin/plugin.json").write_text(
            json.dumps({"name": "demo", "version": "1.2.3"}), encoding="utf-8"
        )
        write_skill(package, "skills/alpha", "alpha")
        write_skill(package, "skills/beta", "beta")
        updated, summary = self.plan([], plugin_roots=[self.root / "plugins"])
        self.assertEqual(self.names(updated), ["codex-plugin-demo"])
        row = updated["skills"][0]
        self.assertEqual(row["baseline_ref"], "plugin:codex/demo@acme")
        self.assertEqual(row["current_version"], "1.2.3")
        self.assertIsNone(row["review_time"])
        self.assertEqual(row["customization_mode"], "unknown")
        self.assertEqual(row["compatibility_constraints"], [])
        self.assertEqual(summary["counts"]["added"], 1)
        errors = self.module.inventory.validate_document(updated, self.source_classes, self.root)
        self.assertEqual(errors, [])

    def test_marketplace_clone_is_not_registered(self):
        package = self.root / "plugins/marketplaces/official/cache/acme/cloned/1.0.0"
        (package / ".codex-plugin").mkdir(parents=True)
        (package / ".codex-plugin/plugin.json").write_text(
            json.dumps({"name": "cloned", "version": "1.0.0"}), encoding="utf-8"
        )
        write_skill(package, "skills/cloned", "cloned")
        updated, summary = self.plan([], plugin_roots=[self.root / "plugins"])
        self.assertEqual(updated["skills"], [])
        self.assertEqual(summary["added"], [])

    def test_nested_skill_stays_with_parent(self):
        parent = write_skill(self.root, "installed/parent-skill", "parent-skill")
        write_skill(parent, "skills/child-skill", "child-skill")
        updated, summary = self.plan([], skill_roots=[self.root / "installed"])
        self.assertEqual(self.names(updated), ["parent-skill"])
        self.assertNotIn("child-skill", self.names(updated))
        self.assertEqual(summary["counts"]["added"], 1)

    def test_bundle_children_are_not_separate_rows(self):
        bundle = self.root / "vendor"
        write_skill(bundle, "one-skill", "one-skill")
        write_skill(bundle, "two-skill", "two-skill")
        row = {
            "name": "vendor-builtin-skills",
            "source_class": "runtime_builtin",
            "authority": {
                "ownership": "runtime_provider",
                "source_authority": "runtime_distribution",
                "update_authority": "runtime_upgrade",
                "canonical_path": None,
            },
            "baseline": {"kind": "upstream_ref", "subject": "vendor/version.json"},
            "adapters": [],
            "status": "active",
            "review_time": None,
            "baseline_ref": "runtime:vendor",
            "current_version": "1",
            "scope": "vendor-managed",
            "install_shape": "real_directory",
            "local_customization": "none",
            "customization_mode": "none",
            "compatibility_constraints": [],
            "visible_paths": [str(bundle)],
        }
        (bundle / "version.json").write_text(json.dumps({"version": "1"}), encoding="utf-8")
        updated, summary = self.plan([row], skill_roots=[bundle])
        self.assertEqual(self.names(updated), ["vendor-builtin-skills"])
        self.assertEqual(summary["added"], [])

    def test_same_name_does_not_attach_a_different_path(self):
        row = self.record()
        write_skill(self.root, "installed/demo-skill", "demo-skill", body="other")
        updated, summary = self.plan([row], skill_roots=[self.root / "installed"])
        self.assertEqual(updated["skills"][0]["visible_paths"], ["skills/demo-skill"])
        self.assertEqual(summary["skipped_counts"].get("name_taken"), 1)
        self.assertEqual(summary["added"], [])

    def test_ambiguous_name_is_not_registered(self):
        write_skill(self.root, "installed/a/same-skill", "same-skill", body="one")
        write_skill(self.root, "installed/b/same-skill", "same-skill", body="two")
        updated, summary = self.plan([], skill_roots=[self.root / "installed"])
        self.assertEqual(updated["skills"], [])
        self.assertEqual(summary["skipped_counts"].get("ambiguous_name"), 2)

    def test_lock_hash_fills_empty_version_only(self):
        skill_dir = self.root / ".agents/skills/locked-skill"
        write_skill(skill_dir.parent, "locked-skill", "locked-skill")
        present = {
            "name": "locked-skill",
            "source_class": "skills_cli_managed",
            "authority": {
                "ownership": "third_party",
                "source_authority": "upstream_repository",
                "update_authority": "skills_cli",
                "canonical_path": None,
            },
            "baseline": {"kind": "upstream_ref", "subject": "skills/locked-skill/SKILL.md"},
            "adapters": [],
            "status": "active",
            "review_time": None,
            "baseline_ref": "github:acme/locked",
            "current_version": None,
            "local_customization": "none",
            "customization_mode": "none",
            "compatibility_constraints": [],
            "visible_paths": [str(skill_dir)],
        }
        lock = {
            "locked-skill": {
                "source": "acme/locked",
                "sourceType": "github",
                "skillFolderHash": "abc123",
                "skillPath": "skills/locked-skill/SKILL.md",
            }
        }
        updated, summary = self.plan([present], lock=lock)
        self.assertEqual(updated["skills"][0]["current_version"], "abc123")
        self.assertIsNone(updated["skills"][0]["review_time"])
        self.assertEqual(updated["skills"][0]["customization_mode"], "none")
        self.assertEqual(summary["backfill"][0]["field"], "current_version")

        present["current_version"] = None
        lock["locked-skill"]["skillFolderHash"] = ""
        updated, summary = self.plan([present], lock=lock)
        self.assertIsNone(updated["skills"][0]["current_version"])
        self.assertEqual(summary["backfill"], [])
        self.assertEqual(summary["left_unset"][0]["reason"], "lock skillFolderHash is empty")

    def test_provider_version_change_does_not_rewrite_baseline(self):
        package = self.root / "plugins/cache/acme/demo/2.0.0"
        (package / ".codex-plugin").mkdir(parents=True)
        manifest = package / ".codex-plugin/plugin.json"
        manifest.write_text(json.dumps({"name": "demo", "version": "2.0.0"}), encoding="utf-8")
        write_skill(package, "skills/alpha", "alpha")
        row = {
            "name": "codex-plugin-demo",
            "source_class": "runtime_plugin",
            "authority": {
                "ownership": "plugin_provider",
                "source_authority": "enabled_plugin",
                "update_authority": "plugin_upgrade",
                "canonical_path": None,
            },
            "baseline": {"kind": "upstream_ref", "subject": str(manifest)},
            "adapters": [],
            "status": "active",
            "review_time": "2026-10-01T00:00:00Z",
            "baseline_ref": "plugin:codex/demo@acme",
            "current_version": "1.0.0",
            "scope": "plugin-managed",
            "install_shape": "real_directory",
            "local_customization": "none",
            "customization_mode": "none",
            "compatibility_constraints": [{"kind": "runtime", "value": "codex"}],
            "visible_paths": [str(package / "skills")],
        }
        updated, summary = self.plan([row], plugin_roots=[self.root / "plugins"])
        current = updated["skills"][0]
        self.assertEqual(current["current_version"], "1.0.0")
        self.assertEqual(current["baseline_ref"], "plugin:codex/demo@acme")
        self.assertEqual(current["visible_paths"], [str(package / "skills")])
        self.assertIsNone(current["review_time"])
        self.assertEqual(current["customization_mode"], "none")
        self.assertEqual(current["compatibility_constraints"], [{"kind": "runtime", "value": "codex"}])
        self.assertEqual(summary["needs_review"][0]["reason"], "provider_version_changed")
        self.assertEqual(summary["added"], [])

    def test_cursor_cache_with_claude_manifest_keeps_cursor_host(self):
        package = self.root / ".cursor/plugins/cache/acme/demo/aaaaaaaa"
        (package / ".claude-plugin").mkdir(parents=True)
        (package / ".claude-plugin/plugin.json").write_text(
            json.dumps({"name": "demo", "version": "1.0.0"}), encoding="utf-8"
        )
        write_skill(package, "skills/alpha", "alpha")
        updated, summary = self.plan([], plugin_roots=[self.root / ".cursor/plugins"])
        self.assertEqual(self.names(updated), ["cursor-plugin-demo"])
        self.assertEqual(updated["skills"][0]["baseline_ref"], "plugin:cursor/demo@acme")
        self.assertEqual(summary["counts"]["added"], 1)

    def test_other_publisher_of_same_plugin_is_not_added(self):
        old = self.root / "plugins/cache/acme/demo/1.0.0"
        (old / ".codex-plugin").mkdir(parents=True)
        (old / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "demo", "version": "1.0.0"}), encoding="utf-8")
        write_skill(old, "skills/alpha", "alpha")
        new = self.root / "plugins/cache/other/demo/9.0.0"
        (new / ".codex-plugin").mkdir(parents=True)
        (new / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "demo", "version": "9.0.0"}), encoding="utf-8")
        write_skill(new, "skills/alpha", "alpha")
        row = {
            "name": "codex-plugin-demo",
            "source_class": "runtime_plugin",
            "authority": {
                "ownership": "plugin_provider",
                "source_authority": "enabled_plugin",
                "update_authority": "plugin_upgrade",
                "canonical_path": None,
            },
            "baseline": {"kind": "upstream_ref", "subject": str(old / ".codex-plugin/plugin.json")},
            "adapters": [],
            "status": "active",
            "review_time": None,
            "baseline_ref": "plugin:codex/demo@acme",
            "current_version": "1.0.0",
            "scope": "plugin-managed",
            "local_customization": "none",
            "customization_mode": "none",
            "compatibility_constraints": [],
            "visible_paths": [str(old / "skills")],
        }
        updated, summary = self.plan([row], plugin_roots=[self.root / "plugins"])
        self.assertEqual(self.names(updated), ["codex-plugin-demo"])
        self.assertIn("plugin_id_recorded", summary["skipped_counts"])


if __name__ == "__main__":
    unittest.main()
