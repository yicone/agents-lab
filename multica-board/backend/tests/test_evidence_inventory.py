"""ACM-581 read-only evidence inventory."""

from __future__ import annotations

import http.client
import json
import threading
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multica_board.evidence_inventory import EVIDENCE_LEVELS, REASONS, build_evidence_inventory  # noqa: E402
from multica_board.server import BoardServer  # noqa: E402

HOME = "/Users/tr"
GENERATED = "2026-10-26T09:00:00Z"
HASH = "HASH-SENTINEL-9f3c"
BODY = "BODY-SENTINEL-content"
SECRET = "token=SECRET-SENTINEL"


def payload(**extra):
    base = {
        "ok": True,
        "generated_at": GENERATED,
        "items": [],
        "meta": {"sources": [{"id": "grok_bot", "state": "ok", "collected_at": GENERATED}]},
    }
    base.update(extra)
    return base


def disk_item(name="demo", **extra):
    path = f"{HOME}/.agents/skills/{name}"
    item = {
        "name": name,
        "origin": "inventory",
        "source_class": "local_custom",
        "scope": "machine",
        "canonical_path": f"skills/{name}",
        "entries": [{"path": path, "lexical": path, "present": True, "scope": "user"}],
        "evidence": [{"state": "present", "path": path, "lexical": path, "scope": "user"}],
        "shared_entry": {"state": "dir", "path": path},
        "workspace_installs": [],
        "runtime_loaded": True,
        "command": "rm -rf /",
        "local_source": {"path": f"skills/{name}/SKILL.md", "content_hash": HASH, "summary": BODY},
    }
    item.update(extra)
    return item


def grok_item(name="demo"):
    return {
        "name": name,
        "origin": "grok_bot",
        "source_class": "grok_bot",
        "content": {"state": "not_applicable", "message": BODY},
        "command": "echo secret",
        "grok_bots": [
            {"id": "bot-a", "name": "Agent Steward", "evidence_state": "observed"},
            {"id": "bot-b", "name": "EO-PM", "evidence_state": "inferred"},
        ],
    }


def cli_item(name="demo"):
    return {"name": name, "origin": "grok_cli", "source_class": "grok_cli", "key": "grok_cli:" + name, "content": BODY}


