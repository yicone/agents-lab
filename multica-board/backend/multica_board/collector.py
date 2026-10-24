"""Cross-workspace collection: CLI JSON -> normalized, minimized rows -> SQLite."""

from __future__ import annotations

from .stdlib_path import prefer_stdlib_imports

prefer_stdlib_imports()

import hashlib
import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .changes import CHANGE_RETENTION_DAYS, diff_issues, diff_runtimes, events_for_tasks
from .cli_runner import CliError, CliRunner
from .mcp_local import McpContext, fingerprints, load_fingerprint_key, row_id
from .mcp_summary import (
    SUMMARY_KEYS,
    SUMMARY_SERVER_KEYS,
    norm_library_transport,
    own_entry_records,
    plain_text as _mcp_name,
    summarize_mcp_config,
)
from .delivery_anomaly import delivery_anomaly
from .redact import classify_error, redact
from .store import Store

log = logging.getLogger("multica_board.collect")

ISSUE_FIELDS = ",".join(
    [
        "id", "workspace_id", "identifier", "number", "title", "status", "status_category", "status_name",
        "priority", "assignee_type", "assignee_id", "creator_type", "creator_id",
        "parent_issue_id", "project_id", "stage", "start_date", "due_date",
        "created_at", "updated_at", "last_activity_at", "labels", "properties",
    ]
)
ISSUE_PAGE = 100
TASK_PAGE = 200
TERMINAL_TASK = {"completed", "failed", "cancelled"}
WORKSPACE = "_workspace"  # sync_status resource holding the per-workspace collect outcome
COLLECTOR = ("*", "workspaces")  # (workspace_id, resource) of the global `workspace list` outcome
SCOPE_MESSAGE = "CLI identity scoped to another workspace"


class ScopeError(CliError):
    """The CLI answered for a different workspace than the one requested."""


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
    mcp_every: int = 5


def _nn(row: dict[str, Any]) -> dict[str, Any]:
    """CLI empty strings mean "unset"; store them as NULL."""
    return {k: (None if v == "" else v) for k, v in row.items()}


def _labels(raw: Any) -> list[str]:
    out = []
    for lab in raw or []:
        if isinstance(lab, dict):
            out.append(lab.get("name") or lab.get("id") or "")
        else:
            out.append(str(lab))
    return [x for x in out if x]


def norm_property(p: dict[str, Any]) -> dict[str, Any]:
    """Keep id, name, type, and option id/name. Descriptions and colors stay out of the cache."""
    options = []
    config = p.get("config") if isinstance(p.get("config"), dict) else {}
    for opt in config.get("options") or []:
        if isinstance(opt, dict) and opt.get("id"):
            options.append({"id": opt["id"], "name": opt.get("name") or ""})
    return {
        "id": p["id"],
        "name": p.get("name"),
        "type": p.get("type"),
        "archived": int(bool(p.get("archived"))),
        "options_json": json.dumps(options, ensure_ascii=False),
    }


def norm_issue(i: dict[str, Any]) -> dict[str, Any]:
    keep = [
        "id", "identifier", "number", "title", "status", "status_category", "status_name",
        "priority", "assignee_type", "assignee_id", "creator_type", "creator_id",
        "parent_issue_id", "project_id", "stage", "start_date", "due_date",
        "created_at", "updated_at", "last_activity_at",
    ]
    row = _nn({k: i.get(k) for k in keep})
    row["labels_json"] = json.dumps(_labels(i.get("labels")), ensure_ascii=False)
    row["properties_json"] = json.dumps(i.get("properties") or {}, ensure_ascii=False)
    return row


def normalize_instructions(text: str) -> str:
    """Canonical form hashed before redaction: NFC, LF newlines, trimmed edges."""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    return text.strip()


