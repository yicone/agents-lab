"""Read models for API contract v0 (multica-board/docs/api-contract.md). Pure cache reads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from .collector import COLLECTOR, WORKSPACE
from .store import Store

CLOSED_CATEGORIES = {"done", "cancelled", "canceled"}
QUEUED_TASK = ("queued", "dispatched", "waiting_local_directory")
LAST_FAILED_WINDOW = timedelta(days=7)
RECENT_WINDOW_H = 24
RUNS_PER_ISSUE = 20
TOP_FAILURE_REASONS = 3


class NotFound(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _key(ws: str, rid: str) -> str:
    return f"{ws}:{rid}"


def _closed(row: dict[str, Any]) -> bool:
    cat = (row.get("status_category") or row.get("status") or "").lower()
    return cat in CLOSED_CATEGORIES


class Views:
    def __init__(self, store: Store, server: str, *, app_url: str, collect_interval_s: int):
        self.store = store
        self.server = server
        self.app_url = app_url.rstrip("/")
        self.collect_interval_s = collect_interval_s

    # --- shared -------------------------------------------------------------

    def generated_at(self) -> str | None:
        last = self.store.get_meta("last_collect") or {}
        return last.get("finished_at")

    def _slugs(self) -> dict[str, str | None]:
        rows = self.store.query("SELECT id, slug FROM workspaces WHERE server=?", (self.server,))
        return {r["id"]: r["slug"] for r in rows}

    def _url(self, slug: str | None, kind: str, rid: str | None) -> str | None:
        if not slug or not rid:
            return None
        return f"{self.app_url}/{quote(slug, safe='')}/{kind}/{quote(rid, safe='')}"

    def _names(self, table: str, key: str, label: str) -> dict[tuple[str, str], Any]:
        rows = self.store.query(f"SELECT workspace_id, {key} AS k, {label} AS v FROM {table} WHERE server=?", (self.server,))
        return {(r["workspace_id"], r["k"]): r["v"] for r in rows}

    @staticmethod
    def _run(t: dict[str, Any], agent_names: dict[tuple[str, str], Any]) -> dict[str, Any]:
        return {
            "task_id": t["id"],
            "status": t["status"],
            "agent_name": agent_names.get((t["workspace_id"], t["agent_id"])),
            "runtime_id": t["runtime_id"],
            "started_at": t["started_at"],
            "completed_at": t["completed_at"],
            "error_kind": t["error_kind"],
            "error_summary": t["error_summary"],
        }

    # --- /api/meta ----------------------------------------------------------

    def meta(self) -> dict[str, Any]:
        from . import API_VERSION

        stale_after = timedelta(seconds=max(self.collect_interval_s, 60) * 3)
        sync = {
            r["workspace_id"]: r
            for r in self.store.query("SELECT * FROM sync_status WHERE server=? AND resource=?", (self.server, WORKSPACE))
        }
        counts: dict[str, dict[str, int]] = {}
        for table, name, extra in (
            ("issues", "open_issues", "AND lower(coalesce(status_category, status, '')) NOT IN ('done','cancelled','canceled')"),
            ("agents", "agents", "AND archived_at IS NULL"),
            ("runtimes", "runtimes", ""),
        ):
            for r in self.store.query(
                f"SELECT workspace_id, COUNT(*) AS n FROM {table} WHERE server=? {extra} GROUP BY workspace_id",
                (self.server,),
            ):
                counts.setdefault(r["workspace_id"], {})[name] = r["n"]

        workspaces = []
        for w in self.store.query("SELECT id, slug, name FROM workspaces WHERE server=? ORDER BY name", (self.server,)):
            s = sync.get(w["id"]) or {}
            last_success = s.get("last_success_at")
            error = {"kind": s.get("last_error_kind") or "unknown", "message": s["last_error"]} if s.get("last_error") else None
            if not last_success:
                state = "never"
            elif error:
                state = "error"
            elif _now() - (_parse(last_success) or _now()) > stale_after:
                state = "stale"
            else:
                state = "ok"
            show_counts = last_success is not None and not (error and error["kind"] == "auth")
            c = counts.get(w["id"], {})
            workspaces.append(
                {
                    "id": w["id"],
                    "slug": w["slug"],
                    "name": w["name"],
                    "collect_state": state,
                    "last_success_at": last_success,
                    "last_attempt_at": s.get("last_attempt_at"),
                    "error": error,
                    "counts": (
                        {"open_issues": c.get("open_issues", 0), "agents": c.get("agents", 0), "runtimes": c.get("runtimes", 0)}
                        if show_counts
                        else None
                    ),
                }
            )
        return {
            "api_version": API_VERSION,
            "generated_at": self.generated_at(),
            "collect_interval_s": self.collect_interval_s,
            "multica_web_base": self.app_url,
            "collector": self._collector(),
            "workspaces": workspaces,
        }

    def _collector(self) -> dict[str, Any]:
        """Global CLI health (`workspace list`); explains an empty or silently stale board."""
        rows = self.store.query(
            "SELECT * FROM sync_status WHERE server=? AND workspace_id=? AND resource=?", (self.server, *COLLECTOR)
        )
        s = rows[0] if rows else {}
        return {
            "last_attempt_at": s.get("last_attempt_at"),
            "last_success_at": s.get("last_success_at"),
            "error": {"kind": s.get("last_error_kind") or "unknown", "message": s["last_error"]} if s.get("last_error") else None,
        }

    # --- /api/issues --------------------------------------------------------

    def issues(self) -> dict[str, Any]:
        slugs = self._slugs()
        agent_names = self._names("agents", "id", "name")
        member_names = self._names("members", "user_id", "name")
        projects = self._names("projects", "id", "title")
        identifiers = self._names("issues", "id", "identifier")

        latest: dict[tuple[str, str], dict[str, Any]] = {}
        last_failed: dict[tuple[str, str], dict[str, Any]] = {}
        failed_cutoff = _iso(_now() - LAST_FAILED_WINDOW)
        for t in self.store.query(
            "SELECT * FROM tasks WHERE server=? AND issue_id IS NOT NULL ORDER BY created_at DESC, id DESC",
            (self.server,),
        ):
            k = (t["workspace_id"], t["issue_id"])
            latest.setdefault(k, t)
            if t["status"] == "failed" and (t["completed_at"] or t["created_at"] or "") >= failed_cutoff:
                last_failed.setdefault(k, t)

        items = []
        for r in self.store.query("SELECT * FROM issues WHERE server=? ORDER BY last_activity_at DESC", (self.server,)):
            ws = r["workspace_id"]
            k = (ws, r["id"])
            if _closed(r) and k not in last_failed:
                continue
            assignee = None
            if r["assignee_type"] and r["assignee_id"]:
                name = None
                if r["assignee_type"] == "agent":
                    name = agent_names.get((ws, r["assignee_id"]))
                elif r["assignee_type"] == "member":
                    name = member_names.get((ws, r["assignee_id"]))
                assignee = {"type": r["assignee_type"], "id": r["assignee_id"], "name": name}
            lf = last_failed.get(k)
            items.append(
                {
                    "key": _key(ws, r["id"]),
                    "workspace_id": ws,
                    "id": r["id"],
                    "identifier": r["identifier"],
                    "title": r["title"],
                    "status": r["status"],
                    "status_category": r["status_category"],
                    "priority": r["priority"],
                    "assignee": assignee,
                    "project": {"id": r["project_id"], "title": projects.get((ws, r["project_id"]))} if r["project_id"] else None,
                    "parent_identifier": identifiers.get((ws, r["parent_issue_id"])) if r["parent_issue_id"] else None,
                    "due_date": r["due_date"],
                    "created_at": r["created_at"],
                    "last_activity_at": r["last_activity_at"],
                    "url": self._url(slugs.get(ws), "issues", r["id"]),
                    "latest_run": self._run(latest[k], agent_names) if k in latest else None,
                    "last_failed_run": (
                        {"task_id": lf["id"], "completed_at": lf["completed_at"], "error_kind": lf["error_kind"]} if lf else None
                    ),
                }
            )
        return {"generated_at": self.generated_at(), "items": items}

    def issue_runs(self, workspace_id: str, issue_id: str) -> dict[str, Any]:
        found = self.store.query(
            "SELECT 1 FROM issues WHERE server=? AND workspace_id=? AND id=?", (self.server, workspace_id, issue_id)
        )
        if not found:
            raise NotFound("issue not in cache")
        agent_names = self._names("agents", "id", "name")
        rows = self.store.query(
            "SELECT * FROM tasks WHERE server=? AND workspace_id=? AND issue_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (self.server, workspace_id, issue_id, RUNS_PER_ISSUE),
        )
        return {
            "generated_at": self.generated_at(),
            "items": [{"key": _key(workspace_id, t["id"]), **self._run(t, agent_names)} for t in rows],
        }

    # --- /api/agents --------------------------------------------------------

    def agents(self) -> dict[str, Any]:
        slugs = self._slugs()
        identifiers = self._names("issues", "id", "identifier")
        cutoff = _iso(_now() - timedelta(hours=RECENT_WINDOW_H))
        load: dict[tuple[str, str], dict[str, int]] = {}
        recent: dict[tuple[str, str], dict[str, Any]] = {}
        for t in self.store.query(
            "SELECT workspace_id, agent_id, issue_id, status, created_at, completed_at, error_kind "
            "FROM tasks WHERE server=? ORDER BY created_at DESC",
            (self.server,),
        ):
            k = (t["workspace_id"], t["agent_id"])
            l = load.setdefault(k, {"running": 0, "queued": 0})
            if t["status"] == "running":
                l["running"] += 1
            elif t["status"] in QUEUED_TASK:
                l["queued"] += 1
            when = t["completed_at"] or t["created_at"] or ""
            if when < cutoff:
                continue
            rc = recent.setdefault(k, {"completed": 0, "failed": 0, "last_failed_at": None,
                                       "last_error_kind": None, "last_failed_issue_identifier": None})
            if t["status"] == "completed":
                rc["completed"] += 1
            elif t["status"] == "failed":
                rc["failed"] += 1
                if rc["last_failed_at"] is None or when > rc["last_failed_at"]:
                    rc["last_failed_at"] = when
                    rc["last_error_kind"] = t["error_kind"]
                    rc["last_failed_issue_identifier"] = identifiers.get((t["workspace_id"], t["issue_id"])) if t["issue_id"] else None

        items = []
        for a in self.store.query("SELECT * FROM agents WHERE server=? ORDER BY name", (self.server,)):
            ws = a["workspace_id"]
            k = (ws, a["id"])
            items.append(
                {
                    "key": _key(ws, a["id"]),
                    "workspace_id": ws,
                    "id": a["id"],
                    "name": a["name"],
                    "status": a["status"],
                    "archived": a["archived_at"] is not None,
                    "runtime_id": a["runtime_id"],
                    "model": a["model"],
                    "thinking_level": a["thinking_level"],
                    "service_tier": a["service_tier"],
                    "max_concurrent_tasks": a["max_concurrent_tasks"],
                    "tasks": load.get(k, {"running": 0, "queued": 0}),
                    "recent": recent.get(k, {"completed": 0, "failed": 0, "last_failed_at": None,
                                             "last_error_kind": None, "last_failed_issue_identifier": None}),
                    "url": self._url(slugs.get(ws), "agents", a["id"]),
                }
            )
        return {"generated_at": self.generated_at(), "recent_window_h": RECENT_WINDOW_H, "items": items}

    # --- /api/runtimes ------------------------------------------------------

    def runtimes(self) -> dict[str, Any]:
        items = [
            {
                "key": _key(r["workspace_id"], r["id"]),
                "workspace_id": r["workspace_id"],
                "id": r["id"],
                "name": r["custom_name"] or r["name"],
                "provider": r["provider"],
                "runtime_mode": r["runtime_mode"],
                "status": r["status"],
                "last_seen_at": r["last_seen_at"],
                "device_info": r["device_info"],
            }
            for r in self.store.query("SELECT * FROM runtimes WHERE server=? ORDER BY provider, name", (self.server,))
        ]
        return {"generated_at": self.generated_at(), "items": items}

    # --- /api/stats/failures ------------------------------------------------

    def failure_stats(self) -> dict[str, Any]:
        """Failed-run counts for the last 24h and 7d, grouped by provider, runtime, and agent.

        `failure_reason` is the classified `error_kind` (quota, rate_limit, …), not the raw
        error text. A run counts when its `completed_at` (else `created_at`) falls in the
        window. Provider comes from the runtime recorded on the run, summed across workspaces.
        """
        now = _now()
        cuts = {
            "h24": _iso(now - timedelta(hours=RECENT_WINDOW_H)),
            "d7": _iso(now - LAST_FAILED_WINDOW),
        }
        agent_rows = {
            (r["workspace_id"], r["id"]): r
            for r in self.store.query(
                "SELECT workspace_id, id, name, runtime_id FROM agents WHERE server=?", (self.server,)
            )
        }
        runtime_rows = {
            (r["workspace_id"], r["id"]): r
            for r in self.store.query(
                "SELECT workspace_id, id, name, custom_name, provider FROM runtimes WHERE server=?",
                (self.server,),
            )
        }
        providers: dict[str | None, dict[str, Any]] = {}
        runtimes: dict[tuple[str, str | None], dict[str, Any]] = {}
        agents: dict[tuple[str, str | None], dict[str, Any]] = {}

        def bucket() -> dict[str, Any]:
            return {"failed": {"h24": 0, "d7": 0}, "reasons": {}}

        def add(group: dict[str, Any], reason: str, window: str) -> None:
            group["failed"][window] += 1
            group["reasons"].setdefault(reason, {"h24": 0, "d7": 0})[window] += 1

        for t in self.store.query(
            "SELECT workspace_id, agent_id, runtime_id, created_at, completed_at, error_kind "
            "FROM tasks WHERE server=? AND status='failed'",
            (self.server,),
        ):
            when = t["completed_at"] or t["created_at"] or ""
            windows = [name for name, cut in cuts.items() if when >= cut]
            if not windows:
                continue
            ws = t["workspace_id"]
            rid = t["runtime_id"] or None
            aid = t["agent_id"] or None
            rt = runtime_rows.get((ws, rid)) if rid else None
            provider = (rt.get("provider") or None) if rt else None
            reason = t["error_kind"] or "unknown"
            for window in windows:
                add(providers.setdefault(provider, bucket()), reason, window)
                add(runtimes.setdefault((ws, rid), bucket()), reason, window)
                add(agents.setdefault((ws, aid), bucket()), reason, window)

        def top_reasons(group: dict[str, Any]) -> list[dict[str, Any]]:
            rows = [
                {"failure_reason": reason, "h24": counts["h24"], "d7": counts["d7"]}
                for reason, counts in group["reasons"].items()
            ]
            rows.sort(key=lambda r: (-r["d7"], -r["h24"], r["failure_reason"]))
            return rows[:TOP_FAILURE_REASONS]

        def emit(group: dict[str, Any]) -> dict[str, Any]:
            return {"failed": dict(group["failed"]), "top_reasons": top_reasons(group)}

        by_provider = [{"provider": provider, **emit(group)} for provider, group in providers.items()]
        by_provider.sort(key=lambda r: (-r["failed"]["d7"], -r["failed"]["h24"], r["provider"] is None, r["provider"] or ""))

        by_runtime = []
        for (ws, rid), group in runtimes.items():
            rt = runtime_rows.get((ws, rid)) if rid else None
            by_runtime.append(
                {
                    "key": _key(ws, rid or ""),
                    "workspace_id": ws,
                    "id": rid,
                    "name": ((rt.get("custom_name") or rt.get("name")) if rt else None),
                    "provider": (rt.get("provider") or None) if rt else None,
                    **emit(group),
                }
            )
        by_runtime.sort(
            key=lambda r: (-r["failed"]["d7"], -r["failed"]["h24"], r["name"] is None, r["name"] or "", r["key"])
        )

        by_agent = []
        for (ws, aid), group in agents.items():
            ag = agent_rows.get((ws, aid)) if aid else None
            by_agent.append(
                {
                    "key": _key(ws, aid or ""),
                    "workspace_id": ws,
                    "id": aid,
                    "name": ag.get("name") if ag else None,
                    "runtime_id": ag.get("runtime_id") if ag else None,
                    **emit(group),
                }
            )
        by_agent.sort(
            key=lambda r: (-r["failed"]["d7"], -r["failed"]["h24"], r["name"] is None, r["name"] or "", r["key"])
        )

        return {
            "generated_at": self.generated_at(),
            "windows": {"h24": RECENT_WINDOW_H, "d7": int(LAST_FAILED_WINDOW.total_seconds() // 3600)},
            "by_provider": by_provider,
            "by_runtime": by_runtime,
            "by_agent": by_agent,
        }
