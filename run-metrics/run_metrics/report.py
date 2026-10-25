"""Assemble the run-metrics.v0.1 report and reject contract violations."""

from __future__ import annotations

from . import RULE_VERSION, SCHEMA_VERSION
from .aggregate import (
    candidate_summary,
    filter_issues,
    filter_runs,
    outcome_block,
    rework_summary,
    rule_hits,
)
from .project import project_run
from .rework import infer_rework
from .runtime_check import reconcile_runtimes, scheduling_view
from .timeutil import DEFAULT_WINDOW, WINDOWS, iso, parse_ts, window_bounds


class ReportInvariantError(Exception):
    pass


def _from_to_stats(changes: list[dict]) -> dict:
    with_both = 0
    without = 0
    for change in changes:
        if change.get("from") and change.get("to"):
            with_both += 1
        else:
            without += 1
    return {
        "events": len(changes),
        "with_from_to": with_both,
        "without_from_to": without,
    }


def _timeline_note(stats: dict) -> str:
    if stats["events"] == 0:
        return "本次没有读到 status_changed，不能复核 S1 关于 from/to 的说法。"
    if stats["without_from_to"] == 0:
        return (
            "S1 称状态变更没有统一 from/to。本次读到的 status_changed 全部带 details.from 与 details.to，"
            "与 S1 不一致；返工推断使用本次读到的字段，不沿用 S1 结论。"
        )
    if stats["with_from_to"] == 0:
        return "本次 status_changed 都没有同时具备 from 与 to，和 S1 的缺口描述一致。"
    return (
        f"本次 {stats['with_from_to']} 条 status_changed 同时有 from/to，"
        f"{stats['without_from_to']} 条没有。S1 说没有统一 from/to，只能部分成立；"
        "缺字段的事件不推断为返工。"
    )


def build_report(snapshot: dict) -> dict:
    collected_at = parse_ts(snapshot.get("collected_at"))
    if collected_at is None:
        raise ReportInvariantError("snapshot.collected_at 不是 RFC3339")
    projected = [project_run(raw) for raw in snapshot.get("runs") or []]
    issues = snapshot.get("issues") or []
    changes = snapshot.get("status_changes") or []
    comments = snapshot.get("comments") or []
    truncated = set(snapshot.get("timeline_truncated_ids") or [])
    missing_timelines = set(snapshot.get("timeline_missing_ids") or [])
    windows = {}
    default_runs: list[dict] = []
    default_rework = None
    for name in WINDOWS:
        start, end = window_bounds(collected_at, name)
        window_issues = filter_issues(issues, start, end)
        window_runs = filter_runs(projected, start, end)
        inferred = infer_rework(
            window_issues,
            projected,
            changes,
            comments,
            start=start,
            end=end,
            timeline_truncated_ids=truncated,
            timeline_missing_ids=missing_timelines,
        )
        outcome = outcome_block(window_runs)
        groups = {
            "by_agent": _group_with_rework(window_runs, "agent_id", inferred),
            "by_runtime": _group_with_rework(window_runs, "runtime_id", inferred),
            "by_workspace": _group_with_rework(window_runs, "workspace_id", inferred),
            "by_issue": _group_with_rework(window_runs, "issue_id", inferred),
        }
        rework = rework_summary(len(window_issues), inferred)
        windows[name] = {
            "start": iso(start),
            "end": iso(end),
            "issue_count": len(window_issues),
            **outcome,
            "groups": groups,
            "failure_rules": rule_hits(window_runs),
            "provider_unstable_candidates": candidate_summary(window_runs),
            "rework": rework,
        }
        if name == DEFAULT_WINDOW:
            default_runs = window_runs
            default_rework = rework

    quota_agents = sorted({
        run["agent_id"]
        for run in default_runs
        if run.get("failure_class") == "quota" and run.get("agent_id")
    })
    checked_at = snapshot.get("collected_at")
    runtimes = snapshot.get("runtimes") or []
    probes = snapshot.get("usage_probes") or []
    from_to = _from_to_stats(changes)
    candidates = candidate_summary(default_runs)
    report = {
        "schema_version": SCHEMA_VERSION,
        "rule_version": RULE_VERSION,
        "generated_at": checked_at,
        "default_window": DEFAULT_WINDOW,
        "window_semantics": "边界为 [generated_at-window, generated_at]，UTC。run 用 started_at，缺失时用 created_at 作为 anchor_at。",
        "source": {
            "mode": snapshot.get("mode") or "cli-readonly",
            "workspace_id": snapshot.get("workspace_id"),
            "commands": [
                "multica issue list",
                "multica issue runs",
                "multica issue timeline --action status_changed",
                "multica issue comment list --roots-only --summary",
                "multica runtime list",
                "multica runtime usage",
            ],
        },
        "collection": {
            "workspace_total": (snapshot.get("issue_scan") or {}).get("workspace_total"),
            "issues_fetched": (snapshot.get("issue_scan") or {}).get("fetched"),
            "issues_in_default_window": (snapshot.get("issue_scan") or {}).get("in_default_window"),
            "run_missing_ids": snapshot.get("run_missing_ids") or [],
            "timeline_missing_ids": snapshot.get("timeline_missing_ids") or [],
            "timeline_truncated_ids": snapshot.get("timeline_truncated_ids") or [],
            "comment_missing_ids": snapshot.get("comment_missing_ids") or [],
            "errors": snapshot.get("collection_errors") or [],
            "runs_missing_anchor": [
                run.get("run_id") for run in projected if not run.get("anchor_at")
            ],
            "partial": bool(
                snapshot.get("run_missing_ids")
                or snapshot.get("timeline_missing_ids")
                or snapshot.get("collection_errors")
            ),
        },
        "runtime_reconciliation": reconcile_runtimes(runtimes, probes, checked_at=checked_at),
        "timeline_from_to": {**from_to, "compared_with_s1": _timeline_note(from_to)},
        "scheduling": scheduling_view(
            runtimes, probes, quota_agents, checked_at=checked_at,
        ),
        "provider_unstable_class": {
            "emitted": False,
            "run_count": 0,
            "rule_version": RULE_VERSION,
            "reason": "用户 2026-10-26 未采纳 provider_unstable 阈值。候选信号只计数，不产出该分类。",
            "threshold_proposal": None,
            "deferred_to": None,
            "candidates": candidates,
        },
        "failure_rules": {
            "version": RULE_VERSION,
            "first_match_wins": True,
            "window": DEFAULT_WINDOW,
            "rules": rule_hits(default_runs),
        },
        "windows": windows,
        "rework": default_rework,
        "runs": default_runs,
    }
    assert_invariants(report)
    return report


