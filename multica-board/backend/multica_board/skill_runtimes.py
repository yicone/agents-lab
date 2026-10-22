"""Runtime columns describe installation evidence, never session loading."""
from __future__ import annotations

from copy import deepcopy


def runtime_columns(document: dict, runtime_document: dict, roots: list[dict]) -> dict:
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
    for column in columns.values():
        configured = [root for root in roots if column["id"] in root.get("tools", [])]
        column["roots_state"] = "configured" if configured else "unconfigured"
        column["roots"] = [{"path": root["path"], "kind": root["kind"]} for root in configured]
        for item in result.get("items") or []:
            mark = item.get("tools", {}).get(column["id"])
            local = bool(item.get("entries") or item.get("local_source"))
            cloud_only = item.get("origin") == "cloud" and not local
            item.setdefault("runtime_evidence", {})[column["id"]] = {
                "mark": mark,
                "state": ("not_applicable" if cloud_only else "unknown" if mark == "unknown" or not local else
                          "observed" if mark else "no_match" if configured else "unknown"),
                "reason": "cloud_only" if cloud_only else "local_installation_unobserved" if not local else
                          None if configured else "skill_roots_unconfigured",
                "source": "disk_installations", "collected_at": document.get("generated_at"),
                "runtime_loaded": None,
            }
    result["tools"] = list(columns.values())
    result["tool_filter_ids"] = list(columns)
    return result
