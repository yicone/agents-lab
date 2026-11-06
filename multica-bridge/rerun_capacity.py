"""At-capacity auto-rerun: classifier, state table, gates, dry-run planner, executor.

auto_rerun = off | dry_run | on
  dry_run  plan and log only (never calls Multica write commands)
  on       same plan; a due "fire" calls `multica issue rerun <issue>` after a fresh
           pre-flight re-read, then reads back the new run. Nothing else is written:
           no status changes, no comments.
"""
from __future__ import annotations

import json
import os
import random
import re
from datetime import datetime, timedelta, timezone

ALLOWED_FAILURE_REASONS = frozenset({
    "provider_capacity",
    "rate_limit",
    "agent_error.model_not_found_or_unavailable",
})
ALLOWED_REASON_FAMILIES = frozenset({"provider_capacity", "rate_limit"})

CAPACITY_TEXT_RE = re.compile(
    r"(selected\s+model\s+is\s+at\s+capacity"
    r"|model\s+is\s+at\s+capacity"
    r"|is\s+at\s+capacity"
    r"|\boverloaded\b"
    r"|http(?:\s+status)?\s*429"
    r"|\bstatus(?:\s+code)?\s*[:=]?\s*429\b"
    r"|\b429\s*(?:too\s+many|error|rate|capacity))",
    re.I,
)
BARE_429_RE = re.compile(r"\b429\b")
EXPLICIT_CAPACITY_RE = re.compile(r"(at\s+capacity|\boverloaded\b)", re.I)
VETO_RE = re.compile(
    r"(\bquota\b|insufficient\s+(?:quota|balance)|no\s+resource\s+package|billing"
    r"|unauthorized|forbidden|invalid\s+(?:api\s+)?key|authentication\s+failed"
    r"|model\s+not\s+found|does\s+not\s+exist|unknown\s+model|no\s+such\s+model)",
    re.I,
)

SECRET_RE = re.compile(
    r"(?i)(authorization|bearer|api[-_]?key|token|webhook(?:[-_]?secret)?)\s*[:=]\s*\S+"
)
TOKENISH_RE = re.compile(r"\b(?:sk|mat|ghp|gho|xox)[-_][A-Za-z0-9._\-/+=]{8,}\b")

ACTIVE_STATUSES = frozenset({"queued", "dispatched", "running", "waiting_local_directory"})
SUCCESS_STATUSES = frozenset({"completed", "succeeded", "success"})
RETRYABLE_ISSUE_STATUSES = frozenset({"todo", "in_progress"})
BACKOFF_MINUTES = (5, 10, 20)
MAX_ATTEMPTS = 3
WINDOW = timedelta(hours=24)
FINGERPRINT = "capacity"
VALID_MODES = ("off", "dry_run", "on")


def utcnow():
    return datetime.now(timezone.utc)


def parse_ts(s):
    if not s:
        return None
    s = str(s).replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        frac = "".join(ch for ch in rest if ch.isdigit())
        tz = rest[len(frac):]
        s = f"{head}.{frac[:6].ljust(6, '0')}{tz}"
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt):
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sanitize_error(text, limit=300):
    t = (text or "").replace("\r", " ").strip()
    t = SECRET_RE.sub(lambda m: m.group(1) + "=<redacted>", t)
    t = TOKENISH_RE.sub("<redacted>", t)
    t = re.sub(r"\s+", " ", t)
    return t if len(t) <= limit else t[:limit] + "…"


# ---------------------------------------------------------------- classifier

def _reason_allowed(failure_reason):
    reason = (failure_reason or "").strip()
    if not reason:
        return False
    if reason in ALLOWED_FAILURE_REASONS:
        return True
    family = reason.split(".", 1)[0]
    return family in ALLOWED_REASON_FAMILIES


def is_capacity_failure(failure_reason, error_text):
    """Narrow match: allowed failure_reason AND explicit capacity/429/overloaded text.

    Other agent_error values never match. Quota/auth/model-not-found text is vetoed
    unless an explicit at-capacity/overloaded phrase is also present.
    """
    if not _reason_allowed(failure_reason):
        return False
    text = error_text or ""
    if not (CAPACITY_TEXT_RE.search(text) or BARE_429_RE.search(text)):
        return False
    if VETO_RE.search(text) and not EXPLICIT_CAPACITY_RE.search(text):
        return False
    return True


