"""Exact read-only argv grammar, bounded output, isolated profile and cwd."""
import os
import re
import selectors
import subprocess
import tempfile
import time

from .roots import BOX_MULTICA_EXECUTABLE, BOX_MULTICA_PROFILE
from .validation import InvalidDocument, parse_json

MAX_OUTPUT = 8 * 1024 * 1024
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_LISTS = {("workspace", "list"), ("agent", "list"), ("skill", "list"), ("runtime", "list")}


class CommandNotAllowed(ValueError):
    pass


class CliError(Exception):
    def __init__(self, reason="error"):
        super().__init__(reason)
        self.reason = reason


def assert_read_only(args):
    args = tuple(args)
    if args in _LISTS:
        return
    if len(args) == 3 and args[:2] in (("agent", "get"), ("skill", "get")) and _UUID.fullmatch(args[2]):
        return
    if len(args) == 4 and args[:3] == ("agent", "mcp", "list") and _UUID.fullmatch(args[3]):
        return
    if len(args) == 4 and args[:2] == ("skill", "get") and _UUID.fullmatch(args[2]) and args[3] == "--with-content":
        return
    raise CommandNotAllowed("command_not_allowed")


class CliRunner:
    def __init__(self, binary=None, profile=BOX_MULTICA_PROFILE, offline=False, timeout=30, interval=1, server_url=None, env_provider=None):
        self.binary = binary or os.environ.get("MULTICA_CLI_PATH") or str(BOX_MULTICA_EXECUTABLE)
        self.profile, self.offline, self.timeout, self.interval = profile, offline, timeout, interval
        self.server_url, self.env_provider = server_url, env_provider
        self._last = 0

    def run(self, args, workspace_id=None):
        assert_read_only(args)
        if workspace_id is not None and not _UUID.fullmatch(workspace_id):
            raise CommandNotAllowed("invalid_workspace")
        if self.offline:
            raise CliError("not_collected")
        delay = self.interval - (time.monotonic() - self._last)
        if delay > 0:
            time.sleep(delay)
        argv = [self.binary, "--profile", self.profile]
        if self.server_url:
            argv += ["--server-url", self.server_url]
        if workspace_id:
            argv += ["--workspace-id", workspace_id]
        argv += list(args) + ["--output", "json"]
        env = self.env_provider() if self.env_provider else {k: v for k, v in os.environ.items() if not k.startswith("MULTICA_")}
        env["GODEBUG"] = "tlsmlkem=0"
        self._last = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="boxsync-cli-") as cwd:
                with subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
                    streams = {proc.stdout: bytearray(), proc.stderr: bytearray()}
                    deadline = time.monotonic() + self.timeout
                    try:
                        with selectors.DefaultSelector() as selector:
                            for stream in streams:
                                selector.register(stream, selectors.EVENT_READ)
                            while selector.get_map():
                                remaining = deadline - time.monotonic()
                                if remaining <= 0:
                                    raise CliError("timeout")
                                for key, _ in selector.select(min(remaining, 0.2)):
                                    chunk = os.read(key.fileobj.fileno(), 65536)
                                    if not chunk:
                                        selector.unregister(key.fileobj)
                                    else:
                                        streams[key.fileobj].extend(chunk)
                                    if sum(map(len, streams.values())) > MAX_OUTPUT:
                                        raise CliError("too_large")
                            try:
                                proc.wait(timeout=max(0.001, deadline - time.monotonic()))
                            except subprocess.TimeoutExpired:
                                raise CliError("timeout") from None
                        stdout, stderr = bytes(streams[proc.stdout]), bytes(streams[proc.stderr])
                        if proc.returncode:
                            auth = re.search(rb"(?i)\b(auth|unauthorized|forbidden|401|403)\b", stderr + stdout)
                            raise CliError("auth" if auth else "error")
                        # Do not silently report a truncated page as complete.
                        if b"--before" in stderr or b"--cursor" in stderr:
                            raise CliError()
                        try:
                            return parse_json(stdout)
                        except InvalidDocument:
                            raise CliError() from None
                    finally:
                        if proc.poll() is None:
                            proc.kill()
                        proc.wait()
        except OSError:
            raise CliError() from None
