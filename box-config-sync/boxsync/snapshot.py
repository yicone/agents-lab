"""Snapshot assembly and bounded private output; never writes source roots."""
import importlib
import json
import os
import stat
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import sidecar
from .domains.common import UUID
from .multica_cli import CliRunner
from .policy import BLACKLIST_VERSION, Policy
from .roots import ACCESSIBLE_WORKSPACES, BOX_ROOTS, SETTINGS_PATH
from .safe_io import UnsafeIO, open_directory
from .secrets_scan import contains_secret
from .validation import InvalidDocument, load_schema, validate

MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
DOMAIN_NAMES = ("profiles", "multica_agents", "workflows", "managed_skills", "plugin_skills",
                "cloud_skills", "hooks", "mcps", "memory", "routines", "attachments")
_SCHEMA = load_schema("snapshot")


@dataclass
class Context:
    roots: dict = field(default_factory=lambda: dict(BOX_ROOTS))
    settings: Path | None = SETTINGS_PATH
    offline: bool = False
    cli: object = None
    workspaces: tuple = ACCESSIBLE_WORKSPACES
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    policy: Policy = field(default_factory=Policy)
    bots: set = field(default_factory=set)
    multica_mcps: list = field(default_factory=list)
    inaccessible_ids: dict = field(default_factory=dict)
    selected: tuple = DOMAIN_NAMES

    def inaccessible(self, workspace, reason):
        self.inaccessible_ids[workspace] = reason if reason in ("auth", "not_found", "timeout") else "error"


def tool_metadata():
    repo = Path(__file__).resolve().parents[2]
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
                                capture_output=True, timeout=5, env=env, check=True).stdout.decode().strip()
        dirty = bool(subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                                    capture_output=True, timeout=5, env=env, check=True).stdout)
        return {"commit": commit, "dirty": dirty, "version": "0.2.0"}
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {"commit": None, "dirty": None, "version": "0.2.0"}


def validate_snapshot(value):
    validate(value, _SCHEMA)
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(text.encode()) > MAX_SNAPSHOT_BYTES:
        raise InvalidDocument()
    if contains_secret(text):
        raise UnsafeIO("snapshot_secret")
    return text


def collect(ctx=None, sidecars=(), tool=None):
    ctx = ctx or Context()
    ctx.cli = ctx.cli or CliRunner(offline=ctx.offline)
    # Even malformed input can identify the Bot whose visibility must remain
    # not_collected. Never use an unvalidated id as a filesystem path.
    for _, data in sidecars:
        bot, _, _ = sidecar.parse(data, ctx.now)
        if bot and UUID.fullmatch(bot):
            ctx.bots.add(bot)
    domains = {name: [] for name in DOMAIN_NAMES}
    domains["hooks"] = {"scope": "external_host", "host": "tr-mini-m4", "state": "not_collected_on_box"}
    states = {}
    for name in DOMAIN_NAMES:
        if name in ctx.selected:
            module = importlib.import_module(".domains." + name, __package__)
            domains[name], states[name] = module.collect(ctx)
    disk_states = [v for k, v in states.items() if k not in ("multica_agents", "cloud_skills", "hooks")]
    cli_states = [v for k, v in states.items() if k in ("multica_agents", "cloud_skills")]
    source_state, consumed = sidecar.apply(ctx, domains, sidecars)
    cli_state = "not_collected" if ctx.offline or not cli_states else ("ok" if all(v == "ok" for v in cli_states) else "partial")
    result = {"schema": "box-config-snapshot/v1", "box_id": "grok-box",
              "collected_at": ctx.now.isoformat().replace("+00:00", "Z"),
              "tool": tool if tool is not None else tool_metadata(),
              "sources": {"box_disk": "ok" if disk_states and all(v == "ok" for v in disk_states) else "partial",
                          "multica_cli": cli_state, "sidecar": source_state},
              "policy": {"blacklist_version": BLACKLIST_VERSION, "skips": dict(ctx.policy.skips)},
              "inaccessible_workspaces": [{"workspace_id": k, "reason": v} for k, v in sorted(ctx.inaccessible_ids.items())],
              "domains": domains, "writeback": {"last_gate": None, "last_audit": None}}
    validate_snapshot(result)
    return result, consumed


def private_directory(path, create=False):
    """No symlink ancestor; only our final directory needs mode 0700."""
    path = Path(path)
    if Policy().denied_path(path):
        raise UnsafeIO("denied")
    if create:
        # Missing parents are private too; existing ancestor modes are untouched.
        parts = path.absolute().parts[1:]
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
        finally:
            os.close(fd)
    with open_directory(path) as fd:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise UnsafeIO("denied")


def write_snapshot(value, out):
    text = validate_snapshot(value)
    out = Path(out)
    if Policy().denied_path(out):
        raise UnsafeIO("denied")
    private_directory(out.parent)
    with open_directory(out.parent) as fd:
        try:
            info = os.stat(out.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            info = None
        if info and (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                     or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise UnsafeIO("denied")
        temporary = ".snapshot-" + uuid.uuid4().hex
        child = None
        try:
            child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            with os.fdopen(child, "w", encoding="utf-8") as stream:
                child = None
                stream.write(text + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, out.name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            if child is not None:
                os.close(child)
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass


def consume_sidecars(state_dir, inputs, consumed):
    """Only our 0700 inbox can be consumed; rejected inputs are retained."""
    inbox = Path(state_dir) / "inbox"
    for source, data in inputs:
        path = Path(source)
        if source == "-" or source not in consumed or path.parent.absolute() != inbox.absolute():
            continue
        private_directory(inbox)
        from .safe_io import read_at
        with open_directory(inbox) as fd:
            result = read_at(fd, path.name, max_bytes=sidecar.MAX_SIDECAR_BYTES)
            if result.state == "observed" and result.data == data:
                os.unlink(path.name, dir_fd=fd)
