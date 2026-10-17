"""Read-only Skills payload for Agent Atlas (design v3 D3 / B).

Inventory, drift, tool-root config, and staging candidates are merged here.
The page and ``GET /api/skills/attention`` both use :func:`assign_buckets`.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

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
MARK_RANK = {None: 0, "shared": 1, "missing": 2, "direct": 3}
SUMMARY_SOURCES = (
    ("", "全部"),
    ("skills_cli_managed", "Skills CLI"),
    ("local_custom", "自建"),
    ("runtime_plugin", "运行时插件"),
    ("runtime_builtin", "运行时内置"),
    ("third_party_unmanaged", "第三方未托管"),
    ("unrecorded", "未登记"),
)

_CACHE: dict[tuple, dict[str, Any]] = {}


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
    token: tuple = (str(path), st.st_mtime_ns, st.st_size, stat.S_ISLNK(st.st_mode))
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
    elif resolved.parent != resolved:
        tokens.append(_stat_token(resolved.parent))
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


def _install_stamp(inventory_path: Path, tool_roots_path: Path, repo_root: Path, staging_root: Path) -> tuple:
    """Fingerprint installs. Inventory mtime alone misses these writes."""
    stamps: list = [_stamp_tree(staging_root)]
    for root in _tool_root_paths(tool_roots_path, repo_root):
        stamps.append(_stamp_tree(root))
    try:
        document = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        document = None
    skills = document.get("skills") if isinstance(document, dict) else None
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
        gate = gates.get(item["name"])
        if gate and (gate.get("blocked") or gate.get("backend") == "fallback"):
            hits.append("capability_pending")
            item["capability_gate"] = {
                "backend": gate.get("backend"),
                "blocked": bool(gate.get("blocked")),
                "fallback": gate.get("fallback"),
                "reason": gate.get("reason") or gate.get("summary"),
            }
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
        return "职责重叠未关闭"
    if bucket == "upgradable":
        return "上游有更新"
    return ""


def _load_tool_roots(path: Path, repo_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    tools = [
        {"id": item["id"], "label": item["label"]}
        for item in document.get("tools") or []
        if isinstance(item, dict) and item.get("id") and item.get("label")
    ]
    roots = []
    for item in document.get("roots") or []:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        raw = str(item["path"])
        if item.get("repo"):
            expanded = str((repo_root / raw).resolve())
        else:
            expanded = str(Path(raw).expanduser().resolve())
        roots.append(
            {
                "path": raw,
                "expanded": expanded.rstrip("/"),
                "tools": [tool for tool in item.get("tools") or [] if isinstance(tool, str)],
                "kind": "shared" if item.get("kind") == "shared" else "direct",
            }
        )
    roots.sort(key=lambda item: len(item["expanded"]), reverse=True)
    return roots, tools


def _match_root(expanded: str, roots: list[dict[str, Any]]) -> dict[str, Any] | None:
    for root in roots:
        base = root["expanded"]
        if expanded == base or expanded.startswith(base + "/"):
            return root
    return None


def _expand(raw: str, repo_root: Path) -> str:
    if raw.startswith("~") or raw.startswith("/"):
        return str(Path(raw).expanduser().resolve())
    return str((repo_root / raw).resolve())


def _bump(current: str | None, mark: str) -> str | None:
    if MARK_RANK[mark] > MARK_RANK[current]:
        return mark
    return current


def tool_marks(paths: list[str], roots: list[dict[str, Any]], repo_root: Path) -> tuple[dict[str, str | None], list[dict[str, Any]]]:
    tool_ids = {tool for root in roots for tool in root["tools"]}
    marks: dict[str, str | None] = {tool: None for tool in tool_ids}
    entries = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip() or not drift_report.is_concrete(raw):
            continue
        located = drift_report.inventory.locate_path(raw, repo_root)
        try:
            present = located.exists()
        except OSError:
            present = False
        target = None
        try:
            if located.is_symlink():
                target = os.readlink(located)
        except OSError:
            target = None
        expanded = _expand(raw, repo_root)
        # Hash-resolved plugin paths should match the root of the concrete file.
        if "*" in raw:
            expanded = str(located)
        root = _match_root(expanded, roots) or _match_root(str(located), roots)
        tools = root["tools"] if root else []
        kind = root["kind"] if root else None
        mark = None
        if kind:
            mark = kind if present else "missing"
            for tool in tools:
                marks[tool] = _bump(marks.get(tool), mark)
        entries.append(
            {
                "path": raw,
                "present": present,
                "symlink": target is not None,
                "target": target,
                "tools": tools,
                "mark": mark,
            }
        )
    return marks, entries


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
        {"code": code, "label": FINDING_LABEL.get(code, code)}
        for code in codes
        if code not in hidden
    ]


def _command(bucket: str | None) -> str:
    if bucket == "capability_pending":
        return "python3 skills-governance/scripts/skill_guard.py diff --help"
    if bucket == "unrecorded":
        return "python3 skills-governance/scripts/validate_skills_inventory.py --check-paths"
    return "python3 skills-governance/scripts/drift_report.py --format text"


def _inventory_item(skill: dict[str, Any], row: dict[str, Any], roots: list[dict[str, Any]], repo_root: Path) -> dict[str, Any]:
    authority = skill.get("authority") if isinstance(skill.get("authority"), dict) else {}
    baseline = skill.get("baseline") if isinstance(skill.get("baseline"), dict) else {}
    paths = [value for value in skill.get("visible_paths") or [] if isinstance(value, str)]
    for adapter in skill.get("adapters") or []:
        if isinstance(adapter, dict) and isinstance(adapter.get("path"), str):
            paths.append(adapter["path"])
    canonical = authority.get("canonical_path")
    if isinstance(canonical, str):
        paths.append(canonical)
    marks, entries = tool_marks(paths, roots, repo_root)
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
        "install_path": None,
        "capability_gate": None,
    }
    item["constraint_count"] = len(item["constraints"])
    return item


def _unrecorded_item(row: dict[str, Any], roots: list[dict[str, Any]], repo_root: Path) -> dict[str, Any]:
    path = row.get("path") or ""
    marks, entries = tool_marks([path] if path else [], roots, repo_root)
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
        "reconciliation_status": "missing",
        "status_label": "未登记",
        "findings": [{"code": "unrecorded", "label": "清单里没有这条安装"}],
        "customization_mode": None,
        "constraints": [],
        "constraint_count": 0,
        "conflict_status": None,
        "tools": marks,
        "entries": entries,
        "install_path": path,
        "capability_gate": None,
    }


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


def _attention(items: list[dict[str, Any]], *, generated_at: str | None, inventory_updated_at: str | None, inventory_sha: str | None, review_days: int, staging_connected: bool) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}
    for code in BUCKETS:
        buckets[code] = {"connected": True, "reason": "", "items": []}
    buckets["upgradable"]["connected"] = False
    buckets["upgradable"]["reason"] = "升级检查还没有数据源"
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
        items.append(_inventory_item(skill, row, roots, repo_root))
    known = {item["name"] for item in items}
    for row in report.get("unrecorded") or []:
        if row.get("name") in known:
            continue
        items.append(_unrecorded_item(row, roots, repo_root))

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
                        "reconciliation_status": "unverified",
                        "status_label": "未核实",
                        "findings": [],
                        "customization_mode": None,
                        "constraints": [],
                        "constraint_count": 0,
                        "conflict_status": None,
                        "tools": {},
                        "entries": [],
                        "install_path": candidate["id"],
                        "capability_gate": None,
                    }
                )
                known.add(name)
    assign_buckets(items, gates, review_days=review_days, now=moment)
    for item in items:
        item["command"] = _command(item.get("bucket"))

    inventory_updated_at = _iso(datetime.fromtimestamp(inventory_path.stat().st_mtime, timezone.utc))
    sha = _git_sha(repo_root, inventory_path)
    skil = shutil.which("skil-lock")
    sources = [
        {"id": "inventory", "label": "清单", "state": "ok", "detail": sha or "无 Git SHA"},
        {"id": "drift", "label": "漂移扫描", "state": "ok", "detail": "刚刚"},
        {
            "id": "skill_lock",
            "label": "SkilLock",
            "state": "ok" if skil else "fallback",
            "detail": "已安装" if skil else "本机未安装，门禁回退到风险信号",
        },
        {"id": "staging", "label": "暂存区", "state": staging_meta["state"], "detail": staging_meta["detail"]},
        {"id": "upgrade", "label": "升级检查", "state": "absent", "detail": "未接入"},
        {"id": "overlap", "label": "重叠检测", "state": "absent", "detail": "未接入，只读 conflict_status"},
    ]
    attention = _attention(
        items,
        generated_at=generated_at,
        inventory_updated_at=inventory_updated_at,
        inventory_sha=sha,
        review_days=review_days,
        staging_connected=staging_meta["state"] != "absent",
    )
    return {
        "ok": True,
        "error": None,
        "generated_at": generated_at,
        "items": items,
        "summary": _summary(items),
        "tools": tools,
        "attention": attention,
        "meta": {
            "inventory_updated_at": inventory_updated_at,
            "inventory_sha": sha,
            "drift_scanned_at": generated_at,
            "review_days": review_days,
            "sources": sources,
            "staging": staging_meta,
        },
    }


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
        )
    moment = clock() if clock is not None else _now()
    key = (
        review_days,
        int(moment.timestamp()) // CACHE_TTL_SECONDS,
        str(inventory_path),
        _mtime(inventory_path),
        str(tool_roots_path),
        _mtime(tool_roots_path),
        str(lock_path),
        _mtime(lock_path),
        _install_stamp(inventory_path, tool_roots_path, repo_root, staging_root),
    )
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
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
    )
    _CACHE.clear()
    _CACHE[key] = document
    return document
