"""Read-only, manifest-bound Grok Bot user skill exports. Never reads memory/API."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

from .skill_content import MAX_BYTES, _parts, _open_dir

EXPORT_ROOT = Path.home() / "multica-handoff/grok-bot-export/agent-steward"
PREFIX = "grok-export:"
LABEL = "Grok Bot 导出"
MESSAGES = {
    "ok": "SHA-256 与字节数匹配",
    "missing": "文件缺失",
    "hash_mismatch": "哈希或字节数不一致",
    "denied": "路径或权限不允许读取（不跟随符号链接）",
    "too_large": "文件超过读取上限",
}


def _read(root: Path, relative: str, limit: int = MAX_BYTES) -> bytes:
    """Open only regular files, anchored by directory FDs, without symlinks."""
    parts = _parts(relative)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in (*root.absolute().parts[1:], *parts[:-1]):
            nested = _open_dir(fd, part)
            os.close(fd)
            fd = nested
        content_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(content_fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("not a regular file")
            if info.st_size > limit:
                raise OverflowError("too large")
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise OverflowError("too large")
        return data
    finally:
        os.close(fd)


def _manifest(root: Path) -> dict:
    doc = json.loads(_read(root, "manifest.json", 1024 * 1024))
    if (not isinstance(doc, dict) or doc.get("hash_algorithm") != "sha256"
            or not isinstance(doc.get("files"), list) or not isinstance(doc.get("skills_user"), dict)
            or not isinstance(doc["skills_user"].get("skills"), list)
            or not isinstance(doc.get("exported_at"), str) or not doc["exported_at"]
            or not isinstance(doc.get("bot_name"), str) or not doc["bot_name"]):
        raise ValueError("invalid export manifest")
    skills = doc["skills_user"]["skills"]
    if not 0 < len(skills) <= 200 or len(doc["files"]) > 2000:
        raise ValueError("invalid count")
    slugs = set()
    names = set()
    for skill in skills:
        if (not isinstance(skill, dict) or not isinstance(skill.get("slug"), str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]+", skill["slug"])
                or skill["slug"] in slugs or not isinstance(skill.get("name"), str)
                or not skill["name"].strip() or skill["name"] in names):
            raise ValueError("invalid skill")
        slugs.add(skill["slug"])
        names.add(skill["name"])
    seen = set()
    for row in doc["files"]:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("invalid file")
        parts = _parts(row["path"])
        if (len(parts) < 3 or parts[0] != "skills-user" or parts[1] not in slugs
                or row.get("skill") != parts[1] or row["path"] in seen
                or not isinstance(row.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                or type(row.get("bytes")) is not int or row["bytes"] < 0):
            raise ValueError("invalid file or missing hash")
        seen.add(row["path"])
    if doc.get("file_count") != len(seen) or doc["skills_user"].get("file_count") != len(seen) or doc["skills_user"].get("count") != len(skills):
        raise ValueError("file count differs")
    for skill in skills:
        rows = [row for row in doc["files"] if row["skill"] == skill["slug"]]
        main = next((row for row in rows if row["path"] == f"skills-user/{skill['slug']}/SKILL.md"), None)
        if (main is None or main["sha256"] != skill.get("sha256") or main["bytes"] != skill.get("bytes")
                or len(rows) != skill.get("file_count") or sum(row["bytes"] for row in rows) != skill.get("total_bytes")):
            raise ValueError("skill files differ")
    return doc


def _verify(root: Path, row: dict) -> tuple[dict, bytes | None]:
    data = None
    try:
        data = _read(root, row["path"])
        state = "ok" if len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"] else "hash_mismatch"
    except FileNotFoundError:
        state = "missing"
    except OverflowError:
        state = "too_large"
    except (OSError, ValueError):
        state = "denied"
    return {"path": row["path"].split("/", 2)[2], "type": "file", "bytes": row["bytes"],
            "sha256": row["sha256"], "state": state, "message": MESSAGES[state], "readable": state == "ok"}, data


def grok_export_skills(root: Path | None = None) -> tuple[list[dict], dict]:
    root = root if root is not None else EXPORT_ROOT
    source = {"id": "grok_bot_export", "label": LABEL, "path": str(root / "manifest.json"),
              "collected_at": datetime.now(timezone.utc).isoformat()}
    try:
        doc = _manifest(root)
    except FileNotFoundError:
        return [], {**source, "state": "absent", "detail": "Grok Bot 导出 manifest.json 缺失"}
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return [], {**source, "state": "bad", "detail": "Grok Bot 导出清单无效，缺少逐文件哈希或文件统计不一致"}
    items = []
    failures = 0
    for skill in doc["skills_user"]["skills"]:
        files = [_verify(root, row)[0] for row in doc["files"] if row["skill"] == skill["slug"]]
        bad = sum(row["state"] != "ok" for row in files)
        failures += bad
        items.append({
            "key": PREFIX + skill["slug"], "name": skill["name"], "origin": "grok_bot",
            "source_class": "grok_bot_export", "source_label": LABEL, "scope": "bot",
            "channel_label": LABEL, "channel_detail": doc["bot_name"],
            "grok_bots": [{"id": doc.get("bot_id"), "name": doc["bot_name"],
                           "evidence_state": "declared", "evidence_label": "Declared · 导出清单声明"}],
            "evidence_detail": "Declared：导出清单声明；Reachable：仅指本机导出文件可读；Observed：逐文件哈希核对，不代表 Bot 当前加载。",
            "manifest_path": str(root / "manifest.json"),
            "export": {"path": PREFIX + skill["slug"], "exported_at": doc["exported_at"],
                       "files": files, "state": "bad" if bad else "ok", "failed_count": bad},
            "status_label": f"校验异常 {bad}/{len(files)}" if bad else f"已校验 {len(files)} 个文件",
            "content": {"state": "available"}, "current_version": None, "observed_version": None,
            "reconciliation_status": None, "review_time": None, "entries": [], "evidence": [], "tools": {},
            "shared_entry": {"state": "not_applicable", "label": "∅", "detail": "导出文件不是本机 Runtime 安装"},
            "bucket": None, "baseline_debt": False, "quiet": False, "command": None,
            "constraints": [], "findings": [], "constraint_count": 0,
        })
    return items, {**source, "state": "bad" if failures else "ok", "exported_at": doc["exported_at"],
                   "detail": f"导出于 {doc['exported_at']} · {len(items)} 个 user skill · {len(doc['files'])} 个文件 · {failures} 个校验异常"}


def read_export_content(*, name: str, path: str, file: str | None = None,
                        tree: bool = False, source: str = "local", root: Path | None = None) -> dict:
    root = root if root is not None else EXPORT_ROOT
    result = {"ok": False, "state": "denied", "name": name, "path": path,
              "source": "grok_bot_export", "evidence_state": "unknown",
              "collected_at": datetime.now(timezone.utc).isoformat(), "message": "不在导出清单内，或清单无效"}
    if source != "local":
        return result
    try:
        doc = _manifest(root)
        skill = next((s for s in doc["skills_user"]["skills"] if s["name"] == name and path == PREFIX + s["slug"]), None)
        if skill is None:
            return result
        rows = [r for r in doc["files"] if r["skill"] == skill["slug"]]
        if tree:
            return {**result, "ok": True, "state": "ok", "message": None, "evidence_state": "observed",
                    "files": [_verify(root, row)[0] for row in rows], "truncated": False}
        relative = "/".join(_parts(file if file is not None else "SKILL.md"))
        row = next((r for r in rows if r["path"] == f"skills-user/{skill['slug']}/{relative}"), None)
        if row is None:
            return result
        checked, data = _verify(root, row)
        if checked["state"] != "ok":
            return {**result, "state": checked["state"], "message": checked["message"]}
        text = data.decode("utf-8")
        if any(ord(char) < 32 and char not in "\n\r\t\f" for char in text):
            raise UnicodeError("binary")
        return {**result, "ok": True, "state": "ok" if data else "empty", "message": None,
                "evidence_state": "observed", "file": relative, "sha256": row["sha256"], "bytes": len(data),
                "exported_at": doc["exported_at"], "format": "markdown" if relative.lower().endswith(".md") else "text", "content": text}
    except UnicodeError:
        return {**result, "state": "unsupported", "message": "仅支持 UTF-8 普通文本"}
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return result
