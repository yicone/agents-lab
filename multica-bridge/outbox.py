"""Persistent outbox, retry/circuit, hold/mute, and drain for the status bridge.

v2: per-destination items + independent circuit breakers.
fanout=shadow: POST only to steward (existing webhook); record intended dest.
fanout=send: POST to each dest's own endpoint. Only HTTP 200 is accepted.
Routing is projects, then title rules, then platform_failure, then workspace, then fallback.
A title rule with before_project beats the project match. Missing Keychain creds hold,
mute, or move to the configured fallback without raising or burning attempts.
"""
from __future__ import annotations

import json
import os
import random
import re
from collections import deque
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

MAX_ATTEMPTS = 8
MAX_BACKOFF_SEC = 3600
PROBE_INTERVAL_SEC = 3600
CIRCUIT_ALERT_AFTER_SEC = 30 * 60
HOLD_NO_CRED_SEC = 900
MAX_PER_TICK = 20
TTL_NORMAL = timedelta(days=7)
TTL_EXCEPTION = timedelta(days=30)
PRUNE_ACCEPTED = timedelta(days=7)
PRUNE_DEAD = timedelta(days=30)
CLIENT_ERROR = frozenset({400, 401, 403, 404})
EXCEPTION_KINDS = frozenset({"run_failed", "parse_failed", "net_alert"})
VALID_MODES = frozenset({"send", "hold", "mute"})
VALID_FANOUT = frozenset({"shadow", "send"})
MISSING_CRED_ACTIONS = frozenset({"hold", "fallback", "mute"})
STATUS_ACTOR_GATE = frozenset({"in_review", "blocked"})
STEWARD_DEST = "steward"
ACM_LAB_SLUG = "acm-lab"
# 15:55 SGT 2026-10-14 issue_list batch: network timeouts misfiled as parse_failed
NETWORK_MISFILED_PARSE_IDS = frozenset({
    "parse_failed:8ebc87ea-27f2-42c4-90f6-24a04e64d3f5:issue_list:2026-10-14T07",
    "parse_failed:b648cd0b-31bf-40fd-8bc1-3f60987d2532:issue_list:2026-10-14T07",
    "parse_failed:ffa95ec6-4e32-4a09-bd10-c707230ae40d:issue_list:2026-10-14T07",
})
MUTE_NETWORK_REASON = (
    "2026-10-14T15:55 network timeout misfiled as parse_failed; do not replay"
)

_SECRETISH = re.compile(
    r"(?i)(authorization|bearer\s+\S+|api[-_]?key|https?://\S+)"
)


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


def empty_circuit():
    return {"state": "closed", "opened_at": None, "last_http": None,
            "next_probe_at": None, "consecutive_client_errors": 0,
            "alerted_over_threshold": None}


def fanout_mode(routes):
    routes = routes or {}
    f = routes.get("fanout")
    if f in VALID_FANOUT:
        return f
    return "send"


def steward_dest(routes=None):
    routes = routes or {}
    return routes.get("steward_dest") or routes.get("target_alias") or STEWARD_DEST


def target_alias(routes):
    """Legacy single-alias helper; prefer dest on each item."""
    return steward_dest(routes)


def endpoints(routes):
    routes = routes or {}
    ep = routes.get("endpoints")
    return ep if isinstance(ep, dict) else {}


def matched_route(routes, slug, project_id):
    """Explicit project then workspace. Does NOT fall back to default/小秘书."""
    routes = routes or {}
    if project_id:
        r = (routes.get("projects") or {}).get(project_id)
        if r:
            return r
    if slug:
        r = (routes.get("workspaces") or {}).get(slug)
        if r:
            return r
    return {}


def endpoint_ref_for_bot(routes, route):
    route = route or {}
    if route.get("endpoint_ref"):
        return route["endpoint_ref"]
    bot_id = route.get("bot_id")
    bot = route.get("bot")
    for ref, spec in endpoints(routes).items():
        if not isinstance(spec, dict):
            continue
        if bot_id and spec.get("bot_id") == bot_id:
            return ref
        if bot and spec.get("bot") == bot:
            return ref
    return None


def _as_route(spec):
    if isinstance(spec, str) and spec:
        return {"endpoint_ref": spec}
    if isinstance(spec, dict):
        return spec
    return {}


def project_route(routes, project_id):
    """Exact project id, else the single longest prefix match. Ambiguous ties miss."""
    projects = (routes or {}).get("projects") or {}
    if not project_id or not isinstance(projects, dict):
        return None
    pid = str(project_id)
    exact = projects.get(pid)
    if exact:
        return _as_route(exact)
    matches = []
    for key, spec in projects.items():
        key = str(key)
        if not key or key == pid:
            continue
        if pid.startswith(key) or key.startswith(pid):
            matches.append((len(key), spec))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    top = matches[0][0]
    best = [item for item in matches if item[0] == top]
    if len(best) != 1:
        return None
    return _as_route(best[0][1])


