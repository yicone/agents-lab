from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multica_board.layers import (  # noqa: E402
    build_report,
    catalog_from_rows,
    code_from_option_name,
    resolve_issues,
)
from multica_board.suggest_layers import _multica, plan_rows  # noqa: E402

CATALOG = catalog_from_rows(
    [
        {
            "id": "p",
            "name": "主层",
            "type": "select",
            "options_json": json.dumps(
                [
                    {"id": "l1", "name": "L1 资源"},
                    {"id": "l2", "name": "L2 执行"},
                    {"id": "l3", "name": "L3 能力"},
                    {"id": "l4", "name": "L4 协作"},
                    {"id": "out", "name": "不在地图"},
                ]
            ),
        },
        {
            "id": "i",
            "name": "涉及层",
            "type": "multi_select",
            "options_json": json.dumps(
                [
                    {"id": "i1", "name": "L1 资源"},
                    {"id": "i2", "name": "L2 执行"},
                    {"id": "i3", "name": "L3 能力"},
                    {"id": "i4", "name": "L4 协作"},
                ]
            ),
        },
        {"id": "g", "name": "治理线", "type": "checkbox", "options_json": "[]"},
    ]
)


def issue(iid, ident, parent=None, props=None, **kw):
    row = {
        "id": iid,
        "identifier": ident,
        "title": ident,
        "workspace_id": "acm",
        "parent_issue_id": parent,
        "status": "todo",
        "status_category": "todo",
        "last_activity_at": "2026-10-01T00:00:00Z",
        "properties": props or {},
    }
    row.update(kw)
    return row


class ParseTests(unittest.TestCase):
    def test_prefix_and_outside(self):
        self.assertEqual(code_from_option_name("L1 资源"), "L1")
        self.assertEqual(code_from_option_name("L5"), "L5")
        self.assertEqual(code_from_option_name("不在地图"), "outside")
        self.assertIsNone(code_from_option_name("L10 资源"))
        self.assertIsNone(code_from_option_name("L1资源"))
        self.assertIsNone(code_from_option_name("不在地图说明"))


class ResolveTests(unittest.TestCase):
    def layer(self, rows, iid):
        return resolve_issues(rows, CATALOG)[iid]

    def test_own(self):
        layer = self.layer([issue("a", "ACM-1", props={"p": "l2", "g": True})], "a")
        self.assertEqual(layer["primary"], "L2")
        self.assertEqual(layer["governance"], True)
        self.assertEqual(layer["source"], {"primary": "own", "involved": None, "governance": "own"})

    def test_inherit_and_multilevel(self):
        rows = [
            issue("g", "ACM-1", props={"p": "l4"}),
            issue("p", "ACM-2", parent="g"),
            issue("c", "ACM-3", parent="p"),
        ]
        layer = self.layer(rows, "c")
        self.assertEqual(layer["primary"], "L4")
        self.assertEqual(layer["source"]["primary"], "inherited:ACM-1")
        self.assertEqual(self.layer(rows, "p")["source"]["primary"], "inherited:ACM-1")

    def test_fields_inherit_independently(self):
        rows = [
            issue("p", "ACM-9", props={"i": ["i2"], "g": True}),
            issue("c", "ACM-10", parent="p", props={"p": "l1"}),
        ]
        layer = self.layer(rows, "c")
        self.assertEqual(layer["primary"], "L1")
        self.assertEqual(layer["source"]["primary"], "own")
        self.assertEqual(layer["involved"], ["L2"])
        self.assertEqual(layer["source"]["involved"], "inherited:ACM-9")
        self.assertEqual(layer["governance"], True)
        self.assertEqual(layer["source"]["governance"], "inherited:ACM-9")

    def test_explicit_governance_false_does_not_inherit(self):
        rows = [
            issue("p", "ACM-9", props={"g": True}),
            issue("c", "ACM-10", parent="p", props={"g": False}),
        ]
        layer = self.layer(rows, "c")
        self.assertEqual(layer["governance"], False)
        self.assertEqual(layer["source"]["governance"], "own")

    def test_involved_containing_primary_is_stripped(self):
        layer = self.layer([issue("a", "ACM-1", props={"p": "l3", "i": ["i3", "i1"]})], "a")
        self.assertEqual(layer["primary"], "L3")
        self.assertEqual(layer["involved"], ["L1"])
        self.assertEqual(layer["source"]["involved"], "own")

    def test_child_primary_strips_only_its_own_layer_from_inherited_involved(self):
        rows = [
            issue("p", "ACM-9", props={"p": "l3", "i": ["i3", "i1"]}),
            issue("c", "ACM-10", parent="p", props={"p": "l1"}),
        ]
        self.assertEqual(self.layer(rows, "p")["involved"], ["L1"])
        child = self.layer(rows, "c")
        self.assertEqual(child["primary"], "L1")
        self.assertEqual(child["involved"], ["L3"])
        self.assertEqual(child["source"]["involved"], "inherited:ACM-9")

    def test_outside_drops_involved(self):
        layer = self.layer([issue("a", "ACM-1", props={"p": "out", "i": ["i1"]})], "a")
        self.assertEqual(layer["primary"], "outside")
        self.assertEqual(layer["involved"], [])
        self.assertIsNone(layer["source"]["involved"])

    def test_involved_only_stays_unassigned(self):
        layer = self.layer([issue("a", "ACM-1", props={"i": ["i2"]})], "a")
        self.assertIsNone(layer["primary"])
        self.assertEqual(layer["involved"], ["L2"])
        self.assertEqual(layer["source"]["involved"], "own")

    def test_parent_missing_from_cache(self):
        layer = self.layer([issue("c", "ACM-3", parent="missing")], "c")
        self.assertEqual(layer["primary"], None)
        self.assertEqual(layer["involved"], [])
        self.assertEqual(layer["governance"], None)
        self.assertEqual(layer["source"], {"primary": None, "involved": None, "governance": None})


