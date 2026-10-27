"""Attach a read-only run-metrics summary. Patrol decisions stay in the caller."""

from __future__ import annotations

import sys
from pathlib import Path


def attach(path, own_alerts: dict) -> dict:
    try:
        bind_own_alerts, load_consumption = _api()
    except ImportError:
        return _unavailable(own_alerts)
    return bind_own_alerts(load_consumption(path), own_alerts)


DEFAULT_REPORT_PATH = Path.home() / ".multica-board" / "run-metrics.v0.1.json"


def resolve_path(explicit, env):
    if explicit:
        return Path(explicit)
    raw = (env or {}).get("RUN_METRICS_REPORT")
    if raw == "":
        return None
    if raw:
        return Path(raw)
    return DEFAULT_REPORT_PATH


def _api():
    root = None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "run-metrics"
        if (candidate / "run_metrics" / "consume.py").is_file():
            root = candidate
            break
    if root is None:
        raise ImportError("run-metrics package not found")
    entry = str(root)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    from run_metrics.consume import bind_own_alerts, load_consumption
    return bind_own_alerts, load_consumption


def _unavailable(own_alerts: dict) -> dict:
    return {
        "readonly": True,
        "writes": [],
        "dispatch_changed": False,
        "alerts_changed": False,
        "classification_copied": False,
        "schema_version": "run-metrics.v0.1",
        "status": "invalid",
        "reason": "loader_unavailable",
        "message": "读不到统一结果模块。巡检继续使用原告警，不补算。",
        "generated_at": "missing",
        "rule_version": "missing",
        "default_window": "missing",
        "windows": "missing",
        "scheduling_fields": {
            name: "missing"
            for name in (
                "unknown_hard_disable",
                "historical_quota_excludes_agent",
                "excluded_agent_ids",
                "historical_quota_agent_ids",
                "signals",
            )
        },
        "runtime_reconciliation": "missing",
        "collection": "missing",
        "provider_unstable": "missing",
        "alert_delta": {
            "own_alerts": own_alerts,
            "unified_default_window": "missing",
            "own_alerts_preserved": True,
            "unified_counts_promoted_to_alerts": False,
            "dispatch_changed": False,
            "difference": "统一结果模块不可用。原告警保持不变，不把缺失写成 0。",
        },
    }
