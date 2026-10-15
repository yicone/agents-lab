import hashlib
import importlib.util
import json
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
        for field in ("review_time", "baseline_ref", "current_version"):
            self.assertIn(field, required)
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

    def test_repo_inventory_covers_canonical_skills(self):
        self.assertTrue(INVENTORY.is_file(), "skills-inventory.json is missing")
        result = self.run_validator(
            INVENTORY,
            "--check-paths",
            "--repo-root",
            str(REPO),
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
        recorded = {item["authority"]["canonical_path"] for item in payload["skills"]}
        found = set()
        for base in (REPO / "skills", REPO / "skills-governance" / "skills"):
            for skill_md in base.rglob("SKILL.md"):
                found.add(skill_md.parent.relative_to(REPO).as_posix())
        self.assertEqual(recorded, found)
        for item in payload["skills"]:
            self.assertIn("review_time", item)
            self.assertIn("baseline_ref", item)
            self.assertIn("current_version", item)
