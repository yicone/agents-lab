import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills-governance" / "scripts" / "conflict_report.py"


def load_report():
    spec = importlib.util.spec_from_file_location("conflict_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_skill(root: Path, relative: str, name: str, description: str, body: str = "") -> None:
    skill_dir = root / relative
    skill_dir.mkdir(parents=True, exist_ok=True)
    text = f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}\n"
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")


class ConflictReportTests(unittest.TestCase):
    def test_unregistered_install_participates_in_responsibility_detection(self):
        for name in ('registered', 'new-install'):
            write_skill(self.root, f'skills/{name}', name, 'Use when generating architecture diagrams.')
        report = self.findings([self.row('registered', 'skills/registered')], [self.root / 'skills'])
        self.assertIn('responsibility_overlap', self.types_for(report, 'registered', 'new-install'))

    def setUp(self):
        self.module = load_report()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def row(self, name, relative, scope="repo", **overrides):
        record = {
            "name": name,
            "source_class": "local_custom",
            "authority": {
                "ownership": "local",
                "source_authority": "canonical_git",
                "update_authority": "repository_maintainer",
                "canonical_path": relative,
            },
            "status": "active",
            "scope": scope,
            "visible_paths": [relative],
            "compatibility_constraints": [],
        }
        record.update(overrides)
        return record

    def findings(self, skills, scan_roots=None):
        document = {"schema": "skills-inventory/v1", "skills": skills}
        report = self.module.build_report(document, self.root, scan_roots or [])
        return report

    def types_for(self, report, *names):
        wanted = set(names)
        return [
            finding["type"]
            for finding in report["findings"]
            if set(finding["skills"]) == wanted or (len(names) == 1 and finding["skills"] == [names[0]])
        ]

    def test_same_bytes_under_one_name_are_not_a_collision(self):
        write_skill(self.root, "skills/alpha", "alpha", "Use when checking one skill.")
        write_skill(self.root, "skills/alpha-copy", "alpha", "Use when checking one skill.")
        source = (self.root / "skills/alpha/SKILL.md").read_text(encoding="utf-8")
        (self.root / "skills/alpha-copy/SKILL.md").write_text(source, encoding="utf-8")
        report = self.findings(
            [
                self.row("alpha", "skills/alpha"),
                self.row("alpha-copy", "skills/alpha-copy"),
            ]
        )
        self.assertNotIn("name_collision", self.types_for(report, "alpha", "alpha-copy"))

    def test_different_bytes_under_one_name_collide_and_diverge(self):
        write_skill(self.root, "skills/alpha", "alpha", "Use when checking one skill.", "Keep the old rule.")
        write_skill(self.root, "skills/alpha-copy", "alpha", "Use when checking one skill.", "Replace the old rule.")
        report = self.findings(
            [
                self.row("alpha", "skills/alpha"),
                self.row("alpha-copy", "skills/alpha-copy"),
            ]
        )
        kinds = self.types_for(report, "alpha", "alpha-copy")
        self.assertIn("name_collision", kinds)
        self.assertIn("behavior_divergence", kinds)

    def test_a_recorded_adapter_with_different_bytes_is_not_a_second_name(self):
        write_skill(self.root, "skills/alpha", "alpha", "Use when checking one skill.", "Canonical text.")
        installed = self.root / "installed"
        write_skill(installed, "alpha", "alpha", "Use when checking one skill.", "Installed text.")
        report = self.findings(
            [
                self.row(
                    "alpha",
                    "skills/alpha",
                    visible_paths=["skills/alpha", str(installed / "alpha")],
                )
            ],
            scan_roots=[installed],
        )
        self.assertEqual(report["summary"]["name_collision"], 0)

    def test_duplicate_inventory_name_is_a_collision(self):
        report = self.findings([self.row("alpha", "skills/missing"), self.row("alpha", "skills/other")])
        self.assertEqual(report["findings"][0]["type"], "name_collision")
        self.assertEqual(report["findings"][0]["skills"], ["alpha"])

    def test_broader_scope_shadows_a_narrower_directory(self):
        write_skill(self.root, "global/widget", "widget", "Use when packing a widget for the machine.")
        write_skill(self.root, "repo/widget", "widget-local", "Use when packing a widget for one repo.")
        report = self.findings(
            [
                self.row("widget", "global/widget", scope="machine"),
                self.row("widget-local", "repo/widget", scope="repo"),
            ]
        )
        finding = next(item for item in report["findings"] if item["type"] == "scope_shadowing")
        self.assertEqual(finding["skills"], ["widget", "widget-local"])
        self.assertTrue(any("rank 3" in item for item in finding["evidence"]))

    def test_bundle_roots_do_not_shadow_each_other(self):
        write_skill(self.root, "vendor/skills", "vendor-pack", "Use when the vendor bundle loads.")
        write_skill(self.root, "plugin/skills", "plugin-pack", "Use when the plugin bundle loads.")
        report = self.findings(
            [
                self.row(
                    "vendor-pack",
                    "vendor/skills",
                    scope="vendor-managed",
                    source_class="runtime_builtin",
                ),
                self.row(
                    "plugin-pack",
                    "plugin/skills",
                    scope="plugin-managed",
                    source_class="runtime_plugin",
                ),
            ]
        )
        self.assertEqual(report["summary"]["scope_shadowing"], 0)

    def test_ego_browser_overlap_records_routing_note_and_not_divergence(self):
        write_skill(
            self.root,
            "skills/agent-browser",
            "agent-browser",
            "Browser automation CLI. Use when clicking buttons and taking screenshots.",
        )
        write_skill(
            self.root,
            "skills/ego-browser",
            "ego-browser",
            "Browser automation for a shared session. Use when clicking buttons and taking screenshots.",
        )
        report = self.findings(
            [
                self.row("agent-browser", "skills/agent-browser"),
                self.row("ego-browser", "skills/ego-browser"),
            ]
        )
        self.assertNotIn("behavior_divergence", self.types_for(report, "agent-browser", "ego-browser"))
        finding = next(item for item in report["findings"] if item["type"] == "responsibility_overlap")
        self.assertEqual(finding["skills"], ["agent-browser", "ego-browser"])
        self.assertIn("不算行为分歧", finding["routing_note"])
        self.assertIn("Profile", finding["routing_note"])
        self.assertIn("Space", finding["routing_note"])
        self.assertIn("ACM-136", finding["routing_note"])

    def test_shared_claim_is_responsibility_overlap(self):
        write_skill(
            self.root,
            "skills/vault",
            "vault",
            "Use when deciding page ownership, namespace choice, and structured properties.",
        )
        write_skill(
            self.root,
            "skills/writer",
            "writer",
            "Build write plans.",
            "It owns:\n\n- page ownership\n- namespace choice\n",
        )
        report = self.findings(
            [self.row("vault", "skills/vault"), self.row("writer", "skills/writer")]
        )
        finding = next(item for item in report["findings"] if item["type"] == "responsibility_overlap")
        self.assertEqual(finding["skills"], ["vault", "writer"])
        self.assertFalse(finding["boundary_stated"])
        self.assertTrue(any("page ownership" in item for item in finding["evidence"]))

    def test_named_rejection_is_a_boundary_not_divergence(self):
        write_skill(self.root, "skills/owner", "vault-owner", "Owns page naming for the record.")
        write_skill(
            self.root,
            "skills/other",
            "vault-other",
            "It must not decide page naming.",
            "Defer to vault-owner.",
        )
        report = self.findings(
            [self.row("vault-owner", "skills/owner"), self.row("vault-other", "skills/other")]
        )
        self.assertNotIn("behavior_divergence", self.types_for(report, "vault-other", "vault-owner"))

    def test_unnamed_rejection_is_behavior_divergence(self):
        write_skill(self.root, "skills/owner", "vault-owner", "Owns page naming for the record.")
        write_skill(self.root, "skills/other", "vault-other", "It must not decide page naming.")
        report = self.findings(
            [self.row("vault-owner", "skills/owner"), self.row("vault-other", "skills/other")]
        )
        self.assertIn("behavior_divergence", self.types_for(report, "vault-other", "vault-owner"))

    def test_overlapping_use_when_clauses_are_trigger_ambiguity(self):
        write_skill(
            self.root,
            "skills/left",
            "left",
            "Use when filling forms and clicking buttons in a browser window.",
        )
        write_skill(
            self.root,
            "skills/right",
            "right",
            "Use when filling forms and clicking buttons in a browser window before export.",
        )
        report = self.findings([self.row("left", "skills/left"), self.row("right", "skills/right")])
        self.assertIn("trigger_ambiguity", self.types_for(report, "left", "right"))

    def test_distinct_use_when_clauses_are_not_ambiguous(self):
        write_skill(self.root, "skills/left", "left", "Use when reconciling a third-party package lock.")
        write_skill(self.root, "skills/right", "right", "Use when drafting a weekly journal review.")
        report = self.findings([self.row("left", "skills/left"), self.row("right", "skills/right")])
        self.assertEqual(report["summary"]["trigger_ambiguity"], 0)

    def test_retired_cyclic_and_pinned_dependencies_conflict(self):
        (self.root / "skills/needs").mkdir(parents=True)
        (self.root / "skills/needs/SKILL.md").write_text(
            "---\nname: needs\ndescription: Use when a helper is required.\n"
            "metadata:\n  depends:\n    - cycle-b\n    - old-skill\n---\n\n# needs\n",
            encoding="utf-8",
        )
        write_skill(self.root, "skills/old", "old-skill", "Use when the old helper is present.")
        (self.root / "skills/cycle-b").mkdir(parents=True)
        (self.root / "skills/cycle-b/SKILL.md").write_text(
            "---\nname: cycle-b\ndescription: Use when the cycle returns.\n"
            "metadata:\n  depends: needs\n---\n\n# cycle-b\n",
            encoding="utf-8",
        )
        report = self.findings(
            [
                self.row("needs", "skills/needs"),
                self.row("old-skill", "skills/old", status="retired"),
                self.row("cycle-b", "skills/cycle-b"),
                self.row(
                    "pinned",
                    "skills/pinned",
                    compatibility_constraints=[
                        {"kind": "upstream_version", "value": "1.0"},
                        {"kind": "upstream_version", "value": "2.0"},
                    ],
                ),
            ]
        )
        texts = [" ".join(item["evidence"]) for item in report["findings"] if item["type"] == "dependency_conflict"]
        self.assertTrue(any("retired" in text for text in texts))
        self.assertTrue(any("dependency cycle" in text for text in texts))
        self.assertTrue(any("upstream_version" in text for text in texts))

    def test_prose_dependency_to_a_missing_skill(self):
        write_skill(
            self.root,
            "skills/needs",
            "needs",
            "Use when a helper is required.",
            "This depends on skill `missing-skill`.",
        )
        report = self.findings([self.row("needs", "skills/needs")])
        self.assertIn("dependency_conflict", self.types_for(report, "needs"))
        self.assertTrue(any("missing-skill" in item for item in report["findings"][0]["evidence"]))

    def test_report_does_not_choose_a_winner_or_write_inventory(self):
        inventory = self.root / "inventory.json"
        write_skill(self.root, "skills/alpha", "vault-alpha", "Owns page naming for the record.")
        write_skill(self.root, "skills/beta", "vault-beta", "It must not decide page naming.")
        payload = {
            "schema": "skills-inventory/v1",
            "skills": [self.row("vault-alpha", "skills/alpha"), self.row("vault-beta", "skills/beta")],
        }
        inventory.write_text(json.dumps(payload), encoding="utf-8")
        before = inventory.read_bytes()
        completed = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--inventory",
                str(inventory),
                "--repo-root",
                str(self.root),
                "--no-default-roots",
                "--format",
                "json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(inventory.read_bytes(), before)
        document = json.loads(completed.stdout)
        self.assertTrue(document["read_only"])
        self.assertNotIn("winner", json.dumps(document))
        self.assertGreater(document["summary"]["behavior_divergence"], 0)


if __name__ == "__main__":
    unittest.main()
