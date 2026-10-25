"""Contract tests for the read-only run-metrics prototype."""

from __future__ import annotations

import unittest

from run_metrics.collect import ReadOnlyViolation, assert_readonly, collect_snapshot
from run_metrics.project import project_run
from run_metrics.report import build_report
from run_metrics.rework import infer_rework
from run_metrics.rules import candidate_ids, classify_failure
from run_metrics.timeutil import parse_ts

NOW = "2026-10-25T16:00:00Z"


def _issue(issue_id, created, updated=None):
    return {
        "id": issue_id,
        "identifier": issue_id.upper(),
        "status": "in_progress",
        "created_at": created,
        "updated_at": updated or created,
        "workspace_id": "ws-1",
    }


def _run(run_id, issue_id, status, start, end=None, **extra):
    row = {
        "id": run_id,
        "issue_id": issue_id,
        "agent_id": extra.pop("agent_id", "agent-1"),
        "runtime_id": "runtime-1",
        "workspace_id": "ws-1",
        "status": status,
        "created_at": start,
        "started_at": start,
        "completed_at": end,
        "attempt": extra.pop("attempt", 1),
    }
    row.update(extra)
    return row


def _snapshot():
    runtimes = [
        {"id": f"rt-{index}", "name": f"Runtime {index}", "provider": "codex", "status": "online"}
        for index in range(10)
    ]
    return {
        "collected_at": NOW,
        "mode": "fixture",
        "workspace_id": "ws-1",
        "issues": [
            _issue("a", "2026-10-25T15:00:00Z"),
            _issue("b", "2026-10-25T15:10:00Z"),
            _issue("c", "2026-10-25T14:00:00Z"),
            _issue("d", "2026-10-25T12:00:00Z"),
            _issue("e", "2026-10-25T15:30:00Z"),
            _issue("f", "2026-10-25T15:40:00Z"),
            _issue("g", "2026-10-25T15:41:00Z"),
            _issue("h", "2026-10-25T15:42:00Z"),
            _issue("i", "2026-10-25T15:50:00Z"),
            _issue("j", "2026-10-20T00:00:00Z"),
        ],
        "runs": [
            _run("a1", "a", "completed", "2026-10-25T15:00:00Z", "2026-10-25T15:00:10Z"),
            _run(
                "b1", "b", "failed", "2026-10-25T15:10:00Z", "2026-10-25T15:11:00Z",
                agent_id="agent-quota",
                error="usage limit resets at 2026-10-26T00:00:00Z",
            ),
            _run("c1", "c", "completed", "2026-10-25T14:00:00Z", "2026-10-25T14:10:00Z"),
            _run("c2", "c", "completed", "2026-10-25T15:00:00Z", "2026-10-25T15:05:00Z"),
            _run("d1", "d", "completed", "2026-10-25T12:00:00Z", "2026-10-25T12:05:00Z"),
            _run("d2", "d", "completed", "2026-10-25T13:00:00Z", "2026-10-25T13:05:00Z", attempt=2),
            _run(
                "e1", "e", "completed", "2026-10-25T15:30:00Z", "2026-10-25T15:31:00Z",
                attempt=2, parent_run_id="parent-e",
            ),
            _run(
                "f1", "f", "cancelled", "2026-10-25T15:40:00Z", "2026-10-25T15:41:00Z",
                error="usage limit",
            ),
            _run(
                "g1", "g", "failed", "2026-10-25T15:41:00Z", "2026-10-25T15:42:00Z",
                error="503 An error occurred while processing your request",
            ),
            _run(
                "h1", "h", "failed", "2026-10-25T15:42:00Z", "2026-10-25T15:43:00Z",
                error="server is at capacity",
            ),
            _run("i1", "i", "running", "2026-10-25T15:50:00Z"),
            _run("j1", "j", "completed", "2026-10-20T00:00:00Z", "2026-10-20T00:01:00Z"),
        ],
        "status_changes": [
            {
                "issue_id": "c",
                "id": "chg-c",
                "created_at": "2026-10-25T14:30:00Z",
                "from": "in_progress",
                "to": "blocked",
            },
            {
                "issue_id": "d",
                "id": "chg-d",
                "created_at": "2026-10-25T12:30:00Z",
                "from": None,
                "to": None,
            },
        ],
        "comments": [],
        "runtimes": runtimes,
        "usage_probes": [
            {"runtime_id": runtime["id"], "days": 1, "record_count": 0, "quota_remaining_present": False}
            for runtime in runtimes
        ],
    }