def is_autopilot_run(run):
    """Autopilot tasks have their own cadence; never auto-rerun them."""
    r = run or {}
    if "autopilot" in str(r.get("kind") or "").lower():
        return True
    attr = r.get("attribution") or {}
    ev = attr.get("evidence") if isinstance(attr, dict) else None
    if isinstance(ev, dict) and "autopilot" in str(ev.get("kind") or "").lower():
        return True
    return bool(r.get("autopilot_id") or r.get("autopilot_run_id"))


def is_rerunnable_failure(run):
    r = run or {}
    if (r.get("status") or "") != "failed":
        return False
    if is_autopilot_run(r):
        return False
    return is_capacity_failure(r.get("failure_reason"), r.get("error"))


def fingerprint_for(failure_reason, error_text):
    if not is_capacity_failure(failure_reason, error_text):
        return None
    return FINGERPRINT


def backoff_seconds(attempt_number, rng=None):
    """attempt_number is 1, 2, or 3 → 5 / 10 / 20 minutes ±20% jitter."""
    if attempt_number < 1 or attempt_number > MAX_ATTEMPTS:
        raise ValueError(f"attempt_number out of range: {attempt_number}")
    base = BACKOFF_MINUTES[attempt_number - 1] * 60
    jitter = (rng or random).uniform(0.8, 1.2)
    return base * jitter


def auto_rerun_mode(routes, route=None):
    route = route or {}
    raw = route.get("auto_rerun", (routes or {}).get("auto_rerun", "dry_run"))
    if raw not in VALID_MODES:
        return "dry_run", f"WARN invalid auto_rerun={raw!r}, treating as dry_run"
    return raw, None


# ---------------------------------------------------------------- state table

def rerun_key(issue_id, agent_id, fingerprint, window_start_iso):
    return f"{issue_id}|{agent_id}|{fingerprint}|{window_start_iso}"


def _entry_key(entry):
    return rerun_key(entry["issue_id"], entry["agent_id"], entry["fingerprint"], entry["failure_window"])


def find_open_entry(entries, issue_id, agent_id, fingerprint, now):
    """Pending or exhausted entry for this key inside the 24h window.

    An exhausted entry still blocks: a capacity failure of our own last rerun
    must not open a fresh 3-attempt budget.
    """
    found = None
    for e in entries.values():
        if e.get("issue_id") != issue_id or e.get("agent_id") != agent_id:
            continue
        if e.get("fingerprint") != fingerprint:
            continue
        if e.get("outcome") not in (None, "pending", "exhausted"):
            continue
        start = parse_ts(e.get("failure_window"))
        if start is None or now - start > WINDOW:
            continue
        if found is None or (parse_ts(e.get("updated_at")) or start) > (parse_ts(found.get("updated_at")) or start):
            found = e
    return found


def new_entry(*, issue_id, agent_id, run_id, failure_reason, error, completed_at, identifier,
              workspace_id, workspace_slug, now, rng=None):
    start = parse_ts(completed_at) or now
    nxt = now + timedelta(seconds=backoff_seconds(1, rng))
    return {
        "issue_id": issue_id,
        "agent_id": agent_id,
        "fingerprint": FINGERPRINT,
        "failure_window": iso(start),
        "run_id": run_id,
        "attempt": 0,
        "next_retry_at": iso(nxt),
        "outcome": "pending",
        "exhausted_alerted": False,
        "rerun_ids": [],
        "inflight_at": None,
        "identifier": identifier,
        "workspace_id": workspace_id,
        "workspace_slug": workspace_slug,
        "failure_reason": failure_reason,
        "error_summary": sanitize_error(error),
        "created_at": iso(now),
        "updated_at": iso(now),
    }


def upsert_from_failure(entries, *, issue_id, agent_id, run_id, failure_reason, error,
                        completed_at, identifier, workspace_id, workspace_slug, now, rng=None):
    """Returns (entry, created|updated|exhausted_hold|ignored)."""
    fp = fingerprint_for(failure_reason, error)
    if not fp:
        return None, "ignored"
    existing = find_open_entry(entries, issue_id, agent_id, fp, now)
    if existing is None:
        e = new_entry(
            issue_id=issue_id, agent_id=agent_id, run_id=run_id, failure_reason=failure_reason,
            error=error, completed_at=completed_at, identifier=identifier,
            workspace_id=workspace_id, workspace_slug=workspace_slug, now=now, rng=rng,
        )
        entries[_entry_key(e)] = e
        return e, "created"
    if existing.get("outcome") == "exhausted":
        return existing, "exhausted_hold"
    existing["run_id"] = run_id
    existing["failure_reason"] = failure_reason
    existing["error_summary"] = sanitize_error(error)
    existing["identifier"] = identifier or existing.get("identifier")
    existing["updated_at"] = iso(now)
    return existing, "updated"


