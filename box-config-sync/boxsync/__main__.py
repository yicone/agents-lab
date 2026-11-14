"""Metadata-only discovery. No subprocesses, content reads, or state writes."""
import argparse
import json
import os
import stat
from pathlib import Path

from .policy import BLACKLIST_VERSION, MAX_TREE_ENTRIES, Policy
from .roots import BOX_ROOTS, BOX_STATE_DIR, fixture_roots
from .safe_io import UnsafeIO, entries, open_directory


def discover(roots=None, policy=None):
    roots = BOX_ROOTS if roots is None else roots
    policy = policy or Policy()
    domains = {}
    for alias, root in roots.items():
        counts = {"files": 0, "directories": 0, "unsupported": 0}
        if root is None:
            domains[alias] = {"state": "not_collected", "counts": counts}
            continue
        visited = 0

        def walk(fd, parent="", depth=0):
            nonlocal visited
            if depth > 64:
                raise UnsafeIO("unknown")
            for entry in entries(fd, policy, parent):
                visited += 1
                if visited > MAX_TREE_ENTRIES:
                    raise UnsafeIO("unknown")
                # File names are never emitted; even ordinary names can be secrets.
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    counts["directories"] += 1
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        opened = os.fstat(child)
                        if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                            raise UnsafeIO("unknown")
                        walk(child, entry.name, depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    counts["files"] += 1
                else:
                    counts["unsupported"] += 1

        try:
            with open_directory(root, policy) as fd:
                walk(fd)
            state = "ok"
        except UnsafeIO as exc:
            state = exc.state if exc.state in ("absent", "denied") else "unknown"
        except OSError:
            state = "unknown"
        domains[alias] = {"state": state, "counts": counts}
    return {"schema": "box-config-discovery/v1", "domains": domains,
            "policy": {"blacklist_version": BLACKLIST_VERSION, "skips": dict(policy.skips)}}


def check_state_dir(path):
    # discover writes nothing and need not create a nonexistent state directory.
    # Existing directories must have the same ownership/permissions as collect.
    try:
        with open_directory(path) as fd:
            info = os.fstat(fd)
            return info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700
    except UnsafeIO as exc:
        return exc.state == "absent"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="boxsync")
    subcommands = parser.add_subparsers(dest="command", required=True)
    command = subcommands.add_parser("discover")
    command.add_argument("--roots", help="Comma-separated registered root aliases")
    command.add_argument("--state-dir", type=Path, default=BOX_STATE_DIR)
    command.add_argument("--format", choices=("json", "text"), default="json")
    command.add_argument("--offline", action="store_true")
    command.add_argument("--fixture-home", type=Path, help="Explicit synthetic Box home (no production registration)")
    args = parser.parse_args(argv)
    roots = fixture_roots(args.fixture_home) if args.fixture_home else dict(BOX_ROOTS)
    if args.roots is not None:
        aliases = args.roots.split(",")
        if any(alias not in roots for alias in aliases):
            parser.error("unknown root alias")
        roots = {alias: roots[alias] for alias in aliases}
    if not check_state_dir(args.state_dir):
        print(json.dumps({"state": "denied", "reason_code": "unsafe_state_directory"}))
        return 4
    result = discover(roots)
    if args.format == "json":
        print(json.dumps(result, sort_keys=True))
    else:
        for alias, domain in result["domains"].items():
            print("%s: %s %s" % (alias, domain["state"], domain["counts"]))
        print("policy.skips: " + json.dumps(result["policy"]["skips"], sort_keys=True))
    return 3 if any(row["state"] != "ok" for row in result["domains"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
