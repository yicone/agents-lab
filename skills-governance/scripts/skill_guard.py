#!/usr/bin/env python3
"""Snapshot, diff, gate, and restore agent Skill trees."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import stat
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


def snapshot(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        if not args.force:
            raise ValueError(f"snapshot already exists: {output}; use --force to replace it")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    roots = []
    for kind, values in (("skill", args.skill_root), ("adapter", args.adapter_root)):
        for index, value in enumerate(values or []):
            source = root_path(value)
            if not source.is_dir():
                raise ValueError(f"{kind} root is not a directory: {source}")
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
    if not roots:
        raise ValueError("at least one --skill-root or --adapter-root is required")
    metadata = json.loads(args.metadata) if args.metadata else {}
    active_adapters = []
    for value in args.adapter_root or []:
        active_adapters.extend(adapter_paths(root_path(value)))
    manifest = {
        "schema": "skill-governance-snapshot/v1",
        "source_class": args.source_class,
        "source_authority": args.source_authority,
        "metadata": metadata,
        "roots": roots,
        "active_adapters": active_adapters,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
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


def diff_snapshot(args: argparse.Namespace) -> int:
    snapshot_dir = Path(args.snapshot).expanduser().resolve()
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    current = [root_path(value) for value in args.current_root] if args.current_root else None
    if current and len(current) != len(manifest["roots"]):
        raise ValueError("--current-root count must match the snapshot root count")
    changes = []
    for index, root_info in enumerate(manifest["roots"]):
        root = current[index] if current else Path(root_info["original_path"])
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
    report = {
        "schema": "skill-governance-diff/v1",
        "snapshot": str(snapshot_dir),
        "blocked": bool(blocked),
        "signals": blocked,
        "changes": changes,
    }
    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(f"Snapshot: {snapshot_dir}")
        print(f"Changes: {len(changes)}")
        print(f"Decision: {'BLOCK' if blocked else 'ALLOW'}")
        if blocked:
            print("Signals: " + ", ".join(blocked))
        for change in changes:
            suffix = ", ".join(change["signals"]) or "no-risk-signal"
            print(f"- {change['kind']}: {change['path']} [{suffix}]")
    return 2 if args.gate and blocked else 0


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
    diff = sub.add_parser("diff", help="compare a current tree with a snapshot")
    diff.add_argument("--snapshot", required=True)
    diff.add_argument("--current-root", action="append")
    diff.add_argument("--format", choices=("text", "json"), default="text")
    diff.add_argument("--gate", action="store_true", help="exit 2 when a review-blocking signal is found")
    diff.set_defaults(func=diff_snapshot)
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
