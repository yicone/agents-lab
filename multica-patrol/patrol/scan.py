"""Issue discovery, incremental scan with cache, coverage accounting."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from . import config, facts, rules
from .cli import CliError


def _fingerprint(issue: dict) -> tuple:
    return (
        issue.get("status"),
        issue.get("assignee_id"),
        issue.get("updated_at"),
        issue.get("last_activity_at"),
    )


def _next_due_at(issue: dict, f: dict, now) -> object:
    """Earliest instant at which a time-threshold rule could newly fire."""
    candidates = []
    status = issue.get("status")
    entered = facts.parse_ts(f.get("entered_status_at"))
    last_progress = facts.parse_ts(f.get("last_progress_at"))
    approval = facts.parse_ts(f.get("approval_wait_at"))
    if status == "in_progress":
        if last_progress:
            candidates.append(last_progress + timedelta(seconds=config.PROGRESS_STALE_S))
        if not issue.get("assignee_id") and entered:
            candidates.append(entered + timedelta(seconds=config.NO_ASSIGNEE_GRACE_S))
    if status == "in_review" and entered:
        candidates.append(entered + timedelta(seconds=config.REVIEW_STALE_S))
    if status == "blocked" and entered:
        candidates.append(entered + timedelta(seconds=config.BLOCKED_STALE_S))
    if approval:
        candidates.append(approval + timedelta(seconds=config.APPROVAL_WAIT_S))
    newest_failed_completed = None
    for run in f.get("runs") or []:
        if run.get("status") == "cancelled":
            continue
        if run.get("status") == "failed":
            newest_failed_completed = facts.parse_ts(run.get("completed_at"))
        break
    if newest_failed_completed:
        candidates.append(newest_failed_completed + timedelta(seconds=config.FAILED_RUN_GRACE_S))
    if status in config.LIGHT_STATUSES:
        candidates.append(now + timedelta(seconds=config.LIGHT_RECHECK_S))
    future = [c for c in candidates if c > now]
    if not future:
        return now + timedelta(seconds=86400)
    return min(future)


def discover(cli) -> tuple[list[dict], list[str]]:
    """Fully paginated issue list per scan status. Failed reads are never empty."""
    issues = []
    errors = []
    for status in config.SCAN_STATUSES:
        offset = 0
        collected = []
        total = None
        status_failed = False
        while True:
            try:
                res = cli.run_json([
                    "issue", "list", "--status", status,
                    "--limit", str(config.PAGE_LIMIT), "--offset", str(offset),
                    "--output", "json"])
            except CliError as exc:
                errors.append(f"issue list {status}@{offset}: {exc}")
                status_failed = True
                break
            page = res.data.get("issues") if isinstance(res.data, dict) else None
            if not isinstance(page, list):
                errors.append(f"issue list {status}@{offset}: bad page shape")
                status_failed = True
                break
            total = res.data.get("total")
            collected.extend(page)
            if not res.data.get("has_more"):
                break
            if not page:
                errors.append(f"issue list {status}@{offset}: no-progress page")
                status_failed = True
                break
            offset += len(page)
        if status_failed:
            continue
        if isinstance(total, int) and total != len(collected):
            errors.append(f"issue list {status}: total={total} collected={len(collected)}")
            continue
        issues.extend(collected)
    return issues, errors


def needs_deep(issue: dict, cached: dict | None, full: bool, now) -> bool:
    if full or cached is None or _fingerprint(issue) != tuple(cached.get("fingerprint") or ()):
        return True
    due = facts.parse_ts(cached.get("next_due_at"))
    return due is not None and now >= due


def _compact_runs(runs: list[dict]) -> list[dict]:
    return [
        {"id": r.get("id"), "status": r.get("status"), "attempt": r.get("attempt"),
         "max_attempts": r.get("max_attempts"), "completed_at": r.get("completed_at"),
         "agent_id": r.get("agent_id")}
        for r in runs
    ]


def _newest_live_id(runs: list[dict]) -> str | None:
    for r in runs or []:
        if r.get("status") != "cancelled":
            return r.get("id")
    return None


def run_scan(cli, state: dict, full: bool, now, monotonic) -> dict:
    """Returns {issues, per_issue: {id: {issue, facts, findings}}, coverage}."""
    discovered, errors = discover(cli)
    cache = state.setdefault("cache", {})
    universe_ids = {i["id"] for i in discovered}
    for stale_id in [k for k in cache if k not in universe_ids]:
        del cache[stale_id]

    per_issue = {}
    deep_jobs = []
    deadline_hit = False
    started = monotonic()

    def budget_left() -> bool:
        return (monotonic() - started) <= config.DEEP_CHECK_BUDGET_S

    def deep_check(issue):
        if issue.get("status") in config.LIGHT_STATUSES:
            return facts.collect_light_facts(cli, issue, now)
        return facts.collect_facts(cli, issue, now)

    for issue in discovered:
        cached = cache.get(issue["id"])
        if needs_deep(issue, cached, full, now):
            if not budget_left():
                deadline_hit = True
                errors.append(f"{issue.get('identifier')}: deferred_by_budget")
                if cached is not None:
                    per_issue[issue["id"]] = {
                        "issue": issue, "facts": cached["facts"],
                        "findings": cached.get("findings") or [],
                        "source": "cache", "deferred": True,
                    }
                else:
                    per_issue[issue["id"]] = {
                        "issue": issue, "facts": None, "findings": [],
                        "source": "deferred", "deferred": True,
                    }
                continue
            deep_jobs.append(issue)
        elif issue.get("assignee_id"):
            # Light refresh for every cached issue with an assignee (any status):
            # run failures/retries may not move the fingerprint. One full runs
            # call both updates the active set and feeds the failed-run rule.
            try:
                res = cli.run_json(["issue", "runs", issue["id"], "--output", "json"])
                runs = _compact_runs(res.data if isinstance(res.data, list) else [])
            except CliError as exc:
                errors.append(f"{issue.get('identifier')}: runs: {exc}")
                per_issue[issue["id"]] = {
                    "issue": issue, "facts": cached["facts"],
                    "findings": cached.get("findings") or [],
                    "source": "cache", "deferred": False,
                }
                continue
            old_facts = cached["facts"]
            old_active = {r.get("id") for r in old_facts.get("active_runs") or []}
            new_active = [r for r in runs if r.get("status") in config.ACTIVE_RUN_STATUSES]
            changed = (_newest_live_id(runs) != _newest_live_id(old_facts.get("runs"))
                       or {r["id"] for r in new_active} != old_active)
            if changed:
                if not budget_left():
                    deadline_hit = True
                    errors.append(f"{issue.get('identifier')}: deferred_by_budget")
                    per_issue[issue["id"]] = {
                        "issue": issue, "facts": old_facts,
                        "findings": cached.get("findings") or [],
                        "source": "cache", "deferred": True,
                    }
                    continue
                deep_jobs.append(issue)
                continue
            old_facts["runs"] = runs
            old_facts["active_runs"] = [{"id": r["id"], "status": r["status"]}
                                        for r in new_active]
            old_facts["checked_at"] = facts.iso(now)
            findings = rules.evaluate(issue, old_facts, now)
            cached["findings"] = findings
            per_issue[issue["id"]] = {
                "issue": issue, "facts": old_facts, "findings": findings,
                "source": "cache+active", "deferred": False,
            }
        else:
            per_issue[issue["id"]] = {
                "issue": issue, "facts": cached["facts"],
                "findings": cached.get("findings") or [],
                "source": "cache", "deferred": False,
            }

    deep_done = 0
    if deep_jobs:
        with ThreadPoolExecutor(max_workers=config.DEEP_CHECK_WORKERS) as pool:
            futures = {pool.submit(deep_check, issue): issue for issue in deep_jobs}
            for future in as_completed(futures):
                issue = futures[future]
                try:
                    f = future.result()
                except Exception as exc:  # per-issue failure must not void the batch
                    errors.append(f"{issue.get('identifier')}: deep check: {exc}")
                    cached = cache.get(issue["id"])
                    per_issue[issue["id"]] = {
                        "issue": issue,
                        "facts": cached["facts"] if cached else None,
                        "findings": (cached or {}).get("findings") or [],
                        "source": "error", "deferred": False,
                    }
                    continue
                deep_done += 1
                findings = rules.evaluate(issue, f, now)
                cache[issue["id"]] = {
                    "fingerprint": list(_fingerprint(issue)),
                    "next_due_at": facts.iso(_next_due_at(issue, f, now)),
                    "findings": findings,
                    "facts": f,
                    "issue": {
                        "id": issue["id"], "identifier": issue.get("identifier"),
                        "status": issue.get("status"),
                        "assignee_id": issue.get("assignee_id"),
                        "parent_issue_id": issue.get("parent_issue_id"),
                    },
                    "checked_at": facts.iso(now),
                }
                per_issue[issue["id"]] = {
                    "issue": issue, "facts": f, "findings": findings,
                    "source": "deep", "deferred": False,
                }

    coverage = {
        "issues_discovered": len(discovered),
        "deep_checked": deep_done,
        "from_cache": sum(1 for p in per_issue.values() if p["source"] in ("cache", "cache+active")),
        "errors": errors,
        "complete": not errors and not deadline_hit,
        "deadline_hit": deadline_hit,
    }
    return {"issues": discovered, "per_issue": per_issue, "coverage": coverage}
