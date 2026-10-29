"""Record advisory routes from current quota health and provider signals.

The approved threshold is applied only inside this document. Failure classes
in the source report stay as they were, and nothing here assigns an issue,
starts a run, or opens a write endpoint.
"""

from __future__ import annotations

import re
from typing import Any

from .timeutil import in_window, parse_ts

SCHEMA_VERSION = "routing-shadow.v0.1"
SOURCE_SCHEMA = "run-metrics.v0.1"
WINDOW = "24h"
MIN_FAILURES = 3
APPROVAL_COMMENT_ID = "01a1855b-fba3-72bc-96d5-4c007113d6dc"
APPROVED_AT = "2026-10-29T01:52:40Z"

# User text from ACM-573 comment 01a1855b-fba3-72bc-96d5-4c007113d6dc.
THRESHOLD_TEXT = (
    "同一 runtime × 同一错误签名，滚动 24h 内失败至少 3 条；",
    "排除 quota / capacity / access_denied；",
    "仅记录建议路由，不改实际派单、不写入口、不新增写入口。",
)

EXCLUDED_CLASSES = ("quota", "capacity", "access_denied")
# S4 candidate constraints. They do not replace the user threshold text.
S4_EXTRA_EXCLUDED_CLASSES = ("config_failed", "cancelled")
_PROVIDER_REASON = re.compile(r"(?i)(?:agent_error\.)?provider_server_error")
_GEO_TEXT = re.compile(
    r"(?i)(user location is not supported|location is not supported for the api)"
)
_CONFIG_TEXT = re.compile(
    r"(?i)(from --model flag is not available|"
    r"model .{0,80} is not available|"
    r"model .{0,80} is not supported|"
    r"not supported when using|"
    r"\bapi_invalid_request\b)"
)
_PC_004 = "PC-004-PROVIDER-SERVER-REASON"
_PC_001 = "PC-001-HTTP-5XX"
_PC_002 = "PC-002-CONNECTION"
_STRUCTURED_SIGNATURE = "agent_error.provider_server_error"


def build_shadow(report: dict | None) -> dict[str, Any]:
    """Return a shadow document. The report object is not modified."""
    if not isinstance(report, dict):
        return _document("invalid", window=None, body=_empty_body())
    if report.get("schema_version") != SOURCE_SCHEMA:
        return _document("schema", window=None, body=_empty_body(), report=report)
    window = _window(report)
    if window is None:
        return _document("missing_window", window=None, body=_empty_body(), report=report)
    if "runs" not in report or not isinstance(report.get("runs"), list):
        return _document("missing_runs", window=window, body=_empty_body(), report=report)

    considered = []
    for run in report["runs"]:
        if not isinstance(run, dict):
            continue
        if not _in_rolling_window(run, window):
            continue
        if run.get("status") != "failed":
            continue
        considered.append(run)

    groups: dict[tuple[str, str, str], list[dict]] = {}
    excluded: dict[tuple[str, str], list[dict]] = {}
    not_provider: dict[tuple[str, str], list[dict]] = {}
    for run in considered:
        decision = _signature(run)
        runtime_id = decision.get("runtime_id") or "missing"
        if decision["countable"]:
            key = (runtime_id, decision["signature"], decision["signature_source"])
            groups.setdefault(key, []).append(run)
            continue
        reason = decision["reason"]
        if reason.startswith("excluded_"):
            excluded.setdefault((runtime_id, reason), []).append(run)
        else:
            klass = run.get("failure_class") if isinstance(run.get("failure_class"), str) else "none"
            not_provider.setdefault((runtime_id, klass), []).append(run)

    marked_ids = {
        runtime_id
        for (runtime_id, _signature, _source), runs in groups.items()
        if len(runs) >= MIN_FAILURES and runtime_id != "missing"
    }
    signals = _signal_rows(report)
    suggestions = []
    below = []
    for key in sorted(groups):
        runtime_id, signature, source = key
        runs = sorted(groups[key], key=lambda row: (row.get("anchor_at") or "", row.get("run_id") or ""))
        row = _group_row(runtime_id, signature, source, runs)
        if len(runs) >= MIN_FAILURES and runtime_id != "missing":
            alternatives = _alternatives(runtime_id, signals, marked_ids)
            action = "avoid_runtime" if alternatives else "no_alternative"
            row["mark"] = "provider_unstable"
            row["threshold_met"] = True
            row["suggested_route"] = {
                "action": action,
                "avoid_runtime_id": runtime_id,
                "reason": "provider_unstable",
                "quota_used_as_reason": False,
                "alternative_runtime_ids": [item["runtime_id"] for item in alternatives],
                "alternatives": alternatives,
                "quota_note": "候选 runtime 的额度健康度未知时，不把未知当成健康或耗尽，也不硬禁用。",
                "applied": False,
            }
            row["comparison"] = {
                "actual_runtime_ids": _unique(run.get("runtime_id") for run in runs),
                "suggested_avoid_runtime_id": runtime_id,
                "diverges": True,
                "dispatch_changed": False,
            }
            suggestions.append(row)
        else:
            row["mark"] = None
            row["threshold_met"] = False
            row["suggested_action"] = "keep_actual"
            row["dispatch_changed"] = False
            below.append(row)

    body = {
        "suggestions": suggestions,
        "below_threshold": below,
        "excluded_groups": _count_rows(excluded, "reason"),
        "not_provider_groups": _count_rows(not_provider, "failure_class"),
        "quota_suggestions": _quota_suggestions(signals),
        "historical_quota": _historical_quota(report),
        "signal_snapshot": {
            "generated_at": report.get("generated_at") if "generated_at" in report else "missing",
            "window": window,
            "failed_run_count": len(considered),
            "quota_health": signals,
            "provider_groups": [_public_group(row) for row in (*suggestions, *below)],
        },
    }
    return _document("ok", window=window, body=body, report=report)


