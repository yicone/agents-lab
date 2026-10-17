"""Read-only layer backfill suggestions (design v3 A4 / D1 B4).

Prints a markdown table. It never calls a mutating `multica` command and never
writes issue properties. Only roots, children whose suggestion differs from
their parent, and children that cannot inherit (parent cancelled or missing)
are listed. Everyone else is omitted on purpose.

Low-confidence rows come first.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

LAYER_LABEL = {
    "L1": "L1 资源",
    "L2": "L2 执行",
    "L3": "L3 能力",
    "L4": "L4 协作",
    "L5": "L5 感知与决策",
    "outside": "不在地图",
}
LAYER_IDS = ("L1", "L2", "L3", "L4", "L5")
_CANCELLED = {"cancelled", "canceled"}

# (pattern, layer, weight). First match of a pattern scores once per title.
_RULES: tuple[tuple[str, str, int], ...] = (
    (r"英语|longform|youtube|\byt:|clipvibe|patreon|去\s*ai\s*味", "outside", 3),
    (r"存储清理|腾空间|pnpm store|time machine|外置盘|磁盘占用", "outside", 3),
    (r"x bookmark|x lists|margin|bookmarks", "outside", 3),
    (r"welcome to multica|getting started", "outside", 3),
    (r"资源层|资源用量|用量统计|codexbar|额度", "L1", 3),
    (r"runtime|daemon|whisper|stt", "L1", 2),
    (r"桥接|outbox|状态桥|看板后端|采集实现|采集", "L2", 2),
    (r"\bdevin\b|provider backend", "L2", 2),
    (r"skill|inventory|colgrep|skil", "L3", 2),
    (r"委派|交接|squad|hermes", "L4", 2),
    (r"设计|调研|评估|研究|信息架构|ui/?ux|favicon|图标|agent atlas|triage|撰写|案例", "L5", 2),
    (r"前端", "L5", 2),
)
_GOV = re.compile(r"治理|governance|skill_guard|门禁|漂移", re.I)
# Projects that are explicitly off the agent map. Title keywords do not override these.
_PROJECT_OUTSIDE = re.compile(r"英语学习|存储清理|已迁出|getting started", re.I)
_PROJECT_L2 = re.compile(r"devin cli", re.I)

_READ_ONLY = {
    ("issue", "list"),
    ("project", "list"),
    ("property", "list"),
}


@dataclass
class Suggestion:
    primary: str | None = None
    involved: list[str] | None = None  # None: unset, inherit; []: explicitly none
    governance: bool | None = None  # None: unset; True: suggest checking 治理线
    basis: str = "—"
    confidence: str = "低"
    has_signal: bool = False

    def key(self) -> tuple:
        return (self.primary, tuple(self.involved or []), self.governance is True)


def _flatten(text: Any) -> str:
    return " ".join(str(text or "").split())


def _score(title: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    for pattern, layer, weight in _RULES:
        if re.search(pattern, title, re.I):
            scores[layer] = scores.get(layer, 0) + weight
    return scores


def classify(title: str, project_title: str | None) -> Suggestion:
    """One issue's own suggestion, before inheritance. Unset fields stay None."""
    title_text = _flatten(title)
    project_text = _flatten(project_title)
    gov = True if _GOV.search(title_text) else None
    if project_text and _PROJECT_OUTSIDE.search(project_text):
        return Suggestion(
            primary="outside", involved=[], governance=gov, basis="项目", confidence="高", has_signal=True
        )

    scores = _score(title_text)
    if scores:
        best = max(scores.values())
        winners = [lid for lid, score in scores.items() if score == best]
        # Stable preference when several layers tie: perception, capability, then the rest.
        prefer = ("L5", "L3", "L4", "L2", "L1", "outside")
        primary = next(lid for lid in prefer if lid in winners)
        involved = [lid for lid in LAYER_IDS if lid in scores and lid != primary]
        if primary == "outside":
            involved = []
        confidence = "高" if len(winners) == 1 and best >= 2 else "低"
        return Suggestion(
            primary=primary,
            involved=involved,
            governance=gov,
            basis="关键词",
            confidence=confidence,
            has_signal=True,
        )

    if project_text and _PROJECT_L2.search(project_text):
        return Suggestion(primary="L2", involved=[], governance=gov, basis="项目", confidence="低", has_signal=True)

    if gov:
        return Suggestion(governance=True, basis="关键词", confidence="低", has_signal=True)
    return Suggestion()


