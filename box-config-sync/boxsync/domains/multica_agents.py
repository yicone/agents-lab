"""Agent instructions are hashed; configuration values are dropped."""
from ..hashing import config_fingerprint, instructions_sha256
from ..multica_cli import CliError
from .common import UUID, label, safe_timestamp


def records(value, key):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        if value.get("next_cursor") or value.get("has_more") is True:
            raise CliError()
        result = value.get(key, value.get("data"))
        if isinstance(result, list):
            return result
    raise CliError()


def assert_scope(value, workspace):
    if not isinstance(value, dict) or value.get("workspace_id", workspace) != workspace:
        raise CliError()


def collect(ctx):
    if ctx.offline:
        return [], "not_collected"
    rows = []
    state = "ok"
    for workspace in ctx.workspaces:
        try:
            agents = records(ctx.cli.run(["agent", "list"], workspace), "agents")
            workspace_rows, mcp_rows = [], []
            for item in agents:
                agent = item.get("id") if isinstance(item, dict) else None
                if not isinstance(agent, str) or not UUID.fullmatch(agent):
                    raise CliError()
                assert_scope(item, workspace)
                data = ctx.cli.run(["agent", "get", agent], workspace)
                assert_scope(data, workspace)
                if data.get("id") != agent:
                    raise CliError()
                # Only the fixed allowlist below can leave this function.
                workspace_rows.append({"id": "multica-agent:" + workspace + "/" + agent,
                                       "workspace_id": workspace, "agent_id": agent,
                                       "name": label(data.get("name")),
                                       "instructions_sha256": instructions_sha256(data.get("instructions")),
                                       "updated_at": safe_timestamp(data.get("updated_at")),
                                       "archived": data.get("archived_at") is not None,
                                       "evidence_level": "observed"})
                config = data.get("mcp_config")
                own = {}
                if isinstance(config, dict) and data.get("mcp_config_redacted") is not True:
                    for container in ("mcp", "mcpServers"):
                        if isinstance(config.get(container), dict):
                            own.update(config[container])
                mcps = records(ctx.cli.run(["agent", "mcp", "list", agent], workspace), "servers")
                for mcp in mcps:
                    assert_scope(mcp, workspace)
                    server_id, name = label(mcp.get("id", mcp.get("server_id")), 128), label(mcp.get("name"), 128)
                    if server_id is None or name is None:
                        raise CliError()
                    mcp_rows.append({"id": "multica-agent-mcp:" + workspace + "/" + agent + "/" + server_id,
                                     "kind": "multica_agent", "workspace_id": workspace, "agent_id": agent,
                                     "name": name, "enabled": mcp.get("enabled") if type(mcp.get("enabled")) is bool else None,
                                     "config_fingerprint": config_fingerprint(own[name]) if name in own else None,
                                     "evidence_level": "observed"})
            rows.extend(workspace_rows)
            ctx.multica_mcps.extend(mcp_rows)
        except CliError as exc:
            ctx.inaccessible(workspace, exc.reason)
            state = "partial"
    return rows, state
