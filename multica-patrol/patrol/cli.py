"""Allow-listed wrapper around the `multica` CLI subprocess."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config
from .redact import redact

READ_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("issue", "list"),
        ("issue", "get"),
        ("issue", "runs"),
        ("issue", "timeline"),
        ("issue", "comment", "list"),
        ("issue", "wakeup", "list"),
        ("agent", "list"),
        ("autopilot", "get"),
        ("autopilot", "runs"),
    }
)
WRITE_COMMAND = ("issue", "comment", "add")

_INJECTED_FLAGS = ("--profile", "--workspace-id", "--server-url")


class CommandNotAllowed(Exception):
    pass


class CliError(Exception):
    def __init__(self, message: str, *, transient: bool = False, uncertain: bool = False):
        super().__init__(redact(message) or "cli error")
        self.transient = transient
        # uncertain: a spawned write may have landed; never blindly resend.
        self.uncertain = uncertain


@dataclass
class CliResult:
    data: Any
    stderr: str = ""


def _words(args: list[str]) -> list[str]:
    return [a for a in args if not a.startswith("-")]


def _command_key(args: list[str]) -> tuple[str, ...] | None:
    words = _words(args)
    for n in (3, 2):
        key = tuple(words[:n])
        if key in READ_COMMANDS:
            return key
    return None


def _is_write(args: list[str]) -> bool:
    words = _words(args)
    return tuple(words[:3]) == WRITE_COMMAND


@dataclass
class PatrolCLI:
    """Executes allow-listed `multica` commands. Injectable exec/sleep for tests."""

    allowed_write_ids: frozenset[str]
    binary: str = "multica"
    timeout_s: float = 90.0
    retries: int = 1
    backoff_s: float = 2.0
    env: dict | None = None
    _exec: Callable = field(default=subprocess.run, repr=False)
    _sleep: Callable = field(default=time.sleep, repr=False)
    writes_attempted: int = 0
    reads: int = 0
    retry_permitted: set = field(default_factory=set)

    def permit_retry(self, issue_id: str) -> None:
        """Register one issue id as retry-writable for this run only.

        Called by the code path that just completed all re-read guards; without
        it, run_retry_write refuses before spawning a process.
        """
        self.retry_permitted.add(issue_id)

    def run_retry_write(self, issue_id: str, content_file) -> CliResult:
        """The provider-error retry comment. Permitted only for registered ids."""
        if issue_id not in self.retry_permitted:
            raise CommandNotAllowed(f"retry write not permitted for {issue_id}")
        with open(content_file) as fh:
            body = fh.read()
        first = next((ln for ln in body.splitlines() if ln.strip()), "")
        if not first.startswith(config.FIRST_LINE_NEXT):
            raise CommandNotAllowed("retry body first line format invalid")
        if config.RETRY_PREFIX not in body:
            raise CommandNotAllowed("retry body lacks [patrol-retry: marker")
        self.writes_attempted += 1
        argv = [self.binary, "issue", "comment", "add", issue_id,
                "--content-file", str(content_file), "--allow-external-file",
                "--output", "json"]
        return self._spawn(argv, write=True)

    def _check(self, args: list[str]) -> bool:
        for a in args:
            if a in _INJECTED_FLAGS or any(a.startswith(f + "=") for f in _INJECTED_FLAGS):
                raise CommandNotAllowed("profile/workspace/server flags are never passed")
        if _is_write(args):
            words = _words(args)
            issue_id = words[3] if len(words) > 3 else ""
            if issue_id not in self.allowed_write_ids:
                raise CommandNotAllowed(f"comment add not allowed for issue {issue_id}")
            return True
        if _command_key(args) is None:
            raise CommandNotAllowed(f"refusing non-allow-listed multica command: {' '.join(args[:4])}")
        return False

    def run_json(self, args: list[str]) -> CliResult:
        """Read path: allow-listed reads with transient retry."""
        is_write = self._check(args)
        if is_write:
            raise CommandNotAllowed("use run_write for comment add")
        self.reads += 1
        argv = [self.binary, *args]
        if "--output" not in args:
            argv += ["--output", "json"]
        last: CliError | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._spawn(argv, write=False)
            except CliError as exc:
                last = exc
                if not exc.transient or attempt == self.retries:
                    break
                self._sleep(self.backoff_s * (2**attempt))
        assert last is not None
        raise last

    def run_write(self, args: list[str]) -> CliResult:
        """The single allowed write. Never retried here: caller reconciles by read-back."""
        if not self._check(args):
            raise CommandNotAllowed("not a write command")
        self.writes_attempted += 1
        argv = [self.binary, *args]
        if "--output" not in args:
            argv += ["--output", "json"]
        return self._spawn(argv, write=True)

    def _env(self) -> dict | None:
        if self.env is None:
            return None
        return dict(self.env)

    def _spawn(self, argv: list[str], *, write: bool) -> CliResult:
        try:
            proc = self._exec(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                env=self._env(),
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise CliError(f"timeout after {self.timeout_s:.0f}s", transient=not write, uncertain=write)
        except FileNotFoundError:
            raise CliError(f"multica CLI not found: {self.binary}")

        stderr = proc.stderr or ""
        if proc.returncode != 0:
            msg = redact(stderr.strip() or f"exit code {proc.returncode}")
            transient = bool(re.search(r"(?i)timeout|timed out|connection|50[234]|429|rate limit|EOF", stderr))
            raise CliError(msg or "cli error", transient=transient and not write, uncertain=write)
        try:
            data = json.loads(proc.stdout, strict=False) if proc.stdout.strip() else None
        except json.JSONDecodeError:
            raise CliError("CLI returned non-JSON output", uncertain=write)
        return CliResult(data=data, stderr=stderr)