class ReportContractTest(unittest.TestCase):
    def setUp(self):
        self.report = build_report(_snapshot())
        self.window = self.report["windows"]["14d"]
        self.day = self.report["windows"]["24h"]

    def test_schema_and_windows(self):
        self.assertEqual(self.report["schema_version"], "run-metrics.v0.1")
        self.assertEqual(self.report["rule_version"], "failure-rules.v0.2")
        self.assertEqual(list(self.report["windows"]), ["24h", "7d", "14d"])
        self.assertEqual(self.window["issue_count"], 10)
        self.assertEqual(self.window["run_count"], 12)
        self.assertEqual(self.day["issue_count"], 9)
        self.assertEqual(self.day["run_count"], 11)

    def test_provider_unstable_is_not_emitted(self):
        unstable = self.report["provider_unstable_class"]
        self.assertFalse(unstable["emitted"])
        self.assertEqual(unstable["run_count"], 0)
        self.assertIsNone(unstable["threshold_proposal"])
        self.assertNotIn("provider_unstable", self.window["failure_class_counts"])
        classes = {run["failure_class"] for run in self.report["runs"]}
        self.assertNotIn("provider_unstable", classes)
        generic = next(run for run in self.report["runs"] if run["run_id"] == "g1")
        self.assertEqual(generic["failure_class"], "unknown")
        self.assertEqual(generic["failure_rule_id"], "FR-090-UNMATCHED")
        self.assertEqual(generic["failure_evidence"]["rule_id"], "FR-090-UNMATCHED")
        self.assertEqual(generic["provider_candidate_ids"], ["PC-001-HTTP-5XX", "PC-003-GENERIC-PROVIDER"])
        candidates = {row["id"]: row["sample_count"] for row in unstable["candidates"]}
        self.assertEqual(candidates["PC-001-HTTP-5XX"], 1)
        self.assertEqual(candidates["PC-003-GENERIC-PROVIDER"], 1)
        server = project_run({
            "id": "server-1",
            "status": "failed",
            "failure_reason": "agent_error.provider_server_error",
            "created_at": "2026-10-25T15:00:00Z",
            "started_at": "2026-10-25T15:00:00Z",
            "completed_at": "2026-10-25T15:01:00Z",
        })
        self.assertEqual(server["failure_class"], "unknown")
        self.assertEqual(server["failure_rule_id"], "FR-090-UNMATCHED")
        self.assertIn("PC-004-PROVIDER-SERVER-REASON", server["provider_candidate_ids"])

    def test_rule_hits_keep_evidence_and_version(self):
        hits = {row["id"]: row["hit_count"] for row in self.report["failure_rules"]["rules"]}
        self.assertEqual(hits["FR-001-CANCELLED-STATUS"], 1)
        self.assertEqual(hits["FR-004-QUOTA-TEXT"], 1)
        self.assertEqual(hits["FR-006-CAPACITY-TEXT"], 1)
        self.assertEqual(hits["FR-090-UNMATCHED"], 1)
        self.assertEqual(hits["FR-010-PROCESS"], 0)
        self.assertTrue(all(row["rule_version"] == "failure-rules.v0.2" for row in self.report["failure_rules"]["rules"]))
        quota = next(run for run in self.report["runs"] if run["run_id"] == "b1")
        self.assertEqual(quota["failure_class"], "quota")
        self.assertIs(quota["failure_recoverable"], True)
        self.assertEqual(quota["recovery_at"], "2026-10-26T00:00:00Z")
        self.assertIn("usage limit", quota["failure_evidence"]["excerpt"])
        cancelled = next(run for run in self.report["runs"] if run["run_id"] == "f1")
        self.assertEqual(cancelled["failure_class"], "cancelled")
        self.assertEqual(cancelled["failure_rule_id"], "FR-001-CANCELLED-STATUS")
        capacity = next(run for run in self.report["runs"] if run["run_id"] == "h1")
        self.assertEqual(capacity["failure_class"], "capacity")

    def test_dual_success_rates(self):
        including = self.window["success_rate"]["including_cancelled"]
        excluding = self.window["success_rate"]["excluding_cancelled"]
        self.assertEqual((including["numerator"], including["denominator"]), (7, 11))
        self.assertEqual((excluding["numerator"], excluding["denominator"]), (7, 10))
        day_including = self.day["success_rate"]["including_cancelled"]
        day_excluding = self.day["success_rate"]["excluding_cancelled"]
        self.assertEqual((day_including["numerator"], day_including["denominator"]), (6, 10))
        self.assertEqual((day_excluding["numerator"], day_excluding["denominator"]), (6, 9))
        self.assertEqual(self.window["running_count"], 1)
        self.assertEqual(self.window["completed_count"], self.window["succeeded_count"])

    def test_rework_coverage_withholds_point_rate(self):
        coverage = self.window["rework"]["rework_coverage"]
        self.assertEqual(coverage["explicit_count"], 1)
        self.assertEqual(coverage["inferred_count"], 1)
        self.assertEqual(coverage["unknown_count"], 1)
        self.assertIsNone(self.window["rework"]["rework_rate"])
        self.assertEqual(self.window["rework"]["rework_rate_claim"], "lower_bound_only")
        lower = self.window["rework"]["rework_rate_lower_bound"]
        self.assertEqual((lower["numerator"], lower["denominator"]), (2, 10))
        self.assertEqual(self.window["groups"]["by_issue"]["c"]["rework_confidence"], "inferred")
        self.assertEqual(self.window["groups"]["by_issue"]["e"]["rework_confidence"], "explicit")
        self.assertEqual(self.window["groups"]["by_issue"]["d"]["rework_confidence"], "unknown")

    def test_quota_history_does_not_exclude_and_unknown_is_not_disabled(self):
        scheduling = self.report["scheduling"]
        self.assertFalse(scheduling["unknown_hard_disable"])
        self.assertFalse(scheduling["historical_quota_excludes_agent"])
        self.assertEqual(scheduling["excluded_agent_ids"], [])
        self.assertEqual(scheduling["historical_quota_agent_ids"], ["agent-quota"])
        self.assertTrue(scheduling["signals"])
        self.assertTrue(all(signal["health"] == "unknown" for signal in scheduling["signals"]))
        self.assertTrue(all(signal["hard_disable"] is False for signal in scheduling["signals"]))
        self.assertTrue(all(signal["quota_remaining"] is None for signal in scheduling["signals"]))

    def test_runtime_reconciliation_records_s1_gap(self):
        recon = self.report["runtime_reconciliation"]
        self.assertEqual(recon["observed_count"], 10)
        self.assertEqual(recon["online_count"], 10)
        self.assertEqual(recon["s1_claim"]["runtime_count"], 12)
        self.assertFalse(recon["quota_remaining_present"])
        self.assertFalse(recon["atlas_gemini_display"]["rechecked_in_atlas_ui"])
        text = " ".join(recon["discrepancies"])
        self.assertIn("12", text)
        self.assertIn("gemini", text)
        self.assertTrue(recon["unverified"])
        self.assertIn("from/to", self.report["timeline_from_to"]["compared_with_s1"])
        self.assertEqual(self.report["timeline_from_to"]["with_from_to"], 1)
        self.assertEqual(self.report["timeline_from_to"]["without_from_to"], 1)

    def test_duration_requires_both_timestamps(self):
        succeeded = next(run for run in self.report["runs"] if run["run_id"] == "a1")
        self.assertEqual(succeeded["status"], "succeeded")
        self.assertEqual(succeeded["duration_ms"], 10000)
        self.assertIsNone(succeeded["failure_class"])
        running = next(run for run in self.report["runs"] if run["run_id"] == "i1")
        self.assertIsNone(running["duration_ms"])
        self.assertGreater(self.window["field_coverage"]["ended_at"]["missing_count"], 0)
        backwards = project_run({
            "id": "back",
            "status": "completed",
            "started_at": "2026-10-25T15:00:00Z",
            "completed_at": "2026-10-25T14:00:00Z",
            "created_at": "2026-10-25T14:00:00Z",
        })
        self.assertIsNone(backwards["duration_ms"])


