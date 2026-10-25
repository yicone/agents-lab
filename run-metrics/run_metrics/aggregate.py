"""Aggregate projected runs into 24h / 7d / 14d windows."""

from __future__ import annotations

from collections import defaultdict

from .rules import CANDIDATE_SIGNALS, public_rules
from .timeutil import in_window, parse_ts

_TERMINAL = frozenset({"succeeded", "failed", "blocked", "cancelled"})
_CLASS_POPULATION = frozenset({"failed", "blocked", "cancelled"})


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _ratio_block(numerator: int, denominator: int, definition: str) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": _rate(numerator, denominator),
        "definition": definition,
    }


def _percentile(values: list[int], percent: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percent / 100)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return ordered[low]
    weight = rank - low
    return int(round(ordered[low] + (ordered[high] - ordered[low]) * weight))


def _coverage(population: int, known: int, unknown: int, missing: int, definition: str) -> dict:
    return {
        "definition": definition,
        "population": population,
        "known_count": known,
        "unknown_count": unknown,
        "missing_count": missing,
        "missing_rate": _rate(missing, population),
        "unknown_rate": _rate(unknown, population),
    }


def field_coverage(runs: list[dict]) -> dict:
    population = len(runs)
    started_missing = sum(1 for run in runs if not run.get("started_at"))
    ended_missing = sum(1 for run in runs if not run.get("ended_at"))
    duration_missing = sum(1 for run in runs if run.get("duration_ms") is None)
    status_missing = sum(1 for run in runs if not run.get("raw_status"))
    status_unknown = sum(1 for run in runs if run.get("raw_status") and run.get("status") == "unknown")
    agent_missing = sum(1 for run in runs if not run.get("agent_id"))
    runtime_missing = sum(1 for run in runs if not run.get("runtime_id"))
    applicable = [run for run in runs if run.get("status") in _CLASS_POPULATION]
    class_unknown = sum(1 for run in applicable if run.get("failure_class") == "unknown")
    class_missing = sum(1 for run in applicable if run.get("failure_class") is None)
    class_known = len(applicable) - class_unknown - class_missing
    return {
        "started_at": _coverage(population, population - started_missing, 0, started_missing, "started_at 为空即缺失，不用读取时间填补"),
        "ended_at": _coverage(population, population - ended_missing, 0, ended_missing, "ended_at 取 completed_at；为空即缺失"),
        "duration_ms": _coverage(population, population - duration_missing, 0, duration_missing, "仅 started_at 与 ended_at 都可解析且 end>=start 时已知"),
        "status": _coverage(population, population - status_missing - status_unknown, status_unknown, status_missing, "无法映射的 raw status 记 unknown，不猜测"),
        "agent_id": _coverage(population, population - agent_missing, 0, agent_missing, "空 agent_id 记缺失，分组键为 unknown"),
        "runtime_id": _coverage(population, population - runtime_missing, 0, runtime_missing, "空 runtime_id 记缺失，分组键为 unknown"),
        "failure_class": _coverage(
            len(applicable),
            class_known,
            class_unknown,
            class_missing,
            "分母是 failed、blocked、cancelled；规则外为 unknown，不是缺失",
        ),
    }


