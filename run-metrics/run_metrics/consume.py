"""Read a finished run-metrics.v0.1 report for other tools.

The report is already aggregated. This module copies the published counts
and field states. It does not import failure rules, does not classify, and
does not write the file back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "run-metrics.v0.1"
WINDOWS = ("24h", "7d", "14d")
DEFAULT_REPORT_PATH = Path.home() / ".multica-board" / "run-metrics.v0.1.json"
BOOL_FIELDS = ("unknown_hard_disable", "historical_quota_excludes_agent")
LIST_FIELDS = ("excluded_agent_ids", "historical_quota_agent_ids", "signals")

ALERT_DIFFERENCE = (
    "本工具的摘要和告警仍按原规则计算。"
    "统一结果只附加已聚合的窗口、双成功率、返工下限和字段状态；"
    "不读取失败分类规则，不把 failure_class 改写成告警，也不改变派单。"
)


def resolve_path(explicit: Path | str | None, env: dict | None = None) -> Path | None:
    """Pick a report path. An empty RUN_METRICS_REPORT means do not read one."""
    if explicit:
        return Path(explicit)
    raw = (env or {}).get("RUN_METRICS_REPORT")
    if raw == "":
        return None
    if raw:
        return Path(raw)
    return DEFAULT_REPORT_PATH


def load_consumption(path: Path | None) -> dict[str, Any]:
    if path is None:
        return _envelope("missing", "not_provided", "未指定统一结果文件。不补算，也不把缺失写成 0。")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return _envelope("missing", "file_missing", "没有读到 run-metrics.v0.1 报告。不补算，也不把缺失写成 0。")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError):
        return _envelope("invalid", "invalid_json", "统一结果文件不是合法 JSON。不使用残缺内容，也不改这个文件。")
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION or _contract_broken(data):
        return _envelope(
            "schema",
            "schema",
            "文件不是完整的 run-metrics.v0.1 聚合（需要 24h/7d/14d、双成功率分母和返工下限）。不补算。",
        )
    projected = _envelope("ok", None, None)
    projected["generated_at"] = data.get("generated_at") if "generated_at" in data else "missing"
    projected["rule_version"] = data.get("rule_version") if "rule_version" in data else "missing"
    projected["default_window"] = data.get("default_window")
    projected["windows"] = {name: _window(data["windows"][name]) for name in WINDOWS}
    projected["scheduling_fields"] = _scheduling_fields(data)
    projected["runtime_reconciliation"] = _runtime(data)
    projected["collection"] = _collection(data)
    projected["provider_unstable"] = _unstable(data)
    return projected


def bind_own_alerts(consumption: dict[str, Any], own_alerts: dict[str, Any]) -> dict[str, Any]:
    """Attach the caller's existing alert summary beside the unified window.

    own_alerts is echoed. It is not recomputed from failure classes.
    """
    out = dict(consumption)
    windows = consumption.get("windows")
    default = consumption.get("default_window")
    unified: Any = "missing"
    if isinstance(windows, dict) and isinstance(default, str) and isinstance(windows.get(default), dict):
        window = windows[default]
        unified = {
            "window": default,
            "success_including_cancelled": window.get("success_including_cancelled", "missing"),
            "success_excluding_cancelled": window.get("success_excluding_cancelled", "missing"),
            "rework_lower_bound": window.get("rework_lower_bound", "missing"),
            "rework_rate_claim": window.get("rework_rate_claim", "missing"),
            "failure_class_counts": window.get("failure_class_counts", "missing"),
        }
    out["alert_delta"] = {
        "own_alerts": own_alerts,
        "unified_default_window": unified,
        "own_alerts_preserved": True,
        "unified_counts_promoted_to_alerts": False,
        "dispatch_changed": False,
        "difference": ALERT_DIFFERENCE,
    }
    return out


def _envelope(status: str, reason: str | None, message: str | None) -> dict[str, Any]:
    return {
        "readonly": True,
        "writes": [],
        "dispatch_changed": False,
        "alerts_changed": False,
        "classification_copied": False,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "message": message,
        "generated_at": "missing",
        "rule_version": "missing",
        "default_window": "missing",
        "windows": "missing",
        "scheduling_fields": {name: "missing" for name in (*BOOL_FIELDS, *LIST_FIELDS)},
        "runtime_reconciliation": "missing",
        "collection": "missing",
        "provider_unstable": "missing",
    }


def _contract_broken(data: dict) -> bool:
    windows = data.get("windows")
    if not isinstance(windows, dict) or any(name not in windows for name in WINDOWS):
        return True
    if data.get("default_window") not in WINDOWS:
        return True
    for name in WINDOWS:
        window = windows.get(name)
        if not isinstance(window, dict):
            return True
        success = window.get("success_rate")
        if not isinstance(success, dict):
            return True
        for side in ("including_cancelled", "excluding_cancelled"):
            if _ratio(success.get(side)) == "missing":
                return True
        rework = window.get("rework")
        lower = rework.get("rework_rate_lower_bound") if isinstance(rework, dict) else None
        if _ratio(lower) == "missing":
            return True
    return False


def _window(window: dict) -> dict[str, Any]:
    success = window.get("success_rate") if isinstance(window.get("success_rate"), dict) else {}
    rework = window.get("rework") if isinstance(window.get("rework"), dict) else {}
    return {
        "issue_count": _present_number(window, "issue_count"),
        "run_count": _present_number(window, "run_count"),
        "success_including_cancelled": _ratio(success.get("including_cancelled")),
        "success_excluding_cancelled": _ratio(success.get("excluding_cancelled")),
        "rework_rate_claim": rework["rework_rate_claim"] if "rework_rate_claim" in rework else "missing",
        "rework_rate": _rework_rate(rework),
        "rework_lower_bound": _ratio(rework.get("rework_rate_lower_bound")),
        "rework_coverage": _rework_coverage(rework.get("rework_coverage")),
        "failure_class_counts": _dict_field(window, "failure_class_counts"),
        "field_coverage": {
            "status": _coverage_counts(window, "status"),
            "failure_class": _coverage_counts(window, "failure_class"),
        },
    }


def _rework_rate(rework: dict) -> Any:
    if "rework_rate" not in rework:
        return "missing"
    value = rework["rework_rate"]
    if value is None:
        return None
    return _ratio(value)


def _rework_coverage(block: Any) -> Any:
    if not isinstance(block, dict):
        return "missing"
    out = {}
    for name in ("explicit_count", "inferred_count", "unknown_count"):
        out[name] = block[name] if name in block else "missing"
    return out


def _coverage_counts(window: dict, name: str) -> Any:
    coverage = window.get("field_coverage")
    if not isinstance(coverage, dict) or name not in coverage:
        return "missing"
    block = coverage[name]
    if not isinstance(block, dict):
        return "unknown"
    if any(key not in block for key in ("known_count", "unknown_count", "missing_count")):
        return "missing"
    return {
        "known_count": block["known_count"],
        "unknown_count": block["unknown_count"],
        "missing_count": block["missing_count"],
    }


def _ratio(block: Any) -> Any:
    if not isinstance(block, dict) or "numerator" not in block or "denominator" not in block:
        return "missing"
    return {"numerator": block["numerator"], "denominator": block["denominator"]}


def _present_number(block: dict, name: str) -> Any:
    if name not in block or block[name] is None:
        return "missing"
    return block[name]


def _dict_field(block: dict, name: str) -> Any:
    if name not in block or block[name] is None:
        return "missing"
    if isinstance(block[name], dict):
        return block[name]
    return "unknown"


def _scheduling_fields(report: dict) -> dict[str, str]:
    scheduling = report.get("scheduling")
    if not isinstance(scheduling, dict):
        return {name: "missing" for name in (*BOOL_FIELDS, *LIST_FIELDS)}
    states: dict[str, str] = {}
    for name in BOOL_FIELDS:
        if name not in scheduling or scheduling[name] is None:
            states[name] = "missing"
        elif isinstance(scheduling[name], bool):
            states[name] = "true" if scheduling[name] else "false"
        else:
            states[name] = "unknown"
    for name in LIST_FIELDS:
        if name not in scheduling or scheduling[name] is None:
            states[name] = "missing"
        elif isinstance(scheduling[name], list):
            states[name] = "empty" if len(scheduling[name]) == 0 else "present"
        else:
            states[name] = "unknown"
    return states


def _runtime(report: dict) -> Any:
    block = report.get("runtime_reconciliation")
    if not isinstance(block, dict):
        return "missing"
    discrepancies = block.get("discrepancies") if "discrepancies" in block else None
    return {
        "observed_count": block["observed_count"] if "observed_count" in block else "missing",
        "online_count": block["online_count"] if "online_count" in block else "missing",
        "offline_count": block["offline_count"] if "offline_count" in block else "missing",
        "discrepancy_count": len(discrepancies) if isinstance(discrepancies, list) else "missing",
        "unverified": block["unverified"] if "unverified" in block else "missing",
        "quota_remaining_present": block["quota_remaining_present"] if "quota_remaining_present" in block else "missing",
    }


def _collection(report: dict) -> Any:
    block = report.get("collection")
    if not isinstance(block, dict):
        return "missing"

    def count_list(name: str) -> Any:
        if name not in block or block[name] is None:
            return "missing"
        if isinstance(block[name], list):
            return len(block[name])
        return "unknown"

    return {
        "partial": block["partial"] if "partial" in block else "missing",
        "run_missing_count": count_list("run_missing_ids"),
        "timeline_missing_count": count_list("timeline_missing_ids"),
        "error_count": count_list("errors"),
    }


def _unstable(report: dict) -> Any:
    block = report.get("provider_unstable_class")
    if not isinstance(block, dict):
        return "missing"
    candidates = block.get("candidates") if "candidates" in block else None
    samples: Any = "missing"
    if isinstance(candidates, list):
        samples = []
        for row in candidates:
            if isinstance(row, dict) and "id" in row and "sample_count" in row:
                samples.append({"id": row["id"], "sample_count": row["sample_count"]})
            else:
                samples.append("unknown")
    return {
        "emitted": block["emitted"] if "emitted" in block else "missing",
        "run_count": block["run_count"] if "run_count" in block else "missing",
        "candidate_sample_counts": samples,
    }
