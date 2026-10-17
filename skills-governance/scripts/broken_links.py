"""Shared broken-symlink scanning and conservative repair helpers."""
from __future__ import annotations

import os
from pathlib import Path


def scan(root: Path) -> list[Path]:
    """Return broken symlinks below *root*, including a broken root link."""
    if root.is_symlink():
        return [root] if not root.exists() else []
    if not root.is_dir():
        return []
    found: list[Path] = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            path = Path(directory) / name
            if path.is_symlink() and not path.exists():
                found.append(path)
    return sorted(found, key=lambda path: path.as_posix())


def repair(path: Path, target: str) -> Path:
    """Replace one broken link with the recorded target; refuse live paths."""
    if not path.is_symlink() or path.exists():
        raise ValueError(f"not a broken symlink: {path}")
    path.unlink()
    path.symlink_to(target)
    return path


def resolved_target(path: Path) -> str:
    return os.readlink(path)
