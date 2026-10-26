"""Read-only Skills/Memory evidence inventory.

Projects ``GET /api/skills`` into ``atlas.evidence_inventory/v1`` without
merging same-named records and without returning bodies or write actions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
import re

SCHEMA = "atlas.evidence_inventory/v1"
SOURCE_KINDS = ("local_machine", "multica_workspace", "grok_bot", "grok_cli", "repository")
SCOPES = ("bot-private", "shared", "workspace", "repository", "unknown")
COLLECTION_STATES = ("collected", "uncollected", "unavailable", "invalid", "not_applicable")
EVIDENCE_LEVELS = ("observed", "declared", "derived", "historical", "unknown")
REASONS = (
    "not_implemented",
    "source_not_configured",
    "not_logged_in",
    "permission_denied",
    "read_failed",
    "schema_invalid",
    "source_unverified",
    "private_rpc_not_supported",
)
HASH_STATES = ("available", "unavailable", "not_collected", "not_applicable")

_SECRET_MARK = re.compile(r"(token|secret|password|cookie|credential|authorization)=", re.I)
_PRESENT = {"present", "broken_link"}
_POSITIVE_SHARED = {"dir", "link", "ok", "content_missing", "broken_link"}


def build_evidence_inventory(
    skills: dict[str, Any] | None,
    *,
    workspace_collected: bool | None = None,
    home: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the read-only inventory. Never copies bodies, hashes, or commands."""
    generated_at = _generated_at(skills, now)
    home_text = str(home if home is not None else Path.home())
    if not isinstance(skills, dict):
        sources = _fixed_sources(generated_at, inventory_ok=False, workspace_state="invalid", grok_state="invalid", grok_cli_collected=False)
        items = _memory_slots()
        _attach_related_names(items)
        return _document(generated_at, sources, items, ok=False, error={"code": "schema_invalid", "message": "skills payload is not an object"})

    raw_items = skills.get("items")
    inventory_ok = skills.get("ok") is True and isinstance(raw_items, list)
    rows = [row for row in raw_items if isinstance(row, dict)] if isinstance(raw_items, list) else []
    if workspace_collected is None:
        workspace_collected = any("workspace_installs" in row for row in rows)
    grok_meta = _meta_source(skills, "grok_bot")
    skill_items: list[dict[str, Any]] = []
    grok_cli_count = 0
    for row in rows:
        kind = row.get("source_class")
        origin = row.get("origin")
        if kind == "grok_bot" or origin == "grok_bot":
            if _grok_readable(grok_meta):
                skill_items.extend(_grok_bot_items(row, generated_at))
            continue
        if kind == "grok_cli" or origin == "grok_cli":
            skill_items.append(_grok_cli_item(row, generated_at))
            grok_cli_count += 1
            continue
        if workspace_collected:
            skill_items.extend(_workspace_items(row, generated_at))
        if not inventory_ok:
            continue
        if origin == "cloud" or kind == "multica_cloud":
            continue
        if origin == "inventory":
            skill_items.append(_repository_item(row, generated_at))
        local = _local_item(row, generated_at, home_text)
        if local:
            skill_items.append(local)

    if workspace_collected:
        workspace_state = "collected"
    elif inventory_ok or isinstance(raw_items, list):
        workspace_state = "uncollected"
    else:
        workspace_state = "invalid"
    grok_state = _grok_source_state(grok_meta, any(item["source_id"] == "grok_bot" for item in skill_items))
    sources = _fixed_sources(
        generated_at,
        inventory_ok=inventory_ok,
        workspace_state=workspace_state,
        grok_state=grok_state,
        grok_cli_collected=grok_cli_count > 0,
        grok_collected_at=_text(grok_meta.get("collected_at")) if isinstance(grok_meta, dict) else None,
    )
    items = skill_items + _memory_slots()
    _attach_related_names(items)
    items.sort(key=lambda item: (_source_index(item["source_id"]), item["display_name"], item["object_id"]))
    return _document(generated_at, sources, items, ok=True, error=None)


