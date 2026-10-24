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
    def fmt(value):
        return "unknown" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)

    def metrics(left, right):
        lines = ["| 指标 | 台账值 | 台账已知/候选（覆盖率） | 代理值 | 代理已知/候选（覆盖率） |",
                 "|---|---:|---|---:|---|"]
        for name, m in left["metrics"].items():
            p = right["metrics"][name]
            lines.append(f"| {name} | {fmt(m['value'])} | {m['known']}/{m['candidates']} ({fmt(m['coverage'])}) | {fmt(p['value'])} | {p['known']}/{p['candidates']} ({fmt(p['coverage'])}) |")
        lines.append(f"周期 P90（小时）：台账={fmt(left['metrics']['cycle_hours']['p90'])}；代理={fmt(right['metrics']['cycle_hours']['p90'])}")
        return lines

    def flow(left, right):
        lines = ["| 口径 | 四周日均 | 70% 示例阈值 | 滚动 24h | 已观察 24h（下界） | 样本日均 × 70%（仅示例） | 触发 |", "|---|---:|---:|---:|---:|---:|---|"]
        for label, item in (("ledger", left), ("proxy", right)):
            f = item["flow"]["example_70_percent"]
            lines.append(f"| {label} | {fmt(f['daily_mean'])} | {fmt(f['threshold'])} | {fmt(f['deliveries_24h'])} | {item['flow']['observed_deliveries_24h']} | {fmt(f['observed_threshold'])} | {fmt(f['triggered'])} |")
        return lines

    def daily(left, right):
        lines = ["| UTC 起点 | 台账完整件数 | 已观察台账（下界） | 代理完整件数 | 已观察代理（下界） |",
                 "|---|---:|---:|---:|---:|"]
        for d, p in zip(left["daily"], right["daily"]):
            lines.append(f"| {d['since']} | {fmt(d['deliveries'])} | {d['observed_deliveries']} | {fmt(p['deliveries'])} | {p['observed_deliveries']} |")
        return lines

    proxy = report["proxy"]
    lines = ["# 四周历史回补基线 v2", "", f"UTC 窗口：{report['since']} — {report['until']}（左闭右开）",
             f"采集：{report['collected_at']}；来源状态：{report['source_status']}", "",
             "代理交付 evidence=proxy：父 issue 当前 done，以完整 timeline 最后一次 done 为结束；排除取消、验收/复验、纯推送/合并/部署单。",
             "代理与台账独立统计，同一件可在两列出现，禁止相加。台账自 2026-10-24（UTC 合并当天）正式记录，历史缺证据不补造。",
             "未识别类型、缺历史或不可访问均 unknown；不以 updated_at 推断结束。每日按冻结时点向前划分 24h，非本地日历日。",
             "70% 仅展示示例，不是已批准的告警阈值；来源不全时不输出确定触发。", "", "## 五项指标（额度独立附列）", ""]
    lines += metrics(report, proxy) + ["", "## 滚动 24 小时", ""] + flow(report, proxy)
    lines += ["", "## 每日交付", ""] + daily(report, proxy)
    for ws, item in report["workspace_flow"].items():
        left, right = item["ledger"], item["proxy"]
        lines += ["", f"## Workspace {ws}", "", f"来源：{item['source_status']}；在制品：{fmt(item['wip'])}", ""]
        lines += metrics(left, right) + [""] + flow(left, right) + [""] + daily(left, right)
    lines += ["", "## 覆盖率与限制", "", "```json", json.dumps({"ledger": report["coverage"], "proxy": proxy["coverage"],
              "proxy_exclusions": report["proxy_exclusions"]}, ensure_ascii=False, indent=2), "```", "",
              "批准/回复/返工未分类、手动处理无台账、逐 run 成本与额度不可归因时保持 unknown。等待仅恢复状态桶，原因不明保留 waiting_unclassified。",
              "Multica cost_usd_ticks 保留原单位；额度永久分栏，不相加。9router Observability 未接入。",
              "本次是历史回补，未完成实时接入后连续 2 天对账。",
              f"采集错误数：{len(report['source_errors'])}；错误仅保留资源、ID、原因枚举。", ""]
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