def _signature(run: dict) -> dict[str, Any]:
    runtime_id = run.get("runtime_id")
    if not isinstance(runtime_id, str) or not runtime_id.strip():
        runtime_id = None
    klass = run.get("failure_class")
    if klass in EXCLUDED_CLASSES:
        return {"countable": False, "reason": f"excluded_{klass}", "runtime_id": runtime_id}
    if klass in S4_EXTRA_EXCLUDED_CLASSES:
        return {"countable": False, "reason": "s4_not_provider", "runtime_id": runtime_id}
    blob = _blob(run)
    if _GEO_TEXT.search(blob) or _CONFIG_TEXT.search(blob):
        return {"countable": False, "reason": "s4_geo_or_config", "runtime_id": runtime_id}
    if runtime_id is None:
        return {"countable": False, "reason": "missing_runtime", "runtime_id": None}
    candidates = run.get("provider_candidate_ids")
    if not isinstance(candidates, list):
        candidates = []
    if _PROVIDER_REASON.search(blob) or _PC_004 in candidates:
        return {
            "countable": True,
            "runtime_id": runtime_id,
            "signature": _STRUCTURED_SIGNATURE,
            "signature_source": "structured_reason",
        }
    unclassified = klass in (None, "unknown")
    if unclassified and _PC_001 in candidates:
        return {
            "countable": True,
            "runtime_id": runtime_id,
            "signature": _PC_001,
            "signature_source": "candidate_signal",
        }
    if unclassified and _PC_002 in candidates:
        return {
            "countable": True,
            "runtime_id": runtime_id,
            "signature": _PC_002,
            "signature_source": "candidate_signal",
        }
    return {"countable": False, "reason": "not_provider_signal", "runtime_id": runtime_id}


def _blob(run: dict) -> str:
    evidence = run.get("failure_evidence") if isinstance(run.get("failure_evidence"), dict) else {}
    parts = [run.get("raw_reason_redacted"), evidence.get("excerpt")]
    return "\n".join(part for part in parts if isinstance(part, str))


