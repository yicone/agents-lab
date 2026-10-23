"""Whether each runtime provider loads Multica-managed MCP servers.

Source-confirmed against multica v0.6.5 `packages/core/agents/mcp-support.ts`
(MCP_SUPPORTED_PROVIDERS) plus the per-provider pass-through code paths.
Re-verify this table whenever the Multica CLI version changes.
"""

from __future__ import annotations

from typing import Any

_UNVERIFIED = {"loads": "yes", "channel": None, "native_merge": "unknown", "remote_filter": False,
               "note": "backend_unverified"}

SUPPORT: dict[str, Any] = {
    "source": "multica v0.6.5 packages/core/agents/mcp-support.ts",
    "providers": {
        # Verified entries (see docs/mcp-design.md).
        "codex": {"loads": "yes", "channel": "codex_home_config", "native_merge": "yes",
                  "remote_filter": False, "note": None},
        "hermes": {"loads": "yes", "channel": "acp_session", "native_merge": "unknown",
                   "remote_filter": True, "note": None},
        "cursor": {"loads": "yes", "channel": "workdir_cursor_mcp_json", "native_merge": "yes",
                   "remote_filter": False, "note": "oauth_needs_auth_source"},
        "mcode": {"loads": "yes", "channel": "acp_session", "native_merge": "unknown",
                  "remote_filter": True, "note": None},
        "claude": {"loads": "yes", "channel": "claude_mcp_config_file", "native_merge": "yes",
                   "remote_filter": False, "note": None},
        "grok": {"loads": "yes", "channel": "acp_session", "native_merge": "unknown",
                "remote_filter": True, "note": None},
        "opencode": {"loads": "conditional", "channel": "opencode_config_content", "native_merge": "yes",
                     "remote_filter": False, "note": "v2_task_fails"},
        "omp": {"loads": "yes", "channel": "workdir_config", "native_merge": "no",
                "remote_filter": False, "note": None},
        # Explicitly not in MCP_SUPPORTED_PROVIDERS.
        "antigravity": {"loads": "no", "channel": None, "native_merge": None,
                        "remote_filter": False, "note": None},
        "copilot": {"loads": "no", "channel": None, "native_merge": None,
                    "remote_filter": False, "note": None},
        # Remaining MCP_SUPPORTED_PROVIDERS members; backend pass-through not individually verified.
        "codebuddy": dict(_UNVERIFIED),
        "codearts": dict(_UNVERIFIED),
        "kimi": dict(_UNVERIFIED),
        "reasonix": dict(_UNVERIFIED),
        "dsh": dict(_UNVERIFIED),
        "kiro": dict(_UNVERIFIED),
        "openclaw": dict(_UNVERIFIED),
        "pi": dict(_UNVERIFIED),
        "qoder": dict(_UNVERIFIED),
        "qoderclicn": dict(_UNVERIFIED),
        "qwen": dict(_UNVERIFIED),
        "qwenpaw": dict(_UNVERIFIED),
        "traecli": dict(_UNVERIFIED),
        "dim": dict(_UNVERIFIED),
    },
}


def runtime_loads(provider: str | None, bound: bool) -> str:
    """`yes`/`no`/`conditional`/`unknown` for a bound runtime; `unbound` without one."""
    if not bound:
        return "unbound"
    if not isinstance(provider, str):
        return "unknown"
    entry = SUPPORT["providers"].get(provider.lower())
    return entry["loads"] if entry else "unknown"
