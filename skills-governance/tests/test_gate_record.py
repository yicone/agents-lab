"""Gate summary persistence must never expose partial or invalid verdicts."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gate_record


class GateRecordTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "staging" / "last-gate.json"

    def test_absent_read_does_not_create_directory(self):
        self.assertEqual(gate_record.read(self.path)["state"], "empty")
        self.assertFalse(self.path.parent.exists())

    def test_replace_failure_preserves_previous_record_and_cleans_temp(self):
        gate_record.save(self.path, {"blocked": True})
        before = self.path.read_bytes()
        with mock.patch.object(gate_record.os, "replace", side_effect=OSError("failed")):
            with self.assertRaises(OSError):
                gate_record.save(self.path, {"blocked": False})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_record_never_looks_like_pass(self):
        record = gate_record.save(self.path, {"blocked": False})
        for field, value in (("time", "yesterday"), ("time", "2026-11-04"),
                             ("result", "allow"), ("source", "unknown"),
                             ("state", "ok"), ("schema", "v0")):
            with self.subTest(field=field, value=value):
                self.path.write_text(json.dumps({**record, field: value}))
                read = gate_record.read(self.path)
                self.assertEqual(read["state"], "error")
                self.assertIsNone(read["result"])
        self.path.write_bytes(b"x" * 1_048_577)
        self.assertEqual(gate_record.read(self.path)["state"], "error")

    def test_reader_uses_actual_path(self):
        record = gate_record.save(self.path, {"blocked": False})
        record["summary_json_path"] = "/untrusted/path"
        self.path.write_text(json.dumps(record))
        self.assertEqual(gate_record.read(self.path)["summary_json_path"], str(self.path))
