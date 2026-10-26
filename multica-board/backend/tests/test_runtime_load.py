import json
import os
import tempfile
import unittest
from pathlib import Path

from multica_board.runtime_load import collect_runtime_load, parse_codex_skills_config, parse_hermes_disabled
from multica_board.skill_runtimes import runtime_columns


def item(name, path):
    return {"name": name, "origin": "inventory", "tools": {"codex": "direct", "hermes": "direct", "cursor": "direct"},
            "entries": [{"path": path}]}


class ParseTests(unittest.TestCase):
    def test_codex_opt_out_is_not_an_enabled_list(self):
        parsed = parse_codex_skills_config(
            'skills.config = [{path = "/skills/alpha/SKILL.md", enabled = false}, '
            '{path = "/skills/beta/SKILL.md", enabled = true}, {path = "nope"}]\n'
        )
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["disabled"][0]["name"], "alpha")
        self.assertEqual(parsed["enabled_count"], 1)
        self.assertEqual(parsed["skipped"], 1)
        self.assertEqual(parse_codex_skills_config("model = \"x\"\n")["status"], "absent_key")
        self.assertEqual(parse_codex_skills_config("skills = {config = 1}\n")["status"], "invalid")

    def test_hermes_disabled_list_ignores_other_sections(self):
        text = "skills:\n  disabled:\n    - alpha # note\n    - hermes-agent\nother:\n  disabled:\n    - nope\n"
        self.assertEqual(parse_hermes_disabled(text)["names"], ["alpha", "hermes-agent"])
        self.assertEqual(parse_hermes_disabled("skills:\n  disabled: [] # none\n")["names"], [])
        self.assertEqual(parse_hermes_disabled('skills:\n  disabled: [alpha, "beta"]\n')["names"], ["alpha", "beta"])
        self.assertEqual(parse_hermes_disabled("skills:\n  disabled: null\n")["status"], "invalid")
        self.assertEqual(parse_hermes_disabled("model: x\n")["status"], "absent_key")
        nested = "skills:\n  other:\n    disabled: [example-skill]\n"
        self.assertEqual(parse_hermes_disabled(nested)["status"], "absent_key")
        self.assertNotIn("example-skill", parse_hermes_disabled(nested).get("names", []))
        both = "skills:\n  disabled: [example-skill]\n  other:\n    disabled: [other-skill]\n"
        self.assertEqual(parse_hermes_disabled(both)["names"], ["example-skill"])