def _window(report: dict) -> dict[str, str] | None:
    windows = report.get("windows")
    if not isinstance(windows, dict):
        return None
    block = windows.get(WINDOW)
    if not isinstance(block, dict):
        return None
    start = block.get("start")
    end = block.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    if parse_ts(start) is None or parse_ts(end) is None:
        return None
    return {"name": WINDOW, "start": start, "end": end}


def _in_rolling_window(run: dict, window: dict[str, str]) -> bool:
    start = parse_ts(window["start"])
    end = parse_ts(window["end"])
    if start is None or end is None:
        return False
    return in_window(parse_ts(run.get("anchor_at")), start, end)


def _group_row(runtime_id: str, signature: str, source: str, runs: list[dict]) -> dict[str, Any]:
    return {
        "runtime_id": runtime_id,
        "signature": signature,
        "signature_source": source,
        "sample_count": len(runs),
        "window": WINDOW,
        "min_failures": MIN_FAILURES,
        "actual_dispatches": [_dispatch(run) for run in runs],
    }


def _dispatch(run: dict) -> dict[str, Any]:
    return {
        "run_id": run.get("run_id"),
        "issue_id": run.get("issue_id"),
        "agent_id": run.get("agent_id"),
        "runtime_id": run.get("runtime_id"),
        "anchor_at": run.get("anchor_at"),
        "failure_class": run.get("failure_class"),
    }


def _public_group(row: dict) -> dict[str, Any]:
    return {
        "runtime_id": row["runtime_id"],
        "signature": row["signature"],
        "sample_count": row["sample_count"],
        "threshold_met": row["threshold_met"],
        "mark": row["mark"],
    }


def _count_rows(groups: dict[tuple[str, str], list[dict]], label: str) -> list[dict]:
    rows = []
    for (runtime_id, value) in sorted(groups):
        runs = groups[(runtime_id, value)]
        rows.append({
            "runtime_id": runtime_id,
            label: value,
            "sample_count": len(runs),
            "actual_dispatches": [_dispatch(run) for run in runs],
            "suggested_action": "keep_actual",
            "dispatch_changed": False,
        })
    return rows


def _signal_rows(report: dict) -> Any:
    scheduling = report.get("scheduling")
    if not isinstance(scheduling, dict) or "signals" not in scheduling:
        return "missing"
    signals = scheduling.get("signals")
    if not isinstance(signals, list):
        return "unknown"
    rows: list[Any] = []
    for signal in signals:
        if not isinstance(signal, dict):
            rows.append("unknown")
            continue
        rows.append({
            "subject_type": signal.get("subject_type"),
            "subject_id": signal.get("subject_id"),
            "provider": signal.get("provider"),
            "name": signal.get("name"),
            "connection_status": signal.get("connection_status"),
            "health": signal["health"] if "health" in signal else "missing",
            "quota_remaining": signal["quota_remaining"] if "quota_remaining" in signal else "missing",
            "hard_disable": signal["hard_disable"] if isinstance(signal.get("hard_disable"), bool) else "missing",
        })
    return rows


def _alternatives(avoid_id: str, signals: Any, marked_ids: set[str]) -> list[dict]:
    if not isinstance(signals, list):
        return []
    found = []
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        runtime_id = signal.get("subject_id")
        if not isinstance(runtime_id, str) or not runtime_id or runtime_id == avoid_id:
            continue
        if runtime_id in marked_ids:
            continue
        if signal.get("connection_status") != "online":
            continue
        if signal.get("hard_disable") is True:
            continue
        if signal.get("health") in {"exhausted", "unavailable"}:
            continue
        found.append({
            "runtime_id": runtime_id,
            "name": signal.get("name"),
            "provider": signal.get("provider"),
            "connection_status": signal.get("connection_status"),
            "quota_health": signal.get("health"),
            "hard_disable": False,
        })
    return found


