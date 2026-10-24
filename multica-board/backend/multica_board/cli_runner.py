"""Allow-listed, read-only wrapper around the `multica` CLI."""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .redact import redact

# Exact (command, subcommand...) prefixes the board may run. Anything else is refused
# before a process is spawned, so no code path can reach a mutating CLI command.
READ_ONLY_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("auth", "status"),
        ("workspace", "list"),
        ("workspace", "member", "list"),
        ("workspace", "mcp", "list"),
        ("issue", "list"),
        ("issue", "timeline"),
        ("issue", "comment", "list"),
        ("project", "list"),
        ("agent", "list"),
        ("agent", "mcp", "list"),
        ("agent", "tasks"),
        ("skill", "list"),
        ("skill", "get"),
        ("runtime", "list"),
        ("runtime", "usage"),
        ("runtime", "activity"),
        ("property", "list"),
    }
)

# The child CLI must authenticate only through --profile. Inherited task/agent credentials or
# overrides would silently scope it to another identity or workspace.
_STRIPPED_ENV = ("MULTICA_WORKSPACE_ID", "MULTICA_TOKEN", "MULTICA_SERVER_URL",
                 "MULTICA_AGENT_ID", "MULTICA_AGENT_NAME", "MULTICA_DAEMON_PORT")
_STRIPPED_ENV_PREFIXES = ("MULTICA_TASK_",)

_NEXT_CURSOR = re.compile(r'--before\s+"([^"]+)"')

# The CLI discovers a `.multica` directory from its cwd upward, and a match overrides --profile.
# Children therefore always start in an empty private directory, whatever the board's own cwd is.
_neutral_cwd: str | None = None
_neutral_lock = threading.Lock()


def neutral_cwd() -> str:
    global _neutral_cwd
    with _neutral_lock:
        if _neutral_cwd is None or not os.path.isdir(_neutral_cwd):
            _neutral_cwd = tempfile.mkdtemp(prefix="multica-board-cli-")
            atexit.register(shutil.rmtree, _neutral_cwd, True)
        return _neutral_cwd


class CommandNotAllowed(Exception):
    pass


class CliError(Exception):
    def __init__(self, message: str, *, transient: bool = False):
        # Messages surface in /api/meta, so every one is redacted at construction.
        super().__init__(redact(message) or "cli error")
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
    task_scoped: bool = False
    _exec: Callable[..., subprocess.CompletedProcess] = field(default=subprocess.run, repr=False)
    _sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    def __post_init__(self) -> None:
        self._slots = threading.BoundedSemaphore(max(1, self.max_parallel))

    def _env(self) -> dict[str, str]:
        if self.task_scoped:
            # Keep the current task identity; never fall back to an Owner profile.
            if not os.environ.get("MULTICA_TASK_CONFIG_ROOT"):
                raise CliError("task-scoped CLI requires the current task config root")
            env = dict(os.environ)
            env["GODEBUG"] = "tlsmlkem=0"
            return env
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in _STRIPPED_ENV and not k.startswith(_STRIPPED_ENV_PREFIXES)
        }
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
        identity_only = args == ["auth", "status"]
        if "--output" not in args and not identity_only:
            argv += ["--output", "json"]

        last: CliError | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._run_once(argv, identity_only=identity_only)
            except CliError as exc:
                last = exc
                if not exc.transient or attempt == self.retries:
                    break
                self._sleep(self.backoff_s * (2**attempt))
        assert last is not None
        raise last

    def _run_once(self, argv: list[str], *, identity_only: bool = False) -> CliResult:
        try:
            with self._slots:
                proc = self._exec(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    env=self._env(),
                    cwd=neutral_cwd(),
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
        if identity_only:
            # auth status has no JSON mode. Keep only one verified Server origin.
            # User and token lines on either stream must not leave this function.
            from .plan_contract import server_identity
            pattern = re.compile(r"^Server:\s+(\S+)\s*$", re.MULTILINE)

            def server_lines(text: str) -> list[str]:
                return pattern.findall(text or "")

            raw = server_lines(proc.stdout) + server_lines(proc.stderr)
            try:
                origins = {server_identity(line) for line in raw}
                if len(raw) == 0 or len(origins) != 1:
                    raise ValueError("missing or ambiguous origin")
                return CliResult({"server_identity": origins.pop()})
            except ValueError:
                raise CliError("CLI server identity could not be verified") from None
        try:
            data = json.loads(proc.stdout) if proc.stdout.strip() else None
        except json.JSONDecodeError:
            raise CliError("CLI returned non-JSON output")
        return CliResult(data=data, stderr=stderr)