class ReworkAndCollectTest(unittest.TestCase):
    def test_acceptance_comment_is_inferred(self):
        issue = _issue("k", "2026-10-25T10:00:00Z")
        runs = [
            project_run(_run("k1", "k", "completed", "2026-10-25T10:00:00Z", "2026-10-25T10:05:00Z")),
            project_run(_run("k2", "k", "completed", "2026-10-25T12:00:00Z", "2026-10-25T12:05:00Z")),
        ]
        inferred = infer_rework(
            [issue],
            runs,
            [{
                "issue_id": "k",
                "id": "chg-k",
                "created_at": "2026-10-25T11:00:00Z",
                "from": "in_review",
                "to": "in_progress",
            }],
            [{
                "issue_id": "k",
                "id": "cmt-k",
                "created_at": "2026-10-25T11:05:00Z",
                "content": "验收退回，请修改后再交",
            }],
            start=parse_ts("2026-10-11T16:00:00Z"),
            end=parse_ts(NOW),
        )
        self.assertEqual(inferred["issue_confidence"]["k"], "inferred")
        reasons = {chain["reason"] for chain in inferred["chains"]}
        self.assertIn("acceptance_return", reasons)

    def test_point_rate_when_nothing_is_unknown(self):
        snapshot = _snapshot()
        snapshot["issues"] = [issue for issue in snapshot["issues"] if issue["id"] == "e"]
        snapshot["runs"] = [run for run in snapshot["runs"] if run["issue_id"] == "e"]
        snapshot["status_changes"] = []
        report = build_report(snapshot)
        rework = report["windows"]["14d"]["rework"]
        self.assertEqual(rework["rework_coverage"]["unknown_count"], 0)
        self.assertEqual(rework["rework_rate_claim"], "point")
        self.assertEqual(rework["rework_rate"]["numerator"], 1)
        self.assertEqual(rework["rework_rate"]["denominator"], 1)

    def test_readonly_guard_and_collect(self):
        with self.assertRaises(ReadOnlyViolation):
            assert_readonly(["issue", "comment", "add", "ACM-1"])
        with self.assertRaises(ReadOnlyViolation):
            assert_readonly(["runtime", "update", "rt-1"])
        assert_readonly(["issue", "runs", "issue-1"])
        calls = []

        def runner(args):
            calls.append(list(args))
            assert_readonly(args)
            if args[:2] == ["issue", "list"]:
                return {"data": {
                    "has_more": False,
                    "total": 1,
                    "issues": [_issue("a", "2026-10-25T15:00:00Z")],
                }, "stderr": ""}
            if args[:2] == ["issue", "runs"]:
                return {"data": [
                    _run("a1", "a", "completed", "2026-10-25T15:00:00Z", "2026-10-25T15:00:10Z"),
                    _run("a2", "a", "failed", "2026-10-25T15:10:00Z", "2026-10-25T15:11:00Z", error="token=sekret usage limit"),
                ], "stderr": ""}
            if args[:2] == ["issue", "timeline"]:
                return {"data": [{
                    "id": "chg-a",
                    "action": "status_changed",
                    "created_at": "2026-10-25T15:05:00Z",
                    "details": {"from": "in_progress", "to": "blocked"},
                }], "stderr": "history truncated at server cap"}
            if args[:3] == ["issue", "comment", "list"]:
                return {"data": [], "stderr": ""}
            if args[:2] == ["runtime", "list"]:
                return {"data": [{"id": "rt-1", "name": "Codex", "provider": "codex", "status": "online"}], "stderr": ""}
            if args[:2] == ["runtime", "usage"]:
                return {"data": [], "stderr": ""}
            raise AssertionError(args)

        snapshot = collect_snapshot(now=parse_ts(NOW), runner=runner, workers=1)
        self.assertEqual(snapshot["issue_scan"]["in_default_window"], 1)
        self.assertEqual(len(snapshot["runs"]), 2)
        self.assertNotIn("sekret", snapshot["runs"][1]["error"])
        self.assertIn("[redacted]", snapshot["runs"][1]["error"])
        self.assertEqual(snapshot["timeline_truncated_ids"], ["a"])
        self.assertTrue(snapshot["status_changes"][0]["from"])
        self.assertFalse(any("add" in call for call in calls))
        report = build_report(snapshot)
        self.assertEqual(report["windows"]["14d"]["rework"]["rework_coverage"]["inferred_count"], 1)
        self.assertEqual(report["provider_unstable_class"]["run_count"], 0)
        self.assertIsNone(report["provider_unstable_class"]["threshold_proposal"])
        self.assertIsNone(report["provider_unstable_class"]["deferred_to"])


