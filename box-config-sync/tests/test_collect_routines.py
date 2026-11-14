"""Compare disk/turn collection paths, rather than pinning literal digests."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from collect_support import BOT, TOOL, PROMPT, fixture, sidecar, write
from boxsync.snapshot import collect


class RoutineFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temporary.cleanup)
        self.ctx, _ = fixture(self.temporary.name)
        self.disk = {"id": "daily", "name": "Daily", "enabled": True,
                     "schedule": "0 1 * * *", "prompt": PROMPT}
        self.turn = {"id": "daily", "name": "Daily", "enabled": True,
                     "schedule": "0 1 * * *",
                     "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest()}

    def rows(self, disk=None, turn=None):
        write(self.ctx.roots["routines"] / BOT / "daily.json", self.disk if disk is None else disk)
        sc = sidecar()
        sc["routines"] = [self.turn if turn is None else turn]
        snapshot, _ = collect(copy.deepcopy(self.ctx), [("fixture", json.dumps(sc).encode())], TOOL)
        self.assertEqual(snapshot["sources"]["sidecar"], "ok")
        return {row["kind"]: row for row in snapshot["domains"]["routines"]
                if row["owner_id"] == BOT and row["routine_id"] == "daily"}

    def test_equal_configuration_matches_across_sources(self):
        rows = self.rows()
        self.assertEqual(rows["disk"]["routine_sha256"], rows["turn"]["routine_sha256"])

    def test_omitted_null_and_empty_optional_fields_share_defaults(self):
        disk = {k: v for k, v in self.disk.items() if k not in ("schedule", "prompt")}
        turn = {k: v for k, v in self.turn.items() if k not in ("schedule", "prompt_sha256")}
        for schedule in (None, "", "   "):
            for explicit_digest in (False, True):
                with self.subTest(schedule=schedule, explicit_digest=explicit_digest):
                    candidate = {**turn, "schedule": schedule}
                    if explicit_digest:
                        candidate["prompt_sha256"] = None
                    rows = self.rows(disk, candidate)
                    self.assertEqual(rows["disk"]["routine_sha256"], rows["turn"]["routine_sha256"])
                    # Snapshot fields still retain the turn's original values.
                    self.assertEqual(rows["turn"]["schedule"], schedule)
                    self.assertEqual("prompt_sha256" in rows["turn"], explicit_digest)

    def test_timestamp_and_run_status_changes_do_not_change_configuration_hash(self):
        baseline = self.rows()["disk"]["routine_sha256"]
        for updated in (None, "2026-11-13T01:00:00Z", "2026-11-14T01:00:00Z"):
            with self.subTest(updated=updated):
                turn = {**self.turn, "last_run_at": "2026-11-14T01:00:00Z",
                        "next_run_at": "2026-11-15T01:00:00Z"}
                rows = self.rows({**self.disk, "updated_at": updated}, turn)
                self.assertEqual(rows["disk"]["routine_sha256"], baseline)
                self.assertEqual(rows["turn"]["routine_sha256"], baseline)
                self.assertEqual(rows["turn"]["last_run_at"], turn["last_run_at"])
                self.assertEqual(rows["turn"]["next_run_at"], turn["next_run_at"])
        rows = self.rows(turn={**self.turn, "last_run_at": None, "next_run_at": None})
        self.assertEqual(rows["turn"]["routine_sha256"], baseline)

    def test_each_real_configuration_change_is_detected_and_matches_when_mirrored(self):
        baseline = self.rows()["disk"]["routine_sha256"]
        changes = (
            ({"name": "Changed"}, {"name": "Changed"}),
            ({"enabled": False}, {"enabled": False}),
            ({"schedule": "0 2 * * *"}, {"schedule": "0 2 * * *"}),
            ({"prompt": PROMPT + " changed"},
             {"prompt_sha256": hashlib.sha256((PROMPT + " changed").encode()).hexdigest()}),
            ({"prompt": ""}, {"prompt_sha256": hashlib.sha256(b"").hexdigest()}),
        )
        for disk_delta, turn_delta in changes:
            with self.subTest(fields=list(disk_delta)):
                rows = self.rows(turn={**self.turn, **turn_delta})
                self.assertNotEqual(rows["disk"]["routine_sha256"], rows["turn"]["routine_sha256"])
                rows = self.rows({**self.disk, **disk_delta}, {**self.turn, **turn_delta})
                self.assertEqual(rows["disk"]["routine_sha256"], rows["turn"]["routine_sha256"])
                self.assertNotEqual(rows["disk"]["routine_sha256"], baseline)

    def test_text_normalization_is_shared_without_rewriting_turn_fields(self):
        turn = {**self.turn, "name": "  Cafe\u0301  Daily  ", "schedule": " 0  1 * * * "}
        rows = self.rows({**self.disk, "name": "Café Daily"}, turn)
        self.assertEqual(rows["disk"]["routine_sha256"], rows["turn"]["routine_sha256"])
        self.assertEqual(rows["turn"]["name"], turn["name"])
        self.assertEqual(rows["turn"]["schedule"], turn["schedule"])
