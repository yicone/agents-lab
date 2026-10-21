"""Disk installation discovery, independent of inventory/version governance.

Keep lexical paths: two symlinks to one skill are two installations. Only
ancestors are cycle checked, so aliases in different branches remain visible.
The same walk fingerprints dependencies for the Skills response cache.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

SKIP_DIRS = frozenset({"node_modules", "__pycache__", "dist", "build", "venv", "env", "site-packages"})
SUPPORT_DIRS = frozenset({"references", "templates", "assets", "scripts"})


def scan_installs(roots: list[Path]) -> dict:
    installs, issues, stamps = [], [], []
    seen: set[str] = set()
    links: dict[str, dict] = {}

    def problem(path: Path, code: str) -> None:
        issues.append({"path": str(path), "code": code})
        stamps.append((str(path), code))

    def probe(path: Path):
        try:
            own = path.lstat()
            target = path.stat() if stat.S_ISLNK(own.st_mode) else own
            link = os.readlink(path) if stat.S_ISLNK(own.st_mode) else None
            if link is not None:
                links[str(path)] = {"path": str(path), "target": link, "resolved": os.path.realpath(path)}
            stamps.append((str(path), own.st_ino, own.st_mode, own.st_mtime_ns, own.st_ctime_ns,
                           own.st_size, link, target.st_dev, target.st_ino, target.st_mode,
                           target.st_mtime_ns, target.st_ctime_ns, target.st_size))
            return target
        except OSError as exc:
            code = "denied" if isinstance(exc, PermissionError) else (
                "link_cycle" if exc.errno == errno.ELOOP else (
                    "missing" if exc.errno in (errno.ENOENT, errno.ENOTDIR) else "read_error"))
            problem(path, code)
            return None

    def walk(path: Path, ancestors: frozenset, root: Path, chain: tuple) -> None:
        lexical = str(path)
        if lexical in seen:
            return
        seen.add(lexical)
        st = probe(path)
        if st is None or not stat.S_ISDIR(st.st_mode):
            return
        identity = (st.st_dev, st.st_ino)
        if identity in ancestors:
            problem(path, "link_cycle")
            return
        try:
            children = sorted(path.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            problem(path, "denied" if isinstance(exc, PermissionError) else "read_error")
            return
        names = {p.name for p in children}
        skill = path / "SKILL.md"
        has_skill = False
        if "SKILL.md" in names:
            skill_stat = probe(skill)
            has_skill = skill_stat is not None and stat.S_ISREG(skill_stat.st_mode)
        if has_skill:
            installs.append({"path": lexical, "root": str(root),
                             "relative_path": path.relative_to(root).as_posix(),
                             "category": path.relative_to(root).parent.as_posix(),
                             "symlink_chain": list(chain)})
        next_chain = chain + (links[lexical],) if lexical in links else chain
        for child in children:
            if child.name in SKIP_DIRS or (child.name.startswith(".") and child.name != ".system"):
                continue
            if has_skill and child.name in SUPPORT_DIRS:
                continue
            # Probe links even when dangling, but avoid stamping ordinary payload
            # files: only directories and SKILL.md affect install visibility.
            try:
                is_dir = child.is_dir()
                is_link = child.is_symlink()
            except OSError:
                is_dir, is_link = True, False
            if is_dir or is_link:
                walk(child, ancestors | {identity}, root, next_chain)

    for raw in roots:
        root = Path(os.path.abspath(raw.expanduser()))
        walk(root, frozenset(), root, ())
    return {"installs": installs, "issues": issues, "stamp": tuple(stamps)}
