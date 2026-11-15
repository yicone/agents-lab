import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from collect_support import BOT, OTHER, NOW, TOOL, TOKEN, fixture, sidecar
from boxsync.sidecar import MAX_SIDECAR_BYTES, parse
from boxsync.snapshot import collect


class SidecarTests(unittest.TestCase):
    def encoded(self, value):
        return json.dumps(value).encode()

    def test_accepted_stale_and_boundary(self):
        value = sidecar()
        self.assertEqual(parse(self.encoded(value), NOW)[2], "ok")
        value["captured_at"] = (NOW - timedelta(seconds=900)).isoformat()
        self.assertEqual(parse(self.encoded(value), NOW)[2], "ok")
        value["captured_at"] = (NOW + timedelta(seconds=901)).isoformat()
        self.assertEqual(parse(self.encoded(value), NOW)[2], "stale_sidecar")

    def test_rejections_and_no_raw_echo(self):
        cases = []
        for key, value in (("url", "https://private.invalid"), ("prompt", "Private prompt"), ("unknown", True)):
            item = sidecar()
            item["routines"][0][key] = value
            cases.append((item, "rejected_schema"))
        item = sidecar()
        item["routines"][0]["name"] = "https://private.invalid"
        cases.append((item, "rejected_schema"))
        item = sidecar()
        item["mcp_servers"][0]["name"] = TOKEN
        cases.append((item, "rejected_secret"))
        item = sidecar()
        item["mcp_servers"][0]["tool_count"] = True
        cases.append((item, "rejected_schema"))
        item = sidecar()
        item["captured_at"] = "2026-99-99T00:00:00Z"
        cases.append((item, "rejected_schema"))
        for value, state in cases:
            with self.subTest(state=state), tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
                ctx, _ = fixture(tmp)
                result, consumed = collect(ctx, [("input", self.encoded(value))], TOOL)
                self.assertEqual(result["sources"]["sidecar"], state)
                self.assertEqual(consumed, [])
                for domain, owner_key in (("mcps", "bot_id"), ("routines", "owner_id")):
                    bot_rows = [r for r in result["domains"][domain] if r.get(owner_key) == BOT and
                                r.get("kind") in ("bot_turn", "turn")]
                    self.assertEqual(len(bot_rows), 1)
                    self.assertEqual(bot_rows[0]["evidence_level"], "not_collected")
                self.assertNotIn(TOKEN, json.dumps(result))

    def test_missing_other_bot_and_historical_sidecar(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            ctx, _ = fixture(tmp)
            value = sidecar()
            value["captured_at"] = (NOW - timedelta(hours=1)).isoformat()
            result, consumed = collect(ctx, [("input", self.encoded(value))], TOOL)
            self.assertEqual(result["sources"]["sidecar"], "stale_sidecar")
            self.assertEqual(consumed, ["input"])
            rows = [r for r in result["domains"]["mcps"] if r.get("kind") == "bot_turn"]
            self.assertEqual({r["bot_id"]: r["evidence_level"] for r in rows},
                             {BOT: "historical", OTHER: "not_collected"})

    def test_duplicate_bot_rejected_as_a_whole(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            ctx, _ = fixture(tmp)
            data = self.encoded(sidecar())
            result, consumed = collect(ctx, [("a", data), ("b", data)], TOOL)
            self.assertEqual(result["sources"]["sidecar"], "rejected_schema")
            self.assertEqual(consumed, [])

    def test_limits_duplicate_json_keys_and_malformed(self):
        for data in (b"x" * (MAX_SIDECAR_BYTES + 1), b"{}", b"[]", b"\xff",
                     b'{"bot_id":"first","bot_id":"second"}', b'{"tool_count":NaN}'):
            self.assertEqual(parse(data, NOW)[2], "rejected_schema")
