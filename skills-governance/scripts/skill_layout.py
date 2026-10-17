"""Skill directory projection and SkilLock behavior reads shared by the gate and the audit.

`capability_gate` and `capability_contract` both import this module. The behavior
parser stays in `capability_contract` and is imported here only inside
`read_behavior`, so the two callers do not import each other.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def fallback(reason: str) -> dict:
    return {
        "backend": "fallback",
        "tool": "skil-lock",
        "blocked": False,
        "fallback": "skill_guard risk signals",
        "reason": reason,
    }


def resolve_skil_lock(explicit: str | None) -> tuple[str | None, dict | None]:
    """Return (binary, fallback_payload). A payload means do not invoke a binary."""
    if explicit == "fallback":
        return None, fallback("skil-lock backend disabled")
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate), None
        found = shutil.which(explicit)
        if found:
            return found, None
        return None, {
            "backend": "skil-lock",
            "tool": "skil-lock",
            "blocked": True,
            "error": True,
            "reason": f"skil-lock binary is not executable: {explicit}",
        }
    found = shutil.which("skil-lock")
    if not found:
        return None, fallback("skil-lock is not on PATH")
    return found, None


def run(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def read_behavior(binary: str, view: Path, lock_path: Path) -> tuple[dict, bool, str | None]:
    """Read the candidate behavior map from `skil-lock init`.

    Returns `(behaviors, measured, error)`. `error` is set only after the
    binary ran and the lock could not be read without dropping observations.
    A missing binary is not this function's job; callers record that as fallback.
    """
    import capability_contract  # local import: this module must not import it at load time

    init = run([binary, "init", "--baseline", str(view), "-o", str(lock_path)])
    if init is None or init.returncode != 0 or not lock_path.is_file():
        return {}, False, None
    try:
        text = lock_path.read_text(encoding="utf-8", errors="replace")
        parsed = capability_contract.parse_lock_behaviors(text)
    except capability_contract.LockParseError as exc:
        return {}, False, str(exc)
    except OSError as exc:
        return {}, False, f"skills.lock could not be read: {exc}"
    if parsed is None:
        return {}, False, "skills.lock is not a behavior map"
    return parsed, True, None


def project_skills(roots: list[Path], view: Path) -> int:
    dest_root = view / ".codex" / "skills"
    dest_root.mkdir(parents=True)
    used: set[str] = set()
    count = 0
    for root in roots:
        for skill in iter_skill_dirs(root):
            slug = unique_name(skill_label(skill), used)
            dest = dest_root / slug
            dest.mkdir()
            for child in skill.iterdir():
                (dest / child.name).symlink_to(child.absolute())
            count += 1
    return count


def iter_skill_dirs(root: Path) -> list[Path]:
    if (root / "SKILL.md").is_file():
        return [root]
    if not root.is_dir():
        return []
    children = []
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return []
    for child in entries:
        if (child / "SKILL.md").is_file():
            children.append(child)
    if children:
        return children
    found = []
    try:
        matches = sorted(root.rglob("SKILL.md"))
    except OSError:
        return []
    for path in matches:
        if path.is_file():
            found.append(path.parent)
    return found


def skill_label(directory: Path) -> str:
    try:
        text = (directory / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return directory.name
    if text.startswith("---") and text.count("---") >= 2:
        front = text.split("---", 2)[1]
        for line in front.splitlines():
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip().strip("\"'")
                if name:
                    return name
    return directory.name


def unique_name(name: str, used: set[str]) -> str:
    slug = name or "skill"
    if slug not in used:
        used.add(slug)
        return slug
    number = 2
    while f"{slug}-{number}" in used:
        number += 1
    chosen = f"{slug}-{number}"
    used.add(chosen)
    return chosen
