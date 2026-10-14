"""Entry point: `python -m multica_board {workspaces,collect,serve}`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from .cli_runner import CliError, CliRunner
from .collector import CollectOptions, Collector
from .server import BoardServer
from .store import Store
from .views import Views

DEFAULT_PROFILE = "desktop-api.multica.ai"
DEFAULT_DB = Path.home() / ".multica-board" / "board.sqlite"
DEFAULT_STATIC = Path(__file__).resolve().parents[2] / "web"

log = logging.getLogger("multica_board")


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
    p.add_argument("--max-cli", type=int, default=4, help="hard cap on concurrent multica CLI processes (default 4)")
    p.add_argument("--timeout", type=float, default=60.0, help="per CLI call timeout seconds")


def _runner(a: argparse.Namespace) -> CliRunner:
    return CliRunner(
        profile=a.profile,
        binary=a.multica_bin,
        timeout_s=getattr(a, "timeout", 60.0),
        max_parallel=getattr(a, "max_cli", 4),
    )


def _opts(a: argparse.Namespace, full: bool) -> CollectOptions:
    return CollectOptions(
        workspaces=a.workspace,
        all_workspaces=a.all_workspaces,
        run_days=a.run_days,
        max_task_pages=a.max_task_pages,
        full=full,
        concurrency=a.concurrency,
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
    store = Store(a.db)
    result = Collector(_runner(a), store, a.profile, _opts(a, full=a.full)).collect()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


def cmd_serve(a: argparse.Namespace) -> int:
    store = Store(a.db)
    views = Views(store, a.profile, app_url=a.app_url, collect_interval_s=a.refresh)
    static = a.static_dir if a.static_dir and a.static_dir.is_dir() else None
    httpd = BoardServer(a.host, a.port, views, static_dir=static)
    stop = threading.Event()

    def refresher() -> None:
        cycle = 0
        while not stop.is_set():
            full = cycle % max(1, a.full_every) == 0
            try:
                res = Collector(_runner(a), store, a.profile, _opts(a, full=full)).collect()
                log.info("collect cycle %d (%s) ok=%s", cycle, "full" if full else "incremental", res.get("ok"))
            except Exception:
                log.exception("collect cycle failed")
            cycle += 1
            stop.wait(a.refresh)

    if a.refresh > 0:
        threading.Thread(target=refresher, name="collector", daemon=True).start()

    def _shutdown(*_: object) -> None:
        stop.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    host, port = httpd.server_address[:2]
    shown = f"[{host}]" if ":" in host else host
    log.info("serving read-only board on http://%s:%s (static: %s)", shown, port, static or "none")
    print(f"multica-board listening on http://{shown}:{port}", flush=True)
    httpd.serve_forever()
    httpd.server_close()
    store.close()
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

    s = sub.add_parser("serve", help="serve the read-only API on loopback and refresh in the background")
    _common(s)
    _collect_args(s)
    s.add_argument("--host", default="127.0.0.1", help="127.0.0.1 (default) or ::1; anything else is refused")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--refresh", type=int, default=60, help="seconds between collect cycles; 0 serves cache only")
    s.add_argument("--full-every", type=int, default=30, help="every Nth cycle is a full run-history pass")
    s.add_argument("--app-url", default="https://multica.ai", help="web app base URL for deep links")
    s.add_argument("--static-dir", type=Path, default=DEFAULT_STATIC, help="frontend directory served at / (default ../web)")

    a = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        return {"workspaces": cmd_workspaces, "collect": cmd_collect, "serve": cmd_serve}[a.cmd](a)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
