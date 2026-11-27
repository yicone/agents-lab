"""Dry-run workflow plans. Only private artifacts below state-dir/plans are written.

ACM-929 selected host-maintained fingerprints: never write the fingerprint.
Unknown fingerprint formats can explicitly fall back to W1 UpdateSkill packages.
No apply, commit, checkout, push, Multica writes or live staging is performed here.
"""
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
import uuid

from .domains.common import SLUG, UUID, directory_names
from .gitio import GitError, GitReader
from .hashing import tree_sha256
from .policy import MAX_TREE_ENTRIES, Policy
from .roots import BOX_ROOTS, BOX_STATE_DIR, fixture_roots
from .safe_io import UnsafeIO, entries, open_directory, read_at, read_file, _signature
from .secrets_scan import contains_secret
from .snapshot import MAX_SNAPSHOT_BYTES, validate_snapshot
from .validation import InvalidDocument, load_schema, parse_json, timestamp, validate

_SCHEMA = load_schema("writeback-plan")
_REPO = Path(__file__).resolve().parents[2]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def plan_sha256(plan):
    """The digest field itself is excluded, as specified by the approved schema."""
    return hashlib.sha256(canonical({k: v for k, v in plan.items() if k != "plan_sha256"})).hexdigest()


def validate_plan(plan):
    validate(plan, _SCHEMA)
    if plan_sha256(plan) != plan["plan_sha256"]:
        raise InvalidDocument()
    if timestamp(plan["expires_at"]) <= timestamp(plan["created_at"]):
        raise InvalidDocument()


def expired(plan, now=None):
    validate_plan(plan)
    return (now or datetime.now(timezone.utc)) >= timestamp(plan["expires_at"])


