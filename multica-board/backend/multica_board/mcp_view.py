"""Read model for `GET /api/mcp` (contract v0.9). Pure cache reads, whitelisted fields only."""

from __future__ import annotations

import json
from typing import Any

from .mcp_support import SUPPORT, runtime_loads
from .redact import redact
from .store import Store

MCP_SCHEMA = 1
ATTENTION_BUCKETS = ("ignored", "shadowed", "may_fail", "hidden", "unused", "disabled_only")
BADGE_BUCKETS = ("ignored", "shadowed", "may_fail")


def _flag(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"set": bool(value.get("set")), "count": int(value.get("count") or 0)}
    return {"set": False, "count": 0}


def _own(raw: str | None) -> dict[str, Any]:
    """Parse the stored own-config summary; re-whitelist to contract fields only."""
    if not raw:
        return {"state": "none", "servers": []}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"state": "invalid", "servers": []}
    if not isinstance(data, dict) or data.get("state") not in ("none", "managed", "hidden", "invalid"):
        return {"state": "invalid", "servers": []}
    servers = []
    if data["state"] == "managed" and isinstance(data.get("servers"), list):
        for s in data["servers"]:
            if isinstance(s, dict):
                servers.append(
                    {
                        "name": s.get("name"),
                        "transport": s.get("transport"),
                        "env": _flag(s.get("env")),
                        "headers": _flag(s.get("headers")),
                    }
                )
    return {"state": data["state"], "servers": servers}


def _sort_key(name: Any, rid: Any) -> tuple[str, str]:
    return ((name or "").casefold(), str(rid or ""))


