"""Consumers read an aggregated report and do not redo failure classification."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from run_metrics.consume import bind_own_alerts, load_consumption


def _ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator, "rate": None, "definition": "kept in source only"}


def _window():
    return {
        "issue_count": 10,
        "run_count": 4,
        "success_rate": {
            "including_cancelled": _ratio(7, 11),
            "excluding_cancelled": _ratio(7, 10),
        },
        "rework": {
            "rework_rate_claim": "lower_bound_only",
            "rework_rate": None,
            "rework_rate_lower_bound": _ratio(2, 10),
            "rework_coverage": {"explicit_count": 0, "inferred_count": 2, "unknown_count": 1},
        },
        "failure_class_counts": {"quota": 1},
        "field_coverage": {
            "status": {"known_count": 4, "unknown_count": 0, "missing_count": 0},
            "failure_class": {"known_count": 1, "unknown_count": 1, "missing_count": 0},
        },
    }


def _report():
    return {
        "schema_version": "run-metrics.v0.1",
        "rule_version": "failure-rules.v0.2",
        "generated_at": "2026-10-26T00:00:00Z",
        "default_window": "14d",
        "windows": {"24h": _window(), "7d": _window(), "14d": _window()},
        "runtime_reconciliation": {
            "observed_count": 10,
            "online_count": 10,
            "offline_count": 0,
            "discrepancies": [{"name": "gemini"}],
            "unverified": ["gemini"],
            "quota_remaining_present": False,
        },
        "collection": {
            "partial": False,
            "run_missing_ids": [],
            "timeline_missing_ids": ["issue-1"],
            "errors": [],
        },
        "scheduling": {
            "unknown_hard_disable": False,
            "historical_quota_excludes_agent": False,
            "excluded_agent_ids": [],
            "historical_quota_agent_ids": ["agent-quota"],
            "signals": [{"health": "unknown"}],
        },
        "provider_unstable_class": {
            "emitted": False,
            "run_count": 0,
            "candidates": [{
                "id": "PC-001",
                "condition": "do not copy this classification condition",
                "sample_count": 6,
            }],
        },
        "failure_rules": [{"id": "R1", "condition": "do not copy this rule"}],
    }


class ConsumeTests(unittest.TestCase):
    def test_projects_aggregates_without_rule_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run-metrics.v0.1.json"
            payload = _report()
            path.write_text(json.dumps(payload), encoding="utf-8")
            before = path.read_bytes()
            loaded = load_consumption(path)
            self.assertEqual(path.read_bytes(), before)
            bound = bind_own_alerts(loaded, {"below_threshold": True, "findings_count": 2})
            text = json.dumps(bound, ensure_ascii=False)
            self.assertNotIn("do not copy", text)
            self.assertNotIn("failure_rules", text)
            self.assertNotIn("definition", text)
            self.assertEqual(bound["status"], "ok")
            self.assertEqual(bound["classification_copied"], False)
            self.assertEqual(bound["writes"], [])
            self.assertEqual(bound["dispatch_changed"], False)
            window = bound["windows"]["14d"]
            self.assertEqual(window["success_including_cancelled"], {"numerator": 7, "denominator": 11})
            self.assertEqual(window["success_excluding_cancelled"], {"numerator": 7, "denominator": 10})
            self.assertIsNone(window["rework_rate"])
            self.assertEqual(window["rework_lower_bound"], {"numerator": 2, "denominator": 10})
            self.assertEqual(window["failure_class_counts"], {"quota": 1})
            self.assertEqual(bound["provider_unstable"]["candidate_sample_counts"], [{"id": "PC-001", "sample_count": 6}])
            self.assertEqual(bound["scheduling_fields"]["unknown_hard_disable"], "false")
            self.assertEqual(bound["scheduling_fields"]["excluded_agent_ids"], "empty")
            self.assertEqual(bound["scheduling_fields"]["signals"], "present")
            self.assertFalse(bound["alert_delta"]["unified_counts_promoted_to_alerts"])
            self.assertEqual(bound["alert_delta"]["own_alerts"]["findings_count"], 2)
            self.assertNotEqual(bound["alert_delta"]["own_alerts"], bound["alert_delta"]["unified_default_window"])

    def test_absent_scheduling_fields_stay_missing(self):
        payload = _report()
        del payload["scheduling"]["historical_quota_excludes_agent"]
        del payload["scheduling"]["excluded_agent_ids"]
        payload["scheduling"]["signals"] = None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_consumption(path)
        self.assertEqual(loaded["status"], "ok")
        self.assertEqual(loaded["scheduling_fields"]["historical_quota_excludes_agent"], "missing")
        self.assertEqual(loaded["scheduling_fields"]["excluded_agent_ids"], "missing")
        self.assertEqual(loaded["scheduling_fields"]["signals"], "missing")
        self.assertNotEqual(loaded["scheduling_fields"]["historical_quota_excludes_agent"], "false")
        self.assertNotEqual(loaded["scheduling_fields"]["excluded_agent_ids"], "empty")

    def test_missing_file_does_not_invent_zero(self):
        loaded = load_consumption(Path("/no/such/run-metrics.v0.1.json"))
        self.assertEqual(loaded["status"], "missing")
        self.assertEqual(loaded["windows"], "missing")
        self.assertEqual(loaded["scheduling_fields"]["excluded_agent_ids"], "missing")
        self.assertNotIn("failure_class_counts", json.dumps(loaded))

    def test_not_provided_and_broken_json(self):
        self.assertEqual(load_consumption(None)["reason"], "not_provided")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("{", encoding="utf-8")
            before = path.read_bytes()
            loaded = load_consumption(path)
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual(loaded["status"], "invalid")
        self.assertEqual(loaded["windows"], "missing")

    def test_source_does_not_import_rules(self):
        source = Path(__file__).resolve().parents[1].joinpath("run_metrics", "consume.py").read_text(encoding="utf-8")
        self.assertNotIn("classify_failure", source)
        self.assertNotIn("from .rules", source)
        self.assertNotIn("import rules", source)


if __name__ == "__main__":
    unittest.main()