def _document(generated_at: str, sources: list[dict[str, Any]], items: list[dict[str, Any]], *, ok: bool, error: dict[str, str] | None) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "read_only": True,
        "ok": ok,
        "error": error,
        "sources": sources,
        "items": items,
    }


def _fixed_sources(
    generated_at: str,
    *,
    inventory_ok: bool,
    workspace_state: str,
    grok_state: str,
    grok_cli_collected: bool,
    grok_collected_at: str | None = None,
) -> list[dict[str, Any]]:
    local_state = "collected" if inventory_ok else "invalid"
    return [
        _source("local", "local_machine", "本机", "shared", local_state, generated_at if inventory_ok else None, "observed" if inventory_ok else "unknown", None if inventory_ok else "schema_invalid"),
        _source("repository", "repository", "仓库声明", "repository", local_state, generated_at if inventory_ok else None, "declared" if inventory_ok else "unknown", None if inventory_ok else "schema_invalid"),
        _source("multica_workspace", "multica_workspace", "Multica Workspace", "workspace", workspace_state, generated_at if workspace_state == "collected" else None, "observed" if workspace_state == "collected" else "unknown", _state_reason(workspace_state, "not_implemented")),
        _source("grok_bot", "grok_bot", "Grok Bot", "bot-private", grok_state, grok_collected_at or (generated_at if grok_state == "collected" else None), "declared" if grok_state == "collected" else "unknown", _state_reason(grok_state, "source_not_configured")),
        _source("grok_cli", "grok_cli", "Grok CLI", "shared", "collected" if grok_cli_collected else "uncollected", generated_at if grok_cli_collected else None, "declared" if grok_cli_collected else "unknown", None if grok_cli_collected else "not_implemented"),
        _source("memory", "grok_bot", "Memory", "unknown", "uncollected", None, "unknown", "source_unverified"),
    ]


def _state_reason(state: str, uncollected_reason: str) -> str | None:
    if state == "collected":
        return None
    if state == "invalid":
        return "schema_invalid"
    if state == "unavailable":
        return "read_failed"
    if state == "not_applicable":
        return "not_implemented"
    return uncollected_reason


def _source(source_id: str, source_kind: str, label: str, scope: str, state: str, collected_at: str | None, level: str, reason: str | None) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "source_kind": source_kind,
        "label": label,
        "scope": scope,
        "collection_state": state,
        "collected_at": collected_at,
        "evidence_level": level,
        "reason": reason,
        "error_code": reason if state in {"invalid", "unavailable"} else None,
    }


def _memory_slots() -> list[dict[str, Any]]:
    specs = (
        ("memory:bot-private", "Bot 私有 Memory", "bot-private", "bot-private 槽位；不与共享 Profile 或 Journal 合并"),
        ("memory:shared_profile", "共享 Profile", "shared", "shared_profile 槽位；不是某个 Bot 的私有正文"),
        ("memory:shared_journal", "共享 Journal", "shared", "shared_journal 槽位；不是某个 Bot 的私有正文"),
    )
    slots = []
    for object_id, name, scope, label in specs:
        slots.append(_item(
            object_type="memory_collection",
            object_id=object_id,
            display_name=name,
            source_id="memory",
            source_kind="grok_bot",
            scope=scope,
            collection_state="uncollected",
            observed_at=None,
            evidence_level="unknown",
            hash_state="not_applicable",
            evidence_kind="metadata",
            location=None,
            object_ref=None,
            source_label=f"{label}。无正文；不探测私有 RPC",
            reason="source_unverified",
        ))
    return slots


