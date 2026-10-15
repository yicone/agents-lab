#!/usr/bin/env python3
"""Validate the machine-readable skills inventory.

The inventory file is the source of truth. This script checks its shape and,
with --check-paths, checks canonical files and in-repo symlink adapters.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path


SCHEMA_NAME = "skills-inventory/v1"
SKILL_REQUIRED = (
    "name",
    "source_class",
    "authority",
    "baseline",
    "adapters",
    "status",
    "review_time",
    "baseline_ref",
    "current_version",
)
AUTHORITY_REQUIRED = (
    "ownership",
    "source_authority",
    "update_authority",
    "canonical_path",
)
ADAPTER_REQUIRED = ("path", "kind", "target")
TOP_REQUIRED = ("schema", "updated_at", "skills")
TOP_KEYS = {"schema", "updated_at", "description", "human_mirror", "skills"}
SKILL_KEYS = {
    *SKILL_REQUIRED,
    "scope",
    "install_shape",
    "local_customization",
    "conflict_status",
    "visible_paths",
}
AUTHORITY_KEYS = set(AUTHORITY_REQUIRED)
BASELINE_KEYS = {"kind", "subject"}
ADAPTER_KEYS = set(ADAPTER_REQUIRED)
STATUSES = {"active", "staged", "quarantined", "retired", "unknown"}
BASELINE_KINDS = {"content_hash", "upstream_ref", "git_commit", "none"}
ADAPTER_KINDS = {"symlink", "directory", "declared_target", "wrapper"}
INSTALL_SHAPES = {
    "canonical_directory",
    "symlink_adapters",
    "real_directory",
    "mixed",
    "unknown",
}
CUSTOMIZATION = {"none", "local_delta", "unknown"}
CONFLICT = {"none", "open", "unreviewed", "unknown"}
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CLASS_AUTHORITY_KEYS = ("ownership", "source_authority", "update_authority")

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = PACKAGE_ROOT / "config" / "skills-inventory.json"
DEFAULT_SOURCE_TYPES = PACKAGE_ROOT / "config" / "source-types.yaml"
DEFAULT_REPO_ROOT = PACKAGE_ROOT.parent


class InventoryError(Exception):
    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}" if path else message)


def load_source_classes(path: Path) -> dict[str, dict[str, str]]:
    """Read source_classes from source-types.yaml without a YAML dependency."""
    classes: dict[str, dict[str, str]] = {}
    current: str | None = None
    in_classes = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not raw.startswith(" ") and stripped.endswith(":"):
            in_classes = stripped == "source_classes:"
            current = None
            continue
        if not in_classes:
            continue
        if raw.startswith("  ") and not raw.startswith("    ") and stripped.endswith(":"):
            current = stripped[:-1]
            classes[current] = {}
            continue
        if current and raw.startswith("    ") and ":" in stripped:
            key, _, value = stripped.partition(":")
            classes[current][key.strip()] = value.strip()
    if not classes:
        raise InventoryError("", f"no source_classes found in {path}")
    return classes


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def skill_name_from_markdown(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    for line in parts[1].splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return None


def join_path(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def expect_keys(value: dict, allowed: set[str], required: tuple[str, ...], path: str, errors: list) -> None:
    if not isinstance(value, dict):
        errors.append({"path": path, "message": "must be an object"})
        return
    missing = [key for key in required if key not in value]
    unknown = sorted(set(value) - allowed)
    for key in missing:
        errors.append({"path": join_path(path, key), "message": "required"})
    for key in unknown:
        errors.append({"path": join_path(path, key), "message": "unknown field"})


def is_relative_repo_path(value: str) -> bool:
    path = Path(value)
    return value == path.as_posix() and not path.is_absolute() and ".." not in path.parts and not value.startswith("~")


def is_external_path(value: str) -> bool:
    return value.startswith("~") or value.startswith("/") or Path(value).is_absolute()


def locate_path(value: str, repo_root: Path) -> Path:
    if is_external_path(value):
        return Path(value).expanduser()
    return repo_root / value


def validate_document(
    document: object,
    source_classes: dict[str, dict[str, str]],
    repo_root: Path | None,
) -> list[dict]:
    errors: list[dict] = []
    if not isinstance(document, dict):
        return [{"path": "", "message": "inventory must be a JSON object"}]
    expect_keys(document, TOP_KEYS, TOP_REQUIRED, "", errors)
    if not isinstance(document, dict):
        return errors
    if "schema" in document and document["schema"] != SCHEMA_NAME:
        errors.append({"path": "schema", "message": f"must be {SCHEMA_NAME}"})
    updated = document.get("updated_at")
    if "updated_at" in document and not (isinstance(updated, str) and TIME_RE.fullmatch(updated)):
        errors.append({"path": "updated_at", "message": "must be UTC timestamp YYYY-MM-DDTHH:MM:SSZ"})
    if "description" in document and not isinstance(document["description"], str):
        errors.append({"path": "description", "message": "must be a string"})
    mirror = document.get("human_mirror")
    if "human_mirror" in document and not isinstance(mirror, dict):
        errors.append({"path": "human_mirror", "message": "must be an object"})
    elif "human_mirror" in document:
        expect_keys(
            mirror,
            {"page", "role", "projection"},
            ("page", "role", "projection"),
            "human_mirror",
            errors,
        )
        if isinstance(mirror, dict):
            if mirror.get("role") != "human_view":
                errors.append({"path": "human_mirror.role", "message": "must be human_view"})
            if mirror.get("projection") != "one_way_from_this_file":
                errors.append(
                    {
                        "path": "human_mirror.projection",
                        "message": "must be one_way_from_this_file",
                    }
                )
            page = mirror.get("page")
            if "page" in mirror and not (isinstance(page, str) and page.strip()):
                errors.append({"path": "human_mirror.page", "message": "must be a non-empty string"})
    skills = document.get("skills")
    if "skills" in document and not isinstance(skills, list):
        errors.append({"path": "skills", "message": "must be an array"})
        return errors
    if not isinstance(skills, list):
        return errors

    seen_names: dict[str, int] = {}
    seen_paths: dict[str, str] = {}
    seen_adapters: dict[str, str] = {}
    for index, skill in enumerate(skills):
        path = f"skills[{index}]"
        if not isinstance(skill, dict):
            errors.append({"path": path, "message": "must be an object"})
            continue
        expect_keys(skill, SKILL_KEYS, SKILL_REQUIRED, path, errors)
        name = skill.get("name")
        if isinstance(name, str) and NAME_RE.fullmatch(name):
            if name in seen_names:
                errors.append({"path": f"{path}.name", "message": f"duplicate of skills[{seen_names[name]}]"})
            else:
                seen_names[name] = index
        elif "name" in skill:
            errors.append({"path": f"{path}.name", "message": "must be a lowercase kebab-case name"})

        source_class = skill.get("source_class")
        class_record = source_classes.get(source_class) if isinstance(source_class, str) else None
        if "source_class" in skill and class_record is None:
            errors.append(
                {
                    "path": f"{path}.source_class",
                    "message": "must be a class defined in source-types.yaml",
                }
            )

        authority = skill.get("authority")
        if isinstance(authority, dict):
            expect_keys(authority, AUTHORITY_KEYS, AUTHORITY_REQUIRED, f"{path}.authority", errors)
            if class_record:
                for key in CLASS_AUTHORITY_KEYS:
                    expected = class_record.get(key)
                    if key in authority and expected and authority.get(key) != expected:
                        errors.append(
                            {
                                "path": f"{path}.authority.{key}",
                                "message": f"must match source class {source_class}: {expected}",
                            }
                        )
            canonical = authority.get("canonical_path")
            if "canonical_path" in authority and canonical is not None and not (
                isinstance(canonical, str) and is_relative_repo_path(canonical)
            ):
                errors.append(
                    {
                        "path": f"{path}.authority.canonical_path",
                        "message": "must be a repo-relative path or null",
                    }
                )
            if source_class == "local_custom" and not isinstance(canonical, str):
                visible = skill.get("visible_paths") if isinstance(skill.get("visible_paths"), list) else []
                has_machine_path = any(isinstance(item, str) and is_external_path(item) for item in visible)
                if not has_machine_path:
                    errors.append(
                        {
                            "path": f"{path}.authority.canonical_path",
                            "message": "required for local_custom unless a machine visible path is recorded",
                        }
                    )
            if isinstance(canonical, str):
                owner = seen_paths.get(canonical)
                if owner:
                    errors.append(
                        {
                            "path": f"{path}.authority.canonical_path",
                            "message": f"duplicate of {owner}",
                        }
                    )
                else:
                    seen_paths[canonical] = path
        elif "authority" in skill:
            errors.append({"path": f"{path}.authority", "message": "must be an object"})

        baseline = skill.get("baseline")
        kind = None
        if isinstance(baseline, dict):
            expect_keys(baseline, BASELINE_KEYS, ("kind",), f"{path}.baseline", errors)
            kind = baseline.get("kind")
            if "kind" in baseline and kind not in BASELINE_KINDS:
                errors.append(
                    {
                        "path": f"{path}.baseline.kind",
                        "message": "must be content_hash, upstream_ref, git_commit, or none",
                    }
                )
            subject = baseline.get("subject")
            if "subject" in baseline and not (isinstance(subject, str) and subject.strip()):
                errors.append({"path": f"{path}.baseline.subject", "message": "must be a non-empty string"})
            if kind == "content_hash" and subject not in (None, "SKILL.md"):
                errors.append(
                    {
                        "path": f"{path}.baseline.subject",
                        "message": "content_hash baseline subject must be SKILL.md",
                    }
                )
        elif "baseline" in skill:
            errors.append({"path": f"{path}.baseline", "message": "must be an object"})

        baseline_ref = skill.get("baseline_ref") if "baseline_ref" in skill else None
        if "baseline_ref" in skill:
            if kind == "none" and baseline_ref is not None:
                errors.append({"path": f"{path}.baseline_ref", "message": "must be null when baseline.kind is none"})
            elif kind == "content_hash" and not (isinstance(baseline_ref, str) and SHA_REF_RE.fullmatch(baseline_ref)):
                errors.append(
                    {
                        "path": f"{path}.baseline_ref",
                        "message": "must be sha256:<64 lowercase hex> when baseline.kind is content_hash",
                    }
                )
            elif kind in {"upstream_ref", "git_commit"} and not (isinstance(baseline_ref, str) and baseline_ref.strip()):
                errors.append({"path": f"{path}.baseline_ref", "message": "must be a non-empty string"})
            elif kind is None and baseline_ref is not None and not isinstance(baseline_ref, str):
                errors.append({"path": f"{path}.baseline_ref", "message": "must be a string or null"})

        review_time = skill.get("review_time")
        if "review_time" in skill and review_time is not None and not (
            isinstance(review_time, str) and TIME_RE.fullmatch(review_time)
        ):
            errors.append(
                {
                    "path": f"{path}.review_time",
                    "message": "must be null or UTC timestamp YYYY-MM-DDTHH:MM:SSZ",
                }
            )
        current_version = skill.get("current_version")
        if "current_version" in skill and current_version is not None and not (
            isinstance(current_version, str) and current_version.strip()
        ):
            errors.append({"path": f"{path}.current_version", "message": "must be a non-empty string or null"})
        if "status" in skill and skill.get("status") not in STATUSES:
            errors.append(
                {
                    "path": f"{path}.status",
                    "message": "must be active, staged, quarantined, retired, or unknown",
                }
            )
        for field, allowed in (
            ("install_shape", INSTALL_SHAPES),
            ("local_customization", CUSTOMIZATION),
            ("conflict_status", CONFLICT),
        ):
            if field in skill and skill.get(field) not in allowed:
                errors.append({"path": f"{path}.{field}", "message": "unsupported value"})
        if "scope" in skill and not (isinstance(skill.get("scope"), str) and skill["scope"].strip()):
            errors.append({"path": f"{path}.scope", "message": "must be a non-empty string"})
        visible = skill.get("visible_paths")
        if "visible_paths" in skill:
            if not isinstance(visible, list) or not all(isinstance(item, str) and item for item in visible):
                errors.append({"path": f"{path}.visible_paths", "message": "must be an array of non-empty strings"})

        adapters = skill.get("adapters")
        if "adapters" in skill and not isinstance(adapters, list):
            errors.append({"path": f"{path}.adapters", "message": "must be an array"})
        elif isinstance(adapters, list):
            for adapter_index, adapter in enumerate(adapters):
                adapter_path = f"{path}.adapters[{adapter_index}]"
                if not isinstance(adapter, dict):
                    errors.append({"path": adapter_path, "message": "must be an object"})
                    continue
                expect_keys(adapter, ADAPTER_KEYS, ADAPTER_REQUIRED, adapter_path, errors)
                if "kind" in adapter and adapter.get("kind") not in ADAPTER_KINDS:
                    errors.append(
                        {
                            "path": f"{adapter_path}.kind",
                            "message": "must be symlink, directory, declared_target, or wrapper",
                        }
                    )
                location = adapter.get("path")
                if "path" in adapter and not (isinstance(location, str) and location.strip()):
                    errors.append({"path": f"{adapter_path}.path", "message": "must be a non-empty string"})
                target = adapter.get("target")
                if "target" in adapter and target is not None and not isinstance(target, str):
                    errors.append({"path": f"{adapter_path}.target", "message": "must be a string or null"})
                if adapter.get("kind") == "symlink" and not (isinstance(target, str) and target):
                    errors.append({"path": f"{adapter_path}.target", "message": "required for symlink adapters"})
                if adapter.get("kind") in {"symlink", "directory"} and isinstance(location, str):
                    if not is_relative_repo_path(location) and not is_external_path(location):
                        errors.append(
                            {
                                "path": f"{adapter_path}.path",
                                "message": "use a repo-relative path, or an absolute/~ path for a machine adapter",
                            }
                        )
                    elif location in seen_adapters:
                        errors.append(
                            {
                                "path": f"{adapter_path}.path",
                                "message": f"duplicate of {seen_adapters[location]}",
                            }
                        )
                    else:
                        seen_adapters[location] = adapter_path

        if repo_root is not None and isinstance(skill, dict):
            errors.extend(check_skill_paths(skill, path, repo_root))
    return errors


def check_skill_paths(skill: dict, path: str, repo_root: Path) -> list[dict]:
    errors: list[dict] = []
    authority = skill.get("authority") if isinstance(skill.get("authority"), dict) else {}
    canonical = authority.get("canonical_path")
    if isinstance(canonical, str):
        skill_dir = repo_root / canonical
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            errors.append({"path": f"{path}.authority.canonical_path", "message": f"SKILL.md not found at {canonical}"})
        else:
            actual = skill_name_from_markdown(skill_md.read_text(encoding="utf-8"))
            if actual != skill.get("name"):
                errors.append(
                    {
                        "path": f"{path}.name",
                        "message": f"does not match SKILL.md name {actual!r}",
                    }
                )
    baseline = skill.get("baseline") if isinstance(skill.get("baseline"), dict) else {}
    if baseline.get("kind") == "content_hash" and isinstance(skill.get("baseline_ref"), str):
        skill_md = None
        if isinstance(canonical, str):
            candidate = repo_root / canonical / "SKILL.md"
            skill_md = candidate if candidate.is_file() else None
        if skill_md is None:
            for visible in skill.get("visible_paths") or []:
                if not isinstance(visible, str):
                    continue
                candidate = locate_path(visible, repo_root) / "SKILL.md"
                if candidate.is_file():
                    skill_md = candidate
                    break
        if skill_md is None:
            errors.append({"path": f"{path}.baseline_ref", "message": "SKILL.md for content_hash was not found"})
        else:
            actual = skill_name_from_markdown(skill_md.read_text(encoding="utf-8"))
            if actual and actual != skill.get("name") and not isinstance(canonical, str):
                errors.append(
                    {
                        "path": f"{path}.name",
                        "message": f"does not match SKILL.md name {actual!r}",
                    }
                )
            digest = "sha256:" + sha256_file(skill_md)
            if skill["baseline_ref"] != digest:
                errors.append(
                    {
                        "path": f"{path}.baseline_ref",
                        "message": "does not match SKILL.md sha256",
                    }
                )
    visible_paths = skill.get("visible_paths")
    if isinstance(visible_paths, list):
        for visible_index, visible in enumerate(visible_paths):
            if not isinstance(visible, str):
                continue
            if not is_relative_repo_path(visible) and not is_external_path(visible):
                errors.append(
                    {
                        "path": f"{path}.visible_paths[{visible_index}]",
                        "message": "use a repo-relative path, or an absolute/~ path",
                    }
                )
                continue
            if not locate_path(visible, repo_root).exists():
                errors.append(
                    {
                        "path": f"{path}.visible_paths[{visible_index}]",
                        "message": "path does not exist",
                    }
                )
    adapters = skill.get("adapters")
    if isinstance(adapters, list):
        for adapter_index, adapter in enumerate(adapters):
            if not isinstance(adapter, dict):
                continue
            kind = adapter.get("kind")
            location = adapter.get("path")
            if kind not in {"symlink", "directory"} or not isinstance(location, str):
                continue
            if not is_relative_repo_path(location) and not is_external_path(location):
                continue
            adapter_file = locate_path(location, repo_root)
            adapter_path = f"{path}.adapters[{adapter_index}]"
            if kind == "symlink":
                if not adapter_file.is_symlink():
                    errors.append({"path": f"{adapter_path}.path", "message": "is not a symlink"})
                elif os.readlink(adapter_file) != adapter.get("target"):
                    errors.append({"path": f"{adapter_path}.target", "message": "does not match the symlink target"})
            elif is_external_path(location):
                if not adapter_file.is_dir():
                    errors.append({"path": f"{adapter_path}.path", "message": "directory adapter does not exist"})
            elif not (adapter_file / "SKILL.md").is_file():
                errors.append({"path": f"{adapter_path}.path", "message": "directory adapter has no SKILL.md"})
    return errors


def emit(ok: bool, payload: dict) -> int:
    print(json.dumps({"ok": ok, **payload}, indent=2))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate skills-inventory.json")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--source-types", type=Path, default=DEFAULT_SOURCE_TYPES)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument(
        "--check-paths",
        action="store_true",
        help="Check canonical SKILL.md files, content hashes, and in-repo symlink adapters",
    )
    args = parser.parse_args(argv)
    inventory_path = args.inventory.expanduser()
    source_types_path = args.source_types.expanduser()
    try:
        document = json.loads(inventory_path.read_text(encoding="utf-8"))
        source_classes = load_source_classes(source_types_path)
    except FileNotFoundError as exc:
        return emit(False, {"errors": [{"path": "", "message": f"file not found: {exc.filename}"}]})
    except json.JSONDecodeError as exc:
        return emit(False, {"errors": [{"path": "", "message": f"invalid JSON: {exc.msg}"}]})
    except InventoryError as exc:
        return emit(False, {"errors": [{"path": exc.path, "message": exc.message}]})
    except OSError as exc:
        return emit(False, {"errors": [{"path": "", "message": str(exc)}]})

    repo_root = None
    if args.check_paths:
        repo_root = (args.repo_root or DEFAULT_REPO_ROOT).expanduser().resolve()
    errors = validate_document(document, source_classes, repo_root)
    if errors:
        return emit(False, {"errors": errors})
    skills = document.get("skills") if isinstance(document, dict) else []
    count = len(skills) if isinstance(skills, list) else 0
    return emit(True, {"schema": SCHEMA_NAME, "skills": count, "checked_paths": repo_root is not None})


if __name__ == "__main__":
    sys.exit(main())
