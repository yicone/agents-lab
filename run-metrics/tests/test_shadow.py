"""Shadow routes stay advisory and leave the source report unchanged."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from run_metrics.__main__ import main
from run_metrics.project import project_run
from run_metrics.shadow import build_shadow

THRESHOLD = [
    "同一 runtime × 同一错误签名，滚动 24h 内失败至少 3 条；",
    "排除 quota / capacity / access_denied；",
    "仅记录建议路由，不改实际派单、不写入口、不新增写入口。",
]
WINDOW = {"name": "24h", "start": "2026-10-28T02:00:00Z", "end": "2026-10-29T02:00:00Z"}


def _report(runs, signals=None, **extra):
    payload = {
        "schema_version": "run-metrics.v0.1",
        "generated_at": WINDOW["end"],
        "windows": {"24h": dict(WINDOW), "7d": dict(WINDOW), "14d": dict(WINDOW)},
        "runs": runs,
        "scheduling": {
            "unknown_hard_disable": False,
            "historical_quota_excludes_agent": False,
            "excluded_agent_ids": [],
            "historical_quota_agent_ids": ["agent-quota"],
            "signals": signals if signals is not None else _signals(),
        },
        "provider_unstable_class": {"emitted": False, "run_count": 0},
    }
    payload.update(extra)
    return payload


def _signals():
    return [
        _signal("rt-hermes", "online", "hermes"),
        _signal("rt-cursor", "online", "cursor"),
        _signal("rt-bad", "online", "codex", health="exhausted"),
        _signal("rt-off", "offline", "codex"),
    ]


def _signal(runtime_id, status, provider, health="unknown"):
    return {
        "subject_type": "runtime",
        "subject_id": runtime_id,
        "provider": provider,
        "name": provider,
        "connection_status": status,
        "health": health,
        "quota_remaining": None,
        "hard_disable": False,
    }


def _projected(run_id, runtime_id, *, reason, error, start="2026-10-29T01:00:00Z", status="failed"):
    raw = project_run({
        "id": run_id,
        "issue_id": f"issue-{run_id}",
        "agent_id": f"agent-{runtime_id}",
        "runtime_id": runtime_id,
        "workspace_id": "ws-1",
        "status": status,
        "created_at": start,
        "started_at": start,
        "failure_reason": reason,
        "error": error,
    })
    return raw


def _server_error(run_id, runtime_id="rt-hermes", start="2026-10-29T01:00:00Z", status="failed"):
    return _projected(
        run_id,
        runtime_id,
        reason="agent_error.provider_server_error",
        error="hermes session/resume failed: Internal error (code=-32603)",
        start=start,
        status=status,
    )


class ShadowTests(unittest.TestCase):
    def test_three_matching_failures_suggest_avoid_without_dispatch(self):
        runs = [_server_error("r1"), _server_error("r2"), _server_error("r3")]
        before = json.dumps(runs)
        classes = [run["failure_class"] for run in runs]
        report = _report(runs)
        shadow = build_shadow(report)
        self.assertEqual(json.dumps(runs), before)
        self.assertEqual([run["failure_class"] for run in runs], classes)
        self.assertNotIn("provider_unstable", classes)
        self.assertEqual(report["provider_unstable_class"]["emitted"], False)
        self.assertEqual(shadow["status"], "ok")
        self.assertEqual(shadow["writes"], [])
        self.assertEqual(shadow["write_endpoints_added"], [])
        self.assertFalse(shadow["dispatch_changed"])
        self.assertFalse(shadow["failure_class_rewritten"])
        self.assertFalse(shadow["provider_unstable_failure_class_emitted"])
        self.assertEqual(shadow["audit"]["threshold_text"], THRESHOLD)
        self.assertEqual(shadow["audit"]["approval_comment_id"], "01a1855b-fba3-72bc-96d5-4c007113d6dc")
        self.assertEqual(len(shadow["suggestions"]), 1)
        suggestion = shadow["suggestions"][0]
        self.assertEqual(suggestion["mark"], "provider_unstable")
        self.assertEqual(suggestion["signature"], "agent_error.provider_server_error")
        self.assertEqual(suggestion["sample_count"], 3)
        self.assertEqual(suggestion["suggested_route"]["action"], "avoid_runtime")
        self.assertEqual(suggestion["suggested_route"]["avoid_runtime_id"], "rt-hermes")
        self.assertEqual(suggestion["suggested_route"]["alternative_runtime_ids"], ["rt-cursor"])
        self.assertFalse(suggestion["suggested_route"]["applied"])
        self.assertFalse(suggestion["suggested_route"]["quota_used_as_reason"])
        self.assertTrue(suggestion["comparison"]["diverges"])
        self.assertFalse(suggestion["comparison"]["dispatch_changed"])
        self.assertEqual(
            [row["runtime_id"] for row in suggestion["actual_dispatches"]],
            ["rt-hermes", "rt-hermes", "rt-hermes"],
        )
        quota = {row["runtime_id"]: row for row in shadow["quota_suggestions"]}
        self.assertEqual(quota["rt-hermes"]["suggested_action"], "do_not_reroute")
        self.assertFalse(quota["rt-hermes"]["hard_disable"])
        self.assertFalse(shadow["historical_quota"]["shadow_excludes_agent"])
        self.assertEqual(shadow["historical_quota"]["source_agent_ids"], ["agent-quota"])
        forced = _report(runs)
        forced["scheduling"]["historical_quota_excludes_agent"] = True
        forced_shadow = build_shadow(forced)
        self.assertTrue(forced_shadow["historical_quota"]["source_excludes_agent"])
        self.assertFalse(forced_shadow["historical_quota"]["shadow_excludes_agent"])
        self.assertEqual(forced_shadow["historical_quota"]["suggested_action"], "keep_actual")
        self.assertEqual(shadow["rollback"]["method"], "discard_shadow_artifact")
        self.assertFalse(shadow["rollback"]["config_written"])

    def test_two_failures_stay_below_threshold(self):
        shadow = build_shadow(_report([_server_error("r1"), _server_error("r2")]))
        self.assertEqual(shadow["suggestions"], [])
        self.assertEqual(shadow["below_threshold"][0]["sample_count"], 2)
        self.assertEqual(shadow["below_threshold"][0]["suggested_action"], "keep_actual")
        self.assertIsNone(shadow["below_threshold"][0]["mark"])

    def test_excluded_classes_never_mark_provider_unstable(self):
        cases = [
            ("quota", "agent_error.provider_quota_limit", "HTTP 503 usage limit"),
            ("capacity", "agent_error.provider_capacity_or_rate_limit", "at capacity"),
            ("access_denied", "agent_error.access_denied", "access denied"),
        ]
        for klass, reason, error in cases:
            runs = [
                _projected(f"{klass}-{index}", "rt-hermes", reason=reason, error=error)
                for index in range(3)
            ]
            self.assertEqual({run["failure_class"] for run in runs}, {klass})
            shadow = build_shadow(_report(runs))
            self.assertEqual(shadow["suggestions"], [], klass)
            self.assertEqual(shadow["excluded_groups"][0]["reason"], f"excluded_{klass}")
            self.assertEqual(shadow["excluded_groups"][0]["suggested_action"], "keep_actual")
            self.assertFalse(shadow["excluded_groups"][0]["dispatch_changed"])

    def test_config_geo_and_generic_provider_text_do_not_mark(self):
        config = [
            _projected(
                f"cfg-{index}", "rt-cursor",
                reason="api_invalid_request",
                error="The model is not supported when using Codex.",
            )
            for index in range(3)
        ]
        geo = [
            _projected(
                f"geo-{index}", "rt-cursor",
                reason="agent_error.unknown",
                error="User location is not supported for the API use.",
            )
            for index in range(3)
        ]
        generic = [
            _projected(
                f"gen-{index}", "rt-cursor",
                reason="agent_error.unknown",
                error="An error occurred while processing your request",
            )
            for index in range(3)
        ]
        for runs in (config, geo, generic):
            shadow = build_shadow(_report(runs))
            self.assertEqual(shadow["suggestions"], [])

    def test_same_signature_on_different_runtimes_does_not_combine(self):
        runs = [
            _server_error("a1", "rt-hermes"),
            _server_error("a2", "rt-hermes"),
            _server_error("b1", "rt-cursor"),
        ]
        shadow = build_shadow(_report(runs))
        self.assertEqual(shadow["suggestions"], [])
        counts = {row["runtime_id"]: row["sample_count"] for row in shadow["below_threshold"]}
        self.assertEqual(counts, {"rt-cursor": 1, "rt-hermes": 2})

    def test_outside_window_and_blocked_do_not_count(self):
        runs = [
            _server_error("old", start="2026-10-27T01:00:00Z"),
            _server_error("r2"),
            _server_error("r3"),
            _server_error("blocked", status="blocked"),
        ]
        shadow = build_shadow(_report(runs))
        self.assertEqual(shadow["suggestions"], [])
        self.assertEqual(shadow["below_threshold"][0]["sample_count"], 2)

    def test_unclassified_http_5xx_uses_candidate_signature(self):
        runs = [
            _projected(f"p{index}", "rt-hermes", reason=None, error="HTTP 503 service unavailable")
            for index in range(3)
        ]
        self.assertEqual({run["failure_class"] for run in runs}, {"unknown"})
        shadow = build_shadow(_report(runs))
        self.assertEqual(shadow["suggestions"][0]["signature"], "PC-001-HTTP-5XX")
        self.assertEqual(shadow["suggestions"][0]["signature_source"], "candidate_signal")

    def test_classified_process_failure_is_not_relabelled(self):
        runs = [
            _projected(
                f"proc-{index}", "rt-hermes",
                reason="agent_error.process_failure",
                error="cursor-agent exited with error: exit status 1",
            )
            for index in range(3)
        ]
        self.assertEqual({run["failure_class"] for run in runs}, {"process_failed"})
        shadow = build_shadow(_report(runs))
        self.assertEqual(shadow["suggestions"], [])
        self.assertEqual(shadow["not_provider_groups"][0]["failure_class"], "process_failed")

    def test_unknown_quota_does_not_hard_disable_and_exhausted_stays_advisory(self):
        signals = [
            _signal("rt-a", "online", "codex", health="unknown"),
            _signal("rt-b", "online", "cursor", health="exhausted"),
        ]
        shadow = build_shadow(_report([], signals=signals))
        by_id = {row["runtime_id"]: row for row in shadow["quota_suggestions"]}
        self.assertEqual(by_id["rt-a"]["suggested_action"], "do_not_reroute")
        self.assertFalse(by_id["rt-a"]["hard_disable"])
        self.assertFalse(by_id["rt-a"]["applied"])
        self.assertEqual(by_id["rt-b"]["suggested_action"], "prefer_other_runtime")
        self.assertFalse(by_id["rt-b"]["applied"])
        self.assertFalse(by_id["rt-b"]["dispatch_changed"])

    def test_no_online_alternative_is_recorded_and_not_applied(self):
        runs = [_server_error(f"r{index}") for index in range(3)]
        signals = [_signal("rt-hermes", "online", "hermes"), _signal("rt-off", "offline", "codex")]
        shadow = build_shadow(_report(runs, signals=signals))
        route = shadow["suggestions"][0]["suggested_route"]
        self.assertEqual(route["action"], "no_alternative")
        self.assertEqual(route["alternative_runtime_ids"], [])
        self.assertFalse(route["applied"])

    def test_missing_inputs_do_not_invent_a_healthy_zero(self):
        missing_runs = _report([])
        del missing_runs["runs"]
        shadow = build_shadow(missing_runs)
        self.assertEqual(shadow["status"], "missing_runs")
        self.assertEqual(shadow["suggestions"], [])
        self.assertEqual(shadow["signal_snapshot"], "missing")
        self.assertEqual(shadow["audit"]["threshold_text"], THRESHOLD)
        broken = dict(_report([]))
        del broken["windows"]
        self.assertEqual(build_shadow(broken)["status"], "missing_window")
        self.assertEqual(build_shadow({"schema_version": "other"})["status"], "schema")
        self.assertEqual(build_shadow(None)["status"], "invalid")

    def test_cli_leaves_the_input_file_unchanged(self):
        report = _report([_server_error(f"r{index}") for index in range(3)])
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "report.json"
            output = Path(tmp) / "shadow.json"
            source.write_text(json.dumps(report), encoding="utf-8")
            before = source.read_bytes()
            code = main(["shadow", "--input", str(source), "--output", str(output)])
            self.assertEqual(code, 0)
            self.assertEqual(source.read_bytes(), before)
            loaded = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(loaded["suggestions"][0]["mark"], "provider_unstable")
            self.assertEqual(loaded["writes"], [])
            with self.assertRaises(SystemExit):
                main(["shadow", "--input", str(source), "--output", str(source)])
            self.assertEqual(source.read_bytes(), before)

    def test_source_does_not_call_multica_or_open_a_write_route(self):
        root = Path(__file__).resolve().parents[1]
        shadow_source = (root / "run_metrics" / "shadow.py").read_text(encoding="utf-8")
        server = (root.parent / "multica-board" / "backend" / "multica_board" / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess", shadow_source)
        self.assertNotIn("multica", shadow_source)
        self.assertNotIn("classify_failure", shadow_source)
        self.assertNotIn("from .rules", shadow_source)
        self.assertNotIn("build_shadow", server)
        self.assertNotIn("routing-shadow", server)

    def test_cli_rejects_invalid_json_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "broken.json"
            output = Path(tmp) / "shadow.json"
            source.write_text("{", encoding="utf-8")
            with self.assertRaises(SystemExit):
                main(["shadow", "--input", str(source), "--output", str(output)])
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
