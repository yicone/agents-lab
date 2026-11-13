"""One-shot sidecar publisher for launchd; never installs or starts a service.

Run from run-metrics: python3 -m run_metrics.pipeline --dry-run --snapshot FILE
Dry-run builds and serializes the report in memory, with no filesystem writes.
Without --snapshot, collection uses the existing read-only Multica allow-list.
Partial reports retain their collection gaps; fatal failures keep the old file.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .collect import collect_snapshot
from .redact import redact
from .report import build_report

REPORT_NAME = "run-metrics.v0.1.json"


@contextmanager
def publication_lock(target: Path):
    # Keep this inode after release: unlinking allows overlapping lock owners.
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target.with_name(target.name + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def atomic_write(target: Path, content: str) -> None:
    # A same-directory 0600 temp file keeps readers on a complete old/new JSON.
    fd, name = tempfile.mkstemp(prefix="." + target.name + ".", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def build(args) -> tuple[dict, str]:
    if args.snapshot:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot must be a JSON object")
    else:
        snapshot = collect_snapshot(workers=args.workers)
    report = build_report(snapshot)
    return report, json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path.home() / ".multica-board" / REPORT_NAME)
    parser.add_argument("--snapshot", type=Path, help="replay an existing snapshot without API calls")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true", help="validate in memory; do not write or lock output")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be positive")
    target = args.output.expanduser().absolute()
    if args.snapshot and args.snapshot.resolve() == target.resolve():
        parser.error("output must not overwrite the input snapshot")
    try:
        if args.dry_run:
            report, content = build(args)
        else:
            with publication_lock(target):
                report, content = build(args)
                atomic_write(target, content)
        window = report["windows"][report["default_window"]]
        print(json.dumps({
            "mode": "dry-run" if args.dry_run else "published",
            "output": str(target),
            "written": not args.dry_run,
            "schema_version": report["schema_version"],
            "generated_at": report["generated_at"],
            "source_mode": report["source"]["mode"],
            "issues": window["issue_count"],
            "runs": window["run_count"],
            "partial": report["collection"]["partial"],
            "collection_errors": len(report["collection"]["errors"]),
            "bytes": len(content.encode("utf-8")),
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        # No raw CLI output, credentials, or report bodies in launchd logs.
        print("run-metrics pipeline failed: " + (redact(str(exc), limit=300) or type(exc).__name__), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
