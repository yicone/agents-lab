"""Read models for API contract v0.5 (multica-board/docs/api-contract.md). Pure cache reads."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from .governance_scan import load_for_atlas
from .agent_delivery import decide_delivery
from .changes import CHANGE_RETENTION_DAYS, CHANGES_PAGE, BadRequest, format_cursor, parse_kind, parse_since
from .collector import COLLECTOR, WORKSPACE
from .layers import ATLAS_SLUG, build_report, catalog_from_rows, empty_layer, resolve_issues
from .store import Store
from .usage import build_usage_response, load_snapshot, quota_alerts_for, snapshot_path_for

CLOSED_CATEGORIES = {"done", "cancelled", "canceled"}
QUEUED_TASK = ("queued", "dispatched", "waiting_local_directory")
LAST_FAILED_WINDOW = timedelta(days=7)
RECENT_WINDOW_H = 24
RUNS_PER_ISSUE = 20
ATTENTION_TITLE_CHARS = 80
ATTENTION_TEXT_BYTES = 2048
ATTENTION_BUCKETS = (
    "in_review",
    "blocked",
    "agent_updates",
    "failed_runs",
    "delivery_anomalies",
    "offline_runtimes",
    "missing_hermes_async_delegation",
)
HERMES_ASYNC_SKILL = "hermes-async-delegation"
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


def _flat(text: Any) -> str | None:
    if text is None:
        return None
    flat = " ".join(str(text).split())
    return flat or None


def _clip_title(text: Any) -> str | None:
    flat = _flat(text)
    if flat is None:
        return None
    return flat[:ATTENTION_TITLE_CHARS]


def _attention_item(
    identifier: str, title: Any, status: str, when: Any, agent_name: Any
) -> dict[str, Any]:
    return {
        "identifier": identifier,
        "title": _clip_title(title),
        "status": status,
        "time": _flat(when),
        "agent_name": _flat(agent_name),
    }


def _issue_bucket(row: dict[str, Any]) -> str | None:
    """`status` wins when it disagrees with `status_category`."""
    status = (row.get("status") or "").lower()
    if status in ("in_review", "blocked"):
        return status
    category = (row.get("status_category") or "").lower()
    if category in ("in_review", "blocked"):
        return category
    return None


def _json_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _mcp_own_state(raw: str | None) -> str:
    try:
        state = json.loads(raw or "null").get("state")
    except (ValueError, AttributeError):
        return "unknown"
    return state if state in {"none", "hidden", "invalid", "managed"} else "unknown"


def _skill_name_key(name: Any) -> str | None:
    if not isinstance(name, str):
        return None
    text = " ".join(name.split())
    return text or None


def build_workspace_installs(
    skill_rows: list[dict[str, Any]],
    agent_rows: list[dict[str, Any]],
    workspace_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Cloud skills grouped by name, with the agents in that workspace that mount each one."""
    workspaces = {row["id"]: row for row in workspace_rows if row.get("id")}
    mounts: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}

    def add_mount(workspace_id: str, key: str | None, agent: dict[str, Any], enabled: bool) -> None:
        if not workspace_id or not key:
            return
        bucket = mounts.setdefault((workspace_id, key), {})
        current = bucket.get(agent["id"])
        if current is None or (enabled and not current["enabled"]):
            bucket[agent["id"]] = {"id": agent["id"], "name": agent.get("name"), "enabled": enabled}

    for agent in agent_rows:
        ws = agent.get("workspace_id")
        if not ws or not agent.get("id"):
            continue
        seen_names: set[str] = set()
        for item in _json_list(agent.get("skills_detail_json")):
            if not isinstance(item, dict):
                continue
            enabled = item.get("enabled") is not False
            if isinstance(item.get("id"), str) and item["id"]:
                add_mount(ws, "id:" + item["id"], agent, enabled)
            name = _skill_name_key(item.get("name"))
            if name:
                seen_names.add(name)
                add_mount(ws, "name:" + name, agent, enabled)
        for name in _skill_names(agent.get("skills_json")):
            key = _skill_name_key(name)
            if key and key not in seen_names:
                add_mount(ws, "name:" + key, agent, True)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for skill in skill_rows:
        name = _skill_name_key(skill.get("name"))
        if not name or not skill.get("id") or not skill.get("workspace_id"):
            continue
        ws = workspaces.get(skill["workspace_id"], {})
        attached: dict[str, dict[str, Any]] = {}
        for source in (
            mounts.get((skill["workspace_id"], "id:" + skill["id"]), {}),
            mounts.get((skill["workspace_id"], "name:" + name), {}),
        ):
            for aid, mount in source.items():
                current = attached.get(aid)
                if current is None or (mount["enabled"] and not current["enabled"]):
                    attached[aid] = mount
        agents = sorted(attached.values(), key=lambda item: ((item.get("name") or "").casefold(), item["id"]))
        grouped.setdefault(name, []).append(
            {
                "workspace_id": skill["workspace_id"],
                "workspace_slug": ws.get("slug"),
                "workspace_name": ws.get("name"),
                "id": skill["id"],
                "name": name,
                "description": skill.get("description"),
                "updated_at": skill.get("updated_at"),
                "content_hash": skill.get("content_hash"),
                "content": {"state": "uncollected", "source": "cloud", "collected_at": None,
                            "evidence_state": "unknown", "message": "Multica Workspace 正文未采集"},
                "agents": agents,
            }
        )
    for installs in grouped.values():
        installs.sort(key=lambda item: ((item.get("workspace_slug") or ""), item["workspace_id"], item["id"]))
    return grouped


