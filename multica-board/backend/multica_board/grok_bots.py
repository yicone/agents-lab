"""Read-only Grok Bot roster for GET /api/bots.

Opens only the five sand-client slices named by the task, plus the two
multica-bridge files. Transcript replicas, drafts, secrets, cookies,
credential files, and the Keychain are never opened.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

from .redact import redact

SLOT_KEY = "sand.client.slice.client-meta.account-slot"
ACCOUNT_PREFIX = "sand.client.slice.account."
SLICE_KINDS = {
    "roster.last-roster": "roster",
    "sidebar.last-sections": "sections",
    "ui-agent-refs": "refs",
    "weekly-usage.cache": "usage",
}
_B32_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz234567")
_CIRCUIT_STATE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_FORBIDDEN_KEY = ("transcript", "composer-draft", "secret", "credential", "cookie")
_TERMINAL_OUTBOX = frozenset({"accepted", "dead_letter", "shadow_record"})

Reader = Callable[[Path], str]


@dataclass(frozen=True)
class GrokPaths:
    persistence_dir: Path
    routes_path: Path
    outbox_path: Path


def default_paths() -> GrokPaths:
    home = Path.home()
    return GrokPaths(
        persistence_dir=home / "Library/Application Support/Grok Bot/sand-client-persistence",
        routes_path=home / ".config/multica-bridge/routes.json",
        outbox_path=home / ".local/state/multica-bridge/outbox.json",
    )


def decode_blob_name(name: str) -> str | None:
    """Base32 blob filename (no padding) back to the sand-client key. None if it is not one."""
    if not name.endswith(".blob"):
        return None
    stem = name[:-5]
    if not stem or any(ch not in _B32_ALPHABET for ch in stem):
        return None
    pad = "=" * ((8 - len(stem) % 8) % 8)
    try:
        raw = base64.b32decode(stem.upper() + pad, casefold=True)
    except Exception:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def slice_kind(key: str) -> tuple[str, str | None] | None:
    """Return (kind, auth_id) for an allow-listed key, else None.

    auth_id is None for the account-slot slice. Transcript and secret keys never match.
    """
    lowered = unquote(key).lower()
    if any(part in lowered for part in _FORBIDDEN_KEY):
        return None
    if key == SLOT_KEY:
        return ("slot", None)
    if not key.startswith(ACCOUNT_PREFIX):
        return None
    rest = key[len(ACCOUNT_PREFIX):]
    for suffix, kind in SLICE_KINDS.items():
        marker = "." + suffix
        if not rest.endswith(marker):
            continue
        auth = rest[: -len(marker)]
        if not auth or "." in auth or "/" in auth or "\\" in auth:
            return None
        return (kind, auth)
    return None


def is_allowed_slice(key: str) -> bool:
    return slice_kind(key) is not None


def _absent() -> dict[str, Any]:
    return {"ok": False, "reason": "absent", "items": []}


def _unreadable() -> dict[str, Any]:
    return {"ok": False, "reason": "unreadable", "items": []}


def _norm_auth(value: str) -> str:
    return unquote(value).strip()


def short_account_slot(value: str) -> str:
    """Last 8 characters of the account id. The provider prefix and full id stay out of the API."""
    text = _norm_auth(value)
    tail = text.replace("|", "/").rstrip("/").split("/")[-1]
    tail = tail.strip()
    if len(tail) <= 8:
        return tail
    return tail[-8:]


def _ms_iso(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 1_000_000_000_000 or value > 9_999_999_999_999:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = redact(value, limit=None)
    if not isinstance(text, str):
        return None
    text = text.strip()
    return text or None


def _read_json(path: Path, reader: Reader) -> Any:
    return json.loads(reader(path))


def _slice_value(payload: Any) -> Any:
    if isinstance(payload, dict) and "value" in payload:
        return payload.get("value")
    return payload


def _empty_outbox() -> dict[str, int]:
    return {"pending": 0, "dead_letter": 0, "shadow_record": 0}


def _circuit(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    state = raw.get("state")
    if not isinstance(state, str) or not _CIRCUIT_STATE.fullmatch(state):
        state = None
    errors = raw.get("consecutive_client_errors")
    if isinstance(errors, bool) or not isinstance(errors, int):
        errors = None
    if state is None and errors is None:
        return None
    return {"state": state, "consecutive_client_errors": errors}


def _delivery(value: Any) -> str | None:
    if isinstance(value, str) and value.strip().lower() in ("shadow", "send"):
        return value.strip().lower()
    return None


def _load_routes(path: Path, reader: Reader) -> tuple[str, dict[str, Any]]:
    if not path.is_file():
        return "absent", {}
    try:
        data = _read_json(path, reader)
    except (OSError, json.JSONDecodeError, UnicodeError, TypeError, ValueError):
        return "unreadable", {}
    if not isinstance(data, dict):
        return "unreadable", {}
    fanout = _delivery(data.get("fanout"))
    by_bot: dict[str, dict[str, Any]] = {}

    def slot(bot_id: str) -> dict[str, Any]:
        return by_bot.setdefault(
            bot_id,
            {"routine": None, "delivery": fanout, "workspaces": [], "dests": set()},
        )

    endpoints = data.get("endpoints") if isinstance(data.get("endpoints"), dict) else {}
    for dest, ep in endpoints.items():
        if not isinstance(ep, dict) or not isinstance(ep.get("bot_id"), str) or not ep["bot_id"]:
            continue
        row = slot(ep["bot_id"])
        if isinstance(dest, str) and dest:
            row["dests"].add(dest)
        routine = _text(ep.get("routine"))
        if routine:
            row["routine"] = routine
        mode = _delivery(ep.get("delivery") or ep.get("mode") or ep.get("fanout"))
        if mode:
            row["delivery"] = mode

    workspaces = data.get("workspaces") if isinstance(data.get("workspaces"), dict) else {}
    for slug, ws in workspaces.items():
        if not isinstance(ws, dict) or not isinstance(ws.get("bot_id"), str) or not ws["bot_id"]:
            continue
        row = slot(ws["bot_id"])
        ref = ws.get("endpoint_ref") if isinstance(ws.get("endpoint_ref"), str) else None
        if ref:
            row["dests"].add(ref)
        events = [e for e in ws.get("events") or [] if isinstance(e, str) and e]
        row["workspaces"].append(
            {
                "slug": slug if isinstance(slug, str) else None,
                "endpoint": ref,
                "events": events,
            }
        )
    return "ok", {"delivery": fanout, "by_bot": by_bot}


def _load_outbox(path: Path, reader: Reader) -> tuple[str, dict[str, Any]]:
    if not path.is_file():
        return "absent", {"by_dest": {}, "circuit": None, "circuits": {}, "totals": _empty_outbox()}
    try:
        data = _read_json(path, reader)
    except (OSError, json.JSONDecodeError, UnicodeError, TypeError, ValueError):
        return "unreadable", {"by_dest": {}, "circuit": None, "circuits": {}, "totals": _empty_outbox()}
    if not isinstance(data, dict):
        return "unreadable", {"by_dest": {}, "circuit": None, "circuits": {}, "totals": _empty_outbox()}
    items = data.get("items")
    rows: list[Any]
    if isinstance(items, dict):
        rows = list(items.values())
    elif isinstance(items, list):
        rows = items
    else:
        rows = []
    by_dest: dict[str, dict[str, int]] = {}
    totals = _empty_outbox()
    for item in rows:
        if not isinstance(item, dict):
            continue
        status = item.get("status") if isinstance(item.get("status"), str) else ""
        dest = item.get("dest") if isinstance(item.get("dest"), str) else ""
        bucket = by_dest.setdefault(dest, _empty_outbox())
        if status == "dead_letter":
            key = "dead_letter"
        elif status == "shadow_record":
            key = "shadow_record"
        elif status in _TERMINAL_OUTBOX:
            continue
        else:
            key = "pending"
        bucket[key] += 1
        totals[key] += 1
    circuits_raw = data.get("circuits") if isinstance(data.get("circuits"), dict) else {}
    circuits = {name: circ for name, raw in circuits_raw.items() if (circ := _circuit(raw)) is not None}
    return "ok", {
        "by_dest": by_dest,
        "circuit": _circuit(data.get("circuit")),
        "circuits": circuits,
        "totals": totals,
    }


def _sum_outbox(by_dest: dict[str, dict[str, int]], dests: set[str]) -> dict[str, int]:
    total = _empty_outbox()
    for dest in dests:
        row = by_dest.get(dest)
        if not row:
            continue
        for key in total:
            total[key] += row.get(key, 0)
    return total


def _roster_items(
    rows: list[Any],
    sections: list[dict[str, Any]],
    pinned: set[str],
    routes: dict[str, Any],
    outbox: dict[str, Any],
) -> list[dict[str, Any]]:
    section_of: dict[str, dict[str, Any]] = {}
    for section in sections:
        for aid in section.get("agent_ids") or []:
            if isinstance(aid, str) and aid not in section_of:
                section_of[aid] = section
    by_bot = routes.get("by_bot") or {}
    by_dest = outbox.get("by_dest") or {}
    circuits = outbox.get("circuits") or {}
    items = []
    for raw in rows:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
            continue
        bot_id = raw["id"]
        members = [m for m in raw.get("memberIds") or [] if isinstance(m, str) and m]
        section = section_of.get(bot_id)
        route = by_bot.get(bot_id) or {}
        dests = route.get("dests") or set()
        circuit = None
        for dest in dests:
            if dest in circuits:
                circuit = circuits[dest]
                break
        title = _text(raw.get("title"))
        items.append(
            {
                "id": bot_id,
                "name": _text(raw.get("name")),
                "title": title,
                "description": _text(raw.get("description")),
                "is_group": bool(raw.get("isGroup")),
                "member_ids": members,
                "harness": raw.get("harness") if isinstance(raw.get("harness"), str) else None,
                "origin": raw.get("origin") if isinstance(raw.get("origin"), str) else None,
                "hidden": bool(raw.get("isHiddenFromSidebar")),
                "pinned": bot_id in pinned,
                "section_id": section.get("id") if section else None,
                "section_name": section.get("name") if section else None,
                "created_at": _ms_iso(raw.get("createdAt")),
                "updated_at": _ms_iso(raw.get("updatedAt")),
                "bridge": {
                    "routine": route.get("routine"),
                    "delivery": route.get("delivery"),
                    "workspaces": list(route.get("workspaces") or []),
                    "outbox": _sum_outbox(by_dest, dests) if dests else _empty_outbox(),
                    "circuit": circuit,
                },
            }
        )
    return items


def _sections(value: Any) -> list[dict[str, Any]]:
    raw_sections = value.get("sections") if isinstance(value, dict) else None
    if not isinstance(raw_sections, list):
        return []
    out = []
    for section in raw_sections:
        if not isinstance(section, dict):
            continue
        out.append(
            {
                "id": section.get("id") if isinstance(section.get("id"), str) else None,
                "name": _text(section.get("name")),
                "agent_ids": [a for a in section.get("agentIds") or [] if isinstance(a, str)],
            }
        )
    return out


def _weekly_usage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    reading = value.get("reading") if isinstance(value.get("reading"), dict) else {}
    usage = reading.get("usage") if isinstance(reading.get("usage"), dict) else {}
    demand = usage.get("onDemand") if isinstance(usage.get("onDemand"), dict) else {}

    def num(src: dict[str, Any], key: str) -> int | float | None:
        got = src.get(key)
        if isinstance(got, bool) or not isinstance(got, (int, float)):
            return None
        return got

    return {
        "kind": value.get("kind") if isinstance(value.get("kind"), str) else None,
        "plan": _text(usage.get("grokPlanLabel")),
        "percent_used": num(usage, "percentUsed"),
        "next_reset_ms": num(usage, "nextResetMs"),
        "read_at_ms": num(reading, "readAtMs"),
        "expires_at_ms": num(value, "expiresAtMs"),
        "is_trial": usage.get("isSandTrial") if isinstance(usage.get("isSandTrial"), bool) else None,
        "is_team_seat": usage.get("isTeamSeat") if isinstance(usage.get("isTeamSeat"), bool) else None,
        "on_demand": {"used_cents": num(demand, "usedCents"), "limit_cents": num(demand, "limitCents")},
    }


def _allowed_files(directory: Path) -> list[tuple[str, str, str | None, Path]]:
    found = []
    try:
        names = list(directory.iterdir())
    except OSError:
        return found
    for path in names:
        try:
            if path.is_symlink() or not path.is_file():
                continue
        except OSError:
            continue
        key = decode_blob_name(path.name)
        if key is None:
            continue
        kind = slice_kind(key)
        if kind is None:
            continue
        found.append((key, kind[0], kind[1], path))
    return found


def load_bots(paths: GrokPaths | None = None, *, reader: Reader | None = None) -> dict[str, Any]:
    """Roster, groups, pins, weekly quota, and bridge routing. Never raises."""
    paths = paths or default_paths()
    read = reader or (lambda p: p.read_text(encoding="utf-8"))
    directory = paths.persistence_dir
    try:
        exists = directory.is_dir()
    except OSError:
        return _unreadable()
    if not exists:
        return _absent()

    files = _allowed_files(directory)
    slot_path = next((path for _key, kind, _auth, path in files if kind == "slot"), None)
    if slot_path is None:
        return _absent()
    try:
        slot_payload = _slice_value(_read_json(slot_path, read))
    except (OSError, json.JSONDecodeError, UnicodeError, TypeError, ValueError):
        return _unreadable()
    if not isinstance(slot_payload, str) or not slot_payload.strip():
        return _absent()
    auth = _norm_auth(slot_payload)
    selected = {
        kind: path
        for _key, kind, file_auth, path in files
        if kind != "slot" and file_auth is not None and _norm_auth(file_auth) == auth
    }
    if "roster" not in selected:
        return _absent()

    def load_kind(kind: str) -> Any:
        path = selected.get(kind)
        if path is None:
            return None
        return _slice_value(_read_json(path, read))

    try:
        roster = load_kind("roster")
        sections_raw = load_kind("sections")
        refs = load_kind("refs")
        usage = load_kind("usage")
    except (OSError, json.JSONDecodeError, UnicodeError, TypeError, ValueError):
        return _unreadable()

    rows = roster.get("rows") if isinstance(roster, dict) else None
    if not isinstance(rows, list):
        return _unreadable()
    sections = _sections(sections_raw)
    pinned_raw = refs.get("pinnedAgentIds") if isinstance(refs, dict) else []
    pinned = {a for a in pinned_raw or [] if isinstance(a, str)}

    try:
        routes_state, routes = _load_routes(paths.routes_path, read)
        outbox_state, outbox = _load_outbox(paths.outbox_path, read)
    except Exception:
        return _unreadable()

    return {
        "ok": True,
        "reason": None,
        "account_slot": short_account_slot(slot_payload),
        "weekly_usage": _weekly_usage(usage),
        "sections": sections,
        "delivery": routes.get("delivery"),
        "circuit": outbox.get("circuit"),
        "outbox": outbox.get("totals") or _empty_outbox(),
        "sources": {"roster": "ok", "routes": routes_state, "outbox": outbox_state},
        "items": _roster_items(rows, sections, pinned, routes, outbox),
    }
