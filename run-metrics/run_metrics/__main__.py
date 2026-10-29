"""CLI for the read-only run-metrics prototype.

Examples:
  python3 -m run_metrics aggregate --input snapshot.json --output report.json
  python3 -m run_metrics report --output run-metrics/out/acm-544-report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .collect import collect_snapshot
from .report import build_report, summarize
from .shadow import build_shadow
from .timeutil import parse_ts


def _dump(path: str, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _shadow(args) -> int:
    source = Path(args.input)
    target = Path(args.output)
    if source.resolve() == target.resolve():
        raise SystemExit("shadow 输出不能覆盖输入报告")
    try:
        before = source.read_bytes()
    except OSError as exc:
        raise SystemExit(f"无法读取报告：{exc.strerror or exc}") from exc
    try:
        report = json.loads(before)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise SystemExit("输入不是合法 JSON，未写出建议") from exc
    built = build_shadow(report)
    if source.read_bytes() != before:
        raise SystemExit("输入报告被改动，已中止")
    _dump(args.output, built)
    print(json.dumps({
        "status": built["status"],
        "suggestion_count": len(built["suggestions"]),
        "dispatch_changed": built["dispatch_changed"],
        "writes": built["writes"],
    }, ensure_ascii=False), file=sys.stderr)
    return 0


def _parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = parse_ts(value)
    if parsed is None:
        raise SystemExit(f"无法解析 --now {value}")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_metrics")
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="只读采集快照，不聚合")
    collect.add_argument("--output", required=True)
    collect.add_argument("--now")
    collect.add_argument("--workers", type=int, default=6)
    collect.add_argument("--window", default="14d")

    aggregate = sub.add_parser("aggregate", help="从快照生成报告")
    aggregate.add_argument("--input", required=True)
    aggregate.add_argument("--output", required=True)
    aggregate.add_argument("--summary")

    report = sub.add_parser("report", help="只读采集并生成报告")
    report.add_argument("--output", required=True)
    report.add_argument("--summary")
    report.add_argument("--snapshot")
    report.add_argument("--now")
    report.add_argument("--workers", type=int, default=6)
    report.add_argument("--window", default="14d")

    shadow = sub.add_parser("shadow", help="从已有报告记录建议路由，不改派单")
    shadow.add_argument("--input", required=True)
    shadow.add_argument("--output", required=True)

    args = parser.parse_args(argv)
    if args.command == "shadow":
        return _shadow(args)
    if args.command == "collect":
        snapshot = collect_snapshot(now=_parse_now(args.now), workers=args.workers, window=args.window)
        _dump(args.output, snapshot)
        return 0
    if args.command == "aggregate":
        built = build_report(_load(args.input))
        _dump(args.output, built)
        if args.summary:
            _dump(args.summary, summarize(built))
        return 0
    snapshot = collect_snapshot(now=_parse_now(args.now), workers=args.workers, window=args.window)
    if args.snapshot:
        _dump(args.snapshot, snapshot)
    built = build_report(snapshot)
    _dump(args.output, built)
    if args.summary:
        _dump(args.summary, summarize(built))
    window = built["windows"][built["default_window"]]
    print(json.dumps({
        "window": built["default_window"],
        "issues": window["issue_count"],
        "runs": window["run_count"],
        "provider_unstable_run_count": built["provider_unstable_class"]["run_count"],
        "rework_claim": window["rework"]["rework_rate_claim"],
        "runtime_observed": built["runtime_reconciliation"]["observed_count"],
    }, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
