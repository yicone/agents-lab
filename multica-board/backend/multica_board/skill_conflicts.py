"""Local, read-only conflict CLI adapter with bounded result caching."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

TTL_SECONDS = 60
_CACHE: dict = {}
_LOCK = threading.Lock()


def check_conflicts(script: Path, repo_root: Path, inventory: Path, roots: list[Path], stamp: tuple) -> dict:
    key = (str(script), str(repo_root), str(inventory), tuple(map(str, roots)), stamp,
           script.stat().st_mtime_ns if script.is_file() else None)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < TTL_SECONDS:
            return cached[1]
        command = [sys.executable, str(script), "--format", "json", "--repo-root", str(repo_root),
                   "--inventory", str(inventory), "--no-default-roots"]
        for root in roots:
            command.extend(["--scan-root", str(root)])
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=20, check=True)
            report = json.loads(completed.stdout)
            if not isinstance(report, dict) or report.get("report") != "skills-conflict/v1" or report.get("read_only") is not True:
                raise ValueError("unexpected report schema")
            findings = report.get("findings")
            if not isinstance(findings, list):
                raise ValueError("findings must be a list")
            for finding in findings:
                if (not isinstance(finding, dict) or not isinstance(finding.get("type"), str)
                    or not isinstance(finding.get("skills"), list) or not finding["skills"]
                    or not all(isinstance(name, str) for name in finding["skills"])
                    or not isinstance(finding.get("evidence"), list)
                    or not all(isinstance(value, str) for value in finding["evidence"])):
                    raise ValueError("invalid finding")
            result = {"state": "ok", "detail": f"本地实时检测：{len(findings)} 条发现（检测缓存 60 秒）", "findings": findings}
        except (OSError, subprocess.SubprocessError, ValueError, UnicodeError) as exc:
            result = {"state": "fallback", "detail": f"实时检测不可用（{type(exc).__name__}），仅保留人工标记", "findings": []}
        _CACHE.clear()
        _CACHE[key] = (time.monotonic(), result)
        return result