# S4 14 天窗口里的 16 条 unknown，外加 2 条被记成 process_failed 的模型配置误判。
# 文本只保留签名，不带回放快照里的地址、令牌或 error id。
_S4_REPLAY = (
    (
        "01a16667-03c4-789f-b3ed-d606c9663828",
        "agent_error.unknown",
        "FAILED_PRECONDITION (code 400): User location is not supported for the API use.",
        "unknown", "FR-090-UNMATCHED",
        "access_denied", "FR-012-GEO-ACCESS",
        [],
    ),
    (
        "01a1665e-9845-794c-b7a9-f7ab899c0422",
        "agent_error.unknown",
        "User location is not supported for the API use.",
        "unknown", "FR-090-UNMATCHED",
        "access_denied", "FR-012-GEO-ACCESS",
        [],
    ),
    (
        "01a15fe8-ca58-77b9-b32c-ed1fcdfce44a",
        "agent_error.unknown",
        "User location is not supported for the API use.",
        "unknown", "FR-090-UNMATCHED",
        "access_denied", "FR-012-GEO-ACCESS",
        [],
    ),
    (
        "01a15fde-25a6-7078-9dbe-9d4dc93d625b",
        "agent_error.unknown",
        "User location is not supported for the API use.",
        "unknown", "FR-090-UNMATCHED",
        "access_denied", "FR-012-GEO-ACCESS",
        [],
    ),
    (
        "01a15fd5-a0ac-79f9-83bd-666d69194944",
        "agent_error.unknown",
        "location is not supported for the API use.",
        "unknown", "FR-090-UNMATCHED",
        "access_denied", "FR-012-GEO-ACCESS",
        [],
    ),
    (
        "01a15a39-5c66-7c8c-98b2-4077faf27206",
        "agent_error.unknown",
        "User location is not supported for the API use.",
        "unknown", "FR-090-UNMATCHED",
        "access_denied", "FR-012-GEO-ACCESS",
        [],
    ),
    (
        "01a15a39-0f84-713f-bac2-fbeecf7b432a",
        "agent_error.unknown",
        "User location is not supported for the API use.",
        "unknown", "FR-090-UNMATCHED",
        "access_denied", "FR-012-GEO-ACCESS",
        [],
    ),
    (
        "01a15a36-c64c-7123-a815-c4d6bec1c1e0",
        "agent_error.unknown",
        "User location is not supported for the API use.",
        "unknown", "FR-090-UNMATCHED",
        "access_denied", "FR-012-GEO-ACCESS",
        [],
    ),
    (
        "01a143b7-cc26-71e8-8110-9fa300d0e4dd",
        "agent_error.unknown",
        "User location is not supported for the API use.",
        "unknown", "FR-090-UNMATCHED",
        "access_denied", "FR-012-GEO-ACCESS",
        [],
    ),
    (
        "01a15d4a-c2d7-79da-87e5-c7c9c908e10d",
        "agent_error.provider_server_error",
        "hermes session/resume failed: Internal error (code=-32603)",
        "unknown", "FR-090-UNMATCHED",
        "unknown", "FR-090-UNMATCHED",
        ["PC-004-PROVIDER-SERVER-REASON"],
    ),
    (
        "01a15cf8-713c-73bc-9e9b-f88c0940b58f",
        "agent_error.provider_server_error",
        "hermes session/resume failed: Internal error (code=-32603)",
        "unknown", "FR-090-UNMATCHED",
        "unknown", "FR-090-UNMATCHED",
        ["PC-004-PROVIDER-SERVER-REASON"],
    ),
    (
        "01a15d7e-8ece-7c27-ae38-a251f348197e",
        "agent_error.provider_server_error",
        "hermes session/resume failed: Internal error (code=-32603)",
        "unknown", "FR-090-UNMATCHED",
        "unknown", "FR-090-UNMATCHED",
        ["PC-004-PROVIDER-SERVER-REASON"],
    ),
    (
        "01a13975-8352-71ab-a42f-19a049499b26",
        "runtime_recovery",
        "daemon restarted while task was in flight",
        "unknown", "FR-090-UNMATCHED",
        "runtime_recovery", "FR-015-RUNTIME-RECOVERY",
        [],
    ),
    (
        "01a13975-5f63-7ac9-a073-739b9f20e065",
        "runtime_recovery",
        "daemon restarted while task was in flight",
        "unknown", "FR-090-UNMATCHED",
        "runtime_recovery", "FR-015-RUNTIME-RECOVERY",
        [],
    ),
    (
        "01a16a5d-7753-7cd3-9f05-3b984418bd60",
        "api_invalid_request",
        "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.",
        "unknown", "FR-090-UNMATCHED",
        "config_failed", "FR-013-CONFIG-REASON",
        [],
    ),
    (
        "01a13df9-004d-7258-b87a-2a8abf1c8243",
        "agent_error.unknown",
        "request failed: write: broken pipe",
        "unknown", "FR-090-UNMATCHED",
        "unknown", "FR-090-UNMATCHED",
        ["PC-002-CONNECTION"],
    ),
    (
        "01a16e1f-27b0-7bdb-a2f7-8b6d302b3a9f",
        "agent_error.process_failure",
        'copilot exited with error: exit status 1; Error: Model "gpt-5.4" from --model flag is not available.',
        "process_failed", "FR-010-PROCESS",
        "config_failed", "FR-014-CONFIG-TEXT",
        [],
    ),
    (
        "01a16e1b-a387-748d-acf0-f96075b54b52",
        "agent_error.process_failure",
        'copilot exited with error: exit status 1; Error: Model "gpt-5-mini" from --model flag is not available.',
        "process_failed", "FR-010-PROCESS",
        "config_failed", "FR-014-CONFIG-TEXT",
        [],
    ),
)


