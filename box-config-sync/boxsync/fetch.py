"""Consume the data branch on Mini: verify, then install a private copy.

Read-only over git: fetch plus object-database reads; never checkout, reset,
merge or commit. Any verification failure leaves the local copy untouched and
records a rejected fetch-status with a machine reason_code (design §5.6.6).
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
MAX_COMMITS = 500
_BOX_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}\Z")


class DataBranchReader(GitReader):
    """Read-only verbs needed to verify history; no write commands, ever."""
    ALLOWED_COMMANDS = frozenset(("fetch", "show", "cat-file", "rev-parse",
                                  "ls-tree", "rev-list", "diff-tree"))


def _read_status(state_dir):
    result = read_file(state_dir, "fetch-status.json", max_bytes=64 * 1024)
    if result.state != "observed":
        return {}
    try:
        doc = parse_json(result.data)
    except InvalidDocument:
        return {}
    return doc if isinstance(doc, dict) else {}


def _write_status(state_dir, doc):
    text = json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n"
    with open_directory(state_dir) as fd:
        temporary = ".fetch-status-" + uuid.uuid4().hex
        child = None
        try:
            child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600, dir_fd=fd)
            with os.fdopen(child, "w", encoding="utf-8") as stream:
                child = None
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, "fetch-status.json", src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            if child is not None:
                os.close(child)
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass


def _parents(reader, *revs):
    out = reader._run("rev-list", "--parents", "--max-count=%d" % (MAX_COMMITS + 1), *revs)
    commits = {}
    for line in out.decode("utf-8").splitlines():
        parts = line.split()
        if parts:
            commits[parts[0]] = parts[1:]
    return commits


def _emit(args, payload):
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(" ".join("%s=%s" % (key, value) for key, value in sorted(payload.items())))


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
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def save(state, reason=None, tip=None, collected_at=None):
        doc = {"schema": STATUS_SCHEMA, "state": state, "checked_at": now,
               "verified_commit": tip if state == "ok" else verified,
               "box_id": args.box_id,
               "collected_at": collected_at if state == "ok" else previous.get("collected_at")}
        if reason:
            doc["reason_code"] = reason
        _write_status(state_dir, doc)

    def fail(code, state, reason=None):
        save(state, reason)
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
    try:
        commits = _parents(reader, tip)
        if len(commits) > MAX_COMMITS:
            return reject("too_many_commits")
        if tip not in commits:
            return reject("history_unreadable")
        if verified is not None and verified not in commits:
            return reject("history_rewritten")
        new = _parents(reader, verified + ".." + tip) if verified is not None else commits
        if len(new) > MAX_COMMITS:
            return reject("too_many_commits")
        for sha, parents in new.items():
            if len(parents) > 1:
                return reject("merge_commit")
            out = reader._run("diff-tree", "--root", "-r", "--name-only", "-z",
                              "--no-commit-id", sha)
            for raw in out.split(b"\0"):
                if not raw:
                    continue
                try:
                    path = raw.decode("utf-8")
                except UnicodeDecodeError:
                    return reject("non_allowlisted_path")
                if not allowlist.matches(path):
                    return reject("non_allowlisted_path")
        relative = "box-snapshots/%s/snapshot.json" % args.box_id
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
        oid, size = blob
        entry = next(e for e in allowlist.entries if e["regex"].fullmatch(relative))
        if size > entry["max_bytes"]:
            return reject("too_large")
        data = reader._run("cat-file", "blob", oid.decode("ascii"), limit=entry["max_bytes"])
        try:
            doc = parse_json(data)
        except InvalidDocument:
            return reject("invalid_snapshot")
        if not isinstance(doc, dict) or doc.get("schema") != entry["content_schema"]:
            return reject("schema_mismatch")
        try:
            validate_snapshot(doc)
        except UnsafeIO:
            return reject("snapshot_secret")
        except (InvalidDocument, ValueError, UnicodeError):
            return reject("invalid_snapshot")
        if doc.get("box_id") != args.box_id:
            return reject("box_id_mismatch")
    except GitError:
        return reject("git_unreadable")
    target_dir = Path(state_dir) / "box-snapshots" / args.box_id
    try:
        private_directory(target_dir, create=True)
        write_snapshot(doc, target_dir / "snapshot.json")
    except (UnsafeIO, OSError):
        return fail(6, "write_failed")
    save("ok", tip=tip, collected_at=doc.get("collected_at"))
    _emit(args, {"state": "ok", "box_id": args.box_id,
                 "collected_at": doc.get("collected_at"), "commit": tip})
    return 0
