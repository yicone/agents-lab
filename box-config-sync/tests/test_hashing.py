import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from support import REPO
from boxsync.hashing import body_sha256, config_fingerprint, instructions_sha256, tree_sha256
from diff_instructions import instructions_sha256 as canonical_instructions_sha256


class HashingTests(unittest.TestCase):
    def test_instructions_reuses_canonical_function(self):
        self.assertIs(instructions_sha256, canonical_instructions_sha256)
        for text in ("  cafe\u0301\r\n中文\rline  ", "", "  ", None):
            self.assertEqual(instructions_sha256(text), canonical_instructions_sha256(text))
        self.assertEqual(instructions_sha256("cafe\u0301\r\ntext"), instructions_sha256("café\ntext"))

    def test_tree_protocol_and_metadata_independence(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            root = Path(tmp)
            (root / "sub").mkdir()
            (root / "empty").mkdir()
            values = {"z": b"last\r\n", "sub/é": b"first"}
            for path, body in values.items():
                (root / path).write_bytes(body)
            (root / ".DS_Store").write_text("ignored")
            (root / "connector-secrets").mkdir()
            (root / "connector-secrets/hidden").write_text("ignored")
            rows = "".join(path + "\0" + hashlib.sha256(values[path]).hexdigest() + "\0" + str(len(values[path])) + "\n"
                           for path in sorted(values, key=lambda p: p.encode()))
            expected = hashlib.sha256(rows.encode()).hexdigest()
            self.assertEqual(tree_sha256(root), {"tree_state": "observed", "tree_sha256": expected})
            (root / "z").chmod(0o600)
            os.utime(root / "z", ns=(1000000000, 2000000000))
            self.assertEqual(tree_sha256(root)["tree_sha256"], expected)
            self.assertEqual(body_sha256(root, "z")["body_sha256"], hashlib.sha256(values["z"]).hexdigest())
            (root / "link").symlink_to("z")
            self.assertEqual(tree_sha256(root), {"tree_state": "unsupported_entry", "tree_sha256": None})
            (root / "link").unlink()
            os.mkfifo(root / "pipe")
            self.assertEqual(tree_sha256(root)["tree_state"], "unsupported_entry")

    def test_empty_missing_and_large_tree(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as tmp:
            self.assertEqual(tree_sha256(tmp)["tree_sha256"], hashlib.sha256(b"").hexdigest())
            self.assertIsNone(tree_sha256(Path(tmp) / "missing")["tree_sha256"])
            self.assertEqual(body_sha256(tmp, "missing")["body_hash_state"], "missing")
            with (Path(tmp) / "huge").open("wb") as stream:
                stream.truncate(512 * 1024 + 1)
            self.assertEqual(tree_sha256(tmp)["tree_state"], "too_large")

    def test_config_canonical_json(self):
        self.assertEqual(config_fingerprint({"z": 1, "a": 2}), config_fingerprint({"a": 2, "z": 1}))
        self.assertEqual(config_fingerprint({"中": True}), hashlib.sha256(b'{"\\u4e2d":true}').hexdigest())
