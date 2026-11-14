"""Shared canonical instructions hashing and boxsync-tree/v1."""
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

from .policy import MAX_TREE_ENTRIES, Policy
from .safe_io import UnsafeIO, entries, open_directory, read_at, read_file, _signature

# Reuse the authoritative algorithm rather than keeping a second normalization.
_SCRIPTS = Path(__file__).resolve().parents[2] / "instructions-governance/scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from diff_instructions import instructions_sha256  # noqa: E402


def config_fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def body_sha256(root, relative, policy=None):
    result = read_file(root, relative, policy)
    return {"body_hash_state": "missing" if result.state == "absent" else result.state,
            "body_sha256": hashlib.sha256(result.data).hexdigest() if result.state == "observed" else None,
            "body_bytes": result.size}


def tree_sha256(root, policy=None):
    policy = policy or Policy()
    rows = []
    count = 0

    def walk(fd, relative="", depth=0):
        nonlocal count
        if depth > 64:
            raise UnsafeIO("too_large")
        before = os.fstat(fd)
        for entry in entries(fd, policy, relative):
            if entry.name == ".DS_Store":
                continue
            count += 1
            if count > MAX_TREE_ENTRIES:
                raise UnsafeIO("too_large")
            path = relative + "/" + entry.name if relative else entry.name
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if (info.st_dev, info.st_ino) != (os.fstat(child).st_dev, os.fstat(child).st_ino):
                        raise UnsafeIO("changed_during_read")
                    walk(child, path, depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                result = read_at(fd, entry.name, policy, relative)
                if result.state != "observed":
                    raise UnsafeIO(result.state)
                rows.append((path, hashlib.sha256(result.data).hexdigest(), result.size))
            else:
                raise UnsafeIO("unsupported_entry")
        if _signature(before) != _signature(os.fstat(fd)):
            raise UnsafeIO("changed_during_read")

    try:
        with open_directory(root, policy) as fd:
            walk(fd)
    except (UnsafeIO, OSError) as exc:
        return {"tree_state": exc.state if isinstance(exc, UnsafeIO) else "denied", "tree_sha256": None}
    payload = "".join(f"{path}\0{sha}\0{size}\n" for path, sha, size in
                      sorted(rows, key=lambda row: row[0].encode("utf-8")))
    return {"tree_state": "observed", "tree_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest()}
