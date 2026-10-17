import unittest
from pathlib import Path


DOC = Path(__file__).resolve().parents[1] / "docs" / "routing-precedence.md"
LAYERS = (
    "target adapter",
    "project",
    "canonical local",
    "runtime plugin",
    "runtime builtin",
    "global",
)


class RoutingPrecedenceDoc(unittest.TestCase):
    def setUp(self):
        self.text = DOC.read_text(encoding="utf-8")

    def test_layers_are_most_specific_first(self):
        positions = []
        for layer in LAYERS:
            ordered = f"\n{LAYERS.index(layer) + 1}. {layer}\n"
            self.assertIn(ordered, self.text)
            positions.append(self.text.index(ordered))
        self.assertEqual(positions, sorted(positions))

    def test_report_does_not_select_a_winner(self):
        self.assertIn("does not select which skill runs", self.text)
        self.assertIn("SCOPE_RANK", self.text)
        self.assertIn("Runtime load order is not a layer", self.text)

    def test_stop_conditions_are_explicit(self):
        for phrase in (
            "The user named a skill in this turn",
            "two or more candidates are still on the same layer",
            "behavior_divergence",
            "name_collision",
            "dependency_conflict",
            "trigger_ambiguity",
            "source_class` `unknown",
            "install, promote, fork, or replace",
            "family marker",
            "scope_shadowing",
        ):
            self.assertIn(phrase, self.text)

    def test_recorded_boundaries_do_not_become_blanket_winners(self):
        self.assertIn("logseq-acns-vault", self.text)
        self.assertIn("logseq-acns-write", self.text)
        self.assertIn("skills-lifecycle-manager", self.text)
        self.assertIn("skills-governance-audit", self.text)
        self.assertIn("ego-browser", self.text)
        self.assertIn("does not choose a winner for every other browser task", self.text)


if __name__ == "__main__":
    unittest.main()
