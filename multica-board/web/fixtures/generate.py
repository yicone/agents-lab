"""Regenerate the fictional mock responses used by `?mock=1`.

Run from anywhere: python3 multica-board/web/fixtures/generate.py
"""

import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).parent
NOW = datetime(2026, 10, 14, 9, 0, 0, tzinfo=timezone.utc)
rng = random.Random(163)


def uid():
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def ts(delta):
    return None if delta is None else (NOW - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(**kw):
    return ts(timedelta(**kw))


WORKSPACES = [
    ("alpha-lab", "Alpha Lab", "ALP", "ok", None),
    ("beta-coach", "Beta 辅导", "BET", "ok", None),
    ("gamma-oasis", "Gamma Oasis", "GAM", "error", ("timeout", "issue list timed out after 20s")),
    ("delta-english", "Delta 英语", "DEL", "ok", None),
    ("epsilon-brand", "Epsilon Brand", "EPS", "never", ("auth", "403 forbidden")),
]

PROVIDERS = ["claude", "codex", "cursor", "hermes", "antigravity"]
MODELS = {
    "claude": [None, "claude-opus-5-5-high", "claude-sonnet-5-5-medium"],
    "codex": [None, "gpt-5.6-sol-high", "gpt-5.5-medium"],
    "cursor": [None, "claude-opus-5-5-high", "composer-2.5"],
    "hermes": [None],
    "antigravity": [None, "gemini-3.1-pro"],
}
AGENT_NAMES = ["Builder", "Reviewer", "前端设计师", "Repo Analyst", "Knowledge Archivist", "Steward", "研究员"]
TITLES = [
    "接入状态桥接 shadow 模式",
    "整理周报模板并输出示例",
    "评估离线缓存方案",
    "修复登录态过期后的重试逻辑",
    "调研：竞品定价页结构",
    "课程卡片交互改版",
    "每日单词推送失败排查",
    "导出 issue 快照到 JSON",
    "首页信息架构草图",
    "迁移旧版配置到新 schema",
    "补充 runtime 切换的回滚说明",
    "批量重命名素材文件",
    "为 API 增加 Host 校验",
    "整理 agent 指令中的重复段落",
    "播客脚本第 3 期初稿",
    "统计近 30 天 run 失败类型",
]
ERRORS = {
    "quota": "provider quota exhausted for current billing period",
    "rate_limit": "429 too many requests, retry after 60s",
    "daemon_restart": "daemon restarted while task was in flight",
    "timeout": "task exceeded 45m wall clock",
    "model_unavailable": "model not available on this runtime",
    "unknown": "process exited with code 1",
}

meta_ws, issues, agents, runtimes = [], [], [], []

for slug, name, prefix, state, err in WORKSPACES:
    ws_id = uid()
    rt_by_provider = {}
    for p in PROVIDERS:
        rt_id = uid()
        online = not (p == "antigravity" and slug == "delta-english")
        rt_by_provider[p] = rt_id
        runtimes.append({
            "key": f"{ws_id}:{rt_id}",
            "workspace_id": ws_id,
            "id": rt_id,
            "name": f"{p.capitalize()} (dev-mini)",
            "provider": p,
            "runtime_mode": "local",
            "status": "online" if online else "offline",
            "last_seen_at": ago(seconds=rng.randint(5, 50)) if online else ago(hours=26),
            "device_info": f"dev-mini · {p}",
        })

    ws_agents = []
    for i, an in enumerate(rng.sample(AGENT_NAMES, rng.randint(3, 6))):
        p = rng.choice(PROVIDERS)
        a_id = uid()
        failed = rng.choice([0, 0, 0, 1, 2])
        kind = rng.choice(list(ERRORS)) if failed else None
        running = rng.choice([0, 0, 1])
        unbound = slug == "beta-coach" and i == 0
        ws_agents.append((a_id, an))
        agents.append({
            "key": f"{ws_id}:{a_id}",
            "workspace_id": ws_id,
            "id": a_id,
            "name": an,
            "status": "working" if running else "idle",
            "archived": slug == "alpha-lab" and i == 2,
            "runtime_id": None if unbound else rt_by_provider[p],
            "model": rng.choice(MODELS[p]),
            "thinking_level": rng.choice([None, None, "high", "medium"]),
            "service_tier": "priority" if p == "codex" and rng.random() < 0.5 else None,
            "max_concurrent_tasks": 6,
            "tasks": {"running": running, "queued": rng.choice([0, 0, 0, 2])},
            "recent": {
                "completed": rng.randint(0, 9),
                "failed": failed,
                "last_failed_at": ago(hours=rng.randint(1, 20)) if failed else None,
                "last_error_kind": kind,
                "last_failed_issue_identifier": f"{prefix}-{rng.randint(10, 99)}" if failed else None,
            },
            "url": f"https://multica.example/{slug}/agents/{a_id}",
        })

    open_count = 0
    if state != "never":
        for n in range(rng.randint(5, 9)):
            iss_id = uid()
            number = 100 + n * 3
            status = rng.choice(["in_review", "in_review", "blocked", "in_progress", "in_progress", "todo", "todo"])
            activity = timedelta(hours=rng.choice([1, 3, 8, 30, 80, 200, 400]))
            a_id, a_name = rng.choice(ws_agents)
            run_roll = rng.random()
            latest = None
            last_failed = None
            if run_roll < 0.75:
                rs = "failed" if run_roll < 0.2 else ("running" if run_roll < 0.3 else "completed")
                kind = rng.choice(list(ERRORS)) if rs == "failed" else None
                started = activity + timedelta(minutes=20)
                latest = {
                    "task_id": uid(),
                    "status": rs,
                    "agent_name": a_name,
                    "runtime_id": None,
                    "started_at": ts(started),
                    "completed_at": None if rs == "running" else ts(activity),
                    "error_kind": kind,
                    "error_summary": ERRORS[kind] if kind else None,
                }
                if rs == "failed":
                    last_failed = {"task_id": latest["task_id"], "completed_at": ts(activity), "error_kind": kind}
                elif rs == "completed" and rng.random() < 0.3:
                    k = rng.choice(list(ERRORS))
                    last_failed = {"task_id": uid(), "completed_at": ts(activity + timedelta(hours=2)), "error_kind": k}
            open_count += 1
            issues.append({
                "key": f"{ws_id}:{iss_id}",
                "workspace_id": ws_id,
                "id": iss_id,
                "identifier": f"{prefix}-{number}",
                "title": rng.choice(TITLES),
                "status": status,
                "status_category": status,
                "priority": rng.choice(["none", "none", "high", "medium", "urgent"]),
                "assignee": {"type": "agent", "id": a_id, "name": a_name} if rng.random() < 0.85 else None,
                "project": None,
                "parent_identifier": None,
                "due_date": (NOW - timedelta(days=1)).strftime("%Y-%m-%d") if rng.random() < 0.1 else None,
                "created_at": ts(activity + timedelta(days=2)),
                "last_activity_at": ts(activity),
                "url": f"https://multica.example/{slug}/issues/{prefix}-{number}",
                "latest_run": latest,
                "last_failed_run": last_failed,
            })

    n_agents = len(ws_agents)
    meta_ws.append({
        "id": ws_id,
        "slug": slug,
        "name": name,
        "collect_state": state,
        "last_success_at": None if state == "never" else (ago(minutes=18) if state == "error" else ago(seconds=20)),
        "last_attempt_at": ago(seconds=18),
        "error": {"kind": err[0], "message": err[1]} if err else None,
        "counts": None if state == "never" else {"open_issues": open_count, "agents": n_agents, "runtimes": len(PROVIDERS)},
    })

never_ids = {w["id"] for w in meta_ws if w["collect_state"] == "never"}
agents = [a for a in agents if a["workspace_id"] not in never_ids]
runtimes = [r for r in runtimes if r["workspace_id"] not in never_ids]

gen = ts(timedelta(0))


def dump(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


dump("meta.json", {
    "api_version": "0",
    "generated_at": gen,
    "collect_interval_s": 60,
    "multica_web_base": "https://multica.example",
    "workspaces": meta_ws,
})
dump("issues.json", {"generated_at": gen, "items": issues})
dump("agents.json", {"generated_at": gen, "recent_window_h": 24, "items": agents})
dump("runtimes.json", {"generated_at": gen, "items": runtimes})
print(f"{len(meta_ws)} workspaces, {len(issues)} issues, {len(agents)} agents, {len(runtimes)} runtimes")