def _title_matches(rule, change):
    if not isinstance(rule, dict):
        return False
    slug = (change or {}).get("workspace_slug")
    want = rule.get("workspace") or rule.get("workspace_slug")
    if want and want != slug:
        return False
    if rule.get("require_no_project") and (change or {}).get("project_id"):
        return False
    title = str((change or {}).get("title") or "")
    prefixes = []
    if rule.get("prefix"):
        prefixes.append(rule["prefix"])
    extra = rule.get("prefixes") or []
    if isinstance(extra, str):
        extra = [extra]
    if isinstance(extra, list):
        prefixes.extend(extra)
    contains = rule.get("contains") or []
    if isinstance(contains, str):
        contains = [contains]
    if not isinstance(contains, list):
        contains = []
    if any(title.startswith(prefix) for prefix in prefixes if prefix):
        return True
    return any(piece and piece in title for piece in contains)


def matching_title_rule(routes, change, *, before_project):
    rules = (routes or {}).get("title_rules") or []
    if not isinstance(rules, list):
        return None
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if bool(rule.get("before_project")) != before_project:
            continue
        if _title_matches(rule, change):
            return rule
    return None


def is_platform_failure(change):
    change = change or {}
    if change.get("platform_failure") is True:
        return True
    return change.get("kind") in EXCEPTION_KINDS


def platform_failure_ref(change, routes):
    routes = routes or {}
    cfg = routes.get("platform_failure")
    if isinstance(cfg, str) and cfg:
        return cfg
    if not isinstance(cfg, dict):
        return steward_dest(routes)
    slug = (change or {}).get("workspace_slug")
    scoped = cfg.get("workspaces") if isinstance(cfg.get("workspaces"), dict) else {}
    if slug and scoped.get(slug):
        return scoped[slug]
    return cfg.get("dest") or cfg.get("fallback_dest") or steward_dest(routes)


def _ref_of(routes, route):
    route = _as_route(route)
    ref = endpoint_ref_for_bot(routes, route)
    if ref:
        return ref
    direct = route.get("endpoint_ref")
    return direct or None


def resolve_dest(change, routes=None):
    """Return (endpoint_ref, reason).

    More specific wins:
    before_project title rules, projects[project_id], other title rules,
    platform_failure, workspaces, then fallback.
    A before_project title rule is the explicit exception that beats a project
    (Bot 描述入 Git stays with steward even inside Agent Atlas).
    Does not raise.
    """
    try:
        return _resolve_dest(change, routes)
    except Exception:
        return steward_dest(routes or {}), "fallback"


def _resolve_dest(change, routes=None):
    routes = routes or {}
    steward = steward_dest(routes)
    change = change or {}
    early = matching_title_rule(routes, change, before_project=True)
    if early:
        ref = _ref_of(routes, early)
        if ref:
            return ref, "title"
    project = project_route(routes, change.get("project_id"))
    if project:
        ref = _ref_of(routes, project)
        if ref:
            return ref, "project"
    titled = matching_title_rule(routes, change, before_project=False)
    if titled:
        ref = _ref_of(routes, titled)
        if ref:
            return ref, "title"
    if is_platform_failure(change):
        ref = platform_failure_ref(change, routes)
        if ref:
            return ref, "platform_failure"
    slug = change.get("workspace_slug")
    raw = (routes.get("workspaces") or {}).get(slug) if slug else None
    if raw:
        ref = _ref_of(routes, raw)
        if ref:
            return ref, "workspace"
    return steward, "fallback"


def select_latest_status_event(events, new_status):
    """Return (event or None, ordered).

    Times are parsed as UTC, including fractional seconds and numeric offsets.
    ordered is False when a matching event has no parseable timestamp, so the
    caller must not treat another event as the latest.
    """
    hits = []
    for event in events or []:
        if not isinstance(event, dict) or event.get("action") != "status_changed":
            continue
        details = event.get("details") or {}
        if details.get("to") != new_status:
            continue
        hits.append(event)
    if not hits:
        return None, True
    keyed = []
    for event in hits:
        when = parse_ts(event.get("created_at"))
        if when is None:
            return None, False
        keyed.append((when, str(event.get("id") or ""), event))
    keyed.sort(key=lambda item: (item[0], item[1]))
    return keyed[-1][2], True


def latest_status_actor(events, new_status):
    """actor_type of the newest timeline event that entered new_status."""
    event, ordered = select_latest_status_event(events, new_status)
    if not ordered or event is None:
        return None
    actor = event.get("actor_type")
    if actor is None:
        return None
    text = str(actor).strip().lower()
    return text or None


def status_actor_allows(new_status, actor_type):
    """in_review and blocked are queued only when an agent caused them."""
    if (new_status or "") not in STATUS_ACTOR_GATE:
        return True
    return str(actor_type or "").strip().lower() == "agent"


