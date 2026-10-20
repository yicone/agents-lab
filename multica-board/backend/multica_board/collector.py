"""Cross-workspace collection: CLI JSON -> normalized, minimized rows -> SQLite."""

from __future__ import annotations

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
    }


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
    return row


class Collector:
    def __init__(self, runner: CliRunner, store: Store, server: str, opts: CollectOptions):
        self.runner = runner
        self.store = store
        self.server = server
        self.opts = opts

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
        return len(rows), True, rows

    def _workspace_skills(self, ws: str):
        res = self.runner.run(["skill", "list"], workspace_id=ws)
        items = _as_list(res.data, "skills")
        self._check_scope(ws, items)
        rows = [norm_workspace_skill(s) for s in items if s.get("id")]
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