def _repository_item(row: dict[str, Any], generated_at: str) -> dict[str, Any]:
    name = _display_name(row)
    canonical = row.get("canonical_path")
    location = canonical if isinstance(canonical, str) and canonical and not canonical.startswith(("/", "~")) else "skills-inventory.json"
    return _item(
        object_type="skill",
        object_id="repository:" + quote(name, safe=""),
        display_name=name,
        source_id="repository",
        source_kind="repository",
        scope="repository",
        collection_state="collected",
        observed_at=generated_at,
        evidence_level="declared",
        hash_state="not_collected",
        evidence_kind="declaration",
        location=location,
        object_ref=None,
        source_label="仓库清单声明；不表示本机已安装或已加载",
        reason=None,
    )


def _local_item(row: dict[str, Any], generated_at: str, home: str) -> dict[str, Any] | None:
    records = [record for record in row.get("evidence") or [] if isinstance(record, dict)]
    states = {record.get("state") for record in records}
    shared = row.get("shared_entry") if isinstance(row.get("shared_entry"), dict) else {}
    shared_state = shared.get("state")
    location = _local_location(row, records, shared, home)
    present = bool(states & _PRESENT) or shared_state in _POSITIVE_SHARED or _concrete(row.get("install_path"))
    denied = "denied" in states or shared_state == "denied"
    unknown = "unknown" in states or shared_state == "unknown"
    if not present and not denied and not unknown:
        return None
    if denied and not present:
        state, level, reason = "unavailable", "unknown", "permission_denied"
        hash_state = "unavailable"
    elif unknown and not present:
        state, level, reason = "unavailable", "unknown", "read_failed"
        hash_state = "unavailable"
    elif (denied or unknown) and present:
        state, level, reason = "collected", "unknown", None
        hash_state = "not_collected"
    else:
        state, level, reason = "collected", "observed", None
        hash_state = "available" if _has_hash(row.get("local_source")) else "not_collected"
    name = _display_name(row)
    if location:
        object_id = "local_machine:" + quote(location, safe="/~.-")
    else:
        object_id = "local_machine:name:" + quote(name, safe="")
        level = "derived"
    return _item(
        object_type="skill",
        object_id=object_id,
        display_name=name,
        source_id="local",
        source_kind="local_machine",
        scope=_local_scope(row, records),
        collection_state=state,
        observed_at=generated_at,
        evidence_level=level,
        hash_state=hash_state,
        evidence_kind="entry",
        location=location,
        object_ref=None,
        source_label="本机目录入口；路径存在不表示当前已加载",
        reason=reason,
    )


def _workspace_items(row: dict[str, Any], generated_at: str) -> list[dict[str, Any]]:
    installs = row.get("workspace_installs")
    if not isinstance(installs, list):
        return []
    items = []
    for index, install in enumerate(installs):
        name = _display_name(row)
        if not isinstance(install, dict):
            items.append(_item(
                object_type="skill",
                object_id=f"multica_workspace:invalid:{index}:" + quote(name, safe=""),
                display_name=name,
                source_id="multica_workspace",
                source_kind="multica_workspace",
                scope="workspace",
                collection_state="invalid",
                observed_at=generated_at,
                evidence_level="unknown",
                hash_state="unavailable",
                evidence_kind="metadata",
                location=None,
                object_ref=None,
                source_label="Workspace 安装记录不符合契约",
                reason="schema_invalid",
            ))
            continue
        install_name = install.get("name") if isinstance(install.get("name"), str) and install.get("name") else name
        workspace_id = install.get("workspace_id") if isinstance(install.get("workspace_id"), str) else ""
        skill_id = install.get("id") if isinstance(install.get("id"), str) else ""
        if workspace_id and skill_id:
            object_id = f"multica_workspace:{workspace_id}:{skill_id}"
            level = "observed"
        else:
            object_id = f"multica_workspace:{workspace_id or 'unknown'}:name:" + quote(install_name, safe="")
            level = "derived"
        items.append(_item(
            object_type="skill",
            object_id=object_id,
            display_name=install_name,
            source_id="multica_workspace",
            source_kind="multica_workspace",
            scope="workspace",
            collection_state="collected",
            observed_at=generated_at,
            evidence_level=level,
            hash_state="available" if _has_hash(install) else "not_collected",
            evidence_kind="metadata",
            location=None,
            object_ref=skill_id or None,
            source_label="Workspace 安装元数据；正文未采集",
            reason=None,
        ))
    return items