def recover_inflight(entry, now, rng=None):
    """A crash between 'attempt marked' and 'result saved' leaves inflight_at set.

    The rerun may or may not have been created, so the attempt stays consumed
    and the next one is scheduled from the recorded fire time. Never refires now.
    """
    t = parse_ts(entry.get("inflight_at"))
    if t is None:
        return False
    entry["inflight_at"] = None
    n = int(entry.get("attempt") or 0)
    entry["next_retry_at"] = iso(t + timedelta(seconds=backoff_seconds(n + 1, rng))) if n < MAX_ATTEMPTS else None
    entry["last_error"] = "recovered after interrupted rerun; attempt counted"
    entry["updated_at"] = iso(now)
    return True


def prune_entries(entries, now):
    """Drop cleared/exhausted rows 24h after their last update; expire stale pending."""
    keep = {}
    for k, e in entries.items():
        if e.get("outcome") in ("cleared", "exhausted"):
            upd = parse_ts(e.get("updated_at")) or parse_ts(e.get("created_at"))
            start = parse_ts(e.get("failure_window"))
            if upd and now - upd > WINDOW and (start is None or now - start > WINDOW):
                continue
        start = parse_ts(e.get("failure_window"))
        if start and now - start > WINDOW and e.get("outcome") == "pending":
            e = dict(e)
            e["outcome"] = "cleared"
            e["clear_reason"] = "window_expired"
            e["next_retry_at"] = None
            e["updated_at"] = iso(now)
        keep[k] = e
    return keep


# ---------------------------------------------------------------- gates

def _run_time(r):
    return (parse_ts(r.get("completed_at") or r.get("started_at") or r.get("created_at"))
            or datetime.min.replace(tzinfo=timezone.utc))


def _newest(runs):
    return max(runs, key=_run_time) if runs else None


def evaluate_gates(entry, *, issue_status, assignee_id, runs, now):
    """Return (action, reason). action: wait|skip|fire|clear|retarget|exhaust.

    retarget's reason is the newer capacity-failed run id. Order follows ACM-119:
    window, issue status, assignee, active runs, newest run is still ours, budget, due.
    """
    start = parse_ts(entry.get("failure_window"))
    if start and now - start > WINDOW:
        return "clear", "window_expired"
    if (issue_status or "") not in RETRYABLE_ISSUE_STATUSES:
        return "clear", f"status:{issue_status or 'unknown'}"
    if not assignee_id:
        return "clear", "unassigned"
    if assignee_id != entry.get("agent_id"):
        return "clear", "assignee_changed"

    runs = runs or []
    nxt = parse_ts(entry.get("next_retry_at"))
    due = nxt is None or now >= nxt
    if any((r.get("status") or "") in ACTIVE_STATUSES for r in runs):
        return ("skip", "active_run") if due else ("wait", "active_run")

    agent = entry.get("agent_id")
    mine = [r for r in runs if not r.get("agent_id") or r.get("agent_id") == agent]
    newest = _newest(mine)
    if newest and newest.get("id") and newest.get("id") != entry.get("run_id"):
        st = newest.get("status") or ""
        if st in SUCCESS_STATUSES:
            return "clear", "run_succeeded"
        if is_rerunnable_failure(newest):
            return "retarget", newest["id"]
        return "clear", "new_run"

    if entry.get("outcome") == "exhausted" or int(entry.get("attempt") or 0) >= MAX_ATTEMPTS:
        return "exhaust", "budget_exhausted"
    if not due:
        return "wait", "before_next_retry"
    return "fire", "due"


def _schedule_after_fire(entry, n, now, rng=None):
    entry["attempt"] = n
    entry["last_fire_at"] = iso(now)
    entry["next_retry_at"] = iso(now + timedelta(seconds=backoff_seconds(n + 1, rng))) if n < MAX_ATTEMPTS else None


def _exhaust(entry, mode):
    entry["outcome"] = "exhausted"
    entry["next_retry_at"] = None
    first = not entry.get("exhausted_alerted")
    entry["exhausted_alerted"] = True
    if not first:
        return None
    return {"event": "exhausted", "reason": "budget_exhausted", "entry": entry,
            "note": plan_note(entry, "exhausted", mode)}