def _class_counts(runs: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for run in runs:
        failure_class = run.get("failure_class")
        if failure_class:
            counts[failure_class] += 1
    return dict(counts)


def outcome_block(runs: list[dict]) -> dict:
    succeeded = sum(1 for run in runs if run.get("status") == "succeeded")
    failed = sum(1 for run in runs if run.get("status") == "failed")
    blocked = sum(1 for run in runs if run.get("status") == "blocked")
    cancelled = sum(1 for run in runs if run.get("status") == "cancelled")
    running = sum(1 for run in runs if run.get("status") == "running")
    queued = sum(1 for run in runs if run.get("status") == "queued")
    unknown_status = sum(1 for run in runs if run.get("status") == "unknown")
    terminal = succeeded + failed + blocked + cancelled
    excluding = terminal - cancelled
    durations = [run["duration_ms"] for run in runs if isinstance(run.get("duration_ms"), int)]
    class_counts = _class_counts(runs)
    class_population = sum(class_counts.values())
    failed_population = sum(
        count for name, count in class_counts.items() if name != "cancelled"
    )
    return {
        "run_count": len(runs),
        "succeeded_count": succeeded,
        "completed_count": succeeded,
        "failed_count": failed,
        "blocked_count": blocked,
        "cancelled_count": cancelled,
        "running_count": running,
        "queued_count": queued,
        "unknown_status_count": unknown_status,
        "success_rate": {
            "including_cancelled": _ratio_block(
                succeeded, terminal, "succeeded / (succeeded+failed+blocked+cancelled)；非终态不计入"
            ),
            "excluding_cancelled": _ratio_block(
                succeeded, excluding, "succeeded / (succeeded+failed+blocked)；cancelled 与非终态不计入"
            ),
        },
        "failure_rate": {
            "including_cancelled": _ratio_block(
                failed, terminal, "failed / (succeeded+failed+blocked+cancelled)"
            ),
            "excluding_cancelled": _ratio_block(
                failed, excluding, "failed / (succeeded+failed+blocked)"
            ),
        },
        "failure_class_counts": class_counts,
        "failure_class_rates_excluding_cancelled": {
            name: _ratio_block(count, failed_population, "该分类 / (失败分类中排除 cancelled)")
            for name, count in class_counts.items()
            if name != "cancelled"
        },
        "failure_class_population": class_population,
        "duration_p50_ms": _percentile(durations, 50),
        "duration_p95_ms": _percentile(durations, 95),
        "duration_sample_count": len(durations),
        "field_coverage": field_coverage(runs),
    }


def _group(runs: list[dict], key: str) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for run in runs:
        grouped[run.get(key) or "unknown"].append(run)
    result = {}
    for group_id in sorted(grouped):
        block = outcome_block(grouped[group_id])
        block.pop("field_coverage", None)
        block.pop("failure_class_rates_excluding_cancelled", None)
        result[group_id] = block
    return result


def rule_hits(runs: list[dict]) -> list[dict]:
    counts: dict[str, int] = defaultdict(int)
    for run in runs:
        rule_id = run.get("failure_rule_id")
        if rule_id:
            counts[rule_id] += 1
    rows = []
    for rule in public_rules():
        row = dict(rule)
        row["hit_count"] = counts.get(rule["id"], 0)
        rows.append(row)
    return rows


def candidate_summary(runs: list[dict]) -> list[dict]:
    counts: dict[str, list[str]] = {row["id"]: [] for row in CANDIDATE_SIGNALS}
    for run in runs:
        for signal_id in run.get("provider_candidate_ids") or []:
            if signal_id not in counts:
                counts[signal_id] = []
            counts[signal_id].append(run.get("run_id"))
    rows = []
    for spec in CANDIDATE_SIGNALS:
        ids = [run_id for run_id in counts.get(spec["id"], []) if run_id]
        rows.append({
            "id": spec["id"],
            "condition": spec["condition"],
            "produces_failure_class": False,
            "sample_count": len(ids),
            "sample_run_ids": ids[:20],
        })
    return rows


def rework_summary(issue_count: int, inferred: dict) -> dict:
    confidence = inferred["issue_confidence"]
    explicit = sum(1 for value in confidence.values() if value == "explicit")
    inferred_count = sum(1 for value in confidence.values() if value == "inferred")
    unknown = sum(1 for value in confidence.values() if value == "unknown")
    not_candidate = sum(1 for value in confidence.values() if value == "not_candidate")
    candidates = explicit + inferred_count + unknown
    proven = explicit + inferred_count
    events = defaultdict(int)
    for chain in inferred["chains"]:
        events[chain["confidence"]] += 1
    claim = "lower_bound_only" if unknown else "point"
    lower = _ratio_block(
        proven,
        issue_count,
        "有 explicit 或 inferred 返工链的 issue / 窗口内 issue；unknown 不计入分子",
    )
    return {
        "rework_coverage": {
            "population": "rework_candidate_issues",
            "candidate_count": candidates,
            "explicit_count": explicit,
            "inferred_count": inferred_count,
            "unknown_count": unknown,
            "not_candidate_count": not_candidate,
            "explicit_ratio": _rate(explicit, candidates),
            "inferred_ratio": _rate(inferred_count, candidates),
            "unknown_ratio": _rate(unknown, candidates),
            "proven_ratio": _rate(proven, candidates),
        },
        "rework_events": {
            "explicit": events["explicit"],
            "inferred": events["inferred"],
            "unknown": events["unknown"],
        },
        "rework_count": proven,
        "rework_rate": lower if claim == "point" else None,
        "rework_rate_lower_bound": lower,
        "rework_rate_claim": claim,
        "rework_rate_note": (
            "候选 issue 中仍有 unknown，未批准覆盖率阈值，因此只给出返工率下限。"
            if claim == "lower_bound_only"
            else "候选 issue 均已归入 explicit 或 inferred，返工率等于下限。"
        ),
        "chains": inferred["chains"],
    }


def filter_runs(runs: list[dict], start, end) -> list[dict]:
    return [run for run in runs if in_window(parse_ts(run.get("anchor_at")), start, end)]


def filter_issues(issues: list[dict], start, end) -> list[dict]:
    selected = []
    for issue in issues:
        created = parse_ts(issue.get("created_at"))
        updated = parse_ts(issue.get("updated_at"))
        if in_window(created, start, end) or in_window(updated, start, end):
            selected.append(issue)
    return selected