def build_mcp(store: Store, server: str, generated_at: str | None, mcp_every: int) -> dict[str, Any]:
    ws_rows = store.query(
        "SELECT id, slug, name FROM workspaces WHERE server=? ORDER BY name", (server,)
    )
    sync: dict[tuple[str, str], dict[str, Any]] = {}
    for r in store.query(
        "SELECT * FROM sync_status WHERE server=? AND resource IN ('mcp_library','mcp_bindings')",
        (server,),
    ):
        sync[(r["workspace_id"], r["resource"])] = r
    states = {
        r["workspace_id"]: r
        for r in store.query("SELECT * FROM mcp_state WHERE server=?", (server,))
    }
    servers_by_ws: dict[str, list[dict[str, Any]]] = {}
    for r in store.query("SELECT * FROM mcp_servers WHERE server=?", (server,)):
        servers_by_ws.setdefault(r["workspace_id"], []).append(r)
    bindings_by_agent: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in store.query("SELECT * FROM mcp_bindings WHERE server=?", (server,)):
        bindings_by_agent.setdefault((r["workspace_id"], r["agent_id"]), []).append(r)
    agent_states = {
        (r["workspace_id"], r["agent_id"]): r["state"]
        for r in store.query("SELECT * FROM mcp_agent_state WHERE server=?", (server,))
    }
    agents_by_ws: dict[str, list[dict[str, Any]]] = {}
    for r in store.query("SELECT * FROM agents WHERE server=?", (server,)):
        agents_by_ws.setdefault(r["workspace_id"], []).append(r)
    providers = {
        (r["workspace_id"], r["id"]): (r.get("provider") or None)
        for r in store.query("SELECT workspace_id, id, provider FROM runtimes WHERE server=?", (server,))
    }

    workspaces: list[dict[str, Any]] = []
    ws_bindings_state: dict[str, str] = {}
    ws_library_state: dict[str, str] = {}
    for w in ws_rows:
        ws = w["id"]
        lib = sync.get((ws, "mcp_library")) or {}
        lib_success = lib.get("last_success_at")
        lib_failed = bool(lib.get("last_error"))
        if not lib_success:
            library_state = "never"
        elif lib_failed:
            library_state = "error"
        else:
            library_state = "ok"
        library_error = None
        if library_state == "error":
            library_error = {
                "kind": lib.get("last_error_kind") or "unknown",
                "message": redact(lib.get("last_error"), limit=200),
            }
        st = states.get(ws)
        bindings_state = (st or {}).get("bindings_state") or "never"
        ws_library_state[ws] = library_state
        ws_bindings_state[ws] = bindings_state

        archived = {a["id"] for a in agents_by_ws.get(ws, []) if a.get("archived_at")}
        counts_ok = bindings_state in ("ok", "none")
        server_items = []
        for s in sorted(servers_by_ws.get(ws, []), key=lambda r: _sort_key(r.get("name"), r["id"])):
            if library_state == "never":
                continue
            if not counts_ok:
                enabled_count = disabled_count = None
            else:
                enabled_count = disabled_count = 0
                for (bws, aid), rows in bindings_by_agent.items():
                    if bws != ws or aid in archived:
                        continue
                    for b in rows:
                        if b["server_id"] != s["id"]:
                            continue
                        if b.get("enabled"):
                            enabled_count += 1
                        else:
                            disabled_count += 1
            server_items.append(
                {
                    "key": f"{ws}:{s['id']}",
                    "id": s["id"],
                    "name": s.get("name"),
                    "transport": s.get("transport") or "unknown",
                    "agent_count": s.get("agent_count") or 0,
                    "enabled_count": enabled_count,
                    "disabled_count": disabled_count,
                    "config_visible": False,
                    "created_at": s.get("created_at"),
                    "updated_at": s.get("updated_at"),
                }
            )
        workspaces.append(
            {
                "id": ws,
                "slug": w.get("slug"),
                "name": w.get("name"),
                "library_state": library_state,
                "library_collected_at": lib_success,
                "library_error": library_error,
                "bindings_state": bindings_state,
                "bindings_collected_at": (st or {}).get("bindings_collected_at"),
                "servers": server_items,
            }
        )

    agents: list[dict[str, Any]] = []
    attention: dict[str, list[dict[str, Any]]] = {b: [] for b in ATTENTION_BUCKETS}
    lib_servers = {
        (r["workspace_id"], r["id"]): r
        for rows in servers_by_ws.values()
        for r in rows
    }
    for w in ws_rows:
        ws = w["id"]
        for a in sorted(agents_by_ws.get(ws, []), key=lambda r: _sort_key(r.get("name"), r["id"])):
            archived = a.get("archived_at") is not None
            own = _own(a.get("mcp_own_json"))
            binding_rows = sorted(
                bindings_by_agent.get((ws, a["id"]), []),
                key=lambda r: _sort_key(r.get("name"), r["server_id"]),
            )
            ws_bstate = ws_bindings_state.get(ws, "never")
            if ws_bstate == "none":
                agent_bstate = "none"
            else:
                agent_bstate = agent_states.get((ws, a["id"])) or "never"
            if not binding_rows and own["state"] == "none" and agent_bstate != "error":
                continue

            rid = a.get("runtime_id")
            bound = bool(rid) and bool(a.get("runtime_bound"))
            if not bound:
                provider, loads = None, "unbound"
            elif (ws, rid) not in providers:
                provider, loads = None, "unknown"
            else:
                provider = providers[(ws, rid)]
                provider = provider.lower() if isinstance(provider, str) else None
                loads = runtime_loads(provider, True)

            own_names = {s.get("name") for s in own["servers"] if isinstance(s.get("name"), str)}
            bindings = []
            if agent_bstate == "ok":
                for b in binding_rows:
                    if own["state"] == "hidden":
                        shadowed = None
                    elif own["state"] == "managed":
                        shadowed = b.get("name") in own_names
                    else:
                        shadowed = False
                    bindings.append(
                        {
                            "server_id": b["server_id"],
                            "name": b.get("name"),
                            "enabled": bool(b.get("enabled")),
                            "shadowed": shadowed,
                        }
                    )

            effective: dict[str, dict[str, Any]] = {}
            for b in bindings:
                if not b["enabled"] or b["shadowed"]:
                    continue
                srv = lib_servers.get((ws, b["server_id"]))
                effective[b["name"] or b["server_id"]] = {
                    "name": b.get("name"),
                    "from": "library",
                    "transport": (srv or {}).get("transport") or "unknown",
                }
            for s in own["servers"]:
                effective[s.get("name") or "?"] = {
                    "name": s.get("name"),
                    "from": "own",
                    "transport": s.get("transport") or "unknown",
                }
            if ws_library_state.get(ws) == "never":
                eff_state = "unknown"
            elif agent_bstate in ("error", "never") or own["state"] in ("hidden", "invalid"):
                eff_state = "partial"
            else:
                eff_state = "known"
            agents.append(
                {
                    "key": f"{ws}:{a['id']}",
                    "workspace_id": ws,
                    "id": a["id"],
                    "name": a.get("name"),
                    "archived": archived,
                    "runtime_id": rid,
                    "provider": provider,
                    "runtime_loads": loads,
                    "own_config": own,
                    "bindings_state": agent_bstate,
                    "bindings": bindings,
                    "effective": {
                        "state": eff_state,
                        "servers": [effective[k] for k in sorted(effective, key=str.casefold)],
                    },
                }
            )

            if archived:
                continue
            managed = any(b["enabled"] for b in bindings) or (
                own["state"] == "managed" and len(own["servers"]) >= 1
            )
            if managed and loads in ("no", "unbound"):
                attention["ignored"].append(
                    {
                        "key": f"{ws}:{a['id']}",
                        "workspace_id": ws,
                        "agent_id": a["id"],
                        "server_id": None,
                        "name": a.get("name"),
                        "reason": "runtime_unbound" if loads == "unbound" else "runtime_no_mcp",
                    }
                )
            for b in bindings:
                if b["enabled"] and b["shadowed"]:
                    attention["shadowed"].append(
                        {
                            "key": f"{ws}:{a['id']}:{b['server_id']}",
                            "workspace_id": ws,
                            "agent_id": a["id"],
                            "server_id": b["server_id"],
                            "name": b.get("name"),
                            "reason": "own_config_same_name",
                        }
                    )
            if managed and provider == "opencode":
                attention["may_fail"].append(
                    {
                        "key": f"{ws}:{a['id']}",
                        "workspace_id": ws,
                        "agent_id": a["id"],
                        "server_id": None,
                        "name": a.get("name"),
                        "reason": "opencode_v2_unsupported",
                    }
                )
            if own["state"] == "hidden":
                attention["hidden"].append(
                    {
                        "key": f"{ws}:{a['id']}",
                        "workspace_id": ws,
                        "agent_id": a["id"],
                        "server_id": None,
                        "name": a.get("name"),
                        "reason": "config_redacted",
                    }
                )

    for w in workspaces:
        for s in w["servers"]:
            if s["agent_count"] == 0:
                attention["unused"].append(
                    {
                        "key": s["key"],
                        "workspace_id": w["id"],
                        "agent_id": None,
                        "server_id": s["id"],
                        "name": s["name"],
                        "reason": "no_agents",
                    }
                )
            elif w["bindings_state"] == "ok" and s["enabled_count"] == 0:
                attention["disabled_only"].append(
                    {
                        "key": s["key"],
                        "workspace_id": w["id"],
                        "agent_id": None,
                        "server_id": s["id"],
                        "name": s["name"],
                        "reason": "all_disabled",
                    }
                )
    for items in attention.values():
        items.sort(key=lambda it: (str(it.get("name") or "").casefold(), it["key"]))

    counts = {b: len(attention[b]) for b in ATTENTION_BUCKETS}
    return {
        "generated_at": generated_at,
        "mcp_schema": MCP_SCHEMA,
        "mcp_every_rounds": mcp_every,
        "support": SUPPORT,
        "workspaces": workspaces,
        "agents": agents,
        "attention": {
            "badge": sum(counts[b] for b in BADGE_BUCKETS),
            "counts": counts,
            "buckets": {b: attention[b] for b in ATTENTION_BUCKETS if attention[b]},
        },
    }
