import json
import os
import tempfile
import unittest
from pathlib import Path

from multica_board.hooks_view import build_hooks, path_reach


SECRET = "SUPER_SECRET_COMMAND_do_not_show"
ERROR_SECRET = "SECRET_ERROR_PATH_do_not_show"


def _exists(paths):
    known = set(paths)

    def exists(path: str) -> bool:
        return path in known

    return exists


class HooksViewTest(unittest.TestCase):
    def test_enabled_and_trusted_hash_do_not_change_reachability(self):
        self.assertEqual(path_reach("/opt/hook.sh", lambda _path: True), "yes")
        self.assertEqual(path_reach("python3 hook.py", lambda _path: True), "unknown")
        self.assertEqual(path_reach("/opt/missing.sh", lambda _path: False), "no")
        self.assertEqual(path_reach("/opt/hook.sh", lambda _path: True, load_error=True), "no")
        self.assertEqual(path_reach("/opt/hook.sh", lambda _path: True, compatibility="mystery"), "unknown")
        self.assertEqual(path_reach("/opt/hook.sh", lambda _path: True, compatibility="enabled"), "yes")

    def test_sources_stay_separate_and_commands_are_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".cursor").mkdir()
            (home / ".codex").mkdir()
            (home / ".hermes" / "plugins" / "nowledge-mem").mkdir(parents=True)
            (home / ".cursor" / "hooks.json").write_text(json.dumps({
                "version": 1,
                "hooks": {"stop": [{"command": f"/opt/cursor.sh {SECRET}", "timeout": 1, "enabled": False, "trusted_hash": "abc"}]},
            }), encoding="utf-8")
            (home / ".codex" / "hooks.json").write_text(json.dumps({
                "hooks": {"Stop": [{"matcher": "*", "hooks": [
                    {"type": "command", "command": "/opt/codex.py", "enabled": False, "trusted_hash": "fff", "statusMessage": SECRET},
                ]}]},
            }), encoding="utf-8")
            (home / ".codex" / "config.toml").write_text('[hooks.state."x"]\nenabled = false\ntrusted_hash = "fff"\n', encoding="utf-8")
            (home / ".hermes" / "config.yaml").write_text(
                "hooks_auto_accept: false\nplugins:\n  enabled:\n    - nowledge-mem\n",
                encoding="utf-8",
            )
            (home / ".hermes" / "plugins" / "nowledge-mem" / "plugin.yaml").write_text(
                "name: nowledge-mem\nhooks:\n  - prefetch\n",
                encoding="utf-8",
            )
            payload = build_hooks(
                home,
                generated_at="2026-10-28T00:00:00Z",
                inspect=lambda: {
                    "hooks": [
                        {"event": "stop", "target": f"/opt/grok.sh {SECRET}", "compatibilityStatus": "enabled"},
                        {"event": "session_start", "target": f"echo {SECRET}"},
                        {"event": "notice", "target": "/opt/grok.sh", "compatibilityStatus": "mystery"},
                    ],
                    "hookLoadErrors": [
                        f"hook hooks:pre_tool_use[0] in {ERROR_SECRET}: this Cursor hook won't run",
                    ],
                },
                exists=_exists({"/opt/cursor.sh", "/opt/codex.py", "/opt/grok.sh"}),
            )
        blob = json.dumps(payload)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn(ERROR_SECRET, blob)
        self.assertNotIn("fff", blob)
        self.assertNotIn("statusMessage", blob)
        self.assertTrue(all(item["observed"] == "unknown" for item in payload["items"]))
        by_source = {}
        for item in payload["items"]:
            by_source.setdefault(item["source"], []).append(item)
        self.assertEqual({item["reachable"] for item in by_source["cursor"]}, {"yes"})
        self.assertEqual({item["reachable"] for item in by_source["codex"]}, {"yes"})
        grok = {item["event"]: item["reachable"] for item in by_source["grok_cli"]}
        self.assertEqual(grok["stop"], "yes")
        self.assertEqual(grok["session_start"], "unknown")
        self.assertEqual(grok["notice"], "unknown")
        self.assertEqual(grok["pre_tool_use"], "no")
        self.assertIn("grok_cli:load-error:pre_tool_use:0", {item["id"] for item in by_source["grok_cli"]})
        hermes = by_source["hermes"][0]
        self.assertEqual(hermes["event"], "nowledge-mem.prefetch")
        self.assertEqual(hermes["reachable"], "unknown")
        self.assertEqual(hermes["declared"], "yes")
        ids = [item["id"] for item in payload["sources"]]
        self.assertEqual(ids, ["cursor", "codex", "grok_cli", "hermes"])
        self.assertNotIn("grok_bot", ids)
        self.assertNotIn("multica", ids)
        hermes_source = payload["sources"][3]
        self.assertIn("空列表不是异常", hermes_source["detail"])

    def test_grok_rescanned_file_is_unreachable_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = build_hooks(
                Path(tmp),
                inspect=lambda: {
                    "hooks": [
                        {"event": "stop", "target": "/opt/cursor.sh", "source": {"type": "user", "path": "/tmp/hooks.json"}},
                        {"event": "stop", "target": "/opt/grok.sh", "compatibilityStatus": "enabled", "source": {"type": "user", "path": "/tmp/other.json"}},
                    ],
                    "hookLoadErrors": [
                        "hook hooks:stop[0] in /tmp/hooks.json: this Cursor hook won't run",
                    ],
                },
                exists=_exists({"/opt/cursor.sh", "/opt/grok.sh"}),
            )
        grok = [item for item in payload["items"] if item["source"] == "grok_cli"]
        self.assertEqual([item["reachable"] for item in grok], ["no", "yes"])
        self.assertFalse(any(item["id"].startswith("grok_cli:load-error:") for item in grok))
        self.assertNotIn("/tmp/hooks.json", json.dumps(payload))
        self.assertNotIn("/opt/cursor.sh", json.dumps(payload))

    def test_dev_null_does_not_make_an_existing_script_unreachable(self):
        if not os.path.exists("/dev/null"):
            self.skipTest("no /dev/null")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            script = home / "hook.sh"
            script.write_text("#!/bin/sh\n", encoding="utf-8")
            (home / ".cursor").mkdir()
            (home / ".cursor" / "hooks.json").write_text(json.dumps({
                "hooks": {"stop": [{"command": f"{script} >/dev/null"}]},
            }), encoding="utf-8")
            payload = build_hooks(home, inspect=lambda: {"hooks": [], "hookLoadErrors": []})
        cursor = [item for item in payload["items"] if item["source"] == "cursor"]
        self.assertEqual([item["reachable"] for item in cursor], ["yes"])
        self.assertNotIn(str(script), json.dumps(payload))

    def test_missing_files_are_unknown_not_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = build_hooks(Path(tmp), inspect=lambda: None, exists=lambda _path: False)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["items"], [])
        self.assertTrue(all(source["state"] == "unknown" for source in payload["sources"]))
        self.assertTrue(all(source["count"] is None for source in payload["sources"]))
        self.assertNotIn("attention", payload)

    def test_empty_hermes_shell_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".hermes").mkdir()
            (home / ".hermes" / "config.yaml").write_text("hooks:\nplugins:\n  enabled: []\n", encoding="utf-8")
            payload = build_hooks(home, inspect=lambda: {"hooks": [], "hookLoadErrors": []}, exists=lambda _path: False)
        hermes = next(source for source in payload["sources"] if source["id"] == "hermes")
        self.assertEqual(hermes["state"], "empty")
        self.assertEqual(hermes["count"], 0)
        self.assertIn("不是异常", hermes["detail"])

    def test_unreadable_hook_shape_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".cursor").mkdir()
            (home / ".cursor" / "hooks.json").write_text("{", encoding="utf-8")
            payload = build_hooks(home, inspect=lambda: {"hooks": "nope"}, exists=lambda _path: True)
        cursor = next(source for source in payload["sources"] if source["id"] == "cursor")
        grok = next(source for source in payload["sources"] if source["id"] == "grok_cli")
        self.assertEqual(cursor["state"], "unknown")
        self.assertEqual(grok["state"], "unknown")
        self.assertEqual(payload["items"], [])


if __name__ == "__main__":
    unittest.main()