def missing_cred_action(routes, dest):
    cfg = (routes or {}).get("missing_cred") or {}
    if not isinstance(cfg, dict):
        return "hold"
    per = cfg.get("endpoints") if isinstance(cfg.get("endpoints"), dict) else {}
    action = per.get(dest) or cfg.get("default") or "fallback"
    if action not in MISSING_CRED_ACTIONS:
        return "hold"
    return action


def missing_cred_fallback_dest(routes, dest):
    cfg = (routes or {}).get("missing_cred") or {}
    if not isinstance(cfg, dict):
        return None
    fallback = cfg.get("fallback_dest") or steward_dest(routes)
    if not fallback or fallback == dest:
        return None
    return fallback


def circuit_alert_after_sec(routes):
    raw = (routes or {}).get("circuit_alert_after_sec")
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return CIRCUIT_ALERT_AFTER_SEC
    if seconds <= 0:
        return CIRCUIT_ALERT_AFTER_SEC
    return seconds


def circuit_threshold_alert(box, now, dest, routes=None):
    """One summary once an open breaker has stayed open for more than the threshold."""
    circuit = circuit_of(box, dest)
    if circuit.get("state") != "open":
        return None
    opened = parse_ts(circuit.get("opened_at"))
    if opened is None:
        return None
    age = (now - opened).total_seconds()
    limit = circuit_alert_after_sec(routes)
    if age <= limit:
        return None
    if circuit.get("alerted_over_threshold"):
        return None
    circuit["alerted_over_threshold"] = iso(now)
    return (
        f"circuit alert dest={dest} open_sec={int(age)} threshold_sec={int(limit)} "
        f"last_http={circuit.get('last_http')} opened_at={circuit.get('opened_at')}"
    )


def unpack_poster(result):
    """(status, retry_after, error_kind, exception_type). Accepts the older 3-tuple."""
    if isinstance(result, tuple):
        if len(result) >= 4:
            return result[0], result[1], result[2], result[3]
        if len(result) == 3:
            return result[0], result[1], result[2], None
        if len(result) == 2:
            return result[0], result[1], None, None
        if len(result) == 1:
            return result[0], None, None, None
    return None, None, "network_error", None


def note_first_error(item, exc_type, err_kind):
    """Record the exception type of the first failed post. Do not touch attempts."""
    if item.get("last_error_type"):
        return
    if int(item.get("attempts") or 0) != 0:
        return
    item["last_error_type"] = exc_type or err_kind or "error"


def _apply_missing_cred(batch, dest, routes, now):
    """Hold, mute, or move to a configured fallback. Never increments attempts."""
    logs = []
    action = missing_cred_action(routes, dest)
    fallback = missing_cred_fallback_dest(routes, dest) if action == "fallback" else None
    untouched = all(not item.get("missing_cred_from") for item in batch)
    if action == "mute":
        for item in batch:
            item["status"] = "dead_letter"
            item["dead_reason"] = "muted"
            item["mute_reason"] = "no_cred"
            item["muted"] = True
            item["next_retry_at"] = None
            item["updated_at"] = iso(now)
        logs.append(f"mute dest={dest} reason=no_cred dropped={len(batch)}")
        return logs
    if action == "fallback" and fallback and untouched:
        for item in batch:
            item["missing_cred_from"] = dest
            item["dest"] = fallback
            payload = item.get("payload")
            if isinstance(payload, dict):
                payload["dest"] = fallback
                payload["dest_reason"] = "missing_cred_fallback"
            item["dest_reason"] = "missing_cred_fallback"
            item["updated_at"] = iso(now)
        logs.append(f"fallback dest={dest} to={fallback} reason=no_cred n={len(batch)}")
        return logs
    delay = iso(now + timedelta(seconds=HOLD_NO_CRED_SEC))
    for item in batch:
        item["held"] = True
        item["hold_reason"] = "no_cred"
        item["status"] = "pending"
        item["next_retry_at"] = delay
        item["updated_at"] = iso(now)
    logs.append(f"hold dest={dest} reason=no_cred retained={len(batch)}")
    return logs


def delivery_mode(routes, route=None):
    """Per-route delivery overrides global. Legacy paused:true -> hold."""
    routes = routes or {}
    r = route or {}
    d = r.get("delivery")
    if d in VALID_MODES:
        return d
    if r.get("paused") is True:
        return "hold"
    d = routes.get("delivery")
    if d in VALID_MODES:
        return d
    if routes.get("paused") is True:
        return "hold"
    return "send"


def is_exception(change):
    return (change or {}).get("kind") in EXCEPTION_KINDS


def enqueue_mode(change, routes, source_route):
    """Exceptions ignore ordinary mute; hold still follows dest/global hold."""
    if is_exception(change):
        mode = delivery_mode(routes, source_route)
        return "send" if mode == "mute" else mode
    return delivery_mode(routes, source_route)


