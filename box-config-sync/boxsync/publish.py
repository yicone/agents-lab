"""Publish a validated snapshot onto the dedicated data branch (design §5.6.4).

Every refusal check runs before any write in the data worktree or any push.
All git invocations pass through run_git so tests can record argv.
"""
import hashlib
import json
import os
import stat
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .data_branch import AllowlistError, load_allowlist
from .gitio import GitError, GitReader, _ref
from .policy import Policy
from .safe_io import UnsafeIO, open_directory, read_file
from .secrets_scan import contains_secret
from .snapshot import MAX_SNAPSHOT_BYTES, private_directory, validate_snapshot
from .validation import InvalidDocument, parse_json

COMMIT_ENV = {"GIT_AUTHOR_NAME": "box-config-sync",
              "GIT_AUTHOR_EMAIL": "box-config-sync@localhost",
              "GIT_COMMITTER_NAME": "box-config-sync",
              "GIT_COMMITTER_EMAIL": "box-config-sync@localhost"}


class GitCommandError(Exception):
    def __init__(self, state, stderr=b""):
        super().__init__(state)
        self.state = state
        self.stderr = stderr


class PushFailed(Exception):
    pass


class _Refused(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def run_git(args, cwd=None, env=None, timeout=60, check=True):
    """Single seam for every git invocation in publish; tests wrap this."""
    cleaned = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    cleaned.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1")
    cleaned.update(env or {})
    argv = ["git", "-c", "core.hooksPath=/dev/null"]
    if cwd is not None:
        argv += ["-C", os.fspath(cwd)]
    argv += list(args)
    try:
        result = subprocess.run(argv, env=cleaned, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise GitCommandError("timeout") from None
    except OSError:
        raise GitCommandError("denied") from None
    if check and result.returncode:
        tail = result.stderr[-256:] if isinstance(result.stderr, bytes) else b""
        raise GitCommandError("error", tail)
    return result


def _tip(cwd, ref):
    result = run_git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                     cwd=cwd, check=False)
    value = result.stdout.decode("ascii", "replace").strip()
    return value if result.returncode == 0 and value else None


def _ancestor(cwd, left, right):
    return run_git(["merge-base", "--is-ancestor", left, right], cwd=cwd, check=False).returncode == 0


def _write_data_file(root, relative, data):
    """Atomic 0600 write inside the data worktree."""
    parts = PurePosixPath(relative).parts
    parent = Path(root, *parts[:-1])
    parent.mkdir(parents=True, exist_ok=True)
    name = parts[-1]
    with open_directory(parent) as fd:
        temporary = ".publish-" + uuid.uuid4().hex
        child = None
        try:
            child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600, dir_fd=fd)
            with os.fdopen(child, "wb") as stream:
                child = None
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            if child is not None:
                os.close(child)
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass


def _allowed(allowlist, raw):
    try:
        path = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return allowlist.matches(path)


def _gate_status(worktree, allowlist):
    """Every changed/untracked/staged path must be allowlisted (pre-staging)."""
    out = run_git(["status", "--porcelain=v1", "-z", "--untracked-files=all"],
                  cwd=worktree).stdout
    clean = True
    for token in out.split(b"\0"):
        if not token:
            continue
        raw = token[3:] if len(token) > 3 and token[2:3] == b" " else token
        if not _allowed(allowlist, raw):
            clean = False
    if not clean:
        # Unstage everything we may have staged; untracked files stay untouched.
        run_git(["reset", "-q"], cwd=worktree, check=False)
        raise _Refused("non_allowlisted_path")


def _gate_tree(worktree, allowlist):
    """Every path in the tree about to be pushed must be allowlisted."""
    if _tip(worktree, "HEAD") is None:
        return  # unborn orphan HEAD: only the gated index contents exist
    out = run_git(["ls-tree", "-r", "--name-only", "-z", "HEAD"], cwd=worktree).stdout
    if any(not _allowed(allowlist, token) for token in out.split(b"\0") if token):
        raise _Refused("non_allowlisted_path")


