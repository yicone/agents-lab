"""Read-only historical adapter. Reuses Board collection, never patrol write paths.

Raw CLI responses exist only in memory. The on-disk metrics snapshot contains
IDs/enums/timestamps; comments, actor names, emails and run errors are discarded.
"""
from __future__ import annotations

import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .cli_runner import CliError, CliResult
from .collector import Collector, CollectOptions
from .metrics import iso, ts
from .store import Store
from .redact import classify_error

ISSUE_KEYS = {"workspace_id", "id", "parent_issue_id", "status", "created_at", "assignee_id"}
RUN_KEYS = {"workspace_id", "id", "issue_id", "agent_id", "runtime_id", "status", "created_at", "completed_at"}


def _reason(exc):
    if "permission" in str(exc).lower():
        return "permission_denied"
    return classify_error(str(exc)) or "collection_failed"


def _rows(result):
    if not isinstance(result.data, list):
        raise CliError("expected a list response")
    if any(not isinstance(r, dict) for r in result.data):
        raise CliError("invalid list row")
    return result.data


def _complete(result):
    # Timeline has a server cap; comments since reads do not fold resolved threads.
    return not any(word in result.stderr.lower() for word in ("truncat", "next ", "partial", "folded"))


def collect_history(runner, *, days=28, now=None, workers=4, previous=None):
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    workspaces = _rows(runner.run(["workspace", "list"]))
    if len({w.get("id") for w in workspaces}) != len(workspaces) or any(not w.get("id") for w in workspaces):
        raise CliError("invalid workspace identity")
    if previous and (previous.get("profile") != runner.profile or
                     not set(previous.get("workspace_ids", [])).issubset({w["id"] for w in workspaces})):
        raise ValueError("resume snapshot identity or workspace scope changed")
    issues, runs, errors = [], [], []
    # A private throwaway store reuses existing issue paging and run paging/scope checks.
    # It never opens or changes the running Board's SQLite database.
    with tempfile.TemporaryDirectory(prefix="atlas-metrics-") as tmp:
        store = Store(Path(tmp) / "history.sqlite")
        collector = Collector(CheckedHistoryReads(runner), store, runner.profile,
                              CollectOptions(all_workspaces=True, run_days=days, full=True,
                                             max_task_pages=1000, concurrency=workers))
        def workspace(w):
            ws = w["id"]
            failures = []
            try:
                _, complete, _ = collector._issues(ws)
                if not complete:
                    failures.append({"workspace_id": ws, "resource": "issues", "reason": "incomplete_pagination"})
            except CliError as exc:
                failures.append({"workspace_id": ws, "resource": "issues", "reason": _reason(exc)})
            try:
                _, _, agents = collector._agents(ws)
                _, complete = collector._tasks(ws, agents)
                if not complete:
                    failures.append({"workspace_id": ws, "resource": "runs", "reason": "incomplete_pagination"})
            except CliError as exc:
                failures.append({"workspace_id": ws, "resource": "runs", "reason": _reason(exc)})
            return failures
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for failures in pool.map(workspace, [] if previous else workspaces):
                    errors.extend(failures)
            issues = [{k: r.get(k) for k in ISSUE_KEYS} for r in store.query("SELECT * FROM issues WHERE server=?", (runner.profile,))]
            runs = [{k: r.get(k) for k in RUN_KEYS} for r in store.query("SELECT * FROM tasks WHERE server=?", (runner.profile,))]
        finally:
            store.close()
    if previous:
        issues, runs = previous["issues"], previous["runs"]
        errors = [e for e in previous["errors"] if e["resource"] not in {"timeline", "comments"}]
    events, history_complete, comments_complete = [], {}, {}
    prior_events = {}
    if previous:
        for e in previous.get("events", []):
            prior_events.setdefault(f"{e['workspace_id']}/{e['issue_id']}", []).append(e)
    # Need all ancestors to resolve delivery identity; deep history only for issues active
    # during the window (created before the window is deliberately allowed).
    def deep(issue):
        ws, iid = issue["workspace_id"], issue["id"]
        key = f"{ws}/{iid}"
        out, failures = [], []
        history_ok = comments_ok = False
        try:
            if previous and previous.get("history_complete", {}).get(key):
                result = CliResult([{"id": e["event_id"], "action": "status_changed", "created_at": e["at"],
                                     "details": {"from": e.get("previous_status"), "to": e.get("status")}}
                                    for e in prior_events.get(key, []) if e["event_type"] == "status"])
            else:
                result = runner.run(["issue", "timeline", iid, "--activity-only"], workspace_id=ws)
            rows = _rows(result)
            history_ok = _complete(result)
            for r in rows:
                if r.get("workspace_id") not in (None, ws) or r.get("issue_id") not in (None, iid):
                    raise CliError("history scope mismatch")
                if r.get("action") == "status_changed" and r.get("id") and ts(r.get("created_at")):
                    details = r.get("details") or {}
                    out.append({"workspace_id": ws, "issue_id": iid, "event_id": r["id"],
                                "event_type": "status", "at": r["created_at"], "source": "multica_timeline",
                                "confidence": "complete", "status": details.get("to"),
                                "previous_status": details.get("from")})
            if not history_ok:
                failures.append({"workspace_id": ws, "issue_id": iid, "resource": "timeline", "reason": "server_truncated"})
        except CliError as exc:
            out = []
            history_ok = False
            failures.append({"workspace_id": ws, "issue_id": iid, "resource": "timeline", "reason": _reason(exc)})
        try:
            created = ts(issue.get("created_at"))
            if not created:
                raise CliError("missing creation time")
            if previous and previous.get("comments_complete", {}).get(key):
                result = CliResult([{"id": e["event_id"], "type": "comment", "created_at": e["at"], "author_type": e.get("actor_type")}
                                    for e in prior_events.get(key, []) if e["event_type"] in {"comment_observed", "attention_unclassified"}])
            else:
                result = runner.run(["issue", "comment", "list", iid, "--since", iso(created - timedelta(seconds=1)), "--compact"], workspace_id=ws)
            comments_ok = _complete(result)
            for r in _rows(result):
                if r.get("workspace_id") not in (None, ws) or r.get("issue_id") not in (None, iid):
                    raise CliError("comment scope mismatch")
                if r.get("type") == "comment" and r.get("id") and ts(r.get("created_at")):
                    # No keyword guessing: an approval/rework requires curated event classification.
                    out.append({"workspace_id": ws, "issue_id": iid, "event_id": r["id"],
                                "event_type": "attention_unclassified" if r.get("author_type") == "member" else "comment_observed",
                                "actor_type": r.get("author_type") if r.get("author_type") in {"member", "agent"} else "unknown",
                                "at": r["created_at"], "source": "multica_comments", "confidence": "partial"})
            if not comments_ok:
                failures.append({"workspace_id": ws, "issue_id": iid, "resource": "comments", "reason": "incomplete_history"})
        except CliError as exc:
            # Drop partial comments from a mismatched response, retaining timeline only.
            out = [e for e in out if e["source"] != "multica_comments"]
            comments_ok = False
            failures.append({"workspace_id": ws, "issue_id": iid, "resource": "comments", "reason": _reason(exc)})
        return key, out, history_ok, comments_ok, failures
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, out, hok, cok, failures in pool.map(deep, issues):
            events.extend(out)
            history_complete[key], comments_complete[key] = hok, cok
            errors.extend(failures)
    return {"schema_version": 1, "profile": runner.profile, "since": previous.get("since") if previous else iso(start), "collected_at": previous.get("collected_at") if previous else iso(now),
            "recovered_at": iso(now) if previous else None,
            "collection_finished_at": iso(datetime.now(timezone.utc)),
            "complete": not errors, "inventory_complete": not any(e["resource"] == "issues" for e in errors), "workspace_ids": [w["id"] for w in workspaces],
            "issues": issues, "runs": runs, "events": events,
            "history_complete": history_complete, "comments_complete": comments_complete,
            "errors": errors,
            "unavailable": ["delivery_ledger", "manual_handling_ledger", "wait_reason", "per_run_cost", "quota_attribution",
                            "9router_observability_not_collected"]}


