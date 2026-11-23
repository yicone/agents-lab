import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collect_support import (BOT, OTHER, NOW, TOOL, TOKEN, CONFIG_VALUE, SKILL_BODY, PROMPT,
                             fixture, sidecar, write)
from boxsync.snapshot import collect, validate_snapshot
from boxsync.hashing import instructions_sha256, tree_sha256
from boxsync.roots import BOX_ROOTS


class DomainTests(unittest.TestCase):
    def test_complete_fixture_schema_and_no_content_leak(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            ctx, home = fixture(tmp)
            bodies = [p.read_text() for p in home.rglob("*") if p.is_file()]
            result, _ = collect(ctx, [("sidecar", json.dumps(sidecar()).encode())], TOOL)
            text = validate_snapshot(result)
            for body in bodies + [PROMPT, TOKEN, CONFIG_VALUE]:
                self.assertNotIn(body, text)
            self.assertEqual(result["sources"], {"box_disk": "ok", "multica_cli": "not_collected", "sidecar": "ok"})
            for name in ("profiles", "workflows", "managed_skills", "plugin_skills", "mcps", "memory", "routines", "attachments"):
                self.assertTrue(result["domains"][name], name)
            flow = result["domains"]["workflows"][0]
            self.assertEqual(flow["body_sha256"], hashlib.sha256(SKILL_BODY.encode()).hexdigest())
            self.assertEqual(flow["tree_sha256"], tree_sha256(ctx.roots["workflows"] / "example")["tree_sha256"])
            self.assertEqual(result["domains"]["hooks"]["scope"], "external_host")
            self.assertEqual(result["domains"]["hooks"]["state"], "not_collected_on_box")
            self.assertEqual(result["domain_states"]["hooks"], "not_collected")
            try:
                import jsonschema
            except ImportError:
                self.skipTest("jsonschema is not installed; builtin validator passed")
            from boxsync.validation import load_schema
            jsonschema.Draft202012Validator(load_schema("snapshot"), format_checker=jsonschema.FormatChecker()).validate(result)

    def test_memory_opens_no_regular_files_and_blacklist_zero_access(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            ctx, _ = fixture(tmp)
            real_open, real_stat = os.open, os.stat
            memory_file_opens = []
            forbidden = []
            def open_spy(path, flags, *args, **kwargs):
                if str(path).endswith("memo.md"):
                    memory_file_opens.append(path)
                if Path(str(path)).name in ("gateway.json", "teach-queue-key.json", ".env", "auth-token"):
                    forbidden.append(path)
                return real_open(path, flags, *args, **kwargs)
            def stat_spy(path, *args, **kwargs):
                if not isinstance(path, int) and Path(str(path)).name in ("gateway.json", "teach-queue-key.json"):
                    forbidden.append(path)
                return real_stat(path, *args, **kwargs)
            with patch("os.open", open_spy), patch("os.stat", stat_spy):
                result, _ = collect(ctx, tool=TOOL)
            self.assertEqual(memory_file_opens, [])
            self.assertEqual(forbidden, [])
            memory = result["domains"]["memory"]
            self.assertTrue(all(row["evidence_level"] == "historical" for row in memory))
            self.assertTrue(all(row["file_count"] == 1 for row in memory))

    def test_unregistered_production_roots_unchanged(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            ctx, _ = fixture(tmp)
            for name in ("memory", "routines", "attachments"):
                ctx.roots[name] = None
            result, _ = collect(ctx, tool=TOOL)
            self.assertEqual(result["sources"]["box_disk"], "partial")
            for name in ("memory", "routines", "attachments", "hooks"):
                self.assertEqual(result["domain_states"][name], "not_collected", name)
            self.assertEqual(result["domains"]["hooks"]["state"], "not_collected_on_box")
            self.assertTrue(result["domains"]["memory"])
            self.assertTrue(all(r["file_count"] is None for r in result["domains"]["memory"]))
            self.assertTrue(result["domains"]["routines"])
            self.assertTrue(all(r["evidence_level"] == "not_collected" for r in result["domains"]["routines"]))
            self.assertEqual(result["domains"]["attachments"], [])
            self.assertEqual({k for k, v in BOX_ROOTS.items() if v is None}, {"mcps", "memory", "routines", "attachments"})

    def test_duplicate_names_numeric_directory_and_invalid_name(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            ctx, _ = fixture(tmp)
            write(ctx.roots["workflows"] / "skill-1234567890123/SKILL.md", SKILL_BODY)
            write(ctx.roots["workflows"] / "invalid dir/SKILL.md", SKILL_BODY)
            result, _ = collect(ctx, tool=TOOL)
            flows = result["domains"]["workflows"]
            self.assertEqual(len(flows), 2)
            self.assertTrue(all("duplicate_name" in f["flags"] for f in flows))
            self.assertEqual(flows[1]["kind"], "numeric")
            self.assertEqual(result["sources"]["box_disk"], "partial")

    def test_nonregular_workflow_and_oversize_attachment(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            ctx, _ = fixture(tmp)
            (ctx.roots["workflows"] / "example/link").symlink_to("SKILL.md")
            with (ctx.roots["attachments"] / BOT / "large.bin").open("wb") as stream:
                stream.truncate(64 * 1024 * 1024 + 1)
            result, _ = collect(ctx, tool=TOOL)
            self.assertEqual(result["domains"]["workflows"][0]["tree_state"], "unsupported_entry")
            large = next(row for row in result["domains"]["attachments"] if row["path"].endswith("large.bin"))
            self.assertIsNone(large["sha256"])
            self.assertEqual(large["hash_state"], "too_large")

    def test_malformed_caches_are_not_reported_as_empty_success(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            ctx, _ = fixture(tmp)
            write(ctx.roots["plugin_skills"] / "cache.json", {"unknown": "unsupported-cache-value"})
            write(ctx.roots["managed_skills"] / "cache.json", {"skills": ["unsupported-record"]})
            result, _ = collect(ctx, tool=TOOL)
            self.assertEqual(result["sources"]["box_disk"], "partial")
            self.assertEqual(result["domains"]["plugin_skills"], [])
            self.assertEqual(result["domains"]["managed_skills"][0]["cache_state"], "cache_unreadable")