def _prepare_worktree(repo, worktree, remote, branch, tip):
    worktree = Path(worktree)
    if worktree.exists():
        if not (worktree / ".git").is_file():
            raise UnsafeIO("denied")  # not a git worktree we created; never touch
        head = _tip(worktree, "HEAD")
        if tip is None:
            return  # remote branch absent: keep local/orphan history
        if head is None:
            run_git(["checkout", "--detach", tip], cwd=worktree)
        elif _ancestor(worktree, "HEAD", tip):
            run_git(["merge", "--ff-only", tip], cwd=worktree)
        elif _ancestor(worktree, tip, "HEAD"):
            pass  # an earlier local commit was never pushed; fine to build on
        else:
            raise _Refused("data_branch_diverged")
        return
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if tip is not None:
        run_git(["worktree", "add", "--detach", os.fspath(worktree), remote + "/" + branch],
                cwd=repo)
        return
    if _tip(repo, "refs/heads/" + branch) is not None:
        run_git(["worktree", "add", os.fspath(worktree), branch], cwd=repo)
        return
    # No data branch yet: start an empty orphan history. checkout --orphan and
    # rm --ignore-unmatch work on git versions well below the installed one.
    run_git(["worktree", "add", "--detach", os.fspath(worktree), remote + "/main"], cwd=repo)
    run_git(["checkout", "--orphan", branch], cwd=worktree)
    run_git(["rm", "-rf", "--ignore-unmatch", "."], cwd=worktree)


def _attempt(worktree, remote, branch, relative, data, message, allowlist):
    """Write, gate, commit if changed, push. Returns (state, data_commit)."""
    _write_data_file(worktree, relative, data)
    _gate_status(worktree, allowlist)
    run_git(["add", "--", relative], cwd=worktree)
    # The tree we would push must be fully allowlisted even when the snapshot
    # content is unchanged and no commit/push happens.
    _gate_tree(worktree, allowlist)
    head = _tip(worktree, "HEAD")
    tip = _tip(worktree, remote + "/" + branch)
    changed = run_git(["diff", "--cached", "--quiet"], cwd=worktree, check=False).returncode != 0
    if not changed:
        if head is not None and head != tip:
            _push(worktree, remote, branch)  # deliver an earlier unpushed commit
            return "pushed", head
        return "unchanged", head
    run_git(["commit", "-q", "-m", message], cwd=worktree, env=COMMIT_ENV)
    _push(worktree, remote, branch)
    return "pushed", _tip(worktree, "HEAD")


def _push(worktree, remote, branch):
    try:
        run_git(["push", remote, "HEAD:refs/heads/" + branch], cwd=worktree)
    except GitCommandError:
        raise PushFailed() from None


