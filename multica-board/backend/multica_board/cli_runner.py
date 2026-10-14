"""Allow-listed, read-only wrapper around the `multica` CLI."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .redact import redact

# Exact (command, subcommand...) prefixes the board may run. Anything else is refused
# before a process is spawned, so no code path can reach a mutating CLI command.
READ_ONLY_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("workspace", "list"),
        ("workspace", "member", "list"),
        ("issue", "list"),
        ("project", "list"),
        ("agent", "list"),
        ("agent", "tasks"),
        ("runtime", "list"),
    }
)

# The board passes --workspace-id explicitly; an inherited default must never leak in.
_STRIPPED_ENV = ("MULTICA_WORKSPACE_ID",)

_NEXT_CURSOR = re.compile(r'--before\s+"([^"]+)"')


class CommandNotAllowed(Exception):
    pass


class CliError(Exception):
    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


@dataclass
class CliResult:
    data: Any
    stderr: str = ""

    @property
    def next_cursor(self) -> str | None:
        m = _NEXT_CURSOR.search(self.stderr or "")
        return m.group(1) if m else None


def _command_key(args: list[str]) -> tuple[str, ...] | None:
    words = [a for a in args if not a.startswith("-")]
    for n in (3, 2):
        key = tuple(words[:n])
        if key in READ_ONLY_COMMANDS:
            return key
    return None


def assert_read_only(args: list[str]) -> None:
    if _command_key(args) is None:
        raise CommandNotAllowed(f"refusing non-allow-listed multica command: {' '.join(args[:3])}")
    for a in args:
        if a in ("--profile", "--workspace-id", "--server-url") or a.startswith(("--profile=", "--workspace-id=", "--server-url=")):
            raise CommandNotAllowed("global flags are injected by the runner, not callers")


@dataclass
class CliRunner:
    profile: str
    binary: str = "multica"
    server_url: str | None = None
    timeout_s: float = 60.0
    retries: int = 2
    backoff_s: float = 1.5
    max_parallel: int = 4
    _exec: Callable[..., subprocess.CompletedProcess] = field(default=subprocess.run, repr=False)
    _sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    def __post_init__(self) -> None:
        self._slots = threading.BoundedSemaphore(max(1, self.max_parallel))

    def _env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
        env["GODEBUG"] = "tlsmlkem=0"
        return env

    def run(self, args: list[str], workspace_id: str | None = None) -> CliResult:
        assert_read_only(args)
        argv = [self.binary, "--profile", self.profile]
        if self.server_url:
            argv += ["--server-url", self.server_url]
        if workspace_id:
            argv += ["--workspace-id", workspace_id]
        argv += list(args)
        if "--output" not in args:
            argv += ["--output", "json"]

        last: CliError | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._run_once(argv)
            except CliError as exc:
                last = exc
                if not exc.transient or attempt == self.retries:
                    break
                self._sleep(self.backoff_s * (2**attempt))
        assert last is not None
        raise last

    def _run_once(self, argv: list[str]) -> CliResult:
        try:
            with self._slots:
                proc = self._exec(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    env=self._env(),
                    stdin=subprocess.DEVNULL,
                )
        except subprocess.TimeoutExpired:
            raise CliError(f"timeout after {self.timeout_s:.0f}s", transient=True)
        except FileNotFoundError:
            raise CliError(f"multica CLI not found: {self.binary}")

        stderr = proc.stderr or ""
        if proc.returncode != 0:
            msg = redact(stderr.strip() or f"exit code {proc.returncode}")
            transient = bool(re.search(r"(?i)timeout|timed out|connection|50[234]|429|rate limit|EOF", stderr))
            raise CliError(msg or "cli error", transient=transient)
        try:
            data = json.loads(proc.stdout) if proc.stdout.strip() else None
        except json.JSONDecodeError:
            raise CliError("CLI returned non-JSON output")
        return CliResult(data=data, stderr=stderr)
