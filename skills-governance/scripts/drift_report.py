#!/usr/bin/env python3
"""Read-only source and version drift report for the skills inventory.

Compares each inventory record with the path, SKILL.md, Skills CLI lock
entry, or provider version file it already names. Uses review_time,
baseline_ref, and current_version. Runtime bundles use runtime:<provider>.
Enabled plugins use plugin:<host>/<id>.
Does not modify skills, inventory, or lock files. A shared skill name is only
an index; it is not provenance.
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_skills_inventory as inventory  # noqa: E402


REPORT_NAME = "skills-drift/v1"
HOME_LOCK = Path.home() / ".agents" / ".skill-lock.json"
DEFAULT_MACHINE_ROOTS = (
    Path.home() / ".agents" / "skills",
    Path.home() / ".claude" / "skills",
    Path.home() / ".gemini" / "config" / "skills",
    Path.home() / ".gemini" / "antigravity" / "global_skills",
    Path.home() / ".gemini" / "antigravity-cli" / "skills",
)
HARD_MISSING = frozenset({"content_missing", "lock_missing", "source_missing"})
MODIFIED = frozenset(
    {
        "content_hash_mismatch",
        "version_mismatch",
        "lock_hash_mismatch",
        "symlink_target_mismatch",
    }
)
UNVERIFIED = frozenset(
    {
        "baseline_absent",
        "name_mismatch",
        "lock_source_mismatch",
        "lock_path_mismatch",
        "lock_hash_empty",
        "provider_version_unreadable",
    }
)
STATUSES = ("missing", "version_changed", "modified", "stale", "unverified", "aligned")
CACHE_HASH_SEGMENT = re.compile(r"^[0-9a-f]{8,64}$")
DEFAULT_CHANGELOG_DIR = Path(__file__).resolve().parents[1] / "changelog"
UNRECORDED_CHECK = frozenset(
    {"content_hash_mismatch", "lock_hash_mismatch", "version_mismatch"}
)


def changelog_file_name(name: object) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(name)) + ".jsonl"


def changelog_entries(changelog_dir: Path, name: object) -> list[dict]:
    try:
        text = (changelog_dir / changelog_file_name(name)).read_text(encoding="utf-8")
    except OSError:
        return []
    result: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            result.append(item)
    return result


def metadata_version(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    in_metadata = False
    for line in parts[1].splitlines():
        if not line.strip():
            continue
        if line.startswith("metadata:"):
            in_metadata = True
            continue
        if in_metadata and line[:1] in {" ", "\t"}:
            stripped = line.strip()
            if stripped.startswith("version:"):
                return stripped.split(":", 1)[1].strip().strip("\"'")
            continue
        in_metadata = False
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return None


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    text = value[:-1]
    if "." in text:
        head, fraction = text.split(".", 1)
        text = head + "." + fraction[:6]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def is_concrete(value: object) -> bool:
    return isinstance(value, str) and "<" not in value and ">" not in value and (
        inventory.is_relative_repo_path(value) or inventory.is_external_path(value)
    )


def pin(path: Path) -> Path:
    return path.expanduser().absolute()


def path_present(path: Path) -> bool:
    return path.exists()


def read_text(path: Path) -> str | None:
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        return None
    return None


def version_sort_key(value: str) -> tuple:
    parts = []
    for chunk in re.split(r"[^0-9A-Za-z]+", value):
        if not chunk:
            continue
        if chunk.isdigit():
            parts.append((0, int(chunk)))
        else:
            parts.append((1, chunk))
    return tuple(parts)


def sibling_plugin_version(version_file: Path, recorded: object) -> str | None:
    """Recorded Codex-style version directory is gone, and a sibling has a new version.

    Layout: ``<plugin>/<version>/.codex-plugin/plugin.json``. A content-hash
    directory is not a version sibling. The recorded version itself is skipped.
    """
    if version_file.name != "plugin.json" or version_file.parent.name != ".codex-plugin":
        return None
    version_dir = version_file.parent.parent
    plugin_dir = version_dir.parent
    if version_dir.exists() or not plugin_dir.is_dir():
        return None
    found: list[tuple[tuple, str]] = []
    try:
        children = list(plugin_dir.iterdir())
    except OSError:
        return None
    for child in children:
        if not child.is_dir() or child.name.startswith("."):
            continue
        manifest = child / ".codex-plugin" / "plugin.json"
        if not manifest.is_file():
            continue
        observed = read_provider_version(manifest)
        if observed is None and not CACHE_HASH_SEGMENT.fullmatch(child.name):
            observed = child.name
        if not observed or observed == recorded:
            continue
        found.append((version_sort_key(observed), observed))
    if not found:
        return None
    found.sort()
    return found[-1][1]


def reconciliation_status(findings: set[str]) -> str:
    if "version_changed" in findings:
        return "version_changed"
    if findings & HARD_MISSING:
        return "missing"
    if findings & MODIFIED:
        return "modified"
    if findings & UNVERIFIED:
        return "unverified"
    if findings & {"review_time_missing", "review_older_than_lock"}:
        return "stale"
    return "aligned"


def first_visible(skill: dict, repo_root: Path) -> Path | None:
    for value in skill.get("visible_paths") or []:
        if is_concrete(value):
            return inventory.locate_path(value, repo_root)
    return None


def skill_files(skill: dict, repo_root: Path) -> tuple[Path | None, list[Path]]:
    authority = skill.get("authority") if isinstance(skill.get("authority"), dict) else {}
    canonical = authority.get("canonical_path")
    canonical_md = None
    if isinstance(canonical, str) and is_concrete(canonical):
        canonical_md = repo_root / canonical / "SKILL.md"
    machine: list[Path] = []
    canonical_dir = canonical_md.parent.resolve() if canonical_md is not None else None
    for value in skill.get("visible_paths") or []:
        if not is_concrete(value):
            continue
        candidate = inventory.locate_path(value, repo_root) / "SKILL.md"
        try:
            same = canonical_dir is not None and candidate.parent.resolve() == canonical_dir
        except OSError:
            same = False
        if same or not candidate.is_file():
            continue
        machine.append(candidate)
    return canonical_md, machine


def note_version(current: object, observed: str | None, findings: set[str]) -> None:
    if not isinstance(current, str) and observed is None:
        return
    if observed == current:
        findings.add("version_match")
    else:
        findings.add("version_mismatch")


def consider_content(
    skill: dict,
    repo_root: Path,
    findings: set[str],
    details: list[str],
) -> tuple[str | None, str | None]:
    baseline_ref = skill.get("baseline_ref")
    canonical_md, machine = skill_files(skill, repo_root)
    observed_version = None
    pairs: list[tuple[Path, str]] = []
    names: list[str | None] = []

    def take(path: Path | None, required: bool) -> None:
        nonlocal observed_version
        if path is None:
            return
        text = read_text(path)
        if text is None:
            if required:
                findings.add("source_missing")
            return
        names.append(inventory.skill_name_from_markdown(text))
        pairs.append((path, "sha256:" + inventory.sha256_file(path)))
        if observed_version is None:
            observed_version = metadata_version(text)

    take(canonical_md, required=canonical_md is not None)
    for path in machine:
        take(path, required=False)
    if not pairs and "source_missing" not in findings:
        findings.add("content_missing")
    if any(name and name != skill.get("name") for name in names):
        findings.add("name_mismatch")
    mismatches = [(path, digest) for path, digest in pairs if digest != baseline_ref]
    if pairs and not mismatches and "name_mismatch" not in findings:
        findings.add("content_hash_match")
    elif mismatches:
        findings.add("content_hash_mismatch")
        for path, digest in mismatches:
            details.append(f"{path} hash {digest} != baseline_ref {baseline_ref}")
    current = skill.get("current_version")
    note_version(current, observed_version, findings)
    if "version_mismatch" in findings:
        details.append(f"metadata.version {observed_version} != current_version {current}")
    observed_baseline = mismatches[0][1] if mismatches else (pairs[0][1] if pairs else None)
    return observed_version, observed_baseline


def provider_coordinate(baseline_ref: object) -> str | None:
    if not isinstance(baseline_ref, str):
        return None
    for prefix in ("runtime:", "plugin:"):
        if baseline_ref.startswith(prefix) and baseline_ref[len(prefix) :].strip():
            return prefix[:-1]
    return None


def read_provider_version(path: Path) -> str | None:
    """Read a provider version from a plugin manifest, runtime package, stamp, or app plist."""
    if not path.is_file():
        return None
    if path.suffix == ".plist":
        try:
            data = plistlib.loads(path.read_bytes())
        except (OSError, plistlib.InvalidFileException, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        value = data.get("CFBundleShortVersionString")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    base = data.get("baseVersion")
    commit = data.get("commit")
    if isinstance(base, str) and base.strip() and isinstance(commit, str) and len(commit) >= 10:
        return f"{base.strip()}+{commit[:10]}"
    version = data.get("version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return None


def consider_provider(
    skill: dict,
    repo_root: Path,
    findings: set[str],
    details: list[str],
) -> tuple[str | None, str | None]:
    baseline = skill.get("baseline") if isinstance(skill.get("baseline"), dict) else {}
    subject = baseline.get("subject")
    primary = first_visible(skill, repo_root)
    if not isinstance(subject, str) or not subject.strip():
        if primary is not None and not path_present(primary):
            findings.add("source_missing")
        findings.add("baseline_absent")
        return None, None
    version_file = inventory.locate_path(subject, repo_root)
    if not version_file.is_file():
        sibling = sibling_plugin_version(version_file, skill.get("current_version"))
        if sibling:
            findings.add("version_changed")
            details.append(
                f"recorded version directory missing; sibling version {sibling} "
                f"(recorded {skill.get('current_version')})"
            )
            return sibling, skill.get("baseline_ref")
        findings.add("source_missing")
        details.append(f"provider version file missing: {subject}")
        return None, None
    if primary is not None and not path_present(primary):
        findings.add("source_missing")
    observed = read_provider_version(version_file)
    if observed is None:
        findings.add("provider_version_unreadable")
        details.append(f"provider version unreadable: {subject}")
        return None, skill.get("baseline_ref")
    current = skill.get("current_version")
    note_version(current, observed, findings)
    if "version_mismatch" in findings:
        details.append(f"provider version {observed} != current_version {current}")
    return observed, skill.get("baseline_ref")


def upstream_source(baseline_ref: object) -> tuple[str | None, str | None]:
    if not isinstance(baseline_ref, str) or not baseline_ref.strip():
        return None, None
    if baseline_ref.startswith("github:"):
        return "github", baseline_ref[len("github:") :]
    return None, baseline_ref


def consider_lock(
    skill: dict,
    lock_skills: dict,
    findings: set[str],
    details: list[str],
    repo_root: Path,
) -> tuple[str | None, str | None, datetime | None]:
    baseline = skill.get("baseline") if isinstance(skill.get("baseline"), dict) else {}
    kind = baseline.get("kind")
    entry = lock_skills.get(skill.get("name")) if isinstance(lock_skills, dict) else None
    primary = first_visible(skill, repo_root)
    if primary is not None and not path_present(primary):
        findings.add("source_missing")
    if not isinstance(entry, dict):
        findings.add("lock_missing")
        return None, None, None
    folder_hash = entry.get("skillFolderHash") or ""
    observed_version = folder_hash or None
    updated = parse_time(entry.get("updatedAt"))
    if kind == "upstream_ref":
        source_type, source = upstream_source(skill.get("baseline_ref"))
        matched = entry.get("source") == source and (source_type is None or entry.get("sourceType") == source_type)
        findings.add("lock_source_match" if matched else "lock_source_mismatch")
        if not matched:
            details.append(f"lock source {entry.get('sourceType')}:{entry.get('source')} != baseline_ref {skill.get('baseline_ref')}")
        subject = baseline.get("subject")
        lock_path = entry.get("skillPath")
        if isinstance(subject, str) and subject.endswith("SKILL.md"):
            if subject == lock_path:
                findings.add("lock_path_match")
            else:
                findings.add("lock_path_mismatch")
                details.append(f"lock skillPath {lock_path} != baseline.subject {subject}")
        current = skill.get("current_version")
        if matched and folder_hash and current not in (None, folder_hash):
            findings.add("lock_hash_mismatch")
            details.append(f"lock skillFolderHash {folder_hash} != current_version {current}")
        elif matched and folder_hash and current is None:
            findings.add("version_mismatch")
            details.append(f"lock skillFolderHash {folder_hash} != current_version {current}")
        return observed_version, observed_lock_baseline(entry), updated
    if not folder_hash:
        findings.add("lock_hash_empty")
        return None, None, updated
    current = skill.get("current_version")
    if folder_hash == skill.get("baseline_ref") and current in (None, folder_hash):
        findings.add("lock_hash_match")
    else:
        findings.add("lock_hash_mismatch")
        details.append(
            f"lock skillFolderHash {folder_hash} != baseline_ref {skill.get('baseline_ref')} current_version {current}"
        )
    return observed_version, folder_hash, updated


def observed_lock_baseline(entry: dict) -> str | None:
    source = entry.get("source")
    if not isinstance(source, str) or not source:
        return None
    if entry.get("sourceType") == "github":
        return "github:" + source
    return source


def consider_links(skill: dict, repo_root: Path, findings: set[str]) -> None:
    for adapter in skill.get("adapters") or []:
        if not isinstance(adapter, dict) or adapter.get("kind") != "symlink":
            continue
        location = adapter.get("path")
        if not is_concrete(location):
            continue
        path = inventory.locate_path(location, repo_root)
        target = adapter.get("target")
        if not path.is_symlink():
            if path.exists():
                findings.add("symlink_target_mismatch")
            else:
                findings.add("symlink_missing")
            continue
        if os.readlink(path) != target:
            findings.add("symlink_target_mismatch")


def consider_review(skill: dict, findings: set[str], lock_updated: datetime | None) -> None:
    if findings & (HARD_MISSING | MODIFIED | UNVERIFIED):
        return
    review_time = parse_time(skill.get("review_time"))
    if skill.get("review_time") is None:
        findings.add("review_time_missing")
    elif review_time is None:
        findings.add("review_time_missing")
    elif lock_updated is not None and lock_updated > review_time:
        findings.add("review_older_than_lock")


def reconcile_skill(
    skill: dict,
    repo_root: Path,
    lock_skills: dict,
    changelog_dir: Path | None = None,
) -> dict:
    findings: set[str] = set()
    details: list[str] = []
    baseline = skill.get("baseline") if isinstance(skill.get("baseline"), dict) else {}
    kind = baseline.get("kind")
    observed_version = None
    observed_baseline = None
    lock_updated = None
    provider = provider_coordinate(skill.get("baseline_ref")) if kind == "upstream_ref" else None
    if kind == "none" or skill.get("baseline_ref") is None and kind != "content_hash":
        findings.add("baseline_absent")
        primary = first_visible(skill, repo_root)
        if primary is not None and not path_present(primary):
            findings.add("source_missing")
    elif kind == "content_hash":
        observed_version, observed_baseline = consider_content(skill, repo_root, findings, details)
        consider_links(skill, repo_root, findings)
    elif provider:
        observed_version, observed_baseline = consider_provider(skill, repo_root, findings, details)
        consider_links(skill, repo_root, findings)
    elif kind in {"git_commit", "upstream_ref"}:
        observed_version, observed_baseline, lock_updated = consider_lock(
            skill, lock_skills, findings, details, repo_root
        )
        consider_links(skill, repo_root, findings)
    else:
        findings.add("baseline_absent")
    for value in skill.get("visible_paths") or []:
        if not is_concrete(value):
            continue
        if not path_present(inventory.locate_path(value, repo_root)):
            findings.add("path_missing")
    consider_review(skill, findings, lock_updated)
    row = {
        "name": skill.get("name"),
        "source_class": skill.get("source_class"),
        "reconciliation_status": reconciliation_status(findings),
        "review_time": skill.get("review_time"),
        "baseline_ref": skill.get("baseline_ref"),
        "current_version": skill.get("current_version"),
        "observed_version": observed_version,
        "observed_baseline": observed_baseline,
        "findings": sorted(findings),
        "details": details,
    }
    if changelog_dir is not None:
        log = changelog_entries(changelog_dir, skill.get("name"))
        row["changelog"] = {
            "entries": len(log),
            "last_date": log[-1].get("date") if log else None,
        }
        if findings & UNRECORDED_CHECK:
            recorded = any(
                value is not None
                and value in {observed_baseline, observed_version}
                for item in log
                for value in (
                    item.get("new_hash"),
                    item.get("new_ref"),
                    item.get("new_version"),
                )
            )
            if not recorded:
                findings.add("change_unrecorded")
                row["findings"] = sorted(findings)
                details.append(
                    "有变更未记录: no changelog entry matches "
                    f"{observed_baseline or observed_version}"
                )
    return row


def is_bundle(skill: dict) -> bool:
    source = skill.get("source_class")
    scope = skill.get("scope")
    return (source == "runtime_builtin" and scope == "vendor-managed") or (
        source == "runtime_plugin" and scope == "plugin-managed"
    )


def covered_and_bundles(skills: list, repo_root: Path) -> tuple[set[Path], set[Path]]:
    covered: set[Path] = set()
    bundles: set[Path] = set()
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        values = list(skill.get("visible_paths") or [])
        for adapter in skill.get("adapters") or []:
            if isinstance(adapter, dict):
                values.append(adapter.get("path"))
        authority = skill.get("authority") if isinstance(skill.get("authority"), dict) else {}
        values.append(authority.get("canonical_path"))
        bundle = is_bundle(skill)
        for value in values:
            if not is_concrete(value):
                continue
            path = pin(inventory.locate_path(value, repo_root))
            covered.add(path)
            if bundle and path.is_dir() and not (path / "SKILL.md").is_file():
                bundles.add(path)
    return covered, bundles


def looks_like_skill(path: Path) -> bool:
    if path.is_symlink() and not path.exists():
        return True
    return (path / "SKILL.md").is_file()


def skill_label(path: Path) -> str:
    text = read_text(path / "SKILL.md")
    if text:
        name = inventory.skill_name_from_markdown(text)
        if name:
            return name
    return path.name


def display_path(path: Path, repo_root: Path) -> str:
    absolute = pin(path)
    try:
        relative = absolute.relative_to(repo_root)
    except ValueError:
        relative = None
    if relative is not None:
        return relative.as_posix()
    home = Path.home()
    try:
        return "~/" + absolute.relative_to(home).as_posix()
    except ValueError:
        return absolute.as_posix()


def discover_unrecorded(skills: list, repo_root: Path, scan_roots: list[Path]) -> list[dict]:
    covered, bundles = covered_and_bundles(skills, repo_root)
    found: list[dict] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        absolute = pin(path)
        if absolute in seen or absolute in covered or absolute in bundles:
            return
        if absolute.parent in bundles:
            return
        seen.add(absolute)
        found.append(
            {
                "name": skill_label(path),
                "path": display_path(path, repo_root),
                "reconciliation_status": "missing",
                "provenance": "unverified",
                "reason": "no inventory path records this install; a shared name is not provenance",
            }
        )

    for root in scan_roots:
        root = pin(root)
        if not root.is_dir() or root in bundles:
            continue
        try:
            children = sorted(root.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        for child in children:
            if child.name.startswith(".") or not looks_like_skill(child):
                continue
            add(child)

    for root in (repo_root / "skills", repo_root / "skills-governance" / "skills"):
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
            if "SKILL.md" not in filenames:
                continue
            add(Path(dirpath))
            dirnames[:] = []
    return sorted(found, key=lambda item: item["path"])


def empty_summary() -> dict:
    summary = {status: 0 for status in STATUSES}
    summary["inventory_records"] = 0
    summary["unrecorded"] = 0
    summary["change_unrecorded"] = 0
    return summary


def build_report(
    document: dict,
    repo_root: Path,
    lock_skills: dict,
    scan_roots: list[Path],
    inventory_path: Path | None = None,
    lock_path: Path | None = None,
    changelog_dir: Path | None = None,
) -> dict:
    skills = document.get("skills") if isinstance(document.get("skills"), list) else []
    rows = [
        reconcile_skill(skill, repo_root, lock_skills, changelog_dir)
        for skill in skills
        if isinstance(skill, dict)
    ]
    unrecorded = discover_unrecorded(skills, repo_root, scan_roots)
    summary = empty_summary()
    summary["inventory_records"] = len(rows)
    summary["unrecorded"] = len(unrecorded)
    for row in rows:
        summary[row["reconciliation_status"]] += 1
        if "change_unrecorded" in row["findings"]:
            summary["change_unrecorded"] += 1
    return {
        "changelog_dir": str(changelog_dir) if changelog_dir else None,
        "report": REPORT_NAME,
        "read_only": True,
        "inventory": str(inventory_path) if inventory_path else None,
        "lock": str(lock_path) if lock_path else None,
        "note": "Same name is not provenance. Reconciliation uses review_time, baseline_ref, current_version, and the recorded path or lock source.",
        "summary": summary,
        "skills": rows,
        "unrecorded": unrecorded,
    }


def render_text(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "Drift Report",
        f"Inventory: {report['inventory']}",
        "Read-only: yes",
        "Summary: "
        + " ".join(
            f"{key}={summary[key]}"
            for key in ("inventory_records", *STATUSES, "unrecorded")
        )
        + (
            f" change_unrecorded={summary['change_unrecorded']}"
            if report.get("changelog_dir")
            else ""
        ),
        "Same name is not provenance.",
    ]
    for row in report["skills"]:
        lines.append(
            f"- {row['name']} source_class={row['source_class']} "
            f"status={row['reconciliation_status']} review_time={row['review_time']} "
            f"baseline_ref={row['baseline_ref']} current_version={row['current_version']} "
            f"observed_version={row['observed_version']}"
        )
        if row["findings"]:
            lines.append("  findings: " + ", ".join(row["findings"]))
        for detail in row.get("details") or []:
            lines.append(f"  detail: {detail}")
    if report["unrecorded"]:
        lines.append("Unrecorded:")
        for item in report["unrecorded"]:
            lines.append(
                f"- {item['path']} name={item['name']} status=missing provenance=unverified"
            )
    return "\n".join(lines) + "\n"


def load_lock(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    skills = payload.get("skills") if isinstance(payload, dict) else None
    if not isinstance(skills, dict):
        raise ValueError("skill lock has no skills object")
    return skills


def machine_roots(document: dict, repo_root: Path, extra: list[Path], include_defaults: bool) -> list[Path]:
    roots: list[Path] = []
    if include_defaults:
        roots.extend(DEFAULT_MACHINE_ROOTS)
    roots.extend(extra)
    roots.extend(repo_root / relative for relative in (".agents/skills", ".codex/skills"))
    skills = document.get("skills") if isinstance(document.get("skills"), list) else []
    for skill in skills:
        if not isinstance(skill, dict) or is_bundle(skill):
            continue
        for value in skill.get("visible_paths") or []:
            if not is_concrete(value) or not inventory.is_external_path(value):
                continue
            path = pin(inventory.locate_path(value, repo_root))
            if path.name == skill.get("name"):
                roots.append(path.parent)
    _, bundles = covered_and_bundles([skill for skill in skills if isinstance(skill, dict)], repo_root)
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        absolute = pin(root)
        if absolute in seen or absolute in bundles or not absolute.is_dir():
            continue
        seen.add(absolute)
        unique.append(absolute)
    return unique


def emit(payload: dict, fmt: str, output: Path | None) -> int:
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n" if fmt == "json" else render_text(payload)
    if output is None:
        sys.stdout.write(text)
    else:
        output.write_text(text, encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only skills source and version drift report")
    parser.add_argument("--inventory", type=Path, default=inventory.DEFAULT_INVENTORY)
    parser.add_argument("--repo-root", type=Path, default=inventory.DEFAULT_REPO_ROOT)
    parser.add_argument("--lock", type=Path, default=None)
    parser.add_argument("--scan-root", type=Path, action="append", default=[])
    parser.add_argument("--no-default-roots", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--changelog-dir", type=Path, default=DEFAULT_CHANGELOG_DIR)
    args = parser.parse_args(argv)
    inventory_path = args.inventory.expanduser()
    repo_root = args.repo_root.expanduser().resolve()
    try:
        document = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"drift report failed: {exc}", file=sys.stderr)
        return 1
    lock_path = args.lock.expanduser() if args.lock else HOME_LOCK
    lock_skills: dict = {}
    used_lock: Path | None = None
    if lock_path.is_file():
        try:
            lock_skills = load_lock(lock_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"drift report failed: {exc}", file=sys.stderr)
            return 1
        used_lock = lock_path
    elif args.lock is not None:
        print(f"drift report failed: lock not found: {lock_path}", file=sys.stderr)
        return 1
    if not isinstance(document, dict):
        print("drift report failed: inventory must be a JSON object", file=sys.stderr)
        return 1
    roots = machine_roots(
        document,
        repo_root,
        [path.expanduser() for path in args.scan_root],
        include_defaults=not args.no_default_roots,
    )
    report = build_report(
        document,
        repo_root,
        lock_skills,
        roots,
        inventory_path,
        used_lock,
        args.changelog_dir.expanduser() if args.changelog_dir else None,
    )
    return emit(report, args.format, args.output)


if __name__ == "__main__":
    sys.exit(main())
