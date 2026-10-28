"""Read-only Skills payload for Agent Atlas (design v3 D3 / B).

Inventory, drift, tool-root config, and staging candidates are merged here.
The page and ``GET /api/skills/attention`` both use :func:`assign_buckets`.
"""

from __future__ import annotations

import errno
import logging
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from .skill_discovery import scan_installs
from .skill_upgrade import DEFAULT_TOTAL_TIMEOUT, check_upgrades
from .skill_conflicts import check_conflicts

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "skills-governance" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import drift_report  # noqa: E402

INVENTORY_PATH = REPO_ROOT / "skills-governance" / "config" / "skills-inventory.json"
TOOL_ROOTS_PATH = REPO_ROOT / "skills-governance" / "config" / "tool-roots.json"
STAGING_ROOT = Path.home() / ".local" / "share" / "agents-lab" / "skill-staging"
LOCK_PATH = drift_report.HOME_LOCK
CHANGELOG_DIR = drift_report.DEFAULT_CHANGELOG_DIR
TEXT_BYTES = 2048
REVIEW_DAYS = (30, 90, 180)
DEFAULT_REVIEW_DAYS = 90
CACHE_TTL_SECONDS = 15
_STAMP_FILES = frozenset({"SKILL.md", "plugin.json", "capability-gate.json", "manifest.json"})
_SKIP_DIRS = frozenset({"node_modules", ".git", "__pycache__", "dist", "build"})

BUCKETS = (
    "missing",
    "version_changed",
    "unrecorded",
    "capability_pending",
    "modified",
    "upgradable",
    "overlap",
)
BUCKET_RANK = {code: index for index, code in enumerate(BUCKETS)}
PROBLEM_STATUSES = {"missing", "version_changed", "modified", "unverified"}
SOURCE_LABEL = {
    "skills_cli_managed": "Skills CLI",
    "local_custom": "自建",
    "runtime_plugin": "运行时插件",
    "runtime_builtin": "运行时内置",
    "third_party_unmanaged": "第三方未托管",
    "third_party_customized": "第三方已定制",
    "unknown": "未知",
    "unrecorded": "未登记",
}
STATUS_LABEL = {
    "aligned": "一致",
    "stale": "未复核",
    "version_changed": "版本已变",
    "modified": "已修改",
    "unverified": "未核实",
    "unrecorded": "未登记",
    "missing": "缺失",
}
FINDING_LABEL = {
    "path_missing": "记录的路径不在",
    "source_missing": "源头文件不在",
    "content_missing": "找不到 SKILL.md",
    "lock_missing": "锁文件里没有这一条",
    "version_changed": "版本目录已换成同级新版本",
    "version_mismatch": "版本和记录不一致",
    "content_hash_mismatch": "内容哈希和基线不一致",
    "lock_hash_mismatch": "锁里的哈希和记录不一致",
    "symlink_target_mismatch": "符号链接目标不一致",
    "symlink_missing": "符号链接不在",
    "review_time_missing": "还没有复核时间",
    "review_older_than_lock": "复核早于锁的更新",
    "baseline_absent": "没有基线",
    "name_mismatch": "名称和清单不一致",
    "lock_source_mismatch": "锁的来源和基线不一致",
    "lock_path_mismatch": "锁的路径和基线不一致",
    "lock_hash_empty": "锁里没有哈希",
    "provider_version_unreadable": "读不出提供者版本",
    "change_unrecorded": "有变更但 changelog 没有对应记录",
}
MARK_RANK = {None: 0, "shared": 1, "missing": 2, "unknown": 3, "direct": 4}
SHARED_LABEL = {
    "unscanned": "未扫描",
    "denied": "无权限",
    "unknown": "无法确定",
    "none": "无入口",
    "broken_link": "坏链",
    "content_missing": "缺 SKILL.md",
    "link": "链接",
    "dir": "目录",
}
SKILLS_SCHEMA = 2
_DENIED_ERRNOS = frozenset({errno.EACCES, errno.EPERM})


def _denied(exc: OSError) -> bool:
    """EACCES/EPERM or a PermissionError raised without an explicit errno."""
    return isinstance(exc, PermissionError) or exc.errno in _DENIED_ERRNOS
# Indirections so tests can inject PermissionError / OSError into the probe.
_lstat = os.lstat
_stat = os.stat
_readlink = os.readlink
_realpath = lambda p: os.path.realpath(p, strict=True)


def _read_head(path):
    with open(path, "rb") as handle:
        return handle.read(1)
SUMMARY_SOURCES = (
    ("", "全部"),
    ("skills_cli_managed", "Skills CLI"),
    ("local_custom", "自建"),
    ("runtime_plugin", "运行时插件"),
    ("runtime_builtin", "运行时内置"),
    ("third_party_unmanaged", "第三方未托管"),
    ("unrecorded", "未登记"),
    ("grok_bot", "Grok Bot"),
    ("grok_bot_export", "Grok Bot 导出"),
)

_CACHE: dict[tuple, tuple[dict[str, Any], float]] = {}


def parse_review_days(value: str | None) -> int:
    if value is None or value == "":
        return DEFAULT_REVIEW_DAYS
    try:
        days = int(value)
    except ValueError as exc:
        raise ValueError("review_days must be 30, 90, or 180") from exc
    if days not in REVIEW_DAYS:
        raise ValueError("review_days must be 30, 90, or 180")
    return days


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _stat_token(path: Path) -> tuple:
    """Change when the path is rewritten, replaced, or retargeted.

    ``lstat`` sees the symlink itself. The link text and the followed
    mtime catch a retarget and a write through the link.
    """
    try:
        st = path.lstat()
    except OSError:
        return (str(path), "missing")
    token: tuple = (str(path), st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_mode, st.st_ino, stat.S_ISLNK(st.st_mode))
    if stat.S_ISLNK(st.st_mode):
        try:
            link = os.readlink(path)
        except OSError:
            link = ""
        followed: tuple = ("dangling",)
        try:
            target = path.stat()
            followed = (target.st_mtime_ns, target.st_size)
        except OSError:
            pass
        token = token + (link, followed)
    return token


def _stamp_tree(root: Path) -> tuple:
    """Stamp the files a drift or staging read actually depends on."""
    if not root.exists():
        return ("absent", str(root))
    if root.is_symlink() or root.is_file():
        return (_stat_token(root),)
    stamps = [_stat_token(root)]
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept = []
        for name in dirnames:
            child = current / name
            if child.is_symlink():
                stamps.append(_stat_token(child))
            if name not in _SKIP_DIRS:
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            child = current / name
            if name in _STAMP_FILES or child.is_symlink():
                stamps.append(_stat_token(child))
    return tuple(stamps)


def _plugin_container(path: Path) -> Path | None:
    """Directory that holds sibling ``<version>`` folders for a Codex plugin file."""
    if path.name == "plugin.json" and path.parent.name == ".codex-plugin":
        version_dir = path.parent.parent
        if version_dir.parent != version_dir:
            return version_dir.parent
    return None


def _stamp_install_path(path: Path) -> tuple:
    tokens = [_stat_token(path)]
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if path.suffix.lower() != ".md":
        tokens.append(_stat_token(resolved / "SKILL.md"))
    container = _plugin_container(path)
    if container is not None:
        tokens.append(_stamp_tree(container))
    # Installation discovery fingerprints skill roots and new siblings. A
    # generic parent can be a runtime home whose unrelated writes would bust
    # both document and conflict caches on every request. Only plugin version
    # discovery above needs a parent container dependency here.
    return tuple(tokens)