class ApplyTests(unittest.TestCase):
    def test_explicit_opt_out_marks_only_the_matching_skill(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            codex = root / "config.toml"
            hermes = root / "config.yaml"
            alpha = root / "alpha"
            codex.write_text(
                f'skills.config = [{{path = "{alpha / "SKILL.md"}", enabled = false}}, '
                f'{{path = "{root / "beta" / "SKILL.md"}", enabled = true}}]\n',
                encoding="utf-8",
            )
            hermes.write_text("skills:\n  disabled:\n    - gamma\n    - hermes-agent\n", encoding="utf-8")
            os.symlink(codex, root / "link.toml")
            probes = collect_runtime_load(codex_path=codex, hermes_path=hermes)
            linked = collect_runtime_load(codex_path=root / "link.toml", hermes_path=hermes)
            document = {"generated_at": "t", "tools": [{"id": "codex", "label": "Codex"}], "items": [
                item("alpha", str(alpha)),
                item("beta", str(root / "beta")),
                item("gamma", str(root / "gamma")),
                item("hermes-agent", str(root / "hermes-agent")),
                item("delta", str(root / "delta")),
            ]}
            result = runtime_columns(document, {"items": []}, [], probes)

        evidence = {row["name"]: row["runtime_evidence"] for row in result["items"]}
        self.assertFalse(evidence["alpha"]["codex"]["runtime_loaded"])
        self.assertTrue(evidence["alpha"]["codex"]["load_checked"])
        self.assertIsNone(evidence["beta"]["codex"]["runtime_loaded"])
        self.assertEqual(evidence["beta"]["codex"]["load_source"], "codex_config")
        self.assertFalse(evidence["gamma"]["hermes"]["runtime_loaded"])
        self.assertIsNone(evidence["hermes-agent"]["hermes"]["runtime_loaded"])
        self.assertIsNone(evidence["delta"]["cursor"]["runtime_loaded"])
        self.assertTrue(evidence["delta"]["cursor"]["load_checked"])
        self.assertEqual(load_probe(result, "cursor")["state"], "unverified")
        self.assertEqual(load_probe(result, "codex")["credibility"], "explicit_opt_out")
        self.assertNotIn("disabled", load_probe(result, "codex"))
        self.assertNotIn(str(alpha), json.dumps(load_probe(result, "codex"), ensure_ascii=False))
        self.assertEqual(linked["codex"]["state"], "unavailable")
        self.assertIsNone(document["items"][0].get("runtime_evidence"))

    def test_nested_hermes_disabled_does_not_block_and_direct_key_still_matches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            codex = root / "config.toml"
            nested = root / "nested.yaml"
            direct = root / "direct.yaml"
            codex.write_text("skills.config = []\n", encoding="utf-8")
            nested.write_text("skills:\n  other:\n    disabled: [example-skill]\n", encoding="utf-8")
            direct.write_text("skills:\n  disabled: [example-skill]\n  other:\n    disabled: [other-skill]\n", encoding="utf-8")
            document = {"generated_at": "t", "tools": [], "items": [
                item("example-skill", str(root / "example-skill")),
                item("other-skill", str(root / "other-skill")),
            ]}
            nested_probe = collect_runtime_load(codex_path=codex, hermes_path=nested)
            hidden = runtime_columns(document, {"items": []}, [], nested_probe)
            shown = runtime_columns(document, {"items": []}, [], collect_runtime_load(codex_path=codex, hermes_path=direct))
            unreliable = root / "bad.yaml"
            unreliable.write_text("skills:\n  disabled: null\n", encoding="utf-8")
            unknown = runtime_columns(document, {"items": []}, [], collect_runtime_load(codex_path=codex, hermes_path=unreliable))
            self.assertNotIn("disabled_names", nested_probe["hermes"])

        hidden_rows = {row["name"]: row["runtime_evidence"]["hermes"] for row in hidden["items"]}
        shown_rows = {row["name"]: row["runtime_evidence"]["hermes"] for row in shown["items"]}
        unknown_rows = {row["name"]: row["runtime_evidence"]["hermes"] for row in unknown["items"]}
        self.assertIsNone(hidden_rows["example-skill"]["runtime_loaded"])
        self.assertIsNone(hidden_rows["other-skill"]["runtime_loaded"])
        self.assertEqual(load_probe(hidden, "hermes")["state"], "unverified")
        self.assertIs(shown_rows["example-skill"]["runtime_loaded"], False)
        self.assertIsNone(shown_rows["other-skill"]["runtime_loaded"])
        self.assertIsNone(unknown_rows["example-skill"]["runtime_loaded"])
        self.assertEqual(load_probe(unknown, "hermes")["state"], "unverified")

    def test_missing_probe_preserves_null(self):
        result = runtime_columns(
            {"generated_at": "t", "tools": [{"id": "codex", "label": "Codex"}], "items": [item("alpha", "/skills/alpha")]},
            {"items": []}, [], None,
        )
        row = result["items"][0]["runtime_evidence"]["codex"]
        self.assertIsNone(row["runtime_loaded"])
        self.assertNotIn("load_checked", row)
        self.assertNotIn("load_probe", result["tools"][0])


def load_probe(result, provider):
    return next(tool["load_probe"] for tool in result["tools"] if tool["id"] == provider)


if __name__ == "__main__":
    unittest.main()