def _grok_bot_items(row: dict[str, Any], generated_at: str) -> list[dict[str, Any]]:
    name = _display_name(row)
    bots = [bot for bot in row.get("grok_bots") or [] if isinstance(bot, dict) and isinstance(bot.get("id"), str) and bot.get("id")]
    if not bots:
        return [_item(
            object_type="skill",
            object_id="grok_bot:name:" + quote(name, safe=""),
            display_name=name,
            source_id="grok_bot",
            source_kind="grok_bot",
            scope="bot-private",
            collection_state="collected",
            observed_at=generated_at,
            evidence_level="derived",
            hash_state="not_applicable",
            evidence_kind="declaration",
            location=None,
            object_ref=None,
            source_label="Grok Bot 名称声明；无稳定 Bot ID，不生成本机安装、版本或正文",
            reason=None,
        )]
    items = []
    for bot in bots:
        bot_name = " ".join(str(bot.get("name") or "").split())[:80]
        label = "Grok Bot 名称声明" + (f" · {bot_name}" if bot_name else "")
        items.append(_item(
            object_type="skill",
            object_id=f"grok_bot:{bot['id']}:" + quote(name, safe=""),
            display_name=name,
            source_id="grok_bot",
            source_kind="grok_bot",
            scope="bot-private",
            collection_state="collected",
            observed_at=generated_at,
            evidence_level="declared",
            hash_state="not_applicable",
            evidence_kind="declaration",
            location=None,
            object_ref=bot["id"],
            source_label=label + "；无正文、版本或本机安装",
            reason=None,
        ))
    return items


def _grok_cli_item(row: dict[str, Any], generated_at: str) -> dict[str, Any]:
    name = _display_name(row)
    key = row.get("key") if isinstance(row.get("key"), str) and row.get("key").startswith("grok_cli:") else ""
    return _item(
        object_type="skill",
        object_id=key or ("grok_cli:" + quote(name, safe="")),
        display_name=name,
        source_id="grok_cli",
        source_kind="grok_cli",
        scope="bot-private" if row.get("scope") == "bot-private" else "shared",
        collection_state="collected",
        observed_at=generated_at,
        evidence_level="declared" if key else "derived",
        hash_state="not_collected",
        evidence_kind="metadata",
        location=None,
        object_ref=None,
        source_label="Grok CLI 只读元数据；不等于 Grok Bot，也不等于本机目录",
        reason=None,
    )


def _item(
    *,
    object_type: str,
    object_id: str,
    display_name: str,
    source_id: str,
    source_kind: str,
    scope: str,
    collection_state: str,
    observed_at: str | None,
    evidence_level: str,
    hash_state: str,
    evidence_kind: str,
    location: str | None,
    object_ref: str | None,
    source_label: str,
    reason: str | None,
) -> dict[str, Any]:
    if collection_state not in COLLECTION_STATES:
        raise ValueError(collection_state)
    if evidence_level not in EVIDENCE_LEVELS:
        raise ValueError(evidence_level)
    if source_kind not in SOURCE_KINDS:
        raise ValueError(source_kind)
    if scope not in SCOPES:
        raise ValueError(scope)
    if hash_state not in HASH_STATES:
        raise ValueError(hash_state)
    if reason is not None and reason not in REASONS:
        raise ValueError(reason)
    return {
        "object_type": object_type,
        "object_id": object_id,
        "display_name": display_name,
        "source_id": source_id,
        "source_kind": source_kind,
        "scope": scope,
        "collection_state": collection_state,
        "observed_at": observed_at,
        "evidence_level": evidence_level,
        "hash": {
            "state": hash_state,
            "algorithm": "sha256" if hash_state == "available" else None,
            "value": None,
        },
        "evidence": {
            "kind": evidence_kind,
            "location": location,
            "object_ref": object_ref,
            "source_label": source_label,
        },
        "runtime_loaded": None,
        "related_name": None,
        "reason": reason,
        "error_code": reason if collection_state in {"invalid", "unavailable"} else None,
    }