def instructions_sha256(text: str | None) -> str | None:
    """SHA-256 of the normalized original. Secrets stay in the hash input, never in the stored text."""
    if not isinstance(text, str):
        return None
    norm = normalize_instructions(text)
    if not norm:
        return None
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _plain_text(value: Any) -> str | None:
    """Redact a free-text field in full. Non-strings are dropped so nested config cannot land in the cache."""
    if not isinstance(value, str):
        return None
    text = redact(value, limit=None)
    if not isinstance(text, str):
        return None
    text = text.strip()
    return text or None


def skill_details(raw: Any) -> list[dict[str, Any]]:
    """id, name, description, enabled. Skill bodies and any other keys stay out of the cache."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sid = item.get("id") if isinstance(item.get("id"), str) and item.get("id").strip() else None
        name = item.get("name")
        if isinstance(name, str):
            name = " ".join(name.split()) or None
        else:
            name = None
        if sid is None and name is None:
            continue
        out.append(
            {
                "id": sid,
                "name": name,
                "description": _plain_text(item.get("description")),
                "enabled": item.get("enabled") is not False,
            }
        )
    return out


def disabled_runtime_skills(raw: Any) -> list[Any]:
    """Names (and ids) only. Runtime skill bodies are not collected."""
    if not isinstance(raw, list):
        return []
    out: list[Any] = []
    for item in raw:
        if isinstance(item, str):
            name = " ".join(item.split())
            if name:
                out.append(_plain_text(name) or name)
            continue
        if not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {}
        if isinstance(item.get("id"), str) and item.get("id").strip():
            entry["id"] = item["id"].strip()
        name = item.get("name") if isinstance(item.get("name"), str) else None
        if name is not None:
            name = " ".join(name.split()) or None
        if name:
            entry["name"] = _plain_text(name) or name
        if entry:
            out.append(entry)
    return out


def _skill_labels(raw: Any) -> list[str]:
    out: list[str] = []
    for lab in raw or []:
        if isinstance(lab, dict):
            text = lab.get("name") or lab.get("id") or ""
        elif isinstance(lab, str):
            text = lab
        else:
            continue
        if not isinstance(text, str):
            continue
        text = " ".join(text.split())
        if not text:
            continue
        cleaned = redact(text, limit=80)
        if cleaned:
            out.append(cleaned)
    return out


def norm_workspace_skill(s: dict[str, Any]) -> dict[str, Any]:
    """Cloud skill row. config and any other keys are not stored."""
    updated = s.get("updated_at")
    return {
        "id": s["id"],
        "name": " ".join(s["name"].split()) if isinstance(s.get("name"), str) else None,
        "description": _plain_text(s.get("description")),
        "labels_json": json.dumps(_skill_labels(s.get("labels")), ensure_ascii=False),
        "updated_at": updated if isinstance(updated, str) and updated else None,
        "content_hash": skill_content_hash(s.get("content_hash")),
    }


def skill_content_hash(value: Any) -> str | None:
    """Only accept SHA-256 metadata, never cache bodies or arbitrary strings."""
    return value.lower() if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) else None


def enabled_skill_names(raw: Any) -> list[str]:
    """Enabled workspace-skill names only. Skill bodies stay out of the cache."""
    names: list[str] = []
    if not isinstance(raw, list):
        return names
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        name = " ".join(name.split())
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    names.sort()
    return names


def norm_agent(a: dict[str, Any]) -> dict[str, Any]:
    keep = [
        "id", "name", "status", "runtime_id", "runtime_mode", "model", "thinking_level",
        "service_tier", "max_concurrent_tasks", "visibility", "owner_id", "archived_at", "updated_at",
    ]
    row = _nn({k: a.get(k) for k in keep})
    row["runtime_bound"] = int(bool(a.get("runtime_bound")))
    row["skills_json"] = json.dumps(enabled_skill_names(a.get("skills")), ensure_ascii=False)
    row["description"] = _plain_text(a.get("description"))
    original = a.get("instructions") if isinstance(a.get("instructions"), str) else None
    row["instructions_sha256"] = instructions_sha256(original)
    row["instructions"] = _plain_text(normalize_instructions(original)) if original is not None else None
    row["skills_detail_json"] = json.dumps(skill_details(a.get("skills")), ensure_ascii=False)
    row["disabled_runtime_skills_json"] = json.dumps(
        disabled_runtime_skills(a.get("disabled_runtime_skills")), ensure_ascii=False
    )
    try:
        # Only the summary may be stored; the raw config never reaches the cache or logs.
        summary = summarize_mcp_config(a.get("mcp_config"), a.get("mcp_config_redacted"))
        assert set(summary) <= SUMMARY_KEYS
        assert all(set(s) <= SUMMARY_SERVER_KEYS for s in summary["servers"])
    except Exception:
        log.warning("mcp_config summary failed for one agent")
        summary = {"state": "invalid", "servers": []}
    row["mcp_own_json"] = json.dumps(summary, ensure_ascii=False)
    return row


def norm_runtime(r: dict[str, Any]) -> dict[str, Any]:
    keep = ["id", "name", "custom_name", "provider", "runtime_mode", "status", "last_seen_at", "visibility", "owner_id"]
    row = _nn({k: r.get(k) for k in keep})
    meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
    row["cli_version"] = meta.get("cli_version") or meta.get("version") or None
    row["device_info"] = redact(r.get("device_info") or None, limit=160)
    return row


_REASON_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def _failure_reason(raw: Any) -> str | None:
    """Keep short reason codes only. Free-text reasons can carry account or token text."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if _REASON_CODE.fullmatch(text):
        return text
    return None


