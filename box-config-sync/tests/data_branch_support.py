"""Local git fixtures for data-branch tests: bare origin + clones.

Everything is created under a tempdir; nothing touches the network or the
real repository's remotes.
"""
import json
import os
import subprocess
from pathlib import Path

from support import PROJECT

ALLOWLIST_REL = "box-config-sync/data-branch-allowlist.json"
SNAPSHOT_REL = "box-snapshots/grok-box/snapshot.json"


def git(cwd, *args, check=True, env=None):
    run_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    run_env.update(GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                   GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    run_env.update(env or {})
    return subprocess.run(["git", "-C", os.fspath(cwd), *args], env=run_env,
                          check=check, capture_output=True)


def allowlist():
    return json.loads((PROJECT / "data-branch-allowlist.json").read_text(encoding="utf-8"))


def write_allowlist(clone, doc):
    path = Path(clone) / ALLOWLIST_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def make_repos(tmp, workflows={"example": {"SKILL.md": "---\nname: Example\n---\nfixture\n"}}):
    """Return (bare, clone, mini_clone, pusher) with allowlist + workflows on main."""
    tmp = Path(tmp)
    bare, clone, mini, pusher = (tmp / name for name in
                                 ("origin.git", "clone", "mini-clone", "pusher"))
    git(tmp, "init", "-q", "-b", "main", "--bare", os.fspath(bare))
    git(tmp, "clone", "-q", os.fspath(bare), os.fspath(clone))
    write_allowlist(clone, allowlist())
    for directory, files in workflows.items():
        root = clone / "grok-workflows" / "skills-user" / directory
        for relative, body in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
    git(clone, "add", "-A")
    git(clone, "commit", "-qm", "Create fixture main.")
    git(clone, "push", "-q", "origin", "main")
    for name in (mini, pusher):
        git(tmp, "clone", "-q", os.fspath(bare), os.fspath(name))
    return bare, clone, mini, pusher


def push_data_commit(pusher, files, message="fixture data commit", parents=(),
                     push=True, dates=None):
    """Commit files directly with plumbing and push to box-snapshots.

    ``parents`` gives the commit parents (zero for a root, two for a merge);
    ``push=False`` only builds the commit for reset_data_branch surgery;
    ``dates`` sets GIT_AUTHOR_DATE/GIT_COMMITTER_DATE (e.g. "200 days ago").
    """
    git(pusher, "checkout", "-q", "--orphan", "fixture-snapshots")
    git(pusher, "rm", "-rf", "--ignore-unmatch", ".")
    for relative, body in files.items():
        path = Path(pusher) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, (bytes, bytearray)):
            path.write_bytes(bytes(body))
        else:
            path.write_text(body, encoding="utf-8")
    git(pusher, "add", "-A")
    tree = git(pusher, "write-tree").stdout.decode().strip()
    args = ["commit-tree", tree]
    for parent in parents:
        args += ["-p", parent]
    args += ["-m", message]
    env = None
    if dates:
        env = {"GIT_AUTHOR_DATE": dates, "GIT_COMMITTER_DATE": dates}
    sha = git(pusher, *args, env=env).stdout.decode().strip()
    if push:
        git(pusher, "push", "-q", "origin", sha + ":refs/heads/box-snapshots")
    # commit-tree never materialized the orphan branch ref; force-switch back.
    git(pusher, "checkout", "-qf", "main")
    git(pusher, "clean", "-fdq")
    return sha


def push_data_fast(pusher, contents, first_epoch=None, parent=None):
    """Build len(contents) linear commits on box-snapshots via fast-import.

    ``contents`` is a list of file-dicts like push_data_commit's ``files``.
    Committer dates default to now so commits sit inside the trust window;
    ``first_epoch`` pins the first commit's timestamp (later commits +1s).
    ``parent`` chains onto an existing tip sha. The new tip is pushed to
    origin and returned.
    """
    epoch = first_epoch if first_epoch is not None else int(__import__("time").time())
    stream, mark, commit_mark = [], 0, 0
    for index, files in enumerate(contents):
        entries = []
        for relative, body in sorted(files.items()):
            mark += 1
            data = body if isinstance(body, (bytes, bytearray)) else body.encode("utf-8")
            stream.append(b"blob\nmark :%d\ndata %d\n" % (mark, len(data)) + data + b"\n")
            entries.append(b"M 100644 :%d %s\n" % (mark, relative.encode()))
        mark += 1
        commit_mark = mark
        when = b"%d +0000" % (epoch + index)
        message = b"commit %d" % index
        stream.append(b"commit refs/heads/box-snapshots\nmark :%d\n"
                      b"author Fixture <fixture@example.invalid> %b\n"
                      b"committer Fixture <fixture@example.invalid> %b\n"
                      b"data %d\n%b\n" % (mark, when, when, len(message), message))
        if index == 0 and parent:
            stream.append(b"from %s\n" % parent.encode())
        elif index:
            stream.append(b"from :%d\n" % (commit_mark - 1 - len(files)))
        stream += entries + [b"\n"]
    result = subprocess.run(
        ["git", "-C", os.fspath(pusher), "fast-import", "--quiet"],
        input=b"".join(stream), capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("fast-import failed: " + result.stderr.decode()[:300])
    tip = git(pusher, "rev-parse", "refs/heads/box-snapshots").stdout.decode().strip()
    git(pusher, "push", "-q", "origin", tip + ":refs/heads/box-snapshots")
    git(pusher, "update-ref", "-d", "refs/heads/box-snapshots")
    return tip


def reset_data_branch(pusher, bare, tip):
    """Fixture-only ref surgery on the bare repo (simulates human cleanup).

    Moves the object through a scratch ref so update-ref can resolve it.
    """
    git(pusher, "push", "-q", "origin", tip + ":refs/heads/data-surgery-tmp")
    git(bare, "update-ref", "refs/heads/box-snapshots", tip)
    git(bare, "update-ref", "-d", "refs/heads/data-surgery-tmp")


def bare_tip(bare, ref="box-snapshots"):
    result = git(bare, "rev-parse", "--verify", "--quiet", ref + "^{commit}", check=False)
    return result.stdout.decode().strip() if result.returncode == 0 else None
