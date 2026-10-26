"""Runtime columns describe installation evidence.

Load evidence is optional and never inferred from disk presence. Codex and
Hermes can mark an explicit config opt-out as not loaded; everything else
stays unverified.
"""
from __future__ import annotations

from copy import deepcopy

from .runtime_load import blocked_item_ids

_PROBE_KEYS = ("state", "source", "credibility", "detail", "collected_at")
_PROBE_LABELS = {"codex": "Codex", "cursor": "Cursor", "hermes": "Hermes"}
_OPT_OUT_DETAIL = {
    "codex": "Codex skills.config 将该技能标为 enabled=false。",
    "hermes": "Hermes skills.disabled 含有该名称。",
}


def runtime_columns(document: dict, runtime_document: dict, roots: list[dict], load_evidence: dict | None = None) -> dict:
    # skills_document is cached; adding live cache data must not mutate it.
    result = deepcopy(document)
    columns: dict[str, dict] = {}

    def add(provider, label, source, collected_at, evidence=None):
        key = provider.strip().lower() if isinstance(provider, str) and provider.strip() else "unknown"
        column = columns.setdefault(key, {"id": key, "provider": key, "label": label or key,
                                          "sources": [], "runtime_ids": [], "runtime_loaded": None})
        record = {"source": source, "collected_at": collected_at,
                  "evidence_state": "unknown" if key == "unknown" else "observed"}
        if record not in column["sources"]:
            column["sources"].append(record)
        if evidence and evidence not in column["runtime_ids"]:
            column["runtime_ids"].append(evidence)
        return column

    for tool in result.get("tools") or []:
        add(tool["id"], tool.get("label"), "tool_roots", document.get("generated_at"))
    for runtime in runtime_document.get("items") or []:
        add(runtime.get("provider"), None, "runtimes", runtime_document.get("generated_at"),
            {"workspace_id": runtime.get("workspace_id"), "id": runtime.get("id")})
    for item in result.get("items") or []:
        for record in item.get("evidence") or []:
            if record.get("harness"):
                add(record["harness"], None, "skill_evidence", document.get("generated_at"))
    probes = load_evidence or {}
    for provider in probes:
        if provider not in columns:
            columns[provider] = {"id": provider, "provider": provider, "label": _PROBE_LABELS.get(provider, provider),
                                 "sources": [], "runtime_ids": [], "runtime_loaded": None}
    items = result.get("items") or []
    blocked = {provider: blocked_item_ids(items, probe) for provider, probe in probes.items()}
    for column in columns.values():
        configured = [root for root in roots if column["id"] in root.get("tools", [])]
        column["roots_state"] = "configured" if configured else "unconfigured"
        column["roots"] = [{"path": root["path"], "kind": root["kind"]} for root in configured]
        probe = probes.get(column["id"])
        if probe:
            column["load_probe"] = {key: probe.get(key) for key in _PROBE_KEYS}
        for item in items:
            mark = item.get("tools", {}).get(column["id"])
            local = bool(item.get("entries") or item.get("local_source"))
            name_only = item.get("origin") == "grok_bot"
            cloud_only = item.get("origin") == "cloud" and not local
            evidence = {
                "mark": mark,
                "state": ("not_applicable" if name_only or cloud_only else "unknown" if mark == "unknown" or not local else
                          "observed" if mark else "no_match" if configured else "unknown"),
                "reason": "grok_bot_names_only" if name_only else "cloud_only" if cloud_only else "local_installation_unobserved" if not local else
                          None if configured else "skill_roots_unconfigured",
                "source": "disk_installations", "collected_at": document.get("generated_at"),
                "runtime_loaded": None,
            }
            if probe and evidence["state"] != "not_applicable":
                evidence["load_checked"] = True
                evidence["load_source"] = probe.get("source")
                if id(item) in blocked.get(column["id"], ()):
                    evidence["runtime_loaded"] = False
                    evidence["load_detail"] = _OPT_OUT_DETAIL.get(column["id"], "配置显式禁用。")
            item.setdefault("runtime_evidence", {})[column["id"]] = evidence
    result["tools"] = list(columns.values())
    result["tool_filter_ids"] = list(columns)
    return result
