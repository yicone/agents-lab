from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multica_board.hooks_view import HOOK_SOURCES, collect_hooks  # noqa: E402

SECRET = "SECRET_COMMAND_DO_NOT_LEAK"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class HooksInventoryTest(unittest.TestCase):
    def test_inventory(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            script = home / "bin" / "hook.sh"
            script.parent.mkdir()
            script.write_text("#!/bin/sh\n", encoding="utf-8")
            write(home / ".cursor" / "hooks.json", json.dumps({
                "hooks": {"stop": [{"command": f"sh {script} {SECRET}", "timeout": 5}], "sessionStart": [{"command": "relative-only"}]}
            }))
            write(home / ".codex" / "hooks.json", json.dumps({
                "hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": SECRET}]}]}
            }))
            write(home / ".codex" / "config.toml", "[features]\nhooks = true\nplugin_hooks = true\n")
            write(home / ".hermes" / "config.yaml", "model: demo\n")
            write(home / ".hermes" / "plugins" / "nowledge-mem" / "plugin.yaml", "name: nowledge-mem\nhooks:\n  - post_llm_call\n  - command: " + SECRET + "\n")
            write(home / ".hermes" / "plugins" / "orca-status" / "plugin.yaml", "name: orca-status\nprovides_hooks:\n  - post_llm_call\n")
            write(home / ".hermes" / "plugins" / "homeassistant" / "plugin.yaml", "name: homeassistant\nprovides_hooks: []\n")
            write(home / ".grokbot" / "hooks.json", json.dumps({"hooks": {"stop": [{"command": SECRET}]}}))

            def inspect():
                return {
                    "hooks": [
                        {"event": "stop", "hookType": "command", "target": SECRET, "source": {"type": "user", "path": str(home / ".claude" / "hooks")}},
                        {"event": "(plugin)", "hookType": "file", "target": "", "source": {"type": "plugin", "plugin_name": "nowledge-mem"}},
                    ],
                    "hookLoadErrors": [f"cursor format will not run {SECRET}"],
                }, None

            payload = collect_hooks(home, inspect=inspect, now=1_700_000_000)
            encoded = json.dumps(payload)
            self.assertNotIn(SECRET, encoded)
            self.assertNotIn("command", encoded)
            self.assertNotIn("target", encoded)
            self.assertNotIn("grok_bot", encoded)
            self.assertNotIn("multica", encoded)
            self.assertEqual([source["id"] for source in payload["sources"]], [item[0] for item in HOOK_SOURCES])
            by_source = {source["id"]: source for source in payload["sources"]}
            self.assertIsNone(by_source["cursor"]["error"])
            self.assertEqual(by_source["cursor"]["collection_state"], "collected")
            self.assertEqual(by_source["grok_cli"]["error"]["code"], "cursor_format_not_run")
            self.assertEqual(by_source["grok_cli"]["collection_state"], "collected")
            self.assertEqual(payload["hermes_shell"], {"collection_state": "collected", "count": 0, "error": None})
            names = [(item["source_id"], item["event"], item["plugin"]) for item in payload["items"]]
            self.assertIn(("cursor", "stop", ""), names)
            self.assertIn(("codex_user", "Stop", ""), names)
            self.assertIn(("grok_cli", "stop", ""), names)
            self.assertEqual(len([item for item in payload["items"] if item["event"] in {"stop", "Stop"}]), 3)
            hermes = [item for item in payload["items"] if item["source_id"] == "hermes_plugin" and item["event"] == "post_llm_call"]
            self.assertEqual({item["plugin"] for item in hermes}, {"nowledge-mem", "orca-status"})
            self.assertTrue(all(item["observed"] == "unknown" for item in payload["items"]))
            cursor_stop = next(item for item in payload["items"] if item["id"] == "cursor:stop:0")
            self.assertEqual(cursor_stop["reachable"], "present")
            cursor_relative = next(item for item in payload["items"] if item["event"] == "sessionStart")
            self.assertEqual(cursor_relative["reachable"], "unknown")

    def test_one_bad_source_does_not_drop_the_others(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write(home / ".cursor" / "hooks.json", "{")
            write(home / ".codex" / "hooks.json", json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "echo"}]}]}}))

            def inspect():
                return None, "missing"

            payload = collect_hooks(home, inspect=inspect, now=1_700_000_000)
            by_source = {source["id"]: source for source in payload["sources"]}
            self.assertEqual(by_source["cursor"]["collection_state"], "invalid")
            self.assertEqual(by_source["codex_user"]["collection_state"], "collected")
            self.assertEqual(by_source["codex_user"]["count"], 1)
            self.assertEqual(by_source["grok_cli"]["collection_state"], "unavailable")
            self.assertEqual(payload["hermes_shell"]["count"], 0)
            self.assertEqual(payload["hermes_shell"]["collection_state"], "collected")

    def test_shell_entries_are_counted_without_becoming_a_source(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write(home / ".hermes" / "config.yaml", "hooks:\n  pre_tool_call:\n    - command: " + SECRET + "\nmodel: demo\n")
            payload = collect_hooks(home, inspect=lambda: (None, "missing"), now=1_700_000_000)
            self.assertEqual(payload["hermes_shell"]["count"], 1)
            self.assertNotIn("hermes_shell", [source["id"] for source in payload["sources"]])
            self.assertNotIn(SECRET, json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
