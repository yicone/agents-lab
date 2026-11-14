"""Schema-aware Memory identity handling must not weaken secret rejection."""
import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from collect_support import BOT, TOOL, TOKEN, fixture, write
from boxsync.__main__ import main
from boxsync.safe_io import UnsafeIO
from boxsync.secrets_scan import contains_secret, scan
from boxsync.snapshot import collect, validate_snapshot, write_snapshot


class MemoryIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reproduce with synthetic UUIDs only; never copy real Bot identifiers.
        cls.bots = []
        for index in range(2000):
            bot = str(uuid.UUID(hashlib.sha256(str(index).encode()).hexdigest()[:32], version=4))
            identifier = "memory:agent_memory/" + bot
            if any(f["rule"] == "high_entropy" for f in scan(identifier)):
                cls.bots.append(bot)
                if len(cls.bots) == 3:
                    break
        assert len(cls.bots) == 3, "synthetic fixture must reproduce entropy rejection"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.tmp.cleanup)
        self.ctx, self.home = fixture(self.tmp.name)
        self.value, _ = collect(self.ctx, tool=TOOL)

    def candidate(self, bot):
        result = copy.deepcopy(self.value)
        row = result["domains"]["memory"][0]
        row.update(id="memory:agent_memory/" + bot, agent_id=bot)
        return result

    def test_high_entropy_ids_survive_validation_and_persistence_unchanged(self):
        for bot in self.bots:
            with self.subTest(case=self.bots.index(bot)):
                value = self.candidate(bot)
                before = copy.deepcopy(value)
                self.assertTrue(contains_secret(json.dumps(value)))
                text = validate_snapshot(value)
                self.assertEqual(json.loads(text), before)
                self.assertEqual(value, before)
                destination = Path(self.tmp.name) / "snapshot.json"
                write_snapshot(value, destination)
                self.assertEqual(json.loads(destination.read_text()), before)

    def test_collect_offline_cli_with_high_entropy_bot(self):
        for bot in self.bots:
            write(self.ctx.roots["profiles"] / bot / "profile.json", {"name": "Synthetic Bot"})
        state = Path(self.tmp.name) / "state"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["collect", "--offline", "--fixture-home", str(self.home),
                         "--state-dir", str(state)])
        self.assertEqual(code, 3)
        result = json.loads(output.getvalue())
        ids = {r["id"] for r in result["domains"]["memory"]}
        self.assertTrue(all("memory:agent_memory/" + bot in ids for bot in self.bots))
        self.assertEqual(result, json.loads((state / "snapshots/latest.json").read_text()))

    def test_real_secrets_in_other_fields_still_block_snapshot_and_write(self):
        for secret in (TOKEN, "token=" + "a" * 64, "aZ8kP1qX3vN6tW9rY2uI5oE7sD0fG4hJ"):
            value = self.candidate(self.bots[0])
            value["domains"]["profiles"][0]["name"] = secret
            with self.subTest(secret_kind=secret[:3]):
                with self.assertRaises(UnsafeIO):
                    validate_snapshot(value)
                destination = Path(self.tmp.name) / "blocked.json"
                with self.assertRaises(UnsafeIO):
                    write_snapshot(value, destination)
                self.assertFalse(destination.exists())

    def test_malformed_or_mismatched_ids_are_not_exempt(self):
        value = self.candidate(self.bots[0])
        identifier = value["domains"]["memory"][0]["id"]
        cases = (
            {"id": identifier + "/token=" + TOKEN},
            {"id": "memory:agent_memory/" + TOKEN},
            {"agent_id": BOT},
            {"scope": "user_memory_by_agent"},
        )
        for change in cases:
            bad = copy.deepcopy(value)
            bad["domains"]["memory"][0].update(change)
            with self.subTest(fields=list(change)), self.assertRaises(UnsafeIO):
                validate_snapshot(bad)

    def test_same_id_text_outside_memory_identity_is_still_scanned(self):
        value = self.candidate(self.bots[0])
        identifier = value["domains"]["memory"][0]["id"]
        value["domains"]["profiles"][0]["name"] = identifier
        with self.assertRaises(UnsafeIO):
            validate_snapshot(value)
        # Neither generic scanning nor explicit secret assignments get exemptions.
        self.assertTrue(contains_secret(identifier))
        self.assertTrue(contains_secret("token=" + identifier))
