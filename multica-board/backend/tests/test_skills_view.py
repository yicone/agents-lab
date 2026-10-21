"""Skills page payload: version drift, buckets, and the attention text."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

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


class SharedEntryTests(unittest.TestCase):
    def setUp(self):
        skills_view._CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "skills").mkdir()
        self.inventory = self.root / "skills-inventory.json"
        self.tools = self.root / "tool-roots.json"
        self.write_tools(
            [
                {"path": str(self.root / "shared"), "tools": ["codex", "cursor"], "kind": "shared"},
                {"path": str(self.root / "hermes" / "skills"), "tools": ["hermes"], "kind": "direct"},
                {"path": ".agents/skills", "tools": ["project", "hermes"], "kind": "direct", "repo": True},
            ]
        )
        self.staging = self.root / "staging"
        self.staging.mkdir()

    def tearDown(self):
        skills_view._CACHE.clear()
        self.tmp.cleanup()

    def write_tools(self, roots):
        self.tools.write_text(
            json.dumps(
                {
                    "tools": [
                        {"id": "codex", "label": "Codex"},
                        {"id": "cursor", "label": "Cursor"},
                        {"id": "hermes", "label": "Hermes"},
                        {"id": "project", "label": "仓库入口"},
                    ],
                    "roots": roots,
                }
            ),
            encoding="utf-8",
        )

    def payload(self, skills, *, extra_unrecorded=None):
        self.inventory.write_text(json.dumps({"skills": skills}), encoding="utf-8")
        return build_payload(
            repo_root=self.repo,
            inventory_path=self.inventory,
            tool_roots_path=self.tools,
            staging_root=self.staging,
            lock_path=self.root / "missing.lock",
            changelog_dir=None,
            now=NOW,
            scan_roots=list(extra_unrecorded or []),
        )

    def make_skill(self, name, **overrides):
        return skill(name, **overrides)

    def make_shared_dir(self, name, skill_md=True):
        path = self.root / "shared" / name
        path.mkdir(parents=True)
        if skill_md:
            (path / "SKILL.md").write_text(f"---\nname: {name}\n---\n\n", encoding="utf-8")
        return path

    def shared_state(self, name, **overrides):
        body = self.payload([self.make_skill(name, **overrides)])
        item = body["items"][0]
        return body, item["shared_entry"]

    def test_real_dir_with_skill_md_is_dir(self):
        self.make_shared_dir("alpha")
        body, entry = self.shared_state("alpha")
        self.assertEqual(entry["state"], "dir")
        self.assertEqual(entry["label"], "目录")
        self.assertEqual(entry["path"], f"{self.root}/shared/alpha")
        self.assertEqual(body["shared_root"]["state"], "ok")
        self.assertEqual(body["repos"][0]["label"], "repo")

    def test_valid_symlink_is_link(self):
        real = self.root / "real-beta"
        real.mkdir()
        (real / "SKILL.md").write_text("---\nname: beta\n---\n\n", encoding="utf-8")
        (self.root / "shared").mkdir()
        os.symlink(real, self.root / "shared" / "beta")
        _, entry = self.shared_state("beta")
        self.assertEqual(entry["state"], "link")
        self.assertEqual(entry["label"], "链接")
        self.assertEqual(entry["target"], str(real))
        self.assertTrue(entry["resolved"])

    def test_dangling_symlink_is_broken_link(self):
        (self.root / "shared").mkdir()
        os.symlink(self.root / "no-such-dir", self.root / "shared" / "gamma")
        _, entry = self.shared_state("gamma")
        self.assertEqual(entry["state"], "broken_link")
        self.assertEqual(entry["label"], "坏链")

    def test_absent_entry_is_none(self):
        (self.root / "shared").mkdir()
        _, entry = self.shared_state("delta")
        self.assertEqual(entry["state"], "none")
        self.assertEqual(entry["label"], "无入口")

    def test_dir_without_skill_md_is_content_missing(self):
        self.make_shared_dir("epsilon", skill_md=False)
        _, entry = self.shared_state("epsilon")
        self.assertEqual(entry["state"], "content_missing")

    def test_link_to_dir_without_skill_md_is_content_missing(self):
        real = self.root / "real-zeta"
        real.mkdir()
        (self.root / "shared").mkdir()
        os.symlink(real, self.root / "shared" / "zeta")
        _, entry = self.shared_state("zeta")
        self.assertEqual(entry["state"], "content_missing")

    def test_no_shared_root_is_unscanned(self):
        self.write_tools([{"path": str(self.root / "codex"), "tools": ["codex"], "kind": "direct"}])
        body, entry = self.shared_state("eta")
        self.assertEqual(entry["state"], "unscanned")
        self.assertEqual(entry["label"], "未扫描")
        self.assertIsNone(entry["path"])
        self.assertEqual(body["shared_root"]["state"], "unscanned")
        self.assertIsNone(body["shared_root"]["path"])

    def test_permission_error_is_denied_not_none(self):
        self.make_shared_dir("theta")
        with mock.patch.object(skills_view, "_lstat", side_effect=PermissionError("denied")):
            _, entry = self.shared_state("theta")
        self.assertEqual(entry["state"], "denied")
        self.assertEqual(entry["label"], "无权限")
        with mock.patch.object(skills_view, "_stat", side_effect=PermissionError("denied")):
            _, entry = self.shared_state("theta")
        self.assertEqual(entry["state"], "denied")

    def test_other_oserror_is_unknown(self):
        self.make_shared_dir("iota")
        with mock.patch.object(skills_view, "_lstat", side_effect=OSError(errno.EIO, "io error")):
            _, entry = self.shared_state("iota")
        self.assertEqual(entry["state"], "unknown")
        self.assertEqual(entry["label"], "无法确定")

    def test_illegal_name_is_unknown(self):
        (self.root / "shared").mkdir()
        _, entry = self.shared_state("a/b")
        self.assertEqual(entry["state"], "unknown")

    def test_missing_shared_root_is_none_with_detail(self):
        _, entry = self.shared_state("kappa")
        self.assertEqual(entry["state"], "none")
        self.assertEqual(entry["detail"], "共享根目录不存在")

    def test_denied_and_unknown_never_collapse_to_none(self):
        # Priority: a permission or IO failure must not read as "no entry".
        (self.root / "shared").mkdir()
        with mock.patch.object(skills_view, "_lstat", side_effect=PermissionError("denied")):
            _, denied = self.shared_state("ghost")
        self.assertEqual(denied["state"], "denied")
        with mock.patch.object(skills_view, "_lstat", side_effect=OSError(errno.EIO, "io")):
            _, unknown = self.shared_state("ghost")
        self.assertEqual(unknown["state"], "unknown")

    def test_unrecorded_and_staging_items_carry_shared_entry(self):
        install = self.make_shared_dir("colgrep-search")
        staged = self.staging / "candidate"
        unit = staged / "roots" / "skill-0"
        unit.mkdir(parents=True)
        (unit / "SKILL.md").write_text("---\nname: staged-skill\n---\n\n", encoding="utf-8")
        (staged / "manifest.json").write_text(
            json.dumps({"status": "staged", "roots": [{"kind": "skill", "staging_path": "roots/skill-0", "live_path": str(install)}]}),
            encoding="utf-8",
        )
        (staged / "capability-gate.json").write_text(json.dumps({"backend": "skil-lock", "blocked": False}), encoding="utf-8")
        body = self.payload([], extra_unrecorded=[self.root / "shared"])
        by_name = {item["name"]: item for item in body["items"]}
        self.assertEqual(by_name["colgrep-search"]["shared_entry"]["state"], "dir")
        self.assertIn("shared_entry", by_name["staged-skill"])
        self.assertEqual(by_name["staged-skill"]["shared_entry"]["state"], "none")


class HarnessAttributionTests(unittest.TestCase):
    def setUp(self):
        skills_view._CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.inventory = self.root / "skills-inventory.json"
        self.tools = self.root / "tool-roots.json"
        self.staging = self.root / "staging"
        self.staging.mkdir()

    def tearDown(self):
        skills_view._CACHE.clear()
        self.tmp.cleanup()

    def write_tools(self, roots):
        self.tools.write_text(
            json.dumps(
                {
                    "tools": [
                        {"id": "codex", "label": "Codex"},
                        {"id": "cursor", "label": "Cursor"},
                        {"id": "hermes", "label": "Hermes"},
                        {"id": "project", "label": "仓库入口"},
                    ],
                    "roots": roots,
                }
            ),
            encoding="utf-8",
        )

    def payload(self, skills):
        self.inventory.write_text(json.dumps({"skills": skills}), encoding="utf-8")
        return build_payload(
            repo_root=self.repo,
            inventory_path=self.inventory,
            tool_roots_path=self.tools,
            staging_root=self.staging,
            lock_path=self.root / "missing.lock",
            changelog_dir=None,
            now=NOW,
            scan_roots=[],
        )

    def make_shared(self, name):
        path = self.root / "shared" / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(f"---\nname: {name}\n---\n\n", encoding="utf-8")
        return path

    def test_symlinked_entry_gives_direct_and_target_shared(self):
        self.write_tools(
            [
                {"path": str(self.root / "shared"), "tools": ["codex", "cursor"], "kind": "shared"},
                {"path": str(self.root / "hermes" / "skills"), "tools": ["hermes"], "kind": "direct"},
            ]
        )
        self.make_shared("archify")
        link = self.root / "hermes" / "skills" / "archify"
        link.parent.mkdir(parents=True)
        os.symlink(self.root / "shared" / "archify", link)
        body = self.payload([skill("archify", visible_paths=[str(link)])])
        item = body["items"][0]
        self.assertEqual(item["tools"]["hermes"], "direct")
        self.assertEqual(item["tools"]["codex"], "shared")
        self.assertEqual(item["tools"]["cursor"], "shared")
        entry = item["entries"][0]
        self.assertEqual(entry["tools"], ["hermes"])
        self.assertEqual(entry["location"], str(self.root / "hermes" / "skills"))
        self.assertTrue(entry["resolved"])
        evidence = {(record["harness"], record["kind"]): record for record in item["evidence"]}
        self.assertEqual(evidence[("hermes", "entry")]["attribution"], "direct")
        self.assertEqual(evidence[("hermes", "entry")]["state"], "present")
        self.assertEqual(evidence[("codex", "target")]["attribution"], "shared")
        self.assertEqual(evidence[("cursor", "target")]["attribution"], "shared")
        self.assertEqual(evidence[("codex", "target")]["root"], str(self.root / "shared"))
        self.assertTrue(evidence[("codex", "target")]["resolved"])
        self.assertEqual(item["shared_entry"]["state"], "dir")

    def test_broken_link_marks_missing_without_target_evidence(self):
        self.write_tools(
            [
                {"path": str(self.root / "shared"), "tools": ["codex", "cursor"], "kind": "shared"},
                {"path": str(self.root / "hermes" / "skills"), "tools": ["hermes"], "kind": "direct"},
            ]
        )
        link = self.root / "hermes" / "skills" / "broken"
        link.parent.mkdir(parents=True)
        os.symlink(self.root / "shared" / "broken", link)
        body = self.payload([skill("broken", visible_paths=[str(link)])])
        item = body["items"][0]
        self.assertEqual(item["tools"]["hermes"], "missing")
        self.assertIsNone(item["tools"]["codex"])
        self.assertIsNone(item["tools"]["cursor"])
        entry = item["entries"][0]
        self.assertEqual(entry["mark"], "missing")
        self.assertFalse([record for record in item["evidence"] if record["kind"] == "target"])
        self.assertIsNone(entry["resolved"])

    def test_one_skill_linked_from_two_direct_roots_is_direct_for_both(self):
        self.write_tools(
            [
                {"path": str(self.root / "shared"), "tools": ["cursor"], "kind": "shared"},
                {"path": str(self.root / "hermes" / "skills"), "tools": ["hermes"], "kind": "direct"},
                {"path": str(self.root / "codexroot" / "skills"), "tools": ["codex"], "kind": "direct"},
            ]
        )
        self.make_shared("dual")
        for parent in (self.root / "hermes" / "skills", self.root / "codexroot" / "skills"):
            parent.mkdir(parents=True)
            os.symlink(self.root / "shared" / "dual", parent / "dual")
        body = self.payload([skill("dual", visible_paths=[str(self.root / "hermes" / "skills" / "dual"), str(self.root / "codexroot" / "skills" / "dual")])])
        item = body["items"][0]
        self.assertEqual(item["tools"]["hermes"], "direct")
        self.assertEqual(item["tools"]["codex"], "direct")
        self.assertEqual(item["tools"]["cursor"], "shared")

    def test_root_under_symlinked_parent_still_matches_lexically(self):
        real_home = self.root / "realhome"
        (real_home / "hermes" / "skills").mkdir(parents=True)
        link_home = self.root / "linkhome"
        os.symlink(real_home, link_home)
        self.write_tools(
            [
                {"path": str(link_home / "hermes" / "skills"), "tools": ["hermes"], "kind": "direct"},
            ]
        )
        install = link_home / "hermes" / "skills" / "linked-root"
        install.mkdir()
        (install / "SKILL.md").write_text("---\nname: linked-root\n---\n\n", encoding="utf-8")
        body = self.payload([skill("linked-root", visible_paths=[str(install)])])
        item = body["items"][0]
        self.assertEqual(item["tools"]["hermes"], "direct")
        self.assertEqual(item["entries"][0]["location"], str(link_home / "hermes" / "skills"))
        self.assertEqual(item["shared_entry"]["state"], "unscanned")

    def test_repo_entries_carry_repo_id_and_stay_separate(self):
        self.write_tools(
            [
                {"path": str(self.root / "shared"), "tools": ["codex"], "kind": "shared"},
                {"path": ".agents/skills", "tools": ["project", "hermes"], "kind": "direct", "repo": True},
            ]
        )
        inside = self.repo / ".agents" / "skills" / "dup"
        inside.mkdir(parents=True)
        (inside / "SKILL.md").write_text("---\nname: dup\n---\n\n", encoding="utf-8")
        outside = self.root / "otherrepo" / ".agents" / "skills" / "dup"
        outside.mkdir(parents=True)
        (outside / "SKILL.md").write_text("---\nname: dup\n---\n\n", encoding="utf-8")
        body = self.payload([skill("dup", visible_paths=[".agents/skills/dup", str(outside)])])
        item = body["items"][0]
        repo_id = body["repos"][0]["id"]
        self.assertEqual(body["repos"][0]["label"], "repo")
        self.assertEqual(item["repo_ids"], [repo_id])
        by_path = {entry["path"]: entry for entry in item["entries"]}
        repo_entry = by_path[".agents/skills/dup"]
        self.assertEqual(repo_entry["repo_id"], repo_id)
        self.assertEqual(repo_entry["scope"], "repo_entry")
        self.assertEqual(repo_entry["location"], ".agents/skills")
        self.assertEqual(sorted(repo_entry["tools"]), ["hermes", "project"])
        other_entry = by_path[str(outside)]
        self.assertIsNone(other_entry["repo_id"])
        self.assertEqual(other_entry["scope"], "other")
        self.assertEqual(other_entry["tools"], [])
        self.assertIsNone(other_entry["mark"])
        self.assertEqual(item["tools"]["project"], "direct")

    def test_overlapping_sources_dedupe_to_one_entry(self):
        """visible_paths + adapters + canonical_path naming the same path ⇒ one entry."""
        self.write_tools(
            [
                {"path": str(self.root / "shared"), "tools": ["codex"], "kind": "shared"},
                {"path": ".agents/skills", "tools": ["project"], "kind": "direct", "repo": True},
            ]
        )
        inside = self.repo / ".agents" / "skills" / "dup-src"
        inside.mkdir(parents=True)
        (inside / "SKILL.md").write_text("---\nname: dup-src\n---\n\n", encoding="utf-8")
        authority = {
            "ownership": "repo",
            "source_authority": "repo",
            "update_authority": "manual",
            "canonical_path": ".agents/skills/dup-src",
        }
        body = self.payload(
            [
                skill(
                    "dup-src",
                    visible_paths=[".agents/skills/dup-src"],
                    adapters=[{"path": ".agents/skills/dup-src"}],
                    authority=authority,
                )
            ]
        )
        item = body["items"][0]
        self.assertEqual(len(item["entries"]), 1)
        keys = [(record["harness"], record["kind"], record["lexical"]) for record in item["evidence"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual({record["entry_index"] for record in item["evidence"]}, {0})
        single = self.payload([skill("dup-src", visible_paths=[".agents/skills/dup-src"], authority=authority)])
        other = single["items"][0]
        self.assertEqual(item["tools"], other["tools"])
        self.assertEqual(item["repo_ids"], other["repo_ids"])
        self.assertEqual(item["shared_entry"], other["shared_entry"])

    def test_tilde_and_absolute_spellings_dedupe(self):
        home = self.root / "home"
        install = home / "skills" / "tilde-skill"
        install.mkdir(parents=True)
        (install / "SKILL.md").write_text("---\nname: tilde-skill\n---\n\n", encoding="utf-8")
        self.write_tools([{"path": "~/skills", "tools": ["codex"], "kind": "direct"}])
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            body = self.payload([skill("tilde-skill", visible_paths=["~/skills/tilde-skill", str(install)])])
        item = body["items"][0]
        self.assertEqual(len(item["entries"]), 1)
        self.assertEqual(item["tools"]["codex"], "direct")

    def test_distinct_lexical_paths_stay_separate(self):
        self.write_tools(
            [
                {"path": str(self.root / "a"), "tools": ["codex"], "kind": "direct"},
                {"path": str(self.root / "b"), "tools": ["hermes"], "kind": "direct"},
            ]
        )
        for parent in (self.root / "a", self.root / "b"):
            (parent / "same-name").mkdir(parents=True)
            (parent / "same-name" / "SKILL.md").write_text("---\nname: same-name\n---\n\n", encoding="utf-8")
        body = self.payload([skill("same-name", visible_paths=[str(self.root / "a" / "same-name"), str(self.root / "b" / "same-name")])])
        item = body["items"][0]
        self.assertEqual(len(item["entries"]), 2)
        self.assertEqual(item["tools"]["codex"], "direct")
        self.assertEqual(item["tools"]["hermes"], "direct")

    def test_user_and_repo_paths_with_same_name_stay_separate(self):
        self.write_tools(
            [
                {"path": str(self.root / "userroot"), "tools": ["codex"], "kind": "direct"},
                {"path": ".agents/skills", "tools": ["project"], "kind": "direct", "repo": True},
            ]
        )
        user_dir = self.root / "userroot" / "dual"
        user_dir.mkdir(parents=True)
        repo_dir = self.repo / ".agents" / "skills" / "dual"
        repo_dir.mkdir(parents=True)
        body = self.payload([skill("dual", visible_paths=[str(user_dir), ".agents/skills/dual"])])
        item = body["items"][0]
        self.assertEqual(len(item["entries"]), 2)
        self.assertEqual({entry["scope"] for entry in item["entries"]}, {"user", "repo_entry"})

    def test_tool_marks_dedupes_repeated_raw_paths(self):
        self.write_tools([{"path": str(self.root / "uroot"), "tools": ["codex"], "kind": "direct"}])
        target = self.root / "uroot" / "one"
        target.mkdir(parents=True)
        roots, _ = skills_view._load_tool_roots(self.tools, self.repo)
        marks, entries, evidence, repo_ids = skills_view.tool_marks([str(target), str(target)], roots, self.repo)
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["entry_index"], 0)
        self.assertEqual(marks["codex"], "direct")


class SharedEntryStateMachineTests(unittest.TestCase):
    """Ordered-step probe: injected failures at each layer, real fixtures otherwise."""

    def setUp(self):
        skills_view._CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.shared_dir = self.root / "shared"
        self.shared_dir.mkdir()

    def tearDown(self):
        skills_view._CACHE.clear()
        for child in self.shared_dir.iterdir():
            try:
                child.chmod(0o755)
            except OSError:
                pass
        self.tmp.cleanup()

    def shared(self):
        return {"path": str(self.shared_dir), "expanded": str(self.shared_dir.resolve())}

    def probe(self, name, **patches):
        shared = self.shared()
        if not patches:
            return skills_view._shared_entry(name, shared)
        with mock.patch.multiple(skills_view, **patches):
            return skills_view._shared_entry(name, shared)

    def fail_at(self, fn_name, match, exc):
        real = getattr(skills_view, fn_name)

        def wrapper(path, *args):
            if match(str(path)):
                raise exc
            return real(path)

        return wrapper

    def assert_outcome(self, entry, state, reason):
        self.assertEqual(entry["state"], state, entry)
        self.assertEqual(entry["reason"], reason, entry)

    # -- (a) priority combinations -------------------------------------------------

    def test_no_shared_root_beats_invalid_name(self):
        entry = skills_view._shared_entry("a/b", None)
        self.assert_outcome(entry, "unscanned", "no_shared_root")
        self.assertIsNone(entry["errno"])
        self.assertEqual(entry["step"], "P0")

    def test_invalid_name_with_healthy_root_is_unknown(self):
        for bad in ("a/b", "", ".", "..", "nul\x00name", None, 5):
            entry = self.probe(bad)
            self.assert_outcome(entry, "unknown", "invalid_name")
            self.assertEqual(entry["step"], "P0")

    def test_root_denied_beats_entry_absent(self):
        entry = self.probe("ghost", _stat=self.fail_at("_stat", lambda p: True, PermissionError(errno.EACCES, "denied")))
        self.assert_outcome(entry, "denied", "root_denied")
        self.assertEqual(entry["step"], "S1")

    def test_broken_link_beats_missing_skill_md(self):
        os.symlink(self.root / "nowhere", self.shared_dir / "broken")
        entry = self.probe("broken")
        self.assert_outcome(entry, "broken_link", "target_absent")
        self.assertEqual(entry["step"], "S4")

    def test_denied_at_entry_target_and_skill_md(self):
        # entry lstat denied -> S2 entry_denied
        os.symlink(self.root / "nowhere", self.shared_dir / "s")
        entry = self.probe("s", _lstat=self.fail_at("_lstat", lambda p: p.endswith("/s"), PermissionError(errno.EACCES, "x")))
        self.assert_outcome(entry, "denied", "entry_denied")
        self.assertEqual(entry["step"], "S2")
        # target stat denied -> S4 target_denied
        target_dir = self.root / "t"
        target_dir.mkdir()
        link = self.shared_dir / "s2"
        os.symlink(target_dir, link)
        entry = self.probe("s2", _stat=self.fail_at("_stat", lambda p: p.endswith("/s2"), PermissionError(errno.EACCES, "x")))
        self.assert_outcome(entry, "denied", "target_denied")
        self.assertEqual(entry["step"], "S4")
        self.assertEqual(entry["target"], str(target_dir))
        # SKILL.md lstat denied -> S6 skill_md_denied
        (target_dir / "SKILL.md").write_text("x", encoding="utf-8")
        entry = self.probe("s2", _lstat=self.fail_at("_lstat", lambda p: p.endswith("SKILL.md"), PermissionError(errno.EACCES, "x")))
        self.assert_outcome(entry, "denied", "skill_md_denied")
        self.assertEqual(entry["step"], "S6")

    # -- (b) injected failures per layer ---------------------------------------------

    def test_root_stat_failures(self):
        cases = [
            (PermissionError(errno.EACCES, "x"), "denied", "root_denied"),
            (OSError(errno.ENOENT, "x"), "none", "root_absent"),
            (OSError(errno.ENOTDIR, "x"), "none", "root_absent"),
            (OSError(errno.ELOOP, "x"), "unknown", "root_loop"),
            (OSError(errno.EIO, "x"), "unknown", "root_error"),
        ]
        for exc, state, reason in cases:
            entry = self.probe("x", _stat=self.fail_at("_stat", lambda p: True, exc))
            self.assert_outcome(entry, state, reason)

    def test_entry_lstat_failures(self):
        cases = [
            (OSError(errno.ENOENT, "x"), "none", "entry_absent"),
            (PermissionError(errno.EACCES, "x"), "denied", "entry_denied"),
            (OSError(errno.ENOTDIR, "x"), "unknown", "entry_error"),
            (OSError(errno.ELOOP, "x"), "unknown", "entry_error"),
            (OSError(errno.EIO, "x"), "unknown", "entry_error"),
        ]
        for exc, state, reason in cases:
            entry = self.probe("x", _lstat=self.fail_at("_lstat", lambda p: p.endswith("/x"), exc))
            self.assert_outcome(entry, state, reason)

    def test_readlink_failures(self):
        target_dir = self.root / "t"
        target_dir.mkdir()
        (target_dir / "SKILL.md").write_text("x", encoding="utf-8")
        os.symlink(target_dir, self.shared_dir / "x")
        cases = [
            (PermissionError(errno.EACCES, "x"), "denied", "readlink_denied"),
            (OSError(errno.EIO, "x"), "unknown", "readlink_error"),
        ]
        for exc, state, reason in cases:
            entry = self.probe("x", _readlink=self.fail_at("_readlink", lambda p: p.endswith("/x"), exc))
            self.assert_outcome(entry, state, reason)

    def test_target_stat_failures(self):
        target_dir = self.root / "t"
        target_dir.mkdir()
        (target_dir / "SKILL.md").write_text("x", encoding="utf-8")
        os.symlink(target_dir, self.shared_dir / "x")
        cases = [
            (PermissionError(errno.EACCES, "x"), "denied", "target_denied"),
            (OSError(errno.ENOENT, "x"), "broken_link", "target_absent"),
            (OSError(errno.ELOOP, "x"), "broken_link", "link_loop"),
            (OSError(errno.ENOTDIR, "x"), "broken_link", "target_enotdir"),
            (OSError(errno.EIO, "x"), "unknown", "target_error"),
        ]
        for exc, state, reason in cases:
            entry = self.probe("x", _stat=self.fail_at("_stat", lambda p: p.endswith("/x"), exc))
            self.assert_outcome(entry, state, reason)

    def test_realpath_failures(self):
        entry_dir = self.shared_dir / "x"
        entry_dir.mkdir()
        (entry_dir / "SKILL.md").write_text("x", encoding="utf-8")
        cases = [
            (PermissionError(errno.EACCES, "x"), "denied", "realpath_denied"),
            (OSError(errno.EIO, "x"), "unknown", "realpath_error"),
        ]
        for exc, state, reason in cases:
            entry = self.probe("x", _realpath=self.fail_at("_realpath", lambda p: p.endswith("/x"), exc))
            self.assert_outcome(entry, state, reason)

    def test_skill_md_failures(self):
        entry_dir = self.shared_dir / "x"
        entry_dir.mkdir()
        (entry_dir / "SKILL.md").write_text("x", encoding="utf-8")
        cases = [
            ("_lstat", PermissionError(errno.EACCES, "x"), "denied", "skill_md_denied"),
            ("_lstat", OSError(errno.ENOENT, "x"), "content_missing", "skill_md_absent"),
            ("_lstat", OSError(errno.EIO, "x"), "unknown", "skill_md_error"),
            ("_lstat", OSError(errno.ELOOP, "x"), "content_missing", "skill_md_dangling"),
            ("_read_head", PermissionError(errno.EACCES, "x"), "denied", "skill_md_unreadable"),
            ("_read_head", OSError(errno.EIO, "x"), "unknown", "skill_md_read_error"),
        ]
        for fn, exc, state, reason in cases:
            entry = self.probe("x", **{fn: self.fail_at(fn, lambda p: p.endswith("SKILL.md"), exc)})
            self.assert_outcome(entry, state, reason)
        # SKILL.md as a dangling symlink: _stat follow fails.
        (entry_dir / "SKILL.md").unlink()
        os.symlink(entry_dir / "gone", entry_dir / "SKILL.md")
        entry = self.probe("x")
        self.assert_outcome(entry, "content_missing", "skill_md_dangling")
        # SKILL.md as a directory.
        (entry_dir / "SKILL.md").unlink()
        (entry_dir / "SKILL.md").mkdir()
        entry = self.probe("x")
        self.assert_outcome(entry, "content_missing", "skill_md_not_file")

    # -- (c) real fixtures -------------------------------------------------------------

    def test_self_loop_symlink_is_broken_link(self):
        os.symlink(self.shared_dir / "loop", self.shared_dir / "loop")
        entry = self.probe("loop")
        self.assert_outcome(entry, "broken_link", "link_loop")
        self.assertEqual(entry["link_kind"], "absolute")

    def test_shared_root_under_a_regular_file_is_absent(self):
        blocker = self.root / "blocker"
        blocker.write_text("file", encoding="utf-8")
        shared = {"path": str(blocker / "sub"), "expanded": str(blocker / "sub")}
        entry = skills_view._shared_entry("x", shared)
        self.assert_outcome(entry, "none", "root_absent")

    def test_relative_link_inside_root(self):
        real = self.shared_dir / "real"
        real.mkdir()
        (real / "SKILL.md").write_text("x", encoding="utf-8")
        os.symlink("real", self.shared_dir / "rel")
        entry = self.probe("rel")
        self.assert_outcome(entry, "link", "ok")
        self.assertEqual(entry["link_kind"], "relative")
        self.assertEqual(entry["target_scope"], "inside_shared_root")
        self.assertEqual(entry["target"], "real")

    def test_relative_link_outside_root(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "SKILL.md").write_text("x", encoding="utf-8")
        os.symlink("../outside", self.shared_dir / "out")
        entry = self.probe("out")
        self.assert_outcome(entry, "link", "ok")
        self.assertEqual(entry["link_kind"], "relative")
        self.assertEqual(entry["target_scope"], "outside_shared_root")

    def test_relative_dangling_link(self):
        os.symlink("../gone", self.shared_dir / "out")
        entry = self.probe("out")
        self.assert_outcome(entry, "broken_link", "target_absent")
        self.assertEqual(entry["link_kind"], "relative")

    def test_absolute_link_outside_root(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "SKILL.md").write_text("x", encoding="utf-8")
        os.symlink(outside, self.shared_dir / "abs")
        entry = self.probe("abs")
        self.assert_outcome(entry, "link", "ok")
        self.assertEqual(entry["link_kind"], "absolute")
        self.assertEqual(entry["target_scope"], "outside_shared_root")

    def test_empty_skill_md_is_content_missing(self):
        entry_dir = self.shared_dir / "empty"
        entry_dir.mkdir()
        (entry_dir / "SKILL.md").write_text("", encoding="utf-8")
        entry = self.probe("empty")
        self.assert_outcome(entry, "content_missing", "skill_md_empty")

    def test_chmod_000_entry_dir_is_denied(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root ignores permission bits")
        entry_dir = self.shared_dir / "closed"
        entry_dir.mkdir()
        (entry_dir / "SKILL.md").write_text("x", encoding="utf-8")
        entry_dir.chmod(0o000)
        entry = self.probe("closed")
        self.assert_outcome(entry, "denied", "skill_md_denied")

    def test_probe_exception_is_unknown(self):
        entry = self.probe("x", _lstat=self.fail_at("_lstat", lambda p: True, RuntimeError("boom")))
        self.assert_outcome(entry, "unknown", "probe_exception")


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        skills_view._CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.inventory = self.root / "skills-inventory.json"
        self.tools = self.root / "tool-roots.json"
        self.staging = self.root / "staging"
        self.staging.mkdir()
        self.shared = self.root / "shared"

    def tearDown(self):
        skills_view._CACHE.clear()
        for path in self.root.rglob("*"):
            try:
                path.chmod(0o755)
            except OSError:
                pass
        self.tmp.cleanup()

    def write_tools(self, roots):
        self.tools.write_text(
            json.dumps(
                {
                    "tools": [
                        {"id": "codex", "label": "Codex"},
                        {"id": "cursor", "label": "Cursor"},
                        {"id": "hermes", "label": "Hermes"},
                        {"id": "claude", "label": "Claude"},
                        {"id": "project", "label": "仓库入口"},
                    ],
                    "roots": roots,
                }
            ),
            encoding="utf-8",
        )

    def payload(self, skills):
        self.inventory.write_text(json.dumps({"skills": skills}), encoding="utf-8")
        return build_payload(
            repo_root=self.repo,
            inventory_path=self.inventory,
            tool_roots_path=self.tools,
            staging_root=self.staging,
            lock_path=self.root / "missing.lock",
            changelog_dir=None,
            now=NOW,
            scan_roots=[],
        )

    def roots(self):
        return [
            {"path": str(self.shared), "tools": ["codex", "cursor"], "kind": "shared"},
            {"path": str(self.root / "hermes" / "skills"), "tools": ["hermes"], "kind": "direct"},
            {"path": str(self.root / "claude" / "skills"), "tools": ["claude"], "kind": "direct"},
        ]

    def test_same_target_from_two_harnesses_keeps_all_records(self):
        self.write_tools(self.roots())
        install = self.shared / "multi"
        install.mkdir(parents=True)
        (install / "SKILL.md").write_text("x", encoding="utf-8")
        paths = []
        for harness in ("hermes", "claude"):
            link = self.root / harness / "skills" / "multi"
            link.parent.mkdir(parents=True)
            os.symlink(install, link)
            paths.append(str(link))
        body = self.payload([skill("multi", visible_paths=paths)])
        item = body["items"][0]
        records = {(r["harness"], r["kind"]) for r in item["evidence"]}
        self.assertEqual(records, {("hermes", "entry"), ("claude", "entry"), ("codex", "target"), ("cursor", "target"), ("codex", "entry"), ("cursor", "entry")})
        self.assertEqual(item["tools"]["hermes"], "direct")
        self.assertEqual(item["tools"]["claude"], "direct")
        self.assertEqual(item["tools"]["codex"], "shared")
        by_harness = {}
        for record in item["evidence"]:
            by_harness.setdefault(record["harness"], []).append(record)
        # Discovery also retains the shared directory's own lexical entry.
        self.assertEqual(len(by_harness["codex"]), 3)
        self.assertEqual(len(by_harness["cursor"]), 3)

    def test_same_basename_different_targets_are_both_kept(self):
        self.write_tools(self.roots())
        for suffix in ("t1", "t2"):
            target = self.root / suffix / "dup"
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("x", encoding="utf-8")
        link_a = self.root / "hermes" / "skills" / "dup"
        link_a.parent.mkdir(parents=True)
        os.symlink(self.root / "t1" / "dup", link_a)
        link_b = self.root / "claude" / "skills" / "dup"
        link_b.parent.mkdir(parents=True)
        os.symlink(self.root / "t2" / "dup", link_b)
        body = self.payload([skill("dup", visible_paths=[str(link_a), str(link_b)])])
        item = body["items"][0]
        resolved = {entry["resolved"] for entry in item["entries"]}
        self.assertEqual(len(resolved), 2)
        self.assertTrue(any(r.endswith("t1/dup") for r in resolved))
        self.assertTrue(any(r.endswith("t2/dup") for r in resolved))

    def test_denied_entry_never_produces_missing(self):
        self.write_tools(self.roots())
        target = self.shared / "shut"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("x", encoding="utf-8")
        link = self.root / "hermes" / "skills" / "shut"
        link.parent.mkdir(parents=True)
        os.symlink(target, link)

        real_stat = skills_view._stat

        def deny_link(path):
            if str(path) == str(link):
                raise PermissionError(errno.EACCES, "denied")
            return real_stat(path)

        with mock.patch.object(skills_view, "_stat", side_effect=deny_link):
            body = self.payload([skill("shut", visible_paths=[str(link)])])
        item = body["items"][0]
        self.assertEqual(item["tools"]["hermes"], "unknown")
        self.assertNotEqual(item["tools"]["hermes"], "missing")
        entry_evidence = [r for r in item["evidence"] if r["harness"] == "hermes"]
        self.assertEqual(entry_evidence[0]["attribution"], "unknown")
        self.assertEqual(entry_evidence[0]["state"], "denied")
        self.assertFalse(item["entries"][0]["present"])
        self.assertIsNone(item["entries"][0]["resolved"])

    def test_evidence_order_is_stable_under_input_permutation(self):
        self.write_tools(self.roots())
        install = self.shared / "stable"
        install.mkdir(parents=True)
        (install / "SKILL.md").write_text("x", encoding="utf-8")
        paths = []
        for harness in ("hermes", "claude"):
            link = self.root / harness / "skills" / "stable"
            link.parent.mkdir(parents=True)
            os.symlink(install, link)
            paths.append(str(link))
        first = self.payload([skill("stable", visible_paths=paths)])["items"][0]["evidence"]
        second = self.payload([skill("stable", visible_paths=list(reversed(paths)))])["items"][0]["evidence"]
        strip = lambda records: [{k: v for k, v in r.items() if k != "entry_index"} for r in records]
        self.assertEqual(strip(first), strip(second))
        # Sorted by payload tool order: codex, cursor, hermes, claude (as written in write_tools).
        harness_order = [r["harness"] for r in first]
        self.assertEqual(harness_order, sorted(harness_order, key=lambda h: ["codex", "cursor", "hermes", "claude"].index(h)))
        self.assertEqual(first[0]["harness"], "codex")
        self.assertEqual(first[0]["kind"], "entry")

    def test_missing_entry_marks_missing(self):
        self.write_tools(self.roots())
        gone = self.root / "hermes" / "skills" / "gone"
        gone.parent.mkdir(parents=True)
        body = self.payload([skill("gone", visible_paths=[str(gone)])])
        item = body["items"][0]
        self.assertEqual(item["tools"]["hermes"], "missing")
        record = next(r for r in item["evidence"] if r["harness"] == "hermes")
        self.assertEqual(record["attribution"], "missing")
        self.assertEqual(record["state"], "absent")

    def test_grok_symlink_is_direct_when_inventory_omits_the_path(self):
        grok = self.root / ".grok" / "skills"
        grok.mkdir(parents=True)
        target = self.root / "real" / "computer-use"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("---\ndescription: use the computer\n---\n", encoding="utf-8")
        os.symlink(target, grok / "computer-use")
        self.tools.write_text(
            json.dumps(
                {
                    "tools": [{"id": "grok", "label": "Grok CLI"}],
                    "roots": [{"path": str(grok), "tools": ["grok"], "kind": "direct"}],
                }
            ),
            encoding="utf-8",
        )
        body = self.payload([skill("computer-use", visible_paths=[])])
        item = body["items"][0]
        self.assertEqual(item["tools"]["grok"], "direct")
        record = next(row for row in item["evidence"] if row["harness"] == "grok")
        self.assertEqual(record["kind"], "entry")
        self.assertTrue(record["readlink"])

    def test_local_source_uses_repo_skills_directory(self):
        folder = self.repo / "skills" / "colgrep-search"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            "---\nname: colgrep-search\ndescription: find code by meaning\n---\n# Colgrep\n",
            encoding="utf-8",
        )
        self.write_tools(self.roots())
        body = self.payload([
            skill(
                "colgrep-search",
                source_class="local_custom",
                authority={
                    "ownership": "local",
                    "source_authority": "canonical_git",
                    "update_authority": "repository_maintainer",
                    "canonical_path": "skills/colgrep-search",
                },
            )
        ])
        local = body["items"][0]["local_source"]
        self.assertEqual(local["path"], "skills/colgrep-search/SKILL.md")
        self.assertEqual(local["summary"], "find code by meaning")
        self.assertIn("T", local["updated_at"])


class RepoIdentityTests(unittest.TestCase):
    def setUp(self):
        skills_view._CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.inventory = self.root / "skills-inventory.json"
        self.tools = self.root / "tool-roots.json"
        self.staging = self.root / "staging"
        self.staging.mkdir()
        self.tools.write_text(
            json.dumps(
                {
                    "tools": [{"id": "project", "label": "仓库入口"}],
                    "roots": [{"path": ".agents/skills", "tools": ["project"], "kind": "direct", "repo": True}],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        skills_view._CACHE.clear()
        self.tmp.cleanup()

    def payload(self, skills, repo_root=None):
        self.inventory.write_text(json.dumps({"skills": skills}), encoding="utf-8")
        return build_payload(
            repo_root=repo_root or self.repo,
            inventory_path=self.inventory,
            tool_roots_path=self.tools,
            staging_root=self.staging,
            lock_path=self.root / "missing.lock",
            changelog_dir=None,
            now=NOW,
            scan_roots=[],
        )

    def test_repos_record_shape_and_ok_state(self):
        body = self.payload([skill("x")])
        repo = body["repos"][0]
        self.assertEqual(repo["id"], str(self.repo.resolve()))
        self.assertEqual(repo["label"], "repo")
        self.assertEqual(repo["identity_state"], "ok")
        self.assertIsNone(repo["reason"])

    def test_alias_symlink_collapses_to_same_id(self):
        alias = self.root / "alias"
        os.symlink(self.repo, alias)
        real = self.payload([skill("x")], repo_root=self.repo)
        via_alias = self.payload([skill("x")], repo_root=alias)
        self.assertEqual(real["repos"][0]["id"], via_alias["repos"][0]["id"])

    def test_absent_repo_root_is_unknown_identity(self):
        missing = self.root / "no-such-repo"
        body = self.payload([skill("x", visible_paths=[".agents/skills/x"])], repo_root=missing)
        repo = body["repos"][0]
        self.assertEqual(repo["id"], "unknown")
        self.assertEqual(repo["label"], "未知仓库")
        self.assertEqual(repo["identity_state"], "unknown")
        self.assertEqual(repo["reason"], "repo_root_absent")
        entry = body["items"][0]["entries"][0]
        self.assertEqual(entry["repo_id"], "unknown")
        self.assertEqual(entry["scope"], "repo_entry")
        self.assertEqual(body["items"][0]["repo_ids"], ["unknown"])

    def test_denied_repo_root_realpath_is_unknown_identity(self):
        def deny(path):
            raise PermissionError(errno.EACCES, "denied")

        with mock.patch.object(skills_view, "_realpath", side_effect=deny):
            body = self.payload([skill("x", visible_paths=[".agents/skills/x"])])
        repo = body["repos"][0]
        self.assertEqual(repo["identity_state"], "unknown")
        self.assertEqual(repo["reason"], "repo_root_denied")
        self.assertEqual(repo["id"], "unknown")

    def test_repo_source_scope_for_unrooted_path_inside_repo(self):
        (self.repo / "skills" / "src-skill").mkdir(parents=True)
        (self.repo / "skills" / "src-skill" / "SKILL.md").write_text("x", encoding="utf-8")
        body = self.payload([skill("src-skill", visible_paths=["skills/src-skill"])])
        entry = body["items"][0]["entries"][0]
        self.assertEqual(entry["scope"], "repo_source")
        self.assertIsNone(entry["location"])
        self.assertEqual(entry["repo_id"], body["repos"][0]["id"])
        self.assertEqual(entry["tools"], [])
        self.assertEqual(body["items"][0]["repo_ids"], [body["repos"][0]["id"]])

    def test_skills_schema_two_on_ok_and_invalid_payloads(self):
        body = self.payload([skill("x")])
        self.assertEqual(body["skills_schema"], 2)
        self.inventory.write_text("{", encoding="utf-8")
        bad = build_payload(
            repo_root=self.repo,
            inventory_path=self.inventory,
            tool_roots_path=self.tools,
            staging_root=self.staging,
            lock_path=self.root / "missing.lock",
            changelog_dir=None,
            now=NOW,
            scan_roots=[],
        )
        self.assertEqual(bad["skills_schema"], 2)
        self.assertEqual(bad["shared_root"]["state"], "unscanned")
        self.assertEqual(bad["repos"], [])


class CacheDependencyTests(unittest.TestCase):
    def setUp(self):
        skills_view._CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.inventory = self.root / "skills-inventory.json"
        self.tools = self.root / "tool-roots.json"
        self.staging = self.root / "staging"
        self.staging.mkdir()
        self.shared = self.root / "shared"
        self.shared.mkdir()
        self.tools.write_text(
            json.dumps(
                {
                    "tools": [{"id": "codex", "label": "Codex"}],
                    "roots": [{"path": str(self.shared), "tools": ["codex"], "kind": "shared"}],
                }
            ),
            encoding="utf-8",
        )
        self.clock = {"at": NOW}

    def tearDown(self):
        skills_view._CACHE.clear()
        for path in self.root.rglob("*"):
            try:
                path.chmod(0o755)
            except OSError:
                pass
        self.tmp.cleanup()

    def cached(self):
        return skills_document(
            repo_root=self.repo,
            inventory_path=self.inventory,
            tool_roots_path=self.tools,
            staging_root=self.staging,
            lock_path=self.root / "missing.lock",
            changelog_dir=None,
            scan_roots=[],
            clock=lambda: self.clock["at"],
        )

    def make_skill(self, name="alpha", symlink_to=None):
        self.inventory.write_text(json.dumps({"skills": [skill(name)]}), encoding="utf-8")
        if symlink_to:
            os.symlink(symlink_to, self.shared / name)
        else:
            (self.shared / name).mkdir()
            (self.shared / name / "SKILL.md").write_text("x", encoding="utf-8")

    def test_shared_skill_md_chmod_invalidates(self):
        self.make_skill()
        first = self.cached()
        self.assertIs(self.cached(), first)
        (self.shared / "alpha" / "SKILL.md").chmod(0o600)
        self.assertIsNot(self.cached(), first)

    def test_symlinked_target_skill_md_delete_invalidates(self):
        target = self.root / "target-alpha"
        target.mkdir()
        (target / "SKILL.md").write_text("x", encoding="utf-8")
        self.make_skill(symlink_to=target)
        first = self.cached()
        (target / "SKILL.md").unlink()
        self.assertIsNot(self.cached(), first)

    def test_retargeting_entry_symlink_invalidates(self):
        target_a = self.root / "ta"
        target_b = self.root / "tb"
        for target in (target_a, target_b):
            target.mkdir()
            (target / "SKILL.md").write_text("x", encoding="utf-8")
        self.make_skill(symlink_to=target_a)
        first = self.cached()
        (self.shared / "alpha").unlink()
        os.symlink(target_b, self.shared / "alpha")
        self.assertIsNot(self.cached(), first)

    def test_removing_shared_root_invalidates(self):
        self.make_skill()
        first = self.cached()
        import shutil as sh

        sh.rmtree(self.shared)
        self.assertIsNot(self.cached(), first)

    def test_editing_tool_roots_invalidates(self):
        self.make_skill()
        first = self.cached()
        self.tools.write_text(self.tools.read_text(encoding="utf-8") + " ", encoding="utf-8")
        self.assertIsNot(self.cached(), first)

    def test_repo_root_mode_change_invalidates(self):
        self.make_skill()
        first = self.cached()
        self.repo.chmod(0o700)
        self.assertIsNot(self.cached(), first)


if __name__ == "__main__":
    unittest.main()
