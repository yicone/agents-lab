"""Read model for `GET /api/mcp` (mcp_schema: 2, mcp-redesign §4/§5/§5.1).

Rows are merged by raw server identity (HMAC row ids); display names are
presentation only. Fingerprints are compared in memory and only group letters
leave this module. Unknown sources beat absent evidence everywhere.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .mcp_local import (
    McpContext,
    TRANSPORT_ORDER,
    assign_row_ids,
    fingerprints,
    load_fingerprint_key,
    load_sources,
    norm_transport_string,
    row_id,
    scan_sources,
    SourcesInvalid,
)
from .mcp_summary import plain_text
from .mcp_support import SUPPORT, runtime_loads
from .redact import redact
from .store import Store

log = logging.getLogger("multica_board.mcp.view")

MCP_SCHEMA = 2
BUCKET_ORDER = ("broken", "ignored", "shadowed", "may_fail", "drift")
RED_BUCKETS = ("broken", "ignored", "shadowed", "may_fail")
BUCKET_TITLES = {
    "broken": "条目损坏",
    "ignored": "会被忽略",
    "shadowed": "同名覆盖",
    "may_fail": "可能失败",
    "drift": "配置不一致",
}
SOURCE_LABELS = {"both": "本机 + Multica", "local_only": "仅本机", "multica_only": "仅 Multica"}
CONSISTENCY_TAGS = {
    "broken": ("bad", "损坏"),
    "differs": ("warn", "不一致"),
    "same": ("ok", "一致"),
    "single": ("muted", "仅一处"),
    "incomparable": ("muted", "不可比"),
}
FILE_STATE_UNKNOWN = frozenset(
    {"denied", "symlink", "not_regular", "too_large", "parse_error", "unsupported", "io_error", "platform_unsupported"}
)
MAX_REASON_CHARS = 80


def _flag(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        st = value.get("set")
        ct = value.get("count")
        return {"set": st if isinstance(st, bool) or st is None else bool(st),
                "count": ct if isinstance(ct, int) or ct is None else 0}
    return {"set": False, "count": 0}


def _own(raw: str | None) -> dict[str, Any]:
    """Parse the stored own-config summary; tolerate v1 rows (no exact/broken_reason)."""
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
                        "name": s.get("name") if isinstance(s.get("name"), str) else None,
                        "exact": s.get("exact") is True,
                        "transport": norm_transport_string(s.get("transport")),
                        "broken_reason": s.get("broken_reason") if s.get("broken_reason") in
                            ("unknown_transport", "bad_field_type", "no_target", "missing_target", "not_object") else None,
                        "env": _flag(s.get("env")),
                        "headers": _flag(s.get("headers")),
                    }
                )
    return {"state": data["state"], "servers": servers}


def _display(raw: str) -> str:
    return plain_text(raw) or "（空名字）"


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_REASON_CHARS else text[: MAX_REASON_CHARS - 1] + "…"


def build_mcp(store: Store, server: str, generated_at: str | None, ctx: McpContext) -> dict[str, Any]:
    base: dict[str, Any] = {
        "mcp_schema": MCP_SCHEMA,
        "generated_at": generated_at,
        "support": SUPPORT,
    }
    try:
        sources = load_sources(ctx.sources_path)
    except SourcesInvalid as exc:
        return {
            **base,
            "ok": False,
            "error": {"code": exc.code, "message": "mcp-sources.json 无效"},
            "multica_collected_at": None,
            "tools": [],
            "repos": [],
            "workspaces": [],
            "unknown": _empty_unknown("never"),
            "items": [],
            "summary": [],
            "attention": _empty_attention(generated_at),
            "meta": {"sources": [{"id": "sources", "label": "文件清单", "state": "bad",
                                   "detail": "mcp-sources.json 无效"}]},
        }

    scan = scan_sources(sources, ctx.home, ctx.repo_root)
    own = ctx.own
    key_res = load_fingerprint_key(ctx.data_dir, geteuid=ctx.geteuid)
    own.sync_key(key_res["stamp"])
    key = key_res["key"]

    # --- multica cache ---------------------------------------------------------
    ws_rows = store.query("SELECT id, slug, name FROM workspaces WHERE server=? ORDER BY name", (server,))
    sync = {
        (r["workspace_id"], r["resource"]): r
        for r in store.query(
            "SELECT * FROM sync_status WHERE server=? AND resource IN ('mcp_library','mcp_bindings')", (server,)
        )
    }
    states = {r["workspace_id"]: r for r in store.query("SELECT * FROM mcp_state WHERE server=?", (server,))}
    servers_by_ws: dict[str, list[dict[str, Any]]] = {}
    for r in store.query("SELECT * FROM mcp_servers WHERE server=?", (server,)):
        servers_by_ws.setdefault(r["workspace_id"], []).append(r)
    bindings_by_agent: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in store.query("SELECT * FROM mcp_bindings WHERE server=?", (server,)):
        bindings_by_agent.setdefault((r["workspace_id"], r["agent_id"]), []).append(r)
    agents_by_ws: dict[str, list[dict[str, Any]]] = {}
    for r in store.query("SELECT * FROM agents WHERE server=?", (server,)):
        agents_by_ws.setdefault(r["workspace_id"], []).append(r)
    providers = {
        (r["workspace_id"], r["id"]): (r.get("provider") or None)
        for r in store.query("SELECT workspace_id, id, provider FROM runtimes WHERE server=?", (server,))
    }

    ws_info: dict[str, dict[str, Any]] = {}
    for w in ws_rows:
        ws = w["id"]
        lib = sync.get((ws, "mcp_library")) or {}
        ok = bool(lib.get("last_success_at"))
        failed = bool(lib.get("last_error"))
        if not ok:
            library_state = "never"
        elif failed:
            library_state = "stale"
        else:
            library_state = "ok"
        st = states.get(ws) or {}
        bindings_state = st.get("bindings_state") or "never"
        ws_info[ws] = {
            "row": w,
            "library_state": library_state,
            "library_known": library_state == "ok",
            "bindings_state": bindings_state,
            "bindings_known": bindings_state in ("ok", "none"),
            "library_error": (
                {"kind": lib.get("last_error_kind") or "unknown",
                 "message": redact(lib.get("last_error"), limit=200)}
                if library_state == "stale" else None
            ),
            "collected_at": st.get("bindings_collected_at") or lib.get("last_success_at"),
        }

    multica_ever = bool(ws_rows) and any(
        (sync.get((w["id"], "mcp_library")) or {}).get("last_success_at") for w in ws_rows
    )
    multica_state = "collected" if multica_ever else ("never" if not ws_rows else "stale")
    if ws_rows and not multica_ever:
        multica_state = "never" if all(
            not (sync.get((w["id"], "mcp_library")) or {}).get("last_attempt_at") for w in ws_rows
        ) else "stale"

    # --- row identity ----------------------------------------------------------
    # Raw names from local/project files and (via OwnFingerprints / exact flags)
    # from Multica sides produce row ids; unverified names get synthetic rows.
    raw_names: list[str] = []
    for tool in scan["tools"]:
        for e in tool["entries"] or []:
            raw_names.append(e["raw"])
    for pf in scan["project"]:
        for e in pf["entries"] or []:
            raw_names.append(e["raw"])
    ids = assign_row_ids(ctx.salt, raw_names)

    rows: dict[str, dict[str, Any]] = {}

    def row_for(rid: str, display: str | None) -> dict[str, Any]:
        row = rows.get(rid)
        if row is None:
            row = rows[rid] = {
                "id": rid,
                "display": display or "（空名字）",
                "entries": [],
                "ws_entries": [],
                "presence": {"local": 0, "project": 0, "library": 0, "own": 0},
                "unverified": False,
            }
        elif display and row["display"] == "（空名字）":
            row["display"] = display
        return row

    def file_entry(scope: str, file_decl: str, tool_id: str | None, location: str | None,
                   rec: dict[str, Any]) -> dict[str, Any]:
        return {
            "scope": scope,
            "tool": tool_id,
            "path": file_decl,
            "location": location,
            "state": "broken" if rec["broken_reason"] else "ok",
            "broken_reason": rec["broken_reason"],
            "disabled": rec["disabled"],
            "transport": rec["transport"],
            "env": rec["env"],
            "headers": rec["headers"],
            "fp": fingerprints(rec["fp_inputs"], key) if (key and rec["fp_inputs"]) else None,
            "member": None,  # display label for consistency groups
        }

    for tool in scan["tools"]:
        for rec in tool["entries"] or []:
            rid = ids[rec["raw"]]
            row = row_for(rid, _display(rec["raw"]))
            ent = file_entry("global", tool["declared"], tool["id"], None, rec)
            ent["member"] = tool["label"]
            row["entries"].append(ent)
            row["presence"]["local"] += 1
    for pf in scan["project"]:
        for rec in pf["entries"] or []:
            rid = ids[rec["raw"]]
            row = row_for(rid, _display(rec["raw"]))
            ent = file_entry("project", pf["declared"], None, pf["location"], rec)
            ent["member"] = f"仓库 {pf['location']}"
            row["entries"].append(ent)
            row["presence"]["project"] += 1

    # --- multica sides ----------------------------------------------------------
    all_files_known = all(f["state"] in ("ok", "absent") for f in scan["tools"]) and all(
        f["state"] in ("ok", "absent") for f in scan["project"]
    )
    files_unknown = [t["id"] for t in scan["tools"] if t["state"] in FILE_STATE_UNKNOWN]
    project_unknown = [p["location"] for p in scan["project"] if p["state"] in FILE_STATE_UNKNOWN]

    lib_unknown_ws: list[str] = []
    bindings_unknown_ws: list[str] = []
    own_hidden = own_invalid = own_pending = 0
    tidy_items: list[dict[str, Any]] = []

    def agent_runtime(a: dict[str, Any]) -> tuple[str | None, str]:
        rid = a.get("runtime_id")
        if not (rid and a.get("runtime_bound")):
            return None, "unbound"
        provider = providers.get((a["workspace_id"], rid))
        provider = provider.lower() if isinstance(provider, str) else None
        if provider is None:
            return None, "unknown"
        return provider, runtime_loads(provider, True)

    for w in ws_rows:
        ws = w["id"]
        info = ws_info[ws]
        ws_name = w.get("name") or ws
        if info["library_state"] != "ok":
            lib_unknown_ws.append(ws)
        if info["bindings_state"] not in ("ok", "none"):
            bindings_unknown_ws.append(ws)

        archived = {a["id"] for a in agents_by_ws.get(ws, []) if a.get("archived_at")}
        agent_rows = {a["id"]: a for a in agents_by_ws.get(ws, [])}

        # library entries
        for s in servers_by_ws.get(ws, []):
            name = s.get("name")
            exact = s.get("name_exact") == 1
            rec = own.library.get((ws, s["id"]))
            stale = info["library_state"] == "stale"
            if rec is not None:
                rid = rec["id"]
                display = name or "（空名字）"
            elif exact and isinstance(name, str):
                rid = ids.get(name) or row_id(ctx.salt, name)
                display = name
            else:
                rid = row_id(ctx.salt, f"\x00unverified:{ws}:{s['id']}")
                display = name or "（空名字）"
            bindings_known = info["bindings_known"]
            agents = None
            if bindings_known:
                agents = []
                for (bws, aid), blist in sorted(bindings_by_agent.items()):
                    if bws != ws or aid in archived:
                        continue
                    for b in blist:
                        if b["server_id"] != s["id"]:
                            continue
                        a = agent_rows.get(aid) or {}
                        provider, loads = agent_runtime({**a, "workspace_id": ws})
                        agents.append({
                            "id": aid,
                            "name": a.get("name"),
                            "enabled": bool(b.get("enabled")),
                            "shadowed": None,  # filled below
                            "provider": provider,
                            "runtime_loads": loads,
                        })
            row = row_for(rid, display)
            row["unverified"] = row["unverified"] or (rec is None and not exact)
            we = {
                "kind": "library",
                "workspace_id": ws,
                "workspace_name": ws_name,
                "server_id": s["id"],
                "transport": norm_transport_string(s.get("transport")),
                "agent_count": s.get("agent_count") or 0,
                "updated_at": s.get("updated_at"),
                "stale": stale,
                "bindings_known": bindings_known,
                "agents": agents,
                "unverified": rec is None and not exact,
            }
            row["ws_entries"].append(we)
            row["presence"]["library"] += 1
            # tidy: only when this workspace's bindings are known
            if bindings_known:
                enabled_n = sum(1 for ag in (agents or []) if ag["enabled"])
                assigned = sum(1 for ag in (agents or []))
                if s.get("agent_count") == 0 and assigned == 0:
                    tidy_items.append({"row": row, "ws": ws, "reason_code": "no_agents"})
                elif s.get("agent_count") and enabled_n == 0:
                    tidy_items.append({"row": row, "ws": ws, "reason_code": "all_disabled"})

        # own configs
        for a in agents_by_ws.get(ws, []):
            aid = a["id"]
            if aid in archived:
                continue
            summary = _own(a.get("mcp_own_json"))
            if summary["state"] == "hidden":
                own_hidden += 1
            elif summary["state"] == "invalid":
                own_invalid += 1
            servers = summary["servers"]
            if not servers:
                continue
            provider, loads = agent_runtime({**a, "workspace_id": ws})
            fp_rec = own.agent_record(ws, aid, a.get("updated_at"), len(servers))
            for index, s in enumerate(servers):
                fp_state = "pending"
                fp = None
                unverified = False
                if fp_rec is not None and index < len(fp_rec["order"]):
                    rid = fp_rec["order"][index]
                    fp_state = "ready"
                    fp = fp_rec["servers"].get(rid)
                elif s["exact"] and isinstance(s["name"], str):
                    rid = ids.get(s["name"]) or row_id(ctx.salt, s["name"])
                else:
                    rid = row_id(ctx.salt, f"\x00unverified:{ws}:{aid}:{index}")
                    unverified = True
                if fp_state == "pending":
                    own_pending += 1
                row = row_for(rid, s["name"] or "（空名字）")
                row["unverified"] = row["unverified"] or unverified
                row["ws_entries"].append(
                    {
                        "kind": "own",
                        "workspace_id": ws,
                        "workspace_name": ws_name,
                        "agent_id": aid,
                        "agent_name": a.get("name"),
                        "provider": provider,
                        "runtime_loads": loads,
                        "fp_state": fp_state,
                        "transport": s["transport"],
                        "broken_reason": s["broken_reason"],
                        "env": s["env"],
                        "headers": s["headers"],
                        "fp": fp,
                        "member": a.get("name") or aid,
                        "unverified": unverified,
                    }
                )
                row["presence"]["own"] += 1

    # shadowed: enabled library binding vs same agent's own entry in the same row
    for row in rows.values():
        own_by_agent: dict[str, list[dict[str, Any]]] = {}
        for we in row["ws_entries"]:
            if we["kind"] == "own":
                own_by_agent.setdefault(we["agent_id"], []).append(we)
        for we in row["ws_entries"]:
            if we["kind"] != "library" or not we["agents"]:
                continue
            for ag in we["agents"]:
                if not ag["enabled"]:
                    continue
                if ag["id"] in own_by_agent:
                    ag["shadowed"] = True
                else:
                    # own config hidden/unknown for this agent → cannot tell
                    a = next((x for x in agents_by_ws.get(we["workspace_id"], []) if x["id"] == ag["id"]), None)
                    own_state = _own((a or {}).get("mcp_own_json"))["state"] if a else "none"
                    ag["shadowed"] = None if own_state in ("hidden", "invalid") else False

    # --- per-row presentation ----------------------------------------------------
    unknown_own_present = own_hidden + own_invalid > 0
    items: list[dict[str, Any]] = []
    for row in rows.values():
        local_entries = row["entries"]
        ws_entries = row["ws_entries"]
        in_local = row["presence"]["local"] + row["presence"]["project"] > 0
        in_multica = row["presence"]["library"] + row["presence"]["own"] > 0
        if in_local and in_multica:
            source_class, source_uncertain = "both", False
        elif in_local:
            source_class = "local_only"
            source_uncertain = bool(lib_unknown_ws or bindings_unknown_ws or unknown_own_present)
        else:
            source_class = "multica_only"
            source_uncertain = not all_files_known

        cells: dict[str, Any] = {}
        per_tool: dict[str, list[dict[str, Any]]] = {}
        for ent in local_entries:
            if ent["scope"] == "global":
                per_tool.setdefault(ent["tool"], []).append(ent)
        any_file_unknown = bool(files_unknown or project_unknown)
        for tool in scan["tools"]:
            ents = per_tool.get(tool["id"], [])
            if ents:
                broken = next((e for e in ents if e["state"] == "broken"), None)
                if broken is not None:
                    cells[tool["id"]] = "missing"
                elif any(e["disabled"] for e in ents):
                    cells[tool["id"]] = "off"
                else:
                    cells[tool["id"]] = "direct"
            elif tool["state"] in FILE_STATE_UNKNOWN:
                cells[tool["id"]] = "unknown"
            elif all_files_known and not in_local and in_multica:
                cells[tool["id"]] = "not_applicable"
            else:
                cells[tool["id"]] = None
        repo_cells: dict[str, Any] = {}
        per_loc: dict[str, list[dict[str, Any]]] = {}
        for ent in local_entries:
            if ent["scope"] == "project":
                per_loc.setdefault(ent["location"], []).append(ent)
        for pf in scan["project"]:
            ents = per_loc.get(pf["location"], [])
            if ents:
                if any(e["state"] == "broken" for e in ents):
                    repo_cells[pf["location"]] = "missing"
                elif any(e["disabled"] for e in ents):
                    repo_cells[pf["location"]] = "off"
                else:
                    repo_cells[pf["location"]] = "direct"
            elif pf["state"] in FILE_STATE_UNKNOWN:
                repo_cells[pf["location"]] = "unknown"
            elif all_files_known and not in_local and in_multica:
                repo_cells[pf["location"]] = "not_applicable"
            else:
                repo_cells[pf["location"]] = None

        transports = sorted(
            {e["transport"] for e in local_entries} | {w["transport"] for w in ws_entries},
            key=TRANSPORT_ORDER.index,
        )

        # consistency
        participants = [
            e for e in local_entries if e["state"] == "ok" and e["fp"]
        ] + [
            w for w in ws_entries if w["kind"] == "own" and w["fp_state"] == "ready" and w["fp"]
        ]
        pending = sum(1 for w in ws_entries if w["kind"] == "own" and w["fp_state"] == "pending")
        any_broken = any(e["state"] == "broken" for e in local_entries) or any(
            w.get("broken_reason") for w in ws_entries if w["kind"] == "own"
        )
        partial = bool(any_file_unknown or pending or row["unverified"])
        if any_broken:
            state = "broken"
            groups: list[dict[str, Any]] = []
            parts = {"launch": "same", "env": "same", "headers": "same"}
        elif not participants:
            state = "incomparable"
            groups = []
            parts = {"launch": "same", "env": "same", "headers": "same"}
        else:
            by_all: dict[bytes, list[Any]] = {}
            for p in participants:
                by_all.setdefault(p["fp"]["all"], []).append(p)
            ordered = sorted(
                by_all.values(),
                key=lambda members: (-len(members), _member_order(members, scan)),
            )
            groups = []
            for letter_index, members in enumerate(ordered):
                letter = chr(ord("A") + letter_index) if letter_index < 26 else f"Z{letter_index}"
                for m in members:
                    m["group"] = letter
                groups.append({"id": letter, "members": sorted({m["member"] for m in members})})
            parts = {}
            for part in ("launch", "env", "headers"):
                distinct = {p["fp"][part] for p in participants}
                parts[part] = "same" if len(distinct) <= 1 else "differs"
            if len(participants) == 1:
                state = "single"
            else:
                state = "same" if len(ordered) == 1 else "differs"
        for ent in local_entries:
            ent.setdefault("group", None)
        for w in ws_entries:
            w.setdefault("group", None)
        consistency = {"state": state, "partial": partial, "pending": pending,
                       "groups": groups, "parts": parts}
        # bucket (first hit wins; the rest become tags) — computed before
        # in-memory-only keys are stripped below.
        hits: list[str] = []
        if any_broken:
            hits.append("broken")
        ignored_hits = _ignored_hits(ws_entries)
        if ignored_hits:
            hits.append("ignored")
        shadow_hits = _shadowed_hits(ws_entries)
        if shadow_hits:
            hits.append("shadowed")
        mayfail_hits = [w for w in ws_entries if w["kind"] == "library" and w["agents"]
                        for ag in w["agents"] if ag["enabled"] and ag["provider"] == "opencode"]
        mayfail_hits += [w for w in ws_entries if w["kind"] == "own" and w["provider"] == "opencode"]
        if mayfail_hits:
            hits.append("may_fail")
        if consistency["state"] == "differs":
            hits.append("drift")
        bucket = hits[0] if hits else None
        tags = hits[1:]
        if row["unverified"]:
            tags = tags + ["name_unverified"]
        reason = _reason(row, bucket, consistency, ignored_hits, shadow_hits, mayfail_hits)
        for ent in local_entries + ws_entries:
            ent.pop("fp", None)
            ent.pop("member", None)

        transports_label = transports or ["unknown"]
        item = {
            "id": row["id"],
            "display_name": row["display"],
            "display_dup": None,
            "source_class": source_class,
            "source_uncertain": source_uncertain,
            "source_label": SOURCE_LABELS[source_class],
            "in_project": row["presence"]["project"] > 0,
            "presence": row["presence"],
            "transports": transports_label,
            "tools": cells,
            "repo_cells": repo_cells,
            "entries": [
                {k: v for k, v in e.items() if k != "location" or e["scope"] == "project"}
                for e in local_entries
            ],
            "workspace_entries": [
                {k: v for k, v in w.items() if k != "unverified"} for w in ws_entries
            ],
            "consistency": consistency,
            "tidy": None,
            "bucket": bucket,
            "tags": tags,
            "reason": reason,
            "quiet": False,
            "command": _open_command(row),
        }
        items.append(item)

    # display_dup
    by_display: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_display.setdefault(item["display_name"], []).append(item)
    for group in by_display.values():
        if len(group) > 1:
            for index, it in enumerate(sorted(group, key=lambda x: x["id"])):
                it["display_dup"] = {"index": index + 1, "total": len(group)}

    # tidy assignment to rows
    for t in tidy_items:
        row = t["row"]
        item = next((i for i in items if i["id"] == row["id"]), None)
        if item is not None and item["tidy"] is None:
            item["tidy"] = {"reason": t["reason_code"], "workspace_id": t["ws"]}
    items.sort(key=lambda i: (i["display_name"].casefold(), i["id"]))

    # --- summary cards -----------------------------------------------------------
    def status_of(item: dict[str, Any]) -> str:
        return item["consistency"]["state"]

    summary = []
    for sid, label in (("all", "全部"), ("both", "本机 + Multica"),
                       ("local_only", "仅本机"), ("multica_only", "仅 Multica")):
        chosen = items if sid == "all" else [i for i in items if i["source_class"] == sid]
        statuses: dict[str, int] = {}
        for i in chosen:
            statuses[status_of(i)] = statuses.get(status_of(i), 0) + 1
        summary.append({
            "id": sid, "label": label, "count": len(chosen),
            "uncertain": sum(1 for i in chosen if i["source_uncertain"]),
            "statuses": statuses,
        })

    # --- buckets / attention -------------------------------------------------------
    buckets: dict[str, dict[str, Any]] = {}
    local_scanned = any(f["state"] != "platform_unsupported" for f in scan["tools"])
    for code in BUCKET_ORDER:
        connected, reason, unknown_sources = True, "", []
        if code == "broken":
            if not local_scanned and not multica_ever:
                connected, reason = False, "本机未扫描且 Multica 从未采集"
            if files_unknown:
                unknown_sources += [f"本机 {tid}" for tid in files_unknown]
            if project_unknown:
                unknown_sources += [f"仓库 {loc}" for loc in project_unknown]
            if unknown_own_present:
                unknown_sources.append("agent 自有配置")
        elif code in ("ignored", "shadowed", "may_fail"):
            if not multica_ever:
                connected, reason = False, "Multica 从未采集"
            if bindings_unknown_ws:
                unknown_sources += [ws_info[w]["row"].get("name") or w for w in bindings_unknown_ws]
            if unknown_own_present:
                unknown_sources.append("agent 自有配置")
            if code == "shadowed" and any(i.get("tidy") is None and "name_unverified" in i["tags"] for i in items):
                unknown_sources.append("原名未确认")
        elif code == "drift":
            if key is None:
                connected, reason = False, f"指纹密钥不可用（{key_res['code']}），未比对"
            elif not local_scanned and not any(
                w["kind"] == "own" and w["fp_state"] == "ready"
                for i in items for w in i["workspace_entries"]
            ):
                connected, reason = False, "本机未扫描且自有配置指纹未就绪"
            if files_unknown or project_unknown:
                unknown_sources += [f"本机 {tid}" for tid in files_unknown]
                unknown_sources += [f"仓库 {loc}" for loc in project_unknown]
            if own_pending:
                unknown_sources.append("agent 自有配置待采集")
        bucket_items = [
            {
                "id": i["id"],
                "title": i["reason"] or BUCKET_TITLES[code],
                "status": code,
                "time": generated_at,
                "source_class": i["source_class"],
                "tags": i["tags"],
            }
            for i in items if i["bucket"] == code
        ]
        buckets[code] = {
            "connected": connected,
            "partial": bool(unknown_sources),
            "unknown_sources": unknown_sources,
            "reason": reason,
            "items": bucket_items,
        }

    tidy_unknown = [ws_info[w]["row"].get("name") or w for w in lib_unknown_ws]
    tidy_unknown += [ws_info[w]["row"].get("name") or w for w in bindings_unknown_ws]
    tidy = {
        "count": len(tidy_items),
        "partial": bool(tidy_unknown),
        "unknown_sources": sorted(set(tidy_unknown)),
        "connected": multica_ever,
        "reason": "" if multica_ever else "Multica 从未采集",
    }
    badge = sum(len(buckets[c]["items"]) for c in BUCKET_ORDER)
    any_partial = any(b["partial"] or not b["connected"] for b in buckets.values())
    if any(len(buckets[c]["items"]) for c in RED_BUCKETS):
        tone = "bad"
    elif buckets["drift"]["items"]:
        tone = "warn"
    elif badge == 0 and (any_partial or tidy["partial"] or not tidy["connected"]):
        tone = "unknown"
    elif badge == 0:
        tone = "none"
    else:
        tone = "warn"
    attention = {
        "badge": badge,
        "tone": tone,
        "partial": any_partial,
        "counts": {c: len(buckets[c]["items"]) for c in BUCKET_ORDER},
        "buckets": buckets,
        "tidy": tidy,
    }

    # --- unknown / meta -----------------------------------------------------------
    unknown = {
        "local_files": files_unknown,
        "project_files": project_unknown,
        "library": lib_unknown_ws,
        "bindings": bindings_unknown_ws,
        "own_hidden": own_hidden,
        "own_invalid": own_invalid,
        "own_pending": own_pending,
        "key": key_res["code"] or "ok",
        "multica": multica_state,
    }

    def chip_local() -> dict[str, Any]:
        files = scan["tools"]
        ok = sum(1 for f in files if f["state"] == "ok")
        absent = sum(1 for f in files if f["state"] == "absent")
        bad = len(files) - ok - absent
        parts = [f"{ok} 已读", f"{absent} 不存在"]
        if bad:
            parts.append(f"{bad} 读不出")
        state = "ok" if bad == 0 else ("bad" if ok + absent == 0 else "warn")
        return {"id": "local_files", "label": "本机配置", "state": state, "detail": " · ".join(parts)}

    def chip_project() -> dict[str, Any]:
        files = scan["project"]
        ok = sum(1 for f in files if f["state"] == "ok")
        absent = sum(1 for f in files if f["state"] == "absent")
        bad = len(files) - ok - absent
        parts = [f"{ok} 有", f"{absent} 不存在"]
        if bad:
            parts.append(f"{bad} 读不出")
        state = "ok" if bad == 0 else ("bad" if ok + absent == 0 else "warn")
        return {"id": "project_files", "label": "仓库项目级", "state": state, "detail": " · ".join(parts)}

    def chip_fp() -> dict[str, Any]:
        if key is None:
            return {"id": "fingerprints", "label": "配置指纹", "state": "bad",
                    "detail": f"密钥文件不可用（{key_res['code']}），未比对"}
        if own_pending:
            return {"id": "fingerprints", "label": "配置指纹", "state": "warn",
                    "detail": f"自有配置待采集 {own_pending} 个"}
        return {"id": "fingerprints", "label": "配置指纹", "state": "ok", "detail": "只比哈希"}

    def chip_library() -> dict[str, Any]:
        total = len(ws_rows)
        bad_n = len(lib_unknown_ws)
        if total == 0:
            return {"id": "multica_library", "label": "Multica 库", "state": "warn", "detail": "从未采集"}
        state = "ok" if bad_n == 0 else ("bad" if bad_n == total else "warn")
        return {"id": "multica_library", "label": "Multica 库", "state": state,
                "detail": f"{total} 个 Workspace" + (f" · {bad_n} 个没读到" if bad_n else "")}

    def chip_bindings() -> dict[str, Any]:
        total = len(ws_rows)
        bad_n = len(bindings_unknown_ws)
        if total == 0:
            return {"id": "agent_bindings", "label": "Agent 分配", "state": "warn", "detail": "从未采集"}
        state = "ok" if bad_n == 0 else ("bad" if bad_n == total else "warn")
        return {"id": "agent_bindings", "label": "Agent 分配", "state": state,
                "detail": f"{total} 个 Workspace" + (f" · {bad_n} 个没读到" if bad_n else "")}

    def chip_own() -> dict[str, Any]:
        bad_n = own_hidden + own_invalid
        if bad_n:
            return {"id": "multica_own_config", "label": "Agent 自有配置", "state": "warn",
                    "detail": f"{bad_n} 个不可见或无效"}
        return {"id": "multica_own_config", "label": "Agent 自有配置", "state": "ok", "detail": "全部可见"}

    meta_sources = [
        chip_local(), chip_project(), chip_fp(), chip_library(), chip_bindings(), chip_own(),
        {"id": "runtime_support", "label": "Runtime 加载表", "state": "ok", "detail": "v0.6.5"},
    ]

    workspaces = [
        {
            "id": w["id"],
            "slug": w.get("slug"),
            "name": w.get("name"),
            "library_state": ws_info[w["id"]]["library_state"],
            "library_error": ws_info[w["id"]]["library_error"],
            "bindings_state": ws_info[w["id"]]["bindings_state"],
            "collected_at": ws_info[w["id"]]["collected_at"],
        }
        for w in ws_rows
    ]
    tools_out = [
        {
            "id": t["id"],
            "label": t["label"],
            "short_label": t["short_label"],
            "files": [{"path": t["declared"], "scope": "global", "state": t["state"],
                        "detail": t["detail"], "entry_count": t["entry_count"]}],
        }
        for t in scan["tools"]
    ]
    repos = [
        {
            "id": ctx.repo_root.name or "repo",
            "label": ctx.repo_root.name or "repo",
            "files": [
                {"path": p["declared"], "location": p["location"], "state": p["state"],
                 "detail": p["detail"], "entry_count": p["entry_count"]}
                for p in scan["project"]
            ],
        }
    ]
    collected_at = max(
        [s.get("last_success_at") or "" for s in sync.values()] or [""]
    ) or generated_at
    return {
        **base,
        "ok": True,
        "error": None,
        "multica_collected_at": collected_at,
        "tools": tools_out,
        "repos": repos,
        "workspaces": workspaces,
        "unknown": unknown,
        "items": items,
        "summary": summary,
        "attention": attention,
        "meta": {"sources": meta_sources},
    }


def _empty_unknown(multica: str) -> dict[str, Any]:
    return {"local_files": [], "project_files": [], "library": [], "bindings": [],
            "own_hidden": 0, "own_invalid": 0, "own_pending": 0, "key": "ok", "multica": multica}


def _empty_attention(generated_at: str | None) -> dict[str, Any]:
    buckets = {
        c: {"connected": False, "partial": False, "unknown_sources": [],
            "reason": "数据无效", "items": []}
        for c in BUCKET_ORDER
    }
    return {
        "badge": 0, "tone": "unknown", "partial": True,
        "counts": {c: 0 for c in BUCKET_ORDER},
        "buckets": buckets,
        "tidy": {"count": 0, "partial": True, "unknown_sources": [], "connected": False,
                  "reason": "数据无效"},
    }


def _member_order(members: list[Any], scan: dict[str, Any]) -> int:
    labels = [m.get("member") for m in members]
    order = {t["label"]: i for i, t in enumerate(scan["tools"])}
    return min((order.get(l, 99) for l in labels), default=99)


def _ignored_hits(ws_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits = []
    for w in ws_entries:
        if w["kind"] == "own" and w["runtime_loads"] in ("no", "unbound"):
            hits.append({"agent_name": w["agent_name"], "loads": w["runtime_loads"],
                          "workspace_name": w["workspace_name"]})
        if w["kind"] == "library" and w["agents"]:
            for ag in w["agents"]:
                if ag["enabled"] and not ag["shadowed"] and ag["runtime_loads"] in ("no", "unbound"):
                    a_name = ag["name"]
                    hits.append({"agent_name": a_name, "loads": ag["runtime_loads"],
                                  "workspace_name": w["workspace_name"]})
    return hits


def _shadowed_hits(ws_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits = []
    for w in ws_entries:
        if w["kind"] == "library" and w["agents"]:
            for ag in w["agents"]:
                if ag["enabled"] and ag["shadowed"] is True:
                    hits.append({"agent_name": ag["name"], "workspace_name": w["workspace_name"]})
    return hits


def _reason(row, bucket, consistency, ignored_hits, shadow_hits, mayfail_hits) -> str | None:
    if bucket == "broken":
        broken = next(
            (e for e in row["entries"] if e["state"] == "broken"),
            next((w for w in row["ws_entries"] if w.get("broken_reason")), None),
        )
        if broken is None:
            return "条目损坏"
        where = broken.get("member") or broken.get("path") or "agent 自有配置"
        labels = {
            "unknown_transport": "类型无法识别",
            "bad_field_type": "字段类型不对",
            "no_target": "缺 command 和 url",
            "missing_target": "缺启动目标",
            "not_object": "条目不是对象",
        }
        return _clip(f"{where} 的条目{labels.get(broken.get('broken_reason') or '', '损坏')}")
    if bucket == "ignored":
        h = ignored_hits[0]
        why = "未绑定 runtime" if h["loads"] == "unbound" else "runtime 不加载 MCP"
        extra = f" 等 {len(ignored_hits)} 个" if len(ignored_hits) > 1 else ""
        return _clip(f"{h['agent_name']} 的 {why}（{h['workspace_name']}）{extra}")
    if bucket == "shadowed":
        h = shadow_hits[0]
        extra = f" 等 {len(shadow_hits)} 个" if len(shadow_hits) > 1 else ""
        return _clip(f"{h['agent_name']} 的自有配置同名，库条目不生效（{h['workspace_name']}）{extra}")
    if bucket == "may_fail":
        names = [h.get("agent_name") for h in mayfail_hits if h.get("agent_name")]
        extra = f" 等 {len(names)} 个" if len(names) > 1 else ""
        return _clip(f"{names[0] if names else 'agent'}：OpenCode 2.x 有托管 MCP 时任务直接失败{extra}")
    if bucket == "drift":
        groups = consistency.get("groups") or []
        names = [g["members"] for g in groups[:2]]
        part = next((p for p, v in consistency["parts"].items() if v == "differs"), "配置")
        part_label = {"launch": "启动方式", "env": "env", "headers": "headers"}[part]
        if len(groups) >= 2:
            return _clip(f"{'、'.join(names[0])} 与 {'、'.join(names[1])} 的 {part_label} 不同")
        return _clip("配置不一致")
    return None


def _open_command(row: dict[str, Any]) -> str | None:
    for ent in row["entries"]:
        path = ent["path"]
        if path:
            return f'"${{EDITOR:-vi}}" {path}'
    return None
