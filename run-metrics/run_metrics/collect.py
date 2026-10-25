"""Read-only Multica collection for the run-metrics prototype.

The allow-list is the whole write boundary: issue/agent/runtime mutations are rejected
before a process is started.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from .redact import redact
from .timeutil import DEFAULT_WINDOW, iso, in_window, parse_ts, window_bounds

READ_PREFIXES = (
    ("issue", "list"),
    ("issue", "runs"),
    ("issue", "timeline"),
    ("issue", "comment", "list"),
    ("runtime", "list"),
    ("runtime", "usage"),
)
_FORBIDDEN_WORDS = frozenset({
    "add", "update", "delete", "create", "assign", "status", "import", "remove",
})
_QUOTA_KEYS = frozenset({
    "quota_remaining", "credits_remaining", "credit_remaining", "recovery_at",
})
_ISSUE_FIELDS = "id,identifier,status,created_at,updated_at,assignee_id,workspace_id,parent_issue_id"


class ReadOnlyViolation(Exception):
    pass


class CollectError(Exception):
    pass


def assert_readonly(args: list[str]) -> None:
    words = [str(arg) for arg in args if not str(arg).startswith("-")]
    if any(word in _FORBIDDEN_WORDS for word in words):
        raise ReadOnlyViolation(" ".join(words))
    if not any(words[:len(prefix)] == list(prefix) for prefix in READ_PREFIXES):
        raise ReadOnlyViolation(" ".join(words) or "(empty)")


def default_runner(args: list[str]) -> dict:
    assert_readonly(args)
    proc = subprocess.run(
        ["multica", *args, "--output", "json"],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if proc.returncode != 0:
        detail = redact(proc.stderr, limit=300) or f"exit {proc.returncode}"
        raise CollectError(detail)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CollectError(redact(str(exc), limit=200) or "invalid json") from exc
    return {"data": data, "stderr": proc.stderr or ""}


def _call(runner: Callable[[list[str]], dict], args: list[str], retries: int = 1) -> dict:
    assert_readonly(args)
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return runner(args)
        except ReadOnlyViolation:
            raise
        except Exception as exc:  # noqa: BLE001 — one retry, then the issue is recorded as a gap
            last = exc
            if attempt < retries:
                time.sleep(0.4)
    assert last is not None
    raise last


def _as_list(data, preferred: str | None = None) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if preferred and isinstance(data.get(preferred), list):
            return data[preferred]
        for key in ("issues", "runs", "comments", "items", "runtimes"):
            if isinstance(data.get(key), list):
                return data[key]
    raise CollectError(f"unexpected payload {type(data).__name__}")


def _quota_remaining_present(payload) -> bool:
    found: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            found.update(str(key).lower() for key in node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return bool(found & _QUOTA_KEYS)


def _slim_run(raw: dict, issue_id: str) -> dict:
    parent = None
    for key in ("parent_run_id", "previous_run_id", "retried_from_run_id"):
        if isinstance(raw.get(key), str) and raw.get(key).strip():
            parent = raw.get(key)
            break
    error = raw.get("error") if isinstance(raw.get("error"), str) else None
    reason = raw.get("failure_reason") if isinstance(raw.get("failure_reason"), str) else None
    slim = {
        "id": raw.get("id"),
        "agent_id": raw.get("agent_id"),
        "runtime_id": raw.get("runtime_id"),
        "workspace_id": raw.get("workspace_id"),
        "issue_id": raw.get("issue_id") or issue_id,
        "status": raw.get("status"),
        "created_at": raw.get("created_at"),
        "started_at": raw.get("started_at"),
        "completed_at": raw.get("completed_at"),
        "attempt": raw.get("attempt") if isinstance(raw.get("attempt"), int) else None,
        "kind": raw.get("kind"),
        "error": redact(error, limit=500),
        "failure_reason": redact(reason, limit=200),
    }
    if parent:
        slim["parent_run_id"] = parent
    return slim


def _changes_from_timeline(issue_id: str, payload, stderr: str) -> tuple[list[dict], bool]:
    rows = []
    for activity in _as_list(payload):
        if not isinstance(activity, dict):
            continue
        if activity.get("action") not in (None, "status_changed"):
            continue
        details = activity.get("details") if isinstance(activity.get("details"), dict) else {}
        source = details.get("from", details.get("from_status"))
        target = details.get("to", details.get("to_status"))
        rows.append({
            "issue_id": issue_id,
            "id": activity.get("id"),
            "created_at": activity.get("created_at"),
            "from": source if isinstance(source, str) and source else None,
            "to": target if isinstance(target, str) and target else None,
        })
    truncated = "truncat" in (stderr or "").lower()
    return rows, truncated


def _comments_from_payload(issue_id: str, payload) -> list[dict]:
    rows = []
    for comment in _as_list(payload):
        if not isinstance(comment, dict):
            continue
        content = comment.get("content") if isinstance(comment.get("content"), str) else ""
        rows.append({
            "issue_id": issue_id,
            "id": comment.get("id"),
            "created_at": comment.get("created_at"),
            "content": redact(content, limit=300),
            "content_truncated": bool(comment.get("content_truncated")),
        })
    return rows


def _needs_comments(runs: list[dict], changes: list[dict]) -> bool:
    if len(runs) >= 2:
        return True
    if any((run.get("attempt") or 0) > 1 for run in runs):
        return True
    for change in changes:
        if change.get("to") == "blocked":
            return True
        if change.get("from") == "in_review" and change.get("to") in {"todo", "in_progress", "backlog"}:
            return True
    return False


def _page_issues(runner: Callable[[list[str]], dict]) -> tuple[list[dict], int | None]:
    offset = 0
    issues: list[dict] = []
    total = None
    while True:
        result = _call(runner, [
            "issue", "list", "--limit", "100", "--offset", str(offset), "--fields", _ISSUE_FIELDS,
        ])
        data = result["data"]
        page = data.get("issues") if isinstance(data, dict) else _as_list(data, "issues")
        if not isinstance(page, list):
            raise CollectError("issue list 没有 issues 数组")
        issues.extend(item for item in page if isinstance(item, dict))
        if isinstance(data, dict):
            total = data.get("total", total)
            has_more = bool(data.get("has_more"))
        else:
            has_more = len(page) == 100
        if not page or not has_more:
            break
        offset += len(page)
    return issues, total


def _fetch_issue(runner: Callable[[list[str]], dict], issue_id: str) -> dict:
    errors = []
    runs: list[dict] = []
    changes: list[dict] = []
    truncated = False
    run_missing = False
    timeline_missing = False
    try:
        result = _call(runner, ["issue", "runs", issue_id])
        runs = [_slim_run(item, issue_id) for item in _as_list(result["data"]) if isinstance(item, dict)]
    except Exception as exc:  # noqa: BLE001 — gap is recorded, collection continues
        run_missing = True
        errors.append(f"runs {issue_id}: {redact(str(exc), limit=180)}")
    try:
        result = _call(runner, ["issue", "timeline", issue_id, "--action", "status_changed"])
        changes, truncated = _changes_from_timeline(issue_id, result["data"], result.get("stderr") or "")
    except Exception as exc:  # noqa: BLE001
        timeline_missing = True
        errors.append(f"timeline {issue_id}: {redact(str(exc), limit=180)}")
    return {
        "issue_id": issue_id,
        "runs": runs,
        "changes": changes,
        "truncated": truncated,
        "run_missing": run_missing,
        "timeline_missing": timeline_missing,
        "errors": errors,
    }


def _fetch_comments(runner: Callable[[list[str]], dict], issue_id: str) -> dict:
    try:
        result = _call(runner, [
            "issue", "comment", "list", issue_id, "--roots-only", "--summary", "--compact",
        ])
        return {"issue_id": issue_id, "comments": _comments_from_payload(issue_id, result["data"]), "error": None}
    except Exception as exc:  # noqa: BLE001
        return {
            "issue_id": issue_id,
            "comments": [],
            "error": f"comments {issue_id}: {redact(str(exc), limit=180)}",
        }


def collect_snapshot(
    *,
    now=None,
    runner: Callable[[list[str]], dict] | None = None,
    workers: int = 6,
    window: str = DEFAULT_WINDOW,
) -> dict:
    """Collect a snapshot. `now` is the window end so a replay stays aligned."""
    runner = runner or default_runner
    from datetime import datetime, timezone

    moment = now or datetime.now(timezone.utc)
    start, end = window_bounds(moment, window)
    issues, total = _page_issues(runner)
    selected = [
        issue for issue in issues
        if in_window(parse_ts(issue.get("created_at")), start, end)
        or in_window(parse_ts(issue.get("updated_at")), start, end)
    ]
    workspace_id = next((issue.get("workspace_id") for issue in issues if issue.get("workspace_id")), None)
    runs: list[dict] = []
    changes: list[dict] = []
    errors: list[str] = []
    truncated_ids: list[str] = []
    run_missing: list[str] = []
    timeline_missing: list[str] = []
    comment_candidates: list[str] = []

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_fetch_issue, runner, issue["id"]) for issue in selected if issue.get("id")]
        done = 0
        for future in as_completed(futures):
            done += 1
            item = future.result()
            runs.extend(item["runs"])
            changes.extend(item["changes"])
            errors.extend(item["errors"])
            if item["truncated"]:
                truncated_ids.append(item["issue_id"])
            if item["run_missing"]:
                run_missing.append(item["issue_id"])
            if item["timeline_missing"]:
                timeline_missing.append(item["issue_id"])
            if _needs_comments(item["runs"], item["changes"]):
                comment_candidates.append(item["issue_id"])
            if done % 25 == 0:
                print(f"collected {done}/{len(futures)} issues", file=sys.stderr, flush=True)

    comments: list[dict] = []
    comment_missing: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_fetch_comments, runner, issue_id) for issue_id in comment_candidates]
        for future in as_completed(futures):
            item = future.result()
            comments.extend(item["comments"])
            if item["error"]:
                comment_missing.append(item["issue_id"])
                errors.append(item["error"])

    runtime_result = _call(runner, ["runtime", "list"])
    runtimes = [item for item in _as_list(runtime_result["data"]) if isinstance(item, dict)]
    probes = []
    for runtime in runtimes:
        runtime_id = runtime.get("id")
        if not runtime_id:
            continue
        try:
            usage = _call(runner, ["runtime", "usage", runtime_id, "--days", "1"])
            payload = usage["data"]
            record_count = len(payload) if isinstance(payload, list) else 1
            probes.append({
                "runtime_id": runtime_id,
                "days": 1,
                "record_count": record_count,
                "quota_remaining_present": _quota_remaining_present(payload),
            })
        except Exception as exc:  # noqa: BLE001
            errors.append(f"usage {runtime_id}: {redact(str(exc), limit=180)}")
            probes.append({
                "runtime_id": runtime_id,
                "days": 1,
                "record_count": None,
                "quota_remaining_present": False,
                "error": redact(str(exc), limit=180),
            })

    return {
        "collected_at": iso(end),
        "mode": "cli-readonly",
        "workspace_id": workspace_id,
        "issue_scan": {
            "workspace_total": total,
            "fetched": len(issues),
            "in_default_window": len(selected),
            "window": window,
            "window_start": iso(start),
        },
        "issues": selected,
        "runs": runs,
        "status_changes": changes,
        "comments": comments,
        "runtimes": [
            {
                "id": runtime.get("id"),
                "name": runtime.get("name"),
                "provider": runtime.get("provider"),
                "status": runtime.get("status"),
                "last_seen_at": runtime.get("last_seen_at"),
            }
            for runtime in runtimes
        ],
        "usage_probes": probes,
        "timeline_truncated_ids": truncated_ids,
        "timeline_missing_ids": timeline_missing,
        "run_missing_ids": run_missing,
        "comment_missing_ids": comment_missing,
        "collection_errors": errors,
    }