def _sha(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def _tree(files):
    payload = "".join(f"{p}\0{_sha(data)}\0{len(data)}\n"
                      for p, data in sorted(files.items(), key=lambda pair: pair[0].encode()))
    return _sha(payload.encode())


def _live(root):
    """Read safe regular files only; a failed read is never proof of absence."""
    result = {}
    policy = Policy()
    count = 0

    def walk(fd, parent="", depth=0):
        nonlocal count
        if depth > 64:
            raise UnsafeIO("too_large")
        before = os.fstat(fd)
        for entry in entries(fd, policy, parent):
            if entry.name == ".DS_Store":
                continue
            count += 1
            if count > MAX_TREE_ENTRIES:
                raise UnsafeIO("too_large")
            path = parent + "/" + entry.name if parent else entry.name
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if _signature(info) != _signature(os.fstat(child)):
                        raise UnsafeIO("changed_during_read")
                    walk(child, path, depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                body = read_at(fd, entry.name, policy, parent)
                if body.state != "observed":
                    raise UnsafeIO(body.state)
                result[path] = body.data
            else:
                raise UnsafeIO("unsupported_entry")
        if _signature(before) != _signature(os.fstat(fd)):
            raise UnsafeIO("changed_during_read")

    try:
        with open_directory(root) as fd:
            walk(fd)
    except UnsafeIO as exc:
        if exc.state == "absent":
            # Only an absent root is a create, not a disappearing child.
            with open_directory(Path(root).parent) as fd:
                try:
                    os.stat(Path(root).name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    return None
        raise
    check = tree_sha256(root)
    if check["tree_state"] != "observed" or check["tree_sha256"] != _tree(result):
        raise UnsafeIO("changed_during_read")
    return result


def _safe_files(files):
    if files is None:
        return
    if len(files) > MAX_TREE_ENTRIES:
        raise UnsafeIO("too_large")
    for path, body in files.items():
        if (Policy().denied_path(path) or contains_secret(path)
                or contains_secret(body.decode("utf-8", errors="replace"))):
            raise UnsafeIO("withheld_secret")


def _private_dir(path):
    # Existing directories are validated, never chmod'ed or followed.
    with open_directory(path.parent) as parent:
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
    with open_directory(path) as fd:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise UnsafeIO("unsafe_state_directory")


def _write(path, data):
    # Artifacts are immutable. Never truncate an existing path or follow a link.
    with open_directory(path.parent) as parent:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())


def _materialize(root, files):
    _private_dir(root)
    for relative, body in sorted(files.items()):
        dest = root / relative
        for directory in reversed(dest.parent.parents):
            if directory == root or root in directory.parents:
                _private_dir(directory)
        _private_dir(dest.parent)
        _write(dest, body)


def _gate(work, before, after):
    """Run the existing gate on private copies, forcing its skill_guard fallback.

    The fallback detects local risk signals, not SkilLock capability approval.
    Errors are represented in the plan and cannot turn into a pass.
    """
    baseline, staging = work / "baseline", work / "staging"
    for directory in (baseline, staging):
        _private_dir(directory)
    _materialize(baseline / "root", before)
    _materialize(staging / "root", after)
    metadata = []
    for path, body in sorted(before.items()):
        # Match skill_guard.entries, including directories and their modes.
        metadata.append({"path": path, "type": "file", "mode": 0o600,
                         "sha256": _sha(body), "size": len(body)})
    directories = {str(parent) for path in before for parent in Path(path).parents if str(parent) != "."}
    metadata += [{"path": path, "type": "directory", "mode": 0o700} for path in sorted(directories)]
    _write(baseline / "manifest.json", canonical({"schema": "skill-governance-snapshot/v1", "roots": [
        {"kind": "skill", "original_path": str(baseline / "root"), "snapshot_path": "root", "entries": metadata}]}))
    _write(staging / "manifest.json", canonical({"schema": "skill-governance-staging/v1", "roots": [
        {"staging_path": "root"}]}))
    record = work / "last-gate.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "SKIL_LOCK"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run([sys.executable, str(_REPO / "skills-governance/scripts/skill_guard.py"),
                               "diff", "--snapshot", str(baseline), "--staging", str(staging),
                               "--gate", "--skil-lock", "fallback", "--gate-record", str(record),
                               "--format", "json"], env=env, cwd=work, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
        raw = read_file(work, record.name, max_bytes=1024 * 1024)
        value = parse_json(raw.data)
        if (proc.returncode not in (0, 2) or raw.state != "observed"
                or value.get("schema") != "skill-governance-last-gate/v1"
                or value.get("source") != "skill_guard"
                or value.get("result") not in ("pass", "block")
                or (proc.returncode == 2) != (value["result"] == "block")):
            raise InvalidDocument()
        return {"state": "block" if value["result"] == "block" else "fallback",
                "signals": sorted(set(value.get("signals", [])))}
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return {"state": "error", "signals": []}


def plan_writeback(snapshot, reader, live_root, state_dir, *, direction="box-to-git",
                   select=None, allow_create=False, allow_delete=False, ttl=86400,
                   now=None, fingerprint_format_confirmed=True, bot_id=None, snapshot_bytes=None):
    """Generate and persist a metadata-only plan from a snapshot and fresh bytes.

    now is an explicit clock input for reproducibility; the default is UTC now.
    state_dir must already exist with mode 0700. All outputs live in plans/.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or type(ttl) is not int or not 0 < ttl <= 7 * 86400:
        raise ValueError("invalid_ttl_or_clock")
    if direction not in ("box-to-git", "git-to-box"):
        raise ValueError("invalid_direction")
    if contains_secret(reader.ref):
        raise UnsafeIO("withheld_secret")
    validate_snapshot(snapshot)
    if snapshot_bytes is None:
        snapshot_bytes = canonical(snapshot)
    elif len(snapshot_bytes) > MAX_SNAPSHOT_BYTES or parse_json(snapshot_bytes) != snapshot:
        raise InvalidDocument()
    if snapshot.get("domain_states", {}).get("workflows") not in ("ok", "absent"):
        raise UnsafeIO("live_unknown")
    age = (now - timestamp(snapshot["collected_at"])).total_seconds()
    if age < -300 or age > 36 * 3600:
        raise UnsafeIO("snapshot_stale")
    state_dir, live_root = Path(state_dir).absolute(), Path(live_root).absolute()
    repo = Path(reader.repo).absolute()
    # Output must not be inside either target tree, including through aliases.
    for target in (live_root.resolve(), repo.resolve()):
        resolved = state_dir.resolve()
        if resolved == target or target in resolved.parents or resolved in target.parents:
            raise UnsafeIO("overlapping_state_directory")
    with open_directory(state_dir) as fd:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise UnsafeIO("unsafe_state_directory")
    git = reader.workflows()
    if git["state"] not in ("ok", "absent"):
        raise UnsafeIO("git_unknown")
    # Snapshot is evidence, not an authority for current live bytes.
    live_rows = snapshot["domains"]["workflows"]
    names = {row["dir_name"] for row in live_rows} | {row["dir_name"] for row in git["items"]}
    current_names, live_state = directory_names(live_root, Policy())
    if live_state not in ("ok", "absent"):
        raise UnsafeIO("live_unknown")
    names.update(current_names)
    if select is not None:
        requested = set(select)
        known_ids = {"grok-workflow:" + name for name in names}
        if not requested or not requested <= known_ids:
            raise ValueError("invalid_selection")
        names = {name for name in names if "grok-workflow:" + name in requested}
    if len(names) > 200:
        raise UnsafeIO("too_large")
    git_rows = {row["dir_name"]: row for row in git["items"]}
    items, diffs, baseline, candidate = [], [], {}, {}
    for name in sorted(names):
        if not SLUG.fullmatch(name) or Policy().denied_path(name) or contains_secret(name):
            raise UnsafeIO("denied")
        live = _live(live_root / name) if live_state == "ok" else None
        row = git_rows.get(name)
        tracked = None
        if row is not None:
            if row["tree_state"] != "observed":
                raise UnsafeIO("git_unknown")
            tracked = {}
            for file in row["files"]:
                body = reader.file("grok-workflows/skills-user/" + name + "/" + file["path"])
                if body is None or _sha(body) != file["sha256"]:
                    raise UnsafeIO("git_unknown")
                tracked[file["path"]] = body
        _safe_files(live)
        _safe_files(tracked)
        before, after = (tracked, live) if direction == "box-to-git" else (live, tracked)
        if before == after:
            continue
        op = "create" if before is None else "delete" if after is None else "update"
        if (op == "create" and not allow_create) or (op == "delete" and not allow_delete):
            raise UnsafeIO("operation_not_allowed")
        files = []
        for path in sorted(set(before or {}) | set(after or {})):
            old, new = (before or {}).get(path), (after or {}).get(path)
            baseline[name + "/" + path] = old if old is not None else b""
            candidate[name + "/" + path] = new if new is not None else b""
            if old is None:
                baseline.pop(name + "/" + path)
            if new is None:
                candidate.pop(name + "/" + path)
            if old == new:
                continue
            file_op = "create" if old is None else "delete" if new is None else "update"
            if (file_op == "create" and not allow_create) or (file_op == "delete" and not allow_delete):
                raise UnsafeIO("operation_not_allowed")
            files.append({"path": path, "op": file_op, "before_sha256": _sha(old),
                          "after_sha256": _sha(new), "after_bytes": None if new is None else len(new)})
            diffs.extend(difflib.unified_diff((old or b"").decode("utf-8", errors="replace").splitlines(True),
                         (new or b"").decode("utf-8", errors="replace").splitlines(True),
                         fromfile="before/" + name + "/" + path, tofile="after/" + name + "/" + path))
        item = {"target_id": "grok-workflow:" + name,
                "target_kind": "git_workflow" if direction == "box-to-git" else "box_workflow",
                "op": op, "writer": "git_local_commit" if direction == "box-to-git" else "boxsync_fs",
                # The approved schema requires null for creates in either direction.
                "expected_live_sha256": None if op == "create" or live is None else _tree(live),
                "expected_git_sha256": None if tracked is None else _tree(tracked),
                "after_sha256": None if after is None else _tree(after),
                "hash_algorithm": "boxsync-tree/v1", "files": files}
        if direction == "git-to-box" and not fingerprint_format_confirmed:
            if not isinstance(bot_id, str) or not UUID.fullmatch(bot_id):
                raise ValueError("bot_id_required")
            item.update(target_kind="grok_update_skill", writer="bot_turn", changeset={
                "tool": "UpdateSkill", "bot_id": bot_id,
                "source_ref": reader.commit() + ":grok-workflows/skills-user/" + name})
        items.append(item)
    action = "apply-workflows-to-git" if direction == "box-to-git" else "apply-workflows-to-box"
    if any(item["writer"] == "bot_turn" for item in items):
        action = "changeset-only"
    plan = {"schema": "box-config-plan/v1", "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl)).isoformat(), "action": action,
            "direction": "changeset-only" if action == "changeset-only" else direction,
            "domain": "workflows", "base": {"git_ref": reader.ref, "git_commit": reader.commit(),
                "snapshot_sha256": _sha(snapshot_bytes), "snapshot_collected_at": snapshot["collected_at"]},
            "flags": {"allow_create": allow_create, "allow_delete": allow_delete}, "items": items}
    # Identity is deterministic across equal explicit inputs, independent of output location.
    plan_id = str(uuid.uuid5(uuid.NAMESPACE_URL, _sha(canonical(plan))))
    plans = state_dir / "plans"
    _private_dir(plans)
    work = plans / plan_id
    existing = read_file(plans, plan_id + ".json", max_bytes=MAX_SNAPSHOT_BYTES)
    if existing.state == "observed":
        saved = parse_json(existing.data)
        validate_plan(saved)
        diff = read_file(plans, plan_id + ".diff", max_bytes=64 * 1024 * 1024)
        record = read_file(work, "last-gate.json", max_bytes=1024 * 1024)
        gate_record = parse_json(record.data)
        if (any(saved.get(key) != value for key, value in plan.items())
                or saved["plan_id"] != plan_id
                or saved.get("diff_path") != "plans/" + plan_id + ".diff"
                or saved["gate"].get("gate_record") != "plans/" + plan_id + "/last-gate.json"
                or diff.state != "observed" or diff.data != "".join(diffs).encode()
                or record.state != "observed" or gate_record.get("source") != "skill_guard"
                or gate_record.get("schema") != "skill-governance-last-gate/v1"
                or gate_record.get("result") != ("block" if saved["gate"]["state"] == "block" else "pass")
                or sorted(set(gate_record.get("signals", []))) != saved["gate"].get("signals")):
            raise UnsafeIO("artifact_conflict")
        return saved
    if existing.state != "absent":
        raise UnsafeIO("artifact_conflict")
    # Never rerun a gate in an existing artifact directory; refuse collisions safely.
    with open_directory(plans) as fd:
        os.mkdir(plan_id, 0o700, dir_fd=fd)
    gate = _gate(work, baseline, candidate)
    plan.update(plan_id=plan_id, gate={**gate, "gate_record": "plans/" + plan_id + "/last-gate.json"},
                diff_path="plans/" + plan_id + ".diff")
    plan["plan_sha256"] = plan_sha256(plan)
    validate_plan(plan)
    _write(plans / (plan_id + ".diff"), "".join(diffs).encode())
    _write(plans / (plan_id + ".json"), canonical(plan) + b"\n")
    return plan


def add_parser(subcommands):
    parser = subcommands.add_parser("plan-writeback", help="Generate a dry-run plan; never write targets")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=_REPO)
    parser.add_argument("--git-ref", default="origin/main")
    parser.add_argument("--domain", choices=("workflows",), default="workflows")
    parser.add_argument("--direction", choices=("box-to-git", "git-to-box"), required=True)
    parser.add_argument("--select", help="Comma-separated grok-workflow IDs")
    parser.add_argument("--allow-create", action="store_true")
    parser.add_argument("--allow-delete", action="store_true")
    parser.add_argument("--ttl", default="24h", help="Positive seconds, or an m/h/d suffix; at most 7d")
    parser.add_argument("--state-dir", type=Path, default=BOX_STATE_DIR)
    parser.add_argument("--fixture-home", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--format", choices=("json", "text"), default="json")


def command(args, parser):
    match = re.fullmatch(r"([1-9][0-9]*)([smhd]?)", args.ttl)
    if not match:
        parser.error("invalid --ttl")
    ttl = int(match[1]) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]
    if ttl > 7 * 86400:
        parser.error("--ttl exceeds 7d")
    try:
        raw = read_file(args.snapshot.parent, args.snapshot.name, max_bytes=MAX_SNAPSHOT_BYTES)
        if raw.state != "observed":
            raise UnsafeIO("snapshot_" + raw.state)
        snapshot = parse_json(raw.data)
        reader = GitReader(args.repo, args.git_ref)
        if not args.offline:
            reader.fetch()
        roots = fixture_roots(args.fixture_home) if args.fixture_home else BOX_ROOTS
        plan = plan_writeback(snapshot, reader, roots["workflows"], args.state_dir,
                              direction=args.direction, select=args.select.split(",") if args.select else None,
                              allow_create=args.allow_create, allow_delete=args.allow_delete, ttl=ttl,
                              snapshot_bytes=raw.data)
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True) if args.format == "json"
              else plan["plan_id"] + " " + plan["plan_sha256"] + " gate=" + plan["gate"]["state"])
        if plan["gate"]["state"] in ("block", "error"):
            return 4
        return 10 if plan["items"] else 0
    except (UnsafeIO, GitError, InvalidDocument, OSError, ValueError, TypeError):
        # Inputs, paths and exception text may contain secrets; never echo them.
        print(json.dumps({"state": "refused", "reason_code": "plan_refused"}))
        return 4
