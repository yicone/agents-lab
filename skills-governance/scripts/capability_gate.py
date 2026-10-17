"""SkilLock capability-drift gate for skill_guard.

Projects snapshot and candidate skill roots into the `.codex/skills/` layout
SkilLock scans, pins the snapshot with `skil-lock init --baseline`, and runs
`skil-lock ci` in block mode. When the binary is absent, the result records
the skill_guard risk-signal fallback instead of treating that absence as a pass.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from skill_layout import project_skills, read_behavior, resolve_skil_lock, run


BLOCK_POLICY = "mode: block\n"
SUMMARY_PREFIXES = ("PASS:", "BLOCK:", "WARN:", "error:")


def finish(payload: dict, behaviors: dict, measured: bool) -> dict:
    payload["behaviors"] = behaviors
    payload["behavior_measured"] = measured
    return payload


def evaluate(
    binary: str | None,
    fallback_payload: dict | None,
    baseline_roots: list[Path],
    candidate_roots: list[Path],
) -> dict:
    behaviors: dict = {}
    measured = False
    if fallback_payload is not None:
        payload = fallback_payload
    elif len(baseline_roots) != len(candidate_roots):
        payload = {
            "backend": "skil-lock",
            "tool": "skil-lock",
            "blocked": True,
            "error": True,
            "reason": "snapshot and candidate skill root counts differ",
        }
    else:
        try:
            with tempfile.TemporaryDirectory(prefix="skill-guard-skil-lock-") as directory:
                root = Path(directory)
                baseline_view = root / "baseline"
                candidate_view = root / "candidate"
                projected = project_skills(baseline_roots, baseline_view)
                project_skills(candidate_roots, candidate_view)
                behaviors, measured = read_behavior(binary, candidate_view, root / "observed.lock")
                (candidate_view / ".skil-lock.yaml").write_text(BLOCK_POLICY, encoding="utf-8")
                lock_path = candidate_view / "skills.lock"
                init = run([binary, "init", "--baseline", str(baseline_view), "-o", str(lock_path)])
                check = None if init is None or init.returncode != 0 else run(
                    [binary, "ci", str(candidate_view)]
                )
                if init is None or init.returncode != 0:
                    payload = invocation_error("skil-lock init --baseline failed", init, projected)
                elif check is None:
                    payload = invocation_error("skil-lock ci failed", check, projected)
                else:
                    summary = summary_line(check.stdout, check.stderr)
                    drift_blocked = check.returncode == 1 and "BLOCK:" in (check.stdout + check.stderr)
                    if check.returncode == 0 or drift_blocked:
                        payload = {
                            "backend": "skil-lock",
                            "tool": "skil-lock",
                            "blocked": drift_blocked,
                            "projected_skills": projected,
                            "summary": summary,
                        }
                    else:
                        payload = invocation_error("skil-lock ci failed", check, projected)
        except OSError as exc:
            behaviors = {}
            measured = False
            payload = {
                "backend": "skil-lock",
                "tool": "skil-lock",
                "blocked": True,
                "error": True,
                "reason": f"capability gate failed: {exc}",
            }
    return finish(payload, behaviors, measured)


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