def apply_gate(entry, action, reason, *, now, mode="dry_run", newest_run=None, rng=None):
    """Mutate entry for a gate result. Returns an event dict or None (silent).

    "fire" here is the dry-run simulation; in mode=on the caller runs
    execute_rerun() instead and must not pass "fire" to this function.
    """
    ident = entry.get("identifier") or entry.get("issue_id")
    entry["updated_at"] = iso(now)
    exhausted = entry.get("outcome") == "exhausted"
    if action == "wait":
        return None
    if action == "skip":
        return {"event": "skipped", "reason": reason, "entry": entry,
                "note": f"{_prefix(mode)}: skip {ident} ({reason}); retry due {entry.get('next_retry_at') or 'now'}"}
    if action == "clear":
        entry["outcome"] = "cleared"
        entry["next_retry_at"] = None
        entry["clear_reason"] = reason
        return {"event": "cleared", "reason": reason, "entry": entry,
                "note": f"{_prefix(mode)}: clear {ident} ({reason}); no rerun"}
    if action == "retarget":
        rid = newest_run if isinstance(newest_run, str) else (newest_run or {}).get("id") or reason
        entry["run_id"] = rid
        if exhausted:
            return None
        if int(entry.get("attempt") or 0) >= MAX_ATTEMPTS:
            return _exhaust(entry, mode)
        return {"event": "retargeted", "reason": reason, "entry": entry,
                "note": f"{_prefix(mode)}: retarget {ident} to run {rid}; next retry {entry.get('next_retry_at')}"}
    if action == "exhaust":
        if exhausted and entry.get("exhausted_alerted"):
            return None
        return _exhaust(entry, mode)
    if action == "fire":
        if exhausted:
            return None
        n = int(entry.get("attempt") or 0) + 1
        _schedule_after_fire(entry, n, now, rng)
        return {"event": "would_rerun", "reason": reason, "entry": entry, "attempt": n,
                "note": plan_note(entry, "would_rerun", mode)}
    return None


# ---------------------------------------------------------------- executor (mode=on)

def _runs_list(raw):
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("runs") or raw.get("items") or []
    return []


def preflight(entry, mc):
    """Fresh read right before a rerun: issue get + full runs + --active runs."""
    ws, iid = entry["workspace_id"], entry["issue_id"]
    issue = mc("--workspace-id", ws, "issue", "get", iid, "--output", "json") or {}
    runs = _runs_list(mc("--workspace-id", ws, "issue", "runs", iid, "--output", "json"))
    active = _runs_list(mc("--workspace-id", ws, "issue", "runs", iid, "--active", "--output", "json"))
    seen = {r.get("id") for r in runs}
    runs = runs + [a for a in active if a.get("id") not in seen]
    return issue.get("status"), issue.get("assignee_id"), runs


def execute_rerun(entry, *, mc, now, save=None, rng=None):
    """Re-check gates on fresh data, then `multica issue rerun`. Never comments or sets status.

    Returns (event_or_None, gate_action, gate_reason). When the fresh gate is not
    "fire", the entry is updated via apply_gate and no rerun is made.
    """
    try:
        status, assignee, runs = preflight(entry, mc)
    except Exception as e:
        entry["last_error"] = sanitize_error(f"preflight: {e}")
        entry["updated_at"] = iso(now)
        return ({"event": "skipped", "reason": "preflight_error", "entry": entry,
                 "note": f"AUTO-RERUN: skip {entry.get('identifier')} (preflight read failed); will retry next tick"},
                "skip", "preflight_error")
    action, reason = evaluate_gates(entry, issue_status=status, assignee_id=assignee, runs=runs, now=now)
    if action != "fire":
        ev = apply_gate(entry, action, reason, now=now, mode="on", newest_run=reason if action == "retarget" else None)
        return ev, action, reason

    n = int(entry.get("attempt") or 0) + 1
    entry["attempt"] = n
    entry["inflight_at"] = iso(now)
    entry["updated_at"] = iso(now)
    if save:
        save()

    ws, iid = entry["workspace_id"], entry["issue_id"]
    new_id, err = None, None
    try:
        res = mc("--workspace-id", ws, "issue", "rerun", iid, "--output", "json")
        if isinstance(res, dict):
            new_id = res.get("id") or res.get("task_id") or res.get("run_id")
    except Exception as e:
        err = sanitize_error(str(e))

    confirmed, dup = False, False
    if err is None:
        try:
            active = _runs_list(mc("--workspace-id", ws, "issue", "runs", iid, "--active", "--output", "json"))
            mine = [a for a in active if not a.get("agent_id") or a.get("agent_id") == entry.get("agent_id")]
            confirmed = bool(new_id) and any(a.get("id") == new_id for a in mine)
            if not confirmed and new_id:
                full = _runs_list(mc("--workspace-id", ws, "issue", "runs", iid, "--output", "json"))
                confirmed = any(r.get("id") == new_id for r in full)
            dup = len(mine) > 1
        except Exception as e:
            entry["last_error"] = sanitize_error(f"readback: {e}")

    _schedule_after_fire(entry, n, now, rng)
    entry["inflight_at"] = None
    if new_id:
        entry.setdefault("rerun_ids", []).append(new_id)
    entry["last_rerun_id"] = new_id
    if err:
        entry["last_error"] = err
    elif confirmed:
        entry["last_error"] = None
    entry["updated_at"] = iso(now)
    if save:
        save()
    kind = "rerun_error" if err else "rerun"
    ev = {"event": kind, "reason": "due", "entry": entry, "attempt": n, "new_run_id": new_id,
          "confirmed": confirmed, "duplicate_active": dup, "error": err,
          "note": plan_note(entry, kind, "on", new_run_id=new_id, error=err,
                            confirmed=confirmed, duplicate=dup)}
    return ev, action, reason


