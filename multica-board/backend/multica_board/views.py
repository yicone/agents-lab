"""Read models built from the SQLite cache. Pure reads; no CLI calls."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from .store import Store

CLOSED_STATUSES = {"done", "cancelled", "canceled", "closed", "duplicate"}
ACTIVE_TASK = ("queued", "dispatched", "running", "waiting_local_directory")
ISSUE_VIEWS = ("attention", "in_review", "blocked", "run_failed", "stale", "open", "all")


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


class Views:
    def __init__(self, store: Store, server: str, *, app_url: str, stale_days: int, refresh_s: int, run_days: int):
        self.store = store
        self.server = server
        self.app_url = app_url.rstrip("/")
        self.stale_days = stale_days
        self.refresh_s = refresh_s
        self.run_days = run_days

    # --- lookups ------------------------------------------------------------

    def _workspaces(self) -> dict[str, dict[str, Any]]:
        rows = self.store.query("SELECT id, name, slug FROM workspaces WHERE server=? ORDER BY name", (self.server,))
        return {r["id"]: r for r in rows}

    def _names(self, table: str, key: str, label: str) -> dict[tuple[str, str], str]:
        rows = self.store.query(f"SELECT workspace_id, {key} AS k, {label} AS v FROM {table} WHERE server=?", (self.server,))
        return {(r["workspace_id"], r["k"]): r["v"] for r in rows}

    def _ws_ref(self, ws: dict[str, Any] | None, ws_id: str) -> dict[str, Any]:
        ws = ws or {}
        return {"id": ws_id, "name": ws.get("name"), "slug": ws.get("slug")}

    def _issue_url(self, slug: str | None, issue_id: str) -> str | None:
        return f"{self.app_url}/{quote(slug, safe='')}/issues/{quote(issue_id, safe='')}" if slug else None

    def _agent_url(self, slug: str | None, agent_id: str) -> str | None:
        return f"{self.app_url}/{quote(slug, safe='')}/agents/{quote(agent_id, safe='')}" if slug else None

    # --- meta / workspaces --------------------------------------------------

    def meta(self) -> dict[str, Any]:
        from . import API_VERSION, __version__

        return {
            "api_version": API_VERSION,
            "backend_version": __version__,
            "read_only": True,
            "server": self.server,
            "generated_at": _iso(_now()),
            "refresh_interval_s": self.refresh_s,
            "stale_issue_days": self.stale_days,
            "run_window_days": self.run_days,
            "last_collect": self.store.get_meta("last_collect"),
            "issue_views": list(ISSUE_VIEWS),
        }

    def workspaces(self) -> list[dict[str, Any]]:
        ws = self._workspaces()
        sync = self.store.query("SELECT * FROM sync_status WHERE server=?", (self.server,))
        by_ws: dict[str, dict[str, Any]] = {}
        for s in sync:
            by_ws.setdefault(s["workspace_id"], {})[s["resource"]] = s
        counts = {
            (r["workspace_id"]): r
            for r in self.store.query(
                "SELECT workspace_id, COUNT(*) AS issues, "
                "SUM(status='in_review') AS in_review, SUM(status='blocked') AS blocked "
                "FROM issues WHERE server=? GROUP BY workspace_id",
                (self.server,),
            )
        }
        agent_counts = {
            r["workspace_id"]: r["n"]
            for r in self.store.query(
                "SELECT workspace_id, COUNT(*) AS n FROM agents WHERE server=? AND archived_at IS NULL GROUP BY workspace_id",
                (self.server,),
            )
        }
        stale_after = timedelta(seconds=max(self.refresh_s * 3, 300))
        out = []
        for ws_id, w in ws.items():
            resources = by_ws.get(ws_id, {})
            successes = [_parse(r["last_success_at"]) for r in resources.values() if r["last_success_at"]]
            errors = {k: r["last_error"] for k, r in resources.items() if r["last_error"]}
            oldest_success = min(successes) if successes else None
            c = counts.get(ws_id, {})
            out.append(
                {
                    **self._ws_ref(w, ws_id),
                    "url": f"{self.app_url}/{quote(w['slug'], safe='')}/issues" if w.get("slug") else None,
                    "sync": {
                        "last_success_at": _iso(oldest_success) if oldest_success else None,
                        "stale": oldest_success is None or bool(errors) or (_now() - oldest_success) > stale_after,
                        "errors": errors,
                        "incomplete": sorted(k for k, r in resources.items() if r["complete"] == 0),
                        "resources": {
                            k: {
                                "last_success_at": r["last_success_at"],
                                "last_attempt_at": r["last_attempt_at"],
                                "count": r["item_count"],
                                "error": r["last_error"],
                            }
                            for k, r in sorted(resources.items())
                        },
                    },
                    "counts": {
                        "issues": c.get("issues") or 0,
                        "in_review": c.get("in_review") or 0,
                        "blocked": c.get("blocked") or 0,
                        "agents": agent_counts.get(ws_id, 0),
                    },
                }
            )
        return out

    # --- issues -------------------------------------------------------------

    def _latest_runs(self) -> dict[tuple[str, str], dict[str, Any]]:
        rows = self.store.query(
            "SELECT t.* FROM tasks t JOIN ("
            "  SELECT workspace_id, issue_id, MAX(created_at) AS mx FROM tasks "
            "  WHERE server=? AND issue_id IS NOT NULL GROUP BY workspace_id, issue_id"
            ") m ON t.workspace_id=m.workspace_id AND t.issue_id=m.issue_id AND t.created_at=m.mx "
            "WHERE t.server=?",
            (self.server, self.server),
        )
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:
            key = (r["workspace_id"], r["issue_id"])
            if key not in out or (r["id"] > out[key]["id"]):
                out[key] = r
        return out

    def _failed_issue_ids(self) -> set[tuple[str, str]]:
        cutoff = _iso(_now() - timedelta(days=self.run_days))
        rows = self.store.query(
            "SELECT DISTINCT workspace_id, issue_id FROM tasks WHERE server=? AND status='failed' "
            "AND issue_id IS NOT NULL AND created_at >= ?",
            (self.server, cutoff),
        )
        return {(r["workspace_id"], r["issue_id"]) for r in rows}

    def _issue_rows(self) -> list[dict[str, Any]]:
        ws = self._workspaces()
        projects = self._names("projects", "id", "title")
        agents = self._names("agents", "id", "name")
        members = self._names("members", "user_id", "name")
        latest = self._latest_runs()
        failed = self._failed_issue_ids()
        stale_cutoff = _now() - timedelta(days=self.stale_days)
        rows = self.store.query("SELECT * FROM issues WHERE server=?", (self.server,))
        out = []
        for r in rows:
            wsid = r["workspace_id"]
            w = ws.get(wsid, {})
            run = latest.get((wsid, r["id"]))
            open_ = (r["status"] or "").lower() not in CLOSED_STATUSES and (r["status_category"] or "").lower() not in CLOSED_STATUSES
            last = _parse(r["last_activity_at"] or r["updated_at"])
            run_failed = bool(run and run["status"] == "failed")
            flags = {
                "open": open_,
                "in_review": r["status"] == "in_review",
                "blocked": r["status"] == "blocked",
                "run_failed": run_failed,
                "recovered_failure": (not run_failed) and (wsid, r["id"]) in failed,
                "stale": open_ and last is not None and last < stale_cutoff and r["status"] != "backlog",
                "run_active": bool(run and run["status"] in ACTIVE_TASK),
            }
            assignee_name = None
            if r["assignee_type"] == "agent":
                assignee_name = agents.get((wsid, r["assignee_id"]))
            elif r["assignee_type"] == "member":
                assignee_name = members.get((wsid, r["assignee_id"]))
            out.append(
                {
                    "id": r["id"],
                    "identifier": r["identifier"],
                    "title": r["title"],
                    "status": r["status"],
                    "status_category": r["status_category"],
                    "status_name": r["status_name"],
                    "priority": r["priority"],
                    "workspace": self._ws_ref(w, wsid),
                    "project": {"id": r["project_id"], "title": projects.get((wsid, r["project_id"]))} if r["project_id"] else None,
                    "assignee": {"type": r["assignee_type"], "id": r["assignee_id"], "name": assignee_name} if r["assignee_id"] else None,
                    "parent_issue_id": r["parent_issue_id"],
                    "labels": json.loads(r["labels_json"] or "[]"),
                    "due_date": r["due_date"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "last_activity_at": r["last_activity_at"],
                    "idle_days": round((_now() - last).total_seconds() / 86400, 1) if last else None,
                    "latest_run": self._run_ref(run, agents) if run else None,
                    "flags": flags,
                    "url": self._issue_url(w.get("slug"), r["id"]),
                }
            )
        return out

    def _run_ref(self, t: dict[str, Any], agents: dict[tuple[str, str], str]) -> dict[str, Any]:
        return {
            "id": t["id"],
            "status": t["status"],
            "agent": {"id": t["agent_id"], "name": agents.get((t["workspace_id"], t["agent_id"]))},
            "kind": t["kind"],
            "created_at": t["created_at"],
            "completed_at": t["completed_at"],
            "error_class": t["error_class"],
            "error": t["error"],
        }

    @staticmethod
    def _match(issue: dict[str, Any], view: str) -> bool:
        f = issue["flags"]
        if view == "all":
            return True
        if view == "open":
            return f["open"]
        if view == "attention":
            return f["open"] and (f["in_review"] or f["blocked"] or f["run_failed"] or f["stale"])
        return bool(f.get(view))

    def issues(self, *, view: str = "attention", workspace: str | None = None, q: str | None = None,
               limit: int = 200, offset: int = 0) -> dict[str, Any]:
        if view not in ISSUE_VIEWS:
            raise ValueError(f"view must be one of {', '.join(ISSUE_VIEWS)}")
        rows = [i for i in self._issue_rows() if self._match(i, view)]
        if workspace:
            rows = [i for i in rows if workspace in (i["workspace"]["id"], i["workspace"]["slug"])]
        if q:
            ql = q.lower()
            rows = [i for i in rows if ql in (i["title"] or "").lower() or ql in (i["identifier"] or "").lower()]
        rows.sort(key=lambda i: i["last_activity_at"] or "", reverse=True)
        return {"total": len(rows), "limit": limit, "offset": offset, "view": view, "issues": rows[offset: offset + limit]}

    def board(self, *, workspace: str | None = None) -> dict[str, Any]:
        rows = self._issue_rows()
        if workspace:
            rows = [i for i in rows if workspace in (i["workspace"]["id"], i["workspace"]["slug"])]
        rows = [i for i in rows if i["flags"]["open"]]
        rows.sort(key=lambda i: i["last_activity_at"] or "", reverse=True)
        columns = {k: [i for i in rows if i["flags"][k]] for k in ("in_review", "blocked", "run_failed", "stale")}
        return {
            "generated_at": _iso(_now()),
            "counts": {k: len(v) for k, v in columns.items()},
            "columns": columns,
        }

    # --- agents -------------------------------------------------------------

    def agents(self, *, workspace: str | None = None, include_archived: bool = False) -> list[dict[str, Any]]:
        ws = self._workspaces()
        runtimes = {
            (r["workspace_id"], r["id"]): r
            for r in self.store.query("SELECT * FROM runtimes WHERE server=?", (self.server,))
        }
        cutoff_7d = _iso(_now() - timedelta(days=7))
        cutoff_24h = _iso(_now() - timedelta(hours=24))
        stats = {
            (r["workspace_id"], r["agent_id"]): r
            for r in self.store.query(
                "SELECT workspace_id, agent_id, "
                "SUM(status='running') AS running, "
                "SUM(status IN ('queued','dispatched','waiting_local_directory')) AS queued, "
                "SUM(status='failed' AND created_at >= ?) AS failed_7d, "
                "SUM(status='failed' AND created_at >= ?) AS failed_24h, "
                "SUM(created_at >= ?) AS runs_7d "
                "FROM tasks WHERE server=? GROUP BY workspace_id, agent_id",
                (cutoff_7d, cutoff_24h, cutoff_7d, self.server),
            )
        }
        last_fail = {}
        for r in self.store.query(
            "SELECT * FROM tasks WHERE server=? AND status='failed' ORDER BY created_at DESC", (self.server,)
        ):
            last_fail.setdefault((r["workspace_id"], r["agent_id"]), r)
        last_run = {}
        observed: dict[tuple[str, str], list[str]] = {}
        for r in self.store.query(
            "SELECT workspace_id, agent_id, status, created_at, models_json FROM tasks WHERE server=? ORDER BY created_at DESC",
            (self.server,),
        ):
            key = (r["workspace_id"], r["agent_id"])
            last_run.setdefault(key, r)
            ms = observed.setdefault(key, [])
            for m in json.loads(r["models_json"] or "[]"):
                if m not in ms and len(ms) < 5:
                    ms.append(m)

        sql = "SELECT * FROM agents WHERE server=?"
        params: list[Any] = [self.server]
        if not include_archived:
            sql += " AND archived_at IS NULL"
        out = []
        for a in self.store.query(sql + " ORDER BY name", params):
            wsid = a["workspace_id"]
            w = ws.get(wsid, {})
            if workspace and workspace not in (wsid, w.get("slug")):
                continue
            key = (wsid, a["id"])
            rt = runtimes.get((wsid, a["runtime_id"])) if a["runtime_id"] else None
            st = stats.get(key, {})
            lf = last_fail.get(key)
            lr = last_run.get(key)
            out.append(
                {
                    "id": a["id"],
                    "name": a["name"],
                    "status": a["status"],
                    "archived": a["archived_at"] is not None,
                    "workspace": self._ws_ref(w, wsid),
                    "runtime": (
                        {
                            "id": rt["id"],
                            "name": rt["custom_name"] or rt["name"],
                            "provider": rt["provider"],
                            "mode": rt["runtime_mode"],
                            "status": rt["status"],
                            "online": rt["status"] == "online",
                            "last_seen_at": rt["last_seen_at"],
                            "device_info": rt["device_info"],
                            "cli_version": rt["cli_version"],
                        }
                        if rt
                        else ({"id": a["runtime_id"], "missing": True} if a["runtime_id"] else None)
                    ),
                    "runtime_bound": bool(a["runtime_bound"]),
                    "model": {
                        "configured": a["model"] or None,
                        "inherits_runtime_default": not a["model"],
                        "observed": observed.get(key, []),
                    },
                    "thinking_level": a["thinking_level"] or None,
                    "service_tier": a["service_tier"] or None,
                    "max_concurrent_tasks": a["max_concurrent_tasks"],
                    "load": {"running": st.get("running") or 0, "queued": st.get("queued") or 0},
                    "runs_7d": st.get("runs_7d") or 0,
                    "failures": {
                        "last_24h": st.get("failed_24h") or 0,
                        "last_7d": st.get("failed_7d") or 0,
                        "last": (
                            {
                                "run_id": lf["id"],
                                "issue_id": lf["issue_id"],
                                "at": lf["completed_at"] or lf["created_at"],
                                "error_class": lf["error_class"],
                                "error": lf["error"],
                                "recovered": bool(lr and lr["created_at"] > lf["created_at"] and lr["status"] != "failed"),
                            }
                            if lf
                            else None
                        ),
                    },
                    "last_run_at": lr["created_at"] if lr else None,
                    "url": self._agent_url(w.get("slug"), a["id"]),
                }
            )
        return out

    # --- runs ---------------------------------------------------------------

    def runs(self, *, workspace: str | None = None, agent: str | None = None, issue: str | None = None,
             status: str | None = None, limit: int = 100) -> dict[str, Any]:
        ws = self._workspaces()
        agents = self._names("agents", "id", "name")
        sql = "SELECT * FROM tasks WHERE server=?"
        params: list[Any] = [self.server]
        if workspace:
            ids = [k for k, w in ws.items() if workspace in (k, w.get("slug"))]
            sql += f" AND workspace_id IN ({','.join('?' for _ in ids) or 'NULL'})"
            params += ids
        if agent:
            sql += " AND agent_id=?"
            params.append(agent)
        if issue:
            sql += " AND issue_id=?"
            params.append(issue)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        out = []
        for t in self.store.query(sql, params):
            w = ws.get(t["workspace_id"], {})
            ref = self._run_ref(t, agents)
            ref.update(
                {
                    "workspace": self._ws_ref(w, t["workspace_id"]),
                    "issue_id": t["issue_id"],
                    "runtime_id": t["runtime_id"],
                    "attempt": t["attempt"],
                    "max_attempts": t["max_attempts"],
                    "started_at": t["started_at"],
                    "models": json.loads(t["models_json"] or "[]"),
                    "issue_url": self._issue_url(w.get("slug"), t["issue_id"]) if t["issue_id"] else None,
                }
            )
            out.append(ref)
        return {"runs": out, "limit": limit}
