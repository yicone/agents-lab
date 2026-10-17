import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills-governance" / "scripts" / "token_footprint.py"


def load_report():
    spec = importlib.util.spec_from_file_location("token_footprint", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_skill(directory: Path, name: str, total_bytes: int, extra: str = "") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    prefix = f"---\nname: {name}\n---\n\n# {name}\n\n{extra}"
    encoded = prefix.encode("utf-8")
    if total_bytes < len(encoded):
        raise ValueError(f"total_bytes {total_bytes} is smaller than the skill prefix")
    payload = encoded + b"a" * (total_bytes - len(encoded))
    (directory / "SKILL.md").write_bytes(payload)


class TokenFootprintTests(unittest.TestCase):
    def setUp(self):
        self.module = load_report()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def report(self, skills, scan_roots=None, global_roots=None):
        document = {"schema": "skills-inventory/v1", "skills": skills}
        return self.module.build_report(
            document,
            self.root,
            scan_roots or [],
            global_roots=global_roots if global_roots is not None else [],
        )

    def row(self, name, relative, scope="repo"):
        return {
            "name": name,
            "source_class": "local_custom",
            "authority": {"canonical_path": relative},
            "scope": scope,
            "visible_paths": [relative],
            "status": "active",
        }

    def skill(self, report, name):
        return next(item for item in report["skills"] if item["name"] == name)

    def test_estimator_rounds_bytes_up_to_a_token(self):
        self.assertEqual(self.module.static_tokens(0), 0)
        self.assertEqual(self.module.static_tokens(1), 1)
        self.assertEqual(self.module.static_tokens(4), 1)
        self.assertEqual(self.module.static_tokens(5), 2)
        self.assertEqual(self.module.TOKEN_WARN, 2000)
        self.assertEqual(self.module.SKILL_BYTES_WARN, 16384)

    def test_exact_token_budget_passes(self):
        write_skill(self.root / "skills" / "small-skill", "small-skill", 8000)
        report = self.report([self.row("small-skill", "skills/small-skill")])
        skill = self.skill(report, "small-skill")
        self.assertEqual(skill["eager_tokens"], 2000)
        self.assertEqual(skill["static_tokens"], 2000)
        self.assertEqual(skill["warnings"], [])
        self.assertEqual(skill["suggestions"], [])
        self.assertEqual(report["gate"], "pass")

    def test_eager_token_overage_suggests_references_split(self):
        write_skill(self.root / "skills" / "wide-skill", "wide-skill", 8001)
        report = self.report([self.row("wide-skill", "skills/wide-skill", scope="repo")])
        skill = self.skill(report, "wide-skill")
        self.assertEqual(skill["eager_tokens"], 2001)
        self.assertEqual(skill["warnings"], ["token_over_budget"])
        self.assertEqual([item["id"] for item in skill["suggestions"]], ["references_subdirectory"])
        self.assertIn("references/", skill["suggestions"][0]["summary"])
        self.assertNotIn("global catalog", skill["suggestions"][0]["summary"])
        self.assertEqual(report["gate"], "fail")
        self.assertEqual(report["summary"]["token_over_budget"], 1)
        self.assertEqual(report["summary"]["size_over_budget"], 0)

    def test_global_overage_also_suggests_target_scope(self):
        write_skill(self.root / "skills" / "global-skill", "global-skill", 8001)
        report = self.report([self.row("global-skill", "skills/global-skill", scope="machine")])
        skill = self.skill(report, "global-skill")
        self.assertEqual(
            [item["id"] for item in skill["suggestions"]],
            ["references_subdirectory", "target_scoped"],
        )
        self.assertIn("target-scoped", skill["suggestions"][1]["summary"])

    def test_home_install_is_global_even_when_scope_is_repo(self):
        global_root = self.root / "home-skills"
        write_skill(global_root / "installed-skill", "installed-skill", 8001)
        report = self.report(
            [self.row("installed-skill", "home-skills/installed-skill", scope="repo")],
            global_roots=[global_root],
        )
        skill = self.skill(report, "installed-skill")
        self.assertIn("target_scoped", [item["id"] for item in skill["suggestions"]])

    def test_size_threshold_is_independent_of_the_token_boundary(self):
        write_skill(self.root / "skills" / "token-only", "token-only", 16384)
        write_skill(self.root / "skills" / "bulky-skill", "bulky-skill", 16385)
        report = self.report(
            [
                self.row("token-only", "skills/token-only"),
                self.row("bulky-skill", "skills/bulky-skill"),
            ]
        )
        token_only = self.skill(report, "token-only")
        bulky = self.skill(report, "bulky-skill")
        self.assertEqual(token_only["warnings"], ["token_over_budget"])
        self.assertEqual(bulky["warnings"], ["token_over_budget", "size_over_budget"])
        self.assertGreater(bulky["eager_bytes"], self.module.SKILL_BYTES_WARN)

    def test_referenced_docs_are_counted_once_and_do_not_escape(self):
        skill_dir = self.root / "skills" / "cited-skill"
        write_skill(
            skill_dir,
            "cited-skill",
            200,
            extra="Read [guide](references/guide.md) and `references/guide.md`.\nOutside: [secret](../secret.md)\n",
        )
        guide = skill_dir / "references" / "guide.md"
        guide.parent.mkdir(parents=True)
        guide.write_bytes(b"b" * 8000)
        (self.root / "skills" / "secret.md").write_text("secret", encoding="utf-8")
        report = self.report([self.row("cited-skill", "skills/cited-skill", scope="repo")])
        skill = self.skill(report, "cited-skill")
        references = [item for item in skill["files"] if item["role"] == "referenced"]
        self.assertEqual([item["path"] for item in references], ["references/guide.md"])
        self.assertEqual(references[0]["static_tokens"], 2000)
        self.assertEqual(skill["referenced_tokens"], 2000)
        self.assertGreater(skill["static_tokens"], 2000)
        self.assertEqual(skill["warnings"], [])
        self.assertEqual(report["gate"], "pass")

    def test_global_package_over_budget_keeps_references_on_demand(self):
        skill_dir = self.root / "skills" / "packaged-skill"
        write_skill(skill_dir, "packaged-skill", 200, extra="See [guide](references/guide.md).\n")
        guide = skill_dir / "references" / "guide.md"
        guide.parent.mkdir(parents=True)
        guide.write_bytes(b"c" * 8000)
        report = self.report([self.row("packaged-skill", "skills/packaged-skill", scope="machine")])
        skill = self.skill(report, "packaged-skill")
        self.assertEqual(skill["warnings"], ["token_over_budget"])
        self.assertEqual(skill["suggestions"][0]["id"], "references_subdirectory")
        self.assertIn("Do not inline", skill["suggestions"][0]["summary"])
        self.assertEqual(skill["suggestions"][1]["id"], "target_scoped")

    def test_missing_skill_is_unmeasured_and_does_not_fail_the_gate(self):
        report = self.report([self.row("gone-skill", "skills/gone-skill")])
        skill = self.skill(report, "gone-skill")
        self.assertEqual(skill["status"], "unmeasured")
        self.assertEqual(skill["reason"], "SKILL.md not found")
        self.assertEqual(report["gate"], "pass")
        self.assertEqual(report["summary"]["unmeasured"], 1)

    def test_scan_and_inventory_share_one_resolved_skill(self):
        source = self.root / "skills" / "once-skill"
        write_skill(source, "once-skill", 300)
        alias_parent = self.root / "aliases"
        alias_parent.mkdir()
        (alias_parent / "once-skill").symlink_to(source, target_is_directory=True)
        report = self.report(
            [self.row("once-skill", "skills/once-skill", scope="repo")],
            scan_roots=[alias_parent, source.parent],
        )
        matches = [item for item in report["skills"] if item["name"] == "once-skill"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["origin"], "inventory")
        self.assertEqual(matches[0]["scope"], "repo")

    def test_command_is_read_only_and_check_returns_the_gate(self):
        skill_dir = self.root / "skills" / "checked-skill"
        write_skill(skill_dir, "checked-skill", 8001)
        inventory = self.root / "inventory.json"
        payload = {"schema": "skills-inventory/v1", "skills": [self.row("checked-skill", "skills/checked-skill")]}
        inventory.write_text(json.dumps(payload), encoding="utf-8")
        before_inventory = inventory.read_bytes()
        before_skill = (skill_dir / "SKILL.md").read_bytes()
        failed = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--inventory",
                str(inventory),
                "--repo-root",
                str(self.root),
                "--no-default-roots",
                "--check",
                "--format",
                "json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(failed.returncode, 2, failed.stderr)
        document = json.loads(failed.stdout)
        self.assertTrue(document["read_only"])
        self.assertEqual(document["estimator"], "bytes_div_4")
        self.assertEqual(document["gate"], "fail")
        self.assertEqual(inventory.read_bytes(), before_inventory)
        self.assertEqual((skill_dir / "SKILL.md").read_bytes(), before_skill)

        write_skill(skill_dir, "checked-skill", 400)
        passed = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--inventory",
                str(inventory),
                "--repo-root",
                str(self.root),
                "--no-default-roots",
                "--check",
                "--format",
                "text",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn("Gate: pass", passed.stdout)
        self.assertIn("warnings: none", passed.stdout)
        self.assertEqual(inventory.read_bytes(), before_inventory)


if __name__ == "__main__":
    unittest.main()
