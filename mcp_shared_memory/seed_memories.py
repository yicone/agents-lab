#!/usr/bin/env python3
import json
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

sys.dont_write_bytecode = True

from server import MemoryStore  # type: ignore


SEED_SOURCE = "seed-2026-02-27"
SEED_TAG = "seed/2026-02-27"  # legacy/global-ish seed tag (kept for backwards compatibility)
PROJECT_SLUG = os.environ.get("MCP_PROJECT_SLUG", "agents-lab").strip()
SCOPED_SEED_TAG = f"seed/{PROJECT_SLUG}/2026-02-27"
PROJECT_TAG = os.environ.get("MCP_PROJECT_TAG", f"proj/{PROJECT_SLUG}").strip()


@dataclass(frozen=True)
class SeedMemory:
    title: str
    tags: List[str]
    body: str

    def render_text(self) -> str:
        return f"title: {self.title}\nseed_source: {SEED_SOURCE}\n\n{self.body}".strip() + "\n"

    def all_tags(self) -> List[str]:
        out = [PROJECT_TAG, SCOPED_SEED_TAG, SEED_TAG, "seed", *self.tags]
        # de-dupe while preserving order
        seen: set[str] = set()
        deduped: List[str] = []
        for t in out:
            if t not in seen:
                seen.add(t)
                deduped.append(t)
        return deduped


def _title_from_text(text: str) -> Optional[str]:
    for line in (text or "").splitlines():
        if line.lower().startswith("title:"):
            return line.split(":", 1)[1].strip() or None
        if line.strip():
            break
    return None


def _existing_seed_by_title(store: MemoryStore, limit: int = 200) -> Dict[str, str]:
    pairs = store.search(query="", tags=[SEED_TAG], limit=limit)
    out: Dict[str, str] = {}
    for rec, _score in pairs:
        title = _title_from_text(rec.text)
        if title:
            out.setdefault(title, rec.id)
    return out


def _ensure_seed(store: MemoryStore, existing_by_title: Dict[str, str], mem: SeedMemory) -> Tuple[str, str]:
    # Idempotency: if we already have a memory with this title under the seed tag, ensure project tag is present.
    if mem.title in existing_by_title:
        mem_id = existing_by_title[mem.title]
        existing = store.get(mem_id)
        if existing and PROJECT_TAG not in (existing.tags or []):
            new_tags: List[str] = []
            seen: set[str] = set()
            for t in ([PROJECT_TAG] + list(existing.tags or [])):
                if t not in seen:
                    seen.add(t)
                    new_tags.append(t)
            updated = store.update(mem_id=mem_id, tags=new_tags)
            return ("updated", (updated.id if updated else mem_id))
        return ("skipped", mem_id)

    rec = store.add(text=mem.render_text(), tags=mem.all_tags(), source=SEED_SOURCE)
    existing_by_title[mem.title] = rec.id
    return ("created", rec.id)