def event_id_for(change):
    c = change or {}
    kind = c.get("kind") or "unknown"
    if c.get("event_id"):
        return str(c["event_id"])
    if kind == "comment" and c.get("comment_id"):
        return f"comment:{c['comment_id']}"
    if kind == "run_failed":
        dry = c.get("dry_run_event")
        rid = c.get("run_id") or "none"
        if dry:
            return f"run_failed_dry:{c.get('issue_id')}:{rid}:{dry}:{c.get('completed_at') or ''}"
        return f"run_failed:{rid}"
    if kind == "parse_failed":
        stage = c.get("stage") or "unknown"
        wid = c.get("workspace_id") or c.get("workspace_slug") or "unknown"
        bucket = c.get("bucket") or ""
        return f"parse_failed:{wid}:{stage}:{bucket}"
    if kind == "net_alert":
        wid = c.get("workspace_id") or c.get("workspace_slug") or "unknown"
        sub = c.get("subtype") or "degraded"
        return f"net_alert:{wid}:{sub}:{c.get('streak_started') or c.get('at') or ''}"
    if kind == "status":
        return f"status:{c.get('issue_id')}:{c.get('new_status')}:{c.get('updated_at') or ''}"
    return f"{kind}:{c.get('issue_id')}:{c.get('updated_at') or c.get('created_at') or ''}"


def hour_bucket(now):
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def backoff_seconds(attempt, rng=None):
    """attempt is the attempt that just failed (1..MAX). Cap ~1h ±20% jitter."""
    n = max(1, int(attempt))
    base = min(300 * (2 ** (n - 1)), MAX_BACKOFF_SEC)
    jitter = (rng or random).uniform(0.8, 1.2)
    return base * jitter


