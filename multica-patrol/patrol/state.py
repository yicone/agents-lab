"""Atomic JSON state store with an fcntl non-blocking lock (sidecar pattern)."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path


def default_root() -> Path:
    override = os.environ.get("PATROL_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".local/state/multica-patrol"


def initial_state() -> dict:
    return {
        "version": 2,
        "last_slot": None,
        "slots": {},
        "cache": {},
        "alerted": {},
        "failures": [],
        "consecutive_failures": 0,
        "self_escalated_at": None,
        "inflight": None,
        "pending_escalation": None,
        "identity_violation": None,
        "last_full_scan_at": None,
        "previous_run_complete": True,
        "unresolved_reported_at": None,
        "daily": {},
        "agent_names": {},
        "issue_index": {},
    }


class Store:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else default_root()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.path = self.root / "state.json"
        self._lock_file = None

    def load(self) -> dict:
        if not self.path.exists():
            return initial_state()
        state = initial_state()
        state.update(json.loads(self.path.read_text(), strict=False))
        return state

    def save(self, state: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        dirfd = os.open(str(self.root), os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)

    def write_body(self, name: str, body: str) -> Path:
        path = self.root / name
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        return path

    def acquire(self) -> bool:
        self._lock_file = (self.root / "service.lock").open("a")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock_file.close()
            self._lock_file = None
            return False
        return True

    def release(self) -> None:
        if self._lock_file is not None:
            fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None
