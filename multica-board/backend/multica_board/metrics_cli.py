"""Steward read command and explicit local-only historical snapshot command.

Usage (from multica-board):
  PYTHONPATH=backend python3 -m multica_board metrics-backfill --snapshot baseline.json
  PYTHONPATH=backend python3 -m multica_board metrics --snapshot baseline.json
  PYTHONPATH=backend python3 -m multica_board metrics --snapshot baseline.json --threshold 3

Optional ledger is a JSON array of the events from metrics.md §4.2 plus coverage
attestations: event_type=coverage, channel=attention|rework|cost, since, until,
confidence=complete. Completion must have source=delivery_ledger and a nonempty
opaque delivery_evidence ID. Cost needs related_run_id and cost_usd_ticks (raw
units, never USD conversion). Approval and user_reply for one operation use the
same event_id. No completion or semantic classification is inferred from prose.
Waiting reasons: wait_open/wait_close, wait_reason=waiting_user|waiting_quota|
waiting_agent|waiting_unclassified. in_review defaults to waiting_agent.

metrics reads only local snapshots; it never refreshes, writes to Multica, runs
CodexBar, reads router databases or dispatches suggested actions. The existing
Board usage collector remains the sole source of external quota snapshots.
Re-run metrics-backfill to refresh; --resume retries failed deep reads using the
same frozen inventory and does not refresh inaccessible workspace inventories.
Use metrics-reconcile after two days of real
collection; a synthetic comparison does not satisfy the two-day requirement.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .metrics import build_report, reconcile, ts
from .metrics_history import collect_history, write_private

EVENT_TYPES = {"delivery_started", "delivery_completed", "delivery_adopted", "approval", "user_reply",
               "manual_handling", "rework", "wait_open", "wait_close", "cost_observed", "coverage"}
EVENT_FIELDS = {"workspace_id", "issue_id", "parent_issue_id", "event_id", "event_type", "at", "source",
                "confidence", "related_run_id", "wait_reason", "delivery_evidence", "channel", "since", "until",
                "cost_usd_ticks"}
ID = re.compile(r"^[A-Za-z0-9_.:/-]{1,256}$")


def load_ledger(path):
    if path is None:
        return []
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("ledger must be an array")
    clean = []
    for row in rows:
        if not isinstance(row, dict) or row.get("event_type") not in EVENT_TYPES:
            raise ValueError("invalid ledger event type")
        event = {k: v for k, v in row.items() if k in EVENT_FIELDS}
        for k in ("workspace_id", "issue_id", "parent_issue_id", "event_id", "related_run_id", "delivery_evidence"):
            if k in event and (not isinstance(event[k], str) or not ID.fullmatch(event[k])):
                raise ValueError(f"ledger {k} must be an opaque ID")
        if not event.get("event_id") or not event.get("workspace_id") or not (event.get("issue_id") or event.get("parent_issue_id")):
            raise ValueError("ledger requires workspace, issue and event IDs")
        if event.get("confidence") not in {"complete", "partial", "unknown"}:
            raise ValueError("invalid ledger confidence")
        if event["event_type"] == "coverage":
            if event.get("channel") not in {"attention", "rework", "cost"} or not ts(event.get("since")) or not ts(event.get("until")) or ts(event["since"]) > ts(event["until"]):
                raise ValueError("invalid ledger coverage interval")
        elif not ts(event.get("at")):
            raise ValueError("ledger event requires a UTC timestamp")
        if event.get("source") not in {"delivery_ledger", "patrol_ledger", "multica_comments", "codexbar", "9router"}:
            raise ValueError("invalid ledger source")
        clean.append(event)
    return clean


def render_markdown(report):
    lines = ["# 四周历史回补基线", "", f"UTC 窗口：{report['since']} — {report['until']}（左闭右开）",
             f"采集：{report['collected_at']}；来源状态：{report['source_status']}", "",
             "缺台账的 done 只作覆盖率候选；不以 updated_at 推断结束，不将 unknown 写成 0。",
             "每日窗口按冻结时点向前划分 24 小时，不是本地日历日。", "",
             "| 指标 | 基线 | 已知/候选 | 覆盖率 |", "|---|---:|---:|---:|"]
    def fmt(value):
        return "unknown" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)
    for name, metric in report["metrics"].items():
        lines.append(f"| {name} | {fmt(metric['value'])} | {metric.get('known', 'unknown')}/{metric.get('candidates', 'unknown')} | {fmt(metric.get('coverage'))} |")
    lines += [f"\n周期 P90（小时）：{fmt(report['metrics']['cycle_hours']['p90'])}",
              f"\n流速：24h={fmt(report['flow']['deliveries_24h'])}；在制品={fmt(report['flow']['wip'])}；阈值={fmt(report['flow']['threshold'])}；触发={fmt(report['flow']['triggered'])}",
              "", "## 每日交付", "", "| UTC 起点 | 完整交付 | 已观察到的台账交付（下界） | 父 issue done 信号（非交付） |", "|---|---:|---:|---:|"]
    lines += [f"| {d['since']} | {fmt(d['deliveries'])} | {d['observed_deliveries']} | {d['observed_done_signals']} |" for d in report["daily"]]
    lines += ["", "## Workspace 覆盖", "", "| Workspace ID | 来源状态 | 在制品 | done 候选 | 有证据交付 |", "|---|---|---:|---:|---:|"]
    for ws, item in report["workspace_flow"].items():
        lines.append(f"| {ws} | {item['source_status']} | {fmt(item['wip'])} | {fmt(item['done_candidates'])} | {fmt(item['evidenced_deliveries'])} |")
    lines += ["", "## 覆盖率与限制", "", "```json", json.dumps(report["coverage"], ensure_ascii=False, indent=2), "```",
              "", "历史评论只保留 ID、作者类型、时间；批准/回复/返工尚无人工分类时保持 unknown。",
              "手动处理、交付结束台账、逐 run 成本和额度归因没有历史来源时不估算。Multica cost_usd_ticks 保留原单位；额度永久分栏，不相加。",
              "9router Observability 未接入，不读取数据库或开关配置；关闭或缺数据均 unknown。",
              "本次为历史回补；实时接入后连续 2 天对账尚未完成。对账命令可输出匹配、仅回补、仅实时、时间差和口径差异。",
              f"采集错误数：{len(report['source_errors'])}。错误只保留资源、ID 和原因枚举。", ""]
    return "\n".join(lines)


def register(sub, common):
    b = sub.add_parser("metrics-backfill", help="read all visible workspace histories into a private local snapshot")
    common(b)
    b.add_argument("--snapshot", type=Path, required=True)
    b.add_argument("--resume", action="store_true", help="reuse successful reads in this snapshot; retry failed deep history only")
    b.add_argument("--task-identity", action="store_true", help="explicitly use current task CLI identity instead of a Board profile")
    b.add_argument("--days", type=int, default=28)
    b.add_argument("--workers", type=int, default=4)
    m = sub.add_parser("metrics", help="read delivery metrics and flow from a local snapshot (no network)")
    m.add_argument("--snapshot", type=Path, required=True)
    m.add_argument("--ledger", type=Path)
    m.add_argument("--days", type=int, default=28)
    m.add_argument("--threshold", type=float)
    m.add_argument("--wip-floor", type=float)
    m.add_argument("--rework-ceiling", type=float)
    m.add_argument("--max-age-hours", type=float, default=24)
    m.add_argument("--now", help="freeze time for a reproducible baseline; ISO UTC")
    m.add_argument("--format", choices=("json", "markdown"), default="json")
    r = sub.add_parser("metrics-reconcile", help="read and compare normalized backfill/realtime events")
    r.add_argument("--backfill", type=Path, required=True)
    r.add_argument("--realtime", type=Path, required=True)


def command(args, runner_factory):
    if args.cmd == "metrics-backfill":
        if not 1 <= args.days <= 90 or not 1 <= args.workers <= 8:
            raise ValueError("days must be 1..90; workers must be 1..8")
        from .metrics_history import TaskHistoryRunner
        runner = TaskHistoryRunner() if args.task_identity else runner_factory(args)
        previous = json.loads(args.snapshot.read_text(encoding="utf-8")) if args.resume else None
        snapshot = collect_history(runner, days=args.days, workers=args.workers, previous=previous)
        from .metrics_history import retain_events
        snapshot = retain_events(snapshot)
        write_private(args.snapshot, snapshot)
        print(json.dumps({"complete": snapshot["complete"], "issues": len(snapshot["issues"]),
                          "runs": len(snapshot["runs"]), "events": len(snapshot["events"]),
                          "errors": len(snapshot["errors"])}, ensure_ascii=False))
        return 0 if snapshot["complete"] else 2
    if args.cmd == "metrics-reconcile":
        a, b = (json.loads(p.read_text(encoding="utf-8")) for p in (args.backfill, args.realtime))
        result = reconcile(a["events"], b["events"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    now = ts(args.now) if args.now else None
    if args.now and now is None:
        raise ValueError("--now requires an ISO timestamp with timezone")
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    report = build_report(snapshot, load_ledger(args.ledger), now=now, days=args.days,
                          threshold=args.threshold, wip_floor=args.wip_floor,
                          rework_ceiling=args.rework_ceiling, max_age_hours=args.max_age_hours)
    print(render_markdown(report) if args.format == "markdown" else json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0
