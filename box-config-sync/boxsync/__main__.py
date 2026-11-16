"""Read-only discovery and metadata/hash collection."""
import argparse
import json
import os
import stat
import sys
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
    command = subcommands.add_parser("collect")
    command.add_argument("--state-dir", type=Path, default=BOX_STATE_DIR)
    command.add_argument("--format", choices=("json", "text"), default="json")
    command.add_argument("--offline", action="store_true", help="Do not invoke Multica or fetch Git")
    command.add_argument("--fixture-home", type=Path, help="Explicit synthetic Box home; never changes the production registry")
    command.add_argument("--domains", help="Comma-separated snapshot domain names")
    command.add_argument("--sidecar", action="append", default=[], help="Per-Bot JSON path or - for stdin")
    command.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.command == "collect":
        return collect_command(args, parser)
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


def collect_command(args, parser):
    from .snapshot import Context, DOMAIN_NAMES, collect, private_directory, write_snapshot
    from .sidecar import MAX_SIDECAR_BYTES
    from .safe_io import read_file
    from .validation import InvalidDocument

    selected = tuple(args.domains.split(",")) if args.domains else DOMAIN_NAMES
    if not selected or any(name not in DOMAIN_NAMES for name in selected):
        parser.error("unknown domain")
    if args.sidecar.count("-") > 1:
        parser.error("stdin can only be consumed once")
    roots = fixture_roots(args.fixture_home) if args.fixture_home else dict(BOX_ROOTS)
    from .roots import SETTINGS_PATH
    settings = SETTINGS_PATH
    if args.fixture_home:
        base = args.fixture_home / "sand-data"
        # Explicit synthetic inputs, not registration of unmeasured Box paths.
        roots.update({name: base / name for name in ("memory", "routines", "attachments")})
        settings = base / "settings.json"
    ctx = Context(roots=roots, settings=settings, offline=args.offline, selected=selected)
    inputs = []
    try:
        if not check_state_dir(args.state_dir):
            raise UnsafeIO("denied")
        out = args.out or args.state_dir / "snapshots/latest.json"
        # Never allow snapshot output or state below a source root or fixture home.
        targets = [out.absolute(), args.state_dir.absolute()]
        sources = [Path(p).absolute() for p in roots.values() if p is not None]
        if settings is not None:
            sources.append(settings.parent.absolute())
        if args.fixture_home:
            sources.append(args.fixture_home.absolute())
        if any(target == source or source in target.parents for target in targets for source in sources):
            raise UnsafeIO("denied")
        if any(Path(p).absolute() == out.absolute() for p in args.sidecar if p != "-"):
            raise UnsafeIO("denied")
        for source in args.sidecar:
            if source == "-":
                data = sys.stdin.buffer.read(MAX_SIDECAR_BYTES + 1)
            else:
                path = Path(source)
                result = read_file(path.parent, path.name, max_bytes=MAX_SIDECAR_BYTES)
                # Failed reads become rejected_schema, never an empty success.
                data = result.data if result.state == "observed" else b""
            inputs.append((source, data))
        # Reuse validated metadata only; no body bytes are persisted.
        prior = read_file(out.parent, out.name, max_bytes=4 * 1024 * 1024)
        if prior.state == "observed":
            try:
                from .validation import parse_json
                from .snapshot import validate_snapshot
                previous = parse_json(prior.data)
                validate_snapshot(previous)
                ctx.previous_cloud_skills = tuple(previous["domains"]["cloud_skills"])
            except (InvalidDocument, UnsafeIO):
                pass
        value, consumed = collect(ctx, inputs)
        private_directory(args.state_dir, create=True)
        if args.out is None:
            private_directory(args.state_dir / "snapshots", create=True)
        write_snapshot(value, out)
        # Consume only successfully persisted inputs in our private inbox.
        # Arbitrary --sidecar files remain user-owned and are left in place.
        from .snapshot import consume_sidecars
        consume_sidecars(args.state_dir, inputs, consumed)
        print(json.dumps(value, sort_keys=True) if args.format == "json" else json.dumps(value["sources"], sort_keys=True))
        return 0 if all(v == "ok" for v in value["sources"].values()) else 3
    except (UnsafeIO, InvalidDocument, OSError, ValueError):
        print(json.dumps({"state": "denied", "reason_code": "collection_refused"}))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
