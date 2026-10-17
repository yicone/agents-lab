import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "skill_patch.py"
CHECK = Path(__file__).parents[1] / "scripts" / "customization_check.py"
GUARD = Path(__file__).parents[1] / "scripts" / "skill_guard.py"
PACKAGE_ROOT = Path(__file__).parents[1]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_tree(root: Path, files: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


SKILL_BASE = b"---\nname: demo\n---\nalpha\nbeta\ngamma\ndelta\nepsilon\nzeta\neta\ntheta\n"
SKILL_LOCAL = b"---\nname: demo\n---\nalpha\nbeta\ngamma\ndelta\nepsilon\nzeta\neta\nLOCAL\n"
SKILL_UPSTREAM = b"---\nname: demo\n---\nUPSTREAM\nbeta\ngamma\ndelta\nepsilon\nzeta\neta\ntheta\n"
SKILL_UPSTREAM_SAME = b"---\nname: demo\n---\nalpha\nbeta\ngamma\ndelta\nepsilon\nzeta\neta\nUPSTREAM\n"


class SkillPatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.customizations = self.base / "customizations"
        self.customizations.mkdir()
        self.baseline = write_tree(
            self.base / "baseline" / "demo",
            {
                "SKILL.md": SKILL_BASE,
                "old.txt": b"remove me\n",
            },
        )
        self.local = write_tree(
            self.base / "local" / "demo",
            {
                "SKILL.md": SKILL_LOCAL,
                "notes.md": b"local only\n",
            },
        )

    def run_patch(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            text=True,
            capture_output=True,
        )

    def create(self, *extra, expect=0):
        result = self.run_patch(
            "create",
            "--skill", "demo",
            "--baseline", str(self.baseline),
            "--local", str(self.local),
            "--customizations-dir", str(self.customizations),
            "--baseline-ref", "github:owner/demo@v1",
            "--reason", "test patch",
            *extra,
        )
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def record_dir(self):
        return self.customizations / "demo"

    def record(self):
        return json.loads((self.record_dir() / "customization.json").read_text())

    def rebase(self, upstream, output, *extra):
        return self.run_patch(
            "rebase",
            "--skill", "demo",
            "--old-baseline", str(self.baseline),
            "--upstream", str(upstream),
            "--output", str(output),
            "--customizations-dir", str(self.customizations),
            *extra,
        )

    def write_inventory(self, baseline_ref="github:owner/demo@v1"):
        inventory = self.base / "inventory.json"
        document = {
            "schema": "skills-inventory/v1",
            "skills": [
                {
                    "name": "demo",
                    "customization_mode": "overlay",
                    "baseline_ref": baseline_ref,
                    "compatibility_constraints": [],
                }
            ],
        }
        inventory.write_text(json.dumps(document), encoding="utf-8")
        return inventory

    def run_check(self, inventory):
        return subprocess.run(
            [
                sys.executable, str(CHECK), "check",
                "--inventory", str(inventory),
                "--customizations-dir", str(self.customizations),
                "--repo-root", str(self.base / "repo"),
            ],
            text=True,
            capture_output=True,
        )

    def test_create_writes_patch_record_and_passes_check(self):
        payload = self.create()
        self.assertTrue(payload["ok"])
        patch_file = self.record_dir() / "local.patch"
        self.assertTrue(patch_file.is_file())
        record = self.record()
        self.assertEqual(record["mode"], "overlay")
        self.assertNotIn("ops", record)
        self.assertEqual(record["patch"]["sha256"], sha256_bytes(patch_file.read_bytes()))
        self.assertEqual(
            record["patch"]["files"],
            {
                "SKILL.md": sha256_bytes(SKILL_BASE),
                "notes.md": None,
                "old.txt": sha256_bytes(b"remove me\n"),
            },
        )
        inventory = self.write_inventory()
        result = self.run_check(inventory)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_create_requires_reason_and_ref_for_new_record(self):
        result = self.run_patch(
            "create",
            "--skill", "demo",
            "--baseline", str(self.baseline),
            "--local", str(self.local),
            "--customizations-dir", str(self.customizations),
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertFalse((self.record_dir() / "customization.json").exists())

    def test_create_rejects_ops_record_and_identical_trees(self):
        (self.local / "SKILL.md").write_bytes(SKILL_BASE)
        (self.local / "notes.md").unlink()
        (self.local / "old.txt").write_bytes(b"remove me\n")
        result = self.run_patch(
            "create",
            "--skill", "demo",
            "--baseline", str(self.baseline),
            "--local", str(self.local),
            "--customizations-dir", str(self.customizations),
            "--baseline-ref", "r", "--reason", "x",
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("no differences", result.stdout)

        self.local = write_tree(
            self.base / "local-b" / "demo",
            {"SKILL.md": SKILL_LOCAL, "notes.md": b"local only\n"},
        )
        self.create()
        record_path = self.record_dir() / "customization.json"
        record = self.record()
        record.pop("patch")
        record["ops"] = [{"op": "delete", "path": "x", "base_sha256": "a" * 64}]
        record_path.write_text(json.dumps(record), encoding="utf-8")
        again = self.run_patch(
            "create",
            "--skill", "demo",
            "--baseline", str(self.baseline),
            "--local", str(self.local),
            "--customizations-dir", str(self.customizations),
        )
        self.assertEqual(again.returncode, 1, again.stdout)
        self.assertIn("ops", again.stdout)

    def test_rebase_clean_merge_then_stage_and_gate(self):
        upstream = write_tree(
            self.base / "up1" / "demo",
            {"SKILL.md": SKILL_UPSTREAM, "old.txt": b"remove me\n", "new-upstream.txt": b"u\n"},
        )
        self.create()
        output = self.base / "out"
        result = self.rebase(upstream, output)
        self.assertEqual(result.returncode, 0, result.stdout)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        target = output / "demo"
        self.assertEqual(Path(payload["output"]), target.resolve())
        merged = (target / "SKILL.md").read_bytes()
        self.assertIn(b"UPSTREAM", merged)
        self.assertIn(b"LOCAL", merged)
        self.assertNotIn(b"<<<<<<<", merged)
        self.assertEqual((target / "notes.md").read_bytes(), b"local only\n")
        self.assertEqual((target / "new-upstream.txt").read_bytes(), b"u\n")
        self.assertFalse((target / "old.txt").exists())
        statuses = {item["path"]: item["status"] for item in payload["files"]}
        self.assertEqual(statuses["SKILL.md"], "merged")
        self.assertFalse((output / "demo.conflicts").exists())

        snapshot = self.base / "snap"
        made = subprocess.run(
            [
                sys.executable, str(GUARD), "snapshot",
                "--skill-root", str(self.baseline.parent),
                "--source-class", "third_party_customized",
                "--output", str(snapshot),
            ],
            text=True, capture_output=True,
        )
        self.assertEqual(made.returncode, 0, made.stderr)
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
                "--format", "json", "--gate", "--skil-lock", "fallback",
            ],
            text=True, capture_output=True,
        )
        report = json.loads(diff.stdout)
        self.assertNotIn("conflict_markers", report["signals"])

    def test_rebase_untouched_upstream_replays(self):
        upstream = write_tree(
            self.base / "up2" / "demo",
            {"SKILL.md": SKILL_BASE, "old.txt": b"remove me\n"},
        )
        self.create()
        output = self.base / "out2"
        result = self.rebase(upstream, output)
        self.assertEqual(result.returncode, 0, result.stdout)
        target = output / "demo"
        self.assertEqual((target / "SKILL.md").read_bytes(), SKILL_LOCAL)
        self.assertEqual((target / "notes.md").read_bytes(), b"local only\n")
        self.assertFalse((target / "old.txt").exists())
        statuses = {
            item["path"]: item["status"]
            for item in json.loads(result.stdout)["files"]
        }
        self.assertEqual(statuses, {"SKILL.md": "replayed", "notes.md": "replayed", "old.txt": "replayed"})

    def test_rebase_absorbed_when_upstream_matches_local(self):
        upstream = write_tree(
            self.base / "up3" / "demo",
            {"SKILL.md": SKILL_LOCAL, "old.txt": b"remove me\n"},
        )
        self.create()
        result = self.rebase(upstream, self.base / "out3")
        self.assertEqual(result.returncode, 0, result.stdout)
        statuses = {
            item["path"]: item["status"]
            for item in json.loads(result.stdout)["files"]
        }
        self.assertEqual(statuses["SKILL.md"], "absorbed")

    def test_rebase_content_conflict_writes_conflicts_tree(self):
        upstream = write_tree(
            self.base / "up4" / "demo",
            {"SKILL.md": SKILL_UPSTREAM_SAME, "old.txt": b"remove me\n"},
        )
        self.create()
        patch_before = (self.record_dir() / "local.patch").read_bytes()
        record_before = (self.record_dir() / "customization.json").read_bytes()
        output = self.base / "out4"
        result = self.rebase(upstream, output)
        self.assertEqual(result.returncode, 2, result.stdout)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        conflicts = {item["path"]: item for item in payload["conflicts"]}
        self.assertEqual(conflicts["SKILL.md"]["reason"], "content")
        self.assertGreaterEqual(conflicts["SKILL.md"]["count"], 1)
        self.assertFalse((output / "demo").exists())
        conflicted = output / "demo.conflicts" / "SKILL.md"
        text = conflicted.read_bytes()
        self.assertIn(b"<<<<<<< local", text)
        self.assertIn(b"||||||| base", text)
        self.assertIn(b">>>>>>> upstream", text)
        self.assertIn(b"LOCAL", text)
        self.assertIn(b"UPSTREAM", text)
        self.assertEqual((self.record_dir() / "local.patch").read_bytes(), patch_before)
        self.assertEqual(
            (self.record_dir() / "customization.json").read_bytes(), record_before
        )

    def test_rebase_modify_delete_and_binary_conflicts(self):
        baseline = write_tree(
            self.base / "b1" / "demo",
            {"SKILL.md": SKILL_BASE, "tweak.txt": b"upstream text\n"},
        )
        local = write_tree(
            self.base / "l1" / "demo",
            {"SKILL.md": SKILL_BASE, "tweak.txt": b"local text\n"},
        )
        upstream = write_tree(
            self.base / "up5" / "demo",
            {"SKILL.md": SKILL_BASE},
        )
        created = self.run_patch(
            "create",
            "--skill", "demo",
            "--baseline", str(baseline),
            "--local", str(local),
            "--customizations-dir", str(self.customizations),
            "--baseline-ref", "r1", "--reason", "x",
        )
        self.assertEqual(created.returncode, 0, created.stdout)
        result = self.run_patch(
            "rebase",
            "--skill", "demo",
            "--old-baseline", str(baseline),
            "--upstream", str(upstream),
            "--output", str(self.base / "out5"),
            "--customizations-dir", str(self.customizations),
        )
        self.assertEqual(result.returncode, 2, result.stdout)
        conflicts = {
            item["path"]: item["reason"]
            for item in json.loads(result.stdout)["conflicts"]
        }
        self.assertEqual(conflicts["tweak.txt"], "modify_delete")
        kept = self.base / "out5" / "demo.conflicts" / "tweak.txt"
        self.assertEqual(kept.read_bytes(), b"local text\n")

        baseline2 = write_tree(
            self.base / "b2" / "demo",
            {"SKILL.md": SKILL_BASE, "bin.dat": b"\x00\x01\x02\x03"},
        )
        local2 = write_tree(
            self.base / "l2" / "demo",
            {"SKILL.md": SKILL_BASE, "bin.dat": b"\x00\x01\x02LOCAL"},
        )
        upstream2 = write_tree(
            self.base / "u2" / "demo",
            {"SKILL.md": SKILL_BASE, "bin.dat": b"\x00\x01\x02UPS"},
        )
        created = self.run_patch(
            "create",
            "--skill", "demo",
            "--baseline", str(baseline2),
            "--local", str(local2),
            "--customizations-dir", str(self.customizations),
            "--baseline-ref", "r2", "--reason", "x",
        )
        self.assertEqual(created.returncode, 0, created.stdout)
        result = self.run_patch(
            "rebase",
            "--skill", "demo",
            "--old-baseline", str(baseline2),
            "--upstream", str(upstream2),
            "--output", str(self.base / "out5b"),
            "--customizations-dir", str(self.customizations),
        )
        self.assertEqual(result.returncode, 2, result.stdout)
        payload = json.loads(result.stdout)
        conflicts = {item["path"]: item["reason"] for item in payload["conflicts"]}
        self.assertEqual(conflicts["bin.dat"], "binary")
        kept = self.base / "out5b" / "demo.conflicts" / "bin.dat"
        self.assertEqual(kept.read_bytes(), b"\x00\x01\x02LOCAL")

    def test_rebase_add_add_conflict(self):
        baseline = write_tree(self.base / "b3" / "demo", {"SKILL.md": SKILL_BASE})
        local = write_tree(
            self.base / "l3" / "demo",
            {"SKILL.md": SKILL_BASE, "extra.md": b"local version\n"},
        )
        upstream = write_tree(
            self.base / "u3" / "demo",
            {"SKILL.md": SKILL_BASE, "extra.md": b"upstream version\n"},
        )
        created = self.run_patch(
            "create",
            "--skill", "demo",
            "--baseline", str(baseline),
            "--local", str(local),
            "--customizations-dir", str(self.customizations),
            "--baseline-ref", "r3", "--reason", "x",
        )
        self.assertEqual(created.returncode, 0, created.stdout)
        result = self.run_patch(
            "rebase",
            "--skill", "demo",
            "--old-baseline", str(baseline),
            "--upstream", str(upstream),
            "--output", str(self.base / "out6"),
            "--customizations-dir", str(self.customizations),
        )
        self.assertEqual(result.returncode, 2, result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["conflicts"][0]["reason"], "content")
        text = (self.base / "out6" / "demo.conflicts" / "extra.md").read_bytes()
        self.assertIn(b"<<<<<<< local", text)
        self.assertIn(b"local version", text)
        self.assertIn(b"upstream version", text)

    def test_rebase_errors(self):
        self.create()
        # old baseline drift
        drifted = write_tree(
            self.base / "drift" / "demo",
            {"SKILL.md": b"drifted\n", "old.txt": b"remove me\n"},
        )
        upstream = write_tree(
            self.base / "up7" / "demo",
            {"SKILL.md": SKILL_UPSTREAM, "old.txt": b"remove me\n"},
        )
        result = self.run_patch(
            "rebase", "--skill", "demo",
            "--old-baseline", str(drifted),
            "--upstream", str(upstream),
            "--output", str(self.base / "o1"),
            "--customizations-dir", str(self.customizations),
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("old baseline does not match record", result.stdout)

        # tampered local.patch
        patch_file = self.record_dir() / "local.patch"
        patch_file.write_bytes(patch_file.read_bytes() + b"\n")
        result = self.rebase(upstream, self.base / "o2")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("local.patch", result.stdout)
        patch_file.write_bytes(patch_file.read_bytes()[:-1])

        # missing record
        result = self.run_patch(
            "rebase", "--skill", "ghost",
            "--old-baseline", str(self.baseline),
            "--upstream", str(upstream),
            "--output", str(self.base / "o3"),
            "--customizations-dir", str(self.customizations),
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("no customization record", result.stdout)

        # patch that cannot apply: break a hunk and re-hash it
        broken = patch_file.read_bytes().replace(b"theta", b"BROKEN")
        patch_file.write_bytes(broken)
        record_path = self.record_dir() / "customization.json"
        record = self.record()
        record["patch"]["sha256"] = sha256_bytes(broken)
        record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        result = self.rebase(upstream, self.base / "o4")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("does not apply", result.stdout)

        # output exists without --force, then with --force
        self.create()
        output = self.base / "o5"
        first = self.rebase(upstream, output)
        self.assertEqual(first.returncode, 0, first.stdout)
        second = self.rebase(upstream, output)
        self.assertEqual(second.returncode, 1, second.stdout)
        self.assertIn("use --force", second.stdout)
        forced = self.rebase(upstream, output, "--force")
        self.assertEqual(forced.returncode, 0, forced.stdout)

    def test_guard_blocks_conflict_markers(self):
        live = write_tree(self.base / "liveroot", {"demo/SKILL.md": SKILL_BASE})
        candidate = write_tree(
            self.base / "candroot",
            {
                "demo/SKILL.md": SKILL_BASE
                + b"<<<<<<< local\nours\n=======\ntheirs\n>>>>>>> upstream\n"
            },
        )
        snapshot = self.base / "gsnap"
        made = subprocess.run(
            [
                sys.executable, str(GUARD), "snapshot",
                "--skill-root", str(live), "--source-class", "third_party_customized",
                "--output", str(snapshot),
            ],
            text=True, capture_output=True,
        )
        self.assertEqual(made.returncode, 0, made.stderr)
        staging = self.base / "gstaging"
        staged = subprocess.run(
            [
                sys.executable, str(GUARD), "stage",
                "--skill-root", str(candidate), "--live-root", str(live),
                "--snapshot", str(snapshot), "--output", str(staging),
            ],
            text=True, capture_output=True,
        )
        self.assertEqual(staged.returncode, 0, staged.stderr)
        diff = subprocess.run(
            [
                sys.executable, str(GUARD), "diff",
                "--snapshot", str(snapshot), "--staging", str(staging),
                "--gate", "--format", "json", "--skil-lock", "fallback",
            ],
            text=True, capture_output=True,
        )
        self.assertEqual(diff.returncode, 2, diff.stdout)
        self.assertIn("conflict_markers", json.loads(diff.stdout)["signals"])
        refused = subprocess.run(
            [
                sys.executable, str(GUARD), "activate",
                "--staging", str(staging), "--skil-lock", "fallback",
            ],
            text=True, capture_output=True,
        )
        self.assertEqual(refused.returncode, 2, refused.stdout)

        sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))
        import skill_guard

        old_marked = b"<<<<<<< a\nx\n>>>>>>> b\n"
        new_marked = b"<<<<<<< a\ny\n=======\nz\n>>>>>>> b\n"
        self.assertNotIn(
            "conflict_markers",
            skill_guard.risk_signals("f.txt", old_marked, new_marked, "modified", "skill"),
        )
        self.assertIn(
            "conflict_markers",
            skill_guard.risk_signals("f.txt", b"plain\n", new_marked, "modified", "skill"),
        )

    def test_resolution_round_trip(self):
        upstream = write_tree(
            self.base / "up8" / "demo",
            {"SKILL.md": SKILL_UPSTREAM_SAME, "old.txt": b"remove me\n"},
        )
        self.create()
        output = self.base / "out8"
        result = self.rebase(upstream, output)
        self.assertEqual(result.returncode, 2, result.stdout)
        resolved = self.base / "resolved" / "demo"
        shutil.copytree(output / "demo.conflicts", resolved)
        (resolved / "SKILL.md").write_bytes(
            b"---\nname: demo\n---\nalpha\nbeta\ngamma\ndelta\nepsilon\nzeta\neta\nRESOLVED\n"
        )
        refreshed = self.run_patch(
            "create",
            "--skill", "demo",
            "--baseline", str(upstream),
            "--local", str(resolved),
            "--customizations-dir", str(self.customizations),
            "--baseline-ref", "github:owner/demo@v2",
        )
        self.assertEqual(refreshed.returncode, 0, refreshed.stdout)
        record = self.record()
        self.assertEqual(record["baseline_ref"], "github:owner/demo@v2")
        inventory = self.write_inventory(baseline_ref="github:owner/demo@v2")
        result = self.run_check(inventory)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
