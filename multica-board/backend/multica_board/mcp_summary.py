"""Reduce an agent's `mcp_config` to a secret-free summary.

Only names, transports, and env/headers set-flags with counts survive.
Values, key names, URLs, commands, args, and cwd never leave this module.
Pure functions: no I/O, no logging of input content.
"""

from __future__ import annotations

from typing import Any

from .redact import redact

MAX_SERVERS = 200
MAX_TRANSPORT_CHARS = 32
MAX_NAME_CHARS = 80
INVALID_MESSAGE = "mcp_config is not a JSON object"

_TRANSPORT_ALIASES = {
    "local": "stdio",
    "stdio": "stdio",
    "remote": "http",
    "http": "http",
    "streamable-http": "http",
    "sse": "sse",
}


def plain_text(value: Any) -> str | None:
    """Collapse whitespace, truncate, redact. Non-strings are dropped."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())[:MAX_NAME_CHARS]
    if not text:
        return None
    return redact(text, limit=None) or None


def _norm_explicit(value: Any) -> str | None:
    """Normalize an explicit `type`/`transport` string; None when absent or empty."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in _TRANSPORT_ALIASES:
        return _TRANSPORT_ALIASES[lowered]
    # Unrecognized explicit values are kept verbatim (truncated, redacted).
    return redact(text[:MAX_TRANSPORT_CHARS], limit=None) or "unknown"


def transport_of(entry: Any) -> str:
    """Mirror Multica `mcpTransportOf` for an own-config server entry."""
    if not isinstance(entry, dict):
        return "unknown"
    explicit = _norm_explicit(entry.get("type"))
    if explicit is not None:
        return explicit
    if isinstance(entry.get("command"), str) and entry["command"].strip():
        return "stdio"
    if isinstance(entry.get("url"), str) and entry["url"].strip():
        return "http"
    return "unknown"


def norm_library_transport(value: Any) -> str:
    """Normalize the CLI's library `transport` string."""
    return _norm_explicit(value) or "unknown"


def _flag_count(value: Any) -> dict[str, Any]:
    """`set`/`count` for an env or headers object. Key names and values are never read out."""
    if isinstance(value, dict):
        return {"set": len(value) > 0, "count": len(value)}
    return {"set": False, "count": 0}


def summarize_mcp_config(config: Any, redacted: Any) -> dict[str, Any]:
    """Summarize an agent's own MCP config. Never echoes any part of the input."""
    if redacted is True:
        return {"state": "hidden", "servers": []}
    if config is None:
        return {"state": "none", "servers": []}
    if not isinstance(config, dict):
        return {"state": "invalid", "servers": [], "message": INVALID_MESSAGE}
    merged: dict[str, Any] = {}
    # `mcpServers` wins over the legacy `mcp` container on name collisions.
    for container_key in ("mcp", "mcpServers"):
        container = config.get(container_key)
        if not isinstance(container, dict):
            continue
        for name, entry in container.items():
            if isinstance(name, str):
                merged[name] = entry
    servers: list[dict[str, Any]] = []
    for name in sorted(merged)[:MAX_SERVERS]:
        entry = merged[name]
        if isinstance(entry, dict):
            servers.append(
                {
                    "name": plain_text(name) or "?",
                    "transport": transport_of(entry),
                    "env": _flag_count(entry.get("env")),
                    "headers": _flag_count(entry.get("headers")),
                }
            )
        else:
            servers.append(
                {
                    "name": plain_text(name) or "?",
                    "transport": "unknown",
                    "env": {"set": False, "count": 0},
                    "headers": {"set": False, "count": 0},
                }
            )
    return {"state": "managed", "servers": servers}
