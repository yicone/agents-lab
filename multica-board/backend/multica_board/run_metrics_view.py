"""Read a run-metrics.v0.1 report for the Atlas page.

The file is produced by the S3 prototype. This module only reads it.
It does not collect, classify, schedule, or write the file back.
When the local S8 switch is on, the response also carries in-memory
shadow suggestions for the approved workspace. Those suggestions are
not written and do not change dispatch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "run-metrics.v0.1"
REPORT_NAME = "run-metrics.v0.1.json"
WINDOWS = ("24h", "7d", "14d")
_BOOL_FIELDS = ("unknown_hard_disable", "historical_quota_excludes_agent")
_LIST_FIELDS = ("excluded_agent_ids", "historical_quota_agent_ids", "signals")

MISSING_MESSAGE = (
    "还没有 run-metrics.v0.1 快照。把 S3 报告保存为看板数据目录里的 "
    "run-metrics.v0.1.json。这个接口只读，不采集，也不改调度。"
)
INVALID_MESSAGE = "run-metrics.v0.1.json 读不出来或不是 JSON。页面不使用残缺内容，也不改这个文件。"
SCHEMA_MESSAGE = "快照不是 run-metrics.v0.1，或缺少 24h/7d/14d 窗口。页面不补算。"


def report_path_for(db_path: str | Path | None) -> Path | None:
    if db_path is None or str(db_path) == ":memory:":
        return None
    return Path(db_path).parent / REPORT_NAME


def load_run_metrics_file(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return _with_shadow(_envelope(False, "missing", MISSING_MESSAGE, None))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return _with_shadow(_envelope(False, "invalid", INVALID_MESSAGE, None))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return _with_shadow(_envelope(False, "schema", SCHEMA_MESSAGE, None))
    if _window_contract_broken(data):
        return _with_shadow(_envelope(False, "invalid", SCHEMA_MESSAGE, None))
    return _with_shadow(_envelope(True, None, None, data, scheduling_field_states(data)))


def scheduling_field_states(report: dict | None) -> dict[str, str]:
    """Mark absent scheduling fields without inventing false or an empty list."""
    scheduling = report.get("scheduling") if isinstance(report, dict) else None
    if not isinstance(scheduling, dict):
        return {name: "missing" for name in (*_BOOL_FIELDS, *_LIST_FIELDS)}
    states: dict[str, str] = {}
    for name in _BOOL_FIELDS:
        if name not in scheduling or scheduling[name] is None:
            states[name] = "missing"
        elif isinstance(scheduling[name], bool):
            states[name] = "true" if scheduling[name] else "false"
        else:
            states[name] = "unknown"
    for name in _LIST_FIELDS:
        if name not in scheduling or scheduling[name] is None:
            states[name] = "missing"
        elif isinstance(scheduling[name], list):
            states[name] = "empty" if len(scheduling[name]) == 0 else "present"
        else:
            states[name] = "unknown"
    return states


def _envelope(
    ok: bool,
    reason: str | None,
    message: str | None,
    report: dict | None,
    scheduling_fields: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "readonly": True,
        "writes": [],
        "schema_version": SCHEMA_VERSION,
        "reason": reason,
        "message": message,
        "report": report,
        "scheduling_fields": scheduling_fields or {name: "missing" for name in (*_BOOL_FIELDS, *_LIST_FIELDS)},
    }


def _with_shadow(envelope: dict[str, Any]) -> dict[str, Any]:
    """Attach the S8 switch result. The report file is not written."""
    report = envelope.get("report")
    if envelope.get("ok") is not True or not isinstance(report, dict):
        envelope["shadow"] = _shadow_unavailable("not_loaded")
        return envelope
    try:
        envelope["shadow"] = _evaluate_s8(report)
    except Exception:
        envelope["shadow"] = _shadow_unavailable("unavailable")
    return envelope


def _evaluate_s8(report: dict) -> dict[str, Any]:
    root = _run_metrics_root()
    if root is None:
        return _shadow_unavailable("unavailable")
    entry = str(root)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    from run_metrics.s8_enable import evaluate_s8

    return evaluate_s8(report)


def _run_metrics_root() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "run-metrics"
        if (candidate / "run_metrics" / "s8_enable.py").is_file():
            return candidate
    return None


def _shadow_unavailable(status: str) -> dict[str, Any]:
    message = {
        "not_loaded": "运行指标还没接上。不生成建议。",
        "unavailable": "影子建议模块不可用。只读展示运行指标，不生成建议。",
    }[status]
    return {
        "schema_version": "s8-shadow-enable.v0.1",
        "enabled": False,
        "status": status,
        "mode": "readonly_display",
        "workspace_id": "8ebc87ea-27f2-42c4-90f6-24a04e64d3f5",
        "readonly": True,
        "writes": [],
        "write_endpoints_added": [],
        "dispatch_changed": False,
        "message": message,
        "suggestions": [],
        "routing_shadow": None,
    }


def _window_contract_broken(data: dict) -> bool:
    windows = data.get("windows")
    if not isinstance(windows, dict) or any(name not in windows for name in WINDOWS):
        return True
    if data.get("default_window") not in WINDOWS:
        return True
    if not isinstance(data.get("runtime_reconciliation"), dict):
        return True
    for name in WINDOWS:
        window = windows.get(name)
        if not isinstance(window, dict):
            return True
        success = window.get("success_rate")
        if not isinstance(success, dict):
            return True
        for side in ("including_cancelled", "excluding_cancelled"):
            block = success.get(side)
            if not isinstance(block, dict) or "numerator" not in block or "denominator" not in block:
                return True
        rework = window.get("rework")
        lower = rework.get("rework_rate_lower_bound") if isinstance(rework, dict) else None
        if not isinstance(lower, dict) or "numerator" not in lower or "denominator" not in lower:
            return True
    return False
