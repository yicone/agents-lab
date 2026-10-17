import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills-governance" / "scripts" / "drift_report.py"


def load_report():
    spec = importlib.util.spec_from_file_location("drift_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_skill(root: Path, relative: str, name: str, version: str | None = None) -> str:
    skill_dir = root / relative
    skill_dir.mkdir(parents=True, exist_ok=True)
    version_line = f'\n  version: "{version}"' if version else ""
    text = f"---\nname: {name}\nmetadata:\n  owner: test{version_line}\n---\n\n# {name}\n"
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return text


class DriftReportTests(unittest.TestCase):
    def setUp(self):
        self.module = load_report()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def report(self, skills, lock=None, scan_roots=None):
        document = {"schema": "skills-inventory/v1", "skills": skills}
        return self.module.build_report(
            document,
            self.root,
            lock or {},
            scan_roots or [],
        )

    def skill_row(self, report, name):
        return next(row for row in report["skills"] if row["name"] == name)

    def content_record(self, **overrides):
        text = write_skill(self.root, "skills/demo-skill", "demo-skill", overrides.pop("version", None))
        digest = "sha256:" + self.module.inventory.sha256_file(self.root / "skills/demo-skill/SKILL.md")
        record = {
            "name": "demo-skill",
            "source_class": "local_custom",
            "authority": {
                "ownership": "local",
                "source_authority": "canonical_git",
                "update_authority": "repository_maintainer",
                "canonical_path": "skills/demo-skill",
            },
            "baseline": {"kind": "content_hash", "subject": "SKILL.md"},
            "adapters": [{"path": "<target>/demo-skill", "kind": "declared_target", "target": None}],
            "status": "active",
            "review_time": None,
            "baseline_ref": digest,
            "current_version": overrides.pop("current_version", None),
            "visible_paths": ["skills/demo-skill"],
        }
        record.update(overrides)
        record["text"] = text
        return record

    def test_matching_hash_without_review_is_stale(self):
        record = self.content_record()
        row = self.skill_row(self.report([record]), "demo-skill")
        self.assertEqual(row["reconciliation_status"], "stale")
        self.assertEqual(row["baseline_ref"], record["baseline_ref"])
        self.assertIn("review_time_missing", row["findings"])
        self.assertIn("content_hash_match", row["findings"])

    def test_hash_mismatch_is_modified(self):
        record = self.content_record()
        record["baseline_ref"] = "sha256:" + ("a" * 64)
        row = self.skill_row(self.report([record]), "demo-skill")
        self.assertEqual(row["reconciliation_status"], "modified")
        self.assertIn("content_hash_mismatch", row["findings"])

    def test_missing_source_is_missing(self):
        record = self.content_record()
        (self.root / "skills/demo-skill/SKILL.md").unlink()
        row = self.skill_row(self.report([record]), "demo-skill")
        self.assertEqual(row["reconciliation_status"], "missing")
        self.assertIn("source_missing", row["findings"])

    def test_version_mismatch_is_modified(self):
        record = self.content_record(version="2.0", current_version="1.0")
        row = self.skill_row(self.report([record]), "demo-skill")
        self.assertEqual(row["reconciliation_status"], "modified")
        self.assertEqual(row["current_version"], "1.0")
        self.assertEqual(row["observed_version"], "2.0")

    def test_reviewed_match_is_aligned(self):
        record = self.content_record()
        record["review_time"] = "2026-10-16T00:00:00Z"
        row = self.skill_row(self.report([record]), "demo-skill")
        self.assertEqual(row["reconciliation_status"], "aligned")
        self.assertEqual(row["review_time"], "2026-10-16T00:00:00Z")

    def test_name_mismatch_is_unverified(self):
        record = self.content_record()
        skill_md = self.root / "skills/demo-skill/SKILL.md"
        skill_md.write_text(skill_md.read_text(encoding="utf-8").replace("name: demo-skill", "name: other-skill"), encoding="utf-8")
        record["baseline_ref"] = "sha256:" + self.module.inventory.sha256_file(skill_md)
        row = self.skill_row(self.report([record]), "demo-skill")
        self.assertEqual(row["reconciliation_status"], "unverified")
        self.assertIn("name_mismatch", row["findings"])

    def test_baseline_none_is_unverified_not_current(self):
        bundle = self.root / "bundle"
        bundle.mkdir()
        record = {
            "name": "codex-builtin-skills",
            "source_class": "runtime_builtin",
            "authority": {
                "ownership": "runtime_provider",
                "source_authority": "runtime_distribution",
                "update_authority": "runtime_upgrade",
                "canonical_path": None,
            },
            "baseline": {"kind": "none"},
            "adapters": [],
            "status": "active",
            "review_time": None,
            "baseline_ref": None,
            "current_version": None,
            "scope": "vendor-managed",
            "visible_paths": [str(bundle)],
        }
        write_skill(bundle, "child-skill", "child-skill")
        report = self.report([record], scan_roots=[bundle])
        row = self.skill_row(report, "codex-builtin-skills")
        self.assertEqual(row["reconciliation_status"], "unverified")
        self.assertEqual(row["baseline_ref"], None)
        self.assertEqual(report["unrecorded"], [])

    def test_same_name_with_different_lock_source_is_unverified(self):
        install = self.root / "installs" / "agent-browser"
        install.mkdir(parents=True)
        record = {
            "name": "agent-browser",
            "source_class": "skills_cli_managed",
            "authority": {
                "ownership": "third_party",
                "source_authority": "upstream_repository",
                "update_authority": "skills_cli",
                "canonical_path": None,
            },
            "baseline": {"kind": "upstream_ref", "subject": "skill-lock"},
            "adapters": [],
            "status": "active",
            "review_time": "2026-10-16T00:00:00Z",
            "baseline_ref": "github:vercel-labs/agent-browser",
            "current_version": None,
            "visible_paths": [str(install)],
        }
        lock = {
            "agent-browser": {
                "source": "other/agent-browser",
                "sourceType": "github",
                "skillFolderHash": "",
            }
        }
        row = self.skill_row(self.report([record], lock=lock), "agent-browser")
        self.assertEqual(row["reconciliation_status"], "unverified")
        self.assertIn("lock_source_mismatch", row["findings"])
        self.assertNotIn("content_hash_match", row["findings"])

    def test_lock_skill_path_mismatch_is_unverified(self):
        install = self.root / "installs" / "demo-skill"
        install.mkdir(parents=True)
        record = {
            "name": "demo-skill",
            "source_class": "skills_cli_managed",
            "authority": {
                "ownership": "third_party",
                "source_authority": "upstream_repository",
                "update_authority": "skills_cli",
                "canonical_path": None,
            },
            "baseline": {"kind": "upstream_ref", "subject": "skills/other/SKILL.md"},
            "adapters": [],
            "status": "active",
            "review_time": "2026-10-16T00:00:00Z",
            "baseline_ref": "github:owner/repo",
            "current_version": "a" * 40,
            "visible_paths": [str(install)],
        }
        lock = {
            "demo-skill": {
                "source": "owner/repo",
                "sourceType": "github",
                "skillPath": "skills/demo-skill/SKILL.md",
                "skillFolderHash": "a" * 40,
            }
        }
        row = self.skill_row(self.report([record], lock=lock), "demo-skill")
        self.assertEqual(row["reconciliation_status"], "unverified")
        self.assertIn("lock_path_mismatch", row["findings"])
        self.assertIn("lock_source_match", row["findings"])

    def test_lock_hash_mismatch_is_modified(self):
        install = self.root / "installs" / "demo-skill"
        install.mkdir(parents=True)
        record = {
            "name": "demo-skill",
            "source_class": "skills_cli_managed",
            "authority": {
                "ownership": "third_party",
                "source_authority": "upstream_repository",
                "update_authority": "skills_cli",
                "canonical_path": None,
            },
            "baseline": {"kind": "git_commit", "subject": "skill-lock"},
            "adapters": [],
            "status": "active",
            "review_time": "2026-10-16T00:00:00Z",
            "baseline_ref": "a" * 40,
            "current_version": "a" * 40,
            "visible_paths": [str(install)],
        }
        lock = {"demo-skill": {"source": "owner/repo", "sourceType": "github", "skillFolderHash": "b" * 40}}
        row = self.skill_row(self.report([record], lock=lock), "demo-skill")
        self.assertEqual(row["reconciliation_status"], "modified")
        self.assertEqual(row["observed_version"], "b" * 40)
        self.assertEqual(row["baseline_ref"], "a" * 40)

    def test_review_older_than_lock_is_stale(self):
        install = self.root / "installs" / "demo-skill"
        install.mkdir(parents=True)
        commit = "c" * 40
        record = {
            "name": "demo-skill",
            "source_class": "skills_cli_managed",
            "authority": {
                "ownership": "third_party",
                "source_authority": "upstream_repository",
                "update_authority": "skills_cli",
                "canonical_path": None,
            },
            "baseline": {"kind": "git_commit", "subject": "skill-lock"},
            "adapters": [],
            "status": "active",
            "review_time": "2020-01-01T00:00:00Z",
            "baseline_ref": commit,
            "current_version": commit,
            "visible_paths": [str(install)],
        }
        lock = {
            "demo-skill": {
                "source": "owner/repo",
                "sourceType": "github",
                "skillFolderHash": commit,
                "updatedAt": "2026-03-19T20:00:26.756Z",
            }
        }
        row = self.skill_row(self.report([record], lock=lock), "demo-skill")
        self.assertEqual(row["reconciliation_status"], "stale")
        self.assertIn("review_older_than_lock", row["findings"])

    def test_unrecorded_install_is_not_attached_by_name(self):
        record = self.content_record()
        extra_root = self.root / "elsewhere"
        write_skill(extra_root, "demo-skill", "demo-skill")
        report = self.report([record], scan_roots=[extra_root])
        self.assertEqual(self.skill_row(report, "demo-skill")["reconciliation_status"], "stale")
        self.assertEqual(len(report["unrecorded"]), 1)
        self.assertEqual(report["unrecorded"][0]["name"], "demo-skill")
        self.assertEqual(report["unrecorded"][0]["reconciliation_status"], "missing")
        self.assertEqual(report["unrecorded"][0]["provenance"], "unverified")

    def test_broken_symlink_is_unrecorded(self):
        record = self.content_record()
        root = self.root / "custom-skills"
        root.mkdir()
        (root / "find-skills").symlink_to(self.root / "missing-skill")
        report = self.report([record], scan_roots=[root])
        self.assertEqual(len(report["unrecorded"]), 1)
        self.assertEqual(report["unrecorded"][0]["name"], "find-skills")
        self.assertEqual(report["unrecorded"][0]["reconciliation_status"], "missing")
        self.assertEqual(report["unrecorded"][0]["provenance"], "unverified")

    def test_symlink_target_mismatch_is_modified(self):
        record = self.content_record()
        link = self.root / ".agents" / "skills" / "demo-skill"
        link.parent.mkdir(parents=True)
        link.symlink_to(self.root / "skills" / "demo-skill")
        record["adapters"].append(
            {"path": ".agents/skills/demo-skill", "kind": "symlink", "target": "../../skills/other"}
        )
        record["visible_paths"].append(".agents/skills/demo-skill")
        row = self.skill_row(self.report([record]), "demo-skill")
        self.assertEqual(row["reconciliation_status"], "modified")
        self.assertIn("symlink_target_mismatch", row["findings"])

    def test_cli_reads_lock_without_writing_it(self):
        record = self.content_record()
        del record["text"]
        inventory = self.root / "skills-inventory.json"
        inventory.write_text(json.dumps({"schema": "skills-inventory/v1", "skills": [record]}), encoding="utf-8")
        lock = self.root / "skill-lock.json"
        lock.write_text(json.dumps({"version": 1, "skills": {}}), encoding="utf-8")
        before = lock.read_bytes()
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--inventory",
                str(inventory),
                "--repo-root",
                str(self.root),
                "--lock",
                str(lock),
                "--no-default-roots",
                "--format",
                "json",
            ],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lock.read_bytes(), before)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["report"], "skills-drift/v1")
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["skills"][0]["reconciliation_status"], "stale")
        self.assertIn("review_time", payload["skills"][0])
        self.assertIn("baseline_ref", payload["skills"][0])
        self.assertIn("current_version", payload["skills"][0])

    def provider_record(self, **overrides):
        bundle = self.root / "bundled"
        bundle.mkdir(exist_ok=True)
        write_skill(bundle, "child-skill", "child-skill")
        record = {
            "name": "cursor-plugin-x",
            "source_class": "runtime_plugin",
            "authority": {
                "ownership": "plugin_provider",
                "source_authority": "enabled_plugin",
                "update_authority": "plugin_upgrade",
                "canonical_path": None,
            },
            "baseline": {"kind": "upstream_ref", "subject": str(self.root / "plugin.json")},
            "adapters": [],
            "status": "active",
            "review_time": None,
            "baseline_ref": "plugin:cursor/x@cursor-public",
            "current_version": "2.6.6",
            "scope": "plugin-managed",
            "visible_paths": [str(bundle)],
        }
        record.update(overrides)
        return record

    def test_provider_version_match_is_stale_and_hides_children(self):
        manifest = self.root / "plugin.json"
        manifest.write_text(json.dumps({"version": "2.6.6"}), encoding="utf-8")
        record = self.provider_record()
        report = self.report([record], scan_roots=[self.root / "bundled"])
        row = self.skill_row(report, "cursor-plugin-x")
        self.assertEqual(row["reconciliation_status"], "stale")
        self.assertEqual(row["observed_version"], "2.6.6")
        self.assertIn("version_match", row["findings"])
        self.assertEqual(report["unrecorded"], [])

    def test_provider_version_mismatch_is_modified(self):
        manifest = self.root / "plugin.json"
        manifest.write_text(json.dumps({"version": "9.0.0"}), encoding="utf-8")
        row = self.skill_row(self.report([self.provider_record()]), "cursor-plugin-x")
        self.assertEqual(row["reconciliation_status"], "modified")
        self.assertEqual(row["observed_version"], "9.0.0")
        self.assertIn("version_mismatch", row["findings"])

    def test_runtime_stamp_ignores_display_version(self):
        stamp = self.root / "install-stamp.json"
        stamp.write_text(
            json.dumps(
                {
                    "baseVersion": "0.21.5",
                    "commit": "3b4a8911741a09440e0a10f25996eb41cf0f061f",
                    "displayVersion": "0.21.5+7948.g3b4a891.dirty",
                }
            ),
            encoding="utf-8",
        )
        record = self.provider_record(
            name="hermes-builtin-skills",
            source_class="runtime_builtin",
            scope="vendor-managed",
            baseline_ref="runtime:hermes",
            current_version="0.21.5+3b4a891174",
            baseline={"kind": "upstream_ref", "subject": str(stamp)},
        )
        record["authority"]["ownership"] = "runtime_provider"
        record["authority"]["source_authority"] = "runtime_distribution"
        record["authority"]["update_authority"] = "runtime_upgrade"
        row = self.skill_row(self.report([record]), "hermes-builtin-skills")
        self.assertEqual(row["reconciliation_status"], "stale")
        self.assertEqual(row["observed_version"], "0.21.5+3b4a891174")

    def test_missing_provider_version_file_is_missing(self):
        record = self.provider_record()
        row = self.skill_row(self.report([record]), "cursor-plugin-x")
        self.assertEqual(row["reconciliation_status"], "missing")
        self.assertIn("source_missing", row["findings"])

    def test_replaced_plugin_version_directory_is_version_changed(self):
        recorded = self.root / "cache" / "demo" / "1.0.49" / ".codex-plugin" / "plugin.json"
        sibling = self.root / "cache" / "demo" / "1.0.50" / ".codex-plugin"
        sibling.mkdir(parents=True)
        (sibling / "plugin.json").write_text(json.dumps({"version": "1.0.50"}), encoding="utf-8")
        record = self.provider_record(
            current_version="1.0.49",
            baseline={"kind": "upstream_ref", "subject": str(recorded)},
            visible_paths=[str(self.root / "cache" / "demo" / "1.0.49" / "skills")],
        )
        row = self.skill_row(self.report([record]), "cursor-plugin-x")
        self.assertEqual(row["reconciliation_status"], "version_changed")
        self.assertEqual(row["observed_version"], "1.0.50")
        self.assertEqual(row["current_version"], "1.0.49")
        self.assertIn("version_changed", row["findings"])
        self.assertNotIn("source_missing", row["findings"])

    def test_missing_plugin_without_sibling_stays_missing(self):
        recorded = self.root / "cache" / "alone" / "1.0.49" / ".codex-plugin" / "plugin.json"
        (self.root / "cache" / "alone").mkdir(parents=True)
        record = self.provider_record(
            baseline={"kind": "upstream_ref", "subject": str(recorded)},
        )
        row = self.skill_row(self.report([record]), "cursor-plugin-x")
        self.assertEqual(row["reconciliation_status"], "missing")
        self.assertIn("source_missing", row["findings"])
        self.assertIsNone(row["observed_version"])

    def report_with_changelog(self, skills, changelog_dir):
        document = {"schema": "skills-inventory/v1", "skills": skills}
        return self.module.build_report(
            document,
            self.root,
            {},
            [],
            changelog_dir=changelog_dir,
        )

    def mismatched_record(self):
        record = self.content_record()
        record["baseline_ref"] = "sha256:" + ("a" * 64)
        return record

    def observed_hash(self):
        return "sha256:" + self.module.inventory.sha256_file(
            self.root / "skills/demo-skill/SKILL.md"
        )

    def test_unrecorded_change_is_flagged(self):
        changelog_dir = self.root / "changelog"
        record = self.mismatched_record()
        row = self.skill_row(
            self.report_with_changelog([record], changelog_dir), "demo-skill"
        )
        self.assertEqual(row["reconciliation_status"], "modified")
        self.assertIn("change_unrecorded", row["findings"])
        self.assertTrue(
            any("有变更未记录" in detail for detail in row["details"])
        )
        self.assertEqual(row["changelog"], {"entries": 0, "last_date": None})

    def test_matching_changelog_entry_is_not_flagged(self):
        changelog_dir = self.root / "changelog"
        changelog_dir.mkdir()
        record = self.mismatched_record()
        entry = {"new_hash": self.observed_hash(), "date": "2026-10-17T00:00:00Z"}
        (changelog_dir / "demo-skill.jsonl").write_text(
            json.dumps(entry) + "\n", encoding="utf-8"
        )
        row = self.skill_row(
            self.report_with_changelog([record], changelog_dir), "demo-skill"
        )
        self.assertEqual(row["reconciliation_status"], "modified")
        self.assertNotIn("change_unrecorded", row["findings"])
        self.assertEqual(row["changelog"]["entries"], 1)
        self.assertEqual(row["changelog"]["last_date"], "2026-10-17T00:00:00Z")

    def test_changelog_dir_none_never_flags(self):
        record = self.mismatched_record()
        row = self.skill_row(self.report([record]), "demo-skill")
        self.assertEqual(row["reconciliation_status"], "modified")
        self.assertNotIn("change_unrecorded", row["findings"])
        self.assertNotIn("changelog", row)

    def test_aligned_skill_is_not_flagged(self):
        changelog_dir = self.root / "changelog"
        record = self.content_record()
        record["review_time"] = "2026-10-16T00:00:00Z"
        row = self.skill_row(
            self.report_with_changelog([record], changelog_dir), "demo-skill"
        )
        self.assertEqual(row["reconciliation_status"], "aligned")
        self.assertNotIn("change_unrecorded", row["findings"])

    def test_malformed_changelog_line_is_ignored(self):
        changelog_dir = self.root / "changelog"
        changelog_dir.mkdir()
        (changelog_dir / "demo-skill.jsonl").write_text(
            "not json\n\n" + json.dumps({"new_hash": "sha256:" + "0" * 64}) + "\n",
            encoding="utf-8",
        )
        record = self.mismatched_record()
        row = self.skill_row(
            self.report_with_changelog([record], changelog_dir), "demo-skill"
        )
        self.assertIn("change_unrecorded", row["findings"])
        self.assertEqual(row["changelog"]["entries"], 1)


if __name__ == "__main__":
    unittest.main()