def _group_with_rework(runs: list[dict], key: str, inferred: dict) -> dict:
    from .aggregate import _group

    grouped = _group(runs, key)
    if key != "issue_id":
        return grouped
    confidence = inferred["issue_confidence"]
    for issue_id, block in grouped.items():
        block["rework_confidence"] = confidence.get(issue_id, "not_candidate")
    return grouped


def assert_invariants(report: dict) -> None:
    unstable = report["provider_unstable_class"]
    if unstable["emitted"] is not False or unstable["run_count"] != 0:
        raise ReportInvariantError("provider_unstable 被产出")
    if unstable["threshold_proposal"] is not None:
        raise ReportInvariantError("S3 不得写入阈值提案")
    for run in report["runs"]:
        if run.get("failure_class") == "provider_unstable":
            raise ReportInvariantError(f"{run.get('run_id')} 被标成 provider_unstable")
        if run.get("failure_class") is not None:
            evidence = run.get("failure_evidence") or {}
            if not run.get("failure_rule_id") or evidence.get("rule_id") != run.get("failure_rule_id"):
                raise ReportInvariantError(f"{run.get('run_id')} 缺少 rule id 或 evidence")
            if run.get("failure_rule_version") != RULE_VERSION:
                raise ReportInvariantError(f"{run.get('run_id')} 规则版本不一致")
    for name, window in report["windows"].items():
        if "provider_unstable" in window["failure_class_counts"]:
            raise ReportInvariantError(f"{name} 的 failure_class_counts 含 provider_unstable")
        success = window["success_rate"]
        for side in ("including_cancelled", "excluding_cancelled"):
            block = success[side]
            if "numerator" not in block or "denominator" not in block:
                raise ReportInvariantError(f"{name} 成功率 {side} 缺少分母")
        coverage = window["rework"]["rework_coverage"]
        if coverage["unknown_count"] > 0:
            if window["rework"]["rework_rate"] is not None:
                raise ReportInvariantError(f"{name} 在 unknown 下仍给出了返工率点估计")
            if window["rework"]["rework_rate_claim"] != "lower_bound_only":
                raise ReportInvariantError(f"{name} 覆盖不足时必须标记下限")
        if window["rework"]["rework_rate_lower_bound"]["denominator"] != window["issue_count"]:
            raise ReportInvariantError(f"{name} 返工率分母不是窗口 issue 数")
    scheduling = report["scheduling"]
    if scheduling["unknown_hard_disable"] is not False:
        raise ReportInvariantError("unknown 被硬禁用")
    if scheduling["historical_quota_excludes_agent"] is not False or scheduling["excluded_agent_ids"]:
        raise ReportInvariantError("历史 quota 产生了排除名单")
    for signal in scheduling["signals"]:
        if signal["health"] == "unknown" and signal["hard_disable"] is not False:
            raise ReportInvariantError(f"{signal.get('subject_id')} unknown 仍被硬禁用")