def _overlay(child: Suggestion, parent: Suggestion) -> Suggestion:
    """Child fields that are set win; the rest keep the parent's effective value."""
    return Suggestion(
        primary=child.primary if child.primary is not None else parent.primary,
        involved=child.involved if child.involved is not None else parent.involved,
        governance=child.governance if child.governance is not None else parent.governance,
        basis=child.basis,
        confidence=child.confidence,
        has_signal=True,
    )


def _cancelled(row: dict[str, Any]) -> bool:
    return (row.get("status_category") or row.get("status") or "").lower() in _CANCELLED


def _label(code: str | None) -> str:
    if not code:
        return "—"
    return LAYER_LABEL.get(code, code)


def _involved_label(codes: list[str]) -> str:
    if not codes:
        return "—"
    return "、".join(LAYER_LABEL[c] for c in codes if c in LAYER_LABEL)


def plan_rows(issues: list[dict[str, Any]], projects: dict[str, str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return table rows plus counts. `projects` maps project id to title."""
    by_id = {i["id"]: i for i in issues if i.get("id")}
    own: dict[str, Suggestion] = {}
    for issue in by_id.values():
        own[issue["id"]] = classify(issue.get("title") or "", projects.get(issue.get("project_id") or ""))

    effective: dict[str, Suggestion] = {}
    write: dict[str, bool] = {}
    basis_override: dict[str, str] = {}

    def parent_of(issue: dict[str, Any]) -> dict[str, Any] | None:
        pid = issue.get("parent_issue_id")
        return by_id.get(pid) if pid else None

    def visit(iid: str, seen: set[str]) -> Suggestion:
        if iid in effective:
            return effective[iid]
        issue = by_id[iid]
        if iid in seen:
            effective[iid] = own[iid]
            write[iid] = not _cancelled(issue)
            return effective[iid]
        seen.add(iid)
        parent = parent_of(issue)
        suggestion = own[iid]
        if parent is None:
            effective[iid] = suggestion
            write[iid] = not _cancelled(issue)
            return suggestion
        parent_eff = visit(parent["id"], seen)
        parent_stores = not _cancelled(parent) and write.get(parent["id"], False)
        if _cancelled(issue):
            effective[iid] = _overlay(suggestion, parent_eff) if suggestion.has_signal else parent_eff
            write[iid] = False
            return effective[iid]
        if not parent_stores:
            # Parent will not hold a value, so this issue has to carry one.
            carried = _overlay(suggestion, parent_eff) if suggestion.has_signal else parent_eff
            effective[iid] = carried
            write[iid] = True
            if not suggestion.has_signal:
                basis_override[iid] = "继承"
            return carried
        if not suggestion.has_signal:
            effective[iid] = parent_eff
            write[iid] = False
            return parent_eff
        merged = _overlay(suggestion, parent_eff)
        if merged.key() == parent_eff.key():
            effective[iid] = parent_eff
            write[iid] = False
            return parent_eff
        effective[iid] = merged
        write[iid] = True
        return merged

    for iid in by_id:
        visit(iid, set())

    rows: list[dict[str, Any]] = []
    for iid, issue in by_id.items():
        if not write.get(iid):
            continue
        suggestion = effective[iid]
        basis = basis_override.get(iid, suggestion.basis)
        confidence = "高" if basis == "继承" and suggestion.confidence == "高" else suggestion.confidence
        if basis == "继承" and not suggestion.primary:
            confidence = "低"
        rows.append(
            {
                "identifier": issue.get("identifier") or "",
                "title": _flatten(issue.get("title")),
                "primary": _label(suggestion.primary),
                "involved": _involved_label(suggestion.involved),
                "governance": "是" if suggestion.governance else "否",
                "basis": basis,
                "confidence": confidence,
                "number": _number(issue.get("identifier") or ""),
            }
        )
    rows.sort(key=lambda r: (0 if r["confidence"] == "低" else 1, r["number"], r["identifier"]))

    omitted = sum(1 for iid, flag in write.items() if not flag and not _cancelled(by_id[iid]))
    cancelled = sum(1 for issue in by_id.values() if _cancelled(issue))
    stats = {
        "write": len(rows),
        "low": sum(1 for r in rows if r["confidence"] == "低"),
        "high": sum(1 for r in rows if r["confidence"] == "高"),
        "inherit_omitted": omitted,
        "cancelled_skipped": cancelled,
        "blank_primary": sum(1 for r in rows if r["primary"] == "—"),
    }
    return rows, stats


def _number(identifier: str) -> int:
    match = re.search(r"(\d+)$", identifier)
    return int(match.group(1)) if match else 0


def render_markdown(rows: list[dict[str, Any]], stats: dict[str, int]) -> str:
    lines = [
        "# 层回填建议",
        "",
        "只读建议，没有写入任何 issue 属性。确认之前不要回填。",
        "",
        "只列出需要亲手写值的 issue：根 issue、建议与父 issue 不同的子 issue、以及父 issue 被取消或不在列表里所以无法继承的子 issue。其余靠继承，表里不出现。",
        "",
        "低置信度在前。建议主层为 — 表示没有关键词或项目信号，需要人工指定。同一标题打到多层时置信度为低：主层按 L5、L3、L4、L2、L1、不在地图的顺序取胜出项，其余放进涉及层。",
        "",
        "| identifier | 标题 | 建议主层 | 建议涉及层 | 治理线 | 依据 | 置信度 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        cells = [
            row["identifier"],
            row["title"].replace("|", "/"),
            row["primary"],
            row["involved"],
            row["governance"],
            row["basis"],
            row["confidence"],
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "## 统计",
        "",
        f"- 建议写入 {stats['write']} 条（低置信度 {stats['low']}，高置信度 {stats['high']}）",
        f"- 其中建议主层为空 {stats['blank_primary']} 条",
        f"- 靠继承省略 {stats['inherit_omitted']} 条",
        f"- 已取消、不建议写入 {stats['cancelled_skipped']} 条",
        "",
    ]
    return "\n".join(lines)


def _command_key(args: list[str]) -> tuple[str, ...] | None:
    words = [a for a in args if not a.startswith("-")]
    for n in (2, 3):
        key = tuple(words[:n])
        if key in _READ_ONLY:
            return key
    return None


def _multica(args: list[str]) -> Any:
    if _command_key(args) is None:
        raise SystemExit(f"refusing non-read-only command: {' '.join(args)}")
    proc = subprocess.run(["multica", *args, "--output", "json"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or f"multica {' '.join(args)} failed")
    return json.loads(proc.stdout)


def _load_issues() -> list[dict[str, Any]]:
    fields = "id,identifier,title,status,status_category,parent_issue_id,project_id"
    offset = 0
    found: dict[str, dict[str, Any]] = {}
    while True:
        data = _multica(["issue", "list", "--limit", "100", "--offset", str(offset), "--fields", fields])
        page = data.get("issues") or []
        for issue in page:
            if issue.get("id"):
                found[issue["id"]] = issue
        if not data.get("has_more") or not page:
            break
        offset += len(page)
    return list(found.values())


def _load_projects() -> dict[str, str]:
    data = _multica(["project", "list"])
    items = data if isinstance(data, list) else (data.get("projects") or [])
    return {p["id"]: p.get("title") or "" for p in items if isinstance(p, dict) and p.get("id")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print a read-only layer backfill suggestion table")
    parser.add_argument("--output", help="write the markdown table to this path (also prints it)")
    args = parser.parse_args(argv)
    rows, stats = plan_rows(_load_issues(), _load_projects())
    text = render_markdown(rows, stats)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
