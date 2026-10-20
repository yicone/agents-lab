"""Grok Bot roster reads only the allow-listed slices and bridge files."""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multica_board.grok_bots import GrokPaths, decode_blob_name, is_allowed_slice, load_bots  # noqa: E402

AUTH = "google-oauth2|user_ABCDEFGH12345678"
OTHER = "google-oauth2|user_OTHERACCOUNT9999"


def blob_name(key: str) -> str:
    return base64.b32encode(key.encode()).decode().lower().rstrip("=") + ".blob"


def write_blob(directory: Path, key: str, value) -> Path:
    path = directory / blob_name(key)
    path.write_text(json.dumps({"schemaVersion": 1, "value": value}), encoding="utf-8")
    return path


class AllowListTests(unittest.TestCase):
    def test_decode_roundtrip_and_forbidden_keys(self):
        key = "sand.client.slice.account." + AUTH.replace("|", "%7C") + ".roster.last-roster"
        self.assertEqual(decode_blob_name(blob_name(key)), key)
        self.assertTrue(is_allowed_slice(key))
        self.assertTrue(is_allowed_slice("sand.client.slice.client-meta.account-slot"))
        for banned in (
            "sand.client.slice.account." + AUTH.replace("|", "%7C") + ".transcript.replicas.18fc579e",
            "sand.client.slice.account.x.composer-drafts",
            "sand.client.slice.account.x.credentials",
            "sand.client.slice.ui-layout",
            "Cookies",
            "sand-secrets.json",
        ):
            self.assertFalse(is_allowed_slice(banned), banned)

    def test_missing_directory_does_not_open_files(self):
        opened: list[Path] = []

        def reader(path: Path) -> str:
            opened.append(path)
            raise AssertionError(path)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = load_bots(
                GrokPaths(root / "missing", root / "routes.json", root / "outbox.json"),
                reader=reader,
            )
        self.assertEqual(body, {"ok": False, "reason": "absent", "items": []})
        self.assertEqual(opened, [])

    def test_reader_never_sees_transcript_or_secret_files(self):
        opened: list[str] = []

        def reader(path: Path) -> str:
            opened.append(path.name)
            if path.name in {"sand-secrets.json", "Cookies"} or "transcript" in path.name or "credential" in path.name:
                raise AssertionError(path.name)
            return path.read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "sand-client-persistence"
            cache.mkdir()
            auth_key = AUTH.replace("|", "%7C")
            other_key = OTHER.replace("|", "%7C")
            write_blob(cache, "sand.client.slice.client-meta.account-slot", AUTH)
            write_blob(
                cache,
                f"sand.client.slice.account.{auth_key}.roster.last-roster",
                {"rows": [{
                    "id": "bot-1",
                    "name": "Agent Steward",
                    "description": "uses sk-abcdefghijk",
                    "title": "",
                    "isGroup": False,
                    "memberIds": [],
                    "harness": "temporal",
                    "origin": "user",
                    "isHiddenFromSidebar": False,
                    "createdAt": 1_762_000_000_000,
                    "updatedAt": 1_762_000_100_000,
                    "path": "/Users/tr/secret-transcript",
                    "lastEntry": {"kind": "text", "text": "private message sk-abcdefghijk"},
                }, {
                    "id": "room-1",
                    "name": "macOS 执委会",
                    "description": "",
                    "isGroup": True,
                    "memberIds": ["bot-1"],
                    "harness": "temporal",
                    "origin": "user",
                }]},
            )
            write_blob(
                cache,
                f"sand.client.slice.account.{auth_key}.sidebar.last-sections",
                {"sections": [{"id": "sec-1", "name": "基建", "agentIds": ["bot-1"]}]},
            )
            write_blob(
                cache,
                f"sand.client.slice.account.{auth_key}.ui-agent-refs",
                {"pinnedAgentIds": ["bot-1"]},
            )
            write_blob(
                cache,
                f"sand.client.slice.account.{auth_key}.weekly-usage.cache",
                {"kind": "present", "expiresAtMs": 1_762_000_200_000, "reading": {
                    "readAtMs": 1_762_000_000_000,
                    "usage": {
                        "percentUsed": 12,
                        "nextResetMs": 1_762_100_000_000,
                        "grokPlanLabel": "Grok Bot Plan",
                        "isSandTrial": False,
                        "isTeamSeat": False,
                        "onDemand": {"usedCents": 0, "limitCents": 100},
                    },
                }},
            )
            write_blob(
                cache,
                f"sand.client.slice.account.{other_key}.roster.last-roster",
                {"rows": [{"id": "other", "name": "Should Not Load", "isGroup": False}]},
            )
            write_blob(
                cache,
                f"sand.client.slice.account.{auth_key}.transcript.replicas.18fc579e",
                {"messages": ["secret transcript"]},
            )
            (cache / "sand-secrets.json").write_text("secret", encoding="utf-8")
            (cache / "Cookies").write_text("secret", encoding="utf-8")
            (cache / "composer-drafts").write_text("draft", encoding="utf-8")
            secret_target = cache / "secret-body.txt"
            secret_target.write_text("credential", encoding="utf-8")
            link = cache / blob_name("sand.client.slice.client-meta.account-slot.credential")
            link.symlink_to(secret_target)
            routes = root / "routes.json"
            routes.write_text(json.dumps({
                "fanout": "shadow",
                "endpoints": {"steward": {"bot": "Agent Steward", "bot_id": "bot-1", "routine": "Multica 状态提醒"}},
                "workspaces": {"acm-lab": {"endpoint_ref": "steward", "bot": "Agent Steward", "bot_id": "bot-1"}},
            }), encoding="utf-8")
            outbox = root / "outbox.json"
            outbox.write_text(json.dumps({
                "circuit": {"state": "closed", "consecutive_client_errors": 0},
                "circuits": {"steward": {"state": "open", "consecutive_client_errors": 2}},
                "items": {
                    "a": {"status": "pending", "dest": "steward", "payload": {"token": "sk-abcdefghijk"}},
                    "b": {"status": "dead_letter", "dest": "steward"},
                    "c": {"status": "shadow_record", "dest": "steward"},
                    "d": {"status": "accepted", "dest": "steward"},
                },
            }), encoding="utf-8")
            body = load_bots(GrokPaths(cache, routes, outbox), reader=reader)

        self.assertTrue(body["ok"], body)
        self.assertEqual(body["account_slot"], "12345678")
        self.assertNotIn(AUTH, json.dumps(body))
        self.assertNotIn("ABCDEFGH", json.dumps(body))
        self.assertNotIn("sk-abcdefghijk", json.dumps(body))
        self.assertNotIn("private message", json.dumps(body))
        self.assertNotIn("/Users/tr", json.dumps(body))
        names = [item["name"] for item in body["items"]]
        self.assertEqual(names, ["Agent Steward", "macOS 执委会"])
        steward = body["items"][0]
        self.assertTrue(steward["pinned"])
        self.assertEqual(steward["section_name"], "基建")
        self.assertEqual(steward["bridge"]["routine"], "Multica 状态提醒")
        self.assertEqual(steward["bridge"]["delivery"], "shadow")
        self.assertEqual(steward["bridge"]["workspaces"][0]["slug"], "acm-lab")
        self.assertEqual(steward["bridge"]["outbox"], {"pending": 1, "dead_letter": 1, "shadow_record": 1})
        self.assertEqual(steward["bridge"]["circuit"]["state"], "open")
        self.assertTrue(body["items"][1]["is_group"])
        self.assertEqual(body["weekly_usage"]["percent_used"], 12)
        self.assertEqual(body["delivery"], "shadow")
        self.assertEqual(body["outbox"]["dead_letter"], 1)
        for name in opened:
            decoded = decode_blob_name(name)
            if decoded is not None:
                self.assertTrue(is_allowed_slice(decoded), decoded)
                self.assertNotIn("transcript", decoded)
                self.assertNotIn("OTHERACCOUNT", decoded)
                self.assertNotIn("credential", decoded)
            else:
                self.assertIn(name, {"routes.json", "outbox.json"})


if __name__ == "__main__":
    unittest.main()