def summarize(report: dict) -> dict:
    """Drop per-run bodies so a review attachment stays readable."""
    default = report["windows"][report["default_window"]]
    rework = default["rework"]
    return {
        "schema_version": report["schema_version"],
        "rule_version": report["rule_version"],
        "generated_at": report["generated_at"],
        "default_window": report["default_window"],
        "collection": report["collection"],
        "runtime_reconciliation": {
            "checked_at": report["runtime_reconciliation"]["checked_at"],
            "observed_count": report["runtime_reconciliation"]["observed_count"],
            "online_count": report["runtime_reconciliation"]["online_count"],
            "offline_count": report["runtime_reconciliation"]["offline_count"],
            "s1_claim": report["runtime_reconciliation"]["s1_claim"],
            "discrepancies": report["runtime_reconciliation"]["discrepancies"],
            "unverified": report["runtime_reconciliation"]["unverified"],
            "quota_remaining_present": report["runtime_reconciliation"]["quota_remaining_present"],
            "atlas_gemini_display": report["runtime_reconciliation"]["atlas_gemini_display"],
            "runtimes": report["runtime_reconciliation"]["runtimes"],
        },
        "timeline_from_to": report["timeline_from_to"],
        "scheduling": {
            "unknown_hard_disable": report["scheduling"]["unknown_hard_disable"],
            "historical_quota_excludes_agent": report["scheduling"]["historical_quota_excludes_agent"],
            "excluded_agent_ids": report["scheduling"]["excluded_agent_ids"],
            "historical_quota_agent_count": len(report["scheduling"]["historical_quota_agent_ids"]),
            "signal_count": len(report["scheduling"]["signals"]),
            "health_values": sorted({signal["health"] for signal in report["scheduling"]["signals"]}),
        },
        "provider_unstable_class": report["provider_unstable_class"],
        "failure_rules": report["failure_rules"],
        "windows": {
            name: {
                "start": window["start"],
                "end": window["end"],
                "issue_count": window["issue_count"],
                "run_count": window["run_count"],
                "succeeded_count": window["succeeded_count"],
                "failed_count": window["failed_count"],
                "blocked_count": window["blocked_count"],
                "cancelled_count": window["cancelled_count"],
                "running_count": window["running_count"],
                "queued_count": window["queued_count"],
                "unknown_status_count": window["unknown_status_count"],
                "success_rate": window["success_rate"],
                "failure_rate": window["failure_rate"],
                "failure_class_counts": window["failure_class_counts"],
                "duration_p50_ms": window["duration_p50_ms"],
                "duration_p95_ms": window["duration_p95_ms"],
                "duration_sample_count": window["duration_sample_count"],
                "field_coverage": window["field_coverage"],
                "rework": {
                    key: value for key, value in window["rework"].items() if key != "chains"
                },
                "provider_unstable_candidates": window["provider_unstable_candidates"],
                "groups_by_runtime": {
                    group_id: {
                        "run_count": block["run_count"],
                        "succeeded_count": block["succeeded_count"],
                        "failed_count": block["failed_count"],
                        "cancelled_count": block["cancelled_count"],
                        "success_rate": block["success_rate"],
                        "failure_class_counts": block["failure_class_counts"],
                    }
                    for group_id, block in window["groups"]["by_runtime"].items()
                },
            }
            for name, window in report["windows"].items()
        },
        "run_projection_count": len(report["runs"]),
        "failure_runs": [run for run in report["runs"] if run.get("failure_class")],
        "rework_chains": rework["chains"],
        "rework_chain_count": len(rework["chains"]),
    }
