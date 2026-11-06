#!/usr/bin/env python3
"""Install the controlled Multica status bridge onto this machine.

Default is dry-run: print the planned copies and backups, write nothing.
Pass --apply to copy. Existing files are copied aside first. The script does
not call launchctl, does not read Keychain, and does not print file contents.
"""
from __future__ import annotations

import argparse
import filecmp
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODULES = (
    "outbox.py",
    "netfail.py",
    "creds.py",
    "rerun_capacity.py",
    "agent_delivery.py",
)
BIN_NAME = "multica-status-bridge.py"
ROUTES_NAME = "routes.json"
URL_RE = re.compile(r"https?://", re.I)
BEARER_RE = re.compile(r"bearer\s+[A-Za-z0-9]", re.I)


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _destinations(args):
    home = Path.home()
    return {
        "bin": Path(args.bin_dir) if args.bin_dir else home / ".local/bin",
        "modules": Path(args.module_dir) if args.module_dir else home / ".local/share/multica-bridge",
        "config": Path(args.config_dir) if args.config_dir else home / ".config/multica-bridge",
    }


def assert_routes_safe(path: Path):
    text = path.read_text(encoding="utf-8")
    if URL_RE.search(text) or BEARER_RE.search(text):
        raise SystemExit("refusing install: routes file failed the secret scan")


def _plan(source: Path):
    jobs = [(source / BIN_NAME, BIN_NAME, "bin")]
    for name in MODULES:
        jobs.append((source / name, name, "modules"))
    jobs.append((source / ROUTES_NAME, ROUTES_NAME, "config"))
    return jobs


def _backup_name(dest: Path):
    candidate = dest.with_name(dest.name + ".bak-" + _stamp())
    n = 1
    while candidate.exists():
        candidate = dest.with_name(dest.name + f".bak-{_stamp()}-{n}")
        n += 1
    return candidate


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sync the controlled status bridge. Default: dry-run.")
    parser.add_argument("--apply", action="store_true", help="copy files; existing files are backed up first")
    parser.add_argument("--dry-run", action="store_true", help="print actions only (this is also the default)")
    parser.add_argument("--source", default=str(ROOT))
    parser.add_argument("--bin-dir")
    parser.add_argument("--module-dir")
    parser.add_argument("--config-dir")
    args = parser.parse_args(argv)
    apply = bool(args.apply) and not args.dry_run
    source = Path(args.source).resolve()
    dests = _destinations(args)
    routes = source / ROUTES_NAME
    if not routes.is_file():
        raise SystemExit("refusing install: routes.json is missing")
    assert_routes_safe(routes)
    for src, _name, _kind in _plan(source):
        if not src.is_file():
            raise SystemExit(f"refusing install: missing {src.name}")

    mode = "apply" if apply else "dry-run"
    print(mode)
    for src, name, kind in _plan(source):
        dest = dests[kind] / name
        if dest.exists():
            backup = _backup_name(dest)
            print(f"backup {dest.name} -> {backup.name}")
            if apply:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, backup)
        else:
            print(f"create {dest.name}")
        print(f"install {src.name} -> {dest.name}")
        if apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            if not filecmp.cmp(src, dest, shallow=False):
                raise SystemExit(f"refusing install: copy check failed for {src.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
