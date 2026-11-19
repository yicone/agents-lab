"""Consume the data branch on Mini: verify, then install a private copy.

Read-only over git: fetch plus object-database reads; never checkout, reset,
merge or commit. Every commit inside the allowlist ``history_days`` window is
verified in topological order — merge commits, non-allowlisted paths and bad
blob content are rejected even when the tip itself is clean. Commits older
than the window are not interpreted (design §5.6.2 retention trust window).
The full ``verified_commit..tip`` id list (all of tip's history on a first
fetch) is enumerated and date-filtered locally — never truncated by
``--max-age``, which would stop traversal at an old-dated intermediate
commit and let an in-window bad ancestor slip through unverified.
Any verification failure leaves the local copy untouched and records a
rejected fetch-status with a machine reason_code (design §5.6.6).

When more than MAX_COMMITS new commits are pending, the oldest MAX_COMMITS
are content-verified, ``verified_commit`` advances to the last one checked,
and the run ends ``catching_up`` (exit 3) so the next run continues where
this one stopped. MAX_COMMITS bounds only per-run content verification,
never the id enumeration. A long healthy history with few new commits ends
``ok``.

On success the run writes, in order: the snapshot copy, ``git-leg.json``
(the Mini's last observed ``origin/main`` workflows leg for the board),
then ``fetch-status.json`` last. Fetch writes no append-only audit; publish
owns ``audit.jsonl``.
"""
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .data_branch import AllowlistError, load_allowlist
from .gitio import GitError, GitReader, _ref
from .safe_io import UnsafeIO, open_directory, read_file
from .snapshot import private_directory, validate_snapshot, write_snapshot
from .validation import InvalidDocument, parse_json

STATUS_SCHEMA = "box-config-fetch-status/v1"
GIT_LEG_SCHEMA = "box-config-git-leg/v1"
GIT_LEG_MAX_BYTES = 4 * 1024 * 1024
MAX_COMMITS = 500
_BOX_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SNAPSHOT_PATH = re.compile(r"^box-snapshots/([A-Za-z0-9][A-Za-z0-9._-]{0,63})/snapshot\.json$")
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}\Z")


class DataBranchReader(GitReader):
    """Read-only verbs needed to verify history; no write commands, ever."""
    ALLOWED_COMMANDS = frozenset(("fetch", "show", "cat-file", "rev-parse",
                                  "ls-tree", "rev-list", "diff-tree"))


def _now():
    """Clock seam: tests patch this to control the trust window."""
    return datetime.now(timezone.utc)


def _read_status(state_dir):
    result = read_file(state_dir, "fetch-status.json", max_bytes=64 * 1024)
    if result.state != "observed":
        return {}
    try:
        doc = parse_json(result.data)
    except InvalidDocument:
        return {}
    return doc if isinstance(doc, dict) else {}


def _write_private_json(state_dir, name, doc):
    """Atomic 0600 JSON document inside the private state directory."""
    text = json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n"
    with open_directory(state_dir) as fd:
        temporary = ".fetch-private-" + uuid.uuid4().hex
        child = None
        try:
            child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600, dir_fd=fd)
            with os.fdopen(child, "w", encoding="utf-8") as stream:
                child = None
                stream.write(text)
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


def _emit(args, payload):
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(" ".join("%s=%s" % (key, value) for key, value in sorted(payload.items())))