def _tool_root_paths(tool_roots_path: Path, repo_root: Path) -> list[Path]:
    try:
        document = json.loads(tool_roots_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return []
    roots = document.get("roots") if isinstance(document, dict) else None
    paths = []
    for item in roots or []:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        raw = str(item["path"])
        paths.append((repo_root / raw) if item.get("repo") else Path(raw).expanduser())
    return paths


def _install_stamp(inventory_path: Path, tool_roots_path: Path, repo_root: Path, staging_root: Path, scan_roots: list[Path] | None = None) -> tuple:
    """Fingerprint installs. Inventory mtime alone misses these writes."""
    stamps: list = [_stamp_tree(staging_root)]
    for root in _tool_root_paths(tool_roots_path, repo_root):
        stamps.append(_stamp_tree(root))
    try:
        document = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        document = None
    # Shared-root probe dependencies: the root itself, its canonical form,
    # and each inventory name's entry + SKILL.md inside it.
    try:
        probe_roots, _ = _load_tool_roots(tool_roots_path, repo_root)
    except (OSError, json.JSONDecodeError, UnicodeError, ValueError, KeyError, TypeError, AttributeError):
        probe_roots = []
    shared = _shared_root(probe_roots)
    skills = document.get("skills") if isinstance(document, dict) else None
    extra_roots = scan_roots if scan_roots is not None else (
        drift_report.machine_roots(document, repo_root, [], True) if isinstance(skills, list) else [])
    disk_roots = _tool_root_paths(tool_roots_path, repo_root) + list(extra_roots)
    stamps.append(scan_installs(disk_roots)["stamp"])
    if isinstance(skills, list):
        for bundle in _bundle_roots([skill for skill in skills if isinstance(skill, dict)], repo_root):
            stamps.append(_stat_token(bundle["manifest"]))
            if bundle["provider"] is not None:
                stamps.append(_stamp_tree(bundle["provider"]))
    if shared is not None:
        shared_dir = Path(shared["expanded"])
        stamps.append(_stat_token(shared_dir))
        stamps.append(("shared_realpath", os.path.realpath(shared_dir)))
        if isinstance(skills, list):
            for skill in skills:
                if not isinstance(skill, dict):
                    continue
                name = skill.get("name")
                if isinstance(name, str) and name.strip() and "/" not in name and name not in (".", ".."):
                    stamps.append(_stat_token(shared_dir / name))
                    stamps.append(_stat_token(shared_dir / name / "SKILL.md"))
    stamps.append(_stat_token(repo_root))
    stamps.append(("repo_realpath", os.path.realpath(repo_root)))
    stamps.append(("repo_root", str(repo_root)))
    if isinstance(skills, list):
        for skill in skills:
            if not isinstance(skill, dict):
                continue
            raw_paths: list[str] = []
            baseline = skill.get("baseline") if isinstance(skill.get("baseline"), dict) else {}
            subject = baseline.get("subject")
            if isinstance(subject, str) and subject.strip():
                raw_paths.append(subject)
            authority = skill.get("authority") if isinstance(skill.get("authority"), dict) else {}
            canonical = authority.get("canonical_path")
            if isinstance(canonical, str) and canonical.strip():
                raw_paths.append(canonical)
            for value in skill.get("visible_paths") or []:
                if isinstance(value, str) and value.strip():
                    raw_paths.append(value)
            for raw in raw_paths:
                stamps.append(_stamp_install_path(drift_report.inventory.locate_path(raw, repo_root)))
    return tuple(stamps)


def _read_inventory(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("skills inventory root must be an object")
    skills = document.get("skills")
    if not isinstance(skills, list):
        raise ValueError("skills inventory has no skills array")
    for index, skill in enumerate(skills):
        if not isinstance(skill, dict):
            raise ValueError(f"skills[{index}] must be an object")
        name = skill.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"skills[{index}] has no name")
        source_class = skill.get("source_class")
        if not isinstance(source_class, str) or not source_class.strip():
            raise ValueError(f"skills[{index}] source_class must be a string")
        if not isinstance(skill.get("authority"), dict):
            raise ValueError(f"skills[{index}] authority must be an object")
        if not isinstance(skill.get("baseline"), dict):
            raise ValueError(f"skills[{index}] baseline must be an object")
    return document


def _clip(value: str, limit: int = 80) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _prefer(current: str | None, new: str | None) -> str | None:
    if new is None:
        return current
    if current is None or BUCKET_RANK[new] < BUCKET_RANK[current]:
        return new
    return current


def structural_bucket(item: dict[str, Any]) -> str | None:
    """Bucket from drift / conflict only. Staging is applied afterwards."""
    if item.get("origin") == "unrecorded":
        return "unrecorded"
    if item.get("origin") == "bundle_member":
        return None
    if item.get("origin") == "staging":
        return None
    status = item.get("reconciliation_status")
    if status == "missing":
        return "missing"
    if status == "version_changed":
        return "version_changed"
    if status in {"modified", "unverified"}:
        return "modified"
    if item.get("conflict_status") == "open":
        return "overlap"
    return None


def is_baseline_debt(item: dict[str, Any], review_days: int, now: datetime) -> bool:
    if item.get("bucket"):
        return False
    if item.get("reconciliation_status") not in {"aligned", "stale"}:
        return False
    reviewed = drift_report.parse_time(item.get("review_time"))
    if reviewed is None:
        return True
    return now - reviewed > timedelta(days=review_days)


def assign_buckets(
    items: list[dict[str, Any]],
    gates: dict[str, dict[str, Any]],
    *,
    review_days: int,
    now: datetime,
) -> None:
    """Set ``bucket``, ``tags``, ``reason``, and ``baseline_debt`` in place.

    One skill, one bucket, first match in ``BUCKETS`` order. Other hits become tags.
    """
    for item in items:
        hits: list[str] = []
        primary = structural_bucket(item)
        if primary:
            hits.append(primary)
        if "unrecorded" not in hits and any(entry.get("registration") == "unrecorded" for entry in item.get("entries", [])):
            hits.append("unrecorded")
        gate = gates.get(item["name"])
        if gate and (gate.get("blocked") or gate.get("backend") == "fallback"):
            hits.append("capability_pending")
            item["capability_gate"] = {
                "backend": gate.get("backend"),
                "blocked": bool(gate.get("blocked")),
                "fallback": gate.get("fallback"),
                "reason": gate.get("reason") or gate.get("summary"),
            }
        if item.get("upgrade_status") == "update":
            hits.append("upgradable")
        if (item.get("conflict_status") == "open" or item.get("conflict_findings")) and "overlap" not in hits:
            hits.append("overlap")
        winner = None
        for code in hits:
            winner = _prefer(winner, code)
        item["bucket"] = winner
        item["tags"] = [code for code in hits if code != winner]
        item["baseline_debt"] = is_baseline_debt(item, review_days, now)
        item["reason"] = _clip(reason_for(item))
        item["quiet"] = bool(item["baseline_debt"])


def reason_for(item: dict[str, Any]) -> str:
    bucket = item.get("bucket")
    current = item.get("current_version")
    observed = item.get("observed_version")
    if bucket == "version_changed" and (current or observed):
        return f"{current or '—'} → {observed or '—'}"
    if bucket == "unrecorded":
        return item.get("install_path") or "未登记安装"
    if bucket == "capability_pending":
        gate = item.get("capability_gate") or {}
        if gate.get("backend") == "fallback":
            return "门禁走了回退，不能当成通过"
        return gate.get("reason") or "能力变化被拦住"
    if bucket == "missing":
        return "记录的路径不在"
    if bucket == "modified":
        return "内容或链接和基线不一致"
    if bucket == "overlap":
        sources = []
        if item.get("conflict_findings"):
            sources.append(f"实时检测 {len(item['conflict_findings'])} 条")
        if item.get("conflict_status") == "open":
            sources.append("人工标记 open")
        return "；".join(sources)
    if bucket == "upgradable":
        upgrade = item.get("upgrade") if isinstance(item.get("upgrade"), dict) else {}
        installed = upgrade.get("installed") or current or "—"
        latest = upgrade.get("latest") or "上游"
        return f"{_short_version(installed)} → {_short_version(latest)}"
    return ""


def _short_version(value: object) -> str:
    text = value.strip() if isinstance(value, str) else str(value)
    if re.fullmatch(r"[0-9a-fA-F]{40}", text):
        return text[:7]
    return text


def _load_tool_roots(path: Path, repo_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    tools = [
        {"id": item["id"], "label": item["label"]}
        for item in document.get("tools") or []
        if isinstance(item, dict) and item.get("id") and item.get("label")
    ]
    roots = []
    for order, item in enumerate(document.get("roots") or []):
        if not isinstance(item, dict) or not item.get("path"):
            continue
        raw = str(item["path"])
        if item.get("repo"):
            lexical = os.path.abspath(repo_root / raw)
            expanded = os.path.realpath(repo_root / raw)
        else:
            lexical = os.path.abspath(Path(raw).expanduser())
            expanded = os.path.realpath(Path(raw).expanduser())
        roots.append(
            {
                "path": raw,
                "lexical": lexical.rstrip("/"),
                "expanded": expanded.rstrip("/"),
                "repo": bool(item.get("repo")),
                "order": order,
                "tools": [tool for tool in item.get("tools") or [] if isinstance(tool, str)],
                "kind": "shared" if item.get("kind") == "shared" else "direct",
            }
        )
    roots.sort(key=lambda item: len(item["expanded"]), reverse=True)
    return roots, tools


def _match_root(expanded: str, roots: list[dict[str, Any]], key: str = "expanded") -> dict[str, Any] | None:
    for root in sorted(roots, key=lambda item: len(item[key]), reverse=True):
        base = root[key]
        if expanded == base or expanded.startswith(base + "/"):
            return root
    return None


def _expand(raw: str, repo_root: Path) -> str:
    if raw.startswith("~") or raw.startswith("/"):
        return os.path.realpath(Path(raw).expanduser())
    return os.path.realpath(repo_root / raw)


def _lexical(raw: str, repo_root: Path) -> str:
    """Absolute path without resolving links, matching how the root was configured."""
    if raw.startswith("~") or raw.startswith("/"):
        return os.path.abspath(os.path.expanduser(raw))
    return os.path.abspath(repo_root / raw)


def _shared_root(roots: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [root for root in roots if root["kind"] == "shared" and not root.get("repo")]
    if not candidates:
        return None
    return min(candidates, key=lambda root: root["order"])


def _bump(current: str | None, mark: str) -> str | None:
    if MARK_RANK[mark] > MARK_RANK[current]:
        return mark
    return current


def _classify_entry(located: Path) -> tuple[str, str | None, bool]:
    """(state, readlink_text, is_link) for one entry path, via the indirections."""
    try:
        lst = _lstat(located)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return "absent", None, False
        if _denied(exc):
            return "denied", None, False
        return "unknown", None, False
    is_link = stat.S_ISLNK(lst.st_mode)
    if not is_link:
        return "present", None, False
    try:
        link_text = _readlink(located)
    except OSError:
        link_text = None
    try:
        _stat(located)
        return "present", link_text, True
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
            return "broken_link", link_text, True
        if _denied(exc):
            return "denied", link_text, True
        return "unknown", link_text, True


def _scope_of(root: dict[str, Any] | None, lexical: str, resolved: str | None, repo_ctx: dict[str, Any] | None) -> str:
    if root is not None:
        return "repo_entry" if root.get("repo") else "user"
    if repo_ctx is not None:
        repo_lexical = repo_ctx["lexical"]
        repo_resolved = repo_ctx.get("resolved")
        if lexical == repo_lexical or lexical.startswith(repo_lexical + os.sep):
            return "repo_source"
        if repo_resolved and resolved and (resolved == repo_resolved or resolved.startswith(repo_resolved + os.sep)):
            return "repo_source"
    return "other"


def _evidence_sort_key(record: dict[str, Any], tool_order: dict[str, int]) -> tuple:
    harness = record["harness"]
    head = (0, tool_order[harness]) if harness in tool_order else (1, harness)
    return (
        head,
        0 if record["kind"] == "entry" else 1,
        {"direct": 0, "shared": 1, "missing": 2, "unknown": 3}[record["attribution"]],
        record["lexical"],
        record["resolved"] or "",
    )


def tool_marks(
    paths: list[str],
    roots: list[dict[str, Any]],
    repo_root: Path,
    repo_ctx: dict[str, Any] | None = None,
    tool_order: dict[str, int] | None = None,
) -> tuple[dict[str, str | None], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    tool_ids = {tool for root in roots for tool in root["tools"]}
    marks: dict[str, str | None] = {tool: None for tool in tool_ids}
    tool_order = tool_order or {}
    entries = []
    evidence = []
    seen_lexical: set[str] = set()
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip() or not drift_report.is_concrete(raw):
            continue
        located = drift_report.inventory.locate_path(raw, repo_root)
        # Hash-resolved plugin paths should match the root of the concrete file.
        lexical = str(located) if "*" in raw else _lexical(raw, repo_root)
        # The same path can arrive via visible_paths, adapters and canonical_path.
        if lexical in seen_lexical:
            continue
        seen_lexical.add(lexical)
        entry_index = len(entries)
        e_state, link_text, is_link = _classify_entry(located)
        present = e_state == "present"
        expanded = _expand(raw, repo_root) if "*" not in raw else str(located)
        resolved = None
        if present:
            try:
                candidate = _realpath(located)
            except OSError:
                candidate = None
            if candidate and candidate != lexical:
                resolved = candidate
        # Direct attribution: where the entry itself sits, before following links.
        root = _match_root(lexical, roots, "lexical")
        target_root = None
        if root is not None:
            if resolved and resolved != lexical:
                found = _match_root(resolved, roots)
                if found is not None and found is not root:
                    target_root = found
        else:
            # No lexical match: keep the resolved/target match as primary.
            root = _match_root(expanded, roots) or _match_root(str(located), roots)
        if e_state == "present":
            entry_state = "present"
            attribution = root["kind"] if root else None
        elif e_state in ("absent", "broken_link"):
            entry_state = e_state
            attribution = "missing" if root else None
        else:
            entry_state = e_state  # denied | unknown
            attribution = "unknown" if root else None
        scope = _scope_of(root, lexical, resolved, repo_ctx)
        tools = root["tools"] if root else []
        mark = attribution
        base = {
            "path": raw,
            "lexical": lexical,
            "readlink": link_text,
            "resolved": resolved,
            "entry_index": entry_index,
        }
        if root is not None:
            record_repo = bool(root.get("repo"))
            for tool in tools:
                evidence.append(
                    {
                        **base,
                        "harness": tool,
                        "kind": "entry",
                        "attribution": attribution,
                        "state": entry_state,
                        "root": root["path"],
                        "repo": record_repo,
                        "repo_id": repo_ctx["id"] if (repo_ctx and record_repo) else None,
                        "scope": "repo_entry" if record_repo else "user",
                    }
                )
        if target_root is not None:
            target_scope = "repo_entry" if target_root.get("repo") else "user"
            for tool in target_root["tools"]:
                evidence.append(
                    {
                        **base,
                        "harness": tool,
                        "kind": "target",
                        "attribution": target_root["kind"],
                        "state": "present",
                        "root": target_root["path"],
                        "repo": bool(target_root.get("repo")),
                        "repo_id": repo_ctx["id"] if (repo_ctx and target_root.get("repo")) else None,
                        "scope": target_scope,
                    }
                )
        entries.append(
            {
                "path": raw,
                "present": present,
                "symlink": link_text is not None,
                "target": link_text,
                "tools": tools,
                "mark": mark,
                "resolved": resolved,
                "location": root["path"] if root else None,
                "repo_id": repo_ctx["id"] if (repo_ctx and scope in ("repo_entry", "repo_source")) else None,
                "scope": scope,
            }
        )
    for record in evidence:
        marks[record["harness"]] = _bump(marks.get(record["harness"]), record["attribution"])
    evidence.sort(key=lambda record: _evidence_sort_key(record, tool_order))
    repo_ids = sorted({entry["repo_id"] for entry in entries if entry.get("repo_id")})
    return marks, entries, evidence, repo_ids


def _shared_entry(name: str, shared: dict[str, Any] | None) -> dict[str, Any]:
    """Probe ``<shared_root>/<name>``; ordered steps, first terminal wins."""
    raw = shared["path"] if shared else None
    path = f"{raw.rstrip('/')}/{name}" if (raw and isinstance(name, str)) else None

    def result(
        state: str,
        reason: str,
        step: str,
        detail: str,
        *,
        err: int | None = None,
        target: str | None = None,
        resolved: str | None = None,
        link_kind: str | None = None,
        target_scope: str | None = None,
    ) -> dict[str, Any]:
        return {
            "state": state,
            "label": SHARED_LABEL[state],
            "reason": reason,
            "errno": err,
            "step": step,
            "path": path,
            "target": target,
            "resolved": resolved,
            "link_kind": link_kind,
            "target_scope": target_scope,
            "detail": detail,
            "root": raw,
        }

    # P0 preconditions: no syscalls.
    if shared is None:
        return result("unscanned", "no_shared_root", "P0", "配置里没有共享根")
    try:
        if not isinstance(name, str) or not name or "/" in name or "\x00" in name or name in (".", ".."):
            return result("unknown", "invalid_name", "P0", "名称不是合法的目录名")
        base = shared["expanded"]
        # S1: shared root itself.
        try:
            root_stat = _stat(base)
        except OSError as exc:
            err = exc.errno
            if _denied(exc):
                return result("denied", "root_denied", "S1", "共享根目录无权限", err=err)
            if err in (errno.ENOENT, errno.ENOTDIR):
                return result("none", "root_absent", "S1", "共享根目录不存在", err=err)
            if err == errno.ELOOP:
                return result("unknown", "root_loop", "S1", "共享根目录路径存在链接循环", err=err)
            return result("unknown", "root_error", "S1", "共享根目录无法读取", err=err)
        if not stat.S_ISDIR(root_stat.st_mode):
            return result("unknown", "root_not_dir", "S1", "共享根路径不是目录")
        # S2: the entry itself.
        entry = os.path.join(base, name)
        try:
            entry_stat = _lstat(entry)
        except OSError as exc:
            err = exc.errno
            if err == errno.ENOENT:
                return result("none", "entry_absent", "S2", "共享目录下没有这个入口", err=err)
            if _denied(exc):
                return result("denied", "entry_denied", "S2", "共享入口无权限", err=err)
            return result("unknown", "entry_error", "S2", "共享入口无法读取", err=err)
        is_link = stat.S_ISLNK(entry_stat.st_mode)
        # S3: link text.
        target = None
        link_kind = None
        if is_link:
            try:
                target = _readlink(entry)
            except OSError as exc:
                err = exc.errno
                if _denied(exc):
                    return result("denied", "readlink_denied", "S3", "链接文本无权限", err=err)
                return result("unknown", "readlink_error", "S3", "链接文本无法读取", err=err)
            link_kind = "absolute" if os.path.isabs(target) else "relative"
        # S4: follow the entry.
        try:
            followed = _stat(entry)
        except OSError as exc:
            err = exc.errno
            if is_link and err in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
                reason = {errno.ENOENT: "target_absent", errno.ENOTDIR: "target_enotdir", errno.ELOOP: "link_loop"}[err]
                return result("broken_link", reason, "S4", "链接目标不存在", err=err, target=target, link_kind=link_kind)
            if _denied(exc):
                return result("denied", "target_denied" if is_link else "entry_denied", "S4", "链接目标无权限" if is_link else "共享入口无权限", err=err, target=target, link_kind=link_kind)
            return result("unknown", "target_error" if is_link else "entry_error", "S4", "链接目标无法读取" if is_link else "共享入口无法读取", err=err, target=target, link_kind=link_kind)
        if not stat.S_ISDIR(followed.st_mode):
            return result("content_missing", "not_a_directory", "S4", "入口不是目录", target=target, link_kind=link_kind)
        # S5: canonical path (informational scope only).
        try:
            resolved = _realpath(entry)
        except PermissionError as exc:
            return result("denied", "realpath_denied", "S5", "无法解析入口路径", err=exc.errno, target=target, link_kind=link_kind)
        except OSError as exc:
            return result("unknown", "realpath_error", "S5", "无法解析入口路径", err=exc.errno, target=target, link_kind=link_kind)
        target_scope = None
        if is_link:
            target_scope = "inside_shared_root" if resolved == base or resolved.startswith(base + os.sep) else "outside_shared_root"
        # S6: SKILL.md inside the entry.
        skill_md = os.path.join(entry, "SKILL.md")
        try:
            md_stat = _lstat(skill_md)
        except OSError as exc:
            err = exc.errno
            if err in (errno.ENOENT, errno.ENOTDIR):
                return result("content_missing", "skill_md_absent", "S6", "没有 SKILL.md", err=err, target=target, resolved=resolved, link_kind=link_kind, target_scope=target_scope)
            if _denied(exc):
                return result("denied", "skill_md_denied", "S6", "SKILL.md 无权限", err=err, target=target, resolved=resolved, link_kind=link_kind, target_scope=target_scope)
            if err == errno.ELOOP:
                return result("content_missing", "skill_md_dangling", "S6", "SKILL.md 是失效链接", err=err, target=target, resolved=resolved, link_kind=link_kind, target_scope=target_scope)
            return result("unknown", "skill_md_error", "S6", "SKILL.md 无法读取", err=err, target=target, resolved=resolved, link_kind=link_kind, target_scope=target_scope)
        if stat.S_ISLNK(md_stat.st_mode):
            try:
                md_stat = _stat(skill_md)
            except OSError as exc:
                err = exc.errno
                if err in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
                    return result("content_missing", "skill_md_dangling", "S6", "SKILL.md 是失效链接", err=err, target=target, resolved=resolved, link_kind=link_kind, target_scope=target_scope)
                if _denied(exc):
                    return result("denied", "skill_md_denied", "S6", "SKILL.md 无权限", err=err, target=target, resolved=resolved, link_kind=link_kind, target_scope=target_scope)
                return result("unknown", "skill_md_error", "S6", "SKILL.md 无法读取", err=err, target=target, resolved=resolved, link_kind=link_kind, target_scope=target_scope)
        if not stat.S_ISREG(md_stat.st_mode):
            return result("content_missing", "skill_md_not_file", "S6", "SKILL.md 不是普通文件", target=target, resolved=resolved, link_kind=link_kind, target_scope=target_scope)
        if md_stat.st_size == 0:
            return result("content_missing", "skill_md_empty", "S6", "SKILL.md 是空文件", target=target, resolved=resolved, link_kind=link_kind, target_scope=target_scope)
        try:
            _read_head(skill_md)
        except PermissionError as exc:
            return result("denied", "skill_md_unreadable", "S6", "SKILL.md 不可读", err=exc.errno, target=target, resolved=resolved, link_kind=link_kind, target_scope=target_scope)
        except OSError as exc:
            return result("unknown", "skill_md_read_error", "S6", "SKILL.md 读取失败", err=exc.errno, target=target, resolved=resolved, link_kind=link_kind, target_scope=target_scope)
        # S7: success.
        if is_link:
            return result("link", "ok", "S7", "链接到共享目录里的有效入口", target=target, resolved=resolved, link_kind=link_kind, target_scope=target_scope)
        return result("dir", "ok", "S7", "共享目录下的目录", resolved=resolved)
    except Exception:
        return result("unknown", "probe_exception", "??", "探测失败")


def _shared_root_state(shared: dict[str, Any] | None) -> dict[str, Any]:
    if shared is None:
        return {"path": None, "state": "unscanned", "reason": "no_shared_root", "detail": "未配置共享目录"}
    try:
        probe = _stat(shared["expanded"])
    except OSError as exc:
        err = exc.errno
        if _denied(exc):
            return {"path": shared["path"], "state": "denied", "reason": "root_denied", "detail": "共享根目录无权限"}
        if err in (errno.ENOENT, errno.ENOTDIR):
            return {"path": shared["path"], "state": "absent", "reason": "root_absent", "detail": "共享根目录不存在"}
        if err == errno.ELOOP:
            return {"path": shared["path"], "state": "unknown", "reason": "root_loop", "detail": "共享根目录路径存在链接循环"}
        return {"path": shared["path"], "state": "unknown", "reason": "root_error", "detail": "共享根目录无法读取"}
    if not stat.S_ISDIR(probe.st_mode):
        return {"path": shared["path"], "state": "unknown", "reason": "root_not_dir", "detail": "共享根路径不是目录"}
    return {"path": shared["path"], "state": "ok", "reason": "ok", "detail": "共享目录已配置"}


def _channel(skill: dict[str, Any]) -> tuple[str, str]:
    source = skill.get("source_class") or ""
    label = SOURCE_LABEL.get(source, source or "—")
    ref = skill.get("baseline_ref") if isinstance(skill.get("baseline_ref"), str) else ""
    authority = skill.get("authority") if isinstance(skill.get("authority"), dict) else {}
    canonical = authority.get("canonical_path")
    if source == "skills_cli_managed" and ref.startswith("github:"):
        return label, ref
    if source == "runtime_plugin" and ref.startswith("plugin:"):
        return label, ref[len("plugin:") :]
    if source == "runtime_builtin" and ref:
        return label, ref
    if isinstance(canonical, str) and canonical.strip():
        return label, canonical
    visible = [value for value in skill.get("visible_paths") or [] if isinstance(value, str)]
    return label, visible[0] if visible else "—"


def _constraints(skill: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for item in skill.get("compatibility_constraints") or []:
        if isinstance(item, dict):
            rows.append({"kind": str(item.get("kind") or ""), "value": str(item.get("value") or "")})
        elif isinstance(item, str) and item:
            rows.append({"kind": "note", "value": item})
    return rows


def _findings(codes: list[str]) -> list[dict[str, str]]:
    hidden = {code for code in codes if code.endswith("_match")}
    return [
        {"code": code, "label": FINDING_LABEL.get(code, "对账发现待复核（原因说明待补充）")}
        for code in codes
        if code not in hidden
    ]


def _frontmatter_description(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    for line in text[3:end].splitlines():
        if not line.startswith("description:"):
            continue
        value = " ".join(line.split(":", 1)[1].split()).strip("\"'")
        return value or None
    return None


def _local_source(skill: dict[str, Any], repo_root: Path) -> dict[str, str] | None:
    """Content fingerprint and metadata of the repository's canonical SKILL.md."""
    authority = skill.get("authority") if isinstance(skill.get("authority"), dict) else {}
    canonical = authority.get("canonical_path")
    name = skill.get("name")
    candidates: list[Path] = []
    if isinstance(canonical, str) and canonical.strip() and not canonical.startswith(("~", "/")):
        candidates.append(repo_root / canonical / "SKILL.md")
    if isinstance(name, str) and name and "/" not in name and "\x00" not in name and name not in (".", ".."):
        candidates.append(repo_root / "skills" / name / "SKILL.md")
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            st = path.stat()
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        try:
            rel = path.resolve().relative_to(repo_root.resolve()).as_posix()
        except (OSError, ValueError):
            continue
        if not rel.startswith("skills/") or not rel.endswith("/SKILL.md"):
            continue
        found: dict[str, str] = {
            "path": rel,
            "updated_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            found["content_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            pass
        summary = _frontmatter_description(path)
        if summary:
            found["summary"] = summary
        return found
    return None


def _grok_skill_root(root: dict[str, Any]) -> bool:
    raw = str(root.get("path") or "").rstrip("/")
    return raw in {"~/.grok/skills", ".grok/skills"} or raw.endswith("/.grok/skills")


def _attach_grok_symlinks(items: list[dict[str, Any]], roots: list[dict[str, Any]]) -> None:
    """Symlinks in ~/.grok/skills are a direct Grok CLI entry even when the inventory omitted that path."""
    grok_roots = [root for root in roots if _grok_skill_root(root)]
    if not grok_roots:
        return
    by_name: dict[str, dict[str, Any]] = {}
    for item in items:
        name = item.get("name")
        if isinstance(name, str) and name:
            by_name.setdefault(name, item)
    for root in grok_roots:
        base = Path(root["expanded"])
        try:
            children = list(base.iterdir())
        except OSError:
            continue
        for child in children:
            if child.name.startswith(".") or child.name not in by_name:
                continue
            try:
                if not child.is_symlink():
                    continue
                link_text = os.readlink(child)
            except OSError:
                continue
            item = by_name[child.name]
            marks = item.setdefault("tools", {})
            marks["grok"] = _bump(marks.get("grok"), "direct")
            path = str(child)
            evidence = item.setdefault("evidence", [])
            if any(record.get("harness") == "grok" and record.get("root") == root.get("path") and record.get("readlink") == link_text for record in evidence):
                continue
            evidence.append(
                {
                    "path": path,
                    "lexical": path,
                    "readlink": link_text,
                    "resolved": None,
                    "entry_index": None,
                    "harness": "grok",
                    "kind": "entry",
                    "attribution": "direct",
                    "state": "present",
                    "root": root["path"],
                    "repo": bool(root.get("repo")),
                    "repo_id": None,
                    "scope": "repo_entry" if root.get("repo") else "user",
                }
            )
            item.setdefault("entries", []).append(
                {
                    "path": path,
                    "present": True,
                    "symlink": True,
                    "target": link_text,
                    "tools": ["grok"],
                    "mark": "direct",
                    "resolved": None,
                    "location": root["path"],
                    "repo_id": None,
                    "scope": "repo_entry" if root.get("repo") else "user",
                }
            )


def _command(bucket: str | None) -> str:
    if bucket == "capability_pending":
        return "python3 skills-governance/scripts/skill_guard.py diff --help"
    if bucket == "unrecorded":
        return "python3 skills-governance/scripts/validate_skills_inventory.py --check-paths"
    return "python3 skills-governance/scripts/drift_report.py --format text"


def _inventory_item(
    skill: dict[str, Any],
    row: dict[str, Any],
    roots: list[dict[str, Any]],
    repo_root: Path,
    repo_ctx: dict[str, Any] | None = None,
    tool_order: dict[str, int] | None = None,
) -> dict[str, Any]:
    authority = skill.get("authority") if isinstance(skill.get("authority"), dict) else {}
    baseline = skill.get("baseline") if isinstance(skill.get("baseline"), dict) else {}
    paths = [value for value in skill.get("visible_paths") or [] if isinstance(value, str)]
    for adapter in skill.get("adapters") or []:
        if isinstance(adapter, dict) and isinstance(adapter.get("path"), str):
            paths.append(adapter["path"])
    canonical = authority.get("canonical_path")
    if isinstance(canonical, str):
        paths.append(canonical)
    marks, entries, evidence, repo_ids = tool_marks(paths, roots, repo_root, repo_ctx, tool_order)
    for entry in entries:
        entry.update(registration="inventory", source_class=skill.get("source_class"), runtime_loaded=None)
    label, detail = _channel(skill)
    canonical_text = canonical if isinstance(canonical, str) else None
    item = {
        "name": skill.get("name"),
        "origin": "inventory",
        "source_class": skill.get("source_class"),
        "source_label": SOURCE_LABEL.get(skill.get("source_class"), skill.get("source_class") or "—"),
        "scope": skill.get("scope"),
        "ownership": authority.get("ownership"),
        "source_authority": authority.get("source_authority"),
        "update_authority": authority.get("update_authority"),
        "canonical_path": canonical_text,
        "repo_is_source": bool(canonical_text) and not canonical_text.startswith(("~", "/")),
        "channel_label": label,
        "channel_detail": detail,
        "current_version": row.get("current_version"),
        "observed_version": row.get("observed_version"),
        "baseline_kind": baseline.get("kind"),
        "baseline_subject": baseline.get("subject") if isinstance(baseline.get("subject"), str) else None,
        "baseline_ref": row.get("baseline_ref"),
        "review_time": row.get("review_time"),
        "reconciliation_status": row.get("reconciliation_status"),
        "status_label": STATUS_LABEL.get(row.get("reconciliation_status"), row.get("reconciliation_status")),
        "findings": _findings(list(row.get("findings") or [])),
        "customization_mode": skill.get("customization_mode"),
        "constraints": _constraints(skill),
        "conflict_status": skill.get("conflict_status"),
        "tools": marks,
        "entries": entries,
        "evidence": evidence,
        "repo_ids": repo_ids,
        "install_path": None,
        "capability_gate": None,
    }
    item["constraint_count"] = len(item["constraints"])
    local = _local_source(skill, repo_root)
    if local:
        item["local_source"] = local
    return item


def _unrecorded_item(
    row: dict[str, Any],
    roots: list[dict[str, Any]],
    repo_root: Path,
    repo_ctx: dict[str, Any] | None = None,
    tool_order: dict[str, int] | None = None,
) -> dict[str, Any]:
    path = row.get("path") or ""
    marks, entries, evidence, repo_ids = tool_marks([path] if path else [], roots, repo_root, repo_ctx, tool_order)
    return {
        "name": row.get("name") or path or "unrecorded",
        "origin": "unrecorded",
        "source_class": "unrecorded",
        "source_label": "未登记",
        "scope": None,
        "ownership": None,
        "source_authority": None,
        "update_authority": None,
        "canonical_path": None,
        "repo_is_source": False,
        "channel_label": "未登记",
        "channel_detail": path or "—",
        "current_version": None,
        "observed_version": None,
        "baseline_kind": None,
        "baseline_subject": None,
        "baseline_ref": None,
        "review_time": None,
        "reconciliation_status": "unrecorded",
        "status_label": "未登记",
        "findings": [{"code": "unrecorded", "label": "清单里没有这条安装"}],
        "customization_mode": None,
        "constraints": [],
        "constraint_count": 0,
        "conflict_status": None,
        "tools": marks,
        "entries": entries,
        "evidence": evidence,
        "repo_ids": repo_ids,
        "install_path": path,
        "capability_gate": None,
    }


def _bundle_roots(skills: list[dict], repo_root: Path) -> list[dict]:
    """Package containment is evidence, never a blanket builtin assertion."""
    bundles = []
    for skill in skills:
        if not drift_report.is_bundle(skill):
            continue
        paths = list(skill.get("visible_paths") or [])
        paths += [adapter.get("path") for adapter in skill.get("adapters", []) if isinstance(adapter, dict)]
        paths.append((skill.get("authority") or {}).get("canonical_path"))
        for raw in paths:
            if not isinstance(raw, str) or not drift_report.is_concrete(raw):
                continue
            path = Path(_lexical(raw, repo_root))
            if not path.is_dir() or (path / "SKILL.md").is_file():
                continue
            manifest = path / ".bundled_manifest"
            names = set()
            # Hermes owns this manifest format. Do not interpret arbitrary
            # provider files as evidence for other runtimes.
            if skill.get("name") == "hermes-builtin-skills":
                try:
                    names = {line.partition(":")[0].strip() for line in manifest.read_text(encoding="utf-8-sig").splitlines() if line.strip()}
                except (OSError, UnicodeError):
                    pass
            subject = (skill.get("baseline") or {}).get("subject")
            provider = Path(_lexical(subject, repo_root)).parent / "skills" if isinstance(subject, str) else None
            bundles.append({"path": path, "skill": skill, "manifest": manifest, "names": names, "provider": provider})
    return sorted(bundles, key=lambda b: len(str(b["path"])), reverse=True)


def _discovered_metadata(path: str, name: str, found: dict | None, bundles: list[dict]) -> dict:
    metadata = {"registration": "unrecorded", "source_class": "unrecorded", "runtime_loaded": None}
    if found:
        metadata.update({key: found[key] for key in ("root", "relative_path", "category", "symlink_chain")})
    for bundle in bundles:
        try:
            relative = Path(path).relative_to(bundle["path"])
        except ValueError:
            continue
        skill = bundle["skill"]
        metadata["package"] = skill["name"]
        metadata["package_path"] = str(bundle["path"])
        provider = bundle["provider"]
        # Manifest names alone cannot distinguish two same-named installs.
        # Corroborate the exact relative directory in the runtime distribution
        # and reject links that escape the package root.
        try:
            inside = Path(path).resolve().is_relative_to(bundle["path"].resolve())
            provider_skill = provider / relative / "SKILL.md" if provider else None
            verified = (inside and name in bundle["names"] and provider_skill is not None and provider_skill.is_file()
                        and drift_report.skill_label(provider_skill.parent) == name)
        except (OSError, RuntimeError, UnicodeError):
            verified = False
        if verified:
            metadata.update(registration="bundle_member", source_class=skill["source_class"],
                            provenance="provider_manifest_and_path", manifest_path=str(bundle["manifest"]),
                            provider_path=str(provider_skill))
        else:
            metadata["provenance"] = "unverified"
        break
    return metadata


def _merge_discovered(items: list[dict], rows: list[dict], discovered: list[dict], bundles: list[dict],
                      roots: list[dict], repo_root: Path, repo_ctx: dict, tool_order: dict) -> None:
    """Merge names for display, identify each installation by lexical path."""
    by_name = {item["name"]: item for item in items}
    owners = { _lexical(entry["path"], repo_root): item for item in items for entry in item["entries"] }
    found_by_path = {row["path"]: row for row in discovered}
    candidates = { _lexical(row["path"], repo_root): row for row in rows }
    for found in discovered:
        path = Path(found["path"])
        try:
            name = drift_report.skill_label(path)
        except UnicodeError:
            name = path.name
        candidates[found["path"]] = {"name": name, "path": str(path)}
    for lexical, row in sorted(candidates.items()):
        metadata = _discovered_metadata(lexical, row["name"], found_by_path.get(lexical), bundles)
        item = owners.get(lexical)
        if item is None:
            item = by_name.get(row["name"])
            if item is None:
                item = _unrecorded_item(row, roots, repo_root, repo_ctx, tool_order)
                if metadata["registration"] == "bundle_member":
                    item.update(origin="bundle_member", source_class=metadata["source_class"],
                                source_label=SOURCE_LABEL[metadata["source_class"]],
                                channel_label="随运行时包治理", channel_detail=metadata["package"],
                                reconciliation_status=None, status_label="包级治理", findings=[])
                items.append(item)
                by_name[item["name"]] = item
            else:
                paths = [entry["path"] for entry in item["entries"]] + [row["path"]]
                previous = {_lexical(entry["path"], repo_root): entry for entry in item["entries"]}
                marks, entries, evidence, repo_ids = tool_marks(paths, roots, repo_root, repo_ctx, tool_order)
                for entry in entries:
                    old = previous.get(_lexical(entry["path"], repo_root), {})
                    entry.update({key: value for key, value in old.items() if key not in entry})
                item.update(tools=marks, entries=entries, evidence=evidence, repo_ids=repo_ids)
            owners[lexical] = item
            for entry in item["entries"]:
                if _lexical(entry["path"], repo_root) == lexical:
                    entry.update(metadata)
        else:
            for entry in item["entries"]:
                if _lexical(entry["path"], repo_root) == lexical and found_by_path.get(lexical):
                    entry.update({key: value for key, value in metadata.items() if key not in ("registration", "source_class")})


def _skill_names(staging_dir: Path, root_info: dict[str, Any]) -> list[str]:
    relative = root_info.get("staging_path") or ""
    root = staging_dir / relative if relative else staging_dir
    names = []
    if (root / "SKILL.md").is_file():
        text = drift_report.read_text(root / "SKILL.md")
        name = drift_report.inventory.skill_name_from_markdown(text or "")
        if name:
            names.append(name)
    elif root.is_dir():
        for dirpath, dirnames, filenames in os.walk(root):
            if "SKILL.md" in filenames:
                text = drift_report.read_text(Path(dirpath) / "SKILL.md")
                name = drift_report.inventory.skill_name_from_markdown(text or "")
                if name:
                    names.append(name)
                dirnames[:] = []
    if names:
        return names
    live = root_info.get("live_path")
    if isinstance(live, str) and live:
        return [Path(live).name]
    return []


def read_staging(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not root.exists():
        return [], {"state": "absent", "detail": "暂存区目录不存在", "pending": 0}
    if not root.is_dir():
        return [], {"state": "bad", "detail": "暂存区路径不是目录", "pending": 0}
    pending = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or child.name.startswith("."):
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pending.append({"id": child.name, "status": "unreadable", "names": [], "gate": {"blocked": True, "error": True, "reason": "manifest.json 无法读取"}})
            continue
        if not isinstance(manifest, dict) or manifest.get("status") == "activated":
            continue
        gate = manifest.get("capability_gate") if isinstance(manifest.get("capability_gate"), dict) else None
        gate_path = child / "capability-gate.json"
        if gate is None and gate_path.is_file():
            try:
                loaded = json.loads(gate_path.read_text(encoding="utf-8"))
                gate = loaded if isinstance(loaded, dict) else {"blocked": True, "error": True, "reason": "capability-gate.json 不是对象"}
            except (OSError, json.JSONDecodeError):
                gate = {"blocked": True, "error": True, "reason": "capability-gate.json 无法读取"}
        names: list[str] = []
        for info in manifest.get("roots") or []:
            if isinstance(info, dict):
                names.extend(_skill_names(child, info))
        pending.append({"id": child.name, "status": manifest.get("status") or "staged", "names": names, "gate": gate})
    return pending, {"state": "ok", "detail": f"未激活 {len(pending)}", "pending": len(pending)}


def _git_sha(repo_root: Path, inventory: Path) -> str | None:
    try:
        relative = str(inventory.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "log", "-1", "--format=%h", "--", relative],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    sha = result.stdout.strip()
    return sha or None


def _summary(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks = []
    for source, label in SUMMARY_SOURCES:
        chosen = items if source == "" else [item for item in items if item.get("source_class") == source]
        counts: dict[str, int] = {}
        for item in chosen:
            status = item.get("reconciliation_status") or "unknown"
            counts[status] = counts.get(status, 0) + 1
        blocks.append({"id": source or "all", "label": label, "count": len(chosen), "statuses": counts})
    return blocks


def _attention(
    items: list[dict[str, Any]],
    *,
    generated_at: str | None,
    inventory_updated_at: str | None,
    inventory_sha: str | None,
    review_days: int,
    staging_connected: bool,
    upgrade: tuple[str, str, bool] | None = None,
    overlap: tuple[str, str] | None = None,
) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}
    for code in BUCKETS:
        buckets[code] = {"connected": True, "reason": "", "items": []}
    if overlap is None:
        buckets["overlap"]["connected"] = False
        buckets["overlap"]["reason"] = "清单无效，实时检测未运行"
    elif overlap[0] == "fallback":
        buckets["overlap"]["reason"] = overlap[1]
        buckets["overlap"]["connected"] = any(item.get("conflict_status") == "open" for item in items)
    if upgrade is None:
        buckets["upgradable"]["connected"] = False
        buckets["upgradable"]["reason"] = "清单无效，升级检查未运行"
    else:
        state, detail, has_updates = upgrade
        # A failed or incomplete check must not look like an empty "无更新" bucket.
        if state == "fallback" and not has_updates:
            buckets["upgradable"]["connected"] = False
            buckets["upgradable"]["reason"] = detail
        else:
            buckets["upgradable"]["connected"] = True
            buckets["upgradable"]["reason"] = ""
    if not staging_connected:
        buckets["capability_pending"]["connected"] = False
        buckets["capability_pending"]["reason"] = "暂存区目录不存在"
    for item in items:
        code = item.get("bucket")
        if not code:
            continue
        buckets[code]["items"].append(
            {
                "identifier": item["name"],
                "title": item.get("reason") or "",
                "status": code,
                "time": generated_at,
                "source_class": item.get("source_class") or "-",
                "tags": item.get("tags") or [],
                "conflict_status": item.get("conflict_status"),
                "conflict_findings": item.get("conflict_findings") or [],
            }
        )
    counts = {code: len(buckets[code]["items"]) for code in BUCKETS}
    return {
        "generated_at": generated_at,
        "inventory_updated_at": inventory_updated_at,
        "inventory_sha": inventory_sha,
        "baseline_debt": sum(1 for item in items if item.get("baseline_debt")),
        "review_days": review_days,
        "badge": sum(counts.values()),
        "counts": counts,
        "buckets": buckets,
    }


def render_skills_attention_text(body: dict[str, Any]) -> str:
    lines = [
        f"generated_at {body.get('generated_at') or '-'}",
        f"inventory_updated_at {body.get('inventory_updated_at') or '-'}",
        f"baseline_debt {body.get('baseline_debt') or 0}",
    ]
    buckets = body.get("buckets") or {}
    for code in BUCKETS:
        bucket = buckets.get(code) or {}
        items = bucket.get("items") or []
        if not items:
            continue
        lines.append(f"{code} {len(items)}")
        for item in items:
            lines.append(
                "\t".join(
                    str(item.get(field) or "-")
                    for field in ("identifier", "status", "time", "source_class", "title")
                )
            )

    def packed(chosen: list[str], truncated: bool) -> str:
        parts = list(chosen)
        if truncated:
            parts.append("…")
        return "\n".join(parts) + "\n"

    if len(packed(lines, False).encode()) <= TEXT_BYTES:
        return packed(lines, False)
    kept: list[str] = []
    for line in lines:
        if len(packed(kept + [line], True).encode()) > TEXT_BYTES:
            break
        kept.append(line)
    return packed(kept or ["…"], bool(kept) and len(kept) < len(lines) or not kept)


def build_payload(
    *,
    repo_root: Path = REPO_ROOT,
    inventory_path: Path = INVENTORY_PATH,
    tool_roots_path: Path = TOOL_ROOTS_PATH,
    staging_root: Path = STAGING_ROOT,
    lock_path: Path = LOCK_PATH,
    changelog_dir: Path | None = CHANGELOG_DIR,
    review_days: int = DEFAULT_REVIEW_DAYS,
    now: datetime | None = None,
    scan_roots: list[Path] | None = None,
    upgrade_fetch: Callable[[str], tuple[int, Any, str]] | None = None,
) -> dict[str, Any]:
    moment = now or _now()
    generated_at = _iso(moment)
    try:
        document = _read_inventory(inventory_path)
        skills = document["skills"]
        roots, tools = _load_tool_roots(tool_roots_path, repo_root)
    except (OSError, json.JSONDecodeError, UnicodeError, ValueError, KeyError, TypeError, AttributeError) as exc:
        attention = _attention([], generated_at=generated_at, inventory_updated_at=None, inventory_sha=None, review_days=review_days, staging_connected=False)
        return {
            "ok": False,
            "error": {"code": "inventory_invalid", "message": str(exc)},
            "generated_at": generated_at,
            "items": [],
            "summary": [],
            "tools": [],
            "attention": attention,
            "skills_schema": SKILLS_SCHEMA,
            "shared_root": {"path": None, "state": "unscanned", "reason": "no_shared_root", "detail": "清单无效，未扫描"},
            "repos": [],
            "meta": {"sources": [{"id": "inventory", "label": "清单", "state": "bad", "detail": str(exc)}]},
        }

    lock_skills: dict = {}
    if lock_path.is_file():
        try:
            lock_skills = drift_report.load_lock(lock_path)
        except (OSError, json.JSONDecodeError, ValueError):
            lock_skills = {}
    scan = drift_report.machine_roots(document, repo_root, [], True) if scan_roots is None else scan_roots
    report = drift_report.build_report(
        document,
        repo_root,
        lock_skills,
        scan,
        inventory_path,
        lock_path if lock_path.is_file() else None,
        changelog_dir,
    )
    by_name = {row.get("name"): row for row in report.get("skills") or []}
    repo_lexical = os.path.abspath(repo_root)
    try:
        repo_resolved = _realpath(repo_root)
        repo_ctx = {"id": repo_resolved, "lexical": repo_lexical, "resolved": repo_resolved}
        repos = [{"id": repo_resolved, "label": Path(repo_resolved).name or repo_resolved, "path": repo_resolved, "identity_state": "ok", "reason": None}]
    except OSError as exc:
        err = exc.errno
        if _denied(exc):
            reason = "repo_root_denied"
        elif err in (errno.ENOENT, errno.ENOTDIR):
            reason = "repo_root_absent"
        else:
            reason = "repo_root_error"
        repo_ctx = {"id": "unknown", "lexical": repo_lexical, "resolved": None}
        repos = [{"id": "unknown", "label": "未知仓库", "path": repo_lexical, "identity_state": "unknown", "reason": reason}]
    tool_order = {tool["id"]: index for index, tool in enumerate(tools)}
    items = []
    for skill in skills:
        row = by_name.get(skill["name"]) or {
            "reconciliation_status": "unverified",
            "findings": ["baseline_absent"],
            "current_version": skill.get("current_version"),
            "observed_version": None,
            "baseline_ref": skill.get("baseline_ref"),
            "review_time": skill.get("review_time"),
        }
        items.append(_inventory_item(skill, row, roots, repo_root, repo_ctx, tool_order))
    discovery = scan_installs([Path(root["lexical"]) for root in roots] + list(scan))
    _merge_discovered(items, report.get("unrecorded") or [], discovery["installs"], _bundle_roots(skills, repo_root),
                      roots, repo_root, repo_ctx, tool_order)
    known = {item["name"] for item in items}

    pending, staging_meta = read_staging(staging_root)
    gates: dict[str, dict[str, Any]] = {}
    for candidate in pending:
        gate = candidate.get("gate")
        if not isinstance(gate, dict):
            continue
        for name in candidate.get("names") or []:
            gates[name] = gate
            if name not in known:
                items.append(
                    {
                        "name": name,
                        "origin": "staging",
                        "source_class": "unrecorded",
                        "source_label": "暂存区",
                        "scope": None,
                        "ownership": None,
                        "source_authority": None,
                        "update_authority": None,
                        "canonical_path": None,
                        "repo_is_source": False,
                        "channel_label": "暂存区",
                        "channel_detail": candidate["id"],
                        "current_version": None,
                        "observed_version": None,
                        "baseline_kind": None,
                        "baseline_subject": None,
                        "baseline_ref": None,
                        "review_time": None,
                        "reconciliation_status": "unrecorded",
                        "status_label": "未登记",
                        "findings": [],
                        "customization_mode": None,
                        "constraints": [],
                        "constraint_count": 0,
                        "conflict_status": None,
                        "tools": {},
                        "entries": [],
                        "evidence": [],
                        "repo_ids": [],
                        "install_path": candidate["id"],
                        "capability_gate": None,
                    }
                )
                known.add(name)
    # Each discovered directory is a scan root, including nested bundle members.
    conflict_roots = list(dict.fromkeys([Path(root["lexical"]) for root in roots] + list(scan)
                                      + [Path(entry["path"]) for entry in discovery["installs"]]))
    overlap = check_conflicts(SCRIPTS / "conflict_report.py", repo_root, inventory_path, conflict_roots,
                              (_mtime(inventory_path), _install_stamp(inventory_path, tool_roots_path, repo_root, staging_root, scan_roots)))
    for item in items:
        item["conflict_findings"] = [finding for finding in overlap["findings"] if item["name"] in finding["skills"]]
        item["conflict_detection_state"] = overlap["state"]
    upgrade = check_upgrades(items, lock_skills, repo_root=repo_root, fetch=upgrade_fetch)
    for item in items:
        if item.get("name") in upgrade.deferred_names:
            item["upgrade_status"] = "deferred"
            item["upgrade_detail"] = "升级状态待补"
        found = upgrade.updates.get(item.get("name"))
        if not found:
            continue
        item["upgrade"] = found
        item["upgrade_status"] = "update"
    assign_buckets(items, gates, review_days=review_days, now=moment)
    shared = _shared_root(roots)
    _attach_grok_symlinks(items, roots)
    for item in items:
        item["shared_entry"] = _shared_entry(item.get("name"), shared)
        item["command"] = _command(item.get("bucket"))

    inventory_updated_at = _iso(datetime.fromtimestamp(inventory_path.stat().st_mtime, timezone.utc))
    sha = _git_sha(repo_root, inventory_path)
    skil = shutil.which("skil-lock")
    sources = [
        {"id": "inventory", "label": "清单", "state": "ok", "detail": sha or "无 Git SHA"},
        {"id": "drift", "label": "漂移扫描", "state": "ok", "detail": "刚刚"},
        {"id": "discovery", "label": "磁盘入口扫描",
         "state": "fallback" if any(issue["code"] != "missing" for issue in discovery["issues"]) else "ok",
         "detail": f"{len(discovery['installs'])} 个入口；未验证 runtime 加载" +
                   ("；部分路径无法扫描，详见 discovery.issues" if any(issue["code"] != "missing" for issue in discovery["issues"]) else "")},
        {
            "id": "skill_lock",
            "label": "SkilLock",
            "state": "ok" if skil else "fallback",
            "detail": "已安装" if skil else "本机未安装，门禁回退到风险信号",
        },
        {"id": "staging", "label": "暂存区", "state": staging_meta["state"], "detail": staging_meta["detail"]},
        {"id": "upgrade", "label": "升级检查", "state": upgrade.state, "detail": upgrade.detail},
        {"id": "overlap", "label": "重叠检测", "state": overlap["state"], "detail": overlap["detail"]},
    ]
    attention = _attention(
        items,
        generated_at=generated_at,
        inventory_updated_at=inventory_updated_at,
        inventory_sha=sha,
        review_days=review_days,
        staging_connected=staging_meta["state"] != "absent",
        upgrade=(upgrade.state, upgrade.detail, bool(upgrade.updates)),
        overlap=(overlap["state"], overlap["detail"]),
    )
    return {
        "ok": True,
        "error": None,
        "generated_at": generated_at,
        "items": items,
        "summary": _summary(items),
        "tools": tools,
        "attention": attention,
        "skills_schema": SKILLS_SCHEMA,
        "shared_root": _shared_root_state(shared),
        "repos": repos,
        "meta": {
            "inventory_updated_at": inventory_updated_at,
            "inventory_sha": sha,
            "drift_scanned_at": generated_at,
            "review_days": review_days,
            "sources": sources,
            "staging": staging_meta,
            "discovery": {"kind": "disk_installations", "runtime_loaded": None,
                          "detail": "磁盘安装可见性；未验证 runtime 当前会话实际加载。包清单与路径仅证明来源，不验证内容未修改。",
                          "install_count": len(discovery["installs"]), "issues": discovery["issues"]},
        },
    }


_DOCUMENT_LOCK = threading.Lock()


def _single_document(build):
    @wraps(build)
    def document(*args, **kwargs):
        # A cold request may join warmup, but never waits for two probe rounds.
        if not _DOCUMENT_LOCK.acquire(blocking=False):
            kwargs = dict(kwargs, upgrade_fetch=lambda _url: (0, None, "deferred"))
            if not _DOCUMENT_LOCK.acquire(timeout=DEFAULT_TOTAL_TIMEOUT):
                return build(*args, **dict(kwargs, now=_now(), clock=None))
            # If the install stamp changed during the wait, return pending
            # local evidence rather than beginning a second network round.
        try:
            return build(*args, **kwargs)
        finally:
            _DOCUMENT_LOCK.release()
    return document


def prewarm_skills() -> threading.Thread:
    """Start one read-only cache fill; callers share its document lock."""
    def warm():
        try:
            skills_document()
        except Exception:
            # No exception text: file paths or upstream details may be sensitive.
            logging.getLogger(__name__).warning("skills cache warmup failed")
    thread = threading.Thread(target=warm, name="skills-warmup", daemon=True)
    thread.start()
    return thread


@_single_document
def skills_document(
    review_days: int = DEFAULT_REVIEW_DAYS,
    *,
    now: datetime | None = None,
    repo_root: Path = REPO_ROOT,
    inventory_path: Path = INVENTORY_PATH,
    tool_roots_path: Path = TOOL_ROOTS_PATH,
    staging_root: Path = STAGING_ROOT,
    lock_path: Path = LOCK_PATH,
    changelog_dir: Path | None = CHANGELOG_DIR,
    scan_roots: list[Path] | None = None,
    clock: Callable[[], datetime] | None = None,
    upgrade_fetch: Callable[[str], tuple[int, Any, str]] | None = None,
) -> dict[str, Any]:
    """Cached payload. The key follows installs, not only the inventory file.

    A rewrite of SKILL.md, a plugin version directory, a symlink retarget,
    or a staging gate file changes the stamp immediately. The TTL is the
    backstop for anything the stamp does not name. ``now`` still bypasses
    the cache for a one-shot payload.
    """
    if now is not None and clock is None:
        return build_payload(
            repo_root=repo_root,
            inventory_path=inventory_path,
            tool_roots_path=tool_roots_path,
            staging_root=staging_root,
            lock_path=lock_path,
            changelog_dir=changelog_dir,
            review_days=review_days,
            now=now,
            scan_roots=scan_roots,
            upgrade_fetch=upgrade_fetch,
        )
    moment = clock() if clock is not None else _now()
    # Wall time describes the payload; production TTL must survive clock resets.
    # An explicit clock keeps tests deterministic, in a separate cache domain.
    cache_time = moment.timestamp() if clock is not None else time.monotonic()
    key = (
        clock is not None,
        review_days,
        str(inventory_path),
        _mtime(inventory_path),
        str(tool_roots_path),
        _mtime(tool_roots_path),
        str(lock_path),
        _mtime(lock_path),
        _install_stamp(inventory_path, tool_roots_path, repo_root, staging_root, scan_roots),
    )
    cached = _CACHE.get(key)
    if cached is not None and 0 <= cache_time - cached[1] < CACHE_TTL_SECONDS:
        return cached[0]
    document = build_payload(
        repo_root=repo_root,
        inventory_path=inventory_path,
        tool_roots_path=tool_roots_path,
        staging_root=staging_root,
        lock_path=lock_path,
        changelog_dir=changelog_dir,
        review_days=review_days,
        now=moment,
        scan_roots=scan_roots,
        upgrade_fetch=upgrade_fetch,
    )
    _CACHE.clear()
    # Bind completion time to this entry so clearing it clears all TTL state.
    completed_at = clock().timestamp() if clock is not None else time.monotonic()
    _CACHE[key] = (document, completed_at)
    return document
