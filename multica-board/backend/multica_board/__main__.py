"""Entry point: `python -m multica_board {workspaces,collect,collect-usage,serve}`."""

from __future__ import annotations

from .stdlib_path import prefer_stdlib_imports

prefer_stdlib_imports()

import argparse
import json
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from .cli_runner import CliError, CliRunner
from .collector import COLLECTOR, CollectOptions, Collector, iso, utcnow
from .redact import redact
from .server import authority, bind_servers, listen_hosts, split_values
from .store import Store
from .usage import DEFAULT_CODEXBAR, HISTORY_RETENTION_DAYS, UsageCollector, paths_beside
from .views import Views
from .runtime_plan import DEFAULT_PLAN_DIR

DEFAULT_PROFILE = "desktop-api.multica.ai"
DEFAULT_DB = Path.home() / ".multica-board" / "board.sqlite"
DEFAULT_STATIC = Path(__file__).resolve().parents[2] / "web"

log = logging.getLogger("multica_board")


def _open_store(path: Path) -> Store:
    # Only the board's own default directory is tightened; a user-chosen parent (e.g. /tmp) is left alone.
    if path.parent == DEFAULT_DB.parent and path.parent.is_dir():
        os.chmod(path.parent, 0o700)
    return Store(path)


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--profile", default=DEFAULT_PROFILE, help=f"multica CLI profile (default {DEFAULT_PROFILE})")
    p.add_argument("--multica-bin", default="multica", help="path to the multica CLI")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"SQLite cache path (default {DEFAULT_DB})")
    p.add_argument("-v", "--verbose", action="store_true")


def _collect_args(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--workspace", action="append", default=[], metavar="ID_OR_SLUG",
                   help="workspace allow-list entry (repeatable)")
    g.add_argument("--all-workspaces", action="store_true", help="collect every workspace this identity can see")
    p.add_argument("--run-days", type=int, default=14, help="run history window in days (default 14)")
    p.add_argument("--max-task-pages", type=int, default=10, help="max 200-run pages per agent per cycle")
    p.add_argument("--concurrency", type=int, default=3, help="workspaces (and agents per workspace) in parallel")
    p.add_argument("--mcp-every", type=int, default=5, help="re-read agent MCP bindings every N cycles (default 5)")
    p.add_argument("--max-cli", type=int, default=4, help="hard cap on concurrent multica CLI processes (default 4)")
    p.add_argument("--timeout", type=float, default=60.0, help="per CLI call timeout seconds")


def _runner(a: argparse.Namespace) -> CliRunner:
    return CliRunner(
        profile=a.profile,
        binary=a.multica_bin,
        timeout_s=getattr(a, "timeout", 60.0),
        max_parallel=getattr(a, "max_cli", 4),
        task_scoped=getattr(a, "task_scoped", False) is True,
    )


def _opts(a: argparse.Namespace, full: bool) -> CollectOptions:
    return CollectOptions(
        workspaces=a.workspace,
        all_workspaces=a.all_workspaces,
        run_days=a.run_days,
        max_task_pages=a.max_task_pages,
        full=full,
        concurrency=a.concurrency,
        mcp_every=getattr(a, "mcp_every", 5),
    )


def cmd_workspaces(a: argparse.Namespace) -> int:
    try:
        res = _runner(a).run(["workspace", "list"])
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for w in res.data or []:
        print(f"{w.get('id')}  {w.get('slug') or '-':<24} {w.get('name') or ''}")
    return 0


def cmd_collect(a: argparse.Namespace) -> int:
    store = _open_store(a.db)
    from .mcp_local import McpContext, REPO_ROOT
    mcp_ctx = McpContext(home=Path.home(), repo_root=REPO_ROOT, data_dir=a.db.parent)
    result = Collector(_runner(a), store, a.profile, _opts(a, full=a.full), mcp_ctx=mcp_ctx).collect()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


def cmd_collect_usage(a: argparse.Namespace) -> int:
    store = _open_store(a.db)
    try:
        snapshot, history = paths_beside(a.db)
        result = UsageCollector(
            _runner(a),
            store,
            a.profile,
            _opts(a, full=False),
            codexbar_bin=a.codexbar_bin,
            usage_days=a.usage_days,
            snapshot_path=snapshot,
            history_path=history,
            history_days=a.history_days,
        ).collect()
    except Exception as exc:
        print(f"error: {redact(str(exc))}", file=sys.stderr)
        return 1
    finally:
        store.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