class ReportTests(unittest.TestCase):
    def test_cross_layer_counted_once_and_cancelled_excluded(self):
        rows = [
            issue("a", "ACM-1", props={"p": "l2", "i": ["i4"]}, status="todo", status_category="todo"),
            issue("b", "ACM-2", props={"p": "l3"}, status="done", status_category="done"),
            issue("c", "ACM-3", props={"p": "out"}, status="todo", status_category="todo"),
            issue("d", "ACM-4", props={"i": ["i1"]}, status="backlog", status_category="backlog"),
            issue("e", "ACM-5", props={"p": "l1"}, status="cancelled", status_category="cancelled"),
            issue("f", "ACM-6", props={"p": "l2", "g": True}, status="in_review", status_category="in_review",
                  last_activity_at="2026-10-10T00:00:00Z"),
            issue("g", "ACM-7", props={"p": "l2"}, status="blocked", status_category="blocked",
                  last_activity_at="2026-09-01T00:00:00Z"),
            issue("h", "ACM-8", props={"p": "l2"}, status="in_review", status_category="in_review",
                  last_activity_at="2026-10-12T00:00:00Z"),
            issue("i", "ACM-9", props={"p": "l2"}, status="blocked", status_category="blocked",
                  last_activity_at="2026-10-11T00:00:00Z"),
        ]
        resolved = resolve_issues(rows, CATALOG)
        events = {"f": [{"at": "2026-08-01T00:00:00Z", "status": "in_review"}]}
        report = build_report(rows, resolved, events, ready=True)
        by_id = {cell["id"]: cell for cell in report["layers"]}
        self.assertEqual(by_id["L2"]["total"], 5)  # a, f, g, h, i
        self.assertEqual(by_id["L3"]["total"], 1)
        self.assertEqual(by_id["L4"]["total"], 0)
        self.assertEqual(by_id["L4"]["involved_extra"], 1)
        self.assertEqual(by_id["L4"]["involved_from"], ["L2"])
        self.assertEqual(by_id["L1"]["total"], 0)  # cancelled e is excluded
        self.assertEqual(report["outside"]["total"], 1)
        self.assertEqual(report["unassigned"]["total"], 1)  # involved-only d
        counted = 5 + 1 + 1 + 1  # L2 + L3 + outside + unassigned
        self.assertEqual(report["header"]["total"], counted)
        self.assertEqual(
            sum(cell["total"] for cell in report["layers"]) + report["outside"]["total"] + report["unassigned"]["total"],
            report["header"]["total"],
        )
        self.assertEqual(report["header"]["assigned"], 6)  # five L2 + one L3
        self.assertEqual(by_id["L2"]["open"], 5)  # b is the done one, on L3
        self.assertEqual(by_id["L3"]["open"], 0)
        self.assertEqual(by_id["L2"]["decide"], 4)
        self.assertEqual(by_id["L2"]["governance_open"], 1)
        self.assertEqual(by_id["L2"]["governance_decide"], 1)
        # Longest wait first, and only three. f entered in_review in August, before the activity fallbacks.
        self.assertEqual([it["identifier"] for it in by_id["L2"]["decide_items"]], ["ACM-6", "ACM-7", "ACM-9"])
        self.assertTrue(by_id["L2"]["decide_items"][0]["waiting_exact"])
        self.assertFalse(by_id["L2"]["decide_items"][1]["waiting_exact"])
        self.assertEqual(report["unassigned"]["open"], 1)  # backlog counts as open

    def test_not_ready_puts_every_issue_in_unassigned(self):
        rows = [issue("a", "ACM-1", props={"p": "l2"}), issue("b", "ACM-2", status="cancelled", status_category="cancelled")]
        report = build_report(rows, resolve_issues(rows, catalog_from_rows([])), {}, ready=False)
        self.assertFalse(report["ready"])
        self.assertEqual(report["header"]["total"], 1)
        self.assertEqual(report["unassigned"]["total"], 1)
        self.assertEqual(report["header"]["assigned"], 0)


