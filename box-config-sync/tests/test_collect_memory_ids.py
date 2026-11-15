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
from unittest.mock import patch

from collect_support import BOT, TOOL, TOKEN, fixture, write
from boxsync.__main__ import main
from boxsync.safe_io import UnsafeIO
from boxsync.secrets_scan import contains_secret, scan
from boxsync.snapshot import collect, validate_snapshot, write_snapshot
from boxsync.validation import InvalidDocument, load_schema, validate


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
            # Identity errors now fail structurally, before secret scanning.
            with self.subTest(fields=list(change)), self.assertRaises(InvalidDocument):
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

    def test_invalid_identity_is_structurally_rejected_before_scan_and_io(self):
        # Both scopes must obey the same UUID and equality contract, regardless
        # of whether entropy heuristics would happen to flag the input.
        for bot in (BOT, *self.bots):
            for scope in ("agent_memory", "user_memory_by_agent"):
                value = self.candidate(bot)
                row = value["domains"]["memory"][0]
                row.update(scope=scope, id="memory:" + scope + "/" + bot)
                other_scope = "user_memory_by_agent" if scope == "agent_memory" else "agent_memory"
                cases = (
                    {"agent_id": "11111111-1111-4111-8111-111111111111"},
                    {"id": "memory:" + scope + "/not-a-uuid"},
                    {"id": row["id"] + "/extra"},
                    {"scope": other_scope},
                    {"agent_id": "not-a-uuid"},
                    {"id": row["id"] + "\n"},
                )
                for case_index, delta in enumerate(cases):
                    bad = copy.deepcopy(value)
                    bad["domains"]["memory"][0].update(delta)
                    before = copy.deepcopy(bad)
                    with self.subTest(entropy="low" if bot == BOT else "high",
                                      scope=scope, case=case_index):
                        destination = Path(self.tmp.name) / "must-not-exist.json"
                        with patch("boxsync.snapshot.contains_secret", side_effect=AssertionError("scan reached")), \
                             patch("boxsync.snapshot.open_directory", side_effect=AssertionError("I/O reached")):
                            with self.assertRaises(InvalidDocument):
                                validate_snapshot(bad)
                            with self.assertRaises(InvalidDocument):
                                write_snapshot(bad, destination)
                        self.assertFalse(destination.exists())
                        self.assertEqual(bad, before)

    def test_both_scopes_preserve_valid_high_and_low_entropy_identities(self):
        for scope in ("agent_memory", "user_memory_by_agent"):
            for bot in (BOT, *self.bots):
                value = self.candidate(bot)
                value["domains"]["memory"][0].update(scope=scope, id="memory:" + scope + "/" + bot)
                with self.subTest(scope=scope, entropy="low" if bot == BOT else "high"):
                    self.assertEqual(json.loads(validate_snapshot(value)), value)
                    destination = Path(self.tmp.name) / "valid.json"
                    write_snapshot(value, destination)
                    self.assertEqual(json.loads(destination.read_text()), value)

    def test_schema_rejects_bad_shapes_and_scope_prefix_mismatch(self):
        try:
            import jsonschema
        except ImportError:
            jsonschema = None
        schema = load_schema("snapshot")
        for scope in ("agent_memory", "user_memory_by_agent"):
            row = copy.deepcopy(self.value["domains"]["memory"][0])
            row.update(scope=scope, id="memory:" + scope + "/" + BOT)
            validate(row, schema["$defs"]["memory"], schema)
            mutations = (
                {"id": "memory:" + scope + "/not-a-uuid"},
                {"id": row["id"] + "/extra"},
                {"scope": "user_memory_by_agent" if scope == "agent_memory" else "agent_memory"},
                {"agent_id": "not-a-uuid"},
            )
            for delta in mutations:
                with self.subTest(scope=scope, fields=list(delta)):
                    with self.assertRaises(InvalidDocument):
                        validate({**row, **delta}, schema["$defs"]["memory"], schema)
                    if jsonschema is not None:
                        with self.assertRaises(jsonschema.ValidationError):
                            jsonschema.Draft202012Validator(schema).validate({
                                **self.value, "domains": {**self.value["domains"], "memory": [{**row, **delta}]}})