def _quota_suggestions(signals: Any) -> Any:
    if not isinstance(signals, list):
        return signals
    rows = []
    for signal in signals:
        if not isinstance(signal, dict):
            rows.append("unknown")
            continue
        health = signal.get("health")
        if health in (None, "unknown", "missing"):
            action = "do_not_reroute"
            reason = "current_quota_unknown"
        elif health in {"exhausted", "unavailable"}:
            action = "prefer_other_runtime"
            reason = "current_quota_unhealthy"
        else:
            action = "keep_actual"
            reason = "current_quota_not_excluding"
        rows.append({
            "runtime_id": signal.get("subject_id"),
            "health": health,
            "suggested_action": action,
            "reason": reason,
            "hard_disable": False,
            "applied": False,
            "dispatch_changed": False,
        })
    return rows


def _historical_quota(report: dict) -> dict[str, Any]:
    scheduling = report.get("scheduling") if isinstance(report.get("scheduling"), dict) else {}
    agent_ids: Any = "missing"
    if "historical_quota_agent_ids" in scheduling:
        raw = scheduling.get("historical_quota_agent_ids")
        agent_ids = list(raw) if isinstance(raw, list) else "unknown"
    source_flag: Any = "missing"
    if "historical_quota_excludes_agent" in scheduling:
        source_flag = scheduling.get("historical_quota_excludes_agent")
    return {
        "source_agent_ids": agent_ids,
        "source_excludes_agent": source_flag,
        "shadow_excludes_agent": False,
        "suggested_action": "keep_actual",
        "dispatch_changed": False,
        "note": "历史 quota 失败只留在快照里，不产生排除名单。",
    }


def _empty_body() -> dict[str, Any]:
    return {
        "suggestions": [],
        "below_threshold": [],
        "excluded_groups": [],
        "not_provider_groups": [],
        "quota_suggestions": "missing",
        "historical_quota": {
            "source_agent_ids": "missing",
            "source_excludes_agent": "missing",
            "shadow_excludes_agent": False,
            "suggested_action": "keep_actual",
            "dispatch_changed": False,
            "note": "历史 quota 失败只留在快照里，不产生排除名单。",
        },
        "signal_snapshot": "missing",
    }


def _document(status: str, *, window: dict | None, body: dict, report: dict | None = None) -> dict[str, Any]:
    generated_at = "missing"
    if isinstance(report, dict) and "generated_at" in report:
        generated_at = report.get("generated_at")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "readonly": True,
        "writes": [],
        "write_endpoints_added": [],
        "dispatch_changed": False,
        "failure_class_rewritten": False,
        "provider_unstable_failure_class_emitted": False,
        "source_schema_version": SOURCE_SCHEMA,
        "source_generated_at": generated_at,
        "window": window if window is not None else "missing",
        **body,
        "rollback": {
            "method": "discard_shadow_artifact",
            "config_written": False,
            "dispatch_code_changed": False,
            "steps": [
                "停止读取这份 shadow JSON。",
                "删除本地输出文件。",
                "派单、agent、workspace、runtime 都没有被写入，不需要回滚它们。",
            ],
        },
        "audit": {
            "issue": "ACM-573",
            "approval_comment_id": APPROVAL_COMMENT_ID,
            "approved_at": APPROVED_AT,
            "threshold_text": list(THRESHOLD_TEXT),
            "threshold": {
                "min_failures": MIN_FAILURES,
                "window": WINDOW,
                "group_by": ["runtime_id", "error_signature"],
                "excluded_failure_classes": list(EXCLUDED_CLASSES),
            },
            "s4_candidate_constraints": (
                "配置类 config_failed、cancelled，以及 location is not supported / "
                "model is not supported 文本不计入。PC-001/PC-002 只用于未分类失败。"
                "结构化 reason agent_error.provider_server_error 优先。"
                "这些约束来自 S4 提案，不改上面的用户阈值原文。"
            ),
            "writes": [],
            "write_endpoints_added": [],
            "dispatch_changed": False,
            "failure_class_rewritten": False,
            "provider_unstable_marked_in": "shadow_suggestions_only",
        },
    }


def _unique(values) -> list:
    seen = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen
