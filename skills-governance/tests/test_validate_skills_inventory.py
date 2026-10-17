import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills-governance" / "scripts" / "validate_skills_inventory.py"
SCHEMA = REPO / "skills-governance" / "config" / "skills-inventory.schema.json"
SOURCE_TYPES = REPO / "skills-governance" / "config" / "source-types.yaml"
INVENTORY = REPO / "skills-governance" / "config" / "skills-inventory.json"


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_skills_inventory", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def skill(**overrides):
    record = {
        "name": "demo-skill",
        "source_class": "local_custom",
        "authority": {
            "ownership": "local",
            "source_authority": "canonical_git",
            "update_authority": "repository_maintainer",
            "canonical_path": "skills/demo-skill",
        },
        "baseline": {"kind": "none"},
        "adapters": [],
        "status": "active",
        "review_time": None,
        "baseline_ref": None,
        "current_version": None,
        "customization_mode": "none",
        "compatibility_constraints": [],
    }
    record.update(overrides)
    return record


def document(skills):
    return {
        "schema": "skills-inventory/v1",
        "updated_at": "2026-10-15T08:30:00Z",
        "skills": skills,
    }


class ValidateSkillsInventoryTests(unittest.TestCase):
    def run_validator(self, inventory: Path, *args: str):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--inventory",
                str(inventory),
                "--source-types",
                str(SOURCE_TYPES),
                *args,
            ],
            text=True,
            capture_output=True,
        )

    def write_inventory(self, root: Path, payload: dict) -> Path:
        path = root / "skills-inventory.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def errors_of(self, result) -> list[dict]:
        self.assertEqual(result.returncode, 1, result.stdout)
        return json.loads(result.stdout)["errors"]

    def test_schema_requires_drift_fields(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        required = schema["$defs"]["skill"]["required"]
        module = load_validator()
        self.assertEqual(list(module.SKILL_REQUIRED), required)
        for field in (
            "review_time",
            "baseline_ref",
            "current_version",
            "customization_mode",
            "compatibility_constraints",
        ):
            self.assertIn(field, required)
        module_modes = set(module.CUSTOMIZATION_MODES)
        self.assertEqual(set(schema["$defs"]["skill"]["properties"]["customization_mode"]["enum"]), module_modes)
        constraint = schema["$defs"]["compatibility_constraint"]
        self.assertEqual(constraint["required"], ["kind", "value"])
        self.assertEqual(set(constraint["properties"]["kind"]["enum"]), set(module.CONSTRAINT_KINDS))
        for field in ("source_class", "authority", "baseline", "adapters", "status"):
            self.assertIn(field, required)

    def test_minimal_record_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = self.write_inventory(Path(directory), document([skill()]))
            result = self.run_validator(inventory)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_missing_review_time_fails(self):
        record = skill()
        del record["review_time"]
        with tempfile.TemporaryDirectory() as directory:
            inventory = self.write_inventory(Path(directory), document([record]))
            result = self.run_validator(inventory)
        paths = [item["path"] for item in self.errors_of(result)]
        self.assertIn("skills[0].review_time", paths)

    def test_duplicate_name_and_unknown_class_fail(self):
        other = skill(name="other-skill")
        other["source_class"] = "not_a_class"
        with tempfile.TemporaryDirectory() as directory:
            inventory = self.write_inventory(
                Path(directory),
                document([skill(), skill(), other]),
            )
            result = self.run_validator(inventory)
        paths = {item["path"] for item in self.errors_of(result)}
        self.assertIn("skills[1].name", paths)
        self.assertIn("skills[2].source_class", paths)

    def test_content_hash_requires_sha_ref(self):
        record = skill(
            baseline={"kind": "content_hash", "subject": "SKILL.md"},
            baseline_ref=None,
        )
        with tempfile.TemporaryDirectory() as directory:
            inventory = self.write_inventory(Path(directory), document([record]))
            result = self.run_validator(inventory)
        self.assertIn(
            "skills[0].baseline_ref",
            [item["path"] for item in self.errors_of(result)],
        )

    def test_authority_must_match_source_class(self):
        record = skill()
        record["authority"] = {**record["authority"], "ownership": "third_party"}
        with tempfile.TemporaryDirectory() as directory:
            inventory = self.write_inventory(Path(directory), document([record]))
            result = self.run_validator(inventory)
        self.assertIn(
            "skills[0].authority.ownership",
            [item["path"] for item in self.errors_of(result)],
        )

    def test_check_paths_accepts_matching_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_md = root / "skills" / "demo-skill" / "SKILL.md"
            skill_md.parent.mkdir(parents=True)
            body = "---\nname: demo-skill\n---\nlocal\n"
            skill_md.write_text(body, encoding="utf-8")
            digest = hashlib.sha256(body.encode()).hexdigest()
            link = root / ".agents" / "skills" / "demo-skill"
            link.parent.mkdir(parents=True)
            link.symlink_to(Path("../../skills/demo-skill"))
            record = skill(
                baseline={"kind": "content_hash", "subject": "SKILL.md"},
                baseline_ref=f"sha256:{digest}",
                adapters=[
                    {
                        "path": ".agents/skills/demo-skill",
                        "kind": "symlink",
                        "target": "../../skills/demo-skill",
                    }
                ],
            )
            inventory = self.write_inventory(root, document([record]))
            result = self.run_validator(inventory, "--check-paths", "--repo-root", str(root))
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_check_paths_rejects_hash_and_symlink_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_md = root / "skills" / "demo-skill" / "SKILL.md"
            skill_md.parent.mkdir(parents=True)
            skill_md.write_text("---\nname: demo-skill\n---\nchanged\n", encoding="utf-8")
            link = root / ".agents" / "skills" / "demo-skill"
            link.parent.mkdir(parents=True)
            link.symlink_to(Path("../../skills/other"))
            record = skill(
                baseline={"kind": "content_hash", "subject": "SKILL.md"},
                baseline_ref="sha256:" + ("a" * 64),
                adapters=[
                    {
                        "path": ".agents/skills/demo-skill",
                        "kind": "symlink",
                        "target": "../../skills/demo-skill",
                    }
                ],
            )
            inventory = self.write_inventory(root, document([record]))
            result = self.run_validator(inventory, "--check-paths", "--repo-root", str(root))
        paths = {item["path"] for item in self.errors_of(result)}
        self.assertIn("skills[0].baseline_ref", paths)
        self.assertIn("skills[0].adapters[0].target", paths)

    def test_machine_local_custom_may_omit_canonical_path(self):
        record = skill(source_class="local_custom", visible_paths=["~/.agents/skills/pr-review-fix"])
        record["authority"] = {
            "ownership": "local",
            "source_authority": "canonical_git",
            "update_authority": "repository_maintainer",
            "canonical_path": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            inventory = self.write_inventory(Path(directory), document([record]))
            result = self.run_validator(inventory)
        self.assertEqual(result.returncode, 0, result.stdout)

        bare = skill()
        bare["authority"] = {**bare["authority"], "canonical_path": None}
        with tempfile.TemporaryDirectory() as directory:
            inventory = self.write_inventory(Path(directory), document([bare]))
            result = self.run_validator(inventory)
        self.assertIn(
            "skills[0].authority.canonical_path",
            [item["path"] for item in self.errors_of(result)],
        )

    def test_external_symlink_adapter_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "real"
            target.mkdir()
            (target / "SKILL.md").write_text("---\nname: demo-skill\n---\n", encoding="utf-8")
            link = root / "linked"
            link.symlink_to(target, target_is_directory=True)
            body = (target / "SKILL.md").read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            record = skill(
                source_class="skills_cli_managed",
                baseline={"kind": "content_hash", "subject": "SKILL.md"},
                baseline_ref=f"sha256:{digest}",
                adapters=[{"path": str(link), "kind": "symlink", "target": str(target)}],
                visible_paths=[str(target)],
            )
            record["authority"] = {
                "ownership": "third_party",
                "source_authority": "upstream_repository",
                "update_authority": "skills_cli",
                "canonical_path": None,
            }
            inventory = self.write_inventory(root, document([record]))
            result = self.run_validator(inventory, "--check-paths", "--repo-root", str(root))
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_agent_worktree_target_is_rejected(self):
        record = skill(
            adapters=[
                {
                    "path": "~/.agents/skills/demo-skill",
                    "kind": "symlink",
                    "target": "/Users/tr/multica_workspaces_desktop-api.multica.ai/acm-180-abcd/workdir/agents-lab/skills/demo-skill",
                }
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            inventory = self.write_inventory(Path(directory), document([record]))
            result = self.run_validator(inventory)
        paths = {item["path"] for item in self.errors_of(result)}
        self.assertIn("skills[0].adapters[0].target", paths)

    def test_repo_inventory_covers_canonical_skills(self):
        self.assertTrue(INVENTORY.is_file(), "skills-inventory.json is missing")
        payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
        for item in payload["skills"]:
            self.assertEqual(item["customization_mode"], "none", item["name"])
            self.assertEqual(item["compatibility_constraints"], [], item["name"])
        result = self.run_validator(
            INVENTORY,
            "--check-paths",
            "--repo-root",
            str(REPO),
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        recorded = {
            item["authority"]["canonical_path"]
            for item in payload["skills"]
            if item["authority"]["canonical_path"]
        }
        found = set()
        for base in (REPO / "skills", REPO / "skills-governance" / "skills"):
            for skill_md in base.rglob("SKILL.md"):
                found.add(skill_md.parent.relative_to(REPO).as_posix())
        self.assertEqual(recorded, found)
        names = [item["name"] for item in payload["skills"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("ego-browser", names)
        classes = {item["name"]: item["source_class"] for item in payload["skills"]}
        self.assertEqual(classes["pr-review-fix"], "local_custom")
        self.assertEqual(classes["wechatide-skill"], "third_party_unmanaged")
        self.assertEqual(classes["external-qa-triage"], "third_party_unmanaged")
        self.assertEqual(classes["writing-child-presentations"], "third_party_unmanaged")
        for bundle in ("cursor-builtin-skills", "codex-builtin-skills", "hermes-builtin-skills"):
            self.assertIn(bundle, names)
        for item in payload["skills"]:
            self.assertIn("review_time", item)
            self.assertIn("baseline_ref", item)
            self.assertIn("current_version", item)
        cli = [item for item in payload["skills"] if item["source_class"] == "skills_cli_managed"]
        self.assertEqual(len(cli), 37)
        for item in cli:
            self.assertEqual(item["baseline"]["kind"], "upstream_ref")
            self.assertTrue(item["baseline"]["subject"].endswith("SKILL.md"), item["name"])
            self.assertTrue(item["baseline_ref"].startswith("github:"), item["name"])
            self.assertIn("/", item["baseline_ref"])
            version = item["current_version"]
            if version is not None:
                self.assertRegex(version, r"^[0-9a-f]{40}$", item["name"])
        by_name = {item["name"]: item for item in cli}
        self.assertIsNone(by_name["agent-browser"]["current_version"])
        self.assertEqual(by_name["agent-browser"]["baseline_ref"], "github:vercel-labs/agent-browser")
        self.assertEqual(by_name["writing-plans"]["baseline_ref"], "github:obra/superpowers")
        self.assertEqual(by_name["writing-plans"]["current_version"], "cc97fa00eb2ccd4f283e34eb2ba06a48e1e679f7")
        self.assertEqual(by_name["doc"]["baseline_ref"], "github:openai/skills")
        self.assertNotEqual(by_name["doc"]["baseline"]["subject"], by_name["playwright"]["baseline"]["subject"])
        by_all = {item["name"]: item for item in payload["skills"]}
        self.assertEqual(by_all["cursor-builtin-skills"]["baseline_ref"], "runtime:cursor")
        self.assertEqual(by_all["cursor-builtin-skills"]["current_version"], "3.22.12")
        self.assertEqual(by_all["codex-builtin-skills"]["baseline_ref"], "runtime:codex")
        self.assertEqual(by_all["codex-builtin-skills"]["current_version"], "0.162.1")
        self.assertEqual(by_all["hermes-builtin-skills"]["baseline_ref"], "runtime:hermes")
        self.assertEqual(by_all["hermes-builtin-skills"]["current_version"], "0.21.5+3b4a891174")
        plugins = [item for item in payload["skills"] if item["source_class"] == "runtime_plugin"]
        self.assertEqual(len(plugins), 16)
        plugin_x = by_all["cursor-plugin-x"]
        self.assertEqual(
            plugin_x["visible_paths"],
            ["~/.cursor/plugins/cache/cursor-public/x/*/skills"],
        )
        self.assertEqual(
            plugin_x["baseline"]["subject"],
            "~/.cursor/plugins/cache/cursor-public/x/*/.cursor-plugin/plugin.json",
        )
        self.assertNotRegex(plugin_x["visible_paths"][0], r"/[0-9a-f]{40}/")
        for item in plugins:
            self.assertEqual(item["scope"], "plugin-managed")
            self.assertEqual(item["baseline"]["kind"], "upstream_ref")
            self.assertTrue(item["baseline_ref"].startswith("plugin:"), item["name"])
            self.assertRegex(item["baseline_ref"], r"^plugin:(codex|cursor|claude)/.+@.+$", item["name"])
            self.assertTrue(item["baseline"]["subject"])
            self.assertTrue(item["current_version"])
            self.assertIsNone(item["review_time"])

    def test_cache_hash_segment_resolves_without_storing_the_hash(self):
        validator = load_validator()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / "cache" / "cursor-public" / "x"
            stale = plugin / ("a" * 40)
            current = plugin / ("b" * 40)
            (stale / "skills").mkdir(parents=True)
            (current / "skills").mkdir(parents=True)
            (current / ".cursor-plugin").mkdir()
            (current / ".cursor-plugin" / "plugin.json").write_text(
                '{"version":"2.6.6"}\n', encoding="utf-8"
            )
            os.utime(stale, (1, 1))
            os.utime(current, (2, 2))
            skills = validator.locate_path("cache/cursor-public/x/*/skills", root)
            manifest = validator.locate_path(
                "cache/cursor-public/x/*/.cursor-plugin/plugin.json", root
            )
            self.assertEqual(skills, current / "skills")
            self.assertEqual(manifest, current / ".cursor-plugin" / "plugin.json")
            (current / "skills").rmdir()
            os.utime(current, (3, 3))
            skills = validator.locate_path("cache/cursor-public/x/*/skills", root)
            self.assertEqual(skills, stale / "skills")

    def test_customization_mode_must_match_local_delta(self):
        record = skill(local_customization="local_delta", customization_mode="none")
        with tempfile.TemporaryDirectory() as directory:
            inventory = self.write_inventory(Path(directory), document([record]))
            result = self.run_validator(inventory)
        self.assertIn(
            "skills[0].customization_mode",
            [item["path"] for item in self.errors_of(result)],
        )

        excluded = skill(
            local_customization="none",
            customization_mode="policy_exclusion",
            compatibility_constraints=[{"kind": "policy", "value": "do not load under claude"}],
        )
        with tempfile.TemporaryDirectory() as directory:
            inventory = self.write_inventory(Path(directory), document([excluded]))
            result = self.run_validator(inventory)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_compatibility_constraint_shape(self):
        record = skill(
            customization_mode="overlay",
            local_customization="local_delta",
            compatibility_constraints=[
                {"kind": "upstream_version", "value": "github:example/skills@abc"},
                {"kind": "nope", "value": "x"},
                "text",
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            inventory = self.write_inventory(Path(directory), document([record]))
            result = self.run_validator(inventory)
        paths = {item["path"] for item in self.errors_of(result)}
        self.assertIn("skills[0].compatibility_constraints[1].kind", paths)
        self.assertIn("skills[0].compatibility_constraints[2]", paths)

    def test_plugin_coordinate_requires_subject_and_version(self):
        record = skill(
            name="codex-plugin-figma",
            source_class="runtime_plugin",
            baseline={"kind": "upstream_ref"},
            baseline_ref="plugin:figma@openai-curated",
            current_version=None,
        )
        record["authority"] = {
            "ownership": "plugin_provider",
            "source_authority": "enabled_plugin",
            "update_authority": "plugin_upgrade",
            "canonical_path": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            inventory = self.write_inventory(Path(directory), document([record]))
            result = self.run_validator(inventory)
        paths = {item["path"] for item in self.errors_of(result)}
        self.assertIn("skills[0].baseline.subject", paths)
        self.assertIn("skills[0].current_version", paths)