def cmd_serve(a: argparse.Namespace) -> int:
    if not 0 <= a.port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    hosts = listen_hosts(a.host)
    allowed = [authority(value) for value in split_values(a.allowed_host, [])] if a.allowed_host else []
    # Best-effort display metadata only. Preserve the collector's profile routing
    # and allow offline/cache-only startup when identity cannot be resolved.
    runner = _runner(a)
    try:
        origin = runner.run(["auth", "status"]).data["server_identity"]
    except (CliError, KeyError, TypeError):
        origin = None
    store = _open_store(a.db)
    from .mcp_local import McpContext, REPO_ROOT
    mcp_ctx = McpContext(home=Path.home(), repo_root=REPO_ROOT, data_dir=a.db.parent)
    views = Views(store, a.profile, app_url=a.app_url, collect_interval_s=a.refresh,
                  server_identity=origin, mcp_every=a.mcp_every, mcp_ctx=mcp_ctx)
    static = a.static_dir if a.static_dir and a.static_dir.is_dir() else None
    actions = None
    journal = None
    try:
        if getattr(a, "write_policy", None):
            from .write_actions import Journal, RuntimeActions, load_policy, validate_task_scope
            from .write_adapter import MulticaWriteAdapter
            if any(host not in {"127.0.0.1", "::1"} for host in hosts):
                raise ValueError("runtime writes require loopback-only listeners")
            policy = load_policy(a.write_policy, a.profile)
            if getattr(a, "task_scoped", False) is True:
                validate_task_scope(policy, a.workspace, a.all_workspaces)
            journal = Journal(a.db.with_name(a.db.stem + "-write-audit.jsonl"))
            actions = RuntimeActions(MulticaWriteAdapter(runner, policy), policy, journal)
        servers = bind_servers(hosts, a.port, views, static_dir=static, plan_dir=a.plan_dir,
                               allowed_hosts=allowed, runtime_actions=actions)
    except Exception:
        store.close()
        if journal:
            journal.close()
        raise
    stop = threading.Event()

    def refresher() -> None:
        cycle = 0
        while not stop.is_set():
            full = cycle % max(1, a.full_every) == 0
            try:
                res = Collector(_runner(a), store, a.profile, _opts(a, full=full), mcp_ctx=mcp_ctx).collect()
                log.info("collect cycle %d (%s) ok=%s", cycle, "full" if full else "incremental", res.get("ok"))
            except Exception as exc:
                log.exception("collect cycle failed")
                try:
                    store.record_sync(a.profile, *COLLECTOR, at=iso(utcnow()), ok=False,
                                      error=redact(f"collect cycle crashed: {type(exc).__name__}: {exc}"),
                                      error_kind="unknown")
                except Exception:
                    log.exception("could not record collector failure")
            cycle += 1
            stop.wait(a.refresh)

    if a.refresh > 0:
        threading.Thread(target=refresher, name="collector", daemon=True).start()

    def _shutdown(*_: object) -> None:
        stop.set()
        for httpd in servers:
            threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    threads = [threading.Thread(target=httpd.serve_forever, name="http", daemon=True)
               for httpd in servers]
    try:
        for httpd, thread in zip(servers, threads):
            thread.start()
            host, port = httpd.server_address[:2]
            shown = f"[{host}]" if ":" in host else host
            log.info("serving board on http://%s:%s (runtime writes: %s; static: %s)", shown, port, actions is not None, static or "none")
            print(f"multica-board listening on http://{shown}:{port}", flush=True)
        for thread in threads:
            thread.join()
    finally:
        stop.set()
        for httpd in servers:
            httpd.server_close()
        store.close()
        if journal:
            journal.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)  # cache, WAL and logs are owner-only
    p = argparse.ArgumentParser(prog="multica-board", description="Read-only cross-workspace Multica board backend")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("workspaces", help="list workspaces visible to the CLI identity")
    _common(w)

    c = sub.add_parser("collect", help="run one collection cycle into the SQLite cache")
    _common(c)
    _collect_args(c)
    c.add_argument("--full", action="store_true", help="page through the full run window, ignoring the incremental stop")

    u = sub.add_parser("collect-usage", help="write the CodexBar + Multica usage snapshot (does not install launchd)")
    _common(u)
    _collect_args(u)
    u.add_argument("--usage-days", type=int, default=14, help="Multica runtime usage window in days (default 14)")
    u.add_argument("--codexbar-bin", default=DEFAULT_CODEXBAR, help=f"codexbar binary (default {DEFAULT_CODEXBAR})")
    u.add_argument("--history-days", type=int, default=HISTORY_RETENTION_DAYS, help="usage history retention in days (default 90)")

    s = sub.add_parser("serve", help="serve the read-only API and refresh in the background")
    _common(s)
    _collect_args(s)
    s.add_argument("--host", action="append", help="numeric listen IP; repeat or comma-separate (default 127.0.0.1)")
    s.add_argument("--allowed-host", action="append", help="exact host[:port] to allow in Host and Origin; repeat or comma-separate; omitted port uses listen port")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--refresh", type=int, default=60, help="seconds between collect cycles; 0 serves cache only")
    s.add_argument("--full-every", type=int, default=30, help="every Nth cycle is a full run-history pass")
    s.add_argument("--app-url", default="https://multica.ai", help="web app base URL for deep links")
    s.add_argument("--static-dir", type=Path, default=DEFAULT_STATIC, help="frontend directory served at / (default ../web)")
    s.add_argument("--task-scoped", action="store_true", help="use the current injected task CLI identity, without Owner fallback")
    s.add_argument("--write-policy", type=Path, help="opt-in owner-private runtime write policy; loopback only")
    s.add_argument("--plan-dir", type=Path, default=DEFAULT_PLAN_DIR, help="private G1 plan directory (read-only HTTP)")

    from .metrics_cli import register
    register(sub, _common)

    a = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(a, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        if a.cmd.startswith("metrics"):
            from .metrics_cli import command
            return command(a, _runner)
        return {
            "workspaces": cmd_workspaces,
            "collect": cmd_collect,
            "collect-usage": cmd_collect_usage,
            "serve": cmd_serve,
        }[a.cmd](a)
    except (ValueError, CliError, OSError) as exc:
        print(f"error: {redact(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
