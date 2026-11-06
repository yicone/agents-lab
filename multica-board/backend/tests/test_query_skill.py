"""ACM-892 CLI/fixture consumption, body exclusion and backend compatibility."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.skill_hash_compare import compare_entries
from multica_board.skill_registration import registration_fields
from multica_board.skill_runtimes import runtime_columns

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/query-skill.py"
FIXTURE = Path(__file__).parent / "fixtures/query-skill.json"
spec = importlib.util.spec_from_file_location("query_skill", SCRIPT)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class QuerySkillTests(unittest.TestCase):
    def setUp(self):
        self.document = json.loads(FIXTURE.read_text())

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], text=True,
                              capture_output=True, timeout=10)

    def test_fixture_name_keeps_sources_and_reverse_declarations(self):
        process = self.run_cli("--fixture", str(FIXTURE), "--name", "colgrep-search", "--json")
        self.assertEqual(process.returncode, 0, process.stderr)
        document = json.loads(process.stdout)
        self.assertEqual(document["input"], "fixture")
        local, grok = document["items"]
        self.assertEqual([local["id"], grok["id"]], ["colgrep-search", "grok_bot:colgrep-search"])
        self.assertEqual(local["hash_compare"]["status"], "match")
        self.assertEqual(local["entries"][1]["hash_state"], "ok")
        self.assertEqual(local["loaders"][1]["id"], "fixture-agent")
        self.assertEqual(local["declarations"][0]["workspace_id"], "fixture-workspace")
        self.assertTrue(local["declarations"][0]["enabled"])
        self.assertEqual(local["declarations"][0]["load_state"], "unverified")
        self.assertEqual(grok["declarations"][0]["name"], "Fixture Bot")
        self.assertEqual(grok["body_hash_state"], "not_applicable")
        self.assertIsNone(grok["hash_compare"])
        self.assertEqual(grok["hash_compare_state"], "unavailable")
        self.assertNotIn("BODY_SENTINEL", process.stdout)

    def test_id_and_text_include_required_fields(self):
        process = self.run_cli("--fixture", str(FIXTURE), "--id", "colgrep-search")
        self.assertEqual(process.returncode, 0, process.stderr)
        for field in cli.REQUIRED_FIELDS + ("hash_compare", "declarations"):
            self.assertIn(field + ":", process.stdout)
        self.assertNotIn('"grok_bot:colgrep-search"', process.stdout)

    def test_not_found_and_argument_errors(self):
        missing = self.run_cli("--fixture", str(FIXTURE), "--name", "absent", "--json")
        self.assertEqual(missing.returncode, 1)
        self.assertEqual(missing.stdout, "")
        self.assertIn("skill not found", json.loads(missing.stderr)["reason"])
        self.assertEqual(self.run_cli("--name", "x", "--id", "y").returncode, 2)
        self.assertEqual(self.run_cli().returncode, 2)

    def test_get_is_bounded_and_does_not_fetch_content(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = cli.URL
        response.read.return_value = json.dumps(self.document).encode()
        with mock.patch.object(cli, "build_opener") as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(cli.read_document()["items"][0]["id"], "colgrep-search")
        opener.return_value.open.assert_called_once_with(cli.URL, timeout=30)
        response.read.assert_called_once_with(cli.MAX_BYTES + 1)
        self.assertIsNone(cli.NoRedirect().redirect_request(None, None, 302, "", {}, "https://invalid"))

    def test_unavailable_board_never_falls_back_or_leaks_errors(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "build_opener", side_effect=URLError("BODY_SENTINEL")), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["--name", "colgrep-search", "--json"]), 1)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(json.loads(err.getvalue())["ok"], False)
        self.assertNotIn("BODY_SENTINEL", err.getvalue())

    def test_invalid_legacy_and_oversized_documents_fail(self):
        bad = copy.deepcopy(self.document)
        del bad["items"][0]["body_sha256"]
        samples = [{"ok": False, "items": []}, {"ok": True, "items": {}}, bad,
                   {"ok": True, "items": [None]}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.json"
            for sample in samples:
                path.write_text(json.dumps(sample))
                with self.assertRaises(cli.QueryError):
                    cli.read_document(path)
            path.write_bytes(b"{broken")
            with self.assertRaises(ValueError):
                cli.read_document(path)
            path.write_bytes(b" " * 33)
            with mock.patch.object(cli, "MAX_BYTES", 32), self.assertRaises(cli.QueryError):
                cli.read_document(path)

    def test_backend_hashes_drift_and_unknowns_survive_projection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            body = b"# demo\r\n"
            (root / "SKILL.md").write_bytes(body)
            row = {"name": "demo", "origin": "inventory", "source_class": "local_custom",
                   "canonical_path": str(root), "entries": [{"path": str(root), "present": True},
                                                               {"path": "missing"}],
                   "reconciliation_status": "modified", "tools": {"codex": "shared"}}
            row["hash_compare"] = compare_entries(row["entries"], lambda p: root / p)
            document = registration_fields(runtime_columns(
                {"ok": True, "items": [row], "tools": [{"id": "codex"}]}, {}, []), repo_root=root)
            result = cli.query(document, name="demo")["items"][0]
        self.assertEqual(result["body_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["reconciliation_status"], "modified")
        self.assertEqual(result["hash_compare"]["status"], "incomplete")
        self.assertEqual(result["hash_compare"]["unknown_entry_indexes"], [1])
        self.assertTrue(result["loaders"][0]["shared_auto_load"])
        self.assertIsNone(result["loaders"][0]["runtime_loaded"])


if __name__ == "__main__":
    unittest.main()
