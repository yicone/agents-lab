"""Classify collection errors and track per-workspace network streaks.

Network failures are logged only. After STREAK consecutive ticks (~30 min at
5-minute launchd), one aggregated alert goes to steward; recovery sends one
notice. No repeat while the streak is open (cooldown = until recover).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

STREAK = 6  # >30 min at 300s interval when consecutive ticks fail

NETWORK_RE = re.compile(
    r"(could not reach the multica server"
    r"|tls handshake timed out"
    r"|handshake never completed"
    r"|connection timed out"
    r"|timed out"
    r"|timeout"
    r"|network is unreachable"
    r"|connection reset"
    r"|connection refused"
    r"|temporary failure in name resolution"
    r"|nodename nor servname"
    r"|ssl error|\bssl:"
    r"|broken pipe"
    r"|check your network)",
    re.I,
)
PARSE_RE = re.compile(
    r"(jsondecode|not valid json|expecting value|unterminated string"
    r"|json\.decoder|no json object|extra data|json parse"
    r"|missing (required )?field|keyerror|typeerror)",
    re.I,
)


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify(err):
    """Return 'network' or 'parse'."""
    if isinstance(err, json.JSONDecodeError):
        return "parse"
    text = str(err or "")
    if PARSE_RE.search(text):
        return "parse"
    if NETWORK_RE.search(text):
        return "network"
    if type(err).__name__ in ("JSONDecodeError",):
        return "parse"
    return "parse"


def empty_state():
    return {"_v": 1, "workspaces": {}}


def load(path):
    if not path or not os.path.exists(path):
        return empty_state()
    try:
        with open(path) as f:
            raw = json.load(f)
    except Exception:
        return empty_state()
    if not isinstance(raw, dict):
        return empty_state()
    ws = raw.get("workspaces")
    if not isinstance(ws, dict):
        ws = {}
    return {"_v": 1, "workspaces": ws}


def save(path, state):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        data = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode()
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _ent(state, wid):
    ws = state.setdefault("workspaces", {})
    if wid not in ws or not isinstance(ws.get(wid), dict):
        ws[wid] = {
            "consecutive": 0, "first_at": None, "last_at": None,
            "last_error": None, "alerted_at": None, "name": None, "slug": None,
        }
    return ws[wid]


def observe(state, *, ok_ids, netfail, now):
    """Update streaks. netfail: {wid: {name, slug, error}}.

    Returns list of change dicts (kind=net_alert, subtype=degraded|recovered).
    Alert once per streak when consecutive >= STREAK; recovered once after that.
    """
    events = []
    now_s = iso(now)
    ok_ids = set(ok_ids or [])
    netfail = netfail or {}

    for wid, info in netfail.items():
        if not wid:
            continue
        ent = _ent(state, wid)
        n = int(ent.get("consecutive") or 0) + 1
        ent["consecutive"] = n
        ent["last_at"] = now_s
        ent["last_error"] = (info.get("error") or "")[:300]
        ent["name"] = info.get("name") or ent.get("name")
        ent["slug"] = info.get("slug") or ent.get("slug")
        if not ent.get("first_at"):
            ent["first_at"] = now_s
        if n >= STREAK and not ent.get("alerted_at"):
            ent["alerted_at"] = now_s
            events.append({
                "kind": "net_alert",
                "subtype": "degraded",
                "workspace": ent.get("name"),
                "workspace_id": wid,
                "workspace_slug": ent.get("slug"),
                "consecutive": n,
                "streak_started": ent.get("first_at"),
                "at": now_s,
                "error": ent.get("last_error"),
                "notify_only": True,
            })

    for wid in ok_ids:
        ent = (state.get("workspaces") or {}).get(wid)
        if not ent:
            continue
        if ent.get("alerted_at"):
            events.append({
                "kind": "net_alert",
                "subtype": "recovered",
                "workspace": ent.get("name"),
                "workspace_id": wid,
                "workspace_slug": ent.get("slug"),
                "consecutive": int(ent.get("consecutive") or 0),
                "streak_started": ent.get("first_at"),
                "at": now_s,
                "error": None,
                "notify_only": True,
            })
        state["workspaces"].pop(wid, None)

    return events