class _Reject(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _verify_snapshot(allowlist, reader, path, oid, size):
    """One blob must parse, carry the entry's schema, validate, and match its box."""
    if not isinstance(path, str):
        raise _Reject("non_allowlisted_path")
    entry = next((e for e in allowlist.entries if e["regex"].fullmatch(path)), None)
    if entry is None:
        raise _Reject("non_allowlisted_path")
    if size > entry["max_bytes"]:
        raise _Reject("too_large")
    data = reader._run("cat-file", "blob", oid.decode("ascii"), limit=entry["max_bytes"])
    try:
        doc = parse_json(data)
    except InvalidDocument:
        raise _Reject("invalid_snapshot") from None
    if not isinstance(doc, dict) or doc.get("schema") != entry["content_schema"]:
        raise _Reject("schema_mismatch")
    try:
        validate_snapshot(doc)
    except UnsafeIO:
        raise _Reject("snapshot_secret") from None
    except (InvalidDocument, ValueError, UnicodeError):
        raise _Reject("invalid_snapshot") from None
    box = _SNAPSHOT_PATH.fullmatch(path)
    if box is not None and doc.get("box_id") != box.group(1):
        raise _Reject("box_id_mismatch")
    return doc


def _verify_commit(allowlist, reader, sha, verified_blobs):
    """diff-tree --raw -z emits ``:meta\\0path\\0`` pairs; verify each entry.

    ``verified_blobs`` caches (path, oid) pairs already checked this run —
    identical blob content at the same path is verified once, not per commit.
    """
    out = reader._run("diff-tree", "--root", "-r", "-z", "--no-commit-id", "--raw", sha)
    tokens = [token for token in out.split(b"\0") if token]
    index = 0
    while index < len(tokens):
        fields = tokens[index].split()
        index += 1
        if len(fields) != 5 or not fields[0].startswith(b":") or index > len(tokens):
            raise _Reject("git_unreadable")
        _old_mode, new_mode, _old_oid, new_oid, status = fields
        kind = status[:1]
        # Rename/copy entries carry source and destination paths.
        paths = 2 if kind in (b"R", b"C") else 1
        if index + paths > len(tokens):
            raise _Reject("git_unreadable")
        raw = tokens[index + paths - 1]
        index += paths
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise _Reject("non_allowlisted_path") from None
        if not allowlist.matches(path):
            raise _Reject("non_allowlisted_path")
        if kind == b"D":
            continue
        if kind not in (b"A", b"M", b"T", b"C", b"R"):
            raise _Reject("git_unreadable")
        if new_mode not in (b"100644", b"100755"):
            raise _Reject("snapshot_not_regular")
        key = (path, new_oid)
        if key in verified_blobs:
            continue
        size = int(reader._run("cat-file", "-s", new_oid.decode("ascii"), limit=32).strip())
        _verify_snapshot(allowlist, reader, path, new_oid, size)
        verified_blobs.add(key)


def _pending(reader, cutoff, tip, verified):
    """In-window commits still to verify, oldest first. Topo order required.

    Enumerate the whole ``verified..tip`` range (or tip's full history on a
    first fetch) and date-filter in Python. ``rev-list --max-age`` would
    stop traversal at the first out-of-window commit and silently skip any
    in-window ancestor behind it — enumeration must not be truncated.
    Listing ids is cheap; MAX_COMMITS bounds only content verification.
    """
    args = ["rev-list", "--timestamp", "--parents", "--topo-order", "--reverse"]
    if verified is not None:
        args += ["^" + verified, tip]
    else:
        args += [tip]
    out = reader._run(*args)
    commits = []
    for line in out.decode("utf-8").splitlines():
        parts = line.split()
        # --timestamp lines are "<committer-ts> <sha> <parents...>".
        if parts and len(parts) >= 2 and int(parts[0]) >= cutoff:
            commits.append((parts[1], parts[2:]))
    return commits


def _git_leg(repo, remote, observed_at):
    """The board's frozen git leg: workflows() at remote/main, read-only."""
    doc = {"schema": GIT_LEG_SCHEMA, "ref": remote + "/main", "commit": None,
           "observed_at": observed_at, "state": "error", "items": []}
    try:
        leg = DataBranchReader(repo, remote + "/main").workflows()
        doc["commit"] = leg.get("commit")
        doc["state"] = leg.get("state", "error")
        doc["items"] = leg.get("items", [])
    except GitError as exc:
        doc["state"] = exc.state
    except Exception:
        doc["state"] = "error"
    text = json.dumps(doc, ensure_ascii=False, sort_keys=True)
    if len(text.encode("utf-8")) > GIT_LEG_MAX_BYTES:
        doc["items"] = []
        doc["state"] = "too_large"
    return doc


def command(args, parser):
    try:
        remote, branch = _ref(args.remote), _ref(args.branch)
    except ValueError:
        parser.error("invalid --remote or --branch")
    if not isinstance(args.box_id, str) or not _BOX_ID.fullmatch(args.box_id):
        parser.error("invalid --box-id")
    state_dir = args.state_dir
    try:
        private_directory(state_dir, create=True)
    except UnsafeIO:
        _emit(args, {"state": "denied", "reason_code": "unsafe_state_directory"})
        return 4
    previous = _read_status(state_dir)
    last = previous.get("verified_commit")
    verified = last if isinstance(last, str) and _SHA.fullmatch(last) else None
    now = _now()
    checked_at = now.isoformat().replace("+00:00", "Z")

    def save(state, reason=None, tip=None, collected_at=None, verified_now=None):
        doc = {"schema": STATUS_SCHEMA, "state": state, "checked_at": checked_at,
               "verified_commit": tip if state == "ok" else
               verified_now if verified_now is not None else verified,
               "box_id": args.box_id,
               "collected_at": collected_at if state == "ok" else previous.get("collected_at")}
        if reason:
            doc["reason_code"] = reason
        _write_private_json(state_dir, "fetch-status.json", doc)

    def fail(code, state, reason=None, **extra):
        save(state, reason, **{k: v for k, v in extra.items() if k == "verified_now"})
        payload = {"state": state, "box_id": args.box_id}
        if reason:
            payload["reason_code"] = reason
        _emit(args, payload)
        return code

    reader = DataBranchReader(args.repo, remote + "/" + branch)
    if not args.offline:
        try:
            reader.fetch(remote, "main")
        except GitError:
            return fail(3, "fetch_failed")
        try:
            reader.fetch(remote, branch)
        except GitError:
            try:
                reader.commit()
            except GitError:
                return fail(3, "absent")
            return fail(3, "fetch_failed")
    try:
        tip = reader.commit()
    except GitError:
        return fail(3, "absent")

    def reject(reason):
        return fail(4, "rejected", reason)

    try:
        allowlist = load_allowlist(DataBranchReader(args.repo, remote + "/main"))
    except (AllowlistError, GitError):
        return reject("allowlist_unavailable")
    if branch != allowlist.branch:
        return reject("branch_not_allowed")
    relative = "box-snapshots/%s/snapshot.json" % args.box_id
    entry = next((e for e in allowlist.entries if e["regex"].fullmatch(relative)), None)
    if entry is None:
        return reject("path_not_allowed")
    cutoff = int(now.timestamp()) - entry["history_days"] * 86400
    try:
        # The tip itself must sit inside the trust window.
        if not reader._run("rev-list", "--max-count=1", "--max-age=%d" % cutoff, tip).strip():
            return reject("tip_outside_history_window")
        if verified is not None:
            try:
                reader._run("rev-parse", "--verify", verified + "^{commit}", limit=128)
            except GitError:
                return reject("history_rewritten")
            if reader._run("rev-list", "--max-count=1", verified, "^" + tip).strip():
                return reject("history_rewritten")
        pending = _pending(reader, cutoff, tip, verified)
        pending = [commit for commit in pending if commit[0] != verified]
        batch, overflow = pending[:MAX_COMMITS], pending[MAX_COMMITS:]
        if any(len(parents) > 1 for _sha, parents in batch):
            return reject("merge_commit")
        verified_blobs = set()
        for sha, _parents in batch:
            _verify_commit(allowlist, reader, sha, verified_blobs)
        if overflow:
            # Bounded work: advance verified_commit, finish next run.
            save("catching_up", "pending_commits", verified_now=batch[-1][0])
            _emit(args, {"state": "catching_up", "box_id": args.box_id,
                         "reason_code": "pending_commits",
                         "verified_commit": batch[-1][0],
                         "pending": len(overflow)})
            return 3
        tree = reader._run("ls-tree", "-r", "-z", "-l", tip)
        blob = None
        for record in tree.split(b"\0"):
            if not record:
                continue
            meta, raw = record.split(b"\t", 1)
            mode, kind, oid, size = meta.split()
            try:
                path = raw.decode("utf-8")
            except UnicodeDecodeError:
                return reject("non_allowlisted_path")
            if not allowlist.matches(path):
                return reject("non_allowlisted_path")
            if path == relative:
                if kind != b"blob" or mode not in (b"100644", b"100755"):
                    return reject("snapshot_not_regular")
                blob = (oid, int(size))
        if blob is None:
            return reject("snapshot_absent")
        doc = _verify_snapshot(allowlist, reader, relative, blob[0], blob[1])
        if doc.get("box_id") != args.box_id:
            return reject("box_id_mismatch")
    except _Reject as exc:
        return reject(exc.reason)
    except GitError:
        return reject("git_unreadable")
    target_dir = Path(state_dir) / "box-snapshots" / args.box_id
    try:
        private_directory(target_dir, create=True)
        write_snapshot(doc, target_dir / "snapshot.json")
        _write_private_json(state_dir, "git-leg.json",
                            _git_leg(args.repo, remote, checked_at))
    except (UnsafeIO, OSError):
        return fail(6, "write_failed")
    save("ok", tip=tip, collected_at=doc.get("collected_at"))
    _emit(args, {"state": "ok", "box_id": args.box_id,
                 "collected_at": doc.get("collected_at"), "commit": tip})
    return 0
