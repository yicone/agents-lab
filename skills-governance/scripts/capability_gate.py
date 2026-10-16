"""SkilLock capability-drift gate for skill_guard.

Projects snapshot and candidate skill roots into the `.codex/skills/` layout
SkilLock scans, pins the snapshot with `skil-lock init --baseline`, and runs
`skil-lock ci` in block mode. When the binary is absent, the result records
the skill_guard risk-signal fallback instead of treating that absence as a pass.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


BLOCK_POLICY = "mode: block\n"
SUMMARY_PREFIXES = ("PASS:", "BLOCK:", "WARN:", "error:")


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


def fallback(reason: str) -> dict:
    return {
        "backend": "fallback",
        "tool": "skil-lock",
        "blocked": False,
        "fallback": "skill_guard risk signals",
        "reason": reason,
    }


def evaluate(
    binary: str | None,
    fallback_payload: dict | None,
    baseline_roots: list[Path],
    candidate_roots: list[Path],
) -> dict:
    if fallback_payload is not None:
        return fallback_payload
    if len(baseline_roots) != len(candidate_roots):
        return {
            "backend": "skil-lock",
            "tool": "skil-lock",
            "blocked": True,
            "error": True,
            "reason": "snapshot and candidate skill root counts differ",
        }
    try:
        with tempfile.TemporaryDirectory(prefix="skill-guard-skil-lock-") as directory:
            root = Path(directory)
            baseline_view = root / "baseline"
            candidate_view = root / "candidate"
            projected = project_skills(baseline_roots, baseline_view)
            project_skills(candidate_roots, candidate_view)
            (candidate_view / ".skil-lock.yaml").write_text(BLOCK_POLICY, encoding="utf-8")
            lock_path = candidate_view / "skills.lock"
            init = run(
                [binary, "init", "--baseline", str(baseline_view), "-o", str(lock_path)]
            )
            if init is None or init.returncode != 0:
                return invocation_error("skil-lock init --baseline failed", init, projected)
            check = run([binary, "ci", str(candidate_view)])
            if check is None:
                return invocation_error("skil-lock ci failed", check, projected)
            summary = summary_line(check.stdout, check.stderr)
            blocked = check.returncode == 1 and "BLOCK:" in (check.stdout + check.stderr)
            if check.returncode == 0:
                return {
                    "backend": "skil-lock",
                    "tool": "skil-lock",
                    "blocked": False,
                    "projected_skills": projected,
                    "summary": summary,
                }
            if blocked:
                return {
                    "backend": "skil-lock",
                    "tool": "skil-lock",
                    "blocked": True,
                    "projected_skills": projected,
                    "summary": summary,
                }
            return invocation_error("skil-lock ci failed", check, projected)
    except OSError as exc:
        return {
            "backend": "skil-lock",
            "tool": "skil-lock",
            "blocked": True,
            "error": True,
            "reason": f"capability gate failed: {exc}",
        }


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


def summary_line(stdout: str, stderr: str) -> str:
    for line in (stderr + "\n" + stdout).splitlines():
        stripped = line.strip()
        if stripped.startswith(SUMMARY_PREFIXES):
            return stripped
    text = (stderr or stdout).strip()
    if not text:
        return ""
    return text.splitlines()[0][:200]


def invocation_error(
    reason: str, completed: subprocess.CompletedProcess[str] | None, projected: int
) -> dict:
    detail = reason
    if completed is not None:
        summary = summary_line(completed.stdout, completed.stderr)
        if summary:
            detail = f"{reason}: {summary}"
        detail = f"{detail} (exit {completed.returncode})"
    else:
        detail = f"{reason}: not executable or timed out"
    return {
        "backend": "skil-lock",
        "tool": "skil-lock",
        "blocked": True,
        "error": True,
        "projected_skills": projected,
        "reason": detail,
    }