class SuggestTests(unittest.TestCase):
    def ids(self, issues, projects=None):
        rows, _stats = plan_rows(issues, projects or {})
        return [r["identifier"] for r in rows], rows

    def test_matching_child_is_omitted(self):
        issues = [
            {"id": "p", "identifier": "ACM-1", "title": "skills inventory", "status": "todo"},
            {"id": "c", "identifier": "ACM-2", "title": "fix inventory link", "parent_issue_id": "p", "status": "todo"},
        ]
        idents, rows = self.ids(issues)
        self.assertEqual(idents, ["ACM-1"])
        self.assertEqual(rows[0]["primary"], "L3 能力")
        self.assertEqual(rows[0]["confidence"], "高")

    def test_different_child_is_listed_and_low_confidence_comes_first(self):
        issues = [
            {"id": "p", "identifier": "ACM-10", "title": "skills inventory", "status": "todo"},
            {"id": "c", "identifier": "ACM-2", "title": "skill 设计", "parent_issue_id": "p", "status": "todo"},
        ]
        idents, rows = self.ids(issues)
        self.assertEqual(idents, ["ACM-2", "ACM-10"])
        child = rows[0]
        self.assertEqual(child["confidence"], "低")
        self.assertEqual(child["primary"], "L5 感知与决策")
        self.assertIn("L3", child["involved"])

    def test_off_map_project_and_cancelled_parent(self):
        projects = {"disk": "Mac 存储清理与维护"}
        issues = [
            {"id": "p", "identifier": "ACM-58", "title": "skills inventory", "status": "cancelled", "status_category": "cancelled"},
            {"id": "c", "identifier": "ACM-59", "title": "follow up", "parent_issue_id": "p", "status": "todo"},
            {"id": "s", "identifier": "ACM-55", "title": "调研：占用", "project_id": "disk", "status": "in_review"},
        ]
        idents, rows = self.ids(issues, projects)
        by_ident = {r["identifier"]: r for r in rows}
        self.assertEqual(set(idents), {"ACM-59", "ACM-55"})
        self.assertEqual(by_ident["ACM-59"]["basis"], "继承")
        self.assertEqual(by_ident["ACM-59"]["primary"], "L3 能力")
        self.assertEqual(by_ident["ACM-55"]["primary"], "不在地图")
        self.assertEqual(by_ident["ACM-55"]["basis"], "项目")
        self.assertEqual(by_ident["ACM-55"]["confidence"], "高")
        self.assertEqual(idents, ["ACM-55", "ACM-59"])

    def test_refuses_property_writes(self):
        with self.assertRaises(SystemExit):
            _multica(["issue", "property", "set", "ACM-1", "主层", "L1"])
        with self.assertRaises(SystemExit):
            _multica(["property", "create", "--name", "主层"])


if __name__ == "__main__":
    unittest.main()
