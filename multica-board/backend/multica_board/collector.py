"""Cross-workspace collection: CLI JSON -> normalized, minimized rows -> SQLite."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .cli_runner import CliError, CliRunner
from .redact import classify_error, redact
from .store import Store

log = logging.getLogger("multica_board.collect")

ISSUE_FIELDS = ",".join(
    [
        "id", "identifier", "number", "title", "status", "status_category", "status_name",
        "priority", "assignee_type", "assignee_id", "creator_type", "creator_id",
        "parent_issue_id", "project_id", "stage", "start_date", "due_date",
        "created_at", "updated_at", "last_activity_at", "labels", "properties",
    ]
)
ISSUE_PAGE = 100
TASK_PAGE = 200
TERMINAL_TASK = {"completed", "failed", "cancelled"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class CollectOptions:
    workspaces: list[str] = field(default_factory=list)  # ids or slugs; empty + all_workspaces => all
    all_workspaces: bool = False
    run_days: int = 14
    max_task_pages: int = 10
    full: bool = False
    concurrency: int = 3


def _labels(raw: Any) -> list[str]:
    out = []
    for lab in raw or []:
        if isinstance(lab, dict):
            out.append(lab.get("name") or lab.get("id") or "")
        else:
            out.append(str(lab))
    return [x for x in out if x]


def norm_issue(i: dict[str, Any]) -> dict[str, Any]:
    keep = [
        "id", "identifier", "number", "title", "status", "status_category", "status_name",
        "priority", "assignee_type", "assignee_id", "creator_type", "creator_id",
        "parent_issue_id", "project_id", "stage", "start_date", "due_date",
        "created_at", "updated_at", "last_activity_at",
    ]
    row = {k: i.get(k) for k in keep}
    row["labels_json"] = json.dumps(_labels(i.get("labels")), ensure_ascii=False)
    row["properties_json"] = json.dumps(i.get("properties") or {}, ensure_ascii=False)
    return row


def norm_agent(a: dict[str, Any]) -> dict[str, Any]:
    keep = [
        "id", "name", "status", "runtime_id", "runtime_mode", "model", "thinking_level",
        "service_tier", "max_concurrent_tasks", "visibility", "owner_id", "archived_at", "updated_at",
    ]
    row = {k: a.get(k) for k in keep}
    row["runtime_bound"] = int(bool(a.get("runtime_bound")))
    return row


def norm_runtime(r: dict[str, Any]) -> dict[str, Any]:
    keep = ["id", "name", "custom_name", "provider", "runtime_mode", "status", "last_seen_at", "visibility", "owner_id"]
    row = {k: r.get(k) for k in keep}
    meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
    row["cli_version"] = meta.get("cli_version") or meta.get("version")
    row["device_info"] = redact(r.get("device_info"), limit=160)
    return row


def norm_task(t: dict[str, Any]) -> dict[str, Any]:
    keep = [
        "id", "agent_id", "runtime_id", "issue_id", "kind", "status", "attempt", "max_attempts",
        "created_at", "dispatched_at", "started_at", "completed_at",
    ]
    row = {k: t.get(k) for k in keep}
    err = t.get("error")
    if err is not None and not isinstance(err, str):
        err = json.dumps(err, ensure_ascii=False)
    row["error"] = redact(err)
    row["error_class"] = classify_error(err) if t.get("status") == "failed" or err else None
    models: list[str] = []
    tin = tout = 0
    for u in t.get("usage") or []:
        if not isinstance(u, dict):
            continue
        m = u.get("model")
        if m and m not in models:
            models.append(m)
        tin += int(u.get("input_tokens") or 0)
        tout += int(u.get("output_tokens") or 0)
    row["models_json"] = json.dumps(models, ensure_ascii=False)
    row["input_tokens"] = tin or None
    row["output_tokens"] = tout or None
    return row


class Collector:
    def __init__(self, runner: CliRunner, store: Store, server: str, opts: CollectOptions):
        self.runner = runner
        self.store = store
        self.server = server
        self.opts = opts

    # --- top level ------------------------------------------------------------

    def collect(self) -> dict[str, Any]:
        started = iso(utcnow())
        try:
            res = self.runner.run(["workspace", "list"])
        except CliError as exc:
            self.store.record_sync(self.server, "*", "workspaces", at=started, ok=False, error=str(exc))
            return {"ok": False, "error": str(exc), "workspaces": []}
        visible = [
            {"id": w["id"], "name": w.get("name"), "slug": w.get("slug")}
            for w in (res.data or [])
            if isinstance(w, dict) and w.get("id")
        ]
        selected = self._select(visible)
        purged = self.store.set_workspaces(self.server, selected)
        self.store.record_sync(self.server, "*", "workspaces", at=started, ok=True, count=len(selected))

        results: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=max(1, self.opts.concurrency)) as pool:
            futures = {pool.submit(self.collect_workspace, w["id"]): w for w in selected}
            for fut, w in futures.items():
                try:
                    results[w["id"]] = fut.result()
                except Exception as exc:  # isolate one workspace's failure from the rest
                    log.exception("workspace %s failed", w["id"])
                    results[w["id"]] = {"ok": False, "error": redact(str(exc))}
        self.store.set_meta("last_collect", {"started_at": started, "finished_at": iso(utcnow())})
        return {"ok": all(r.get("ok") for r in results.values()), "workspaces": results, "purged": purged}

    def _select(self, visible: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.opts.all_workspaces and not self.opts.workspaces:
            return visible
        wanted = set(self.opts.workspaces)
        picked = [w for w in visible if w["id"] in wanted or (w.get("slug") and w["slug"] in wanted)]
        missing = wanted - {w["id"] for w in picked} - {w.get("slug") for w in picked}
        if missing:
            log.warning("allow-listed workspaces not visible to this identity: %s", ", ".join(sorted(missing)))
        return picked

    # --- per workspace ------------------------------------------------------

    def collect_workspace(self, ws: str) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": True}
        steps = {
            "issues": self._issues,
            "projects": self._projects,
            "members": self._members,
            "runtimes": self._runtimes,
            "agents": self._agents,
        }
        agents: list[dict[str, Any]] | None = None

        def step(name: str):
            at = iso(utcnow())
            try:
                count, complete, extra = steps[name](ws)
            except CliError as exc:
                self.store.record_sync(self.server, ws, name, at=at, ok=False, error=str(exc))
                return name, exc, None
            self.store.record_sync(self.server, ws, name, at=at, ok=True, count=count, complete=complete)
            return name, count, extra

        with ThreadPoolExecutor(max_workers=len(steps)) as pool:
            for name, result, extra in pool.map(step, steps):
                if isinstance(result, CliError):
                    out["ok"] = False
                    out[name] = f"error: {result}"
                else:
                    out[name] = result
                    if name == "agents":
                        agents = extra

        at = iso(utcnow())
        if agents is None:
            self.store.record_sync(self.server, ws, "tasks", at=at, ok=False, error="agents unavailable")
            out["ok"] = False
            return out
        try:
            count, complete = self._tasks(ws, agents)
            self.store.record_sync(self.server, ws, "tasks", at=at, ok=True, count=count, complete=complete)
            out["tasks"] = count
        except CliError as exc:
            out["ok"] = False
            out["tasks"] = f"error: {exc}"
            self.store.record_sync(self.server, ws, "tasks", at=at, ok=False, error=str(exc))
        return out

    def _issues(self, ws: str):
        seen: dict[str, dict[str, Any]] = {}
        offset = 0
        total = None
        while True:
            res = self.runner.run(
                ["issue", "list", "--limit", str(ISSUE_PAGE), "--offset", str(offset), "--fields", ISSUE_FIELDS],
                workspace_id=ws,
            )
            data = res.data or {}
            page = data.get("issues") or []
            total = data.get("total", total)
            for i in page:
                if i.get("id"):
                    seen[i["id"]] = norm_issue(i)  # offset drift can repeat rows; dedupe by UUID
            if not data.get("has_more") or not page:
                break
            offset += len(page)
        complete = total is None or len(seen) >= int(total)
        self.store.replace_snapshot(self.server, ws, "issues", list(seen.values()))
        return len(seen), complete, None

    def _projects(self, ws: str):
        res = self.runner.run(["project", "list"], workspace_id=ws)
        rows = [{"id": p["id"], "title": p.get("title"), "status": p.get("status")} for p in _as_list(res.data, "projects")]
        self.store.replace_snapshot(self.server, ws, "projects", rows)
        return len(rows), True, None

    def _members(self, ws: str):
        res = self.runner.run(["workspace", "member", "list", ws], workspace_id=ws)
        rows = [
            {"user_id": m.get("user_id") or m.get("id"), "name": m.get("name"), "role": m.get("role")}
            for m in _as_list(res.data, "members")
        ]
        rows = [r for r in rows if r["user_id"]]
        self.store.replace_snapshot(self.server, ws, "members", rows)
        return len(rows), True, None

    def _runtimes(self, ws: str):
        res = self.runner.run(["runtime", "list"], workspace_id=ws)
        rows = [norm_runtime(r) for r in _as_list(res.data, "runtimes") if r.get("id")]
        self.store.replace_snapshot(self.server, ws, "runtimes", rows)
        return len(rows), True, None

    def _agents(self, ws: str):
        res = self.runner.run(["agent", "list", "--include-archived"], workspace_id=ws)
        rows = [norm_agent(a) for a in _as_list(res.data, "agents") if a.get("id")]
        self.store.replace_snapshot(self.server, ws, "agents", rows)
        return len(rows), True, rows

    def _tasks(self, ws: str, agents: list[dict[str, Any]]):
        cutoff = iso(utcnow() - timedelta(days=self.opts.run_days))
        with ThreadPoolExecutor(max_workers=max(1, self.opts.concurrency)) as pool:
            results = list(pool.map(lambda a: self._agent_tasks(ws, a["id"], cutoff), agents))
        self.store.prune_tasks(self.server, ws, iso(utcnow() - timedelta(days=max(self.opts.run_days * 2, 30))))
        return sum(n for n, _ in results), all(c for _, c in results)

    def _agent_tasks(self, ws: str, agent_id: str, cutoff: str) -> tuple[int, bool]:
        known = self.store.known_task_ids(self.server, ws, agent_id)
        cursor: str | None = None
        total = pages = 0
        while True:
            args = ["agent", "tasks", agent_id, "--limit", str(TASK_PAGE)]
            if cursor:
                args += ["--before", cursor]
            res = self.runner.run(args, workspace_id=ws)
            rows = [norm_task(t) for t in _as_list(res.data, "tasks") if t.get("id")]
            self.store.upsert_many(self.server, ws, "tasks", rows)
            total += len(rows)
            pages += 1
            cursor = res.next_cursor
            if not cursor or not rows:
                return total, True
            oldest = min((r["created_at"] or "") for r in rows)
            if oldest and oldest < cutoff:
                return total, True
            # Incremental: stop once we reach terminal runs we already cached.
            if not self.opts.full and any(known.get(r["id"]) in TERMINAL_TASK for r in rows):
                return total, True
            if pages >= self.opts.max_task_pages:
                return total, False


def _as_list(data: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict) and isinstance(data.get(key), list):
        return [x for x in data[key] if isinstance(x, dict)]
    return []
