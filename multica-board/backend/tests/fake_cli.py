"""In-process stand-in for the multica CLI, driven by fixture dicts."""

from __future__ import annotations

import json
import subprocess
from typing import Any

FAKE_TOKEN = "mul_FAKEtoken0123456789abcdefABCDEF"


def _parse(argv: list[str]) -> tuple[dict[str, str], list[str]]:
    flags: dict[str, str] = {}
    words: list[str] = []
    it = iter(argv[1:])
    for a in it:
        if a.startswith("--"):
            if a in ("--include-archived",):
                flags[a] = "1"
            else:
                flags[a] = next(it)
        else:
            words.append(a)
    return flags, words


class FakeMultica:
    def __init__(self, workspaces: dict[str, dict[str, Any]]):
        # workspaces[id] = {"meta": {...}, "issues": [...], "agents": [...], "tasks": {agent_id: [...]}, ...}
        self.workspaces = workspaces
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.cwds: list[str | None] = []
        self.fail: dict[str, str] = {}  # workspace_id -> stderr message

    def __call__(self, argv, **kw) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        self.envs.append(dict(kw.get("env") or {}))
        self.cwds.append(kw.get("cwd"))
        flags, words = _parse(argv)
        ws = flags.get("--workspace-id")
        if ws and ws in self.fail:
            return subprocess.CompletedProcess(argv, 1, "", self.fail[ws])
        key = tuple(words[:2])
        out: Any
        stderr = ""
        if key == ("workspace", "list"):
            out = [w["meta"] for w in self.workspaces.values()]
        elif key == ("workspace", "member"):
            out = self.workspaces[ws].get("members", [])
        elif key == ("issue", "list"):
            issues = self.workspaces[ws].get("issues", [])
            limit, offset = int(flags["--limit"]), int(flags["--offset"])
            drift = self.workspaces[ws].get("offset_drift", 0) if offset else 0
            page = issues[max(0, offset - drift): offset - drift + limit]
            out = {"issues": page, "total": len(issues), "limit": limit, "offset": offset,
                   "has_more": offset + len(page) < len(issues)}
        elif key == ("project", "list"):
            out = self.workspaces[ws].get("projects", [])
        elif key == ("runtime", "list"):
            out = self.workspaces[ws].get("runtimes", [])
        elif key == ("agent", "list"):
            out = self.workspaces[ws].get("agents", [])
        elif key == ("skill", "list"):
            out = self.workspaces[ws].get("skills", [])
        elif key == ("property", "list"):
            out = self.workspaces[ws].get("properties", [])
        elif key == ("agent", "tasks"):
            tasks = self.workspaces[ws].get("tasks", {}).get(words[2], [])
            limit = int(flags["--limit"])
            start = 0
            if "--before" in flags:
                start = next(i for i, t in enumerate(tasks) if t["id"] == flags["--before"].split("|")[1]) + 1
            out = tasks[start: start + limit]
            if start + limit < len(tasks):
                last = out[-1]
                stderr = f'More runs available; use --before "{last["created_at"]}|{last["id"]}" to fetch the next page.\n'
        else:
            return subprocess.CompletedProcess(argv, 1, "", f"unknown command {words}")
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), stderr)


def issue(ws: str, n: int, **kw: Any) -> dict[str, Any]:
    base = {
        "id": f"{ws}-issue-{n:04d}", "workspace_id": ws, "identifier": f"T-{n}", "number": n,
        "title": f"Issue {n}", "status": "todo", "status_category": "todo", "status_name": "",
        "priority": "none", "assignee_type": None, "assignee_id": None, "creator_type": "member",
        "creator_id": "u1", "parent_issue_id": None, "project_id": None, "stage": None,
        "start_date": None, "due_date": None, "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-14T00:00:00Z", "last_activity_at": "2026-10-14T00:00:00Z",
        "labels": [], "properties": {}, "description": f"secret body {FAKE_TOKEN}",
    }
    base.update(kw)
    return base


def agent(ws: str, aid: str, **kw: Any) -> dict[str, Any]:
    base = {
        "id": aid, "workspace_id": ws, "name": f"Agent {aid}", "status": "idle", "runtime_id": "rt1",
        "runtime_mode": "local", "runtime_bound": True, "model": "", "thinking_level": "",
        "service_tier": "", "max_concurrent_tasks": 2, "visibility": "workspace", "owner_id": "u1",
        "archived_at": None, "updated_at": "2026-10-14T00:00:00Z",
        "instructions": f"use token {FAKE_TOKEN}", "custom_env": {"API_KEY": FAKE_TOKEN},
        "custom_args": ["--api-key", FAKE_TOKEN], "mcp_config": {"token": FAKE_TOKEN},
        "runtime_config": {"x": FAKE_TOKEN},
    }
    base.update(kw)
    return base


def runtime(ws: str, rid: str = "rt1", **kw: Any) -> dict[str, Any]:
    base = {
        "id": rid, "workspace_id": ws, "name": "Codex (box)", "custom_name": None, "provider": "codex",
        "runtime_mode": "local", "status": "online", "last_seen_at": "2026-10-14T00:00:00Z",
        "visibility": "private", "owner_id": "u1", "device_info": "box · codex 1.0",
        "metadata": {"cli_version": "0.6.2"}, "launch_header": f"Authorization: Bearer {FAKE_TOKEN}",
    }
    base.update(kw)
    return base


def task(ws: str, tid: str, agent_id: str, created_at: str, status: str = "completed", **kw: Any) -> dict[str, Any]:
    base = {
        "id": tid, "workspace_id": ws, "agent_id": agent_id, "runtime_id": "rt1", "issue_id": None,
        "kind": "direct", "status": status, "attempt": 1, "max_attempts": 2, "created_at": created_at,
        "dispatched_at": created_at, "started_at": created_at, "completed_at": created_at,
        "error": None, "result": {"output": f"done {FAKE_TOKEN}"}, "work_dir": "/Users/someone/w",
        "usage": [{"model": "gpt-x", "input_tokens": 10, "output_tokens": 5}],
    }
    base.update(kw)
    return base
