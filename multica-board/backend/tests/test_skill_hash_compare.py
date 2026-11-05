"""ACM-826: equivalence groups, raw hashes and unavailable-file semantics."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.skill_hash_compare import compare_entries


class HashCompareTests(unittest.TestCase):
    def test_raw_bytes_symlink_file_entry_and_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "original").mkdir()
            body = b"\xef\xbb\xbf# demo\r\n"
            (root / "original/SKILL.md").write_bytes(body)
            (root / "alias").symlink_to(root / "original", target_is_directory=True)
            paths = ["original", "alias", "original/SKILL.md"]
            entries = [{"path": path} for path in paths]
            result = compare_entries(entries, lambda path: root / path)
            self.assertEqual(result["status"], "match")
            self.assertEqual(result["groups"], [{"content_hash": hashlib.sha256(body).hexdigest(),
                                                 "entry_indexes": [0, 1, 2]}])
            self.assertTrue(all("content" not in entry for entry in entries))
            entries.append({"path": "missing"})
            result = compare_entries(entries, lambda path: root / path)
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["unknown_entry_indexes"], [3])
            self.assertEqual(entries[3]["hash_state"], "missing")
            (root / "different").mkdir()
            (root / "different/SKILL.md").write_bytes(body.replace(b"\r\n", b"\n"))
            entries.append({"path": "different"})
            result = compare_entries(entries, lambda path: root / path)
            self.assertEqual(result["status"], "mismatch")
            self.assertEqual(result["compared_entry_count"], 4)
            print("ACM-826 sample=demo " + json.dumps(result, sort_keys=True))

    def test_empty_file_hashes_and_no_or_single_entry_is_not_match(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "SKILL.md").write_bytes(b"")
            for paths in ([], [{"path": "SKILL.md"}]):
                result = compare_entries(paths, lambda path: root / path)
                self.assertEqual(result["status"], "insufficient_entries")
            self.assertEqual(paths[0]["content_hash"], hashlib.sha256(b"").hexdigest())

    def test_permission_denied_and_fifo_are_not_matches(self):
        with mock.patch("multica_board.skill_hash_compare.os.open", side_effect=PermissionError):
            entries = [{"path": "demo"}, {"path": "other"}]
            result = compare_entries(entries, Path)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["groups"], [])
        self.assertEqual([entry["hash_state"] for entry in entries], ["denied", "denied"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "SKILL.md"
            os.mkfifo(path)
            entries = [{"path": str(path)}]
            result = compare_entries(entries, Path)
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(entries[0]["hash_state"], "not_regular")


if __name__ == "__main__":
    unittest.main()