class FailureRulesV02ReplayTest(unittest.TestCase):
    def test_s4_samples_change_only_the_approved_gaps(self):
        seen = set()
        for run_id, reason, error, _old_class, _old_rule, new_class, new_rule, candidates in _S4_REPLAY:
            self.assertNotIn(run_id, seen)
            seen.add(run_id)
            hit = classify_failure("failed", "failed", error, reason)
            self.assertEqual(hit["failure_class"], new_class, run_id)
            self.assertEqual(hit["failure_rule_id"], new_rule, run_id)
            self.assertNotEqual(hit["failure_class"], "provider_unstable", run_id)
            self.assertEqual(hit["failure_evidence"]["rule_id"], new_rule, run_id)
            self.assertEqual(hit["failure_evidence"]["rule_version"], "failure-rules.v0.2")
            self.assertTrue(hit["failure_evidence"]["excerpt"], run_id)
            self.assertEqual(candidate_ids(error, reason), candidates, run_id)
        self.assertEqual(len(seen), 18)
        self.assertEqual(len([row for row in _S4_REPLAY if row[3] == "unknown"]), 16)
        self.assertEqual(
            {row[0] for row in _S4_REPLAY if row[5] == "unknown"},
            {
                "01a15d4a-c2d7-79da-87e5-c7c9c908e10d",
                "01a15cf8-713c-73bc-9e9b-f88c0940b58f",
                "01a15d7e-8ece-7c27-ae38-a251f348197e",
                "01a13df9-004d-7258-b87a-2a8abf1c8243",
            },
        )

    def test_reason_vocab_and_ordering(self):
        capacity = classify_failure(
            "failed", "failed",
            "Selected model is at capacity. Please try a different model.",
            "agent_error.model_not_found_or_unavailable",
        )
        self.assertEqual(capacity["failure_rule_id"], "FR-005-CAPACITY-REASON")
        self.assertEqual(capacity["failure_class"], "capacity")
        auth = classify_failure(
            "failed", "failed",
            "authentication failed or timed out",
            "agent_error.provider_auth_or_access",
        )
        self.assertEqual(auth["failure_rule_id"], "FR-007-ACCESS-REASON")
        self.assertEqual(auth["failure_class"], "access_denied")
        login = classify_failure(
            "failed", "failed",
            "cursor-agent exited with error: exit status 1; Authentication required. Please run agent login first.",
            "agent_error.process_failure",
        )
        self.assertEqual(login["failure_rule_id"], "FR-008-ACCESS-TEXT")
        process = classify_failure(
            "failed", "failed",
            "cursor-agent exited with error: exit status 1",
            "agent_error.process_failure",
        )
        self.assertEqual(process["failure_class"], "process_failed")
        self.assertEqual(process["failure_rule_id"], "FR-010-PROCESS")
        self.assertEqual(process["failure_evidence"]["source_field"], "error")
        reason_only = classify_failure("failed", "failed", None, "agent_error.process_failure")
        self.assertEqual(reason_only["failure_class"], "process_failed")
        self.assertEqual(reason_only["failure_rule_id"], "FR-010-PROCESS")
        self.assertEqual(reason_only["failure_evidence"]["source_field"], "failure_reason")
        recovery_reason = classify_failure("failed", "failed", None, "runtime_recovery")
        self.assertEqual(recovery_reason["failure_rule_id"], "FR-015-RUNTIME-RECOVERY")
        self.assertEqual(recovery_reason["failure_evidence"]["source_field"], "failure_reason")
        wrapped = classify_failure(
            "failed", "failed",
            "HTTP 503 usage limit",
            "agent_error.provider_quota_limit",
        )
        self.assertEqual(wrapped["failure_class"], "quota")
        self.assertIn("PC-001-HTTP-5XX", candidate_ids("HTTP 503 usage limit", "agent_error.provider_quota_limit"))
        pipe = candidate_ids("write: EPIPE", None)
        self.assertEqual(pipe, ["PC-002-CONNECTION"])


if __name__ == "__main__":
    unittest.main()
