#!/usr/bin/env python3
"""Snapshot, stage, diff, gate, activate, and restore agent Skill trees."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path


GATE_SIGNALS = {
    "unexpected_instructions",
    "unexpected_files",
    "scripts",
    "network_behavior",
    "secret_access",
    "trigger_changes",
    "adapter_paths",
}

NETWORK_RE = re.compile(
    r"(?:https?://|curl\b|wget\b|requests\b|urllib\b|socket\b|fetch\s*\(|npm\s+(?:install|exec)|pip\s+install)",
    re.IGNORECASE,
)
SECRET_RE = re.compile(
    r"(?:\.env(?:\.|$)|api[_ -]?key|access[_ -]?token|secret|password|credential|private[_ -]?key|keychain|ssh/)",
    re.IGNORECASE,
)
TRIGGER_RE = re.compile(
    r"(?:^description:|^name:|trigger|activation|when to use|use when)",
    re.IGNORECASE,
)

SNAPSHOT_SCHEMA = "skill-governance-snapshot/v1"
STAGING_SCHEMA = "skill-governance-staging/v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def root_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def entries(root: Path) -> list[dict]:
    if not root.exists() and not root.is_symlink():
        raise ValueError(f"root does not exist: {root}")
    result: list[dict] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        item = {"path": relative, "mode": stat.S_IMODE(path.lstat().st_mode)}
        if path.is_symlink():
            item.update({"type": "symlink", "target": os.readlink(path)})
        elif path.is_dir():
            item["type"] = "directory"
        elif path.is_file():
            item.update({"type": "file", "sha256": sha256(path), "size": path.stat().st_size})
        else:
            item["type"] = "other"
        result.append(item)
    return result


def adapter_paths(root: Path) -> list[dict]:
    result: list[dict] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            result.append(
                {
                    "path": str(path),
                    "kind": "symlink",
                    "target": os.readlink(path),
                    "resolved": str(path.resolve(strict=False)),
                }
            )
        elif path.is_dir() and (path / "SKILL.md").is_file():
            result.append(
                {
                    "path": str(path),
                    "kind": "skill-directory",
                    "target": None,
                    "resolved": str(path.resolve()),
                }
            )
    return result


def copy_tree(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        else:
            shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, symlinks=True)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def prepare_output_dir(output: Path, force: bool, label: str) -> None:
    if output.exists():
        if not force:
            raise ValueError(f"{label} already exists: {output}; use --force to replace it")
        shutil.rmtree(output)
    output.mkdir(parents=True)


def collect_input_roots(
    skill_roots: list[str] | None, adapter_roots: list[str] | None
) -> list[tuple[str, int, Path]]:
    collected: list[tuple[str, int, Path]] = []
    for kind, values in (("skill", skill_roots), ("adapter", adapter_roots)):
        for index, value in enumerate(values or []):
            source = root_path(value)
            if not source.is_dir():
                raise ValueError(f"{kind} root is not a directory: {source}")
            collected.append((kind, index, source))
    if not collected:
        raise ValueError("at least one --skill-root or --adapter-root is required")
    return collected


def paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def snapshot(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser().resolve()
    prepare_output_dir(output, args.force, "snapshot")
    roots = []
    for kind, index, source in collect_input_roots(args.skill_root, args.adapter_root):
        relative = f"roots/{kind}-{index}"
        copy_tree(source, output / relative)
        roots.append(
            {
                "kind": kind,
                "original_path": str(source),
                "snapshot_path": relative,
                "entries": entries(source),
            }
        )
    metadata = json.loads(args.metadata) if args.metadata else {}
    active_adapters = []
    for value in args.adapter_root or []:
        active_adapters.extend(adapter_paths(root_path(value)))
    manifest = {
        "schema": SNAPSHOT_SCHEMA,
        "source_class": args.source_class,
        "source_authority": args.source_authority,
        "metadata": metadata,
        "roots": roots,
        "active_adapters": active_adapters,
    }
    write_json(output / "manifest.json", manifest)
    print(json.dumps({"snapshot": str(output), "roots": len(roots), "adapters": len(active_adapters)}))
    return 0


def read_bytes(root: Path, relative: str) -> bytes:
    path = root / relative
    return path.read_bytes() if path.is_file() else b""


def risk_signals(
    relative: str, old: bytes, new: bytes, change_kind: str, root_kind: str
) -> list[str]:
    signals: set[str] = set()
    name = Path(relative).name
    text_old = old.decode("utf-8", errors="replace")
    text_new = new.decode("utf-8", errors="replace")
    if change_kind == "added":
        signals.add("unexpected_files")
    if root_kind == "adapter":
        signals.add("adapter_paths")
    if name == "SKILL.md":
        signals.add("unexpected_instructions")
        old_front = (
            text_old.split("---", 2)[1]
            if text_old.startswith("---") and text_old.count("---") >= 2
            else ""
        )
        new_front = (
            text_new.split("---", 2)[1]
            if text_new.startswith("---") and text_new.count("---") >= 2
            else ""
        )
        changed_lines = difflib.unified_diff(
            text_old.splitlines(), text_new.splitlines(), lineterm=""
        )
        changed_trigger = any(
            line[:1] in {"+", "-"}
            and not line.startswith(("+++", "---"))
            and TRIGGER_RE.search(line[1:])
            for line in changed_lines
        )
        if old_front != new_front or changed_trigger:
            signals.add("trigger_changes")
    if relative.startswith("scripts/") or "/scripts/" in relative:
        signals.add("scripts")
    if NETWORK_RE.search(text_new) and not NETWORK_RE.search(text_old):
        signals.add("network_behavior")
    if SECRET_RE.search(relative) or (
        SECRET_RE.search(text_new) and not SECRET_RE.search(text_old)
    ):
        signals.add("secret_access")
    if (
        name.endswith((".md", ".yaml", ".yml", ".json", ".py", ".sh", ".ts", ".js"))
        and change_kind == "modified"
        and name != "SKILL.md"
        and text_old != text_new
    ):
        signals.add("unexpected_instructions")
    return sorted(signals)


def load_staging_manifest(staging_dir: Path) -> dict:
    manifest = read_json(staging_dir / "manifest.json")
    if manifest.get("schema") != STAGING_SCHEMA:
        raise ValueError(f"not a staging directory: {staging_dir}")
    return manifest


def staging_compare_roots(staging_dir: Path, snapshot_root_count: int) -> list[Path]:
    manifest = load_staging_manifest(staging_dir)
    roots = [staging_dir / item["staging_path"] for item in manifest["roots"]]
    if len(roots) != snapshot_root_count:
        raise ValueError("staging root count must match the snapshot root count")
    return roots


def compare_to_snapshot(snapshot_dir: Path, current_roots: list[Path] | None) -> dict:
    manifest = read_json(snapshot_dir / "manifest.json")
    if current_roots is not None and len(current_roots) != len(manifest["roots"]):
        raise ValueError("compare root count must match the snapshot root count")
    changes = []
    for index, root_info in enumerate(manifest["roots"]):
        root = current_roots[index] if current_roots is not None else Path(root_info["original_path"])
        old = {item["path"]: item for item in root_info["entries"]}
        now = {item["path"]: item for item in entries(root)}
        for relative in sorted(set(old) | set(now)):
            before, after = old.get(relative), now.get(relative)
            if before == after:
                continue
            kind = "added" if before is None else "removed" if after is None else "modified"
            old_bytes = read_bytes(snapshot_dir / root_info["snapshot_path"], relative)
            new_bytes = read_bytes(root, relative)
            changes.append(
                {
                    "root": str(root),
                    "kind": kind,
                    "path": relative,
                    "signals": risk_signals(
                        relative, old_bytes, new_bytes, kind, root_info["kind"]
                    ),
                    "diff": list(
                        difflib.unified_diff(
                            old_bytes.decode("utf-8", errors="replace").splitlines(),
                            new_bytes.decode("utf-8", errors="replace").splitlines(),
                            fromfile=f"snapshot/{relative}",
                            tofile=f"current/{relative}",
                            lineterm="",
                        )
                    )[:80],
                }
            )
    blocked = sorted(
        {
            signal
            for change in changes
            for signal in change["signals"]
            if signal in GATE_SIGNALS
        }
    )
    return {
        "schema": "skill-governance-diff/v1",
        "snapshot": str(snapshot_dir),
        "blocked": bool(blocked),
        "signals": blocked,
        "changes": changes,
    }


def resolve_compare_roots(args: argparse.Namespace, snapshot_root_count: int) -> list[Path] | None:
    staging = getattr(args, "staging", None)
    current = getattr(args, "current_root", None)
    if staging and current:
        raise ValueError("use either --staging or --current-root, not both")
    if staging:
        return staging_compare_roots(root_path(staging), snapshot_root_count)
    if current:
        roots = [root_path(value) for value in current]
        if len(roots) != snapshot_root_count:
            raise ValueError("--current-root count must match the snapshot root count")
        return roots
    return None


def print_diff_report(report: dict, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(report, indent=2))
        return
    snapshot_dir = report["snapshot"]
    blocked = report["blocked"]
    print(f"Snapshot: {snapshot_dir}")
    print(f"Changes: {len(report['changes'])}")
    print(f"Decision: {'BLOCK' if blocked else 'ALLOW'}")
    if blocked:
        print("Signals: " + ", ".join(report["signals"]))
    for change in report["changes"]:
        suffix = ", ".join(change["signals"]) or "no-risk-signal"
        print(f"- {change['kind']}: {change['path']} [{suffix}]")


def diff_snapshot(args: argparse.Namespace) -> int:
    snapshot_dir = Path(args.snapshot).expanduser().resolve()
    manifest = read_json(snapshot_dir / "manifest.json")
    current = resolve_compare_roots(args, len(manifest["roots"]))
    report = compare_to_snapshot(snapshot_dir, current)
    if getattr(args, "staging", None):
        report["staging"] = str(root_path(args.staging))
    print_diff_report(report, args.format)
    return 2 if args.gate and report["blocked"] else 0


def stage(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser().resolve()
    candidates = collect_input_roots(args.skill_root, args.adapter_root)
    live_roots = [root_path(value) for value in args.live_root] if args.live_root else []
    if live_roots and len(live_roots) != len(candidates):
        raise ValueError("--live-root count must match staged root count")
    snapshot_dir = root_path(args.snapshot) if args.snapshot else None
    for index, (kind, _offset, source) in enumerate(candidates):
        live = live_roots[index] if live_roots else None
        if live is not None and source == live:
            raise ValueError(
                f"candidate {kind} root is the live path {live}; "
                "stage a separate unpacked tree, never the runtime directory"
            )
        if live is not None and paths_overlap(output, live):
            raise ValueError(f"staging directory must be outside the live root: {live}")
        if paths_overlap(output, source):
            raise ValueError(f"staging directory must be outside the candidate root: {source}")
    prepare_output_dir(output, args.force, "staging directory")
    roots = []
    for index, (kind, offset, source) in enumerate(candidates):
        relative = f"roots/{kind}-{offset}"
        copy_tree(source, output / relative)
        live = live_roots[index] if live_roots else None
        roots.append(
            {
                "kind": kind,
                "live_path": str(live) if live is not None else None,
                "candidate_path": str(source),
                "staging_path": relative,
                "entries": entries(source),
            }
        )
    manifest = {
        "schema": STAGING_SCHEMA,
        "status": "staged",
        "snapshot": str(snapshot_dir) if snapshot_dir else None,
        "roots": roots,
    }
    write_json(output / "manifest.json", manifest)
    print(
        json.dumps(
            {
                "staging": str(output),
                "status": "staged",
                "roots": len(roots),
                "live_roots": [item["live_path"] for item in roots],
            }
        )
    )
    return 0


def activate_targets(args: argparse.Namespace, staging_manifest: dict) -> list[Path]:
    if args.target:
        targets = [root_path(value) for value in args.target]
        if len(targets) != len(staging_manifest["roots"]):
            raise ValueError("--target count must match staging root count")
        return targets
    targets = []
    for item in staging_manifest["roots"]:
        live = item.get("live_path")
        if not live:
            raise ValueError("live target missing; pass --target or stage with --live-root")
        targets.append(root_path(live))
    return targets


def activate(args: argparse.Namespace) -> int:
    staging_dir = Path(args.staging).expanduser().resolve()
    staging_manifest = load_staging_manifest(staging_dir)
    snapshot_dir = root_path(args.snapshot) if args.snapshot else None
    if snapshot_dir is None and staging_manifest.get("snapshot"):
        snapshot_dir = root_path(staging_manifest["snapshot"])
    report = None
    if not args.force:
        if snapshot_dir is None:
            raise ValueError(
                "activation requires --snapshot (or a staging-bound snapshot); "
                "use --force only after explicit review"
            )
        current = staging_compare_roots(staging_dir, len(read_json(snapshot_dir / "manifest.json")["roots"]))
        report = compare_to_snapshot(snapshot_dir, current)
        report["staging"] = str(staging_dir)
        if report["blocked"]:
            print(
                json.dumps(
                    {
                        "staging": str(staging_dir),
                        "activated": [],
                        "blocked": True,
                        "signals": report["signals"],
                        "diff": report,
                    },
                    indent=2,
                )
            )
            return 2
    targets = activate_targets(args, staging_manifest)
    activated = []
    for index, root_info in enumerate(staging_manifest["roots"]):
        source = staging_dir / root_info["staging_path"]
        target = targets[index]
        if paths_overlap(source, target):
            raise ValueError(f"refusing to activate staging onto itself: {target}")
        copy_tree(source, target)
        activated.append(str(target))
    staging_manifest["status"] = "activated"
    write_json(staging_dir / "manifest.json", staging_manifest)
    print(
        json.dumps(
            {
                "staging": str(staging_dir),
                "activated": activated,
                "blocked": False,
                "gate": "skipped" if args.force else "allow",
                "snapshot": str(snapshot_dir) if snapshot_dir else None,
            }
        )
    )
    return 0


def rollback(args: argparse.Namespace) -> int:
    if not args.force:
        raise ValueError("rollback requires --force")
    snapshot_dir = Path(args.snapshot).expanduser().resolve()
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    current = [root_path(value) for value in args.current_root] if args.current_root else None
    if current and len(current) != len(manifest["roots"]):
        raise ValueError("--current-root count must match the snapshot root count")
    restored = []
    for index, root_info in enumerate(manifest["roots"]):
        target = current[index] if current else Path(root_info["original_path"])
        copy_tree(snapshot_dir / root_info["snapshot_path"], target)
        restored.append(str(target))
    print(json.dumps({"snapshot": str(snapshot_dir), "restored": restored}))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot", help="capture Skill trees and adapter metadata")
    snap.add_argument("--skill-root", action="append")
    snap.add_argument("--adapter-root", action="append")
    snap.add_argument("--source-class", required=True)
    snap.add_argument("--source-authority", default="unknown")
    snap.add_argument("--metadata", help="JSON source metadata")
    snap.add_argument("--output", required=True)
    snap.add_argument("--force", action="store_true")
    snap.set_defaults(func=snapshot)
    diff = sub.add_parser("diff", help="compare a current or staged tree with a snapshot")
    diff.add_argument("--snapshot", required=True)
    diff.add_argument("--current-root", action="append")
    diff.add_argument("--staging", help="compare snapshot against a staging directory instead of live roots")
    diff.add_argument("--format", choices=("text", "json"), default="text")
    diff.add_argument("--gate", action="store_true", help="exit 2 when a review-blocking signal is found")
    diff.set_defaults(func=diff_snapshot)
    staged = sub.add_parser(
        "stage",
        help="copy a candidate tree into staging without writing live runtime roots",
    )
    staged.add_argument("--skill-root", action="append", help="unpacked candidate Skill tree")
    staged.add_argument("--adapter-root", action="append", help="unpacked candidate adapter tree")
    staged.add_argument(
        "--live-root",
        action="append",
        help="intended live path to activate later; never written by stage",
    )
    staged.add_argument("--snapshot", help="optional baseline snapshot to bind for later activate")
    staged.add_argument("--output", required=True)
    staged.add_argument("--force", action="store_true")
    staged.set_defaults(func=stage)
    apply = sub.add_parser(
        "activate",
        help="replace live roots with staging only after snapshot/diff/gate review",
    )
    apply.add_argument("--staging", required=True)
    apply.add_argument("--snapshot", help="baseline snapshot; defaults to the staging-bound snapshot")
    apply.add_argument("--target", action="append", help="override live paths recorded at stage time")
    apply.add_argument(
        "--force",
        action="store_true",
        help="activate even if gate would block, or if no snapshot is bound",
    )
    apply.set_defaults(func=activate)
    restore = sub.add_parser("rollback", help="restore trees from a snapshot")
    restore.add_argument("--snapshot", required=True)
    restore.add_argument("--current-root", action="append")
    restore.add_argument("--force", action="store_true")
    restore.set_defaults(func=rollback)
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        return args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
