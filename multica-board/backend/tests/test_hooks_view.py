import json
import os
import tempfile
import unittest
from pathlib import Path

from multica_board.hooks_view import collect_hooks, path_reach


SECRET = "SUPER_SECRET_COMMAND_do_not_show"
ERROR_SECRET = "SECRET_ERROR_PATH_do_not_show"


def _exists(paths):
    known = set(paths)

    def exists(path: str) -> bool:
        return path in known

    return exists


def _collect(home, inspect_payload, exists):
    return collect_hooks(
        home,
        inspect=lambda: (inspect_payload, None) if inspect_payload is not None else (None, "missing"),
        exists=exists,
        now=1_700_000_000,
    )


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
            payload = _collect(
                home,
                {
                    "hooks": [
                        {"event": "stop", "target": f"/opt/grok.sh {SECRET}", "compatibilityStatus": "enabled"},
                        {"event": "session_start", "target": f"echo {SECRET}"},
                        {"event": "notice", "target": "/opt/grok.sh", "compatibilityStatus": "mystery"},
                    ],
                    "hookLoadErrors": [
                        f"hook hooks:pre_tool_use[0] in {ERROR_SECRET}: this Cursor hook won't run",
                    ],
                },
                _exists({"/opt/cursor.sh", "/opt/codex.py", "/opt/grok.sh"}),
            )
        blob = json.dumps(payload)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn(ERROR_SECRET, blob)
        self.assertNotIn("fff", blob)
        self.assertNotIn("statusMessage", blob)
        self.assertTrue(all(item["observed"] == "unknown" for item in payload["items"]))
        by_source = {}
        for item in payload["items"]:
            by_source.setdefault(item["source_id"], []).append(item)
        self.assertEqual({item["reachable"] for item in by_source["cursor"]}, {"present"})
        self.assertEqual({item["reachable"] for item in by_source["codex_user"]}, {"present"})
        grok = {item["event"]: item["reachable"] for item in by_source["grok_cli"]}
        self.assertEqual(grok["stop"], "present")
        self.assertEqual(grok["session_start"], "unknown")
        self.assertEqual(grok["notice"], "unknown")
        self.assertEqual(grok["pre_tool_use"], "missing")
        self.assertIn("grok_cli:load-error:pre_tool_use:0", {item["id"] for item in by_source["grok_cli"]})
        hermes = by_source["hermes_plugin"][0]
        self.assertEqual(hermes["event"], "prefetch")
        self.assertEqual(hermes["plugin"], "nowledge-mem")
        self.assertEqual(hermes["reachable"], "unknown")
        self.assertEqual(hermes["declared"], "present")
        ids = [item["id"] for item in payload["sources"]]
        self.assertEqual(ids, ["cursor", "codex_user", "grok_cli", "hermes_plugin"])
        self.assertNotIn("grok_bot", ids)
        self.assertNotIn("multica", ids)
        self.assertNotIn("hermes_shell", ids)
        self.assertEqual(payload["hermes_shell"]["collection_state"], "collected")
        self.assertEqual(payload["hermes_shell"]["count"], 0)

    def test_grok_rescanned_file_is_unreachable_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = _collect(
                Path(tmp),
                {
                    "hooks": [
                        {"event": "stop", "target": "/opt/cursor.sh", "source": {"type": "user", "path": "/tmp/hooks.json"}},
                        {"event": "stop", "target": "/opt/grok.sh", "compatibilityStatus": "enabled", "source": {"type": "user", "path": "/tmp/other.json"}},
                    ],
                    "hookLoadErrors": [
                        "hook hooks:stop[0] in /tmp/hooks.json: this Cursor hook won't run",
                    ],
                },
                _exists({"/opt/cursor.sh", "/opt/grok.sh"}),
            )
        grok = [item for item in payload["items"] if item["source_id"] == "grok_cli"]
        self.assertEqual([item["reachable"] for item in grok], ["missing", "present"])
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
            payload = collect_hooks(home, inspect=lambda: ({"hooks": [], "hookLoadErrors": []}, None), now=1_700_000_000)
        cursor = [item for item in payload["items"] if item["source_id"] == "cursor"]
        self.assertEqual([item["reachable"] for item in cursor], ["present"])
        self.assertNotIn(str(script), json.dumps(payload))

    def test_missing_files_are_not_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = collect_hooks(Path(tmp), inspect=lambda: (None, "missing"), exists=lambda _path: False, now=1_700_000_000)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["items"], [])
        by_source = {source["id"]: source for source in payload["sources"]}
        self.assertEqual(by_source["cursor"]["collection_state"], "collected")
        self.assertEqual(by_source["cursor"]["count"], 0)
        self.assertIsNone(by_source["cursor"]["error"])
        self.assertEqual(by_source["grok_cli"]["collection_state"], "unavailable")
        self.assertEqual(payload["hermes_shell"]["count"], 0)
        self.assertNotIn("attention", payload)

    def test_empty_hermes_shell_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".hermes").mkdir()
            (home / ".hermes" / "config.yaml").write_text("hooks:\nplugins:\n  enabled: []\n", encoding="utf-8")
            payload = collect_hooks(
                home,
                inspect=lambda: ({"hooks": [], "hookLoadErrors": []}, None),
                exists=lambda _path: False,
                now=1_700_000_000,
            )
        self.assertEqual(payload["hermes_shell"]["collection_state"], "collected")
        self.assertEqual(payload["hermes_shell"]["count"], 0)
        self.assertNotIn("hermes_shell", [source["id"] for source in payload["sources"]])
        hermes = next(source for source in payload["sources"] if source["id"] == "hermes_plugin")
        self.assertEqual(hermes["collection_state"], "collected")
        self.assertIsNone(hermes["error"])

    def test_unreadable_hook_shape_is_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".cursor").mkdir()
            (home / ".cursor" / "hooks.json").write_text("{", encoding="utf-8")
            payload = collect_hooks(home, inspect=lambda: ({"hooks": "nope"}, None), exists=lambda _path: True, now=1_700_000_000)
        by_source = {source["id"]: source for source in payload["sources"]}
        self.assertEqual(by_source["cursor"]["collection_state"], "invalid")
        self.assertEqual(by_source["grok_cli"]["collection_state"], "invalid")
        self.assertEqual(by_source["codex_user"]["collection_state"], "collected")
        self.assertEqual([item for item in payload["items"] if item["source_id"] in {"cursor", "grok_cli"}], [])


if __name__ == "__main__":
    unittest.main()