def norm_task(t: dict[str, Any]) -> dict[str, Any]:
    keep = [
        "id", "agent_id", "runtime_id", "issue_id", "kind", "status", "attempt", "max_attempts",
        "created_at", "dispatched_at", "started_at", "completed_at",
    ]
    row = _nn({k: t.get(k) for k in keep})
    err = t.get("error")
    if err is not None and not isinstance(err, str):
        err = json.dumps(err, ensure_ascii=False)
    err = err or None
    reason = _failure_reason(t.get("failure_reason"))
    row["error_summary"] = redact(err)
    row["failure_reason"] = reason
    row["error_kind"] = classify_error(err, reason) if (err or reason or t.get("status") == "failed") else None
    if row["error_kind"] is None and t.get("status") == "failed":
        row["error_kind"] = "unknown"
    row.update(delivery_anomaly(t))
    row["delivery_checked"] = 1
    if row["delivery_anomaly"]:
        row["error_kind"] = None
        row["error_summary"] = "[Error] An error occurred while processing your request."
    return row


class Collector:
    def __init__(self, runner: CliRunner, store: Store, server: str, opts: CollectOptions,
                 mcp_ctx: McpContext | None = None):
        self.runner = runner
        self.store = store
        self.server = server
        self.opts = opts
        self.mcp_ctx = mcp_ctx

    # --- top level ------------------------------------------------------------

    def collect(self) -> dict[str, Any]:
        self.store.prune_changes(self.server, iso(utcnow() - timedelta(days=CHANGE_RETENTION_DAYS)))
        started = iso(utcnow())
        try:
            res = self.runner.run(["workspace", "list"])
        except CliError as exc:
            self._collector_failed(started, str(exc), _kind(exc))
            return {"ok": False, "error": str(exc), "workspaces": {}}
        except Exception as exc:  # e.g. OSError spawning the CLI; must still reach /api/meta
            log.exception("workspace list failed")
            msg = redact(f"{type(exc).__name__}: {exc}")
            self._collector_failed(started, msg, "unknown")
            return {"ok": False, "error": msg, "workspaces": {}}
        visible = [
            _nn({"id": w["id"], "name": w.get("name"), "slug": w.get("slug")})
            for w in (res.data or [])
            if isinstance(w, dict) and w.get("id")
        ]
        selected, missing = self._select(visible)
        purged = self.store.set_workspaces(self.server, selected)
        if missing:
            self._collector_failed(
                started, redact(f"allow-listed workspaces not visible to this CLI identity: {', '.join(missing)}"), "auth"
            )
        else:
            self.store.record_sync(self.server, *COLLECTOR, at=started, ok=True, count=len(selected))

        results: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=max(1, self.opts.concurrency)) as pool:
            futures = {pool.submit(self.collect_workspace, w["id"]): w for w in selected}
            for fut, w in futures.items():
                try:
                    results[w["id"]] = fut.result()
                except Exception as exc:  # isolate one workspace's failure from the rest
                    log.exception("workspace %s failed", w["id"])
                    msg = redact(str(exc))
                    self.store.record_sync(self.server, w["id"], WORKSPACE, at=iso(utcnow()), ok=False,
                                           error=msg, error_kind="unknown")
                    results[w["id"]] = {"ok": False, "error": msg}
        self.store.set_meta("last_collect", {"started_at": started, "finished_at": iso(utcnow())})
        return {"ok": all(r.get("ok") for r in results.values()), "workspaces": results, "purged": purged}

    def _collector_failed(self, at: str, message: str, kind: str) -> None:
        log.warning("collector: %s", message)
        self.store.record_sync(self.server, *COLLECTOR, at=at, ok=False, error=message[:200], error_kind=kind)

    def _select(self, visible: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        if self.opts.all_workspaces and not self.opts.workspaces:
            return visible, []
        wanted = set(self.opts.workspaces)
        picked = [w for w in visible if w["id"] in wanted or (w.get("slug") and w["slug"] in wanted)]
        missing = wanted - {w["id"] for w in picked} - {w.get("slug") for w in picked}
        return picked, sorted(missing)

    # --- per workspace ------------------------------------------------------

    def collect_workspace(self, ws: str) -> dict[str, Any]:
        at_start = iso(utcnow())
        out: dict[str, Any] = {"ok": True}
        errors: list[tuple[str, CliError]] = []
        steps = {
            "issues": self._issues,
            "projects": self._projects,
            "members": self._members,
            "runtimes": self._runtimes,
            "agents": self._agents,
            "workspace_skills": self._workspace_skills,
            "property_defs": self._property_defs,
        }
        agents: list[dict[str, Any]] | None = None

        def step(name: str):
            at = iso(utcnow())
            try:
                count, complete, extra = steps[name](ws)
            except CliError as exc:
                self.store.record_sync(self.server, ws, name, at=at, ok=False, error=str(exc), error_kind=_kind(exc))
                return name, exc, None
            self.store.record_sync(self.server, ws, name, at=at, ok=True, count=count, complete=complete)
            return name, count, extra

        with ThreadPoolExecutor(max_workers=len(steps)) as pool:
            for name, result, extra in pool.map(step, steps):
                if isinstance(result, CliError):
                    errors.append((name, result))
                    out[name] = f"error: {result}"
                else:
                    out[name] = result
                    if name == "agents":
                        agents = extra

        if agents is not None and not any(_kind(e) == "auth" for _, e in errors):
            at = iso(utcnow())
            try:
                count, complete = self._tasks(ws, agents)
                self.store.record_sync(self.server, ws, "tasks", at=at, ok=True, count=count, complete=complete)
                out["tasks"] = count
            except CliError as exc:
                errors.append(("tasks", exc))
                out["tasks"] = f"error: {exc}"
                self.store.record_sync(self.server, ws, "tasks", at=at, ok=False, error=str(exc), error_kind=_kind(exc))
            try:
                # Older CLIs lack the mcp commands: failures are recorded under their own
                # resources and must not flip the workspace outcome. `auth` still purges.
                self._mcp(ws, agents)
            except CliError as exc:
                if _kind(exc) == "auth":
                    errors.append(("mcp", exc))
                else:
                    self.store.record_sync(self.server, ws, "mcp_library", at=iso(utcnow()), ok=False,
                                           error=str(exc), error_kind=_kind(exc))
            except Exception as exc:
                log.warning("workspace %s mcp collection failed", ws)
                self.store.record_sync(self.server, ws, "mcp_library", at=iso(utcnow()), ok=False,
                                       error=redact(str(exc)), error_kind="unknown")

        if not errors:
            self.store.record_sync(self.server, ws, WORKSPACE, at=at_start, ok=True)
            return out

        out["ok"] = False
        name, first = next(((n, e) for n, e in errors if _kind(e) == "auth"), errors[0])
        kind = _kind(first)
        if kind == "auth":
            # Revoked access or a mis-scoped identity: never keep (or show) possibly foreign data.
            self.store.purge_data(self.server, ws)
        self.store.record_sync(self.server, ws, WORKSPACE, at=at_start, ok=False,
                               error=f"{name}: {first}"[:200], error_kind=kind)
        return out

    def _check_scope(self, ws: str, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            got = r.get("workspace_id")
            if got and got != ws:
                raise ScopeError(SCOPE_MESSAGE)

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
            self._check_scope(ws, page)
            total = data.get("total", total)
            for i in page:
                if i.get("id"):
                    seen[i["id"]] = norm_issue(i)  # offset drift can repeat rows; dedupe by UUID
            if not data.get("has_more") or not page:
                break
            offset += len(page)
        complete = total is None or len(seen) >= int(total)
        self.store.replace_snapshot(
            self.server, ws, "issues", list(seen.values()), observe=diff_issues, at=iso(utcnow())
        )
        return len(seen), complete, None

    def _projects(self, ws: str):
        res = self.runner.run(["project", "list"], workspace_id=ws)
        items = _as_list(res.data, "projects")
        self._check_scope(ws, items)
        rows = [_nn({"id": p["id"], "title": p.get("title"), "status": p.get("status")}) for p in items if p.get("id")]
        self.store.replace_snapshot(self.server, ws, "projects", rows)
        return len(rows), True, None

    def _members(self, ws: str):
        res = self.runner.run(["workspace", "member", "list", ws], workspace_id=ws)
        items = _as_list(res.data, "members")
        self._check_scope(ws, items)
        rows = [
            _nn({"user_id": m.get("user_id") or m.get("id"), "name": m.get("name"), "role": m.get("role")})
            for m in items
        ]
        rows = [r for r in rows if r["user_id"]]
        self.store.replace_snapshot(self.server, ws, "members", rows)
        return len(rows), True, None

    def _runtimes(self, ws: str):
        res = self.runner.run(["runtime", "list"], workspace_id=ws)
        items = _as_list(res.data, "runtimes")
        self._check_scope(ws, items)
        rows = [norm_runtime(r) for r in items if r.get("id")]
        self.store.replace_snapshot(self.server, ws, "runtimes", rows, observe=diff_runtimes, at=iso(utcnow()))
        return len(rows), True, None

    def _agents(self, ws: str):
        res = self.runner.run(["agent", "list", "--include-archived"], workspace_id=ws)
        items = _as_list(res.data, "agents")
        self._check_scope(ws, items)
        rows = [norm_agent(a) for a in items if a.get("id")]
        self.store.replace_snapshot(self.server, ws, "agents", rows)
        self._record_own(ws, items)
        return len(rows), True, rows

    def _record_own(self, ws: str, items: list[dict[str, Any]]) -> None:
        """Fingerprint each agent's raw mcp_config into OwnFingerprints (§3.5).

        Raw configs and raw server names stay in process memory; the store only
        gets the whitelisted summary written by norm_agent. Counts only in logs.
        """
        ctx = self.mcp_ctx
        if ctx is None:
            return
        own = ctx.own
        keep: set[str] = set()
        key_res = load_fingerprint_key(ctx.data_dir, geteuid=ctx.geteuid)
        own.sync_key(key_res["stamp"])
        key = key_res["key"]
        at = iso(utcnow())
        for a in items:
            aid = a.get("id")
            if not aid or a.get("archived_at"):
                continue
            keep.add(aid)
            config = a.get("mcp_config")
            if key is None or not isinstance(config, dict):
                continue
            recs = own_entry_records(config)
            order: list[str] = []
            servers: dict[str, dict[str, bytes]] = {}
            for rec in recs:
                rid = row_id(ctx.salt, rec["raw"])
                order.append(rid)
                if rec["fp_inputs"] is not None:
                    servers[rid] = fingerprints(rec["fp_inputs"], key)
            own.record_agent(ws, aid, agent_updated_at=a.get("updated_at"),
                             collected_at=at, servers=servers, order=order)
        own.prune_agents(ws, keep)
        log.info("own fingerprints: %d agents", len(own.agents))

    def _workspace_skills(self, ws: str):
        res = self.runner.run(["skill", "list"], workspace_id=ws)
        items = _as_list(res.data, "skills")
        self._check_scope(ws, items)
        rows = [norm_workspace_skill(s) for s in items if s.get("id")]
        previous = {row["id"]: row for row in self.store.query(
            "SELECT id, updated_at, content_hash FROM workspace_skills WHERE server=? AND workspace_id=?",
            (self.server, ws),
        )}
        for row in rows:
            if row["content_hash"]:
                continue
            old = previous.get(row["id"], {})
            if row["updated_at"] and old.get("updated_at") == row["updated_at"] and old.get("content_hash"):
                row["content_hash"] = old["content_hash"]
                continue
            try:
                # Default get returns the hash without fetching SKILL.md or attachment bodies.
                detail = self.runner.run(["skill", "get", row["id"]], workspace_id=ws).data
            except CliError as exc:
                if _kind(exc) == "auth":
                    raise
                continue  # Older CLI / unavailable detail: explicit time inference in the UI.
            if not isinstance(detail, dict):
                continue
            self._check_scope(ws, [detail])
            if detail.get("id") == row["id"] and detail.get("updated_at") == row["updated_at"]:
                row["content_hash"] = skill_content_hash(detail.get("content_hash"))
        self.store.replace_snapshot(self.server, ws, "workspace_skills", rows)
        return len(rows), True, None

    def _property_defs(self, ws: str):
        """Option names are required to parse 主层 / 涉及层 by the L1–L5 prefix."""
        res = self.runner.run(["property", "list"], workspace_id=ws)
        items = _as_list(res.data, "properties")
        self._check_scope(ws, items)
        rows = [norm_property(p) for p in items if p.get("id")]
        self.store.replace_snapshot(self.server, ws, "property_defs", rows)
        return len(rows), True, None

    def _mcp_state(self, ws: str) -> dict[str, Any]:
        rows = self.store.query(
            "SELECT fingerprint, rounds, bindings_state, bindings_collected_at FROM mcp_state "
            "WHERE server=? AND workspace_id=?",
            (self.server, ws),
        )
        return rows[0] if rows else {}

    def _set_mcp_state(self, ws: str, row: dict[str, Any]) -> None:
        self.store.upsert_many(self.server, ws, "mcp_state", [{k: row.get(k) for k in
                               ("fingerprint", "rounds", "bindings_state", "bindings_collected_at")}])

    def _mcp(self, ws: str, agents: list[dict[str, Any]]) -> None:
        """Workspace MCP library plus per-agent bindings. `auth` CliErrors propagate."""
        at = iso(utcnow())
        try:
            res = self.runner.run(["workspace", "mcp", "list", ws], workspace_id=ws)
        except CliError as exc:
            self.store.record_sync(self.server, ws, "mcp_library", at=at, ok=False,
                                   error=str(exc), error_kind=_kind(exc))
            if _kind(exc) == "auth":
                raise
            return  # keep the last good library rows; skip bindings this round
        items = _as_list(res.data, "servers")
        self._check_scope(ws, items)
        rows = []
        lib_ids: list[tuple[str, str]] = []
        for s in items:
            if not s.get("id"):
                continue
            count = s.get("agent_count")
            raw_name = s.get("name") if isinstance(s.get("name"), str) else ""
            display = _mcp_name(raw_name)
            rows.append({
                "id": s["id"],
                "name": display,
                "name_exact": int(display == raw_name and display is not None),
                "transport": norm_library_transport(s.get("transport")),
                "agent_count": count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else 0,
                "created_at": s.get("created_at") if isinstance(s.get("created_at"), str) else None,
                "updated_at": s.get("updated_at") if isinstance(s.get("updated_at"), str) else None,
            })
            if self.mcp_ctx is not None:
                lib_ids.append((s["id"], row_id(self.mcp_ctx.salt, raw_name)))
        if self.mcp_ctx is not None:
            self.mcp_ctx.own.record_library(ws, lib_ids)
        self.store.replace_snapshot(self.server, ws, "mcp_servers", rows)
        self.store.record_sync(self.server, ws, "mcp_library", at=at, ok=True, count=len(rows))

        prev = self._mcp_state(ws)
        if not rows or sum(r["agent_count"] for r in rows) == 0:
            # Determined empty: no agent mcp calls. Distinguish "none" from "never read".
            self.store.replace_snapshot(self.server, ws, "mcp_bindings", [])
            self.store.replace_snapshot(self.server, ws, "mcp_agent_state", [])
            self._set_mcp_state(ws, {"fingerprint": None, "rounds": 0, "bindings_state": "none",
                                     "bindings_collected_at": prev.get("bindings_collected_at")})
            self.store.record_sync(self.server, ws, "mcp_bindings", at=at, ok=True, count=0)
            if self.mcp_ctx is not None:
                self.mcp_ctx.own.workspaces.add(ws)
            return

        fingerprint = hashlib.sha256(
            "\n".join(sorted(f"{r['id']}|{r['updated_at']}|{r['agent_count']}" for r in rows)).encode()
        ).hexdigest()
        rounds = int(prev.get("rounds") or 0) + 1
        refresh = (
            not prev.get("fingerprint")
            or prev["fingerprint"] != fingerprint
            or rounds >= max(1, self.opts.mcp_every)
            or (self.mcp_ctx is not None and ws not in self.mcp_ctx.own.workspaces)
        )
        if not refresh:
            self._set_mcp_state(ws, {**prev, "rounds": rounds})
            return

        active = [a for a in agents if a.get("id") and not a.get("archived_at")]
        collected: dict[str, list[dict[str, Any]]] = {}
        failures: dict[str, CliError] = {}

        def one(agent_id: str) -> tuple[str, list[dict[str, Any]] | None, CliError | None]:
            try:
                res = self.runner.run(["agent", "mcp", "list", agent_id], workspace_id=ws)
            except CliError as exc:
                return agent_id, None, exc
            items = _as_list(res.data, "servers")
            self._check_scope(ws, items)
            bindings = []
            for b in items:
                sid = b.get("id") or b.get("server_id")
                if not isinstance(sid, str) or not sid:
                    continue
                bindings.append({
                    "agent_id": agent_id,
                    "server_id": sid,
                    "name": _mcp_name(b.get("name")),
                    "enabled": 0 if b.get("enabled") is False else 1,
                })
            return agent_id, bindings, None

        with ThreadPoolExecutor(max_workers=max(1, self.opts.concurrency)) as pool:
            for agent_id, bindings, exc in pool.map(one, [a["id"] for a in active]):
                if exc is not None:
                    failures[agent_id] = exc
                else:
                    collected[agent_id] = bindings or []
        auth = next((e for e in failures.values() if _kind(e) == "auth"), None)
        if auth is not None:
            self.store.record_sync(self.server, ws, "mcp_bindings", at=iso(utcnow()), ok=False,
                                   error=str(auth), error_kind="auth")
            raise auth

        self.store.replace_snapshot(self.server, ws, "mcp_bindings", list(sum(collected.values(), [])))
        self.store.replace_snapshot(
            self.server, ws, "mcp_agent_state",
            [{"agent_id": a["id"],
              "state": ("error" if a["id"] in failures else ("ok" if collected.get(a["id"]) else "none"))}
             for a in active],
        )
        if self.mcp_ctx is not None and not failures:
            self.mcp_ctx.own.workspaces.add(ws)
        if not failures:
            state = "ok"
        elif len(failures) == len(active):
            state = "error"
        else:
            state = "partial"
        self.store.record_sync(self.server, ws, "mcp_bindings", at=at, ok=not failures,
                               error=None if not failures else f"{len(failures)} agent mcp reads failed",
                               error_kind=None if not failures else "unknown",
                               count=len(collected))
        # Any failure keeps the old fingerprint so the next cycle retries the refresh.
        self._set_mcp_state(ws, {
            "fingerprint": fingerprint if not failures else prev.get("fingerprint"),
            "rounds": 0 if not failures else rounds,
            "bindings_state": state,
            "bindings_collected_at": iso(utcnow()) if not failures else prev.get("bindings_collected_at"),
        })

    def _tasks(self, ws: str, agents: list[dict[str, Any]]):
        cutoff = iso(utcnow() - timedelta(days=self.opts.run_days))
        with ThreadPoolExecutor(max_workers=max(1, self.opts.concurrency)) as pool:
            results = list(pool.map(lambda a: self._agent_tasks(ws, a["id"], cutoff), agents))
        self.store.prune_tasks(self.server, ws, iso(utcnow() - timedelta(days=max(self.opts.run_days * 2, 30))))
        return sum(n for n, _ in results), all(c for _, c in results)

    def _agent_tasks(self, ws: str, agent_id: str, cutoff: str) -> tuple[int, bool]:
        resource = f"tasks:{agent_id}"
        baselined = self.store.has_baseline(self.server, ws, resource)
        known = self.store.known_task_ids(self.server, ws, agent_id)
        cursor: str | None = None
        total = pages = 0
        finished = False
        try:
            while True:
                args = ["agent", "tasks", agent_id, "--limit", str(TASK_PAGE)]
                if cursor:
                    args += ["--before", cursor]
                res = self.runner.run(args, workspace_id=ws)
                items = _as_list(res.data, "tasks")
                self._check_scope(ws, items)
                rows = [norm_task(t) for t in items if t.get("id")]
                self.store.upsert_tasks(
                    self.server, ws, rows, observe=events_for_tasks if baselined else None, at=iso(utcnow())
                )
                total += len(rows)
                pages += 1
                cursor = res.next_cursor
                if not cursor or not rows:
                    finished = True
                    return total, True
                oldest = min((r["created_at"] or "") for r in rows)
                if oldest and oldest < cutoff:
                    finished = True
                    return total, True
                # Incremental: stop once we reach terminal runs we already cached.
                if not self.opts.full and any(known.get(r["id"]) in TERMINAL_TASK for r in rows):
                    finished = True
                    return total, True
                if pages >= self.opts.max_task_pages:
                    finished = True
                    return total, False
        finally:
            if finished and not baselined:
                self.store.set_baseline(self.server, ws, resource)


def _kind(exc: CliError) -> str:
    if isinstance(exc, ScopeError):
        return "auth"
    return classify_error(str(exc)) or "unknown"


def _as_list(data: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict) and isinstance(data.get(key), list):
        return [x for x in data[key] if isinstance(x, dict)]
    return []