def _attach_related_names(items: list[dict[str, Any]]) -> None:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        name = item.get("display_name")
        if item.get("object_type") != "skill" or not isinstance(name, str) or not name:
            item["related_name"] = None
            continue
        groups.setdefault(name, []).append(item)
    for name, group in groups.items():
        related = name if len(group) > 1 else None
        for item in group:
            item["related_name"] = related


def _local_location(row: dict[str, Any], records: list[dict[str, Any]], shared: dict[str, Any], home: str) -> str | None:
    local = row.get("local_source") if isinstance(row.get("local_source"), dict) else {}
    candidates: list[Any] = []
    for record in records:
        candidates.extend(record.get(key) for key in ("lexical", "path"))
    for entry in row.get("entries") or []:
        if isinstance(entry, dict):
            candidates.extend(entry.get(key) for key in ("lexical", "path"))
    candidates.extend((row.get("install_path"), shared.get("path"), local.get("path")))
    for candidate in candidates:
        location = _redact_location(candidate, home)
        if location:
            return location
    return None


def _local_scope(row: dict[str, Any], records: list[dict[str, Any]]) -> str:
    scopes = {record.get("scope") for record in records}
    for entry in row.get("entries") or []:
        if isinstance(entry, dict):
            scopes.add(entry.get("scope"))
    disk = {scope for scope in scopes if scope in {"repo_entry", "repo_source", "user", "other"}}
    if disk and disk <= {"repo_entry", "repo_source"}:
        return "repository"
    if "user" in disk or "other" in disk:
        return "shared"
    raw = row.get("scope")
    if raw == "repo":
        return "repository"
    if raw in {"machine", "plugin-managed", "vendor-managed"}:
        return "shared"
    if raw in SCOPES:
        return raw
    return "shared"


def _redact_location(path: Any, home: str) -> str | None:
    if not isinstance(path, str) or not path.strip():
        return None
    text = path.replace("\\", "/")
    if _SECRET_MARK.search(text):
        return "[redacted]"
    root = home.rstrip("/")
    if root and (text == root or text.startswith(root + "/")):
        text = "~" + text[len(root):]
    return text[:240]


def _display_name(row: dict[str, Any]) -> str:
    name = row.get("name")
    return name if isinstance(name, str) else ""


def _has_hash(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("content_hash"), str) and bool(value.get("content_hash"))


def _concrete(path: Any) -> bool:
    return isinstance(path, str) and bool(path.strip())


def _meta_source(skills: dict[str, Any], source_id: str) -> dict[str, Any] | None:
    meta = skills.get("meta") if isinstance(skills.get("meta"), dict) else {}
    for source in meta.get("sources") or []:
        if isinstance(source, dict) and source.get("id") == source_id:
            return source
    return None


def _grok_readable(source: dict[str, Any] | None) -> bool:
    if source is None:
        return True
    return source.get("state") == "ok"


def _grok_source_state(source: dict[str, Any] | None, has_items: bool) -> str:
    if isinstance(source, dict) and source.get("state") == "bad":
        return "invalid"
    if isinstance(source, dict) and source.get("state") == "absent":
        return "uncollected"
    if has_items or (isinstance(source, dict) and source.get("state") == "ok"):
        return "collected"
    return "uncollected"


def _generated_at(skills: dict[str, Any] | None, now: datetime | None) -> str:
    if isinstance(skills, dict) and isinstance(skills.get("generated_at"), str) and skills.get("generated_at"):
        return skills["generated_at"]
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _source_index(source_id: str) -> int:
    order = ("local", "repository", "multica_workspace", "grok_bot", "grok_cli", "memory")
    return order.index(source_id) if source_id in order else len(order)
