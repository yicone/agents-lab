"""Turn S7 shadow suggestions on for the one approved workspace.

The switch lives in this repository. It does not write production config,
does not assign work, and does not change agent, workspace, or runtime records.
Closing the switch returns the read-only metrics view and stops suggestion
generation.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "s8-shadow-enable.v0.1"
APPROVED_WORKSPACE_ID = "8ebc87ea-27f2-42c4-90f6-24a04e64d3f5"
MODE = "readonly_shadow_suggestions"
CONFIG_RELATIVE = "run-metrics/config/s8-shadow-enable.json"
PRE_ENABLE_MAIN = "ece1fbd81f78b02b88b997a74b569e2a4bac0a1b"
S7_IMPLEMENTATION = "0a93992757b80dbfee28cfa6cb1050dd8a67e957"
S7_MERGE = "b37aa561b380ce5b396edb5374baa4d18af873cd"
PROPOSAL_COMMENT_ID = "01a186e5-d3fd-7593-9e46-e1238aa14052"
CONFIRMATION_COMMENT_ID = "01a18795-c794-766a-a3a7-6ada68242c30"

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "s8-shadow-enable.json"

_MESSAGES = {
    "disabled": "S8 未启用。只读展示运行指标，不生成建议。",
    "invalid_config": "S8 配置无法识别。只读展示运行指标，不生成建议。",
    "config_unreadable": "S8 配置读不出来。只读展示运行指标，不生成建议。",
    "out_of_scope": "S8 配置不在已批准的 workspace 内。只读展示运行指标，不生成建议。",
    "not_loaded": "运行指标还没接上。不生成建议。",
    "unavailable": "影子建议模块不可用。只读展示运行指标，不生成建议。",
    "invalid_report": "运行指标报告不可用。不生成建议。",
    "refused": "建议结果不是只读，已丢弃。不改派单。",
    "enabled": "已启用只读影子建议。建议和审计留在响应里，不改派单。",
}


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Read the local switch. A missing or broken file stays off."""
    target = path or _CONFIG_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {"schema_version": SCHEMA_VERSION, "enabled": False, "_read_status": "config_unreadable"}
    if not isinstance(data, dict):
        return {"schema_version": SCHEMA_VERSION, "enabled": False, "_read_status": "invalid_config"}
    return data


def evaluate_s8(report: dict | None, config: dict | None = None) -> dict[str, Any]:
    """Return shadow suggestions for the approved workspace, or a closed switch.

    The report object is not modified. Nothing is written to disk.
    """
    loaded = load_config() if config is None else config
    allowed, status = _decision(loaded)
    if not allowed:
        return _closed(status)
    if not isinstance(report, dict):
        return _closed("invalid_report", enabled=True)
    before = _fingerprint(report)
    if before is None:
        return _closed("invalid_report", enabled=True)
    scoped, in_scope, excluded = _scope(report)
    try:
        from .shadow import build_shadow

        routing = build_shadow(scoped)
    except Exception:
        return _closed("unavailable", enabled=True)
    if _fingerprint(report) != before:
        return _closed("refused", enabled=True)
    if not _routing_is_readonly(routing):
        return _closed("refused", enabled=True)
    suggestions = routing.get("suggestions")
    if not isinstance(suggestions, list):
        return _closed("refused", enabled=True)
    return {
        **_shell(enabled=True, status="enabled"),
        "routing_status": routing.get("status"),
        "in_scope_run_count": in_scope,
        "excluded_other_workspace_run_count": excluded,
        "suggestions": suggestions,
        "routing_shadow": routing,
    }


def _decision(config: Any) -> tuple[bool, str]:
    if not isinstance(config, dict):
        return False, "invalid_config"
    if config.get("_read_status") == "config_unreadable":
        return False, "config_unreadable"
    if config.get("schema_version") != SCHEMA_VERSION:
        return False, "invalid_config"
    enabled = config.get("enabled")
    if enabled is False:
        return False, "disabled"
    if enabled is not True:
        return False, "invalid_config"
    if config.get("workspace_id") != APPROVED_WORKSPACE_ID:
        return False, "out_of_scope"
    if config.get("mode") != MODE:
        return False, "invalid_config"
    return True, "enabled"


def _scope(report: dict) -> tuple[dict, Any, Any]:
    scoped = copy.deepcopy(report)
    runs = scoped.get("runs")
    if not isinstance(runs, list):
        return scoped, "missing", "missing"
    kept = []
    excluded = 0
    for run in runs:
        if isinstance(run, dict) and run.get("workspace_id") == APPROVED_WORKSPACE_ID:
            kept.append(run)
        else:
            excluded += 1
    scoped["runs"] = kept
    return scoped, len(kept), excluded


def _routing_is_readonly(routing: Any) -> bool:
    if not isinstance(routing, dict):
        return False
    return (
        routing.get("readonly") is True
        and routing.get("writes") == []
        and routing.get("write_endpoints_added") == []
        and routing.get("dispatch_changed") is False
        and routing.get("failure_class_rewritten") is False
    )


def _fingerprint(report: dict) -> str | None:
    try:
        return json.dumps(report, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return None


def _closed(status: str, *, enabled: bool = False) -> dict[str, Any]:
    body = _shell(enabled=enabled, status=status)
    body["routing_status"] = None
    body["in_scope_run_count"] = "missing"
    body["excluded_other_workspace_run_count"] = "missing"
    body["suggestions"] = []
    body["routing_shadow"] = None
    return body


def _shell(*, enabled: bool, status: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "enabled": enabled,
        "status": status,
        "mode": MODE if status == "enabled" else "readonly_display",
        "workspace_id": APPROVED_WORKSPACE_ID,
        "readonly": True,
        "writes": [],
        "write_endpoints_added": [],
        "dispatch_changed": False,
        "message": _MESSAGES[status],
        "config_path": CONFIG_RELATIVE,
        "audit": {
            "issue": "ACM-574",
            "proposal_comment_id": PROPOSAL_COMMENT_ID,
            "confirmation_comment_id": CONFIRMATION_COMMENT_ID,
            "s7_implementation": S7_IMPLEMENTATION,
            "s7_merge": S7_MERGE,
            "pre_enable_main": PRE_ENABLE_MAIN,
            "workspace_id": APPROVED_WORKSPACE_ID,
            "runtime_changes": [],
            "account_changes": [],
            "dispatch_changed": False,
            "writes": [],
        },
        "rollback": {
            "method": "set_enabled_false",
            "config_path": CONFIG_RELATIVE,
            "config_written_to_production": False,
            "pre_enable_main": PRE_ENABLE_MAIN,
            "steps": [
                "把 run-metrics/config/s8-shadow-enable.json 的 enabled 改为 false。",
                "再读 GET /api/run-metrics：shadow.enabled 为 false，suggestions 为空。",
                "writes 仍为 []，dispatch_changed 仍为 false。",
                "不要重启 8767，不要改派单、agent、workspace 或 runtime。",
                "若要丢掉这段启用代码，回到启用前 main ece1fbd81f78b02b88b997a74b569e2a4bac0a1b。",
            ],
        },
    }
