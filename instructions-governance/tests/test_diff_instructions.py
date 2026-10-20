import base64
import importlib.util
import json
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "instructions-governance" / "scripts" / "diff_instructions.py"


def load_module():
    spec = importlib.util.spec_from_file_location("diff_instructions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blob_name(key: str) -> str:
    token = base64.b32encode(key.encode()).decode().rstrip("=").lower()
    return token + ".blob"


class DiffInstructionTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hash_matches_collector_normalization(self):
        nfd = "e" + unicodedata.normalize("NFD", "é")
        nfc = unicodedata.normalize("NFC", nfd)
        self.assertNotEqual(nfd, nfc)
        self.assertEqual(self.mod.instructions_sha256(nfd), self.mod.instructions_sha256(nfc))
        self.assertEqual(self.mod.instructions_sha256("  line\r\nnext  "), self.mod.instructions_sha256("line\nnext"))
        self.assertIsNone(self.mod.instructions_sha256("  \n"))
        self.assertIsNone(self.mod.instructions_sha256(None))

    def test_aligned_and_modified_follow_collected_hashes(self):
        digest = self.mod.instructions_sha256("keep this")
        self.mod.seed_sources(
            self.root,
            [{"workspace_id": "ws", "workspace_slug": "lab", "id": "agent-1", "name": "Builder", "instructions": "keep this"}],
            [{"id": "bot-1", "name": "Steward", "description": "watch the board"}],
        )
        report = self.mod.build_report(
            self.root,
            [{"workspace_id": "ws", "id": "agent-1", "name": "Builder", "instructions": "keep this", "instructions_sha256": digest}],
            [{"id": "bot-1", "name": "Steward", "description": "watch the board"}],
        )
        self.assertEqual(report["agents"][0]["status"], "aligned")
        self.assertEqual(report["bots"][0]["status"], "aligned")

        drifted = self.mod.build_report(
            self.root,
            [{"workspace_id": "ws", "id": "agent-1", "instructions": "keep this", "instructions_sha256": "f" * 64}],
            [{"id": "bot-1", "description_sha256": "a" * 64, "description": "watch the board"}],
        )
        self.assertEqual(drifted["agents"][0]["status"], "modified")
        self.assertEqual(drifted["bots"][0]["status"], "modified")
        diff = self.mod.line_diff("keep this", "changed\nline", "Builder")
        self.assertIn("-keep this", diff)
        self.assertIn("+changed", diff)

    def test_collect_skips_a_workspace_whose_list_stays_elsewhere(self):
        def run(argv):
            if argv[:2] == ["workspace", "list"]:
                return json.dumps([
                    {"id": "ws-a", "slug": "alpha", "name": "Alpha"},
                    {"id": "ws-b", "slug": "beta", "name": "Beta"},
                    {"id": "ws-c", "slug": "gamma", "name": "Gamma"},
                ])
            workspace_id = argv[1] if argv and argv[0] == "--workspace-id" else ""
            if workspace_id == "ws-c":
                raise SystemExit("permission denied")
            return json.dumps([
                {"id": "agent-1", "name": "Builder", "workspace_id": "ws-a", "instructions": "hello"},
            ])

        agents, skipped = self.mod.collect_agents(run)
        self.assertEqual([agent["workspace_slug"] for agent in agents], ["alpha"])
        self.assertEqual(agents[0]["instructions_sha256"], self.mod.instructions_sha256("hello"))
        self.assertEqual([row["slug"] for row in skipped], ["beta", "gamma"])

    def test_secret_bodies_are_not_written(self):
        counts = self.mod.seed_sources(
            self.root,
            [{"workspace_id": "ws", "workspace_slug": "lab", "id": "agent-1", "name": "Builder", "instructions": "token sk-abcdefghijklmnop"}],
            [],
        )
        self.assertEqual(counts["withheld"], 1)
        self.assertFalse((self.root / "sources" / "agents" / "lab" / "agent-1.md").exists())
        report = self.mod.build_report(self.root, [{"workspace_id": "ws", "id": "agent-1", "instructions": "token sk-abcdefghijklmnop"}], [])
        self.assertEqual(report["agents"][0]["status"], "withheld")

    def test_roster_reader_skips_forbidden_blobs(self):
        roster = self.root / "roster"
        roster.mkdir()
        opened = []

        def read(path: Path) -> str:
            opened.append(path.name)
            return path.read_text(encoding="utf-8")

        auth = "auth-1"
        slot = "sand.client.slice.client-meta.account-slot"
        roster_key = f"sand.client.slice.account.{auth}.roster.last-roster"
        secret_key = f"sand.client.slice.account.{auth}.transcript.replicas.1"
        (roster / blob_name(slot)).write_text(json.dumps(auth), encoding="utf-8")
        (roster / blob_name(roster_key)).write_text(
            json.dumps({"rows": [{"id": "bot-1", "name": "Steward", "description": "watch"}]}),
            encoding="utf-8",
        )
        (roster / blob_name(secret_key)).write_text("do not read", encoding="utf-8")
        rows = self.mod.read_roster_descriptions(roster, read)
        self.assertEqual(rows, [{"id": "bot-1", "name": "Steward", "description": "watch", "description_sha256": self.mod.instructions_sha256("watch")}])
        self.assertFalse(any("transcript" in name or name == blob_name(secret_key) for name in opened))

    def test_sync_dry_run_does_not_call_update_and_apply_backs_up_first(self):
        self.mod.seed_sources(
            self.root,
            [{"workspace_id": "ws", "workspace_slug": "lab", "id": "agent-1", "name": "Builder", "instructions": "from git"}],
            [],
        )
        report = self.mod.build_report(
            self.root,
            [{"workspace_id": "ws", "id": "agent-1", "name": "Builder", "instructions": "live text"}],
            [],
        )
        calls = []
        dry = self.mod.sync_agents(report, apply=False, backup_dir=self.root / "backup", apply_fn=lambda *args: calls.append(args))
        self.assertEqual(calls, [])
        self.assertFalse((self.root / "backup").exists())
        self.assertIn("-from git", dry[0]["diff"])
        self.assertIn("+live text", dry[0]["diff"])
        self.assertFalse(dry[0]["applied"])

        def fail_backup(*_args):
            raise AssertionError("update ran before backup")

        blocked = self.root / "backup-file"
        blocked.write_text("not a directory", encoding="utf-8")
        with self.assertRaises(OSError):
            self.mod.sync_agents(report, apply=True, backup_dir=blocked, apply_fn=fail_backup)

        def record(workspace_id, agent_id, instructions):
            backup = list((self.root / "backups").rglob("*.md"))
            self.assertEqual(len(backup), 1)
            self.assertEqual(backup[0].read_text(encoding="utf-8"), "live text")
            calls.append((workspace_id, agent_id, instructions))

        applied = self.mod.sync_agents(report, apply=True, backup_dir=self.root / "backups", apply_fn=record)
        self.assertEqual(calls, [("ws", "agent-1", "from git")])
        self.assertTrue(applied[0]["applied"])

    def test_cli_dry_run_prints_diff_without_invoking_multica(self):
        self.mod.seed_sources(
            self.root,
            [{"workspace_id": "ws", "workspace_slug": "lab", "id": "agent-1", "name": "Builder", "instructions": "from git"}],
            [{"id": "bot-1", "name": "Steward", "description": "from git"}],
        )
        agents = self.root / "agents.json"
        agents.write_text(
            json.dumps({"agents": [{"workspace_id": "ws", "id": "agent-1", "name": "Builder", "instructions": "live line"}]}),
            encoding="utf-8",
        )
        log = self.root / "multica.log"
        fake = self.root / "multica"
        fake.write_text(f"#!/bin/sh\necho \"$0 $*\" >> {log}\nexit 9\n", encoding="utf-8")
        fake.chmod(0o755)
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "sync",
                "--root",
                str(self.root),
                "--agents-json",
                str(agents),
                "--dry-run",
                "--multica",
                str(fake),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("-from git", proc.stdout)
        self.assertIn("+live line", proc.stdout)
        self.assertIn("multica agent update was not called", proc.stdout)
        self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
