"""Read-only per-skill registration, separate from claims of runtime loading."""
from __future__ import annotations

from pathlib import Path

from .skill_content import read_content


def registration_fields(document: dict, *, repo_root: Path) -> dict:
    """Enrich a fresh API document; never promote disk presence to load proof.

    Local IDs are inventory names; source-specific rows retain their existing
    keys. Hashes cover raw SKILL.md bytes, never folder/baseline hashes.
    """
    for item in document.get("items") or []:
        item["id"] = item.get("key") or item["name"]
        canonical = item.get("canonical_path")
        selected = canonical or (item.get("local_source") or {}).get("path")
        if not selected:
            selected = next((entry.get("path") for entry in item.get("entries") or []
                             if entry.get("present") and entry.get("path")), None)
        item["canonical_path"] = canonical or selected
        item["body_sha256"] = None
        item["body_hash_path"] = selected
        item["body_hash_state"] = "uncollected"
        if item.get("source_class") == "grok_bot_export":
            from .skill_grok_export import read_export_content
            selected = (item.get("export") or {}).get("path")
            body = read_export_content(name=item["name"], path=selected or "")
            item["body_hash_path"] = selected
        elif item.get("origin") == "grok_bot":
            body = {"state": "not_applicable"}
        elif selected:
            body = read_content({"items": [item]}, name=item["name"], path=selected, repo_root=repo_root)
        else:
            body = {"state": "uncollected"}
        item["body_hash_state"] = "observed" if body.get("ok") else body["state"]
        if body.get("ok"):
            item["body_sha256"] = body["sha256"]

        loaders = []
        for provider, evidence in sorted((item.get("runtime_evidence") or {}).items()):
            loaded = evidence.get("runtime_loaded")
            shared = evidence.get("mark") == "shared" or any(
                entry.get("harness") == provider and entry.get("attribution") == "shared"
                and entry.get("state") == "present" for entry in item.get("evidence") or [])
            loaders.append({
                "id": provider, "kind": "runtime", "mark": evidence.get("mark"),
                "shared_auto_load": shared,
                "runtime_loaded": loaded,
                "load_state": "loaded" if loaded is True else "not_loaded" if loaded is False else "unverified",
                "source": evidence.get("load_source") or evidence.get("source"),
                "collected_at": evidence.get("collected_at"),
            })
        for bot in item.get("grok_bots") or []:
            loaders.append({"id": bot["id"], "kind": "grok_bot", "mark": "declared",
                            "shared_auto_load": False, "runtime_loaded": None,
                            "load_state": "unverified", "source": item.get("source_class"),
                            "collected_at": None})
        for install in item.get("workspace_installs") or []:
            for agent in install.get("agents") or []:
                loaders.append({"id": agent["id"], "workspace_id": install.get("workspace_id"),
                                "skill_id": install.get("id"), "enabled": agent.get("enabled"),
                                "kind": "workspace_agent", "mark": "declared", "shared_auto_load": False,
                                "runtime_loaded": None, "load_state": "unverified",
                                "source": "workspace_installs", "collected_at": None})
        item["loaders"] = loaders
    return document
