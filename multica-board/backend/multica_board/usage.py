"""Local usage snapshot: CodexBar quota windows and Multica token totals.

The two sources use different units. CodexBar reports window percentages (and,
for Codex, credits that this module does not store). Multica reports token
counts and cost ticks per runtime-day. Nothing here adds them together.

`collect-usage` writes a raw snapshot. `GET /api/usage` decorates signals from
that snapshot and counts runs from the SQLite task cache at read time.
"""

from __future__ import annotations

from .stdlib_path import prefer_stdlib_imports

prefer_stdlib_imports()

import json
import logging
import math
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .cli_runner import CliError, CliRunner
from .collector import CollectOptions, iso, utcnow
from .redact import classify_error, redact
from .store import Store

log = logging.getLogger("multica_board.usage")

USAGE_NOTE = (
    "Multica token usage and codexbar quota percentages use independent accounting and must not be summed."
)
CODEXBAR_PROVIDERS = ("cursor", "grok", "codex")
UNCONNECTED_PROVIDERS = ("9router", "orca")
HISTORY_RETENTION_DAYS = 90
SAMPLE_MAX_AGE_S = 2700
DEFAULT_CODEXBAR = "/opt/homebrew/bin/codexbar"
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd_ticks",
)
COUNT_FIELDS = (
    "runs_total",
    "completed",
    "failed_total",
    "capacity_failed",
    "rate_limit_failed",
    "quota_failed",
    "true_failed",
    "cancelled",
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_DROP_KEYS = frozenset({
    "accountemail",
    "accountorganization",
    "loginmethod",
    "identity",
    "accountid",
    "providerid",
    "providercost",
    "id",
    "balanceisworkspace",
    "events",
})
_WINDOW_SLOTS = ("primary", "secondary", "tertiary")


def redact_usage(text: str | None) -> str | None:
    if text is None:
        return None
    return redact(_EMAIL.sub("[redacted]", str(text)))


def scrub(obj: Any) -> Any:
    """Drop identity fields and emails before anything is written or returned."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if not isinstance(key, str):
                continue
            folded = key.casefold()
            if folded in _DROP_KEYS or folded.endswith("email"):
                continue
            out[key] = scrub(value)
        return out
    if isinstance(obj, list):
        return [scrub(item) for item in obj]
    if isinstance(obj, str):
        return _EMAIL.sub("[redacted]", obj)
    return obj


def snapshot_path_for(db_path: str | Path) -> Path | None:
    if str(db_path) == ":memory:":
        return None
    return Path(db_path).parent / "usage-snapshot.json"


def paths_beside(db_path: str | Path) -> tuple[Path, Path]:
    parent = Path(db_path).parent
    return parent / "usage-snapshot.json", parent / "usage-history.jsonl"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def _int(value: Any) -> int:
    number = _num(value)
    if number is None or number < 0 or not number.is_integer():
        return 0
    return int(number)


def _strict_int(value: Any, field: str) -> int:
    number = _num(value)
    if number is None or number < 0 or not number.is_integer():
        raise ValueError(f"invalid {field}")
    return int(number)


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        flat = " ".join(value.split())
        return flat or None
    return None


def _parse_time(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _utc_day(value: str | None) -> str | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return parsed.strftime("%Y-%m-%d")


def _pace(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    expected = _num(raw.get("expectedUsedPercent"))
    delta = _num(raw.get("deltaPercent"))
    will = raw.get("willLastToReset")
    stage = _text(raw.get("stage"))
    if expected is None and delta is None and not isinstance(will, bool) and stage is None:
        return None
    return {
        "expected_used_percent": None if expected is None else round(expected, 2),
        "delta_percent": None if delta is None else round(delta, 2),
        "will_last_to_reset": will if isinstance(will, bool) else None,
        "stage": stage,
    }


def _extra_key(title: str, seen: set[str]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.casefold()).strip("_") or "window"
    key = f"extra:{slug}"
    n = 2
    while key in seen:
        key = f"extra:{slug}_{n}"
        n += 1
    seen.add(key)
    return key


def _root_object(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        if len(payload) == 1 and isinstance(payload[0], dict):
            return payload[0]
        raise ValueError("codexbar payload must be one object")
    if isinstance(payload, dict):
        return payload
    raise ValueError("codexbar payload must be an object")


def extract_provider(provider: str, payload: Any) -> dict[str, Any]:
    """Whitelist CodexBar fields. Account, identity, cost, and id fields are ignored."""
    if provider not in CODEXBAR_PROVIDERS:
        raise ValueError("unsupported provider")
    root = _root_object(payload)
    usage = root.get("usage") if isinstance(root.get("usage"), dict) else {}
    labels = root.get("rateWindowLabels") if isinstance(root.get("rateWindowLabels"), dict) else {}
    pace_root = root.get("pace") if isinstance(root.get("pace"), dict) else {}
    windows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in _WINDOW_SLOTS:
        raw = usage.get(key)
        if not isinstance(raw, dict):
            continue
        used = _num(raw.get("usedPercent"))
        if used is None:
            continue
        seen.add(key)
        windows.append({
            "key": key,
            "label": _text(labels.get(key)) or key,
            "window_minutes": _num(raw.get("windowMinutes")),
            "used_percent": used,
            "resets_at": _text(raw.get("resetsAt")),
            "pace": _pace(pace_root.get(key)),
        })
    extras = usage.get("extraRateWindows")
    if isinstance(extras, list):
        for item in extras:
            if not isinstance(item, dict):
                continue
            title = _text(item.get("title")) or "window"
            box = item.get("window") if isinstance(item.get("window"), dict) else {}
            used = _num(box.get("usedPercent"))
            if used is None:
                continue
            windows.append({
                "key": _extra_key(title, seen),
                "label": title,
                "window_minutes": _num(box.get("windowMinutes")),
                "used_percent": used,
                "resets_at": _text(box.get("resetsAt")),
                "pace": None,
            })
    return {
        "provider": provider,
        "status": "ok",
        "source": _text(root.get("source")),
        "updated_at": _text(usage.get("updatedAt")),
        "last_success_at": None,
        "error": None,
        "windows": windows,
    }


def _error_obj(raw: Any) -> dict[str, str] | None:
    message = None
    if isinstance(raw, dict):
        message = raw.get("message")
    elif isinstance(raw, str):
        message = raw
    if not isinstance(message, str) or not message.strip():
        return None
    cleaned = redact_usage(message.strip())
    if not cleaned:
        return None
    return {"message": cleaned}


def provider_error(provider: str, previous: dict[str, Any] | None, message: str) -> dict[str, Any]:
    """Keep the last successful windows when a provider fails. Other providers are untouched."""
    windows = []
    source = None
    updated_at = None
    last_success = None
    if isinstance(previous, dict) and previous.get("provider") == provider and previous.get("windows"):
        windows = previous.get("windows") or []
        source = previous.get("source")
        updated_at = previous.get("updated_at")
        last_success = previous.get("last_success_at")
    return {
        "provider": provider,
        "status": "error",
        "source": source,
        "updated_at": updated_at,
        "last_success_at": last_success,
        "error": _error_obj(message) or {"message": "codexbar failed"},
        "windows": windows,
    }


def unconnected(provider: str) -> dict[str, Any]:
    return {"provider": provider, "status": "unconnected", "label": "未接入", "windows": []}


def hours_until(resets_at: str | None, now: datetime) -> float | None:
    parsed = _parse_time(resets_at)
    if parsed is None:
        return None
    return (parsed - now.astimezone(timezone.utc)).total_seconds() / 3600


def evaluate_signal(
    used_percent: float,
    window_minutes: float | None,
    hours_left: float | None,
    pace: dict[str, Any] | None,
) -> tuple[str, str | None]:
    """Quota signal for one window. Only evaluated while the reset is still in the future."""
    if hours_left is None or hours_left <= 0:
        return "normal", None
    remaining = 100 - used_percent
    pace_bad = False
    if pace:
        if pace.get("will_last_to_reset") is False:
            pace_bad = True
        delta = pace.get("delta_percent")
        if isinstance(delta, (int, float)) and not isinstance(delta, bool) and delta >= 15:
            pace_bad = True
    critical = used_percent >= 90 and hours_left >= 2
    warning = used_percent >= 80 and pace_bad
    if critical or warning:
        kind = "critical" if critical else "warning"
        return "exhausting", f"{used_percent:.1f}% used with {hours_left:.1f}h until reset ({kind})"
    if window_minutes is not None:
        horizon = 48 if window_minutes <= 10080 else 72
        if 0 < hours_left <= horizon and remaining >= 30:
            return "dispatchable", f"{remaining:.1f}% remaining with {hours_left:.1f}h until reset"
    return "normal", None


def quota_band(used: float) -> str:
    for threshold, band in ((99.9, "exhausted"), (95, "critical"), (85, "warning"), (70, "watch")):
        if used >= threshold:
            return band
    return "normal"


def sample_freshness(item: dict[str, Any], now: datetime) -> tuple[float | None, bool]:
    # A successful fetch must not make an old upstream sample look fresh.
    times = [_parse_time(item.get(field)) for field in ("updated_at", "last_success_at")]
    known = [at for at in times if at is not None]
    age = max(0.0, (now - min(known)).total_seconds()) if known else None
    return (None if age is None else round(age, 1), age is None or age > SAMPLE_MAX_AGE_S)


def decorate_window(
    window: dict[str, Any], now: datetime, *, sample_age_s: float | None = None, stale: bool = False,
) -> dict[str, Any]:
    used = float(window["used_percent"])
    hours = hours_until(window.get("resets_at"), now)
    signal, reason = evaluate_signal(used, window.get("window_minutes"), hours, window.get("pace"))
    if stale:
        signal, reason = "normal", "stale sample; quota unknown"
    # ETA is anchored to the observation, not to each GET. Past forecasts are
    # no longer predictions; reaching a threshold in the sample is immediate.
    def eta(field: str, threshold: float) -> str | None:
        if stale or hours is None or hours <= 0:
            return None
        if used >= threshold:
            return window.get("sample_at")
        value = window.get(field)
        parsed = _parse_time(value)
        return value if parsed and now < parsed <= _parse_time(window.get("resets_at")) else None

    return {
        "key": window.get("key"),
        "label": window.get("label"),
        "window_minutes": window.get("window_minutes"),
        "used_percent": round(used, 2),
        "remaining_percent": round(100 - used, 2),
        "resets_at": window.get("resets_at"),
        "hours_until_reset": None if hours is None else round(hours, 1),
        "pace": window.get("pace"),
        "signal": signal,
        "signal_reason": reason,
        "band": "unknown" if stale else quota_band(used),
        "sample_age_s": sample_age_s,
        "stale": stale,
        "delta_24h_percent": None if stale else window.get("delta_24h_percent"),
        "daily_delta": [] if stale else window.get("daily_delta", []),
        "eta_exhausted_at": eta("eta_exhausted_at", 100),
        "eta_warning_at": eta("eta_warning_at", 85),
        "eta_source": None if stale else window.get("eta_source"),
    }


def _known_quotas(raw: Any) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("provider") in CODEXBAR_PROVIDERS and item["provider"] not in found:
                found[item["provider"]] = item
    return [found[name] for name in CODEXBAR_PROVIDERS if name in found]


def present_quotas(raw: Any, now: datetime) -> list[dict[str, Any]]:
    presented: list[dict[str, Any]] = []
    for item in _known_quotas(raw):
        age, stale = sample_freshness(item, now)
        windows = []
        for window in item.get("windows") or []:
            if isinstance(window, dict) and _num(window.get("used_percent")) is not None:
                decorated = decorate_window(window, now, sample_age_s=age, stale=stale)
                if item["provider"] == "cursor" and window.get("key") == "tertiary":
                    decorated["label"] = "Third Party"
                windows.append(decorated)
        presented.append({
            "provider": item["provider"],
            "status": item.get("status") if item.get("status") in ("ok", "error") else "error",
            "source": _text(item.get("source")),
            "updated_at": _text(item.get("updated_at")),
            "last_success_at": _text(item.get("last_success_at")),
            "error": _error_obj(item.get("error")),
            "sample_age_s": age,
            "stale": stale,
            "windows": windows,
        })
    presented.extend(unconnected(name) for name in UNCONNECTED_PROVIDERS)
    return presented


def collect_signals(quotas: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    dispatchable: list[dict[str, Any]] = []
    exhausting: list[dict[str, Any]] = []
    for quota in quotas:
        if quota.get("status") == "unconnected":
            continue
        for window in quota.get("windows") or []:
            if window.get("signal") not in ("dispatchable", "exhausting"):
                continue
            item = {
                "provider": quota.get("provider"),
                "window_key": window.get("key"),
                "label": window.get("label"),
                "remaining_percent": window.get("remaining_percent"),
                "resets_at": window.get("resets_at"),
                "hours_until_reset": window.get("hours_until_reset"),
            }
            if window.get("signal") == "dispatchable":
                dispatchable.append(item)
            else:
                exhausting.append(item)
    return {"dispatchable": dispatchable, "exhausting": exhausting}


def quota_alerts_for(snapshot: dict[str, Any] | None, now: datetime) -> list[dict[str, Any]]:
    if not snapshot:
        return []
    alerts = []
    for quota in present_quotas(snapshot.get("external_quotas"), now):
        provider = str(quota.get("provider") or "")
        for window in quota.get("windows") or []:
            if window.get("band") not in ("warning", "critical", "exhausted", "unknown"):
                continue
            used = window.get("used_percent")
            hours = window.get("hours_until_reset")
            label = window.get("label") or window.get("key") or ""
            title_name = " ".join(part for part in (provider.replace("_", " ").title(), str(label)) if part)
            if isinstance(hours, (int, float)) and not isinstance(hours, bool):
                title = f"{title_name} 额度已用 {float(used):.0f}%（距重置 {float(hours):.0f}h）"
            else:
                title = f"{title_name} 额度已用 {float(used):.0f}%"
            if window["stale"]:
                age = window["sample_age_s"]
                title = f"{title_name} 额度未知（" + (f"数据为 {age / 3600:.1f}h 前采样" if age is not None else "无采样时间") + "）"
            alerts.append({
                "identifier": f"{provider}:{window.get('key')}",
                "title": title,
                "status": "quota_unknown" if window["stale"] else "quota_exhausting",
                "time": quota.get("updated_at"),
                "agent_name": None,
                "provider": provider,
                "window_key": window.get("key"),
                "label": label,
                **{key: window.get(key) for key in (
                    "band", "used_percent", "remaining_percent", "resets_at", "hours_until_reset",
                    "sample_age_s", "stale", "delta_24h_percent", "daily_delta",
                    "eta_exhausted_at", "eta_warning_at", "eta_source",
                )},
            })
    alerts.sort(key=lambda item: (item["identifier"] or "", item["title"] or ""))
    return alerts


def _blank() -> dict[str, Any]:
    return {name: 0 for name in (*COUNT_FIELDS, *TOKEN_FIELDS)}


def _effective_kind(task: dict[str, Any]) -> str | None:
    kind = task.get("error_kind")
    reason = task.get("failure_reason")
    if isinstance(reason, str) and kind in (None, "", "unknown", "model_unavailable"):
        upgraded = classify_error(None, reason)
        if upgraded:
            return upgraded
    return kind if isinstance(kind, str) and kind else None


def _public_day(cell: dict[str, Any]) -> dict[str, Any]:
    day = {"date": cell["date"]}
    day.update({name: cell[name] for name in (*COUNT_FIELDS, *TOKEN_FIELDS)})
    return day


def clean_hourly(data: Any, *, workspace_id: str | None = None) -> list[dict[str, int]]:
    if not isinstance(data, list):
        return []
    rows = []
    scope_mismatch = False
    for item in data:
        if not isinstance(item, dict):
            continue
        if workspace_id is not None and item.get("workspace_id") is not None and item.get("workspace_id") != workspace_id:
            scope_mismatch = True
            continue
        hour = item.get("hour")
        count = item.get("count")
        if isinstance(hour, bool) or isinstance(count, bool):
            continue
        if not isinstance(hour, int) or not isinstance(count, int):
            continue
        if not 0 <= hour <= 23 or count < 0:
            continue
        rows.append({"hour": hour, "count": count})
    if scope_mismatch:
        raise ValueError("workspace scope mismatch in activity data")
    rows.sort(key=lambda row: row["hour"])
    return rows


def sum_usage_rows(data: Any, runtime_id: str, *, workspace_id: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        return []
    acc: dict[str, dict[str, Any]] = {}
    scope_mismatch = False
    for row in data:
        if not isinstance(row, dict):
            continue
        owner = row.get("runtime_id")
        if owner and owner != runtime_id:
            continue
        if workspace_id is not None and row.get("workspace_id") is not None and row.get("workspace_id") != workspace_id:
            scope_mismatch = True
            continue
        day = row.get("date")
        if not isinstance(day, str) or not _DATE.fullmatch(day):
            continue
        cell = acc.setdefault(day, {"date": day, **{name: 0 for name in TOKEN_FIELDS}})
        for name in TOKEN_FIELDS:
            cell[name] += _strict_int(row.get(name), name)
    if scope_mismatch:
        raise ValueError("workspace scope mismatch in usage data")
    return [acc[day] for day in sorted(acc)]


def assemble_multica(
    tasks: list[dict[str, Any]],
    runtimes: list[dict[str, Any]],
    workspaces: list[dict[str, Any]],
    token_rows: list[dict[str, Any]],
    *,
    days: int,
    now: datetime,
) -> dict[str, Any]:
    days = max(1, min(int(days), 90))
    start = (now.astimezone(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
    today = now.astimezone(timezone.utc).date().isoformat()
    runtime_meta = {(row.get("workspace_id"), row.get("id")): row for row in runtimes if row.get("id")}
    workspace_meta = {row.get("id"): row for row in workspaces if row.get("id")}
    cells: dict[tuple[str, str, str], dict[str, Any]] = {}
    hourly: dict[tuple[str, str], list[dict[str, int]]] = {}

    def slot(ws: str, rid: str, day: str, provider: Any, name: Any) -> dict[str, Any]:
        key = (ws, rid, day)
        if key not in cells:
            cells[key] = {
                **_blank(),
                "workspace_id": ws,
                "runtime_id": rid,
                "date": day,
                "provider": provider,
                "name": name,
            }
        elif provider and not cells[key].get("provider"):
            cells[key]["provider"] = provider
        if name and not cells[key].get("name"):
            cells[key]["name"] = name
        return cells[key]

    for task in tasks:
        day = _utc_day(task.get("completed_at") or task.get("started_at") or task.get("created_at"))
        if day is None or day < start or day > today:
            continue
        ws = task.get("workspace_id")
        rid = task.get("runtime_id") or ""
        if not ws:
            continue
        meta = runtime_meta.get((ws, rid)) or {}
        cell = slot(ws, rid, day, meta.get("provider"), meta.get("custom_name") or meta.get("name"))
        cell["runs_total"] += 1
        status = (task.get("status") or "").casefold()
        kind = _effective_kind(task)
        if status == "completed":
            cell["completed"] += 1
        elif status == "cancelled" or (status == "failed" and kind == "cancelled"):
            cell["cancelled"] += 1
        elif status == "failed" and kind in ("rate_limit", "quota"):
            cell["capacity_failed"] += 1
            cell["failed_total"] += 1
            if kind == "rate_limit":
                cell["rate_limit_failed"] += 1
            else:
                cell["quota_failed"] += 1
        elif status == "failed":
            cell["true_failed"] += 1
            cell["failed_total"] += 1

    for runtime in token_rows:
        if not isinstance(runtime, dict):
            continue
        ws = runtime.get("workspace_id")
        rid = runtime.get("runtime_id")
        if not isinstance(ws, str) or not isinstance(rid, str) or not ws or not rid:
            continue
        meta = runtime_meta.get((ws, rid)) or {}
        provider = meta.get("provider") or runtime.get("provider")
        name = meta.get("custom_name") or meta.get("name") or runtime.get("name")
        hourly[(ws, rid)] = clean_hourly(runtime.get("hourly_activity"))
        for day_row in runtime.get("daily") or []:
            if not isinstance(day_row, dict):
                continue
            day = day_row.get("date")
            if not isinstance(day, str) or day < start or day > today:
                continue
            cell = slot(ws, rid, day, provider, name)
            for name_field in TOKEN_FIELDS:
                cell[name_field] += _strict_int(day_row.get(name_field), name_field)

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for (ws, rid, _day), cell in cells.items():
        grouped.setdefault((ws, rid), []).append(cell)

    by_runtime = []
    for (ws, rid), rows in grouped.items():
        rows.sort(key=lambda item: item["date"])
        meta = runtime_meta.get((ws, rid)) or {}
        info = workspace_meta.get(ws) or {}
        by_runtime.append({
            "key": f"{ws}:{rid}",
            "workspace_id": ws,
            "workspace_slug": info.get("slug"),
            "runtime_id": rid,
            "name": meta.get("custom_name") or meta.get("name") or rows[0].get("name"),
            "provider": meta.get("provider") or rows[0].get("provider"),
            "daily": [_public_day(item) for item in rows],
            "hourly_activity": hourly.get((ws, rid), []),
        })
    by_runtime.sort(key=lambda item: (
        (item.get("workspace_slug") or "").casefold(),
        (item.get("name") or "").casefold(),
        item.get("runtime_id") or "",
    ))

    by_provider_cells: dict[tuple[str, str], dict[str, Any]] = {}
    for cell in cells.values():
        provider = cell.get("provider")
        if not isinstance(provider, str) or not provider:
            continue
        key = (provider, cell["date"])
        acc = by_provider_cells.setdefault(key, {**_blank(), "provider": provider, "date": cell["date"]})
        for name_field in (*COUNT_FIELDS, *TOKEN_FIELDS):
            acc[name_field] += cell[name_field]
    by_provider = [
        {"provider": key[0], "date": key[1], **{name: acc[name] for name in (*COUNT_FIELDS, *TOKEN_FIELDS)}}
        for key, acc in by_provider_cells.items()
    ]
    by_provider.sort(key=lambda item: (item["provider"], item["date"]))
    return {"note": USAGE_NOTE, "by_provider": by_provider, "by_runtime": by_runtime}


def _snapshot_state(snapshot: dict[str, Any] | None, quotas: list[dict[str, Any]]) -> str:
    if snapshot is None:
        return "missing"
    recorded = snapshot.get("snapshot_state")
    if recorded in {"partial", "error", "empty"}:
        return recorded
    collected = [item for item in quotas if item.get("status") != "unconnected"]
    if not collected:
        return "empty"
    if any(item.get("status") == "error" for item in collected):
        return "partial" if any(item.get("status") == "ok" for item in collected) else "error"
    return "ok"


def build_usage_response(
    snapshot: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
    runtimes: list[dict[str, Any]],
    workspaces: list[dict[str, Any]],
    *,
    now: datetime,
) -> dict[str, Any]:
    days = 14
    collected_at = None
    token_rows: list[dict[str, Any]] = []
    raw_quotas: Any = []
    collection_errors: list[str] = []
    recorded_state: str | None = None
    if isinstance(snapshot, dict):
        if isinstance(snapshot.get("days"), int) and snapshot["days"] > 0:
            days = snapshot["days"]
        collected_at = _text(snapshot.get("usage_collected_at"))
        token_rows = snapshot.get("token_by_runtime") or []
        raw_quotas = snapshot.get("external_quotas")
        recorded_state = snapshot.get("snapshot_state") if isinstance(snapshot.get("snapshot_state"), str) else None
        collection_errors = [str(item) for item in (snapshot.get("collection_errors") or []) if item]
    quotas = present_quotas(raw_quotas, now)
    return {
        "generated_at": iso(now),
        "usage_collected_at": collected_at,
        "days": days,
        "snapshot_state": recorded_state or _snapshot_state(snapshot, quotas),
        "collection_errors": collection_errors,
        "external_quotas": quotas,
        "signals": collect_signals(quotas),
        "multica_daily": assemble_multica(tasks, runtimes, workspaces, token_rows, days=days, now=now),
    }


def load_snapshot(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    return scrub(data)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def write_snapshot(path: Path, payload: dict[str, Any]) -> None:
    body = json.dumps(scrub(payload), ensure_ascii=False, indent=2).encode()
    _atomic_write(path, body)


def read_history(path: Path) -> list[dict[str, Any]]:
    """Read history only during collection; tolerate truncated/corrupt records."""
    records = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(item, dict) and _parse_time(item.get("at")):
                    records.append(item)
    except (OSError, UnicodeError):
        pass
    return sorted(records, key=lambda item: _parse_time(item["at"]))


def enrich_quota_trends(quotas: list[dict[str, Any]], history: list[dict[str, Any]], now: datetime) -> None:
    """Persist same-cycle observations and forecasts before writing the snapshot."""
    for quota in _known_quotas(quotas):
        _, stale = sample_freshness(quota, now)
        at = _parse_time(quota.get("updated_at")) or _parse_time(quota.get("last_success_at"))
        for window in quota.get("windows") or []:
            window.update(delta_24h_percent=None, daily_delta=[], eta_exhausted_at=None,
                          eta_warning_at=None, eta_source=None, sample_at=iso(at) if at else None)
            reset = _parse_time(window.get("resets_at"))
            used = _num(window.get("used_percent"))
            if stale or quota.get("status") != "ok" or not at or not reset or reset <= at or used is None:
                continue
            samples: dict[datetime, float] = {}
            for record in history:
                if _parse_time(record["at"]) > at:
                    continue
                providers = record.get("providers")
                for old_quota in providers if isinstance(providers, list) else []:
                    if not isinstance(old_quota, dict) or old_quota.get("provider") != quota["provider"]:
                        continue
                    windows = old_quota.get("windows")
                    for old in windows if isinstance(windows, list) else []:
                        if not isinstance(old, dict) or old.get("key") != window.get("key"):
                            continue
                        # Even A -> B -> A is a discontinuity, not one cycle.
                        if _parse_time(old.get("resets_at")) != reset:
                            samples.clear()
                            continue
                        if old_quota.get("status") != "ok" or old_quota.get("stale") is True:
                            continue
                        old_at = _parse_time(old.get("sample_at") or old_quota.get("updated_at") or record["at"])
                        old_used = _num(old.get("used_percent"))
                        if old_at and old_at < at and old_used is not None:
                            samples[old_at] = old_used
            # A decrease without a changed resets_at is also an unsafe trend.
            samples[at] = used
            ordered = sorted(samples.items())
            start = 0
            for index in range(1, len(ordered)):
                if ordered[index][1] < ordered[index - 1][1]:
                    start = index
            ordered = ordered[start:]
            target = at - timedelta(hours=24)
            anchors = [sample for sample in ordered if sample[0] <= target]
            rate = None
            source = None
            if anchors:
                anchor_at, anchor_used = anchors[-1]
                delta = used - anchor_used
                window["delta_24h_percent"] = round(delta, 2)
                rate = delta / ((at - anchor_at).total_seconds() / 3600)
                source = "history_24h"
            elif len(ordered) >= 3:
                nonzero = [sample for sample in ordered if sample[1] > 0]
                if nonzero and nonzero[0][0] < at:
                    anchor_at, anchor_used = nonzero[0]
                    rate = (used - anchor_used) / ((at - anchor_at).total_seconds() / 3600)
                    source = "history_cycle"
            daily: dict[str, list[float]] = {}
            for sample_at, sample_used in ordered:
                daily.setdefault(sample_at.date().isoformat(), []).append(sample_used)
            window["daily_delta"] = [
                {"date": day, "delta_percent": round(values[-1] - values[0], 2) if len(values) >= 2 else None}
                for day, values in daily.items()
            ]
            # Provider pace supplies elapsed expected consumption, not a direct
            # exhaustion timestamp. Use its implied elapsed window for average rate.
            pace = window.get("pace") or {}
            expected = _num(pace.get("expected_used_percent"))
            minutes = _num(window.get("window_minutes"))
            if expected is not None and expected > 0 and minutes is not None and minutes > 0:
                rate = used / (minutes / 60 * expected / 100)
                source = "pace"
            if rate is not None and rate > 0:
                window["eta_source"] = source
            for field, threshold in (("eta_exhausted_at", 100), ("eta_warning_at", 85)):
                if used >= threshold:
                    window[field] = iso(at)
                elif rate is not None and rate > 0:
                    hours = (threshold - used) / rate
                    # Bound before timedelta construction and don't forecast past reset.
                    if hours <= (reset - at).total_seconds() / 3600:
                        window[field] = iso(at + timedelta(hours=hours))


def history_from(response: dict[str, Any]) -> dict[str, Any]:
    providers = []
    for quota in response.get("external_quotas") or []:
        providers.append({
            "provider": quota.get("provider"),
            "status": quota.get("status"),
            "updated_at": quota.get("updated_at"),
            "stale": quota.get("stale"),
            "windows": [
                {
                    "key": window.get("key"),
                    "used_percent": window.get("used_percent"),
                    "remaining_percent": window.get("remaining_percent"),
                    "signal": window.get("signal"),
                    "resets_at": window.get("resets_at"),
                    "sample_at": quota.get("updated_at") or quota.get("last_success_at"),
                }
                for window in quota.get("windows") or []
            ],
        })
    daily = []
    for row in (response.get("multica_daily") or {}).get("by_provider") or []:
        daily.append({
            "provider": row.get("provider"),
            "date": row.get("date"),
            "runs_total": row.get("runs_total"),
            "capacity_failed": row.get("capacity_failed"),
            "true_failed": row.get("true_failed"),
            "input_tokens": row.get("input_tokens"),
            "output_tokens": row.get("output_tokens"),
            "cache_read_tokens": row.get("cache_read_tokens"),
            "cache_write_tokens": row.get("cache_write_tokens"),
            "cost_usd_ticks": row.get("cost_usd_ticks"),
        })
    return {"at": response.get("usage_collected_at") or response.get("generated_at"), "providers": providers, "daily": daily}


def write_history(path: Path, record: dict[str, Any], *, retention_days: int, now: datetime) -> None:
    cutoff = iso(now.astimezone(timezone.utc) - timedelta(days=max(1, retention_days)))
    kept: list[dict[str, Any]] = []
    if path.is_file():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            at = item.get("at") if isinstance(item, dict) else None
            if isinstance(at, str) and at >= cutoff:
                kept.append(scrub(item))
    kept.append(scrub(record))
    body = "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in kept).encode()
    _atomic_write(path, body)


def run_codexbar(
    binary: str,
    provider: str,
    *,
    timeout: float = 30,
    exec_: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Any:
    if provider not in CODEXBAR_PROVIDERS:
        raise ValueError("unsupported provider")
    argv = [binary, "usage", "--provider", provider, "--format", "json", "--no-color"]
    try:
        proc = exec_(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"timeout after {timeout:.0f}s") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("codexbar not found") from exc
    stderr = proc.stderr or ""
    if proc.returncode != 0:
        raise RuntimeError(redact_usage(stderr.strip() or f"exit code {proc.returncode}") or "codexbar failed")
    try:
        return json.loads(proc.stdout) if (proc.stdout or "").strip() else None
    except json.JSONDecodeError as exc:
        raise RuntimeError("codexbar returned non-JSON output") from exc


def _filter_workspaces(visible: list[dict[str, Any]], opts: CollectOptions) -> list[dict[str, Any]]:
    if opts.all_workspaces and not opts.workspaces:
        return visible
    wanted = set(opts.workspaces)
    return [row for row in visible if row.get("id") in wanted or (row.get("slug") and row["slug"] in wanted)]


class UsageCollector:
    def __init__(
        self,
        runner: CliRunner,
        store: Store,
        server: str,
        opts: CollectOptions,
        *,
        codexbar_bin: str = DEFAULT_CODEXBAR,
        usage_days: int = 14,
        snapshot_path: Path,
        history_path: Path,
        history_days: int = HISTORY_RETENTION_DAYS,
        codexbar_exec: Callable[..., subprocess.CompletedProcess] | None = None,
        now: datetime | None = None,
    ):
        self.runner = runner
        self.store = store
        self.server = server
        self.opts = opts
        self.codexbar_bin = codexbar_bin
        self.usage_days = max(1, min(int(usage_days), 90))
        self.snapshot_path = Path(snapshot_path)
        self.history_path = Path(history_path)
        self.history_days = max(1, int(history_days))
        self.codexbar_exec = codexbar_exec or subprocess.run
        self.now = now or utcnow()

    def collect(self) -> dict[str, Any]:
        previous = load_snapshot(self.snapshot_path) or {}
        previous_quotas = {
            item.get("provider"): item
            for item in previous.get("external_quotas") or []
            if isinstance(item, dict)
        }
        quotas = self._codexbar(previous_quotas)
        enrich_quota_trends(quotas, read_history(self.history_path), self.now)
        tokens, queried, errors = self._tokens(previous)
        for item in quotas:
            err = item.get("error")
            if item.get("status") == "error" and isinstance(err, dict) and err.get("message"):
                errors.append(f"{item.get('provider')}: {err['message']}")
        collected_at = iso(self.now)
        snapshot_state = "ok" if not errors else ("partial" if tokens or any(q.get("status") == "ok" for q in quotas) else "error")
        snapshot = scrub({
            "usage_collected_at": collected_at,
            "days": self.usage_days,
            "snapshot_state": snapshot_state,
            "collection_errors": errors,
            "external_quotas": quotas,
            "token_by_runtime": tokens,
        })
        write_snapshot(self.snapshot_path, snapshot)
        response = self._response(snapshot)
        write_history(self.history_path, history_from(response), retention_days=self.history_days, now=self.now)
        log.info(
            "usage snapshot providers=%s runtimes=%d errors=%d",
            {item["provider"]: item["status"] for item in quotas},
            queried,
            len(errors),
        )
        return {
            "ok": not bool(errors),
            "usage_collected_at": collected_at,
            "providers": {item["provider"]: item["status"] for item in quotas},
            "runtimes_queried": queried,
            "usage_errors": errors,
        }

    def _response(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return build_usage_response(
            snapshot,
            self.store.query(
                "SELECT workspace_id, runtime_id, status, error_kind, failure_reason, "
                "completed_at, started_at, created_at FROM tasks WHERE server=?",
                (self.server,),
            ),
            self.store.query(
                "SELECT workspace_id, id, name, custom_name, provider FROM runtimes WHERE server=?",
                (self.server,),
            ),
            self.store.query("SELECT id, slug, name FROM workspaces WHERE server=?", (self.server,)),
            now=self.now,
        )

    def _codexbar(self, previous: dict[str, Any]) -> list[dict[str, Any]]:
        def one(provider: str) -> dict[str, Any]:
            try:
                payload = run_codexbar(self.codexbar_bin, provider, exec_=self.codexbar_exec)
                extracted = extract_provider(provider, payload)
                extracted["last_success_at"] = iso(self.now)
                return extracted
            except Exception as exc:
                message = redact_usage(f"{type(exc).__name__}: {exc}") or "codexbar failed"
                log.warning("codexbar %s failed", provider)
                return provider_error(provider, previous.get(provider), message)

        with ThreadPoolExecutor(max_workers=len(CODEXBAR_PROVIDERS)) as pool:
            collected = list(pool.map(one, CODEXBAR_PROVIDERS))
        collected.extend(unconnected(name) for name in UNCONNECTED_PROVIDERS)
        return collected

    def _workspaces(self) -> tuple[list[dict[str, Any]], str | None]:
        try:
            result = self.runner.run(["workspace", "list"])
        except CliError as exc:
            cached = self.store.query("SELECT id, name, slug FROM workspaces WHERE server=?", (self.server,))
            return _filter_workspaces(cached, self.opts), str(exc)
        visible = []
        for row in result.data or []:
            if isinstance(row, dict) and row.get("id"):
                visible.append({"id": row.get("id"), "name": row.get("name"), "slug": row.get("slug")})
        return _filter_workspaces(visible, self.opts), None

    def _runtime_rows(self, ws: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
        cached = self.store.query(
            "SELECT id, name, custom_name, provider, status FROM runtimes WHERE server=? AND workspace_id=?",
            (self.server, ws["id"]),
        )
        if cached:
            return cached, None
        try:
            result = self.runner.run(["runtime", "list"], workspace_id=ws["id"])
        except CliError as exc:
            return [], str(exc)
        rows = []
        for raw in result.data or []:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            rows.append({
                "id": raw.get("id"),
                "name": raw.get("name"),
                "custom_name": raw.get("custom_name"),
                "provider": raw.get("provider"),
                "status": raw.get("status"),
            })
        return rows, None

    def _tokens(self, previous: dict[str, Any]) -> tuple[list[dict[str, Any]], int, list[str]]:
        errors: list[str] = []
        workspaces, workspace_error = self._workspaces()
        if workspace_error:
            errors.append(workspace_error)
        start = (self.now.astimezone(timezone.utc).date() - timedelta(days=max(1, self.opts.run_days) - 1)).isoformat()
        recent: set[tuple[str, str]] = set()
        for task in self.store.query(
            "SELECT workspace_id, runtime_id, completed_at, started_at, created_at FROM tasks WHERE server=?",
            (self.server,),
        ):
            day = _utc_day(task.get("completed_at") or task.get("started_at") or task.get("created_at"))
            if day and day >= start and task.get("workspace_id") and task.get("runtime_id"):
                recent.add((task["workspace_id"], task["runtime_id"]))
        seen_before = {
            (row.get("workspace_id"), row.get("runtime_id"))
            for row in previous.get("token_by_runtime") or []
            if isinstance(row, dict) and row.get("workspace_id") and row.get("runtime_id")
        }
        selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for ws in workspaces:
            rows, err = self._runtime_rows(ws)
            if err:
                errors.append(f"{ws.get('id')}: {err}")
            for row in rows:
                key = (ws["id"], row["id"])
                online = (row.get("status") or "").casefold() == "online"
                if online or key in recent or key in seen_before:
                    selected.append((ws, row))

        def fetch(item: tuple[dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
            ws, row = item
            rid = row["id"]
            old = next((x for x in previous.get("token_by_runtime") or []
                        if isinstance(x, dict) and x.get("workspace_id") == ws["id"] and x.get("runtime_id") == rid), {})
            entry: dict[str, Any] = {
                "workspace_id": ws["id"],
                "workspace_slug": ws.get("slug"),
                "runtime_id": rid,
                "name": row.get("custom_name") or row.get("name"),
                "provider": row.get("provider"),
                "daily": old.get("daily", []) if isinstance(old, dict) else [],
                "hourly_activity": old.get("hourly_activity", []) if isinstance(old, dict) else [],
            }
            try:
                usage = self.runner.run(
                    ["runtime", "usage", rid, "--days", str(self.usage_days)],
                    workspace_id=ws["id"],
                )
                entry["daily"] = sum_usage_rows(usage.data, rid, workspace_id=ws["id"])
            except CliError as exc:
                entry["usage_error"] = str(exc)
            except Exception as exc:
                entry["usage_error"] = redact_usage(f"{type(exc).__name__}: {exc}") or "usage failed"
            try:
                activity = self.runner.run(["runtime", "activity", rid], workspace_id=ws["id"])
                entry["hourly_activity"] = clean_hourly(activity.data, workspace_id=ws["id"])
            except CliError as exc:
                entry["activity_error"] = str(exc)
            except Exception as exc:
                entry["activity_error"] = redact_usage(f"{type(exc).__name__}: {exc}") or "activity failed"
            return entry

        fetched: list[dict[str, Any]] = []
        if selected:
            workers = max(1, self.opts.concurrency)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                fetched = list(pool.map(fetch, selected))
        stored = []
        for entry in fetched:
            if entry.get("usage_error"):
                errors.append(f"{entry['runtime_id']}: {entry['usage_error']}")
            if entry.get("activity_error"):
                errors.append(f"{entry['runtime_id']} activity: {entry['activity_error']}")
            stored.append({key: value for key, value in entry.items() if key not in ("usage_error", "activity_error")})
        current_keys = {(x.get("workspace_id"), x.get("runtime_id")) for x in stored}
        for old in previous.get("token_by_runtime") or []:
            if isinstance(old, dict) and (old.get("workspace_id"), old.get("runtime_id")) not in current_keys:
                stored.append(old)
        return stored, len(selected), errors