def write_private(path, value):
    """Atomic owner-only snapshots; a failed replacement leaves the previous file intact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".metrics-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class TaskHistoryRunner:
    """Explicit task-credential mode, confined to the runtime owner's visible scope.

    Profile-based Board collection stays unchanged. This mode is opt-in for an
    assigned local task where the CLI already authenticates via its environment.
    Uses the same read allow-list, subprocess safety and response parser.
    """
    profile = "current-task"

    def __init__(self):
        from .cli_runner import CliRunner
        class TaskCLI(CliRunner):
            def _env(self):
                env = dict(os.environ)
                env["GODEBUG"] = "tlsmlkem=0"
                return env
        self.cli = TaskCLI(profile=self.profile, retries=0)

    def run(self, args, workspace_id=None):
        from .cli_runner import assert_read_only
        assert_read_only(args)
        argv = [self.cli.binary]
        if workspace_id:
            argv += ["--workspace-id", workspace_id]
        argv += args
        if "--output" not in args:
            argv += ["--output", "json"]
        # Match the Board runner's bounded retry policy for transient read failures.
        for attempt in range(3):
            try:
                return self.cli._run_once(argv)
            except CliError as exc:
                if not exc.transient or attempt == 2:
                    raise
                self.cli._sleep(self.cli.backoff_s * (2 ** attempt))


def retain_events(snapshot):
    """Bound the local normalized event snapshot to the agreed 90 days.

    A lifecycle cut by retention loses its complete-history claim. Snapshot
    inventories remain available to resolve parents outside the event window.
    """
    anchor = ts(snapshot.get("recovered_at")) or ts(snapshot["collected_at"])
    cutoff = anchor - timedelta(days=90)
    kept, gaps = [], set()
    for e in snapshot["events"]:
        at = ts(e.get("at"))
        if at and at >= cutoff:
            kept.append(e)
        else:
            key = f"{e['workspace_id']}/{e['issue_id']}"
            gaps.add(key)
            field = "history_complete" if e["event_type"] == "status" else "comments_complete"
            snapshot[field][key] = False
    snapshot["events"] = kept
    snapshot["retention_days"] = 90
    snapshot["retained_since"] = iso(cutoff)
    snapshot["retention_gap_issues"] = sorted(gaps)
    return snapshot


class CheckedHistoryReads:
    """Reject malformed inventory/run responses before the shared collector sees them."""
    def __init__(self, runner):
        self.runner = runner

    def run(self, args, workspace_id=None):
        result = self.runner.run(args, workspace_id=workspace_id)
        if args[:2] == ["issue", "list"]:
            data = result.data
            if (not isinstance(data, dict) or not isinstance(data.get("issues"), list)
                    or not isinstance(data.get("has_more"), bool)
                    or any(not isinstance(row, dict) or not row.get("id") for row in data["issues"])
                    or (data["has_more"] and not data["issues"])):
                raise CliError("malformed issue page")
        elif args[:2] in (["agent", "list"], ["agent", "tasks"]):
            key = "agents" if args[1] == "list" else "tasks"
            data = result.data.get(key) if isinstance(result.data, dict) else result.data
            if not isinstance(data, list) or any(not isinstance(row, dict) or not row.get("id") for row in data):
                raise CliError("malformed agent/run page")
        return result
