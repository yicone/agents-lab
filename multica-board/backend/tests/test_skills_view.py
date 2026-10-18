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
        self.assertEqual(sorted(entry["target_tools"]), ["codex", "cursor"])
        self.assertEqual(entry["target_mark"], "shared")
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
        self.assertEqual(entry["target_tools"], [])
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
        self.assertEqual(item["repo_id"], repo_id)
        by_path = {entry["path"]: entry for entry in item["entries"]}
        repo_entry = by_path[".agents/skills/dup"]
        self.assertEqual(repo_entry["repo_id"], repo_id)
        self.assertEqual(repo_entry["location"], ".agents/skills")
        self.assertEqual(sorted(repo_entry["tools"]), ["hermes", "project"])
        other_entry = by_path[str(outside)]
        self.assertIsNone(other_entry["repo_id"])
        self.assertEqual(other_entry["tools"], [])
        self.assertIsNone(other_entry["mark"])
        self.assertEqual(item["tools"]["project"], "direct")


if __name__ == "__main__":
    unittest.main()