def main() -> int:
    db_path = os.environ.get("MCP_MEMORY_DB_PATH", "~/.codex/mcp/shared-memory/memory.sqlite3")
    expanded = os.path.expandvars(os.path.expanduser(db_path))
    store = MemoryStore(expanded)

    seeds: List[SeedMemory] = [
        SeedMemory(
            title="taxonomy_v1",
            tags=[
                "taxonomy",
                "axis/coding-agent",
                "axis/orchestrator",
                "axis/memory",
                "axis/skills",
                "axis/platform",
                "axis/prompts",
            ],
            body=(
                "目标：建立 AI Agent（尤其 Coding Agent）研究的统一分类与命名，便于跨会话检索与复用。\n"
                "\n"
                "主轴（Primary axis）：\n"
                "1. coding-agent：面向代码/仓库/issue 的 agent（IDE/CLI/服务），如 Codex/Claude Code/OpenCode/Amp/Windsurf/Cursor…\n"
                "2. orchestrator：多 agent/工作流编排（teams、并行、review loop、CI loop、插件框架）\n"
                "3. memory：跨会话/长期记忆、上下文层（MCP memory server、向量库、KV、日志）\n"
                "4. skills：技能/工具插件形态、context engineering 方法论与实践\n"
                "5. platform：agent 运行时/托管/部署平台（如 cloudflare/agents、nanobot host）\n"
                "6. prompts：system prompt/工具提示词/模型行为语料与对比\n"
                "\n"
                "次轴（Secondary axis）：\n"
                "- open-source vs closed-source\n"
                "- ide vs cli vs service\n"
                "- single-agent vs multi-agent\n"
                "\n"
                "每个条目最少记录字段（以后写笔记按这个模板）：\n"
                "- 是什么（1 句）、解决什么（1 句）、新意（≤3 点）、证据链接、下一步动作（monitor/evaluate/deep-dive）\n"
            ),
        ),
        SeedMemory(
            title="products_scope_v1",
            tags=["scope", "axis/coding-agent", "products"],
            body=(
                "正在关注/使用：Windsurf、Antigravity、Codex、Claude Code、OpenCode、Amp\n"
                "暂未使用但需要跟踪：Kilo、Factory、Auggie、Kiro、TRAE、Cursor\n"
                "\n"
                "记录规则：\n"
                "- 每个产品单独一条 dossier memory（后续新增），tag 用 product/<name> + axis/*\n"
                "- 只要出现“新能力/多 agent/记忆/上下文工程/成本模型/隐私模式”变化，就更新 dossier 并写入“证据链接 + 影响判断”\n"
            ),
        ),
        SeedMemory(
            title="workflow_weekly_v1",
            tags=["workflow", "cadence/weekly"],
            body=(
                "每周固定 60 分钟（建议周五/周末）执行一次：\n"
                "1. 收集：GitHub Trending（monthly）、关注仓库 release/README 变化、X/博客的关键更新（只记“可验证证据”）\n"
                "2. 分类：给每个新条目打 primary/secondary axis\n"
                "3. 评估：按 rubric_v1 打分，决定 monitor / evaluate / deep-dive\n"
                "4. 落库：写入 1 条 weekly-log YYYY-WW（包含：新增条目、重要变化、决策与理由、下周待办）\n"
                "\n"
                "触发 deep-dive 的典型信号：\n"
                "- 解决你当前痛点（多 agent/上下文/自动化）且可落地\n"
                "- 出现可复现的 demo / eval / benchmark / 真实用户迁移案例\n"
            ),
        ),
        SeedMemory(
            title="eval_rubric_v1",
            tags=["rubric", "decision"],
            body=(
                "评分维度（1–5 分）：\n"
                "1. 相关性：对 coding agent/工作流/记忆 的直接价值\n"
                "2. 可落地：你是否能在 1 周内试用/集成/复现实验\n"
                "3. 差异化：相对现有工具（Codex/Claude Code/OpenCode…）的新增能力是否明确\n"
                "4. 证据强度：是否有代码、文档、demo、eval、真实案例（而非口号）\n"
                "5. 风险/成本：锁定风险、隐私、安全、维护成本\n"
                "\n"
                "决策阈值（建议）：\n"
                "- deep-dive：相关性≥4 且 可落地≥4 且 证据强度≥3\n"
                "- evaluate：相关性≥3 且（差异化≥3 或 证据强度≥4）\n"
                "- monitor：其余\n"
                "\n"
                "deep-dive 输出物（固定格式）：\n"
                "- 1 页结论：能做什么/不能做什么、集成路径、替换成本、下一步建议\n"
            ),
        ),
    ]

    existing_by_title = _existing_seed_by_title(store)
    results: List[Dict[str, str]] = []
    for s in seeds:
        status, mem_id = _ensure_seed(store, existing_by_title, s)
        results.append({"title": s.title, "status": status, "id": mem_id})

    sys.stdout.write(json.dumps({"ok": True, "seed_source": SEED_SOURCE, "results": results}, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
