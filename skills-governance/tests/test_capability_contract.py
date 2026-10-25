import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills-governance" / "scripts" / "capability_contract.py"


def load_contract():
    spec = importlib.util.spec_from_file_location("capability_contract", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LOCK = """
schema_version: "0.1"
generated_by: skil-lock test
skills:
  demo:
    runtime: codex
    source_path: .codex/skills/demo/SKILL.md
    behavior:
      shell_commands:
        - curl
      network_urls:
        - https://evil.example/hook
      file_reads:
        - notes/readme.md
      file_writes:
        - .env
      allowed_tools:
        - Write
      bundled_scripts: []
"""


class CapabilityContractTests(unittest.TestCase):
    def setUp(self):
        self.module = load_contract()

    def test_lock_parser_reads_skil_lock_behavior(self):
        behaviors = self.module.parse_lock_behaviors(LOCK)
        self.assertEqual(behaviors["demo"]["network_urls"], ["https://evil.example/hook"])
        self.assertEqual(behaviors["demo"]["shell_commands"], ["curl"])
        self.assertEqual(behaviors["demo"]["file_writes"], [".env"])
        self.assertIsNone(self.module.parse_lock_behaviors("skills.lock\n"))

    def test_frontmatter_allowlist_and_rejection(self):
        text = """---
name: demo
capabilities:
  network:
    - api.example.com
    - "*.github.com"
  secrets: true
  filesystem: read
---
Body.
"""
        declared = self.module.parse_capabilities(text)
        self.assertEqual(declared["network"], ["api.example.com", "*.github.com"])
        self.assertTrue(declared["secrets"])
        self.assertEqual(declared["filesystem"], "read")
        malformed = self.module.parse_capabilities("---\nname: demo\ncapabilities:\n  shell: true\n---\n")
        self.assertIn("unknown keys", malformed["error"])

    def test_overreach_uses_skil_lock_fields(self):
        behavior = self.module.parse_lock_behaviors(LOCK)["demo"]
        denied = self.module.violations(
            {"network": False, "secrets": False, "filesystem": "read"},
            behavior,
        )
        self.assertTrue(any("network_urls" in message for message in denied))
        self.assertTrue(any("shell_commands" in message for message in denied))
        self.assertTrue(any("protected path" in message for message in denied))
        self.assertTrue(any("requires write" in message for message in denied))
        granted = self.module.violations(
            {"network": True, "secrets": True, "filesystem": "write"},
            behavior,
        )
        self.assertEqual(granted, [])
        allow = self.module.violations(
            {"network": ["evil.example"], "secrets": True, "filesystem": "write"},
            {**behavior, "shell_commands": []},
        )
        self.assertEqual(allow, [])

    def test_protected_path_globs_match_skil_lock_defaults(self):
        self.assertTrue(self.module.glob_match("**/.env", ".env"))
        self.assertTrue(self.module.glob_match("**/secrets/**", "pkg/secrets/token"))
        self.assertTrue(self.module.glob_match("**/*secret*", "dir/my-secret.txt"))
        self.assertFalse(self.module.glob_match("**/.env.*", ".env"))
        self.assertTrue(self.module.glob_match("**/.env.*", ".env.local"))

    def test_quarantine_and_policy_exclusion_block_promotion(self):
        declared = {"network": True, "secrets": False, "filesystem": "read"}
        active = self.module.evaluate_record(
            "demo", declared, False, None, False, "active", None, "none"
        )
        self.assertEqual(active["isolation"], "required")
        self.assertEqual(active["promotion"], "blocked")
        quarantined = self.module.evaluate_record(
            "demo", declared, False, None, False, "quarantined", None, "none"
        )
        self.assertEqual(quarantined["isolation"], "ok")
        self.assertEqual(quarantined["promotion"], "blocked")
        self.assertIn("quarantined", quarantined["promotion_block_reasons"][0])
        excluded = self.module.evaluate_record(
            "demo", declared, False, None, False, "active", "2026-10-17T00:00:00Z", "policy_exclusion"
        )
        self.assertEqual(excluded["isolation"], "ok")
        self.assertEqual(excluded["promotion"], "blocked")
        reviewed = self.module.evaluate_record(
            "demo", declared, False, None, False, "active", "2026-10-17T00:00:00Z", "none"
        )
        self.assertEqual(reviewed["promotion"], "allowed")

    def test_check_fails_closed_for_unreviewed_high_risk_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "skills" / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: demo\ncapabilities:\n  network: true\n  secrets: true\n  filesystem: write\n---\n",
                encoding="utf-8",
            )
            inventory = {
                "schema": "skills-inventory/v1",
                "skills": [
                    {
                        "name": "demo",
                        "status": "active",
                        "review_time": None,
                        "customization_mode": "none",
                        "authority": {"canonical_path": "skills/demo"},
                    }
                ],
            }
            inventory_path = root / "inventory.json"
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            blocked = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT), "--skil-lock", "fallback",
                    "--inventory",
                    str(inventory_path),
                    "--repo-root",
                    str(root),
                    "--check",
                    "--format",
                    "json",
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
            payload = json.loads(blocked.stdout)
            self.assertEqual(payload["observation"], "unmeasured")
            self.assertEqual(payload["skills"][0]["isolation"], "required")
            inventory["skills"][0]["status"] = "quarantined"
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            isolated = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT), "--skil-lock", "fallback",
                    "--inventory",
                    str(inventory_path),
                    "--repo-root",
                    str(root),
                    "--check",
                    "--format",
                    "json",
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(isolated.returncode, 0, isolated.stdout + isolated.stderr)
            promote = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT), "--skil-lock", "fallback",
                    "--inventory",
                    str(inventory_path),
                    "--repo-root",
                    str(root),
                    "--promote",
                    "demo",
                    "--check",
                    "--format",
                    "json",
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(promote.returncode, 2, promote.stdout + promote.stderr)
            self.assertEqual(json.loads(promote.stdout)["skills"][0]["promotion"], "blocked")

    def test_lock_parser_edges(self):
        empty = 'schema_version: "0.1"\nskills: {}\n'
        self.assertEqual(self.module.parse_lock_behaviors(empty), {})
        crlf = LOCK.replace("\n", "\r\n")
        self.assertEqual(self.module.parse_lock_behaviors(crlf)["demo"]["bundled_scripts"], [])
        inline = """
schema_version: "0.1"
skills:
  demo:
    behavior:
      network_urls: ["https://example.test/a"]
      shell_commands: []
"""
        parsed = self.module.parse_lock_behaviors(inline)
        self.assertEqual(parsed["demo"]["network_urls"], ["https://example.test/a"])
        self.assertEqual(parsed["demo"]["shell_commands"], [])
        missing = """
schema_version: "0.1"
skills:
  demo:
    runtime: codex
"""
        self.assertIsNone(self.module.parse_lock_behaviors(missing))
        quoted = """---
name: demo
capabilities:
  network:
    - "api.example.com"
  secrets: false
  filesystem: none
---
"""
        declared = self.module.parse_capabilities(quoted.replace("\n", "\r\n"))
        self.assertEqual(declared["network"], ["api.example.com"])
        same_indent = """
schema_version: "0.1"
skills:
  demo:
    behavior:
      shell_commands: []
      network_urls:
      - https://evil.example/hook
      file_reads: []
      file_writes: []
      allowed_tools: []
      bundled_scripts: []
"""
        parsed_same = self.module.parse_lock_behaviors(same_indent)
        self.assertIsNotNone(parsed_same)
        self.assertEqual(parsed_same["demo"]["network_urls"], ["https://evil.example/hook"])
        self.assertEqual(
            self.module.violations(
                {"network": False, "secrets": False, "filesystem": "none"},
                parsed_same["demo"],
            ),
            ["network: declared false but SkilLock network_urls is https://evil.example/hook"],
        )
        quoted = """
schema_version: "0.1"
skills:
  "demo":
    "behavior":
      "network_urls":
        - "https://evil.example/hook"
      shell_commands: []
"""
        parsed_quoted = self.module.parse_lock_behaviors(quoted)
        self.assertEqual(parsed_quoted["demo"]["network_urls"], ["https://evil.example/hook"])
        inline = """
schema_version: "0.1"
skills:
  demo:
    behavior: {"network_urls": ["https://evil.example/hook"], "shell_commands": []}
"""
        parsed_inline = self.module.parse_lock_behaviors(inline)
        self.assertEqual(parsed_inline["demo"]["network_urls"], ["https://evil.example/hook"])
        dropped = """
schema_version: "0.1"
skills:
  demo:
    behavior:
      network_urls: https://evil.example/hook
"""
        with self.assertRaises(self.module.LockParseError):
            self.module.parse_lock_behaviors(dropped)

    def test_unmeasured_undeclared_signal_is_high_risk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "skills" / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: demo\n---\nUse curl https://example.test/hook\n",
                encoding="utf-8",
            )
            inventory = {
                "schema": "skills-inventory/v1",
                "skills": [
                    {
                        "name": "demo",
                        "status": "active",
                        "review_time": None,
                        "customization_mode": "none",
                        "authority": {"canonical_path": "skills/demo"},
                    }
                ],
            }
            inventory_path = root / "inventory.json"
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            flagged = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT), "--skil-lock", "fallback",
                    "--inventory",
                    str(inventory_path),
                    "--repo-root",
                    str(root),
                    "--format",
                    "json",
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(flagged.returncode, 0, flagged.stderr)
            row = json.loads(flagged.stdout)["skills"][0]
            self.assertTrue(row["high_risk"])
            self.assertIn("network_behavior", row["signals"])
            self.assertEqual(row["promotion"], "blocked")
            self.assertEqual(row["isolation"], "required")
            (skill / "SKILL.md").write_text(
                "---\nname: demo\ncapabilities:\n  network: false\n  secrets: false\n  filesystem: none\n---\n"
                "Use curl https://example.test/hook\n",
                encoding="utf-8",
            )
            declared = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT), "--skil-lock", "fallback",
                    "--inventory",
                    str(inventory_path),
                    "--repo-root",
                    str(root),
                    "--skill-root",
                    str(skill),
                    "--format",
                    "json",
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(declared.returncode, 0, declared.stderr)
            self.assertNotIn("AttributeError", declared.stderr)
            declared_row = json.loads(declared.stdout)["skills"][0]
            self.assertFalse(declared_row["high_risk"])
            self.assertEqual(declared_row["signals"], [])
            self.assertEqual(declared_row["promotion"], "allowed")

    def test_repository_flags_vaultwarden_and_undeclared_signals(self):
        report = subprocess.run(
            [sys.executable, str(SCRIPT), "--skil-lock", "fallback", "--format", "json"],
            text=True,
            capture_output=True,
            cwd=REPO,
        )
        self.assertEqual(report.returncode, 0, report.stderr)
        payload = json.loads(report.stdout)
        self.assertEqual(payload["observation"], "unmeasured")
        by_name = {row["name"]: row for row in payload["skills"]}
        vault = by_name["vaultwarden-secret-migration"]
        self.assertTrue(vault["high_risk"])
        self.assertEqual(vault["status"], "quarantined")
        self.assertIsNone(vault["review_time"])
        self.assertEqual(vault["promotion"], "blocked")
        self.assertEqual(vault["isolation"], "ok")
        self.assertEqual(vault["declared"]["network"], True)
        self.assertTrue(vault["declared"]["secrets"])
        self.assertEqual(vault["declared"]["filesystem"], "write")
        self.assertGreater(payload["summary"]["high_risk"], 1)
