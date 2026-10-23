"""Reduce an agent's `mcp_config` to a secret-free summary (mcp-redesign §3.5).

Stored summaries hold display names, `exact` flags, the transport enum,
`broken_reason`, and env/headers set-flags with counts only. Raw names,
values, key names, URLs, commands, args, and cwd never leave memory.
Pure functions: no I/O, no logging of input content.
"""

from __future__ import annotations

from typing import Any

from .mcp_local import norm_transport_string, summarize_entry
from .redact import redact

MAX_SERVERS = 200
MAX_NAME_CHARS = 80
INVALID_MESSAGE = "mcp_config is not a JSON object"

# Keys allowed in the stored mcp_own_json summary (asserted before write).
SUMMARY_KEYS = frozenset({"state", "servers", "message"})
SUMMARY_SERVER_KEYS = frozenset({"name", "exact", "transport", "broken_reason", "env", "headers"})


def plain_text(value: Any) -> str | None:
    """Collapse whitespace, truncate, redact. Display only — never an identity."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())[:MAX_NAME_CHARS]
    if not text:
        return None
    return redact(text, limit=None) or None


def norm_library_transport(value: Any) -> str:
    """Normalize the CLI's library `transport` string into the enum."""
    return norm_transport_string(value)


def _merged(config: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    # `mcpServers` wins over the legacy `mcp` container on name collisions.
    for container_key in ("mcp", "mcpServers"):
        container = config.get(container_key)
        if not isinstance(container, dict):
            continue
        for name, entry in container.items():
            if isinstance(name, str):
                merged[name] = entry
    return merged


def summarize_mcp_config(config: Any, redacted: Any) -> dict[str, Any]:
    """Summarize an agent's own MCP config. Never echoes any part of the input."""
    if redacted is True:
        return {"state": "hidden", "servers": []}
    if config is None:
        return {"state": "none", "servers": []}
    if not isinstance(config, dict):
        return {"state": "invalid", "servers": [], "message": INVALID_MESSAGE}
    merged = _merged(config)
    servers: list[dict[str, Any]] = []
    for name in sorted(merged)[:MAX_SERVERS]:
        rec = summarize_entry(name, merged[name])
        display = plain_text(name)
        servers.append(
            {
                "name": display or "（空名字）",
                "exact": display == name,
                "transport": rec["transport"],
                "broken_reason": rec["broken_reason"],
                "env": rec["env"],
                "headers": rec["headers"],
            }
        )
    return {"state": "managed", "servers": servers}


def own_entry_records(config: Any) -> list[dict[str, Any]]:
    """Per-entry records (raw name + fingerprint inputs) in stored-summary order.

    In-memory only: used by the collector to fill OwnFingerprints, then dropped.
    """
    if not isinstance(config, dict):
        return []
    merged = _merged(config)
    return [summarize_entry(name, merged[name]) for name in sorted(merged)[:MAX_SERVERS]]
