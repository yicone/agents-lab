#!/usr/bin/env python3
"""Plan registry updates for skills installed on this machine.

Default mode is dry-run: print a change summary and leave
``skills-inventory.json`` untouched. ``--apply`` writes only when the planned
document still validates.

The command fills mechanical fields for a new install: name, path, source
class, and a hash or ``baseline_ref``. It does not write a ``review_time``
timestamp, and it does not write ``customization_mode`` or
``compatibility_constraints``. New rows use ``customization_mode: unknown``
because no review has chosen a mode. An existing mode or constraint list is
left as it was.

A content-hash or provider-version change is reported as needs-review. The
recorded baseline stays in place. If that row had a review timestamp, it is
cleared back to null. An install whose recorded paths are all gone is marked
``status: unknown`` and kept. Rows are not deleted.

Plugin caches become one row per plugin package. Marketplace clones, skills
nested inside a skill that already has ``SKILL.md``, and children of a
recorded runtime bundle are not separate rows. A second copy of a plugin id
that is already recorded is reported, not added.

This command does not install a launchd job, git hook, or autopilot. A daily
local launchd run of this dry-run is the proposed schedule; creating that job
needs a separate approval. An autopilot would spend an agent run on a disk
scan this script already does, and a git hook would miss installs that happen
outside commits.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drift_report  # noqa: E402
import validate_skills_inventory as inventory  # noqa: E402


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = inventory.DEFAULT_INVENTORY
DEFAULT_SOURCE_TYPES = inventory.DEFAULT_SOURCE_TYPES
DEFAULT_REPO_ROOT = inventory.DEFAULT_REPO_ROOT
DEFAULT_TOOL_ROOTS = PACKAGE_ROOT / "config" / "tool-roots.json"
DEFAULT_LOCK = drift_report.HOME_LOCK
SKIP_DIRS = frozenset(
    {"node_modules", "__pycache__", "dist", "build", "venv", "env", "site-packages", "marketplaces"}
)
MANIFEST_HOSTS = {
    ".codex-plugin": "codex",
    ".cursor-plugin": "cursor",
    ".claude-plugin": "claude",
}
HASH_SEGMENT = re.compile(r"^[0-9a-f]{8,64}$")
PROTECTED = (
    "review_time is never set to a timestamp",
    "customization_mode is never changed on an existing row and is unknown on a new row",
    "compatibility_constraints is never changed and stays empty on a new row",
)


def kebab(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace("_", "-")
    text = re.sub(r"[^a-z0-9-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if inventory.NAME_RE.fullmatch(text):
        return text
    return None


def pin(path: Path) -> Path:
    return drift_report.pin(path)


def inside(path: Path, parent: Path) -> bool:
    try:
        pin(path).relative_to(pin(parent))
    except ValueError:
        return False
    return True


def unique_paths(paths: list[Path]) -> list[Path]:
    chosen: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(pin(path))
        if key in seen:
            continue
        seen.add(key)
        chosen.append(pin(path))
    return chosen


def load_tool_roots(path: Path, repo_root: Path) -> tuple[list[Path], list[Path]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    skill_roots: list[Path] = []
    plugin_roots: list[Path] = []
    for item in document.get("roots") or []:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        raw = str(item["path"])
        location = (repo_root / raw) if item.get("repo") else Path(raw).expanduser()
        if "plugins" in Path(raw).parts:
            plugin_roots.append(location)
        else:
            skill_roots.append(location)
    return unique_paths(skill_roots), unique_paths(plugin_roots)


def looks_like_skill(path: Path) -> bool:
    if path.is_symlink() and not path.exists():
        return True
    return (path / "SKILL.md").is_file()


def walk_skill_dirs(root: Path):
    if not root.exists() and not root.is_symlink():
        return
    if looks_like_skill(root):
        yield root
        return
    if not root.is_dir():
        return
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return
    for child in children:
        if child.name.startswith(".") or child.name in SKIP_DIRS:
            continue
        if child.is_symlink() or child.is_dir():
            yield from walk_skill_dirs(child)


def skill_identity(path: Path) -> tuple[str | None, str]:
    text = drift_report.read_text(path / "SKILL.md")
    front = inventory.skill_name_from_markdown(text or "")
    name = kebab(front) or kebab(path.name)
    return name, "invalid_name" if name is None else ""


def recorded_locations(skill: dict, repo_root: Path) -> list[Path]:
    values: list[Path] = []
    authority = skill.get("authority") if isinstance(skill.get("authority"), dict) else {}
    canonical = authority.get("canonical_path")
    if drift_report.is_concrete(canonical):
        values.append(repo_root / canonical)
    for value in skill.get("visible_paths") or []:
        if drift_report.is_concrete(value):
            values.append(inventory.locate_path(value, repo_root))
    for adapter in skill.get("adapters") or []:
        if isinstance(adapter, dict) and drift_report.is_concrete(adapter.get("path")):
            values.append(inventory.locate_path(adapter["path"], repo_root))
    return unique_paths(values)


def bundle_roots(skills: list, repo_root: Path) -> list[Path]:
    roots: list[Path] = []
    for skill in skills:
        if not isinstance(skill, dict) or not drift_report.is_bundle(skill):
            continue
        for path in recorded_locations(skill, repo_root):
            if path.is_dir() and not (path / "SKILL.md").is_file():
                roots.append(path)
    return unique_paths(roots)


def content_hashes(skill: dict, repo_root: Path) -> list[str]:
    found: list[str] = []
    for path in recorded_locations(skill, repo_root):
        skill_md = path / "SKILL.md"
        if skill_md.is_file():
            found.append("sha256:" + inventory.sha256_file(skill_md))
    return found


def file_hash(path: Path) -> str | None:
    skill_md = path / "SKILL.md"
    if not skill_md.is_file():
        return None
    return "sha256:" + inventory.sha256_file(skill_md)


def repo_skill_path(path: Path, repo_root: Path) -> str | None:
    try:
        relative = pin(path).resolve().relative_to(pin(repo_root).resolve())
    except (OSError, ValueError):
        return None
    posix = relative.as_posix()
    if posix == "skills" or posix.startswith("skills/") or posix.startswith("skills-governance/skills/"):
        if posix in {"skills", "skills-governance/skills"}:
            return None
        return posix
    return None


def agents_install(path: Path, name: str) -> bool:
    return path.name == name and path.parent.name == "skills" and path.parent.parent.name == ".agents"


def authority_for(source_classes: dict, source_class: str, canonical: str | None) -> dict:
    record = source_classes[source_class]
    return {
        "ownership": record["ownership"],
        "source_authority": record["source_authority"],
        "update_authority": record["update_authority"],
        "canonical_path": canonical,
    }


def blank_review_fields() -> dict:
    return {
        "review_time": None,
        "local_customization": "unknown",
        "customization_mode": "unknown",
        "compatibility_constraints": [],
        "conflict_status": "unreviewed",
    }


def symlink_adapter(path: Path, display: str) -> dict | None:
    if not path.is_symlink():
        return None
    try:
        target = os.readlink(path)
    except OSError:
        return None
    if inventory.is_agent_worktree_path(target):
        return None
    return {"path": display, "kind": "symlink", "target": target}


def base_record(name: str, source_class: str, source_classes: dict, canonical: str | None) -> dict:
    return {
        "name": name,
        "source_class": source_class,
        "authority": authority_for(source_classes, source_class, canonical),
        "baseline": {"kind": "content_hash", "subject": "SKILL.md"},
        "adapters": [],
        "status": "active",
        **blank_review_fields(),
        "baseline_ref": None,
        "current_version": None,
        "scope": "machine",
        "install_shape": "real_directory",
        "visible_paths": [],
    }


def display_of(path: Path, repo_root: Path) -> str:
    return drift_report.display_path(path, repo_root)


def star_cache_segment(portable: str, version_dirname: str, plugin_version: str) -> str:
    """Keep a content-addressed cache directory as ``*``, like cursor-plugin-x."""
    if version_dirname == plugin_version or not HASH_SEGMENT.fullmatch(version_dirname):
        return portable
    parts = portable.split("/")
    if parts.count(version_dirname) != 1:
        return portable
    parts[parts.index(version_dirname)] = "*"
    return "/".join(parts)


def discover_plugins(plugin_roots: list[Path]) -> tuple[list[dict], list[dict]]:
    found: list[dict] = []
    skipped: list[dict] = []
    seen: set[Path] = set()
    for root in plugin_roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            path = Path(dirpath)
            if "marketplaces" in path.parts:
                dirnames[:] = []
                continue
            dirnames[:] = [
                name
                for name in dirnames
                if name not in SKIP_DIRS and (not name.startswith(".") or name in MANIFEST_HOSTS)
            ]
            if path.name not in MANIFEST_HOSTS or "plugin.json" not in filenames:
                if path.name in MANIFEST_HOSTS:
                    dirnames[:] = []
                continue
            manifest = path / "plugin.json"
            package = path.parent
            dirnames[:] = []
            if package in seen:
                continue
            seen.add(package)
            identity = cache_identity(manifest)
            if identity is None:
                skipped.append({"name": package.name, "path": str(package), "reason": "plugin_path_unparsed"})
                continue
            publisher, plugin_id, version_dirname = identity
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeError):
                skipped.append({"name": plugin_id, "path": str(manifest), "reason": "plugin_manifest_unreadable"})
                continue
            if not isinstance(payload, dict):
                skipped.append({"name": plugin_id, "path": str(manifest), "reason": "plugin_manifest_unreadable"})
                continue
            version = payload.get("version")
            if not isinstance(version, str) or not version.strip():
                skipped.append({"name": plugin_id, "path": str(manifest), "reason": "plugin_version_missing"})
                continue
            if not package_has_skill(package):
                skipped.append({"name": plugin_id, "path": str(package), "reason": "plugin_without_skills"})
                continue
            plugin_key = kebab(plugin_id)
            host = install_host(manifest)
            row_name = kebab(f"{host}-plugin-{plugin_key}") if plugin_key and host else None
            if row_name is None:
                skipped.append({"name": plugin_id, "path": str(package), "reason": "invalid_name"})
                continue
            skills_dir = package / "skills"
            visible = skills_dir if skills_dir.is_dir() else package
            found.append(
                {
                    "name": row_name,
                    "host": host,
                    "plugin_id": plugin_key,
                    "publisher": publisher,
                    "version": version.strip(),
                    "version_dirname": version_dirname,
                    "package": package,
                    "manifest": manifest,
                    "visible": visible,
                    "baseline_ref": f"plugin:{host}/{plugin_key}@{publisher}",
                }
            )
    return found, skipped


def install_host(manifest: Path) -> str | None:
    """The install host is the plugins root, not the manifest folder name.

    A Cursor cache can ship ``.claude-plugin/plugin.json``. That file is still
    a Cursor install.
    """
    parts = manifest.parts
    for index, part in enumerate(parts[:-1]):
        if part in {".cursor", ".codex", ".claude"} and parts[index + 1] == "plugins":
            return part[1:]
    return MANIFEST_HOSTS.get(manifest.parent.name)


def cache_identity(manifest: Path) -> tuple[str, str, str] | None:
    version_dir = manifest.parent.parent
    plugin_dir = version_dir.parent
    publisher_dir = plugin_dir.parent
    if publisher_dir.parent.name != "cache":
        return None
    publisher = kebab(publisher_dir.name)
    plugin_id = kebab(plugin_dir.name)
    if publisher is None or plugin_id is None:
        return None
    return publisher, plugin_id, version_dir.name


def package_has_skill(package: Path) -> bool:
    for dirpath, dirnames, filenames in os.walk(package, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS and not (name.startswith(".") and name not in MANIFEST_HOSTS)]
        if "SKILL.md" in filenames:
            return True
        if Path(dirpath).name in MANIFEST_HOSTS:
            dirnames[:] = []
    return False


def discover_skills(skill_roots: list[Path], blocked: list[Path], repo_root: Path) -> tuple[list[dict], list[dict]]:
    found: list[dict] = []
    skipped: list[dict] = []
    seen: set[Path] = set()
    for root in skill_roots:
        if any(root == block or inside(root, block) for block in blocked):
            continue
        for path in walk_skill_dirs(root):
            absolute = pin(path)
            if absolute in seen or any(inside(absolute, block) or absolute == block for block in blocked):
                continue
            seen.add(absolute)
            if repo_skill_path(absolute, repo_root) is None and inventory.is_agent_worktree_path(str(absolute)):
                skipped.append({"name": path.name, "path": str(absolute), "reason": "agent_worktree"})
                continue
            name, reason = skill_identity(path)
            if name is None:
                skipped.append({"name": path.name, "path": str(absolute), "reason": reason})
                continue
            if path.is_symlink() and not path.exists():
                skipped.append({"name": name, "path": str(absolute), "reason": "skill_unreadable"})
                continue
            found.append({"name": name, "path": absolute})
    return found, skipped


def plugin_ref_index(plugins: list[dict]) -> dict[str, dict]:
    return {item["baseline_ref"]: item for item in plugins}


def recorded_plugin_identity(skills: list) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        ref = skill.get("baseline_ref")
        if not isinstance(ref, str) or not ref.startswith("plugin:"):
            continue
        host_id, _, _publisher = ref[len("plugin:") :].partition("@")
        host, _, plugin_id = host_id.partition("/")
        if host and plugin_id:
            found.add((host, plugin_id))
    return found


def clear_review_time(skill: dict) -> bool:
    if skill.get("review_time") is None:
        return False
    skill["review_time"] = None
    return True


def mark_needs_review(skill: dict, summary: dict, reason: str, observed: str | None, detail: str | None = None) -> None:
    summary["needs_review"].append(
        {
            "name": skill.get("name"),
            "reason": reason,
            "baseline_ref": skill.get("baseline_ref"),
            "current_version": skill.get("current_version"),
            "observed": observed,
            "detail": detail,
            "review_time_cleared": clear_review_time(skill),
        }
    )


def plan_sync(
    document: dict,
    repo_root: Path,
    *,
    skill_roots: list[Path],
    plugin_roots: list[Path],
    lock_skills: dict,
    source_classes: dict,
    now: str,
) -> tuple[dict, dict]:
    original = document.get("skills") if isinstance(document.get("skills"), list) else []
    skills = [dict(item) if isinstance(item, dict) else item for item in original]
    summary: dict = {
        "added": [],
        "uninstalled": [],
        "needs_review": [],
        "backfill": [],
        "left_unset": [],
        "skipped": [],
        "protected": list(PROTECTED),
        "rules": [
            "marketplace clones are not installs",
            "a plugin package is one row",
            "a skill nested under SKILL.md stays with that skill",
            "children of a recorded runtime bundle stay with that bundle",
            "a shared name is not provenance and does not attach a new path",
            "a second copy of a recorded plugin id is not added",
        ],
    }
    bundles = bundle_roots([item for item in skills if isinstance(item, dict)], repo_root)
    plugins, plugin_skips = discover_plugins(plugin_roots)
    summary["skipped"].extend(plugin_skips)
    plugin_packages = [item["package"] for item in plugins]
    for skill in skills:
        if isinstance(skill, dict) and drift_report.is_bundle(skill):
            plugin_packages.extend(recorded_locations(skill, repo_root))
    installs, install_skips = discover_skills(skill_roots, bundles + plugin_packages, repo_root)
    summary["skipped"].extend(install_skips)
    by_ref = plugin_ref_index(plugins)
    used_names = {item.get("name") for item in skills if isinstance(item, dict)}
    recorded_plugin_ids = recorded_plugin_identity(skills)
    covered = set()
    for skill in skills:
        if isinstance(skill, dict):
            for path in recorded_locations(skill, repo_root):
                covered.add(str(pin(path)))

    for skill in skills:
        if not isinstance(skill, dict):
            continue
        consider_existing(skill, repo_root, lock_skills, by_ref, summary)

    recorded_refs = {item.get("baseline_ref") for item in skills if isinstance(item, dict)}
    for plugin in plugins:
        if plugin["baseline_ref"] in recorded_refs:
            continue
        if (plugin["host"], plugin["plugin_id"]) in recorded_plugin_ids:
            summary["skipped"].append(
                {
                    "name": plugin["name"],
                    "path": display_of(plugin["package"], repo_root),
                    "reason": "plugin_id_recorded",
                }
            )
            continue
        if plugin["name"] in used_names:
            summary["skipped"].append(
                {"name": plugin["name"], "path": display_of(plugin["package"], repo_root), "reason": "name_taken"}
            )
            continue
        record = plugin_record(plugin, repo_root, source_classes)
        insert_named(skills, record)
        used_names.add(record["name"])
        summary["added"].append(added_row(record, display_of(plugin["visible"], repo_root)))

    grouped: dict[str, list[dict]] = {}
    for install in installs:
        key = str(pin(install["path"]))
        if key in covered or any(inside(install["path"], Path(item)) for item in covered):
            continue
        grouped.setdefault(install["name"], []).append(install)
    for name, group in sorted(grouped.items()):
        if name in used_names:
            for install in group:
                summary["skipped"].append(
                    {"name": name, "path": display_of(install["path"], repo_root), "reason": "name_taken"}
                )
            continue
        resolved = set()
        for install in group:
            try:
                resolved.add(str(install["path"].resolve()))
            except OSError:
                resolved.add(str(pin(install["path"])))
        if len(resolved) > 1:
            for install in group:
                summary["skipped"].append(
                    {"name": name, "path": display_of(install["path"], repo_root), "reason": "ambiguous_name"}
                )
            continue
        record = skill_record(name, group, repo_root, lock_skills, source_classes)
        if record is None:
            summary["skipped"].append(
                {"name": name, "path": display_of(group[0]["path"], repo_root), "reason": "skill_unreadable"}
            )
            continue
        insert_named(skills, record)
        used_names.add(name)
        summary["added"].append(added_row(record, display_of(group[0]["path"], repo_root)))

    changed = file_changed(original, skills, summary)
    updated = dict(document)
    updated["skills"] = skills
    if changed:
        updated["updated_at"] = now
    summary["counts"] = {
        "inventory_before": len(original),
        "inventory_after": len(skills),
        "added": len(summary["added"]),
        "uninstalled": len(summary["uninstalled"]),
        "needs_review": len(summary["needs_review"]),
        "backfill": len(summary["backfill"]),
        "left_unset": len(summary["left_unset"]),
        "skipped": len(summary["skipped"]),
        "review_time_left_null": sum(
            1 for item in skills if isinstance(item, dict) and item.get("review_time") is None
        ),
        "file_changed": changed,
    }
    summary["skipped_counts"] = count_reasons(summary["skipped"])
    return updated, summary


def file_changed(original: list, skills: list, summary: dict) -> bool:
    if summary["added"] or summary["uninstalled"] or summary["backfill"]:
        return True
    if any(item.get("review_time_cleared") for item in summary["needs_review"]):
        return True
    return [item if isinstance(item, dict) else item for item in original] != skills


def added_row(record: dict, path: str) -> dict:
    return {
        "name": record["name"],
        "path": path,
        "source_class": record["source_class"],
        "baseline_ref": record["baseline_ref"],
        "current_version": record["current_version"],
    }


def insert_named(skills: list, record: dict) -> None:
    name = record["name"]
    for index, skill in enumerate(skills):
        if isinstance(skill, dict) and str(skill.get("name", "")) > name:
            skills.insert(index, record)
            return
    skills.append(record)


def consider_existing(skill: dict, repo_root: Path, lock_skills: dict, plugins: dict[str, dict], summary: dict) -> None:
    locations = recorded_locations(skill, repo_root)
    present = [path for path in locations if path.exists()]
    baseline = skill.get("baseline") if isinstance(skill.get("baseline"), dict) else {}
    kind = baseline.get("kind")
    ref = skill.get("baseline_ref")
    source = skill.get("source_class")
    observed_plugin = plugins.get(ref) if isinstance(ref, str) else None
    reviewed = False

    if kind == "content_hash":
        hashes = content_hashes(skill, repo_root)
        if isinstance(ref, str) and hashes and any(item != ref for item in hashes):
            mark_needs_review(skill, summary, "content_hash_mismatch", next(item for item in hashes if item != ref))
            reviewed = True
        elif ref is None and hashes:
            skill["baseline_ref"] = hashes[0]
            summary["backfill"].append(
                {"name": skill.get("name"), "field": "baseline_ref", "before": None, "after": hashes[0]}
            )
        elif present and not hashes:
            mark_needs_review(skill, summary, "content_missing", None)
            reviewed = True

    if isinstance(ref, str) and drift_report.provider_coordinate(ref):
        reviewed = consider_provider(skill, repo_root, observed_plugin, present, summary) or reviewed
    elif source == "skills_cli_managed" and kind == "upstream_ref":
        reviewed = consider_lock(skill, lock_skills, summary) or reviewed

    if (
        kind == "content_hash"
        and skill.get("current_version") is None
        and not any(item["name"] == skill.get("name") and item["field"] == "current_version" for item in summary["backfill"])
        and not any(item["name"] == skill.get("name") for item in summary["needs_review"])
    ):
        summary["left_unset"].append(
            {
                "name": skill.get("name"),
                "field": "current_version",
                "reason": "git history is the version authority for a content hash",
            }
        )

    if reviewed or observed_plugin is not None:
        return
    if skill.get("status") == "active" and locations and not present:
        skill["status"] = "unknown"
        summary["uninstalled"].append(
            {"name": skill.get("name"), "status_before": "active", "status_after": "unknown"}
        )


def consider_provider(skill: dict, repo_root: Path, observed: dict | None, present: list[Path], summary: dict) -> bool:
    baseline = skill.get("baseline") if isinstance(skill.get("baseline"), dict) else {}
    subject = baseline.get("subject")
    version_file = inventory.locate_path(subject, repo_root) if isinstance(subject, str) else None
    observed_version = None
    if version_file is not None and version_file.is_file():
        observed_version = drift_report.read_provider_version(version_file)
    if observed_version is None and observed is not None:
        observed_version = observed.get("version")
    current = skill.get("current_version")
    subject_exists = version_file is not None and version_file.is_file()
    if current is None and observed_version and subject_exists:
        skill["current_version"] = observed_version
        summary["backfill"].append(
            {"name": skill.get("name"), "field": "current_version", "before": None, "after": observed_version}
        )
        return False
    if observed is not None and (not subject_exists or (current is not None and current != observed.get("version"))):
        mark_needs_review(
            skill,
            summary,
            "provider_version_changed",
            observed.get("version"),
            display_of(observed["manifest"], repo_root),
        )
        return True
    if observed_version and current is not None and observed_version != current:
        mark_needs_review(skill, summary, "provider_version_changed", observed_version)
        return True
    if not present and observed is None and not subject_exists:
        return False
    return False


def consider_lock(skill: dict, lock_skills: dict, summary: dict) -> bool:
    entry = lock_skills.get(skill.get("name")) if isinstance(lock_skills, dict) else None
    if not isinstance(entry, dict):
        if skill.get("current_version") is None:
            summary["left_unset"].append(
                {"name": skill.get("name"), "field": "current_version", "reason": "lock entry missing"}
            )
        return False
    folder_hash = entry.get("skillFolderHash") or ""
    source_type, source = drift_report.upstream_source(skill.get("baseline_ref"))
    matched = entry.get("source") == source and (source_type is None or entry.get("sourceType") == source_type)
    current = skill.get("current_version")
    if not folder_hash:
        if current is None:
            summary["left_unset"].append(
                {"name": skill.get("name"), "field": "current_version", "reason": "lock skillFolderHash is empty"}
            )
        return False
    if not matched:
        if current is None:
            summary["left_unset"].append(
                {"name": skill.get("name"), "field": "current_version", "reason": "lock source does not match baseline_ref"}
            )
        return False
    if current is None:
        skill["current_version"] = folder_hash
        summary["backfill"].append(
            {"name": skill.get("name"), "field": "current_version", "before": None, "after": folder_hash}
        )
        return False
    if current != folder_hash:
        mark_needs_review(skill, summary, "lock_hash_changed", folder_hash)
        return True
    return False


def plugin_record(plugin: dict, repo_root: Path, source_classes: dict) -> dict:
    visible = star_cache_segment(display_of(plugin["visible"], repo_root), plugin["version_dirname"], plugin["version"])
    subject = star_cache_segment(display_of(plugin["manifest"], repo_root), plugin["version_dirname"], plugin["version"])
    record = base_record(plugin["name"], "runtime_plugin", source_classes, None)
    record["baseline"] = {"kind": "upstream_ref", "subject": subject}
    record["baseline_ref"] = plugin["baseline_ref"]
    record["current_version"] = plugin["version"]
    record["scope"] = "plugin-managed"
    record["visible_paths"] = [visible]
    return record


def skill_record(name: str, group: list[dict], repo_root: Path, lock_skills: dict, source_classes: dict) -> dict | None:
    primary = group[0]["path"]
    canonical = repo_skill_path(primary, repo_root)
    entry = lock_skills.get(name) if isinstance(lock_skills, dict) else None
    if canonical:
        digest = file_hash(primary)
        if digest is None:
            return None
        record = base_record(name, "local_custom", source_classes, canonical)
        record["baseline_ref"] = digest
        record["scope"] = "repo"
        record["visible_paths"] = [canonical]
        record["install_shape"] = "canonical_directory"
        return record
    if isinstance(entry, dict) and agents_install(primary, name) and entry.get("source"):
        record = base_record(name, "skills_cli_managed", source_classes, None)
        record["baseline"] = {
            "kind": "upstream_ref",
            "subject": entry.get("skillPath") if isinstance(entry.get("skillPath"), str) else "SKILL.md",
        }
        record["baseline_ref"] = drift_report.observed_lock_baseline(entry)
        folder_hash = entry.get("skillFolderHash") or ""
        record["current_version"] = folder_hash or None
        record["visible_paths"] = [display_of(primary, repo_root)]
        adapter = symlink_adapter(primary, record["visible_paths"][0])
        if adapter:
            record["adapters"] = [adapter]
            record["install_shape"] = "symlink_adapters"
        if not record["baseline_ref"]:
            return None
        return record
    digest = file_hash(primary)
    if digest is None:
        return None
    record = base_record(name, "unknown", source_classes, None)
    record["baseline_ref"] = digest
    displays = []
    adapters = []
    for install in group:
        display = display_of(install["path"], repo_root)
        if inventory.is_agent_worktree_path(display):
            continue
        displays.append(display)
        adapter = symlink_adapter(install["path"], display)
        if adapter:
            adapters.append(adapter)
    if not displays:
        return None
    record["visible_paths"] = displays
    record["adapters"] = adapters
    if adapters:
        record["install_shape"] = "symlink_adapters"
    return record


def count_reasons(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = str(row.get("reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def render_text(summary: dict) -> str:
    counts = summary["counts"]
    lines = ["Skills inventory sync"]
    if summary.get("validation_errors"):
        lines.append(f"Validation failed: {len(summary['validation_errors'])} errors; inventory not written")
    elif summary.get("dry_run"):
        lines.append("Dry-run: yes")
    elif counts.get("applied"):
        lines.append("Applied: yes")
    else:
        lines.append("Applied: no changes")
    lines.append(
        "Summary: "
        + " ".join(
            f"{key}={counts[key]}"
            for key in ("added", "uninstalled", "needs_review", "backfill", "left_unset", "skipped")
        )
    )
    lines.append("Protected: " + "; ".join(summary["protected"]))
    for label, key in (
        ("Added", "added"),
        ("Needs review", "needs_review"),
        ("Backfill", "backfill"),
        ("Uninstalled", "uninstalled"),
        ("Left unset", "left_unset"),
    ):
        rows = summary[key]
        if not rows:
            continue
        lines.append(f"{label}:")
        for row in rows:
            lines.append("- " + " ".join(f"{name}={row.get(name)}" for name in row if row.get(name) is not None))
    if summary["skipped_counts"]:
        lines.append(
            "Skipped: " + " ".join(f"{key}={value}" for key, value in summary["skipped_counts"].items())
        )
    return "\n".join(lines) + "\n"


def write_inventory_atomic(path: Path, text: str) -> None:
    """Replace ``path`` from a temp file in the same directory.

    ``os.replace`` is atomic on the same filesystem. A failure while writing,
    flushing, or replacing leaves the original file unchanged and deletes the
    temp file. The replacement keeps the original permission bits.
    """
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def emit(summary: dict, fmt: str, output: Path | None) -> None:
    text = json.dumps(summary, indent=2, ensure_ascii=False) + "\n" if fmt == "json" else render_text(summary)
    if output is None:
        sys.stdout.write(text)
    else:
        output.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run skills inventory sync. Write only with --apply.")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--source-types", type=Path, default=DEFAULT_SOURCE_TYPES)
    parser.add_argument("--tool-roots", type=Path, default=DEFAULT_TOOL_ROOTS)
    parser.add_argument("--lock", type=Path, default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    inventory_path = args.inventory.expanduser()
    repo_root = args.repo_root.expanduser().resolve()
    try:
        document = json.loads(inventory_path.read_text(encoding="utf-8"))
        source_classes = inventory.load_source_classes(args.source_types.expanduser())
        skill_roots, plugin_roots = load_tool_roots(args.tool_roots.expanduser(), repo_root)
    except (OSError, json.JSONDecodeError, inventory.InventoryError) as exc:
        print(f"inventory sync failed: {exc}", file=sys.stderr)
        return 1
    if not isinstance(document, dict):
        print("inventory sync failed: inventory must be a JSON object", file=sys.stderr)
        return 1
    skill_roots = unique_paths(skill_roots + [repo_root / "skills", repo_root / "skills-governance" / "skills"])
    lock_path = args.lock.expanduser() if args.lock else DEFAULT_LOCK
    lock_skills: dict = {}
    if lock_path.is_file():
        try:
            lock_skills = drift_report.load_lock(lock_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"inventory sync failed: {exc}", file=sys.stderr)
            return 1
    elif args.lock is not None:
        print(f"inventory sync failed: lock not found: {lock_path}", file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated, summary = plan_sync(
        document,
        repo_root,
        skill_roots=skill_roots,
        plugin_roots=plugin_roots,
        lock_skills=lock_skills,
        source_classes=source_classes,
        now=now,
    )
    summary["dry_run"] = not args.apply
    if args.apply and summary["counts"]["file_changed"]:
        errors = inventory.validate_document(updated, source_classes, repo_root)
        if errors:
            summary["validation_errors"] = errors
            summary["counts"]["applied"] = False
            emit(summary, args.format, args.output)
            return 2
        write_inventory_atomic(inventory_path, json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
        summary["counts"]["applied"] = True
    else:
        summary["counts"]["applied"] = False
    emit(summary, args.format, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
