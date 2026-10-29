"""S8 enables read-only shadow suggestions for one workspace."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from run_metrics.project import project_run
from run_metrics.s8_enable import (
    APPROVED_WORKSPACE_ID,
    CONFIG_RELATIVE,
    MODE,
    PRE_ENABLE_MAIN,
    S7_IMPLEMENTATION,
    S7_MERGE,
    SCHEMA_VERSION,
    evaluate_s8,
    load_config,
)
from run_metrics.shadow import build_shadow

WINDOW = {"name": "24h", "start": "2026-10-28T02:00:00Z", "end": "2026-10-29T02:00:00Z"}
OTHER_WORKSPACE_ID = "11111111-1111-1111-1111-111111111111"


def _config(**overrides):
    payload = {
        "schema_version": SCHEMA_VERSION,
        "enabled": True,
        "workspace_id": APPROVED_WORKSPACE_ID,
        "mode": MODE,
    }
    payload.update(overrides)
    return payload


def _report(runs):
    return {
        "schema_version": "run-metrics.v0.1",
        "generated_at": WINDOW["end"],
        "windows": {"24h": dict(WINDOW), "7d": dict(WINDOW), "14d": dict(WINDOW)},
        "runs": runs,
        "scheduling": {
            "unknown_hard_disable": False,
            "historical_quota_excludes_agent": False,
            "excluded_agent_ids": [],
            "historical_quota_agent_ids": [],
            "signals": [
                {
                    "subject_type": "runtime",
                    "subject_id": "rt-other",
                    "provider": "cursor",
                    "name": "cursor",
                    "connection_status": "online",
                    "health": "unknown",
                    "quota_remaining": None,
                    "hard_disable": False,
                }
            ],
        },
    }


def _failure(run_id, workspace_id, runtime_id="rt-hermes"):
    return project_run({
        "id": run_id,
        "issue_id": f"issue-{run_id}",
        "agent_id": "agent-1",
        "runtime_id": runtime_id,
        "workspace_id": workspace_id,
        "status": "failed",
        "created_at": "2026-10-29T01:00:00Z",
        "started_at": "2026-10-29T01:00:00Z",
        "failure_reason": "agent_error.provider_server_error",
        "error": "hermes session/resume failed: Internal error (code=-32603)",
    })


class ConfigTests(unittest.TestCase):
    def test_committed_switch_is_on_for_the_approved_workspace_only(self):
        config = load_config()
        self.assertEqual(config["schema_version"], SCHEMA_VERSION)
        self.assertIs(config["enabled"], True)
        self.assertEqual(config["workspace_id"], APPROVED_WORKSPACE_ID)
        self.assertEqual(config["mode"], MODE)
        self.assertNotIn("workspace_ids", config)
        path = Path(CONFIG_RELATIVE)
        self.assertEqual(path.as_posix(), "run-metrics/config/s8-shadow-enable.json")
        self.assertNotIn(".multica-board", path.as_posix())
        self.assertEqual(config["rollback"]["pre_enable_main"], PRE_ENABLE_MAIN)

    def test_closed_switch_does_not_call_shadow(self):
        report = _report([_failure("r1", APPROVED_WORKSPACE_ID)])
        with patch("run_metrics.shadow.build_shadow") as mocked:
            result = evaluate_s8(report, _config(enabled=False))
        mocked.assert_not_called()
        self.assertFalse(result["enabled"])
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["suggestions"], [])
        self.assertEqual(result["routing_shadow"], None)
        self.assertEqual(result["writes"], [])
        self.assertFalse(result["dispatch_changed"])
        self.assertEqual(result["mode"], "readonly_display")

    def test_non_boolean_or_other_workspace_stays_closed(self):
        report = _report([_failure(f"r{index}", APPROVED_WORKSPACE_ID) for index in range(3)])
        with patch("run_metrics.shadow.build_shadow") as mocked:
            text = evaluate_s8(report, _config(enabled="true"))
            other = evaluate_s8(report, _config(workspace_id=OTHER_WORKSPACE_ID))
            wrong_mode = evaluate_s8(report, _config(mode="dispatch"))
        mocked.assert_not_called()
        self.assertEqual(text["status"], "invalid_config")
        self.assertEqual(other["status"], "out_of_scope")
        self.assertEqual(wrong_mode["status"], "invalid_config")
        self.assertEqual(text["suggestions"], [])
        self.assertEqual(other["suggestions"], [])
        self.assertEqual(wrong_mode["suggestions"], [])


class ScopeTests(unittest.TestCase):
    def test_only_the_approved_workspace_can_produce_a_suggestion(self):
        approved = [_failure(f"a{index}", APPROVED_WORKSPACE_ID) for index in range(3)]
        other = [_failure(f"b{index}", OTHER_WORKSPACE_ID, runtime_id="rt-other") for index in range(3)]
        report = _report([*approved, *other])
        before = json.dumps(report, sort_keys=True)
        result = evaluate_s8(report, _config())
        self.assertEqual(json.dumps(report, sort_keys=True), before)
        self.assertTrue(result["enabled"])
        self.assertEqual(result["in_scope_run_count"], 3)
        self.assertEqual(result["excluded_other_workspace_run_count"], 3)
        self.assertEqual(len(result["suggestions"]), 1)
        suggestion = result["suggestions"][0]
        self.assertEqual(suggestion["runtime_id"], "rt-hermes")
        self.assertEqual(suggestion["suggested_route"]["applied"], False)
        self.assertFalse(suggestion["comparison"]["dispatch_changed"])
        self.assertEqual(result["writes"], [])
        self.assertEqual(result["write_endpoints_added"], [])
        self.assertFalse(result["dispatch_changed"])
        self.assertFalse(result["routing_shadow"]["dispatch_changed"])
        self.assertEqual(result["routing_shadow"]["writes"], [])
        self.assertEqual(result["audit"]["s7_implementation"], S7_IMPLEMENTATION)
        self.assertEqual(result["audit"]["s7_merge"], S7_MERGE)
        self.assertEqual(result["audit"]["runtime_changes"], [])
        self.assertEqual(result["audit"]["account_changes"], [])
        self.assertEqual(result["rollback"]["config_written_to_production"], False)

    def test_an_extra_workspace_list_does_not_widen_scope(self):
        approved = [_failure(f"a{index}", APPROVED_WORKSPACE_ID) for index in range(3)]
        other = [_failure(f"b{index}", OTHER_WORKSPACE_ID, runtime_id="rt-other") for index in range(3)]
        report = _report([*approved, *other])
        result = evaluate_s8(report, _config(workspace_ids=[APPROVED_WORKSPACE_ID, OTHER_WORKSPACE_ID]))
        self.assertEqual(result["status"], "enabled")
        self.assertEqual(len(result["suggestions"]), 1)
        self.assertEqual(result["suggestions"][0]["runtime_id"], "rt-hermes")
        self.assertEqual(result["excluded_other_workspace_run_count"], 3)

    def test_other_workspace_alone_generates_no_suggestion(self):
        report = _report([_failure(f"b{index}", OTHER_WORKSPACE_ID) for index in range(3)])
        result = evaluate_s8(report, _config())
        self.assertTrue(result["enabled"])
        self.assertEqual(result["in_scope_run_count"], 0)
        self.assertEqual(result["excluded_other_workspace_run_count"], 3)
        self.assertEqual(result["suggestions"], [])
        self.assertEqual(build_shadow(report)["suggestions"][0]["runtime_id"], "rt-hermes")

    def test_missing_workspace_id_is_outside_the_approved_scope(self):
        run = _failure("m1", APPROVED_WORKSPACE_ID)
        del run["workspace_id"]
        report = _report([run, run, run])
        result = evaluate_s8(report, _config())
        self.assertEqual(result["suggestions"], [])
        self.assertEqual(result["in_scope_run_count"], 0)
        self.assertEqual(result["excluded_other_workspace_run_count"], 3)


if __name__ == "__main__":
    unittest.main()