def _skill_names(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    if not isinstance(data, list):
        return set()
    return {name for name in data if isinstance(name, str) and name}


def _sort_attention(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Stable: time descending, null times last, identifier ascending on ties.
    ordered = sorted(items, key=lambda it: it["identifier"] or "")
    ordered.sort(key=lambda it: it["time"] or "", reverse=True)
    return ordered


def _cell(value: Any) -> str:
    return _flat(value) or "-"


def render_attention_text(body: dict[str, Any]) -> str:
    """Compact plaintext for `?format=text`. Always ends with a newline; capped at 2048 bytes."""
    lines = [
        f"generated_at {_cell(body.get('generated_at'))}",
        f"window_h {_cell(body.get('window_h'))}",
    ]
    for ws in body.get("workspaces") or []:
        lines.append("")
        lines.append(f"# {_cell(ws.get('slug') or ws.get('id'))} {_cell(ws.get('name'))}")
        for bucket in ATTENTION_BUCKETS:
            items = ws.get(bucket) or []
            if not items:
                continue
            lines.append(f"{bucket} {len(items)}")
            for it in items:
                lines.append(
                    "\t".join(
                        _cell(it.get(field))
                        for field in ("identifier", "status", "time", "agent_name", "title")
                    )
                )
    alerts = body.get("quota_alerts") or []
    if alerts:
        lines.append("")
        lines.append("# quota_alerts")
        lines.append(f"quota_alerts {len(alerts)}")
        for it in alerts:
            summary = _cell(it.get("title"))
            if "band" in it:
                summary += " " + " ".join(
                    f"{label}={_cell(it.get(field))}"
                    for label, field in (("band", "band"), ("age_s", "sample_age_s"),
                                         ("stale", "stale"), ("Δ24h_pp", "delta_24h_percent"),
                                         ("reset", "resets_at"),
                                         ("ETA85", "eta_warning_at"), ("ETA100", "eta_exhausted_at"))
                )
                daily = it.get("daily_delta") or []
                if daily:
                    summary += f" Δday_pp={_cell(daily[-1].get('delta_percent'))}@{_cell(daily[-1].get('date'))}"
            lines.append(
                "\t".join(
                    summary if field == "title" else _cell(it.get(field))
                    for field in ("identifier", "status", "time", "agent_name", "title")
                )
            )
    return _fit_text(lines)


def _fit_text(lines: list[str]) -> str:
    def packed(chosen: list[str], truncated: bool) -> str:
        parts = list(chosen)
        if truncated:
            parts.append("…")
        return "\n".join(parts) + "\n"

    if len(packed(lines, False).encode()) <= ATTENTION_TEXT_BYTES:
        return packed(lines, False)
    kept: list[str] = []
    for line in lines:
        if len(packed(kept + [line], True).encode()) > ATTENTION_TEXT_BYTES:
            break
        kept.append(line)
    if not kept:
        return "…\n"
    return packed(kept, True)


def _cloud_only_item(name: str, installs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "origin": "cloud",
        "source_class": "multica_cloud",
        "source_label": "Multica Workspace",
        "scope": "workspace",
        "ownership": None,
        "source_authority": None,
        "update_authority": None,
        "canonical_path": None,
        "repo_is_source": False,
        "channel_label": "Multica Workspace",
        "channel_detail": f"{len(installs)} 个 Workspace",
        "current_version": None,
        "observed_version": None,
        "baseline_kind": None,
        "baseline_subject": None,
        "baseline_ref": None,
        "review_time": None,
        "reconciliation_status": "unrecorded",
        "status_label": "未登记",
        "findings": [],
        "customization_mode": None,
        "constraints": [],
        "constraint_count": 0,
        "conflict_status": None,
        "tools": {},
        "entries": [],
        "evidence": [],
        "repo_ids": [],
        "install_path": None,
        "capability_gate": None,
        "baseline_debt": False,
        "bucket": None,
        "quiet": False,
        "command": None,
        "shared_entry": {
            "state": "none",
            "label": "未检查",
            "reason": "cloud_only",
            "path": None,
            "target": None,
            "resolved": None,
            "detail": "Multica Workspace 技能不在本机清单，共享入口未检查",
        },
        "workspace_installs": installs,
    }


class Views:
    def __init__(self, store: Store, server: str, *, app_url: str, collect_interval_s: int,
                 server_identity: str | None = None, mcp_every: int = 5, mcp_ctx=None):
        self.store = store
        self.server = server
        self.server_identity = server_identity
        self.app_url = app_url.rstrip("/")
        self.collect_interval_s = collect_interval_s
        self.mcp_every = mcp_every
        self._mcp_ctx = mcp_ctx

    def _mcp_context(self):
        if self._mcp_ctx is None:
            from .mcp_local import McpContext, REPO_ROOT
            from pathlib import Path
            data_dir = self.store.path.parent if str(self.store.path) != ":memory:" else Path.home() / ".multica-board"
            self._mcp_ctx = McpContext(home=Path.home(), repo_root=REPO_ROOT, data_dir=data_dir)
        return self._mcp_ctx

    # --- shared -------------------------------------------------------------

    def generated_at(self) -> str | None:
        last = self.store.get_meta("last_collect") or {}
        return last.get("finished_at")

    def _slugs(self) -> dict[str, str | None]:
        from .owner_agents import load_snapshot, snapshot_path, WORKSPACES
        rows = self.store.query("SELECT id, slug FROM workspaces WHERE server=?", (self.server,))
        slugs = {r["id"]: r["slug"] for r in rows}
        for ws, entry in load_snapshot(snapshot_path(self.store.path), self.server).get("workspaces", {}).items():
            if ws in WORKSPACES and isinstance(entry, dict) and entry.get("status") == "ok":
                slugs.setdefault(ws, WORKSPACES[ws])
        return slugs

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
            "created_at": t.get("created_at"),
            "status": t["status"],
            "agent_name": agent_names.get((t["workspace_id"], t["agent_id"])),
            "runtime_id": t["runtime_id"],
            "started_at": t["started_at"],
            "completed_at": t["completed_at"],
            "error_kind": t["error_kind"],
            "error_summary": t["error_summary"],
            "delivery_anomaly": t.get("delivery_anomaly"),
            "request_id": t.get("request_id"),
            "model": t.get("model"),
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
        # A user inventory may exist before the first regular all-workspace collection.
        from .owner_agents import load_snapshot, snapshot_path, WORKSPACES
        known = {w["id"] for w in workspaces}
        for ws, entry in load_snapshot(snapshot_path(self.store.path), self.server).get("workspaces", {}).items():
            if ws in WORKSPACES and ws not in known and isinstance(entry, dict) and entry.get("status") == "ok":
                workspaces.append({"id": ws, "slug": WORKSPACES[ws], "name": WORKSPACES[ws],
                    "collect_state": "never", "last_success_at": None, "last_attempt_at": None,
                    "error": None, "counts": None})
        return {
            "api_version": API_VERSION,
            "server_identity": self.server_identity,
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

        layers = self._layer_index()
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        last_anomaly: dict[tuple[str, str], dict[str, Any]] = {}
        last_failed: dict[tuple[str, str], dict[str, Any]] = {}
        failed_cutoff = _iso(_now() - LAST_FAILED_WINDOW)
        for t in self.store.query(
            "SELECT * FROM tasks WHERE server=? AND issue_id IS NOT NULL ORDER BY created_at DESC, id DESC",
            (self.server,),
        ):
            k = (t["workspace_id"], t["issue_id"])
            latest.setdefault(k, t)
            if t.get("delivery_anomaly") and (t["completed_at"] or t["created_at"] or "") >= failed_cutoff:
                last_anomaly.setdefault(k, t)
            if t["status"] == "failed" and (t["completed_at"] or t["created_at"] or "") >= failed_cutoff:
                last_failed.setdefault(k, t)

        acm = next((ws for ws, slug in slugs.items() if slug == ATLAS_SLUG), None)
        governance = load_for_atlas(self.store.path, self.server, acm) if acm else {"state": "not_collected", "findings": []}
        findings_by = {}
        for finding in governance.pop("findings"):
            findings_by.setdefault(finding["issue_id"], []).append(finding)
        items = []
        for r in self.store.query("SELECT * FROM issues WHERE server=? ORDER BY last_activity_at DESC", (self.server,)):
            ws = r["workspace_id"]
            k = (ws, r["id"])
            if _closed(r) and k not in last_failed and k not in last_anomaly:
                continue
            if ws == acm and any(f.get("source_updated_at") != r.get("updated_at") for f in findings_by.get(r["id"], [])):
                governance["state"] = "changed"
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
                    "last_delivery_anomaly": self._run(last_anomaly[k], agent_names) if k in last_anomaly else None,
                    "last_failed_run": (
                        {"task_id": lf["id"], "completed_at": lf["completed_at"], "error_kind": lf["error_kind"]} if lf else None
                    ),
                    "layer": layers.get(k) or empty_layer(),
                    "governance_findings": [f for f in findings_by.get(r["id"], [])
                        if ws == acm and f.get("source_updated_at") == r.get("updated_at")],
                }
            )
        return {"generated_at": self.generated_at(), "items": items, "governance_scan": governance}

    def _layer_index(self) -> dict[tuple[str, str], dict[str, Any]]:
        rows = self.store.query("SELECT * FROM issues WHERE server=?", (self.server,))
        defs = self.store.query("SELECT * FROM property_defs WHERE server=?", (self.server,))
        by_ws: dict[str, list[dict[str, Any]]] = {}
        defs_by: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_ws.setdefault(row["workspace_id"], []).append(row)
        for row in defs:
            defs_by.setdefault(row["workspace_id"], []).append(row)
        index: dict[tuple[str, str], dict[str, Any]] = {}
        for ws, issues in by_ws.items():
            resolved = resolve_issues(issues, catalog_from_rows(defs_by.get(ws, [])))
            for iid, layer in resolved.items():
                index[(ws, iid)] = layer
        return index

    def _status_events(self, workspace_id: str) -> dict[str, list[dict[str, Any]]]:
        found: dict[str, list[dict[str, Any]]] = {}
        for row in self.store.query(
            "SELECT resource_id, at, payload_json FROM changes WHERE server=? AND workspace_id=? AND kind='issue_status'",
            (self.server, workspace_id),
        ):
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            found.setdefault(row["resource_id"], []).append({"at": row["at"], "status": payload.get("status")})
        return found

    def layers(self) -> dict[str, Any]:
        """ACM Lab map counts. Other workspaces stay on `/api/issues` only."""
        found = self.store.query(
            "SELECT id, slug, name FROM workspaces WHERE server=? AND lower(coalesce(slug, ''))=?",
            (self.server, ATLAS_SLUG),
        )
        generated = self.generated_at()
        if not found:
            body = build_report([], {}, {}, ready=False)
            body["generated_at"] = generated
            body["workspace"] = None
            return body
        ws = found[0]
        rows = self.store.query("SELECT * FROM issues WHERE server=? AND workspace_id=?", (self.server, ws["id"]))
        defs = self.store.query(
            "SELECT * FROM property_defs WHERE server=? AND workspace_id=?", (self.server, ws["id"])
        )
        catalog = catalog_from_rows(defs)
        resolved = resolve_issues(rows, catalog)
        body = build_report(
            rows,
            resolved,
            self._status_events(ws["id"]),
            ready=catalog.ready,
            url_for=lambda iid: self._url(ws["slug"], "issues", iid),
        )
        body["generated_at"] = generated
        body["workspace"] = {"id": ws["id"], "slug": ws["slug"], "name": ws["name"]}
        return body

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
        titles = self._names("issues", "id", "title")
        activity = {}
        current = {}
        bindings = {}
        for binding in self.store.query("SELECT workspace_id, agent_id, server_id, name, enabled FROM mcp_bindings WHERE server=?", (self.server,)):
            bindings.setdefault((binding["workspace_id"], binding["agent_id"]), []).append({"server_id": binding["server_id"], "name": binding["name"], "enabled": bool(binding["enabled"])})
        binding_states = {(r["workspace_id"], r["agent_id"]): r["state"] for r in self.store.query("SELECT * FROM mcp_agent_state WHERE server=?", (self.server,))}
        cutoff = _iso(_now() - timedelta(hours=RECENT_WINDOW_H))
        load: dict[tuple[str, str], dict[str, int]] = {}
        recent: dict[tuple[str, str], dict[str, Any]] = {}
        for t in self.store.query(
            "SELECT workspace_id, agent_id, issue_id, status, created_at, completed_at, error_kind, delivery_anomaly "
            "FROM tasks WHERE server=? ORDER BY created_at DESC, id DESC",
            (self.server,),
        ):
            k = (t["workspace_id"], t["agent_id"])
            task = {"status": t["status"], "created_at": t["created_at"], "identifier": identifiers.get((t["workspace_id"], t["issue_id"])), "title": titles.get((t["workspace_id"], t["issue_id"]))}
            activity.setdefault(k, task)
            if t["status"] == "running" or t["status"] in QUEUED_TASK:
                current.setdefault(k, []).append(task)
            l = load.setdefault(k, {"running": 0, "queued": 0})
            if t["status"] == "running":
                l["running"] += 1
            elif t["status"] in QUEUED_TASK:
                l["queued"] += 1
            when = t["completed_at"] or t["created_at"] or ""
            if when < cutoff:
                continue
            rc = recent.setdefault(k, {"completed": 0, "failed": 0, "delivery_anomalies": 0, "last_failed_at": None,
                                       "last_error_kind": None, "last_failed_issue_identifier": None})
            if t["status"] == "completed":
                rc["completed"] += 1
                if t.get("delivery_anomaly"):
                    rc["delivery_anomalies"] += 1
            elif t["status"] == "failed":
                rc["failed"] += 1
                if rc["last_failed_at"] is None or when > rc["last_failed_at"]:
                    rc["last_failed_at"] = when
                    rc["last_error_kind"] = t["error_kind"]
                    rc["last_failed_issue_identifier"] = identifiers.get((t["workspace_id"], t["issue_id"])) if t["issue_id"] else None

        from .owner_agents import overlay, snapshot_path
        rows, owner_inventory = overlay(
            self.store.query("SELECT * FROM agents WHERE server=? ORDER BY name", (self.server,)),
            snapshot_path(self.store.path), self.server,
        )
        items = []
        for a in rows:
            ws = a["workspace_id"]
            k = (ws, a["id"])
            items.append(
                {
                    "key": _key(ws, a["id"]),
                    "workspace_id": ws,
                    "id": a["id"],
                    "name": a["name"],
                    "status": a.get("status"),
                    "archived": a.get("archived_at") is not None,
                    "runtime_id": a.get("runtime_id"),
                    "model": a.get("model"),
                    "thinking_level": a.get("thinking_level"),
                    "service_tier": a.get("service_tier"),
                    "max_concurrent_tasks": a.get("max_concurrent_tasks"),
                    "inventory_source": a.get("inventory_source"),
                    "inventory_collected_at": a.get("inventory_collected_at"),
                    "description": a.get("description"),
                    "instructions": a.get("instructions"),
                    "instructions_sha256": a.get("instructions_sha256"),
                    "skills": _json_list(a.get("skills_detail_json")),
                    "disabled_runtime_skills": _json_list(a.get("disabled_runtime_skills_json")),
                    "latest_run": activity.get(k),
                    "current_tasks": current.get(k, []),
                    "mcp_bindings": bindings.get(k, []) if binding_states.get(k) == "ok" else None,
                    "mcp_own_state": _mcp_own_state(a.get("mcp_own_json")),
                    "tasks": load.get(k, {"running": 0, "queued": 0}),
                    "recent": recent.get(k, {"completed": 0, "failed": 0, "delivery_anomalies": 0, "last_failed_at": None,
                                             "last_error_kind": None, "last_failed_issue_identifier": None}),
                    "url": self._url(slugs.get(ws), "agents", a["id"]),
                }
            )
        return {"generated_at": self.generated_at(), "recent_window_h": RECENT_WINDOW_H,
                "owner_inventory": owner_inventory, "items": items}

    # --- /api/bots ----------------------------------------------------------

    def bots(self) -> dict[str, Any]:
        from .grok_bots import load_bots

        return load_bots()

    # --- /api/mcp ------------------------------------------------------------

    def mcp(self) -> dict[str, Any]:
        from .mcp_view import build_mcp

        return build_mcp(self.store, self.server, self.generated_at(), self._mcp_context())

    # --- /api/hooks ----------------------------------------------------------

    def hooks(self) -> dict[str, Any]:
        from pathlib import Path

        from .hooks_view import build_hooks

        return build_hooks(Path.home(), generated_at=self.generated_at())

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

    # --- /api/attention -----------------------------------------------------

    def _agent_update_items(self, issue_rows, agent_names, cutoff: str):
        """Completed assignee runs in the window that no status reminder covered.

        Comment text is not in the cache, so this is the run fallback of
        decide_delivery. The bridge applies the same function to comments.
        """
        runs: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for task in self.store.query(
            "SELECT * FROM tasks WHERE server=? AND lower(status)='completed' "
            "AND coalesce(completed_at, created_at, '') >= ?",
            (self.server, cutoff),
        ):
            if not task.get("issue_id") or not task.get("agent_id"):
                continue
            runs.setdefault((task["workspace_id"], task["issue_id"]), []).append({
                "id": task["id"],
                "agent_id": task["agent_id"],
                "status": "completed",
                "completed_at": task.get("completed_at") or task.get("created_at"),
                "is_new": True,
            })
        transitions: dict[tuple[str, str], list[dict[str, str]]] = {}
        for row in self.store.query(
            "SELECT workspace_id, resource_id, at, payload_json FROM changes "
            "WHERE server=? AND kind='issue_status' AND at >= ?",
            (self.server, cutoff),
        ):
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                continue
            transitions.setdefault((row["workspace_id"], row["resource_id"]), []).append(
                {"to": payload.get("status"), "at": row["at"]}
            )
        found: list[tuple[str, dict[str, Any]]] = []
        for key, issue in issue_rows.items():
            issue_runs = runs.get(key)
            if not issue_runs:
                continue
            if issue.get("assignee_type") != "agent" or not issue.get("assignee_id"):
                continue
            decision = decide_delivery(
                status=issue.get("status"),
                status_category=issue.get("status_category"),
                assignee_type=issue.get("assignee_type"),
                assignee_id=issue.get("assignee_id"),
                comments=[],
                runs=issue_runs,
                transitions=transitions.get(key) or [],
            )
            if decision["action"] != "notify":
                continue
            ws_id = issue["workspace_id"]
            agent = agent_names.get((ws_id, issue.get("assignee_id")))
            found.append((
                ws_id,
                _attention_item(
                    issue.get("identifier") or issue["id"],
                    issue.get("title"),
                    issue.get("status") or "agent_update",
                    decision.get("at"),
                    agent,
                ),
            ))
        return found

    def attention(self) -> dict[str, Any]:
        """Workspace-grouped items Steward should look at. Fields are intentionally tiny."""
        agent_names = self._names("agents", "id", "name")
        issue_rows: dict[tuple[str, str], dict[str, Any]] = {}
        buckets: dict[str, dict[str, list[dict[str, Any]]]] = {}

        def ensure(ws: str) -> dict[str, list[dict[str, Any]]]:
            return buckets.setdefault(ws, {name: [] for name in ATTENTION_BUCKETS})

        for row in self.store.query("SELECT * FROM issues WHERE server=?", (self.server,)):
            issue_rows[(row["workspace_id"], row["id"])] = row
            bucket = _issue_bucket(row)
            if not bucket:
                continue
            agent = None
            if row.get("assignee_type") == "agent" and row.get("assignee_id"):
                agent = agent_names.get((row["workspace_id"], row["assignee_id"]))
            ensure(row["workspace_id"])[bucket].append(
                _attention_item(
                    row["identifier"] or row["id"],
                    row.get("title"),
                    row.get("status") or bucket,
                    row.get("last_activity_at") or row.get("updated_at") or row.get("created_at"),
                    agent,
                )
            )

        cutoff = _iso(_now() - timedelta(hours=RECENT_WINDOW_H))
        for task in self.store.query(
            "SELECT * FROM tasks WHERE server=? AND lower(status)='failed' "
            "AND coalesce(completed_at, created_at, '') >= ?",
            (self.server, cutoff),
        ):
            issue = issue_rows.get((task["workspace_id"], task["issue_id"])) if task.get("issue_id") else None
            identifier = (issue or {}).get("identifier") or task.get("issue_id") or task["id"]
            agent = agent_names.get((task["workspace_id"], task["agent_id"])) if task.get("agent_id") else None
            ensure(task["workspace_id"])["failed_runs"].append(
                _attention_item(
                    identifier,
                    (issue or {}).get("title"),
                    "failed",
                    task.get("completed_at") or task.get("created_at"),
                    agent,
                )
            )

        for task in self.store.query(
            "SELECT * FROM tasks WHERE server=? AND status='completed' AND delivery_anomaly IS NOT NULL "
            "AND coalesce(completed_at, created_at, '') >= ?", (self.server, cutoff),
        ):
            issue = issue_rows.get((task["workspace_id"], task["issue_id"])) or {}
            item = _attention_item(issue.get("identifier") or task.get("issue_id") or task["id"],
                                   issue.get("title"), "delivery_anomaly", task.get("completed_at"),
                                   agent_names.get((task["workspace_id"], task["agent_id"])))
            item.update(self._run(task, agent_names))
            item.update(issue_id=task["issue_id"], agent_id=task["agent_id"])
            ensure(task["workspace_id"])["delivery_anomalies"].append(item)

        for ws_id, item in self._agent_update_items(issue_rows, agent_names, cutoff):
            ensure(ws_id).setdefault("agent_updates", []).append(item)

        bound: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for agent in self.store.query(
            "SELECT workspace_id, id, name, runtime_id FROM agents "
            "WHERE server=? AND archived_at IS NULL AND runtime_id IS NOT NULL AND runtime_id != ''",
            (self.server,),
        ):
            bound.setdefault((agent["workspace_id"], agent["runtime_id"]), []).append(
                (agent["name"] or "", agent["id"])
            )

        providers = {
            (runtime["workspace_id"], runtime["id"]): (runtime.get("provider") or "").casefold()
            for runtime in self.store.query(
                "SELECT workspace_id, id, provider FROM runtimes WHERE server=?", (self.server,)
            )
        }
        for agent in self.store.query(
            "SELECT workspace_id, id, name, runtime_id, updated_at, skills_json FROM agents "
            "WHERE server=? AND archived_at IS NULL AND runtime_id IS NOT NULL AND runtime_id != ''",
            (self.server,),
        ):
            if providers.get((agent["workspace_id"], agent["runtime_id"])) != "hermes":
                continue
            if HERMES_ASYNC_SKILL in _skill_names(agent.get("skills_json")):
                continue
            ensure(agent["workspace_id"])["missing_hermes_async_delegation"].append(
                _attention_item(
                    agent["id"],
                    agent.get("name"),
                    "missing_skill",
                    agent.get("updated_at"),
                    agent.get("name"),
                )
            )

        for runtime in self.store.query(
            "SELECT * FROM runtimes WHERE server=? AND lower(status)='offline'", (self.server,)
        ):
            agents = bound.get((runtime["workspace_id"], runtime["id"]))
            if not agents:
                continue
            ordered = sorted(agents, key=lambda pair: ((_flat(pair[0]) or pair[1]).casefold(), pair[1]))
            ensure(runtime["workspace_id"])["offline_runtimes"].append(
                _attention_item(
                    runtime["id"],
                    runtime.get("custom_name") or runtime.get("name"),
                    "offline",
                    runtime.get("last_seen_at"),
                    ", ".join(_flat(name) or aid for name, aid in ordered),
                )
            )

        meta = {
            r["id"]: r
            for r in self.store.query("SELECT id, slug, name FROM workspaces WHERE server=?", (self.server,))
        }
        workspaces = []
        for ws_id, groups in buckets.items():
            filled = {name: _sort_attention(items) for name, items in groups.items() if items}
            if not filled:
                continue
            info = meta.get(ws_id) or {}
            workspaces.append(
                {
                    "id": ws_id,
                    "slug": info.get("slug"),
                    "name": info.get("name"),
                    **{name: filled[name] for name in ATTENTION_BUCKETS if name in filled},
                }
            )
        workspaces.sort(key=lambda w: ((w.get("name") or "").casefold(), w["id"]))
        return {
            "generated_at": self.generated_at(),
            "window_h": RECENT_WINDOW_H,
            "quota_alerts": self._quota_alerts(),
            "workspaces": workspaces,
        }

    def _usage_snapshot(self) -> dict[str, Any] | None:
        return load_snapshot(snapshot_path_for(self.store.path))

    def _quota_alerts(self) -> list[dict[str, Any]]:
        return quota_alerts_for(self._usage_snapshot(), _now())

    def usage(self) -> dict[str, Any]:
        """CodexBar windows from the snapshot, run counts from the task cache. The two are not added."""
        return build_usage_response(
            self._usage_snapshot(),
            self.store.query(
                "SELECT workspace_id, runtime_id, status, error_kind, failure_reason, "
                "completed_at, started_at, created_at FROM tasks WHERE server=?",
                (self.server,),
            ),
            self.store.query(
                "SELECT workspace_id, id, name, custom_name, provider FROM runtimes WHERE server=?",
                (self.server,),
            ),
            self.store.query("SELECT id, slug, name FROM workspaces WHERE server=?", (self.server,)),
            now=_now(),
        )

    def run_metrics(self) -> dict[str, Any]:
        """Read the S3 run-metrics.v0.1 file. No collection and no scheduling write."""
        from .run_metrics_view import load_run_metrics_file, report_path_for

        return load_run_metrics_file(report_path_for(self.store.path))

    def attention_text(self) -> str:
        return render_attention_text(self.attention())

    # --- /api/changes -------------------------------------------------------

    def changes(self, since: str | None, kind: str | None = None, *, limit: int = CHANGES_PAGE) -> dict[str, Any]:
        """Deltas after `since` (ISO time or a previous cursor). Omitted `since` only issues a cursor.

        `kind` limits the page (`status` is the alias for `issue_status`). Omitted returns every kind.
        """
        if limit < 1 or limit > CHANGES_PAGE:
            raise BadRequest("limit out of range")
        stored_kind = parse_kind(kind)
        mode, value = parse_since(since)
        horizon = _iso(_now() - timedelta(days=CHANGE_RETENTION_DAYS))
        self.store.prune_changes(self.server, horizon)

        def envelope(items: list[dict[str, Any]], cursor: int, reset: bool, has_more: bool) -> dict[str, Any]:
            return {
                "generated_at": self.generated_at(),
                "cursor": format_cursor(cursor),
                "reset": reset,
                "retained_days": CHANGE_RETENTION_DAYS,
                "has_more": has_more,
                "items": items,
            }

        if mode == "none":
            tip, _oldest = self.store.change_bounds(self.server)
            return envelope([], tip, False, False)
        if mode == "time" and value < horizon:
            tip, _oldest = self.store.change_bounds(self.server)
            return envelope([], tip, True, False)

        if mode == "time":
            snap = self.store.fetch_changes(
                self.server, after_seq=None, after_at=value, limit=limit + 1, kind=stored_kind
            )
        else:
            snap = self.store.fetch_changes(
                self.server, after_seq=value, after_at=None, limit=limit + 1, kind=stored_kind
            )
            oldest = snap["oldest"]
            if value > snap["tip"] or (oldest is not None and oldest > value + 1):
                return envelope([], snap["tip"], True, False)

        rows = snap["rows"]
        has_more = len(rows) > limit
        page = rows[:limit]
        if has_more:
            cursor = int(page[-1]["seq"])
        else:
            floor = value if mode == "seq" else 0
            cursor = max(snap["tip"], floor)
        return envelope(self._render_changes(page), cursor, False, has_more)

    def _render_changes(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        agent_names = self._names("agents", "id", "name")
        member_names = self._names("members", "user_id", "name")
        identifiers = self._names("issues", "id", "identifier")
        return [self._change_item(r, agent_names, member_names, identifiers) for r in rows]

    def _change_item(
        self,
        row: dict[str, Any],
        agent_names: dict[tuple[str, str], Any],
        member_names: dict[tuple[str, str], Any],
        identifiers: dict[tuple[str, str], Any],
    ) -> dict[str, Any]:
        payload = json.loads(row["payload_json"])
        ws = row["workspace_id"]
        kind = row["kind"]
        base: dict[str, Any] = {"kind": kind, "at": row["at"], "workspace_id": ws}
        if kind in ("issue_status", "issue_assignee"):
            base["key"] = _key(ws, row["resource_id"])
            base["id"] = row["resource_id"]
            base["identifier"] = payload.get("identifier")
            if kind == "issue_status":
                base.update(
                    {
                        "status": payload.get("status"),
                        "status_category": payload.get("status_category"),
                        "previous_status": payload.get("previous_status"),
                        "previous_status_category": payload.get("previous_status_category"),
                    }
                )
            else:
                base["assignee"] = self._person(ws, payload.get("assignee_type"), payload.get("assignee_id"), agent_names, member_names)
                base["previous_assignee"] = self._person(
                    ws, payload.get("previous_assignee_type"), payload.get("previous_assignee_id"), agent_names, member_names
                )
            return base
        if kind in ("run", "run_failed"):
            issue_id = payload.get("issue_id")
            base.update(
                {
                    "key": _key(ws, row["resource_id"]),
                    "task_id": row["resource_id"],
                    "issue_id": issue_id,
                    "identifier": identifiers.get((ws, issue_id)) if issue_id else None,
                    "status": payload.get("status"),
                    "agent_name": agent_names.get((ws, payload.get("agent_id"))),
                    "runtime_id": payload.get("runtime_id"),
                    "started_at": payload.get("started_at"),
                    "completed_at": payload.get("completed_at"),
                    "error_kind": payload.get("error_kind"),
                    "error_summary": payload.get("error_summary"),
                }
            )
            return base
        if kind == "runtime":
            base.update(
                {
                    "key": _key(ws, row["resource_id"]),
                    "id": row["resource_id"],
                    "name": payload.get("name"),
                    "status": payload.get("status"),
                    "previous_status": payload.get("previous_status"),
                }
            )
            return base
        raise ValueError(f"unknown change kind {kind}")

    @staticmethod
    def _person(
        ws: str,
        typ: str | None,
        aid: str | None,
        agent_names: dict[tuple[str, str], Any],
        member_names: dict[tuple[str, str], Any],
    ) -> dict[str, Any] | None:
        if not typ or not aid:
            return None
        name = None
        if typ == "agent":
            name = agent_names.get((ws, aid))
        elif typ == "member":
            name = member_names.get((ws, aid))
        return {"type": typ, "id": aid, "name": name}

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

        anomaly_counts = {"h24": 0, "d7": 0}
        anomaly_runs = []
        anomaly_agent_names = {k: r.get("name") for k, r in agent_rows.items()}
        for task in self.store.query(
            "SELECT * FROM tasks WHERE server=? AND status='completed' AND delivery_anomaly IS NOT NULL "
            "AND coalesce(completed_at, created_at, '') >= ? ORDER BY completed_at DESC, id DESC",
            (self.server, cuts["d7"]),
        ):
            when = task["completed_at"] or task["created_at"] or ""
            for window, cut in cuts.items():
                if when >= cut:
                    anomaly_counts[window] += 1
            anomaly_runs.append({**self._run(task, anomaly_agent_names),
                                 "workspace_id": task["workspace_id"], "issue_id": task["issue_id"],
                                 "agent_id": task["agent_id"]})

        return {
            "delivery_anomalies": {"counts": anomaly_counts, "runs": anomaly_runs},
            "generated_at": self.generated_at(),
            "windows": {"h24": RECENT_WINDOW_H, "d7": int(LAST_FAILED_WINDOW.total_seconds() // 3600)},
            "by_provider": by_provider,
            "by_runtime": by_runtime,
            "by_agent": by_agent,
        }

    # --- /api/skills --------------------------------------------------------

    def evidence_inventory(self) -> dict[str, Any]:
        from .evidence_inventory import build_evidence_inventory

        return build_evidence_inventory(self.skills(), workspace_collected=self._workspace_installs() is not None)

    def skills(self, review_days: int = 90) -> dict[str, Any]:
        from .skills_view import parse_review_days, skills_document, _summary

        try:
            days = parse_review_days(None if review_days == 90 else str(review_days))
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        document = skills_document(days)
        from .skills_view import _load_tool_roots, TOOL_ROOTS_PATH, REPO_ROOT
        from .skill_runtimes import runtime_columns
        from .runtime_load import collect_runtime_load
        from .skill_grok_bots import grok_bot_skills
        from .skill_grok_export import grok_export_skills
        try:
            roots, _ = _load_tool_roots(TOOL_ROOTS_PATH, REPO_ROOT)
        except (OSError, ValueError, TypeError, KeyError):
            roots = []
        installs = self._workspace_installs()
        items = []
        seen: set[str] = set()
        for item in document.get("items") or []:
            copied = dict(item)
            key = _skill_name_key(item.get("name")) or ""
            if installs is not None:
                copied["workspace_installs"] = installs.get(key, [])
            if key:
                seen.add(key)
            items.append(copied)
        for name in sorted(installs or {}):
            if name in seen:
                continue
            items.append(_cloud_only_item(name, installs[name]))
        bot_items, bot_source = grok_bot_skills()
        # Keep declarations separate even when a disk/Workspace skill has the same name.
        local_names = {_skill_name_key(item.get("name")) for item in items}
        for item in bot_items:
            item["related_skill_name"] = item["name"] if item["name"] in local_names else None
        items.extend(bot_items)
        export_items, export_source = grok_export_skills()
        for item in export_items:
            item["related_skill_name"] = item["name"] if item["name"] in local_names else None
        items.extend(export_items)
        summary = _summary(items)
        for block in summary:
            if block["id"] == "grok_bot":
                block["uncollected"] = bot_source["state"] != "ok"
                block["detail"] = bot_source["detail"]
            if block["id"] == "grok_bot_export":
                block["uncollected"] = not export_items
                block["detail"] = export_source["detail"]
        meta = {**(document.get("meta") or {})}
        meta["sources"] = [*(meta.get("sources") or []), bot_source, export_source]
        return runtime_columns(
            {**document, "items": items, "summary": summary, "meta": meta},
            self.runtimes(),
            roots,
            collect_runtime_load(),
        )

    def skills_content(self, **arguments) -> dict[str, Any]:
        from .skill_grok_export import PREFIX, read_export_content
        from .skill_content import read_content
        from .skills_view import REPO_ROOT, skills_document
        if arguments.get("source", "local") == "local" and arguments.get("path", "").startswith(PREFIX):
            return read_export_content(**arguments)
        # Cloud selection never consults local files. Only the local inventory
        # (not workspace metadata) can authorize a filesystem read.
        document = {} if arguments.get("source") == "cloud" else skills_document()
        return read_content(document, repo_root=REPO_ROOT, **arguments)

    def _workspace_installs(self) -> dict[str, list[dict[str, Any]]] | None:
        """None when the ACM-291 cache table is not on this board yet."""
        try:
            skills = self.store.query("SELECT * FROM workspace_skills WHERE server=?", (self.server,))
            agents = self.store.query(
                "SELECT workspace_id, id, name, skills_json, skills_detail_json FROM agents WHERE server=?",
                (self.server,),
            )
            workspaces = self.store.query("SELECT id, name, slug FROM workspaces WHERE server=?", (self.server,))
        except sqlite3.OperationalError:
            return None
        return build_workspace_installs(skills, agents, workspaces)

    def skills_meta(self, review_days: int = 90) -> dict[str, Any]:
        document = self.skills(review_days)
        return {
            "ok": document["ok"],
            "error": document.get("error"),
            "generated_at": document["generated_at"],
            "meta": document.get("meta"),
            "summary": document.get("summary"),
            "tools": document.get("tools"),
            "tool_filter_ids": document.get("tool_filter_ids"),
        }

    def skills_attention(self, review_days: int = 90) -> dict[str, Any]:
        return self.skills(review_days)["attention"]

    def skills_attention_text(self, review_days: int = 90) -> str:
        from .skills_view import render_skills_attention_text

        return render_skills_attention_text(self.skills_attention(review_days))
