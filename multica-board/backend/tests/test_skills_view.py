"""Skills page payload: version drift, buckets, and the attention text."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multica_board import skills_view  # noqa: E402
from multica_board.skills_view import (  # noqa: E402
    CACHE_TTL_SECONDS,
    build_payload,
    parse_review_days,
    render_skills_attention_text,
    skills_document,
)

NOW = datetime(2026, 10, 17, 8, 30, tzinfo=timezone.utc)


def skill(name, **overrides):
    record = {
        "name": name,
        "source_class": "runtime_plugin",
        "authority": {
            "ownership": "plugin_provider",
            "source_authority": "enabled_plugin",
            "update_authority": "plugin_upgrade",
            "canonical_path": None,
        },
        "baseline": {"kind": "upstream_ref", "subject": overrides.pop("subject", None)},
        "adapters": [],
        "status": "active",
        "review_time": None,
        "baseline_ref": "plugin:codex/demo@openai-bundled",
        "current_version": "1.0.49",
        "scope": "plugin-managed",
        "customization_mode": "none",
        "compatibility_constraints": [],
        "conflict_status": "unreviewed",
        "visible_paths": [],
    }
    record.update(overrides)
    return record


class SkillsPayloadTests(unittest.TestCase):
    def setUp(self):
        skills_view._CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "skills").mkdir()
        self.inventory = self.root / "skills-inventory.json"
        self.tools = self.root / "tool-roots.json"
        self.tools.write_text(
            json.dumps(
                {
                    "tools": [{"id": "codex", "label": "Codex"}, {"id": "cursor", "label": "Cursor"}],
                    "roots": [
                        {"path": str(self.root / "shared"), "tools": ["codex", "cursor"], "kind": "shared"},
                        {"path": str(self.root / "codex"), "tools": ["codex"], "kind": "direct"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.staging = self.root / "staging"
        self.staging.mkdir()

    def tearDown(self):
        skills_view._CACHE.clear()
        self.tmp.cleanup()

    def cached(self, clock):
        return skills_document(
            repo_root=self.repo,
            inventory_path=self.inventory,
            tool_roots_path=self.tools,
            staging_root=self.staging,
            lock_path=self.root / "missing.lock",
            changelog_dir=None,
            scan_roots=[],
            clock=clock,
        )

    def payload(self, skills, *, review_days=90, extra_unrecorded=None):
        self.inventory.write_text(json.dumps({"skills": skills}), encoding="utf-8")
        return build_payload(
            repo_root=self.repo,
            inventory_path=self.inventory,
            tool_roots_path=self.tools,
            staging_root=self.staging,
            lock_path=self.root / "missing.lock",
            changelog_dir=None,
            review_days=review_days,
            now=NOW,
            scan_roots=list(extra_unrecorded or []),
        )

    def test_sibling_version_is_not_a_broken_link(self):
        recorded = self.root / "codex" / "demo" / "1.0.49" / ".codex-plugin" / "plugin.json"
        sibling = self.root / "codex" / "demo" / "1.0.50" / ".codex-plugin"
        sibling.mkdir(parents=True)
        (sibling / "plugin.json").write_text(json.dumps({"version": "1.0.50"}), encoding="utf-8")
        visible = self.root / "codex" / "demo" / "1.0.49" / "skills"
        record = skill("codex-plugin-visualize", subject=str(recorded), visible_paths=[str(visible)])
        body = self.payload([record])
        item = body["items"][0]
        self.assertTrue(body["ok"])
        self.assertEqual(item["reconciliation_status"], "version_changed")
        self.assertEqual(item["observed_version"], "1.0.50")
        self.assertEqual(item["bucket"], "version_changed")
        self.assertEqual(item["reason"], "1.0.49 → 1.0.50")
        self.assertEqual(item["tools"]["codex"], "missing")
        self.assertEqual(body["attention"]["badge"], 1)
        self.assertEqual(body["attention"]["baseline_debt"], 0)
        self.assertEqual(body["attention"]["counts"]["missing"], 0)

    def test_plain_missing_stays_missing(self):
        recorded = self.root / "codex" / "gone" / "1.0.0" / ".codex-plugin" / "plugin.json"
        (self.root / "codex" / "gone").mkdir(parents=True)
        body = self.payload([skill("gone-plugin", subject=str(recorded))])
        self.assertEqual(body["items"][0]["bucket"], "missing")
        self.assertEqual(body["attention"]["counts"]["version_changed"], 0)

    def test_baseline_debt_is_counted_but_not_badged(self):
        skill_dir = self.repo / "skills" / "quiet"
        skill_dir.mkdir(parents=True)
        text = "---\nname: quiet\nversion: 1\n---\n\nbody\n"
        (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
        digest = "sha256:" + __import__("hashlib").sha256((skill_dir / "SKILL.md").read_bytes()).hexdigest()
        record = skill(
            "quiet",
            source_class="local_custom",
            subject="SKILL.md",
            baseline_ref=digest,
            current_version="1",
            scope="repo",
            visible_paths=["skills/quiet"],
            baseline={"kind": "content_hash", "subject": "SKILL.md"},
            authority={
                "ownership": "local",
                "source_authority": "canonical_git",
                "update_authority": "repository_maintainer",
                "canonical_path": "skills/quiet",
            },
        )
        body = self.payload([record])
        item = body["items"][0]
        self.assertEqual(item["reconciliation_status"], "stale")
        self.assertTrue(item["baseline_debt"])
        self.assertIsNone(item["bucket"])
        self.assertEqual(body["attention"]["baseline_debt"], 1)
        self.assertEqual(body["attention"]["badge"], 0)
        text_body = render_skills_attention_text(body["attention"])
        self.assertIn("baseline_debt 1", text_body)
        self.assertNotIn("quiet", text_body)

    def test_unrecorded_and_fallback_gate_share_the_attention_function(self):
        install = self.root / "shared" / "colgrep-search"
        install.mkdir(parents=True)
        (install / "SKILL.md").write_text("---\nname: colgrep-search\n---\n\n", encoding="utf-8")
        staged = self.staging / "candidate"
        unit = staged / "roots" / "skill-0"
        unit.mkdir(parents=True)
        (unit / "SKILL.md").write_text("---\nname: writing-plans\n---\n\n", encoding="utf-8")
        (staged / "manifest.json").write_text(
            json.dumps(
                {
                    "status": "staged",
                    "roots": [{"kind": "skill", "staging_path": "roots/skill-0", "live_path": str(install)}],
                }
            ),
            encoding="utf-8",
        )
        (staged / "capability-gate.json").write_text(
            json.dumps({"backend": "fallback", "blocked": False, "fallback": "skill_guard risk signals", "reason": "skil-lock is not on PATH"}),
            encoding="utf-8",
        )
        body = self.payload([], extra_unrecorded=[self.root / "shared"])
        by_name = {item["name"]: item for item in body["items"]}
        self.assertEqual(by_name["colgrep-search"]["bucket"], "unrecorded")
        self.assertEqual(by_name["colgrep-search"]["tools"]["codex"], "shared")
        self.assertEqual(by_name["colgrep-search"]["tools"]["cursor"], "shared")
        self.assertEqual(by_name["writing-plans"]["bucket"], "capability_pending")
        self.assertIn("回退", by_name["writing-plans"]["reason"])
        attention = body["attention"]
        self.assertEqual(attention["badge"], attention["counts"]["unrecorded"] + attention["counts"]["capability_pending"])
        text_body = render_skills_attention_text(attention)
        self.assertIn(f"unrecorded {attention['counts']['unrecorded']}", text_body)
        self.assertIn(f"capability_pending {attention['counts']['capability_pending']}", text_body)
        self.assertNotIn("\nmissing ", "\n" + text_body)
        self.assertLessEqual(len(text_body.encode()), 2048)

    def test_cache_misses_when_skill_md_symlink_plugin_or_gate_changes(self):
        clock = {"at": NOW}

        def now():
            return clock["at"]

        skill_md = self.repo / "skills" / "quiet" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        original = "---\nname: quiet\nversion: 1\n---\n\nbody\n"
        skill_md.write_text(original, encoding="utf-8")
        digest = "sha256:" + hashlib.sha256(original.encode()).hexdigest()
        record = skill(
            "quiet",
            source_class="local_custom",
            subject="SKILL.md",
            baseline_ref=digest,
            current_version="1",
            scope="repo",
            visible_paths=["skills/quiet"],
            baseline={"kind": "content_hash", "subject": "SKILL.md"},
            authority={
                "ownership": "local",
                "source_authority": "canonical_git",
                "update_authority": "repository_maintainer",
                "canonical_path": "skills/quiet",
            },
        )
        self.inventory.write_text(json.dumps({"skills": [record]}), encoding="utf-8")
        first = self.cached(now)
        self.assertIs(self.cached(now), first)
        self.assertEqual(first["items"][0]["reconciliation_status"], "stale")
        skill_md.write_text(original + "edited\n", encoding="utf-8")
        edited = self.cached(now)
        self.assertIsNot(edited, first)
        self.assertEqual(edited["items"][0]["reconciliation_status"], "modified")

        link = self.repo / "skills" / "linked"
        target_a = self.repo / "skills" / "target-a"
        target_b = self.repo / "skills" / "target-b"
        target_a.mkdir()
        target_b.mkdir()
        linked_body = "---\nname: linked\nversion: 1\n---\n\nbody\n"
        linked_digest = "sha256:" + hashlib.sha256(linked_body.encode()).hexdigest()
        (target_a / "SKILL.md").write_text(linked_body, encoding="utf-8")
        (target_b / "SKILL.md").write_text(linked_body + "other\n", encoding="utf-8")
        os.symlink(target_a, link)
        linked = skill(
            "linked",
            source_class="local_custom",
            subject="SKILL.md",
            baseline_ref=linked_digest,
            current_version="1",
            scope="repo",
            visible_paths=["skills/linked"],
            baseline={"kind": "content_hash", "subject": "SKILL.md"},
            authority={
                "ownership": "local",
                "source_authority": "canonical_git",
                "update_authority": "repository_maintainer",
                "canonical_path": "skills/linked",
            },
        )
        self.inventory.write_text(json.dumps({"skills": [linked]}), encoding="utf-8")
        skills_view._CACHE.clear()
        linked_first = self.cached(now)
        self.assertEqual(linked_first["items"][0]["reconciliation_status"], "stale")
        link.unlink()
        os.symlink(target_b, link)
        linked_next = self.cached(now)
        self.assertIsNot(linked_next, linked_first)
        self.assertEqual(linked_next["items"][0]["reconciliation_status"], "modified")

        recorded = self.root / "codex" / "demo" / "1.0.49" / ".codex-plugin" / "plugin.json"
        recorded.parent.mkdir(parents=True)
        recorded.write_text(json.dumps({"version": "1.0.49"}), encoding="utf-8")
        plugin = skill("codex-plugin-visualize", subject=str(recorded), visible_paths=[str(recorded.parent)])
        self.inventory.write_text(json.dumps({"skills": [plugin]}), encoding="utf-8")
        skills_view._CACHE.clear()
        plugin_first = self.cached(now)
        self.assertNotEqual(plugin_first["items"][0]["reconciliation_status"], "version_changed")
        shutil.rmtree(recorded.parent.parent)
        sibling = self.root / "codex" / "demo" / "1.0.50" / ".codex-plugin"
        sibling.mkdir(parents=True)
        (sibling / "plugin.json").write_text(json.dumps({"version": "1.0.50"}), encoding="utf-8")
        plugin_next = self.cached(now)
        self.assertIsNot(plugin_next, plugin_first)
        self.assertEqual(plugin_next["items"][0]["reconciliation_status"], "version_changed")

        self.inventory.write_text(json.dumps({"skills": []}), encoding="utf-8")
        staged = self.staging / "candidate"
        unit = staged / "roots" / "skill-0"
        unit.mkdir(parents=True)
        (unit / "SKILL.md").write_text("---\nname: writing-plans\n---\n\n", encoding="utf-8")
        (staged / "manifest.json").write_text(
            json.dumps({"status": "staged", "roots": [{"kind": "skill", "staging_path": "roots/skill-0"}]}),
            encoding="utf-8",
        )
        gate = staged / "capability-gate.json"
        gate.write_text(json.dumps({"backend": "skil-lock", "blocked": False}), encoding="utf-8")
        skills_view._CACHE.clear()
        staged_first = self.cached(now)
        self.assertIsNone(next(item for item in staged_first["items"] if item["name"] == "writing-plans")["bucket"])
        gate.write_text(
            json.dumps({"backend": "fallback", "blocked": False, "reason": "skil-lock is not on PATH"}),
            encoding="utf-8",
        )
        staged_next = self.cached(now)
        self.assertIsNot(staged_next, staged_first)
        self.assertEqual(
            next(item for item in staged_next["items"] if item["name"] == "writing-plans")["bucket"],
            "capability_pending",
        )

        clock["at"] = NOW + timedelta(seconds=CACHE_TTL_SECONDS)
        expired = self.cached(now)
        self.assertIsNot(expired, staged_next)

    def test_invalid_inventory_shapes_degrade(self):
        bodies = ("[]", "null", '{"skills":["invalid"]}', '{"skills":[{"name":"quiet"}]}')
        for text in bodies:
            self.inventory.write_text(text, encoding="utf-8")
            body = build_payload(
                repo_root=self.repo,
                inventory_path=self.inventory,
                tool_roots_path=self.tools,
                staging_root=self.staging,
                lock_path=self.root / "missing.lock",
                changelog_dir=None,
                now=NOW,
                scan_roots=[],
            )
            self.assertFalse(body["ok"], text)
            self.assertEqual(body["error"]["code"], "inventory_invalid", text)
            self.assertEqual(body["items"], [], text)
            self.assertEqual(body["attention"]["badge"], 0, text)

    def test_invalid_inventory_is_a_payload_not_an_exception(self):
        self.inventory.write_text("{", encoding="utf-8")
        body = build_payload(
            repo_root=self.repo,
            inventory_path=self.inventory,
            tool_roots_path=self.tools,
            staging_root=self.staging,
            lock_path=self.root / "missing.lock",
            changelog_dir=None,
            now=NOW,
            scan_roots=[],
        )
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"]["code"], "inventory_invalid")
        self.assertEqual(body["attention"]["badge"], 0)

    def test_review_days_choices(self):
        self.assertEqual(parse_review_days(None), 90)
        self.assertEqual(parse_review_days("30"), 30)
        with self.assertRaises(ValueError):
            parse_review_days("7")


if __name__ == "__main__":
    unittest.main()