class EvidenceInventoryTest(unittest.TestCase):
    def build(self, skills, **kwargs):
        return build_evidence_inventory(skills, home=Path(HOME), **kwargs)

    def test_same_name_sources_stay_separate_and_runtime_stays_null(self):
        body = self.build(payload(items=[
            disk_item(workspace_installs=[{
                "workspace_id": "ws-1", "id": "skill-1", "name": "demo",
                "content_hash": HASH, "content": {"message": BODY}, "description": BODY,
            }]),
            grok_item(),
            cli_item(),
        ]), workspace_collected=True)
        skills = [item for item in body["items"] if item["object_type"] == "skill" and item["display_name"] == "demo"]
        kinds = {item["source_kind"] for item in skills}
        self.assertEqual(kinds, {"local_machine", "repository", "multica_workspace", "grok_bot", "grok_cli"})
        self.assertEqual(len(skills), 6)  # local, repo, workspace, two bots, cli
        self.assertEqual(len({item["object_id"] for item in skills}), 6)
        self.assertTrue(all(item["related_name"] == "demo" for item in skills))
        self.assertTrue(all(item["runtime_loaded"] is None for item in body["items"]))
        self.assertEqual({item["source_kind"] for item in skills if item["source_id"] == "grok_bot"}, {"grok_bot"})
        self.assertEqual({item["source_kind"] for item in skills if item["source_id"] == "grok_cli"}, {"grok_cli"})
        bots = [item for item in skills if item["source_id"] == "grok_bot"]
        self.assertEqual({item["evidence_level"] for item in bots}, {"declared"})
        self.assertTrue(all(item["evidence"]["location"] is None for item in bots))
        self.assertTrue(all(item["hash"]["state"] == "not_applicable" and item["hash"]["value"] is None for item in bots))
        encoded = json.dumps(body)
        for secret in (HASH, BODY, SECRET, "rm -rf /", "echo secret"):
            self.assertNotIn(secret, encoded)
        self.assertNotIn('"content"', encoded)
        self.assertNotIn('"command"', encoded)
        self.assertEqual(body["read_only"], True)
        self.assertEqual(body["schema"], "atlas.evidence_inventory/v1")

    def test_export_does_not_duplicate_declaration_ids_or_claim_runtime_loading(self):
        exported = dict(grok_item(), key="grok-export:demo", source_class="grok_bot_export",
                        export={"state": "ok", "exported_at": GENERATED}, status_label="已校验 2 个文件")
        for state, expected in [("ok", "collected"), ("bad", "unavailable")]:
            exported["export"]["state"] = state
            body = self.build(payload(items=[grok_item(), exported]))
            skills = [i for i in body["items"] if i["object_type"] == "skill"]
            self.assertEqual(len(skills), len({i["object_id"] for i in skills}))
            row = next(i for i in skills if i["object_id"] == "grok-export:demo")
            self.assertEqual(row["collection_state"], expected)
            self.assertEqual(row["evidence_level"], "observed")
            self.assertIsNone(row["runtime_loaded"])
            self.assertIn("Grok Bot 导出", row["evidence"]["source_label"])

    def test_uncollected_sources_are_not_zero_and_levels_cover_observation_failure(self):
        body = self.build(payload(items=[
            disk_item(),
            {
                "name": "blocked",
                "origin": "unrecorded",
                "source_class": "unrecorded",
                "entries": [],
                "evidence": [],
                "shared_entry": {"state": "denied", "path": f"{HOME}/.agents/skills/blocked"},
                "workspace_installs": [],
            },
        ]))
        by_id = {source["source_id"]: source for source in body["sources"]}
        self.assertEqual(by_id["grok_cli"]["collection_state"], "uncollected")
        self.assertIsNone(by_id["grok_cli"]["collected_at"])
        self.assertEqual(by_id["grok_cli"]["reason"], "not_implemented")
        self.assertEqual(by_id["grok_cli"]["evidence_level"], "unknown")
        self.assertFalse(any(item["source_id"] == "grok_cli" for item in body["items"]))
        memory = [item for item in body["items"] if item["object_type"] == "memory_collection"]
        self.assertEqual(len(memory), 3)
        self.assertEqual(by_id["memory"]["collection_state"], "uncollected")
        self.assertEqual(by_id["memory"]["reason"], "source_unverified")
        self.assertTrue(all(item["collection_state"] == "uncollected" and item["reason"] == "source_unverified" for item in memory))
        encoded_memory = json.dumps(body)
        self.assertNotIn("no_supported_export", encoded_memory)
        self.assertNotIn("export_candidate_unverified", encoded_memory)
        self.assertIn("source_unverified", REASONS)
        self.assertNotIn("no_supported_export", REASONS)
        self.assertNotIn("export_candidate_unverified", REASONS)
        self.assertTrue(all(item["hash"]["value"] is None and item["evidence"]["location"] is None for item in memory))
        self.assertEqual({item["scope"] for item in memory}, {"bot-private", "shared"})
        denied = next(item for item in body["items"] if item["display_name"] == "blocked")
        self.assertEqual(denied["collection_state"], "unavailable")
        self.assertEqual(denied["evidence_level"], "unknown")
        self.assertEqual(denied["reason"], "permission_denied")
        self.assertNotEqual(denied["collection_state"], "collected")
        local = next(item for item in body["items"] if item["source_id"] == "local" and item["display_name"] == "demo")
        self.assertEqual(local["evidence_level"], "observed")
        self.assertEqual(local["evidence"]["location"], "~/.agents/skills/demo")
        self.assertIsNone(local["hash"]["value"])
        self.assertEqual(local["hash"]["state"], "available")
        repo = next(item for item in body["items"] if item["source_id"] == "repository")
        self.assertEqual(repo["evidence_level"], "declared")
        self.assertIn("derived", EVIDENCE_LEVELS)
        self.assertIn("historical", EVIDENCE_LEVELS)

    def test_case_distinct_names_are_not_linked_and_disk_grok_is_not_cli(self):
        body = self.build(payload(items=[
            disk_item("Demo"),
            disk_item("demo", entries=[{
                "path": f"{HOME}/.grok/skills/demo",
                "lexical": f"{HOME}/.grok/skills/demo",
                "present": True,
                "scope": "user",
            }], evidence=[{
                "state": "present",
                "lexical": f"{HOME}/.grok/skills/demo",
                "scope": "user",
            }]),
        ]))
        named = [item for item in body["items"] if item["object_type"] == "skill" and item["source_id"] == "local"]
        self.assertEqual({item["display_name"] for item in named}, {"Demo", "demo"})
        demo = next(item for item in named if item["display_name"] == "demo")
        named_demo = next(item for item in named if item["display_name"] == "Demo")
        self.assertEqual(demo["related_name"], "demo")
        self.assertEqual(named_demo["related_name"], "Demo")
        self.assertNotEqual(demo["related_name"], named_demo["related_name"])
        self.assertEqual(demo["source_kind"], "local_machine")
        self.assertNotEqual(demo["source_kind"], "grok_cli")
        self.assertEqual(demo["evidence"]["location"], "~/.grok/skills/demo")
        cli = next(source for source in body["sources"] if source["source_id"] == "grok_cli")
        self.assertEqual(cli["collection_state"], "uncollected")
        self.assertNotEqual(cli["source_kind"], "grok_bot")

    def test_missing_workspace_and_invalid_inventory_are_not_empty_collections(self):
        uncollected = self.build(payload(items=[disk_item(workspace_installs=[{
            "workspace_id": "ws-1", "id": "skill-1", "name": "demo",
        }])]), workspace_collected=False)
        workspace = next(source for source in uncollected["sources"] if source["source_id"] == "multica_workspace")
        self.assertEqual(workspace["collection_state"], "uncollected")
        self.assertFalse(any(item["source_id"] == "multica_workspace" for item in uncollected["items"]))

        invalid = self.build({
            "ok": False,
            "generated_at": GENERATED,
            "items": [grok_item(), "not-a-record"],
            "meta": {"sources": [{"id": "grok_bot", "state": "ok", "collected_at": GENERATED}]},
        }, workspace_collected=True)
        states = {source["source_id"]: source["collection_state"] for source in invalid["sources"]}
        self.assertEqual(states["local"], "invalid")
        self.assertEqual(states["repository"], "invalid")
        self.assertEqual(states["grok_bot"], "collected")
        self.assertFalse(any(item["source_id"] in {"local", "repository"} for item in invalid["items"]))
        self.assertEqual(len([item for item in invalid["items"] if item["source_id"] == "grok_bot"]), 2)

        broken = self.build(payload(items=[{
            "name": "demo",
            "origin": "cloud",
            "source_class": "multica_cloud",
            "workspace_installs": [BODY],
        }]), workspace_collected=True)
        bad = next(item for item in broken["items"] if item["collection_state"] == "invalid")
        self.assertEqual(bad["reason"], "schema_invalid")
        self.assertEqual(bad["evidence_level"], "unknown")
        self.assertNotIn(BODY, json.dumps(broken))

    def test_secret_path_is_redacted_and_declared_only_skill_has_no_fake_local_row(self):
        body = self.build(payload(items=[{
            "name": "quiet",
            "origin": "inventory",
            "source_class": "local_custom",
            "canonical_path": "skills/quiet",
            "entries": [{"path": f"{HOME}/secret/{SECRET}", "lexical": f"{HOME}/secret/{SECRET}", "present": True}],
            "evidence": [{"state": "present", "lexical": f"{HOME}/secret/{SECRET}"}],
            "shared_entry": {"state": "unscanned"},
            "workspace_installs": [],
        }, {
            "name": "declared-only",
            "origin": "inventory",
            "entries": [],
            "evidence": [],
            "shared_entry": {"state": "unscanned"},
            "workspace_installs": [],
        }]))
        quiet = next(item for item in body["items"] if item["display_name"] == "quiet" and item["source_id"] == "local")
        self.assertEqual(quiet["evidence"]["location"], "[redacted]")
        self.assertNotIn(SECRET, json.dumps(body))
        self.assertFalse(any(item["display_name"] == "declared-only" and item["source_id"] == "local" for item in body["items"]))
        self.assertTrue(any(item["display_name"] == "declared-only" and item["evidence_level"] == "declared" for item in body["items"]))

    def test_missing_stable_id_is_derived_and_grok_manifest_failure_is_invalid(self):
        derived = self.build(payload(items=[{
            "name": "demo",
            "origin": "cloud",
            "workspace_installs": [{"workspace_id": "ws-1", "name": "demo"}],
        }]), workspace_collected=True)
        item = next(row for row in derived["items"] if row["source_id"] == "multica_workspace")
        self.assertEqual(item["evidence_level"], "derived")
        self.assertNotEqual(item["object_id"], "demo")

        bad_grok = self.build(payload(
            items=[grok_item()],
            meta={"sources": [{"id": "grok_bot", "state": "bad", "detail": BODY}]},
        ))
        source = next(row for row in bad_grok["sources"] if row["source_id"] == "grok_bot")
        self.assertEqual(source["collection_state"], "invalid")
        self.assertEqual(source["reason"], "schema_invalid")
        self.assertFalse(any(row["source_id"] == "grok_bot" for row in bad_grok["items"]))
        self.assertNotIn(BODY, json.dumps(bad_grok))

    def test_non_object_payload_is_invalid_without_fake_items(self):
        body = self.build(None)
        self.assertFalse(body["ok"])
        self.assertEqual(body["read_only"], True)
        self.assertTrue(all(item["object_type"] == "memory_collection" for item in body["items"]))
        self.assertTrue(all(item["collection_state"] == "uncollected" for item in body["items"]))


class EvidenceRouteTest(unittest.TestCase):
    def test_get_is_read_only_and_post_is_rejected(self):
        views = mock.Mock()
        views.evidence_inventory.return_value = {
            "schema": "atlas.evidence_inventory/v1",
            "read_only": True,
            "ok": True,
            "sources": [],
            "items": [],
        }
        server = BoardServer("127.0.0.1", 0, views)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(*server.server_address)
            connection.request("GET", "/api/evidence-inventory")
            response = connection.getresponse()
            body = json.loads(response.read())
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(body["schema"], "atlas.evidence_inventory/v1")
            self.assertTrue(body["read_only"])
            views.evidence_inventory.assert_called_once()

            connection = http.client.HTTPConnection(*server.server_address)
            connection.request("GET", "/api/evidence-inventory?write=1")
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(response.status, 400)

            connection = http.client.HTTPConnection(*server.server_address)
            connection.request("POST", "/api/evidence-inventory", body=b"{}")
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(response.status, 405)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
