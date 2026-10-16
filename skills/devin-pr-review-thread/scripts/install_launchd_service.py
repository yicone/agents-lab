#!/usr/bin/env python3
"""Install the host worker as a login-persistent macOS LaunchAgent."""

from __future__ import annotations

import argparse
import os
import pathlib
import plistlib
import shutil
import subprocess
import sys
from typing import Any

LABEL = "com.tr.agentslab.devin-pr-review-thread"
QUEUE = "/private/tmp/devin-host-review"


def render_plist(repo_root: str, python_path: str, devin_path: str) -> dict[str, Any]:
    script = pathlib.Path(repo_root) / "skills/devin-pr-review-thread/scripts/host_worker.py"
    home = pathlib.Path.home()
    return {
        "Label": LABEL,
        "ProgramArguments": [python_path, str(script), "--queue", QUEUE],
        "WorkingDirectory": repo_root,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 5,
        "EnvironmentVariables": {
            "PATH": f"{home}/.local/bin:{home}/.pyenv/shims:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            "PYTHONUNBUFFERED": "1",
            "https_proxy": "http://127.0.0.1:7890",
            "http_proxy": "http://127.0.0.1:7890",
            "all_proxy": "socks5://127.0.0.1:7890",
        },
        "StandardOutPath": "/private/tmp/devin-host-review/worker.stdout.log",
        "StandardErrorPath": "/private/tmp/devin-host-review/worker.stderr.log",
    }


def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], text=True, capture_output=True, timeout=15)


def install(repo_root: str, python_path: str, devin_path: str, launch_agents: pathlib.Path) -> pathlib.Path:
    launch_agents.mkdir(mode=0o700, parents=True, exist_ok=True)
    plist_path = launch_agents / f"{LABEL}.plist"
    with plist_path.open("wb") as stream:
        plistlib.dump(render_plist(repo_root, python_path, devin_path), stream, sort_keys=False)
    os.chmod(plist_path, 0o600)
    domain = f"gui/{os.getuid()}"
    launchctl("bootout", f"{domain}/{LABEL}")
    loaded = launchctl("bootstrap", domain, str(plist_path))
    if loaded.returncode != 0:
        raise RuntimeError(loaded.stderr.strip() or "launchctl bootstrap failed")
    started = launchctl("kickstart", "-k", f"{domain}/{LABEL}")
    if started.returncode != 0:
        raise RuntimeError(started.stderr.strip() or "launchctl kickstart failed")
    return plist_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the Devin host worker LaunchAgent")
    parser.add_argument("--repo-root", default=str(pathlib.Path(__file__).resolve().parents[3]))
    parser.add_argument("--python", dest="python_path", default=sys.executable)
    parser.add_argument("--devin", dest="devin_path", default=shutil.which("devin") or "/Users/tr/.local/bin/devin")
    parser.add_argument("--launch-agents", default=str(pathlib.Path.home() / "Library/LaunchAgents"))
    args = parser.parse_args()
    path = install(str(pathlib.Path(args.repo_root).resolve()), args.python_path, args.devin_path, pathlib.Path(args.launch_agents).expanduser())
    print(f"installed {LABEL} at {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
