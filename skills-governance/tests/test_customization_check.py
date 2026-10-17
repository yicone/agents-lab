import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "customization_check.py"
GUARD = Path(__file__).parents[1] / "scripts" / "skill_guard.py"
PACKAGE_ROOT = Path(__file__).parents[1]
SCHEMA = Path(__file__).parents[1] / "config" / "skills-customization.schema.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


class CustomizationCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.customizations = self.base / "customizations"
        self.customizations.mkdir()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.inventory = self.base / "inventory.json"
        self.skills = []

    def write_inventory(self):
        document = {"schema": "skills-inventory/v1", "updated_at": "2026-10-17T00:00:00Z", "skills": self.skills}
        self.inventory.write_text(json.dumps(document), encoding="utf-8")

    def add_skill(self, name, mode, baseline_ref="github:owner/demo", constraints=None):
        self.skills.append(
            {
                "name": name,
                "customization_mode": mode,
                "baseline_ref": baseline_ref,
                "compatibility_constraints": constraints or [],
            }
        )

    def record(self, name, **fields):
        record = {
            "schema": "skills-customization/v1",
            "skill": name,
            "mode": "overlay",
            "reason": "test delta",
            "baseline_ref": "github:owner/demo",
            "reviewed_at": "2026-10-17T00:00:00Z",
        }
        record.update(fields)
        directory = self.customizations / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "customization.json").write_text(json.dumps(record), encoding="utf-8")
        return directory

    def write_delta(self, directory, relative, data: bytes, mode=0o644):
        path = directory / "delta" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
        return data

    def run_check(self, *extra):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "check",
                "--inventory",
                str(self.inventory),
                "--customizations-dir",
                str(self.customizations),
                "--repo-root",
                str(self.repo),
                *extra,
            ],
            text=True,
            capture_output=True,
        )

    def run_apply(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "apply", "--customizations-dir", str(self.customizations), *args],
            text=True,
            capture_output=True,
        )

    def errors_of(self, result):
        self.assertEqual(result.returncode, 1, result.stdout)
        return json.loads(result.stdout)["errors"]

    def messages(self, result):
        return " | ".join(error["message"] for error in self.errors_of(result))

    def test_check_valid_records_for_all_modes(self):
        self.add_skill("wrap", "wrapper")
        (self.repo / "skills/wrap").mkdir(parents=True)
        (self.repo / "skills/wrap/SKILL.md").write_text("---\nname: wrap\n---\n", encoding="utf-8")
        self.record("wrap", mode="wrapper", path="skills/wrap")

        self.add_skill("forked", "fork")
        (self.repo / "skills/forked").mkdir(parents=True)
        (self.repo / "skills/forked/SKILL.md").write_text("---\nname: forked\n---\n", encoding="utf-8")
        self.record("forked", mode="fork", path="skills/forked")

        self.add_skill("replaced", "local_replacement")
        (self.repo / "skills/replaced").mkdir(parents=True)
        (self.repo / "skills/replaced/SKILL.md").write_text("---\nname: replaced\n---\n", encoding="utf-8")
        self.record("replaced", mode="local_replacement", path="skills/replaced")

        self.add_skill("excluded", "policy_exclusion", constraints=[{"kind": "policy", "value": "do not load"}])
        self.record("excluded", mode="policy_exclusion", policy="do not load")

        self.add_skill("overlaid", "overlay")
        directory = self.record(
            "overlaid",
            mode="overlay",
            ops=[
                {
                    "op": "add",
                    "path": "notes.md",
                    "source": "notes.md",
                    "sha256": sha256_bytes(b"local notes\n"),
                }
            ],
        )
        self.write_delta(directory, "notes.md", b"local notes\n")

        self.write_inventory()
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["skills_checked"], 5)

    def test_missing_record_for_inventory_mode(self):
        self.add_skill("demo", "overlay")
        self.write_inventory()
        self.assertIn("record required", self.messages(self.run_check()))

    def test_record_mode_mismatch(self):
        self.add_skill("demo", "fork")
        self.record("demo", mode="overlay", ops=[{"op": "delete", "path": "x", "base_sha256": "a" * 64}])
        self.write_inventory()
        self.assertIn("does not match inventory customization_mode", self.messages(self.run_check()))

    def test_orphan_record(self):
        self.add_skill("demo", "none")
        (self.customizations / "demo").mkdir()
        (self.customizations / "ghost").mkdir()
        self.write_inventory()
        errors = self.errors_of(self.run_check())
        orphan_paths = {e["path"] for e in errors if "orphan record" in e["message"]}
        self.assertEqual(orphan_paths, {"customizations/demo", "customizations/ghost"})

    def test_unknown_mode(self):
        self.add_skill("demo", "unknown")
        self.write_inventory()
        self.assertIn("unresolved customization: classify the mode", self.messages(self.run_check()))

    def test_stale_baseline_ref(self):
        self.add_skill("demo", "overlay", baseline_ref="github:owner/demo@v2")
        self.record("demo", ops=[{"op": "delete", "path": "old.txt", "base_sha256": "a" * 64}])
        self.write_inventory()
        self.assertIn("stale delta: re-review against new baseline", self.messages(self.run_check()))

    def test_bad_schema_and_unknown_key(self):
        self.add_skill("demo", "overlay")
        self.record(
            "demo",
            schema="other/v9",
            extra="nope",
            ops=[{"op": "delete", "path": "x", "base_sha256": "a" * 64}],
        )
        self.write_inventory()
        messages = self.messages(self.run_check())
        self.assertIn("must be skills-customization/v1", messages)
        self.assertIn("unknown field", messages)

    def test_unsafe_op_paths(self):
        self.add_skill("demo", "overlay")
        self.record(
            "demo",
            ops=[
                {"op": "delete", "path": "../escape", "base_sha256": "a" * 64},
                {"op": "delete", "path": "/abs/path", "base_sha256": "a" * 64},
                {"op": "delete", "path": "a//b", "base_sha256": "a" * 64},
                {"op": "delete", "path": "ok.txt", "base_sha256": "a" * 64},
                {"op": "delete", "path": "ok.txt", "base_sha256": "a" * 64},
            ],
        )
        self.write_inventory()
        messages = self.messages(self.run_check())
        self.assertIn("must be a relative path", messages)
        self.assertIn("duplicate", messages)

    def test_delta_source_hash_mismatch_and_missing_source(self):
        self.add_skill("demo", "overlay")
        directory = self.record(
            "demo",
            ops=[
                {"op": "add", "path": "a.md", "source": "a.md", "sha256": "b" * 64},
                {"op": "add", "path": "b.md", "source": "missing.md", "sha256": "c" * 64},
            ],
        )
        self.write_delta(directory, "a.md", b"content\n")
        self.write_inventory()
        messages = self.messages(self.run_check())
        self.assertIn("does not match delta source sha256", messages)
        self.assertIn("not found in delta/", messages)

    def test_op_field_rules(self):
        self.add_skill("demo", "overlay")
        self.record(
            "demo",
            ops=[
                {"op": "add", "path": "a", "source": "s", "sha256": "a" * 64, "base_sha256": "b" * 64},
                {"op": "replace", "path": "b", "source": "s", "sha256": "a" * 64},
                {"op": "delete", "path": "c", "source": "s", "base_sha256": "b" * 64},
            ],
        )
        self.write_inventory()
        messages = self.messages(self.run_check())
        self.assertIn("not allowed for add", messages)
        self.assertIn("not allowed for delete", messages)
        self.assertIn("required", messages)

    def test_wrapper_missing_path_and_skill_md(self):
        self.add_skill("demo", "wrapper")
        self.record("demo", mode="wrapper")
        self.write_inventory()
        self.assertIn("required", self.messages(self.run_check()))

        self.skills.clear()
        self.add_skill("demo", "wrapper")
        self.record("demo", mode="wrapper", path="skills/demo")
        self.write_inventory()
        self.assertIn("SKILL.md not found", self.messages(self.run_check()))

    def test_policy_exclusion_needs_policy_constraint(self):
        self.add_skill("demo", "policy_exclusion")
        self.record("demo", mode="policy_exclusion", policy="do not load")
        self.write_inventory()
        self.assertIn("kind policy", self.messages(self.run_check()))

    def make_upstream(self):
        parent = self.base / "uproot"
        upstream = parent / "demo"
        upstream.mkdir(parents=True, exist_ok=True)
        (upstream / "SKILL.md").write_bytes(b"---\nname: demo\n---\nbody\n")
        (upstream / "old.txt").write_bytes(b"old bytes\n")
        (upstream / "keep.sh").write_bytes(b"#!/bin/sh\necho hi\n")
        (upstream / "keep.sh").chmod(0o755)
        return upstream

    def make_overlay_ops_record(self, name="demo"):
        upstream = self.make_upstream()
        directory = self.record(
            name,
            ops=[
                {
                    "op": "replace",
                    "path": "SKILL.md",
                    "source": "SKILL.md",
                    "sha256": sha256_bytes(b"---\nname: demo\n---\npatched\n"),
                    "base_sha256": sha256_file(upstream / "SKILL.md"),
                },
                {
                    "op": "add",
                    "path": "extra/notes.md",
                    "source": "extra/notes.md",
                    "sha256": sha256_bytes(b"local\n"),
                },
                {
                    "op": "delete",
                    "path": "old.txt",
                    "base_sha256": sha256_file(upstream / "old.txt"),
                },
            ],
        )
        self.write_delta(directory, "SKILL.md", b"---\nname: demo\n---\npatched\n")
        self.write_delta(directory, "extra/notes.md", b"local\n")
        return upstream

    def test_apply_success(self):
        upstream = self.make_overlay_ops_record()
        output = self.base / "out"
        result = self.run_apply("--skill", "demo", "--upstream", str(upstream), "--output", str(output))
        self.assertEqual(result.returncode, 0, result.stdout)
        payload = json.loads(result.stdout)
        target = Path(payload["output"])
        self.assertEqual(target, (output / "demo").resolve())
        self.assertEqual((target / "SKILL.md").read_bytes(), b"---\nname: demo\n---\npatched\n")
        self.assertEqual((target / "extra/notes.md").read_bytes(), b"local\n")
        self.assertFalse((target / "old.txt").exists())
        self.assertTrue(os.access(target / "keep.sh", os.X_OK))
        # upstream untouched
        self.assertEqual((upstream / "SKILL.md").read_bytes(), b"---\nname: demo\n---\nbody\n")
        self.assertTrue((upstream / "old.txt").exists())

    def test_apply_conflicts_write_nothing(self):
        upstream = self.make_overlay_ops_record()
        (upstream / "SKILL.md").write_bytes(b"changed upstream\n")
        (upstream / "old.txt").unlink()
        (upstream / "extra").mkdir()
        (upstream / "extra/notes.md").write_bytes(b"now upstream has it\n")
        output = self.base / "out"
        result = self.run_apply("--skill", "demo", "--upstream", str(upstream), "--output", str(output))
        self.assertEqual(result.returncode, 2, result.stdout)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        reasons = {conflict["reason"] for conflict in payload["conflicts"]}
        self.assertEqual(reasons, {"upstream_changed", "upstream_missing", "upstream_added"})
        self.assertFalse((output / "demo").exists())

    def test_apply_rejects_invalid_record_without_writing(self):
        upstream = self.make_upstream()
        self.record(
            "demo",
            ops=[{"op": "delete", "path": "../escape.txt", "base_sha256": sha256_bytes(b"x")}],
        )
        output = self.base / "out"
        result = self.run_apply("--skill", "demo", "--upstream", str(upstream), "--output", str(output))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("relative path inside the skill directory", result.stdout)
        self.assertFalse(output.exists())

    def test_apply_refuses_non_overlay_and_existing_output(self):
        self.add_skill("demo", "fork")
        (self.repo / "skills/demo").mkdir(parents=True)
        (self.repo / "skills/demo/SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
        self.record("demo", mode="fork", path="skills/demo")
        upstream = self.make_upstream()
        result = self.run_apply("--skill", "demo", "--upstream", str(upstream), "--output", str(self.base / "out"))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("has no overlay ops", result.stdout)

        self.make_overlay_ops_record()
        output = self.base / "out2"
        first = self.run_apply("--skill", "demo", "--upstream", str(upstream), "--output", str(output))
        self.assertEqual(first.returncode, 0, first.stdout)
        second = self.run_apply("--skill", "demo", "--upstream", str(upstream), "--output", str(output))
        self.assertEqual(second.returncode, 1, second.stdout)
        forced = self.run_apply("--skill", "demo", "--upstream", str(upstream), "--output", str(output), "--force")
        self.assertEqual(forced.returncode, 0, forced.stdout)

    def test_apply_then_guard_stage_and_diff(self):
        upstream = self.make_overlay_ops_record()
        output = self.base / "out"
        applied = self.run_apply("--skill", "demo", "--upstream", str(upstream), "--output", str(output))
        self.assertEqual(applied.returncode, 0, applied.stdout)

        snapshot = self.base / "snap"
        made = subprocess.run(
            [
                sys.executable, str(GUARD), "snapshot",
                "--skill-root", str(upstream.parent), "--source-class", "third_party_customized",
                "--output", str(snapshot),
            ],
            text=True, capture_output=True,
        )
        self.assertEqual(made.returncode, 0, made.stderr)

        # snapshot records a skill root directory; stage the applied tree the same way
        staging = self.base / "staging"
        staged = subprocess.run(
            [
                sys.executable, str(GUARD), "stage",
                "--skill-root", str(output), "--snapshot", str(snapshot),
                "--output", str(staging),
            ],
            text=True, capture_output=True,
        )
        self.assertEqual(staged.returncode, 0, staged.stderr)

        diff = subprocess.run(
            [
                sys.executable, str(GUARD), "diff",
                "--snapshot", str(snapshot), "--staging", str(staging),
                "--format", "json", "--skil-lock", "fallback",
            ],
            text=True, capture_output=True,
        )
        self.assertEqual(diff.returncode, 0, diff.stdout)
        report = json.loads(diff.stdout)
        changes = {(c["kind"], c["path"]) for c in report["changes"]}
        self.assertIn(("modified", "demo/SKILL.md"), changes)
        self.assertIn(("added", "demo/extra/notes.md"), changes)
        self.assertIn(("removed", "demo/old.txt"), changes)

    def test_real_repo_state_passes(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "check"],
            cwd=PACKAGE_ROOT.parent,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["skills_checked"], 0)

    def test_schema_enums_match_validator(self):
        sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))
        import customization_check

        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(set(schema["properties"]["mode"]["enum"]), customization_check.MODES)
        op_enum = set(schema["$defs"]["op"]["properties"]["op"]["enum"])
        self.assertEqual(op_enum, customization_check.OP_KINDS)


if __name__ == "__main__":
    unittest.main()
