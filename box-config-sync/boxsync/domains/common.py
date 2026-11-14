"""Bounded, descriptor-relative collection helpers."""
import hashlib
import os
import re
import stat
import unicodedata
from datetime import datetime, timezone

from ..policy import MAX_CONFIG_BYTES, MAX_TREE_ENTRIES
from ..safe_io import UnsafeIO, entries, open_directory, read_at, read_file, _signature
from ..secrets_scan import contains_secret
from ..validation import InvalidDocument, parse_json, timestamp

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def label(value, limit=200):
    if not isinstance(value, str) or contains_secret(value):
        return None
    value = " ".join(unicodedata.normalize("NFC", value).split())
    if not value or len(value) > limit or any(x in value for x in ("https://", "http://", "file://")):
        return None
    return value


def safe_id(value):
    return isinstance(value, str) and SLUG.fullmatch(value) is not None and not contains_secret(value)


def safe_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        return timestamp(value).isoformat().replace("+00:00", "Z")
    except InvalidDocument:
        return None


def directory_names(root, policy):
    if root is None:
        return [], "not_collected"
    names = []
    try:
        with open_directory(root, policy) as fd:
            for i, entry in enumerate(entries(fd, policy)):
                if i >= MAX_TREE_ENTRIES:
                    raise UnsafeIO("error")
                if stat.S_ISDIR(entry.stat(follow_symlinks=False).st_mode):
                    names.append(entry.name)
        return sorted(names), "ok"
    except (UnsafeIO, OSError) as exc:
        state = exc.state if isinstance(exc, UnsafeIO) else "error"
        return [], state if state in ("absent", "denied") else "error"


def json_file(root, relative, policy):
    if root is None:
        return None, "not_collected"
    result = read_file(root, relative, policy, MAX_CONFIG_BYTES)
    if result.state != "observed":
        return None, result.state
    try:
        data = parse_json(result.data)
        if not isinstance(data, dict):
            raise InvalidDocument()
        return data, "observed"
    except InvalidDocument:
        return None, "error"


def walk_metadata(root, policy, *, content=False, max_bytes=None):
    """Memory uses content=False: regular files are stat'ed, never opened."""
    rows = []
    visited = 0

    def walk(fd, parent="", depth=0):
        nonlocal visited
        if depth > 64:
            raise UnsafeIO("too_large")
        before = os.fstat(fd)
        for entry in entries(fd, policy, parent):
            if entry.name == ".DS_Store":
                continue
            visited += 1
            if visited > MAX_TREE_ENTRIES:
                raise UnsafeIO("too_large")
            path = parent + "/" + entry.name if parent else entry.name
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                        raise UnsafeIO("changed_during_read")
                    walk(child, path, depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                row = {"path": path, "bytes": info.st_size, "mtime": iso(info.st_mtime),
                       "sha256": None, "state": "observed"}
                if content:
                    result = read_at(fd, entry.name, policy, parent, max_bytes)
                    row.update(bytes=result.size, state=result.state,
                               sha256=hashlib.sha256(result.data).hexdigest() if result.state == "observed" else None)
                rows.append(row)
            else:
                raise UnsafeIO("unsupported_entry")
        if _signature(before) != _signature(os.fstat(fd)):
            raise UnsafeIO("changed_during_read")

    try:
        with open_directory(root, policy) as fd:
            walk(fd)
        return rows, "ok"
    except (UnsafeIO, OSError) as exc:
        return [], exc.state if isinstance(exc, UnsafeIO) else "denied"


def cache_entries(data):
    """Accept explicit mapping or list cache records; never infer from prose."""
    if data is None:
        return {}
    data = data.get("skills", data)
    if isinstance(data, dict):
        if any(not isinstance(v, dict) for v in data.values()):
            raise InvalidDocument()
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    if isinstance(data, list):
        if any(not isinstance(v, dict) or not isinstance(v.get("slug", v.get("name")), str) for v in data):
            raise InvalidDocument()
        return {v.get("slug", v.get("name")): v for v in data if isinstance(v, dict)
                and isinstance(v.get("slug", v.get("name")), str)}
    raise InvalidDocument()
