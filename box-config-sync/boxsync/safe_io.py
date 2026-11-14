"""Descriptor-relative reads, no symlink traversal, bounded and nonblocking."""
import errno
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import PurePath

from .policy import MAX_FILE_BYTES, Policy


class UnsafeIO(Exception):
    def __init__(self, state):
        super().__init__(state)
        self.state = state


def error_state(exc):
    if exc.errno == errno.ENOENT:
        return "absent"
    if exc.errno in (errno.EACCES, errno.EPERM, errno.ELOOP, errno.ENOTDIR):
        return "denied"
    return "error"


@contextmanager
def open_directory(path, policy=None):
    """Validate every name before *any* filesystem access, then open each segment."""
    policy = policy or Policy()
    path = os.fspath(path)
    if policy.denied_path(path):
        raise UnsafeIO("denied")
    parts = PurePath(path).parts
    fd = None
    try:
        fd = os.open("/" if os.path.isabs(path) else ".",
                     os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in parts:
            if part in ("/", "."):
                continue
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    except OSError as exc:
        raise UnsafeIO(error_state(exc)) from None
    finally:
        if fd is not None:
            os.close(fd)


def entries(fd, policy, parent=""):
    """Only name access precedes policy. Callers may inspect the yielded entries."""
    with os.scandir(fd) as iterator:
        for entry in iterator:
            if not policy.skip(entry.name, parent):
                yield entry


@dataclass(frozen=True)
class ReadResult:
    state: str
    data: bytes = field(default=b"", repr=False)
    size: object = None


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)


def read_at(fd, name, policy=None, parent="", max_bytes=MAX_FILE_BYTES):
    policy = policy or Policy()
    if "/" in name or name in ("", ".", "..") or policy.skip(name, parent):
        return ReadResult("denied")
    child = None
    try:
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        before = os.fstat(child)
        if not stat.S_ISREG(before.st_mode):
            return ReadResult("not_regular")
        if before.st_size > max_bytes:
            return ReadResult("too_large", size=before.st_size)
        chunks = []
        size = 0
        while size <= max_bytes:
            chunk = os.read(child, min(65536, max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        after = os.fstat(child)
        # Check the name as well: rename/replacement during the read is a conflict.
        named = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if _signature(before) != _signature(after) or _signature(after) != _signature(named):
            return ReadResult("changed_during_read")
        if size > max_bytes:
            return ReadResult("too_large", size=size)
        if size != after.st_size:
            return ReadResult("changed_during_read")
        return ReadResult("observed", b"".join(chunks), size)
    except OSError as exc:
        return ReadResult("changed_during_read" if child is not None and exc.errno == errno.ENOENT
                          else error_state(exc))
    finally:
        if child is not None:
            os.close(child)


def read_file(root, relative, policy=None, max_bytes=MAX_FILE_BYTES):
    policy = policy or Policy()
    relative = os.fspath(relative)
    if os.path.isabs(relative) or policy.denied_path(relative):
        return ReadResult("denied")
    path = PurePath(relative)
    if not path.name:
        return ReadResult("denied")
    try:
        with open_directory(os.path.join(os.fspath(root), str(path.parent)), policy) as fd:
            return read_at(fd, path.name, policy, str(path.parent), max_bytes)
    except UnsafeIO as exc:
        return ReadResult(exc.state)
