#!/usr/bin/env python3
"""Create and rebase local.patch payloads for overlay-mode customizations.

`create` snapshots the diff between an upstream baseline and the local tree as
`customizations/<skill>/local.patch` plus a `patch` manifest in the record.
`rebase` replays that patch onto a new upstream tree with a real three-way
merge; on conflict it emits git-style diff3 markers and blocks activation by
writing only a `<skill>.conflicts/` tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import customization_check  # noqa: E402
from customization_check import (  # noqa: E402
    DEFAULT_CUSTOMIZATIONS,
    DEFAULT_REPO_ROOT,
    SCHEMA_NAME,
    SHA256_RE,
    TIME_RE,
    check_record,
    is_safe_relative,
    sha256_file,
)


def git_binary() -> str | None:
    return shutil.which("git")


def git_env() -> dict:
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    return env


def emit(payload: dict, code: int = 0) -> int:
    print(json.dumps(payload, indent=2))
    return code


def fail(message: str, path: str = "") -> int:
    return emit({"ok": False, "errors": [{"path": path, "message": message}]}, 1)


def tree_files(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ValueError(f"symlink not allowed in {root}: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"non-regular file not allowed in {root}: {path}")
        result[path.relative_to(root).as_posix()] = path
    return result


def read_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def files_manifest(baseline: dict[str, Path], local: dict[str, Path]) -> dict:
    manifest: dict[str, str | None] = {}
    for rel in sorted(set(baseline) | set(local)):
        base = baseline.get(rel)
        new = local.get(rel)
        if base is not None and new is not None:
            if sha256_file(base) != sha256_file(new):
                manifest[rel] = sha256_file(base)
        elif base is not None:
            manifest[rel] = sha256_file(base)
        else:
            manifest[rel] = None
    return manifest


def diff_trees(baseline: Path, local: Path, workdir: Path) -> bytes:
    shutil.copytree(baseline, workdir / "a")
    shutil.copytree(local, workdir / "b")
    result = subprocess.run(
        [
            git_binary(),
            "-c", "core.quotepath=off",
            "-c", "diff.noprefix=false",
            "-c", "diff.mnemonicPrefix=false",
            "-c", "diff.renames=false",
            "diff", "--no-index", "--binary", "--full-index", "--no-color",
            "--no-ext-diff", "--src-prefix=", "--dst-prefix=", "a", "b",
        ],
        cwd=workdir,
        env=git_env(),
        capture_output=True,
    )
    if result.returncode == 0:
        raise ValueError("no differences between baseline and local")
    if result.returncode != 1 or not result.stdout:
        raise ValueError(
            f"git diff --no-index failed: {result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return result.stdout


def load_record(record_dir: Path) -> tuple[dict | None, int | None]:
    record_file = record_dir / "customization.json"
    if not record_file.is_file():
        return None, fail(f"no customization record: {record_file}")
    try:
        record = json.loads(record_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, fail(f"invalid JSON: {exc.msg}", str(record_file))
    return record, None


def merge_file(ours: bytes, base: bytes, theirs: bytes, workdir: Path) -> tuple[bytes, int]:
    ours_path = workdir / "merge.ours"
    base_path = workdir / "merge.base"
    theirs_path = workdir / "merge.theirs"
    ours_path.write_bytes(ours)
    base_path.write_bytes(base)
    theirs_path.write_bytes(theirs)
    result = subprocess.run(
        [
            git_binary(), "merge-file", "-p", "--diff3",
            "-L", "local", "-L", "base", "-L", "upstream",
            str(ours_path), str(base_path), str(theirs_path),
        ],
        env=git_env(),
        capture_output=True,
    )
    if result.returncode < 0 or result.returncode >= 128 or result.returncode == 255:
        raise ValueError(
            f"git merge-file failed: {result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return result.stdout, result.returncode


def cmd_create(args: argparse.Namespace) -> int:
    baseline = args.baseline.expanduser().resolve()
    local = args.local.expanduser().resolve()
    for label, root in (("baseline", baseline), ("local", local)):
        if not (root / "SKILL.md").is_file():
            return fail(f"no SKILL.md in {label} dir: {root}", label)
    try:
        baseline_files = tree_files(baseline)
        local_files = tree_files(local)
    except ValueError as exc:
        return fail(str(exc))
    manifest = files_manifest(baseline_files, local_files)
    if not manifest:
        return fail("no differences between baseline and local")

    record_dir = args.customizations_dir.expanduser() / args.skill
    record_file = record_dir / "customization.json"
    record: dict
    if record_file.is_file():
        record, code = load_record(record_dir)
        if record is None:
            return code
        if "ops" in record:
            return fail("record uses ops; convert it to patch manually", "ops")
    else:
        if not args.reason or not args.baseline_ref:
            return fail("--reason and --baseline-ref are required for a new record")
        record = {
            "schema": SCHEMA_NAME,
            "skill": args.skill,
            "mode": "overlay",
            "reason": args.reason,
            "baseline_ref": args.baseline_ref,
            "reviewed_at": args.reviewed_at
            or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        try:
            patch_bytes = diff_trees(baseline, local, workdir)
        except ValueError as exc:
            return fail(str(exc))
        record["patch"] = {
            "sha256": hashlib.sha256(patch_bytes).hexdigest(),
            "files": manifest,
        }
        if args.baseline_ref is not None:
            record["baseline_ref"] = args.baseline_ref
        if args.reviewed_at is not None:
            record["reviewed_at"] = args.reviewed_at
        errors: list[dict] = []
        staged_dir = workdir / "record"
        staged_dir.mkdir()
        (staged_dir / "local.patch").write_bytes(patch_bytes)
        check_record(record, args.skill, None, staged_dir, DEFAULT_REPO_ROOT, errors)
        if errors:
            return emit({"ok": False, "errors": errors}, 1)

    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / "local.patch").write_bytes(patch_bytes)
    record_file.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return emit(
        {
            "ok": True,
            "record": str(record_file),
            "local_patch": str(record_dir / "local.patch"),
            "files": manifest,
        }
    )


def cmd_rebase(args: argparse.Namespace) -> int:
    record_dir = args.customizations_dir.expanduser() / args.skill
    record, code = load_record(record_dir)
    if record is None:
        return code
    if record.get("mode") != "overlay" or "patch" not in record:
        return fail(
            "record has no patch; ops-style records use customization_check.py apply",
            "mode",
        )
    errors: list[dict] = []
    check_record(record, args.skill, None, record_dir, DEFAULT_REPO_ROOT, errors)
    if errors:
        return emit({"ok": False, "errors": errors}, 1)

    old_baseline = args.old_baseline.expanduser().resolve()
    upstream = args.upstream.expanduser().resolve()
    for label, root in (("old-baseline", old_baseline), ("upstream", upstream)):
        if not (root / "SKILL.md").is_file():
            return fail(f"no SKILL.md in {label} dir: {root}", label)
    try:
        tree_files(old_baseline)
        tree_files(upstream)
    except ValueError as exc:
        return fail(str(exc))

    manifest = record["patch"]["files"]
    mismatches = []
    for rel, base_sha in manifest.items():
        base_file = old_baseline / rel
        if base_sha is None:
            if base_file.exists() or base_file.is_symlink():
                mismatches.append(rel)
        elif not base_file.is_file() or sha256_file(base_file) != base_sha:
            mismatches.append(rel)
    if mismatches:
        return emit(
            {
                "ok": False,
                "errors": [
                    {
                        "path": rel,
                        "message": "old baseline does not match record",
                    }
                    for rel in sorted(mismatches)
                ],
            },
            1,
        )

    output_root = args.output.expanduser().resolve()
    target = output_root / args.skill
    conflicts_tree = output_root / f"{args.skill}.conflicts"
    stale = [p for p in (target, conflicts_tree) if p.exists() or p.is_symlink()]
    if stale:
        if not args.force:
            return fail(
                f"output already exists: {stale[0]}; use --force to replace it", "output"
            )
        for path in stale:
            if path.is_symlink() or path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path)

    patch_bytes = (record_dir / "local.patch").read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        ours = workdir / "ours"
        shutil.copytree(old_baseline, ours)
        applied = subprocess.run(
            [git_binary(), "apply", "--binary", "--whitespace=nowarn", "-p1"],
            cwd=ours,
            env=git_env(),
            input=patch_bytes,
            capture_output=True,
        )
        if applied.returncode != 0:
            return fail(
                "local.patch does not apply to old baseline: "
                + applied.stderr.decode("utf-8", errors="replace").strip()
            )
        try:
            ours_files = tree_files(ours)
        except ValueError as exc:
            return fail(str(exc))
        old_files = tree_files(old_baseline)
        changed = {
            rel
            for rel in set(old_files) | set(ours_files)
            if (rel not in old_files)
            or (rel not in ours_files)
            or sha256_file(old_files[rel]) != sha256_file(ours_files[rel])
        }
        if changed != set(manifest):
            return fail(
                "applied patch changed files outside the manifest: "
                + ", ".join(sorted(changed ^ set(manifest)))
            )

        results: dict[str, dict] = {}
        contents: dict[str, bytes | None] = {}
        merge_dir = workdir / "merge"
        merge_dir.mkdir()
        for index, (rel, base_sha) in enumerate(sorted(manifest.items())):
            slot = merge_dir / str(index)
            slot.mkdir()
            base_b = read_bytes(old_baseline / rel)
            ours_b = read_bytes(ours / rel)
            theirs_b = read_bytes(upstream / rel)
            if theirs_b == base_b:
                results[rel] = {"path": rel, "status": "replayed"}
                contents[rel] = ours_b
            elif ours_b == theirs_b:
                results[rel] = {"path": rel, "status": "absorbed"}
                contents[rel] = theirs_b
            elif base_b is None and ours_b is not None and theirs_b is not None:
                merged, count = merge_file(ours_b, b"", theirs_b, slot)
                if count:
                    results[rel] = {
                        "path": rel,
                        "status": "conflict",
                        "reason": "content",
                        "count": count,
                    }
                else:
                    results[rel] = {"path": rel, "status": "merged"}
                contents[rel] = merged
            elif ours_b is None or theirs_b is None:
                results[rel] = {"path": rel, "status": "conflict", "reason": "modify_delete"}
                contents[rel] = theirs_b if theirs_b is not None else ours_b
            elif any(b"\x00" in blob for blob in (base_b, ours_b, theirs_b)):
                results[rel] = {"path": rel, "status": "conflict", "reason": "binary"}
                contents[rel] = ours_b
            else:
                merged, count = merge_file(ours_b, base_b, theirs_b, slot)
                if count:
                    results[rel] = {
                        "path": rel,
                        "status": "conflict",
                        "reason": "content",
                        "count": count,
                    }
                else:
                    results[rel] = {"path": rel, "status": "merged"}
                contents[rel] = merged

        files = [results[rel] for rel in sorted(results)]
        conflicts = [
            {key: value for key, value in item.items() if key != "status"}
            for item in files
            if item["status"] == "conflict"
        ]
        destination = conflicts_tree if conflicts else target
        shutil.copytree(upstream, destination)
        for rel, blob in contents.items():
            path = destination / rel
            if blob is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(blob)

    if conflicts:
        return emit(
            {
                "ok": False,
                "conflicts": conflicts,
                "conflicts_tree": str(conflicts_tree),
                "files": files,
                "next": (
                    "resolve markers in the .conflicts tree, then run skill_patch.py "
                    "create --baseline <upstream> --local <resolved tree> to refresh "
                    "local.patch, then rebase/stage"
                ),
            },
            2,
        )
    return emit({"ok": True, "output": str(target), "files": files})


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    create = sub.add_parser(
        "create", help="write local.patch and the patch manifest from baseline/local trees"
    )
    create.add_argument("--skill", required=True)
    create.add_argument("--baseline", type=Path, required=True)
    create.add_argument("--local", type=Path, required=True)
    create.add_argument("--customizations-dir", type=Path, default=DEFAULT_CUSTOMIZATIONS)
    create.add_argument("--baseline-ref")
    create.add_argument("--reviewed-at")
    create.add_argument("--reason")
    create.set_defaults(func=cmd_create)
    rebase = sub.add_parser(
        "rebase", help="replay local.patch onto a new upstream tree with a 3-way merge"
    )
    rebase.add_argument("--skill", required=True)
    rebase.add_argument("--old-baseline", type=Path, required=True)
    rebase.add_argument("--upstream", type=Path, required=True)
    rebase.add_argument("--output", type=Path, required=True)
    rebase.add_argument("--customizations-dir", type=Path, default=DEFAULT_CUSTOMIZATIONS)
    rebase.add_argument("--force", action="store_true")
    rebase.set_defaults(func=cmd_rebase)
    return root


def main(argv: list[str] | None = None) -> int:
    if git_binary() is None:
        print("error: git binary not found on PATH", file=sys.stderr)
        return 1
    try:
        args = parser().parse_args(argv)
        return args.func(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
