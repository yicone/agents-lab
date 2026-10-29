"""The Atlas run-metrics endpoint only reads a local run-metrics.v0.1 file."""

from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multica_board.run_metrics_view import (  # noqa: E402
    SCHEMA_VERSION,
    load_run_metrics_file,
    report_path_for,
    scheduling_field_states,
)
from multica_board.server import BoardServer  # noqa: E402
from multica_board.store import Store  # noqa: E402
from multica_board.views import Views  # noqa: E402


def _report() -> dict:
    window = {
        "success_rate": {
            "including_cancelled": {"numerator": 1, "denominator": 2, "rate": 0.5, "definition": "in"},
            "excluding_cancelled": {"numerator": 1, "denominator": 1, "rate": 1.0, "definition": "ex"},
        },
        "rework": {
            "rework_rate_lower_bound": {"numerator": 0, "denominator": 1, "rate": 0.0, "definition": "low"},
        },
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "default_window": "14d",
        "runtime_reconciliation": {"observed_count": 0},
        "windows": {name: json.loads(json.dumps(window)) for name in ("24h", "7d", "14d")},
    }


class LoadTests(unittest.TestCase):
    def test_missing_snapshot_stays_absent(self):
        self.assertIsNone(report_path_for(":memory:"))
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "run-metrics.v0.1.json"
            loaded = load_run_metrics_file(missing)
            self.assertFalse(missing.exists())
            self.assertFalse(loaded["ok"])
            self.assertTrue(loaded["readonly"])
            self.assertEqual(loaded["writes"], [])
            self.assertIsNone(loaded["report"])

    def test_read_does_not_change_bytes_and_rejects_other_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run-metrics.v0.1.json"
            path.write_text(json.dumps(_report()), encoding="utf-8")
            before = path.read_bytes()
            loaded = load_run_metrics_file(path)
            self.assertEqual(path.read_bytes(), before)
            self.assertTrue(loaded["ok"])
            self.assertEqual(loaded["report"]["default_window"], "14d")
            self.assertIn("shadow", loaded)
            self.assertEqual(loaded["shadow"]["writes"], [])
            self.assertFalse(loaded["shadow"]["dispatch_changed"])
            path.write_text(json.dumps({"schema_version": "run-metrics.v9"}), encoding="utf-8")
            rejected = load_run_metrics_file(path)
            self.assertEqual(rejected["reason"], "schema")
            self.assertIsNone(rejected["report"])

    def test_missing_scheduling_is_marked_and_not_invented(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run-metrics.v0.1.json"
            path.write_text(json.dumps(_report()), encoding="utf-8")
            before = path.read_bytes()
            loaded = load_run_metrics_file(path)
            self.assertEqual(path.read_bytes(), before)
            self.assertTrue(loaded["ok"])
            self.assertNotIn("scheduling", loaded["report"])
            self.assertEqual(
                loaded["scheduling_fields"],
                {
                    "unknown_hard_disable": "missing",
                    "historical_quota_excludes_agent": "missing",
                    "excluded_agent_ids": "missing",
                    "historical_quota_agent_ids": "missing",
                    "signals": "missing",
                },
            )

    def test_explicit_false_and_empty_lists_stay_distinct(self):
        report = _report()
        report["scheduling"] = {
            "unknown_hard_disable": False,
            "historical_quota_excludes_agent": False,
            "excluded_agent_ids": [],
            "historical_quota_agent_ids": [],
            "signals": [],
        }
        self.assertEqual(
            scheduling_field_states(report),
            {
                "unknown_hard_disable": "false",
                "historical_quota_excludes_agent": "false",
                "excluded_agent_ids": "empty",
                "historical_quota_agent_ids": "empty",
                "signals": "empty",
            },
        )
        report["scheduling"] = {
            "historical_quota_excludes_agent": "false",
            "excluded_agent_ids": {},
            "historical_quota_agent_ids": "none",
            "signals": 0,
        }
        states = scheduling_field_states(report)
        self.assertEqual(states["historical_quota_excludes_agent"], "unknown")
        self.assertEqual(states["excluded_agent_ids"], "unknown")
        self.assertEqual(states["historical_quota_agent_ids"], "unknown")
        self.assertEqual(states["signals"], "unknown")
        self.assertEqual(states["unknown_hard_disable"], "missing")


class ShadowAttachTests(unittest.TestCase):
    def test_file_read_suggests_only_the_approved_workspace(self):
        approved = "8ebc87ea-27f2-42c4-90f6-24a04e64d3f5"
        other = "11111111-1111-1111-1111-111111111111"
        report = _report()
        window = {"start": "2026-10-28T02:00:00Z", "end": "2026-10-29T02:00:00Z"}
        for name in report["windows"]:
            report["windows"][name].update(window)

        def run(run_id, workspace_id, runtime_id):
            return {
                "run_id": run_id,
                "runtime_id": runtime_id,
                "workspace_id": workspace_id,
                "status": "failed",
                "anchor_at": "2026-10-29T01:00:00Z",
                "failure_class": "process_failed",
                "raw_reason_redacted": "agent_error.provider_server_error",
                "failure_evidence": {"excerpt": "Internal error"},
            }

        report["runs"] = [
            *[run(f"a{index}", approved, "rt-hermes") for index in range(3)],
            *[run(f"b{index}", other, "rt-other") for index in range(3)],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run-metrics.v0.1.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            before = path.read_bytes()
            loaded = load_run_metrics_file(path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(loaded["report"]["runs"][3]["workspace_id"], other)
            shadow = loaded["shadow"]
            self.assertTrue(shadow["enabled"])
            self.assertEqual(shadow["in_scope_run_count"], 3)
            self.assertEqual(shadow["excluded_other_workspace_run_count"], 3)
            self.assertEqual(len(shadow["suggestions"]), 1)
            self.assertEqual(shadow["suggestions"][0]["runtime_id"], "rt-hermes")
            self.assertEqual(shadow["writes"], [])
            self.assertFalse(shadow["dispatch_changed"])
            self.assertFalse(shadow["routing_shadow"]["failure_class_rewritten"])


class HttpTests(unittest.TestCase):
    def test_get_reads_and_post_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / "board.sqlite")
            path = root / "run-metrics.v0.1.json"
            path.write_text(json.dumps(_report()), encoding="utf-8")
            before = path.read_bytes()
            views = Views(store, "metrics-test", app_url="https://app.test", collect_interval_s=0)
            server = BoardServer("127.0.0.1", 0, views)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/api/run-metrics")
                response = conn.getresponse()
                body = json.loads(response.read())
                conn.close()
                self.assertEqual(response.status, 200)
                self.assertTrue(body["readonly"])
                self.assertEqual(body["writes"], [])
                self.assertTrue(body["ok"])
                self.assertTrue(body["shadow"]["enabled"])
                self.assertEqual(body["shadow"]["workspace_id"], "8ebc87ea-27f2-42c4-90f6-24a04e64d3f5")
                self.assertEqual(body["shadow"]["suggestions"], [])
                self.assertEqual(body["shadow"]["writes"], [])
                self.assertFalse(body["shadow"]["dispatch_changed"])
                self.assertEqual(body["shadow"]["write_endpoints_added"], [])
                self.assertTrue(body["shadow"]["readonly"])
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/api/run-metrics")
                denied = conn.getresponse()
                denied.read()
                conn.close()
                self.assertEqual(denied.status, 405)
                self.assertEqual(path.read_bytes(), before)
            finally:
                server.shutdown()
                server.server_close()
                store.close()
