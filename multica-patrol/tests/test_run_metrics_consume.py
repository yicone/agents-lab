"""Patrol keeps its own alerts and only attaches a run-metrics summary."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from patrol import config
from tests.fakes import BASE_NOW, GOOD_ENV, FakeMultica, invoke, last_result, make_issue


def _report():
    ratio = lambda numerator, denominator: {"numerator": numerator, "denominator": denominator}
    window = {
        "issue_count": 10,
        "run_count": 4,
        "success_rate": {
            "including_cancelled": ratio(7, 11),
            "excluding_cancelled": ratio(7, 10),
        },
        "rework": {
            "rework_rate_claim": "lower_bound_only",
            "rework_rate": None,
            "rework_rate_lower_bound": ratio(2, 10),
        },
        "failure_class_counts": {"quota": 3},
    }
    return {
        "schema_version": "run-metrics.v0.1",
        "generated_at": "2026-10-26T00:00:00Z",
        "default_window": "14d",
        "windows": {"24h": window, "7d": window, "14d": window},
        "scheduling": {"unknown_hard_disable": False, "excluded_agent_ids": []},
        "failure_rules": [{"condition": "do not copy this rule"}],
    }


class PatrolRunMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir = Path(self.tmp.name)
        self.fake = FakeMultica()
        self.fake.issues = [
            make_issue("ACM-229", "in_progress", uid=config.LEDGER_ISSUE_ID, assignee_id=None),
            make_issue("ACM-487", "in_progress", uid=config.INBOX_ISSUE_ID, assignee_id=None),
            make_issue("ACM-50", "in_progress", updated_at=BASE_NOW - timedelta(hours=5),
                       last_activity_at=BASE_NOW - timedelta(hours=5)),
        ]
        self.report = Path(self.tmp.name) / "run-metrics.v0.1.json"
        self.report.write_text(json.dumps(_report()), encoding="utf-8")

    def _run(self, *extra, env=None):
        return invoke(self.fake, ["run", "--slot", "2026102221", *extra],
                      env=env, state_dir=self.state_dir)

    def test_report_does_not_change_findings_or_writes(self):
        before = self.report.read_bytes()
        without = self._run(env=dict(GOOD_ENV, RUN_METRICS_REPORT=""))
        self.fake.comment_add_count = 0
        self.fake.comments.clear()
        self.state_dir.joinpath("state.json").unlink(missing_ok=True)
        with_report = self._run("--run-metrics", str(self.report))
        self.assertEqual(self.report.read_bytes(), before)
        plain = last_result(without[1])
        attached = last_result(with_report[1])
        self.assertEqual(without[0], 0, without[2])
        self.assertEqual(with_report[0], 0, with_report[2])
        for key in ("findings_count", "suppressed", "escalations", "writes_attempted"):
            self.assertEqual(attached[key], plain[key])
        self.assertGreater(attached["findings_count"], 0)
        self.assertEqual(attached["run_metrics"]["status"], "ok")
        self.assertEqual(plain["run_metrics"]["reason"], "not_provided")
        self.assertFalse(attached["run_metrics"]["alert_delta"]["unified_counts_promoted_to_alerts"])
        self.assertEqual(attached["run_metrics"]["alert_delta"]["own_alerts"]["findings_count"],
                         attached["findings_count"])
        self.assertEqual(attached["run_metrics"]["windows"]["14d"]["failure_class_counts"], {"quota": 3})
        self.assertNotIn("do not copy", with_report[1])
        for comments in self.fake.comments.values():
            for comment in comments:
                self.assertNotIn("run-metrics.v0.1", comment["content"])
                self.assertNotIn("failure_class", comment["content"])

    def test_daily_pack_mentions_gap_without_mentions(self):
        code, out, err = invoke(
            self.fake,
            ["daily-pack", "--format", "md", "--run-metrics", str(self.report)],
            state_dir=self.state_dir,
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Unified run metrics", out)
        self.assertIn("status: ok", out)
        self.assertNotIn("mention://", out)
        self.assertNotIn("do not copy", out)
        self.assertEqual(self.fake.comment_add_count, 0)


if __name__ == "__main__":
    unittest.main()