# ---------------------------------------------------------------- notes

def _prefix(mode):
    return "AUTO-RERUN" if mode == "on" else "DRY-RUN"


def plan_note(entry, kind, mode="dry_run", *, new_run_id=None, error=None, confirmed=None, duplicate=False):
    ident = entry.get("identifier") or entry.get("issue_id")
    rid = entry.get("run_id")
    n = int(entry.get("attempt") or 0)
    nxt = entry.get("next_retry_at")
    after = f"next retry at {nxt}" if nxt else "final attempt; exhausted alert if it fails again"
    p = _prefix(mode)
    if kind == "scheduled":
        verb = "will rerun" if mode == "on" else "if auto_rerun=on, would retry"
        tail = "" if mode == "on" else " No rerun executed."
        return (f"{p}: {verb} {ident} at {nxt} as attempt {n + 1}/{MAX_ATTEMPTS} "
                f"(backoff 5/10/20 min ±20% jitter) for failed run {rid}.{tail}")
    if kind == "would_rerun":
        return f"{p}: would rerun {ident} NOW as attempt {n}/{MAX_ATTEMPTS} for failed run {rid}; {after}. No rerun executed."
    if kind == "rerun":
        chk = "" if confirmed else " (new run not yet visible in readback)"
        dup = " WARN: more than one active run for this agent after rerun." if duplicate else ""
        return (f"{p}: reran {ident} attempt {n}/{MAX_ATTEMPTS} via `issue rerun` (fresh session) "
                f"for failed run {rid} → new run {new_run_id or '?'}{chk}; {after}.{dup}")
    if kind == "rerun_error":
        return f"{p}: rerun of {ident} attempt {n}/{MAX_ATTEMPTS} failed: {error}; attempt counted; {after}."
    if kind == "exhausted":
        tail = (" No further automatic reruns. Suggest: switch account/model, manual rerun, or check 9router."
                if mode == "on" else " No further retries; no rerun executed.")
        return f"{p}: auto_rerun budget exhausted for {ident} ({MAX_ATTEMPTS}/{MAX_ATTEMPTS}); last failed run {rid}.{tail}"
    if kind == "exhausted_hold":
        return f"{p}: {ident} already exhausted in this 24h window; failed run {rid} not rerun."
    return f"{p}: {kind} {ident} run {rid}."


def dry_run_note(entry, kind="scheduled"):
    """Phase-1 compatible wrapper."""
    k = {"would_rerun_final": "would_rerun"}.get(kind, kind)
    return plan_note(entry, k, "dry_run")


def notify_event_id(entry, event, attempt=None):
    base = f"auto_rerun:{entry.get('issue_id')}:{entry.get('agent_id')}:{entry.get('failure_window')}"
    if event == "exhausted":
        return f"{base}:exhausted"
    return f"{base}:attempt{attempt or entry.get('attempt')}"


# ---------------------------------------------------------------- persistence

def load_rerun_state(path):
    if not os.path.exists(path):
        return {"_v": 1, "entries": {}}
    with open(path) as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        return {"_v": 1, "entries": {}}
    raw.setdefault("_v", 1)
    raw.setdefault("entries", {})
    return raw


def save_atomic(path, obj):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        data = json.dumps(obj, ensure_ascii=False, indent=None).encode()
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def acquire_lock(path):
    import fcntl
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def release_lock(fd):
    import fcntl
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
