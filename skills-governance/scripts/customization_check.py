#!/usr/bin/env python3
"""Check non-local skill customization records and apply overlay deltas.

The upstream baseline is never stored in this repo. A customization record
(`customizations/<skill>/customization.json`) records which delta applies and
which upstream baseline it was reviewed against. Overlay payloads live under
`customizations/<skill>/delta/`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path


SCHEMA_NAME = "skills-customization/v1"
MODES = {"wrapper", "overlay", "fork", "policy_exclusion", "local_replacement"}
OP_KINDS = {"add", "replace", "delete"}
COMMON_REQUIRED = ("schema", "skill", "mode", "reason", "baseline_ref", "reviewed_at")
RECORD_KEYS = {
    *COMMON_REQUIRED,
    "retire_when",
    "ops",
    "patch",
    "path",
    "policy",
}
OP_KEYS = {"op", "path", "source", "sha256", "base_sha256"}
OP_REQUIRED = {
    "add": {"source", "sha256"},
    "replace": {"source", "sha256", "base_sha256"},
    "delete": {"base_sha256"},
}
OP_FORBIDDEN = {
    "add": {"base_sha256"},
    "replace": set(),
    "delete": {"source", "sha256"},
}
MODE_FIELDS = {
    "overlay": {"ops", "patch"},
    "wrapper": {"path"},
    "fork": {"path"},
    "local_replacement": {"path"},
    "policy_exclusion": {"policy"},
}
MODE_EXTRA_FIELDS = {"ops", "patch", "path", "policy"}
PATCH_KEYS = {"sha256", "files"}
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = PACKAGE_ROOT / "config" / "skills-inventory.json"
DEFAULT_CUSTOMIZATIONS = PACKAGE_ROOT / "customizations"
DEFAULT_REPO_ROOT = PACKAGE_ROOT.parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_safe_relative(value: object) -> bool:
    """Relative path with no absolute prefix, no `..`, and no empty segments."""
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    if path.is_absolute() or value.startswith("~"):
        return False
    if any(part in ("", ".", "..") for part in value.split("/")):
        return False
    return True


def join_path(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def expect_keys(
    value: object, allowed: set[str], required: tuple[str, ...], path: str, errors: list
) -> None:
    if not isinstance(value, dict):
        errors.append({"path": path, "message": "must be an object"})
        return
    missing = [key for key in required if key not in value]
    unknown = sorted(set(value) - allowed)
    for key in missing:
        errors.append({"path": join_path(path, key), "message": "required"})
    for key in unknown:
        errors.append({"path": join_path(path, key), "message": "unknown field"})


def non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_op(op_value: object, op_index: int, path: str, delta_dir: Path, errors: list) -> None:
    op_path = f"{path}.ops[{op_index}]"
    if not isinstance(op_value, dict):
        errors.append({"path": op_path, "message": "must be an object"})
        return
    expect_keys(op_value, OP_KEYS, ("op", "path"), op_path, errors)
    kind = op_value.get("op")
    if "op" in op_value and kind not in OP_KINDS:
        errors.append({"path": f"{op_path}.op", "message": "must be add, replace, or delete"})
        return
    target = op_value.get("path")
    if "path" in op_value and not is_safe_relative(target):
        errors.append(
            {
                "path": f"{op_path}.path",
                "message": "must be a relative path inside the skill directory",
            }
        )
    for field in ("sha256", "base_sha256"):
        if field in op_value and not (
            isinstance(op_value[field], str) and SHA256_RE.fullmatch(op_value[field])
        ):
            errors.append({"path": f"{op_path}.{field}", "message": "must be 64 lowercase hex"})
    if kind not in OP_KINDS:
        return
    for field in sorted(OP_REQUIRED[kind]):
        if field not in op_value:
            errors.append({"path": f"{op_path}.{field}", "message": "required"})
    for field in sorted(OP_FORBIDDEN[kind]):
        if field in op_value:
            errors.append({"path": f"{op_path}.{field}", "message": f"not allowed for {kind}"})
    source = op_value.get("source")
    if "source" in op_value:
        if not is_safe_relative(source):
            errors.append(
                {
                    "path": f"{op_path}.source",
                    "message": "must be a relative path inside the delta directory",
                }
            )
        else:
            source_file = delta_dir / source
            if source_file.is_symlink() or not source_file.is_file():
                errors.append(
                    {"path": f"{op_path}.source", "message": f"not found in delta/: {source}"}
                )
            elif (
                isinstance(op_value.get("sha256"), str)
                and SHA256_RE.fullmatch(op_value["sha256"])
                and sha256_file(source_file) != op_value["sha256"]
            ):
                errors.append(
                    {"path": f"{op_path}.sha256", "message": "does not match delta source sha256"}
                )


def check_patch(patch_value: object, path: str, record_dir: Path, errors: list) -> None:
    patch_path = f"{path}.patch"
    expect_keys(patch_value, PATCH_KEYS, ("sha256", "files"), patch_path, errors)
    if not isinstance(patch_value, dict):
        return
    digest = patch_value.get("sha256")
    if "sha256" in patch_value and not (
        isinstance(digest, str) and SHA256_RE.fullmatch(digest)
    ):
        errors.append({"path": f"{patch_path}.sha256", "message": "must be 64 lowercase hex"})
    files = patch_value.get("files")
    if "files" in patch_value:
        if not isinstance(files, dict) or not files:
            errors.append({"path": f"{patch_path}.files", "message": "must be a non-empty object"})
        else:
            for rel, base_sha in files.items():
                if not is_safe_relative(rel):
                    errors.append(
                        {
                            "path": f"{patch_path}.files",
                            "message": f"unsafe path: {rel}",
                        }
                    )
                if base_sha is not None and not (
                    isinstance(base_sha, str) and SHA256_RE.fullmatch(base_sha)
                ):
                    errors.append(
                        {
                            "path": f"{patch_path}.files.{rel}",
                            "message": "must be 64 lowercase hex or null",
                        }
                    )
    local_patch = record_dir / "local.patch"
    if local_patch.is_symlink() or not local_patch.is_file():
        errors.append(
            {"path": patch_path, "message": "local.patch not found as a regular non-symlink file"}
        )
    elif (
        isinstance(digest, str)
        and SHA256_RE.fullmatch(digest)
        and sha256_file(local_patch) != digest
    ):
        errors.append(
            {"path": f"{patch_path}.sha256", "message": "does not match local.patch sha256"}
        )


def check_record(record: object, name: str, skill: dict | None, record_dir: Path, repo_root: Path, errors: list) -> None:
    path = f"customizations/{name}/customization.json"
    expect_keys(record, RECORD_KEYS, COMMON_REQUIRED, path, errors)
    if not isinstance(record, dict):
        return
    if "schema" in record and record["schema"] != SCHEMA_NAME:
        errors.append({"path": f"{path}.schema", "message": f"must be {SCHEMA_NAME}"})
    if "skill" in record and record["skill"] != name:
        errors.append({"path": f"{path}.skill", "message": f"must equal directory name {name}"})
    mode = record.get("mode")
    if "mode" in record and mode not in MODES:
        errors.append(
            {"path": f"{path}.mode", "message": "must be wrapper, overlay, fork, policy_exclusion, or local_replacement"}
        )
        mode = None
    if skill is not None and record.get("skill") == name:
        inventory_mode = skill.get("customization_mode")
        if mode is not None and mode != inventory_mode:
            errors.append(
                {
                    "path": f"{path}.mode",
                    "message": f"{mode} does not match inventory customization_mode {inventory_mode}",
                }
            )
        if "baseline_ref" in record and record.get("baseline_ref") != skill.get("baseline_ref"):
            errors.append(
                {
                    "path": f"{path}.baseline_ref",
                    "message": "stale delta: re-review against new baseline",
                }
            )
    if "reason" in record and not non_empty_string(record["reason"]):
        errors.append({"path": f"{path}.reason", "message": "must be a non-empty string"})
    if "baseline_ref" in record and not (
        record["baseline_ref"] is None or isinstance(record["baseline_ref"], str)
    ):
        errors.append({"path": f"{path}.baseline_ref", "message": "must be a string or null"})
    if "reviewed_at" in record and not (
        isinstance(record["reviewed_at"], str) and TIME_RE.fullmatch(record["reviewed_at"])
    ):
        errors.append(
            {"path": f"{path}.reviewed_at", "message": "must be UTC timestamp YYYY-MM-DDTHH:MM:SSZ"}
        )
    if "retire_when" in record and not non_empty_string(record["retire_when"]):
        errors.append({"path": f"{path}.retire_when", "message": "must be a non-empty string"})
    if mode is None:
        return
    for field in sorted(MODE_EXTRA_FIELDS - MODE_FIELDS[mode]):
        if field in record:
            errors.append({"path": f"{path}.{field}", "message": f"not allowed for {mode}"})
    if mode == "overlay":
        if ("ops" in record) == ("patch" in record):
            errors.append({"path": path, "message": "overlay requires exactly one of ops or patch"})
    else:
        for field in sorted(MODE_FIELDS[mode]):
            if field not in record:
                errors.append({"path": f"{path}.{field}", "message": "required"})

    if mode == "overlay":
        ops = record.get("ops")
        if "patch" in record:
            check_patch(record["patch"], path, record_dir, errors)
        if "ops" in record:
            if not isinstance(ops, list) or not ops:
                errors.append({"path": f"{path}.ops", "message": "must be a non-empty array"})
            else:
                seen_paths: dict[str, int] = {}
                for index, op_value in enumerate(ops):
                    check_op(op_value, index, path, record_dir / "delta", errors)
                    if isinstance(op_value, dict):
                        target = op_value.get("path")
                        if is_safe_relative(target):
                            if target in seen_paths:
                                errors.append(
                                    {
                                        "path": f"{path}.ops[{index}].path",
                                        "message": f"duplicate of ops[{seen_paths[target]}]",
                                    }
                                )
                            else:
                                seen_paths[target] = index
    elif mode in {"wrapper", "fork", "local_replacement"}:
        target = record.get("path")
        if "path" in record:
            if not is_safe_relative(target):
                errors.append(
                    {"path": f"{path}.path", "message": "must be a repo-relative directory path"}
                )
            elif not (repo_root / target / "SKILL.md").is_file():
                errors.append(
                    {"path": f"{path}.path", "message": f"SKILL.md not found at {target}"}
                )
    elif mode == "policy_exclusion":
        if "policy" in record and not non_empty_string(record["policy"]):
            errors.append({"path": f"{path}.policy", "message": "must be a non-empty string"})
        constraints = skill.get("compatibility_constraints") if isinstance(skill, dict) else None
        has_policy = any(
            isinstance(item, dict) and item.get("kind") == "policy"
            for item in constraints or []
        )
        if not has_policy:
            errors.append(
                {
                    "path": f"{path}.policy",
                    "message": "inventory skill has no compatibility_constraints item with kind policy",
                }
            )


def check(args: argparse.Namespace) -> int:
    inventory_path = args.inventory.expanduser()
    customizations_dir = args.customizations_dir.expanduser()
    repo_root = args.repo_root.expanduser()
    try:
        document = json.loads(inventory_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return emit(False, {"errors": [{"path": "", "message": f"file not found: {inventory_path}"}]})
    except json.JSONDecodeError as exc:
        return emit(False, {"errors": [{"path": "", "message": f"invalid JSON: {exc.msg}"}]})
    skills = document.get("skills") if isinstance(document, dict) else None
    if not isinstance(skills, list):
        return emit(False, {"errors": [{"path": "skills", "message": "inventory must be a skills array"}]})

    errors: list[dict] = []
    expected: dict[str, dict] = {}
    for index, skill in enumerate(skills):
        if not isinstance(skill, dict):
            continue
        name = skill.get("name")
        mode = skill.get("customization_mode")
        if mode == "unknown":
            errors.append(
                {
                    "path": f"skills[{index}].customization_mode",
                    "message": "unresolved customization: classify the mode",
                }
            )
        if mode in MODES and isinstance(name, str):
            expected[name] = skill
    if customizations_dir.is_dir():
        for child in sorted(customizations_dir.iterdir()):
            if child.is_dir() and child.name not in expected:
                errors.append(
                    {
                        "path": f"customizations/{child.name}",
                        "message": "orphan record: skill missing or inventory mode is none",
                    }
                )
    skills_checked = 0
    for name, skill in sorted(expected.items()):
        skills_checked += 1
        record_dir = customizations_dir / name
        record_file = record_dir / "customization.json"
        if not record_file.is_file():
            errors.append(
                {
                    "path": f"customizations/{name}/customization.json",
                    "message": "record required by inventory customization_mode",
                }
            )
            continue
        try:
            record = json.loads(record_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(
                {
                    "path": f"customizations/{name}/customization.json",
                    "message": f"invalid JSON: {exc.msg}",
                }
            )
            continue
        check_record(record, name, skill, record_dir, repo_root, errors)
    return emit(not errors, {"errors": errors, "skills_checked": skills_checked})


def conflict(path: str, op: str, reason: str, expected: str | None, actual: str | None) -> dict:
    return {"path": path, "op": op, "reason": reason, "expected": expected, "actual": actual}


def apply_overlay(args: argparse.Namespace) -> int:
    record_dir = args.customizations_dir.expanduser() / args.skill
    record_file = record_dir / "customization.json"
    if not record_file.is_file():
        print(
            json.dumps({"ok": False, "errors": [{"path": str(record_file), "message": "no customization record"}]}),
        )
        return 1
    try:
        record = json.loads(record_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "errors": [{"path": str(record_file), "message": f"invalid JSON: {exc.msg}"}]}))
        return 1
    if record.get("mode") != "overlay":
        print(
            json.dumps(
                {
                    "ok": False,
                    "errors": [
                        {
                            "path": "mode",
                            "message": f"mode {record.get('mode')} has no overlay ops",
                        }
                    ],
                }
            )
        )
        return 1
    upstream = args.upstream.expanduser().resolve()
    if not (upstream / "SKILL.md").is_file():
        print(json.dumps({"ok": False, "errors": [{"path": "upstream", "message": "no SKILL.md in upstream dir"}]}))
        return 1
    record_errors: list[dict] = []
    check_record(record, args.skill, None, record_dir, DEFAULT_REPO_ROOT, record_errors)
    if record_errors:
        print(json.dumps({"ok": False, "errors": record_errors}, indent=2))
        return 1
    if "patch" in record:
        print(
            json.dumps(
                {
                    "ok": False,
                    "errors": [
                        {
                            "path": "patch",
                            "message": "patch-style record: use skill_patch.py rebase",
                        }
                    ],
                }
            )
        )
        return 1
    ops = record["ops"]
    delta_dir = record_dir / "delta"

    conflicts: list[dict] = []
    for op_value in ops:
        target = op_value["path"]
        kind = op_value["op"]
        upstream_file = upstream / target
        if kind == "add":
            if upstream_file.exists() or upstream_file.is_symlink():
                conflicts.append(conflict(target, kind, "upstream_added", None, sha256_file(upstream_file) if upstream_file.is_file() else "exists"))
            continue
        if not upstream_file.is_file() or upstream_file.is_symlink():
            conflicts.append(conflict(target, kind, "upstream_missing", op_value["base_sha256"], None))
            continue
        actual = sha256_file(upstream_file)
        if actual != op_value["base_sha256"]:
            conflicts.append(conflict(target, kind, "upstream_changed", op_value["base_sha256"], actual))
    if conflicts:
        print(json.dumps({"ok": False, "conflicts": conflicts}, indent=2))
        return 2

    output = args.output.expanduser().resolve()
    if output.exists() and not output.is_dir():
        print(json.dumps({"ok": False, "errors": [{"path": "output", "message": f"not a directory: {output}"}]}))
        return 1
    target_dir = output / args.skill
    if target_dir.exists() or target_dir.is_symlink():
        if not args.force:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "errors": [
                            {
                                "path": "output",
                                "message": f"output already exists: {target_dir}; use --force to replace it",
                            }
                        ],
                    }
                )
            )
            return 1
        if target_dir.is_symlink() or target_dir.is_file():
            target_dir.unlink()
        else:
            shutil.rmtree(target_dir)
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(upstream, target_dir, symlinks=True)
    applied = []
    for op_value in ops:
        target = op_value["path"]
        kind = op_value["op"]
        destination = target_dir / target
        if kind == "delete":
            destination.unlink()
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(delta_dir / op_value["source"], destination)
        applied.append({"op": kind, "path": target})
    print(json.dumps({"ok": True, "output": str(target_dir), "applied": applied}, indent=2))
    return 0


def emit(ok: bool, payload: dict) -> int:
    print(json.dumps({"ok": ok, **payload}, indent=2))
    return 0 if ok else 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    check_parser = sub.add_parser("check", help="validate customization records against the inventory")
    check_parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    check_parser.add_argument("--customizations-dir", type=Path, default=DEFAULT_CUSTOMIZATIONS)
    check_parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    check_parser.set_defaults(func=check)
    apply_parser = sub.add_parser("apply", help="materialize an overlay delta onto an upstream tree")
    apply_parser.add_argument("--skill", required=True)
    apply_parser.add_argument("--upstream", type=Path, required=True)
    apply_parser.add_argument("--output", type=Path, required=True)
    apply_parser.add_argument("--customizations-dir", type=Path, default=DEFAULT_CUSTOMIZATIONS)
    apply_parser.add_argument("--force", action="store_true")
    apply_parser.set_defaults(func=apply_overlay)
    return root


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        return args.func(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
