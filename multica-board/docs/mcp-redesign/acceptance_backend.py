"""In-process backend for acceptance.py check 11.

Builds a temp HOME with fake MCP configs (identical pair, different env,
symlinked file, broken entry), seeds a real Store via Collector + the test
harness FakeMultica (no real multica CLI), then serves the real BoardServer
+ Views + McpContext on a loopback port. Only data is fake; all code is real.

Usage: python3 docs/mcp-redesign/acceptance_backend.py <port> <tmpdir>
Prints "READY <port>" on stdout once listening.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BOARD = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BOARD / "backend"))
sys.path.insert(0, str(BOARD / "backend" / "tests"))

from fake_cli import FakeMultica, agent, runtime
from multica_board.cli_runner import CliRunner
from multica_board.collector import Collector, CollectOptions
from multica_board.mcp_local import McpContext
from multica_board.server import BoardServer
from multica_board.store import Store
from multica_board.views import Views


def main() -> int:
    port = int(sys.argv[1])
    tmp = Path(sys.argv[2])
    home, data_dir, repo = tmp / "home", tmp / "data", tmp / "repo"
    (home / ".cursor").mkdir(parents=True)
    (home / ".codex").mkdir()
    data_dir.mkdir()
    repo.mkdir()

    # shared-srv identical in cursor + project file; codex has different env;
    # .claude.json is a symlink (must render ?, never be followed);
    # hermes yaml has a broken entry (no command/url).
    (home / ".cursor" / "mcp.json").write_text(json.dumps(
        {"mcpServers": {"shared-srv": {"command": "node", "args": ["srv.js"], "env": {"K1": "v1"}}}}))
    (home / ".codex" / "config.toml").write_text(
        'mcp_servers.shared-srv = { command = "node", args = ["srv.js"], env = { K2 = "v2" } }\n'
        'mcp_servers.only-codex = { command = "npx", args = ["x"] }\n')
    (home / "real-claude.json").write_text(json.dumps(
        {"mcpServers": {"shared-srv": {"command": "node"}}}))
    (home / ".claude.json").symlink_to(home / "real-claude.json")
    (home / ".hermes").mkdir()
    (home / ".hermes" / "config.yaml").write_text("mcp_servers:\n  broken-srv:\n")
    (repo / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"shared-srv": {"command": "node", "args": ["srv.js"], "env": {"K1": "v1"}}}}))

    data = {"w1": {
        "meta": {"id": "w1", "name": "Alpha Lab", "slug": "alpha-lab"},
        "issues": [], "runtimes": [runtime("w1")],
        "agents": [agent("w1", "ag1", mcp_config={"mcpServers": {"shared-srv": {"command": "node", "args": ["srv.js"], "env": {"K1": "v1"}}}})],
        "tasks": {},
        "mcp_servers": [{"id": "srv-shared", "workspace_id": "w1", "name": "shared-srv",
                         "transport": "stdio", "agent_count": 1, "created_at": None,
                         "updated_at": "2026-10-20T08:00:00Z"}],
        "agent_mcp": {"ag1": [{"id": "srv-shared", "name": "shared-srv", "enabled": True}]},
    }}
    store = Store(data_dir / "board.sqlite")
    runner = CliRunner(profile="acceptance", _exec=FakeMultica(data), _sleep=lambda s: None, retries=0)
    ctx = McpContext(home=home, repo_root=repo, data_dir=data_dir)
    Collector(runner, store, "acceptance", CollectOptions(all_workspaces=True), mcp_ctx=ctx).collect()
    views = Views(store, "acceptance", app_url="https://multica.ai", collect_interval_s=60, mcp_ctx=ctx)
    httpd = BoardServer("127.0.0.1", port, views, static_dir=BOARD / "web")
    print(f"READY {httpd.server_address[1]}", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
