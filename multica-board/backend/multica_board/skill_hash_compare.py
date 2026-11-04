"""Read-only comparison of current local SKILL.md bytes, independent of drift.

Groups are a compact matrix: entries in one group match, entries in different
groups mismatch, and entries in unknown_entry_indexes cannot be compared.
No file body is returned or persisted.
"""
from __future__ import annotations

import errno
import hashlib
import os
import stat
from pathlib import Path
from typing import Callable


def _fingerprint(path: Path) -> dict:
    target = path if path.name == "SKILL.md" else path / "SKILL.md"
    result = {"content_hash": None, "hash_file": str(target), "hash_state": "unknown"}
    try:
        # Nonblocking open plus fstat prevents a FIFO/device masquerading as a
        # skill file from hanging the read-only payload builder.
        fd = os.open(target, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                return {**result, "hash_state": "not_regular"}
            digest = hashlib.sha256()
            while chunk := handle.read(64 * 1024):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        if (before.st_mtime_ns, before.st_ctime_ns, before.st_size) != (
                after.st_mtime_ns, after.st_ctime_ns, after.st_size):
            return {**result, "hash_state": "changed_during_read"}
        return {**result, "content_hash": digest.hexdigest(), "hash_state": "ok"}
    except OSError as exc:
        state = ("denied" if isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM)
                 else "missing" if exc.errno in (errno.ENOENT, errno.ENOTDIR) else "unknown")
        return {**result, "hash_state": state}


def compare_entries(entries: list[dict], locate: Callable[[str], Path]) -> dict:
    """Attach per-entry hashes and return equivalence groups by entry index.

    ``locate`` uses the same inventory path resolution as installation discovery.
    A mismatch is conclusive even with unknown entries; a match requires every
    entry to be readable and at least two entries. Lexical aliases remain entries.
    """
    groups: dict[str, list[int]] = {}
    unknown = []
    for index, entry in enumerate(entries):
        try:
            fingerprint = _fingerprint(locate(entry["path"]))
        except (OSError, ValueError, RuntimeError):
            fingerprint = {"content_hash": None, "hash_file": None, "hash_state": "unknown"}
        entry.update(fingerprint)
        if fingerprint["hash_state"] == "ok":
            groups.setdefault(fingerprint["content_hash"], []).append(index)
        else:
            unknown.append(index)
    status = ("mismatch" if len(groups) > 1 else "incomplete" if unknown else
              "match" if len(entries) >= 2 else "insufficient_entries")
    return {"algorithm": "sha256", "subject": "SKILL.md", "status": status,
            "entry_count": len(entries), "compared_entry_count": len(entries) - len(unknown),
            "groups": [{"content_hash": digest, "entry_indexes": indexes}
                       for digest, indexes in groups.items()],
            "unknown_entry_indexes": unknown}