def _audit(state_dir, record):
    record = {**record, "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    path = Path(state_dir) / "audit.jsonl"
    private_directory(state_dir, create=True)
    try:
        info = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        info = None
    if info is not None and (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                             or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise UnsafeIO("denied")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _rescan_secret(doc, text):
    """Re-scan the serialized bytes; the memory-id exemption mirrors validate."""
    memory = [{**row, "id": ""} for row in doc["domains"].get("memory", [])]
    scan = {**doc, "domains": {**doc["domains"], "memory": memory}}
    return contains_secret(json.dumps(scan, ensure_ascii=False, sort_keys=True, allow_nan=False))


def _publish(repo, remote, branch, snapshot_path, worktree, record):
    """Returns (payload, exit_code, audit_result, reason_code)."""
    def refused(reason, code=4, state="refused"):
        return {"state": state, "reason_code": reason, "box_id": record["box_id"]}, code, "refused", reason

    try:
        run_git(["fetch", "--no-tags", "--no-write-fetch-head", "--", remote, "main"], cwd=repo)
    except GitCommandError:
        return ({"state": "fetch_failed", "reason_code": "fetch_failed"}, 3,
                "fetch_failed", "fetch_failed")
    try:
        allowlist = load_allowlist(GitReader(repo, remote + "/main"))
    except (AllowlistError, GitError):
        return refused("allowlist_unavailable")
    if branch != allowlist.branch:
        return refused("branch_not_allowed")
    if Policy().denied_path(snapshot_path):
        return refused("blacklisted_input")
    result = read_file(snapshot_path.parent, snapshot_path.name, max_bytes=MAX_SNAPSHOT_BYTES)
    if result.state != "observed":
        return refused("snapshot_unavailable")
    try:
        doc = parse_json(result.data)
        text = validate_snapshot(doc)
    except UnsafeIO:
        return refused("snapshot_secret")
    except (InvalidDocument, ValueError, UnicodeError):
        return refused("invalid_snapshot")
    record["box_id"] = doc["box_id"]
    data = (text + "\n").encode("utf-8")
    record["snapshot_sha256"] = hashlib.sha256(data).hexdigest()
    entry = next((e for e in allowlist.entries if e["content_schema"] == doc["schema"]), None)
    if entry is None:
        return refused("invalid_snapshot")
    if len(data) > entry["max_bytes"]:
        return refused("too_large")
    relative = "box-snapshots/%s/snapshot.json" % doc["box_id"]
    if not entry["regex"].fullmatch(relative):
        return refused("path_not_allowed")
    if _rescan_secret(doc, text):
        return refused("snapshot_secret")

    try:
        run_git(["fetch", "--no-tags", "--no-write-fetch-head", "--", remote, branch], cwd=repo)
    except GitCommandError:
        pass  # the first publish creates the branch
    tip = _tip(repo, remote + "/" + branch)
    try:
        _prepare_worktree(repo, worktree, remote, branch, tip)
        tool = doc.get("tool") or {}
        message = "box-snapshot %s %s tool=%s" % (
            doc["box_id"], doc["collected_at"], tool.get("commit") or "unknown")
        try:
            state, data_commit = _attempt(worktree, remote, branch, relative, data,
                                        message, allowlist)
        except PushFailed:
            # One retry: re-fetch, reset the dedicated worktree to the new remote
            # tip, rewrite, recommit, push once more. No loops.
            run_git(["fetch", "--no-tags", "--no-write-fetch-head", "--", remote, branch],
                    cwd=repo)
            tip = _tip(repo, remote + "/" + branch)
            if tip is None:
                raise GitCommandError("error")
            run_git(["reset", "--hard", tip], cwd=worktree)
            state, data_commit = _attempt(worktree, remote, branch, relative, data,
                                        message, allowlist)
    except _Refused as exc:
        return refused(exc.reason)
    except PushFailed:
        return ({"state": "push_failed", "reason_code": "push_failed",
                 "box_id": record["box_id"]}, 3, "push_failed", "push_failed")
    record["data_commit"] = data_commit
    return ({"state": state, "box_id": record["box_id"], "data_commit": data_commit},
            0, state, None)


def command(args, parser):
    from .__main__ import check_state_dir
    try:
        remote, branch = _ref(args.remote), _ref(args.branch)
    except ValueError:
        parser.error("invalid --remote or --branch")
    state_dir = args.state_dir
    snapshot_path = args.snapshot or state_dir / "snapshots/latest.json"
    worktree = args.worktree or state_dir / "data-worktree"
    if not check_state_dir(state_dir):
        payload = {"state": "denied", "reason_code": "unsafe_state_directory"}
        print(json.dumps(payload, sort_keys=True))
        return 4
    record = {"schema": "box-config-audit/v1", "action": "push-snapshot", "approve": "ACM-910",
              "result": None, "reason_code": None, "box_id": None,
              "data_commit": None, "snapshot_sha256": None}
    try:
        payload, code, result, reason = _publish(args.repo, remote, branch,
                                                 snapshot_path, worktree, record)
    except UnsafeIO as exc:
        payload, code, result, reason = ({"state": "denied", "reason_code": "unsafe_io"}, 4,
                                         "refused", "unsafe_io")
    except GitCommandError as exc:
        payload, code, result, reason = ({"state": "error", "reason_code": "git_" + exc.state}, 6,
                                         "error", "git_" + exc.state)
    except OSError:
        payload, code, result, reason = ({"state": "error", "reason_code": "io_error"}, 6,
                                         "error", "io_error")
    record.update(result=result, reason_code=reason)
    try:
        _audit(state_dir, record)
    except (UnsafeIO, OSError):
        payload, code = {"state": "error", "reason_code": "audit_failed"}, 6
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(" ".join("%s=%s" % (key, value) for key, value in sorted(payload.items())))
    return code
