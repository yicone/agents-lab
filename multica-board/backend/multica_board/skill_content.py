"""Bounded, read-only text access to skill directories exposed by the inventory.

The caller selects an existing entry, never an arbitrary filesystem root.
Directory descriptors and O_NOFOLLOW prevent descendant symlink races.
"""
from __future__ import annotations

import errno
import hashlib
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_BYTES = 512 * 1024
MAX_TREE_ENTRIES = 2000
MAX_TREE_DEPTH = 16


def _parts(value: str) -> list[str]:
    if not value or value.startswith("/") or "\\" in value or "\x00" in value:
        raise ValueError("invalid relative file")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("invalid relative file")
    return parts


def _known_root(document: dict, name: str, selected: str, repo_root: Path) -> Path:
    if "\x00" in selected or ".." in selected.split("/"):
        raise ValueError("invalid entry path")
    for item in document.get("items", []):
        if item.get("name") != name:
            continue
        candidates = [(entry.get("path"), entry.get("resolved")) for entry in item.get("entries", [])]
        candidates.append(((item.get("local_source") or {}).get("path"), None))
        # Compare with exposed strings, including their absolute spelling. Do not
        # authorize arbitrary aliases merely because they resolve to the same inode.
        for raw, resolved in candidates:
            if not isinstance(raw, str) or not raw:
                continue
            # Version patterns are selectable only by their exposed spelling;
            # use the scan's concrete resolved path, never glob request input.
            if "*" in raw and not resolved:
                continue
            path = Path(resolved if "*" in raw else raw).expanduser()
            if not path.is_absolute():
                path = repo_root / path
            if selected not in (raw, str(path)):
                continue
            if resolved and str(path.resolve(strict=True)) != resolved:
                raise ValueError("entry changed since inventory scan")
            if path.name == "SKILL.md":
                return path.parent.resolve(strict=True)
            if path.is_dir() and (path / "SKILL.md").is_file():
                return path.resolve(strict=True)
    raise ValueError("entry is not an exposed skill directory")


def _open_dir(parent: int, name: str) -> int:
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def _tree(root_fd: int) -> tuple[list[dict], bool]:
    entries: list[dict] = []
    truncated = False

    def walk(fd: int, prefix: str, depth: int) -> None:
        nonlocal truncated
        # scandir is bounded while iterating; never load an unbounded directory.
        with os.scandir(fd) as children:
            for child in children:
                if len(entries) >= MAX_TREE_ENTRIES:
                    truncated = True
                    return
                relative = prefix + child.name
                try:
                    info = child.stat(follow_symlinks=False)
                    kind = ("symlink" if stat.S_ISLNK(info.st_mode) else
                            "directory" if stat.S_ISDIR(info.st_mode) else
                            "file" if stat.S_ISREG(info.st_mode) else "special")
                    entry = {"path": relative, "type": kind, "bytes": info.st_size,
                             "readable": kind == "file" and info.st_size <= MAX_BYTES}
                    entries.append(entry)
                    if kind == "directory":
                        if depth >= MAX_TREE_DEPTH:
                            truncated = True
                        else:
                            nested = _open_dir(fd, child.name)
                            try:
                                walk(nested, relative + "/", depth + 1)
                            finally:
                                os.close(nested)
                except OSError:
                    if entries and entries[-1]["path"] == relative:
                        entries[-1].update(state="unknown", readable=False)
                    else:
                        entries.append({"path": relative, "type": "unknown", "bytes": None, "readable": False})
    walk(root_fd, "", 0)
    return sorted(entries, key=lambda item: item["path"]), truncated


def read_content(document: dict, *, name: str, path: str = "", file: str | None = None,
                 tree: bool = False, source: str = "local", repo_root: Path) -> dict[str, Any]:
    observed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result: dict[str, Any] = {"ok": False, "name": name, "path": path, "source": source,
                              "collected_at": observed, "evidence_state": "unknown"}
    if source == "cloud":
        return {**result, "state": "uncollected", "collected_at": None,
                "message": "Workspace 正文未采集；本机文件不代表 Workspace 内容"}
    try:
        if source != "local":
            raise ValueError("invalid source")
        parts = _parts(file if file is not None else "SKILL.md")
        root = _known_root(document, name, path, repo_root)
        # root is canonical; refuse a symlink introduced into any ancestor
        # between resolution and open, not only at the final component.
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in root.parts[1:]:
                nested = _open_dir(fd, part)
                os.close(fd)
                fd = nested
            if tree:
                files, truncated = _tree(fd)
                return {**result, "ok": True, "state": "ok", "evidence_state": "observed",
                        "files": files, "truncated": truncated, "max_bytes": MAX_BYTES}
            for part in parts[:-1]:
                nested = _open_dir(fd, part)
                os.close(fd)
                fd = nested
            content_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(content_fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("only regular text files are supported")
                if info.st_size > MAX_BYTES:
                    return {**result, "state": "too_large", "message": "文件超过 512 KiB 上限"}
                data = handle.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                return {**result, "state": "too_large", "message": "文件超过 512 KiB 上限"}
            text = data.decode("utf-8")
            if any(ord(char) < 32 and char not in "\n\r\t\f" for char in text):
                raise UnicodeError("binary content")
            return {**result, "ok": True, "state": "ok" if data else "empty", "file": "/".join(parts),
                    "evidence_state": "observed", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                    "format": "markdown" if parts[-1].lower().endswith(".md") else "text", "content": text}
        finally:
            os.close(fd)
    except UnicodeError:
        return {**result, "state": "unsupported", "message": "仅支持 UTF-8 普通文本"}
    except ValueError:
        return {**result, "state": "denied", "message": "路径不在已知技能目录内或文件类型不允许"}
    except OSError as exc:
        state = "missing" if exc.errno == errno.ENOENT else "denied" if exc.errno in (
            errno.EACCES, errno.EPERM, errno.ELOOP, errno.ENOTDIR) else "error"
        return {**result, "state": state, "message": {"missing": "文件不存在", "denied": "路径或权限不允许读取",
                                                    "error": "读取失败"}[state]}
