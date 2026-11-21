"""Bounded reads from a pinned Git commit; no checkout, index or worktree writes.

fetch is explicit and never part of workflow reads. Callers can remain offline.
Missing paths are established by a successful tree listing, never by interpreting
stderr from a failed show/cat-file as proof of absence.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

from .domains.workflows import frontmatter
from .policy import MAX_FILE_BYTES, MAX_TREE_ENTRIES, Policy
from .instructions import instructions_sha256

ALLOWED_COMMANDS = frozenset(("fetch", "show", "cat-file", "rev-parse", "ls-tree"))
MAX_OUTPUT = 4 * 1024 * 1024
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,255}\Z")


class GitError(Exception):
    def __init__(self, state):
        self.state = state
        super().__init__(state)


def _ref(value):
    if (not isinstance(value, str) or not _REF.fullmatch(value)
            or ".." in value or "//" in value or value.endswith("/")):
        raise ValueError("invalid Git ref")
    return value


class GitReader:
    # Read-only verbs; subclasses may widen (never to write commands).
    ALLOWED_COMMANDS = ALLOWED_COMMANDS

    def __init__(self, repo, ref="origin/main", *, timeout=15):
        self.repo = Path(repo).absolute()
        self.ref = _ref(ref)
        self.timeout = timeout
        self._commit = None

    def _run(self, command, *args, limit=MAX_OUTPUT):
        if command not in self.ALLOWED_COMMANDS:
            raise ValueError("Git command is not read-only")
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1")
        # Disk-backed output keeps malformed/large repositories out of memory.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                result = subprocess.run(
                    ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                     "-c", "protocol.ext.allow=never", "-C", str(self.repo), command, *args],
                    env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                    timeout=self.timeout, check=False)
            except subprocess.TimeoutExpired:
                raise GitError("timeout") from None
            except OSError:
                raise GitError("denied") from None
            if result.returncode:
                raise GitError("error")
            if output.tell() > limit:
                raise GitError("too_large")
            output.seek(0)
            return output.read(limit + 1)

    def fetch(self, remote="origin", ref="main"):
        """Explicit opt-in; may update remote refs, never the working tree."""
        self._run("fetch", "--no-tags", "--no-write-fetch-head", "--", _ref(remote), _ref(ref))
        self._commit = None

    def commit(self):
        if self._commit is not None:
            return self._commit
        value = self._run("rev-parse", "--verify", self.ref + "^{commit}", limit=128).strip().decode("ascii")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
            raise GitError("error")
        self._commit = value
        return value

    def _file(self, commit, path):
        """None only after a successful exact tree lookup proves absence."""
        if (not isinstance(path, str) or path.startswith("/")
                or any(part in ("", ".", "..") for part in path.split("/"))
                or Policy().denied_path(path)):
            raise GitError("denied")
        data = self._run("ls-tree", "-z", "-l", commit, "--", ":(literal)" + path)
        if not data:
            return None
        records = data.rstrip(b"\0").split(b"\0")
        if len(records) != 1:
            raise GitError("error")
        meta, name = records[0].split(b"\t", 1)
        mode, kind, oid, size = meta.split()
        if name.decode("utf-8") != path or kind != b"blob" or mode not in (b"100644", b"100755"):
            raise GitError("unsupported_entry")
        return self._blob(oid.decode("ascii"))

    def file(self, path):
        """Blob bytes at path on the pinned commit; None only when the exact
        tree lookup proves absence."""
        return self._file(self.commit(), path)

    def instructions(self):
        """Hash tracked sources/index.json bodies at the same pinned commit."""
        result = {"state": "ok", "agents": [], "bots": []}
        try:
            commit = self.commit()
            data = self._file(commit, "instructions-governance/sources/index.json")
            if data is None:
                return {**result, "state": "absent"}
            index = json.loads(data)
            if not isinstance(index, dict):
                raise GitError("error")
            for kind in ("agents", "bots"):
                items = index.get(kind, [])
                if not isinstance(items, list) or len(items) > MAX_TREE_ENTRIES:
                    raise GitError("error")
                seen = set()
                for item in items:
                    if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                            or not item["id"] or kind == "agents" and
                            (not isinstance(item.get("workspace_id"), str) or not item["workspace_id"])):
                        raise GitError("error")
                    identity = (item.get("workspace_id") if kind == "agents" else None, item["id"])
                    if identity in seen:
                        raise GitError("error")
                    seen.add(identity)
                    row = {key: item.get(key) for key in ("id", "workspace_id", "path", "withheld")}
                    if row["path"] is not None and not isinstance(row["path"], str):
                        raise GitError("error")
                    row["sha256"] = None
                    if not row["withheld"] and row["path"]:
                        try:
                            path = item.get("path")
                            if not isinstance(path, str) or not path:
                                raise GitError("error")
                            body = self._file(commit, "instructions-governance/sources/" + path)
                            if body is None:
                                raise GitError("absent")
                            row["sha256"] = instructions_sha256(body.decode("utf-8"))
                        except (GitError, UnicodeError):
                            result["state"] = "partial"
                    result[kind].append(row)
            return result
        except (GitError, ValueError, UnicodeError, TypeError) as exc:
            # Preserve successfully identified rows but never treat failure as absence.
            return {**result, "state": exc.state if isinstance(exc, GitError) else "error"}

    def _blob(self, oid):
        size = int(self._run("cat-file", "-s", oid, limit=32).strip())
        if size > MAX_FILE_BYTES:
            raise GitError("too_large")
        return self._run("cat-file", "blob", oid, limit=MAX_FILE_BYTES)

    def workflows(self):
        """Read actual tracked bytes; do not trust hashes from a manifest."""
        try:
            commit = self.commit()
            data = self._run("ls-tree", "-r", "-z", "-l", commit, "--", "grok-workflows/skills-user")
            grouped = {}
            policy = Policy()
            for record in data.split(b"\0"):
                if not record:
                    continue
                meta, raw_path = record.split(b"\t", 1)
                mode, kind, oid, size = meta.split()
                path = raw_path.decode("utf-8")
                parts = PurePosixPath(path).parts
                if len(parts) < 4 or parts[:2] != ("grok-workflows", "skills-user"):
                    raise GitError("unsupported_entry")
                directory = parts[2]
                relative = "/".join(parts[3:])
                if policy.denied_path("/".join(parts[:3])):
                    raise GitError("denied")
                grouped.setdefault(directory, [])
                if policy.denied_path(path) or parts[-1] == ".DS_Store":
                    continue
                grouped[directory].append((relative, mode, kind, oid, size))
            rows = []
            for directory, entries in sorted(grouped.items()):
                row = {"id": "grok-workflow:" + directory, "dir_name": directory,
                       "name": None, "body_hash_state": "uncollected", "body_sha256": None,
                       "tree_state": "observed", "tree_sha256": None, "files": []}
                try:
                    if len(entries) > MAX_TREE_ENTRIES:
                        raise GitError("too_large")
                    for path, mode, kind, oid, size in entries:
                        if kind != b"blob" or mode not in (b"100644", b"100755"):
                            raise GitError("unsupported_entry")
                        if int(size) > MAX_FILE_BYTES:
                            raise GitError("too_large")
                        body = self._blob(oid.decode("ascii"))
                        digest = hashlib.sha256(body).hexdigest()
                        row["files"].append({"path": path, "sha256": digest, "bytes": len(body), "state": "observed"})
                        if path == "SKILL.md":
                            row["name"], row["flags"] = frontmatter(body)
                            row.update(body_sha256=digest, body_hash_state="observed")
                    payload = "".join(f'{f["path"]}\0{f["sha256"]}\0{f["bytes"]}\n'
                                      for f in sorted(row["files"], key=lambda f: f["path"].encode("utf-8")))
                    row["tree_sha256"] = hashlib.sha256(payload.encode()).hexdigest()
                except GitError as exc:
                    row.update(tree_state=exc.state, tree_sha256=None)
                rows.append(row)
            return {"state": "ok", "items": rows, "commit": commit}
        except (GitError, ValueError, UnicodeError) as exc:
            return {"state": exc.state if isinstance(exc, GitError) else "error", "items": []}
