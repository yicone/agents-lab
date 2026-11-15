"""Settings fingerprints plus explicit turn visibility placeholders."""
from ..hashing import config_fingerprint
from .common import json_file, label


def turn_placeholder(bot):
    return {"id": "grok-mcp:" + bot + "/not-collected", "kind": "bot_turn",
            "bot_id": bot, "name": "", "status": None, "evidence_level": "not_collected"}


def collect(ctx):
    # settings.json has an independently measured path. The unmeasured mcps
    # root remains unregistered, even when settings or Sidecars supply evidence.
    data, read_state = json_file(ctx.settings.parent, ctx.settings.name, ctx.policy) if ctx.settings else (None, "not_collected")
    rows = list(ctx.multica_mcps)
    state = "ok" if read_state == "observed" else "partial"
    if data is not None:
        servers = data.get("mcpBoxServers")
        if not isinstance(servers, list):
            state = "partial"
        else:
            for server in servers:
                name = label(server.get("name"), 128) if isinstance(server, dict) else None
                if name is None:
                    state = "partial"
                    continue
                rows.append({"id": "grok-mcp-setting:" + name, "kind": "box_setting", "name": name,
                             "config_fingerprint": config_fingerprint(server), "evidence_level": "observed"})
    rows.extend(turn_placeholder(bot) for bot in sorted(ctx.bots))
    return rows, state