def parse_retry_after(value, now):
    if value is None or value == "":
        return None
    try:
        sec = int(str(value).strip())
        return min(max(sec, 0), MAX_BACKOFF_SEC)
    except (TypeError, ValueError):
        pass
    try:
        dt = parsedate_to_datetime(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        sec = (dt.astimezone(timezone.utc) - now).total_seconds()
        return min(max(int(sec), 0), MAX_BACKOFF_SEC)
    except Exception:
        return None


def format_log(alias, event_id, http_status, attempts, action):
    """Delivery logs: alias, event_id, HTTP status, attempts only."""
    st = "none" if http_status is None else str(http_status)
    msg = f"{action} dest={alias} event_id={event_id} HTTP {st} attempt={attempts}"
    if _SECRETISH.search(msg):
        msg = f"{action} dest={alias} event_id=<redacted> HTTP {st} attempt={attempts}"
    return msg


def empty_outbox():
    c = empty_circuit()
    return {
        "_v": 2,
        "circuit": c,
        "circuits": {"steward": c},
        "muted": {"count": 0, "last_at": None, "by_dest": {}},
        "items": {},
    }


def circuit_of(box, dest):
    dest = dest or STEWARD_DEST
    box.setdefault("circuits", {})
    if dest not in box["circuits"] or not isinstance(box["circuits"].get(dest), dict):
        box["circuits"][dest] = empty_circuit()
    if dest == STEWARD_DEST:
        box["circuit"] = box["circuits"][dest]
    return box["circuits"][dest]


def item_dest(item):
    return (item or {}).get("dest") or STEWARD_DEST


def load_outbox(path):
    if not os.path.exists(path):
        return empty_outbox()
    with open(path) as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        return empty_outbox()
    box = empty_outbox()
    ver = int(raw.get("_v") or 1)
    box["_v"] = 2
    items = raw.get("items") or {}
    if isinstance(items, dict):
        for eid, item in items.items():
            if not isinstance(item, dict):
                continue
            if not item.get("dest"):
                item = dict(item)
                item["dest"] = STEWARD_DEST
            box["items"][eid] = item
    muted = raw.get("muted")
    if isinstance(muted, dict):
        box["muted"]["count"] = muted.get("count") or 0
        box["muted"]["last_at"] = muted.get("last_at")
        by = muted.get("by_dest")
        if isinstance(by, dict):
            box["muted"]["by_dest"] = by
    circuits = raw.get("circuits")
    if isinstance(circuits, dict) and circuits:
        for k, v in circuits.items():
            if isinstance(v, dict):
                c = empty_circuit()
                c.update({kk: v.get(kk) for kk in c})
                box["circuits"][k] = c
        box["circuit"] = box["circuits"].setdefault(STEWARD_DEST, empty_circuit())
    elif isinstance(raw.get("circuit"), dict):
        c = empty_circuit()
        c.update({k: raw["circuit"].get(k) for k in c})
        box["circuits"] = {STEWARD_DEST: c}
        box["circuit"] = c
    if ver < 2:
        box["_migrated_from"] = ver
    return box


def save_outbox(path, box):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        to_store = dict(box)
        to_store["_v"] = 2
        data = json.dumps(to_store, ensure_ascii=False, separators=(",", ":")).encode()
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def persist_two_phase(outbox_path, box, state_path, state, crash=None):
    """Write outbox first, then scan cursor. Crash after outbox keeps events."""
    save_outbox(outbox_path, box)
    if crash == "after_outbox":
        raise InterruptedError("crash after outbox")
    _save_state(state_path, state)


def _save_state(path, obj):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        data = json.dumps(obj, ensure_ascii=False).encode()
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _ttl_for(change):
    return TTL_EXCEPTION if is_exception(change) else TTL_NORMAL


def _new_item(change, now, event_id, dest, dest_reason):
    payload = dict(change)
    payload["event_id"] = event_id
    payload["dest"] = dest
    payload["dest_reason"] = dest_reason
    payload["notify_only"] = True
    exp = now + _ttl_for(change)
    return {
        "event_id": event_id,
        "kind": change.get("kind"),
        "status": "pending",
        "dest": dest,
        "dest_reason": dest_reason,
        "payload": payload,
        "attempts": 0,
        "next_retry_at": iso(now),
        "last_http": None,
        "created_at": iso(now),
        "updated_at": iso(now),
        "accepted_at": None,
        "expires_at": iso(exp),
        "issue_id": change.get("issue_id"),
        "identifier": change.get("identifier"),
        "workspace_slug": change.get("workspace_slug"),
        "project_id": change.get("project_id"),
        "change_count": 1,
        "first_at": change.get("updated_at") or change.get("created_at") or iso(now),
        "held": False,
        "dead_reason": None,
    }


def _pending_status(box, issue_id, dest):
    found = None
    for item in box["items"].values():
        if item.get("status") != "pending":
            continue
        if item.get("kind") != "status":
            continue
        if item.get("issue_id") != issue_id:
            continue
        if item_dest(item) != dest:
            continue
        if found is None or (item.get("created_at") or "") < (found.get("created_at") or ""):
            found = item
    return found


def enqueue(box, change, now, mode="send", dest=None, dest_reason=None, routes=None):
    """Insert or merge. Returns added|duplicate|merged|muted."""
    if dest is None:
        dest, dest_reason = resolve_dest(change, routes)
    dest_reason = dest_reason or "route"
    circuit_of(box, dest)
    if mode == "mute":
        box["muted"]["count"] = int(box["muted"].get("count") or 0) + 1
        box["muted"]["last_at"] = iso(now)
        by = box["muted"].setdefault("by_dest", {})
        by[dest] = int(by.get(dest) or 0) + 1
        return "muted"
    eid = event_id_for(change)
    existing = box["items"].get(eid)
    if existing is not None:
        if (existing.get("status") == "pending"
                and (change or {}).get("kind") == "parse_failed"
                and existing.get("kind") == "parse_failed"):
            existing["payload"] = dict(change)
            existing["payload"]["event_id"] = existing["event_id"]
            existing["payload"]["dest"] = dest
            existing["payload"]["merged"] = True
            existing["payload"]["change_count"] = int(existing.get("change_count") or 1) + 1
            existing["change_count"] = int(existing.get("change_count") or 1) + 1
            existing["updated_at"] = iso(now)
            return "merged"
        return "duplicate"
    if (change or {}).get("kind") == "status" and change.get("issue_id"):
        prev = _pending_status(box, change["issue_id"], dest)
        if prev is not None:
            prev["payload"] = dict(change)
            prev["payload"]["event_id"] = prev["event_id"]
            prev["payload"]["dest"] = dest
            prev["payload"]["dest_reason"] = dest_reason
            prev["payload"]["merged"] = True
            prev["payload"]["change_count"] = int(prev.get("change_count") or 1) + 1
            prev["payload"]["old_status"] = prev["payload"].get("old_status") or change.get("old_status")
            prev["payload"]["new_status"] = change.get("new_status")
            prev["payload"]["updated_at"] = change.get("updated_at")
            prev["payload"]["first_at"] = prev.get("first_at")
            prev["payload"]["notify_only"] = True
            prev["change_count"] = int(prev.get("change_count") or 1) + 1
            prev["identifier"] = change.get("identifier") or prev.get("identifier")
            prev["updated_at"] = iso(now)
            prev["held"] = False
            return "merged"
    box["items"][eid] = _new_item(change, now, eid, dest, dest_reason)
    return "added"


def expire_pending(box, now):
    """Pending past TTL -> dead_letter. Returns count expired."""
    n = 0
    for item in box["items"].values():
        if item.get("status") != "pending":
            continue
        exp = parse_ts(item.get("expires_at"))
        if exp and now >= exp:
            item["status"] = "dead_letter"
            item["dead_reason"] = "expired"
            item["updated_at"] = iso(now)
            n += 1
    return n


def prune(box, now):
    keep = {}
    for k, item in box["items"].items():
        st = item.get("status")
        upd = parse_ts(item.get("updated_at")) or parse_ts(item.get("created_at"))
        if st == "accepted":
            acc = parse_ts(item.get("accepted_at")) or upd
            if acc and now - acc > PRUNE_ACCEPTED:
                continue
        elif st == "dead_letter":
            if upd and now - upd > PRUNE_DEAD:
                continue
        keep[k] = item
    box["items"] = keep


def circuit_is_open(box, now, dest=STEWARD_DEST):
    c = circuit_of(box, dest)
    return c.get("state") == "open"


def circuit_probe_due(box, now, dest=STEWARD_DEST):
    c = circuit_of(box, dest)
    if c.get("state") != "open":
        return False
    nxt = parse_ts(c.get("next_probe_at"))
    return nxt is None or now >= nxt


def open_circuit(box, now, http_status, dest=STEWARD_DEST):
    c = circuit_of(box, dest)
    c["state"] = "open"
    c["opened_at"] = c.get("opened_at") or iso(now)
    c["last_http"] = http_status
    c["consecutive_client_errors"] = int(c.get("consecutive_client_errors") or 0) + 1
    c["next_probe_at"] = iso(now + timedelta(seconds=PROBE_INTERVAL_SEC))


def close_circuit(box, now, dest=STEWARD_DEST):
    box["circuits"][dest] = empty_circuit()
    if dest == STEWARD_DEST:
        box["circuit"] = box["circuits"][dest]


def _item_mode(item, routes, route_for):
    payload = item.get("payload") or item
    if is_exception(payload):
        dest_spec = endpoints(routes).get(item_dest(item)) or {}
        return enqueue_mode(payload, routes, dest_spec)
    route = route_for(routes, item.get("workspace_slug"), item.get("project_id")) if route_for else {}
    return delivery_mode(routes, route)


def due_items(box, *, routes, now, route_for, limit=MAX_PER_TICK, dest=None):
    """Pending items ready to send. Hold is skipped; mute pending is dropped."""
    ready = []
    dropped_mute = 0
    held = 0
    for item in box["items"].values():
        if item.get("status") != "pending":
            continue
        if dest is not None and item_dest(item) != dest:
            continue
        mode = _item_mode(item, routes, route_for)
        if mode == "mute":
            item["status"] = "dead_letter"
            item["dead_reason"] = "muted"
            item["updated_at"] = iso(now)
            box["muted"]["count"] = int(box["muted"].get("count") or 0) + 1
            box["muted"]["last_at"] = iso(now)
            by = box["muted"].setdefault("by_dest", {})
            d = item_dest(item)
            by[d] = int(by.get(d) or 0) + 1
            dropped_mute += 1
            continue
        if mode == "hold":
            item["held"] = True
            item["updated_at"] = iso(now)
            held += 1
            continue
        nxt = parse_ts(item.get("next_retry_at"))
        if nxt and now < nxt:
            continue
        ready.append(item)
    ready.sort(key=lambda x: (x.get("created_at") or "", x.get("event_id") or ""))
    resume = [i for i in ready if i.get("held")]
    fresh = [i for i in ready if not i.get("held")]
    ordered = resume + fresh
    return ordered[:limit], held, dropped_mute


def mark_accepted(item, now, http_status, attempts=None):
    item["status"] = "accepted"
    item["accepted_at"] = iso(now)
    item["updated_at"] = iso(now)
    item["last_http"] = http_status
    item["held"] = False
    if attempts is not None:
        item["attempts"] = attempts


def mark_dead(item, now, reason, http_status=None):
    item["status"] = "dead_letter"
    item["dead_reason"] = reason
    item["updated_at"] = iso(now)
    if http_status is not None:
        item["last_http"] = http_status
    item["next_retry_at"] = None


def apply_retryable_failure(item, now, http_status, retry_after_sec, rng=None):
    n = int(item.get("attempts") or 0) + 1
    item["attempts"] = n
    item["last_http"] = http_status
    item["updated_at"] = iso(now)
    if n >= MAX_ATTEMPTS:
        mark_dead(item, now, "max_attempts", http_status)
        return "dead_letter"
    if retry_after_sec is not None:
        delay = min(int(retry_after_sec), MAX_BACKOFF_SEC)
    else:
        delay = backoff_seconds(n, rng)
    item["next_retry_at"] = iso(now + timedelta(seconds=delay))
    item["status"] = "pending"
    return "retry"


def replay(box, event_ids, now, reset_circuit=False, dest=None):
    """Requeue dead_letter (or specified) items as pending. Returns count."""
    ids = list(event_ids) if event_ids is not None else None
    n = 0
    seen_dests = set()
    for eid, item in box["items"].items():
        if ids is not None and eid not in ids:
            continue
        if ids is None and item.get("status") != "dead_letter":
            continue
        if dest is not None and item_dest(item) != dest:
            continue
        if item.get("muted") or str(item.get("dead_reason") or "").startswith("muted"):
            continue
        if item.get("status") not in ("dead_letter", "pending"):
            continue
        item["status"] = "pending"
        item["attempts"] = 0
        item["next_retry_at"] = iso(now)
        item["dead_reason"] = None
        item["updated_at"] = iso(now)
        item["expires_at"] = iso(now + (_ttl_for(item.get("payload") or item)))
        seen_dests.add(item_dest(item))
        n += 1
    if reset_circuit:
        if dest:
            close_circuit(box, now, dest)
        elif seen_dests:
            for d in seen_dests:
                close_circuit(box, now, d)
        else:
            close_circuit(box, now, STEWARD_DEST)
    return n


def mute_network_misfiled(box, now, ids=None, reason=None):
    """Mark the 15:55 network-as-parse items muted. Do not replay. Returns count."""
    ids = NETWORK_MISFILED_PARSE_IDS if ids is None else set(ids)
    reason = reason or MUTE_NETWORK_REASON
    n = 0
    for eid in ids:
        item = box["items"].get(eid)
        if not item:
            continue
        if item.get("dead_reason") == "muted_network" and item.get("muted"):
            continue
        item["status"] = "dead_letter"
        item["dead_reason"] = "muted_network"
        item["mute_reason"] = reason
        item["muted"] = True
        item["next_retry_at"] = None
        item["updated_at"] = iso(now)
        box["muted"]["count"] = int(box["muted"].get("count") or 0) + 1
        box["muted"]["last_at"] = iso(now)
        n += 1
    return n


def mute_items(box, ids, now, reason, tag="muted_replay"):
    """Mark items muted so replay/requeue skip them. Returns count newly muted.

    Pending rows become dead_letter (never sent); delivered rows keep status=accepted
    and last_http so the record of what reached the webhook stays true.
    """
    n = 0
    for eid in ids:
        item = box["items"].get(eid)
        if not item or item.get("muted"):
            continue
        if item.get("status") == "pending":
            item["status"] = "dead_letter"
            item["dead_reason"] = tag
            item["next_retry_at"] = None
        item["muted"] = True
        item["mute_tag"] = tag
        item["mute_reason"] = reason
        item["updated_at"] = iso(now)
        box["muted"]["count"] = int(box["muted"].get("count") or 0) + 1
        box["muted"]["last_at"] = iso(now)
        by = box["muted"].setdefault("by_dest", {})
        d = item_dest(item)
        by[d] = int(by.get(d) or 0) + 1
        n += 1
    return n


def requeue_shadow(box, now, since=None):
    """Pending-ize accepted rows that were shadow-acked without HTTP 200.

    Skips last_http==200 (already delivered). Returns count requeued.
    """
    n = 0
    since_dt = parse_ts(since) if since else None
    for item in box["items"].values():
        if item.get("status") != "accepted":
            continue
        if item.get("last_http") != "shadow":
            continue
        if item.get("muted") or str(item.get("dead_reason") or "").startswith("muted"):
            continue
        if since_dt is not None:
            ts = parse_ts(item.get("accepted_at") or item.get("created_at"))
            if ts is not None and ts < since_dt:
                continue
        item["status"] = "pending"
        item["attempts"] = 0
        item["next_retry_at"] = iso(now)
        item["accepted_at"] = None
        item["held"] = False
        item["dead_reason"] = None
        item["updated_at"] = iso(now)
        n += 1
    return n


def _drain_one_dest(box, dest, *, routes, now, poster, route_for, rng, fanout,
                    limit, on_progress, circuit_dest=None):
    logs = []
    cdest = circuit_dest or dest
    circuit_of(box, cdest)

    open_now = circuit_is_open(box, now, cdest)
    probe = circuit_probe_due(box, now, cdest)
    alert = circuit_threshold_alert(box, now, cdest, routes)
    if alert:
        logs.append(alert)
    if open_now and not probe:
        logs.append(f"circuit open dest={cdest} skip_until={circuit_of(box, cdest).get('next_probe_at')}")
        return logs

    due, held, muted = due_items(
        box, routes=routes, now=now, route_for=route_for, limit=limit, dest=dest,
    )
    if held:
        logs.append(f"hold dest={dest} retained={held}")
    if muted:
        logs.append(f"mute dest={dest} dropped={muted} count={box['muted'].get('count')}")
    if not due:
        return logs

    if open_now and probe:
        due = due[:1]
        logs.append(f"circuit probe dest={cdest}")

    batches = deque()
    batches.append(due)
    while batches:
        batch = batches.popleft()
        payloads = [it["payload"] for it in batch]
        try:
            status, retry_after, err_kind, exc_type = unpack_poster(poster(payloads))
        except Exception as exc:
            status, retry_after, err_kind, exc_type = None, None, "network_error", type(exc).__name__
        ra_sec = parse_retry_after(retry_after, now) if retry_after is not None else None

        if status == 200:
            for it in batch:
                n = int(it.get("attempts") or 0) + 1
                mark_accepted(it, now, 200, attempts=n)
                intended = item_dest(it)
                it["relayed_via"] = cdest if fanout == "shadow" else dest
                if fanout == "shadow":
                    logs.append(format_log(intended, it["event_id"], "shadow", 0, "shadow"))
                    logs.append(format_log(cdest, it["event_id"], 200, n, "accepted"))
                else:
                    logs.append(format_log(dest, it["event_id"], 200, n, "accepted"))
            close_circuit(box, now, cdest)
            if on_progress:
                on_progress(box)
            continue

        if status == 413 and len(batch) > 1:
            mid = max(1, len(batch) // 2)
            batches.appendleft(batch[mid:])
            batches.appendleft(batch[:mid])
            logs.append(f"split dest={dest} n={len(batch)} HTTP 413")
            continue

        if status in CLIENT_ERROR:
            open_circuit(box, now, status, cdest)
            for it in batch:
                it["last_http"] = status
                it["updated_at"] = iso(now)
                if int(it.get("attempts") or 0) == 0:
                    note_first_error(it, exc_type or "HTTPError", err_kind or f"HTTP {status}")
                    logs.append(
                        f"first_failure dest={cdest} event_id={it.get('event_id')} "
                        f"error_type={it.get('last_error_type')}"
                    )
                logs.append(format_log(cdest, it["event_id"], status, int(it.get("attempts") or 0), "circuit"))
            logs.append(
                f"circuit open dest={cdest} HTTP {status} probe_at={circuit_of(box, cdest).get('next_probe_at')}"
            )
            if on_progress:
                on_progress(box)
            break

        if err_kind == "no_cred":
            logs.extend(_apply_missing_cred(batch, dest, routes, now))
            if on_progress:
                on_progress(box)
            break

        reason = err_kind or (f"HTTP {status}" if status is not None else "error")
        if status == 413:
            reason = "HTTP 413"
        for it in batch:
            if int(it.get("attempts") or 0) == 0:
                note_first_error(it, exc_type, err_kind or reason)
                logs.append(
                    f"first_failure dest={cdest} event_id={it.get('event_id')} "
                    f"error_type={it.get('last_error_type')}"
                )
            outcome = apply_retryable_failure(it, now, status, ra_sec, rng)
            logs.append(format_log(
                cdest, it["event_id"], status,
                int(it.get("attempts") or 0),
                "dead_letter" if outcome == "dead_letter" else "retry",
            ))
        if status == 429 and ra_sec is not None:
            logs.append(f"retry_after dest={cdest} sec={ra_sec}")
        if on_progress:
            on_progress(box)
    return logs


def drain(box, *, routes, now, poster, route_for, rng=None, alias=None, limit=MAX_PER_TICK,
          on_progress=None, poster_for=None):
    """Send due items. poster(changes)->(http_status, retry_after, error_kind[, exc_type]).

    Independent per dest in send mode: one dest's 400/circuit does not block another.
    fanout=shadow POSTs only to steward (relay), logs intended dest, uses steward circuit.
    poster_for(dest) -> poster, if given; else the same poster for every dest.
    on_progress(box) after each batch so accepted rows survive a crash.
    A missing-cred fallback is drained in a later pass of this same call.
    """
    fanout = fanout_mode(routes)
    logs = []
    expire_pending(box, now)
    prune(box, now)

    dests = sorted({item_dest(it) for it in box["items"].values()} | set((box.get("circuits") or {}).keys()))
    if not dests:
        dests = [STEWARD_DEST]

    steward = steward_dest(routes)
    seen = set()
    pending_dests = list(dests)
    guard = 0
    while pending_dests and guard < 8:
        guard += 1
        current = pending_dests
        pending_dests = []
        for dest in current:
            if dest in seen:
                continue
            seen.add(dest)
            if fanout == "shadow":
                p = poster_for(steward) if poster_for is not None else poster
                cdest = steward
            else:
                p = poster_for(dest) if poster_for is not None else poster
                cdest = dest
            logs.extend(_drain_one_dest(
                box, dest, routes=routes, now=now, poster=p, route_for=route_for,
                rng=rng, fanout=fanout, limit=limit, on_progress=on_progress,
                circuit_dest=cdest,
            ))
        extra = []
        for item in box["items"].values():
            if item.get("status") != "pending":
                continue
            moved = item_dest(item)
            if moved not in seen:
                extra.append(moved)
        pending_dests = sorted(set(extra))
    return logs


def counts(box):
    c = {"pending": 0, "accepted": 0, "dead_letter": 0, "shadowed": 0}
    by = {}
    for it in box["items"].values():
        st = it.get("status")
        d = item_dest(it)
        by.setdefault(d, {"pending": 0, "accepted": 0, "dead_letter": 0, "shadowed": 0})
        if st in ("pending", "accepted", "dead_letter"):
            c[st] += 1
            by[d][st] += 1
        if st == "accepted" and it.get("last_http") == "shadow":
            c["shadowed"] += 1
            by[d]["shadowed"] += 1
    c["muted"] = int((box.get("muted") or {}).get("count") or 0)
    c["by_dest"] = by
    return c
