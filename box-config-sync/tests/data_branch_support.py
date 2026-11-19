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


def git(cwd, *args, check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    return subprocess.run(["git", "-C", os.fspath(cwd), *args], env=env,
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


def push_data_commit(pusher, files, message="fixture data commit", parents=(), push=True):
    """Commit files directly with plumbing and push to box-snapshots.

    ``parents`` gives the commit parents (zero for a root, two for a merge);
    ``push=False`` only builds the commit for reset_data_branch surgery.
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
    sha = git(pusher, *args).stdout.decode().strip()
    if push:
        git(pusher, "push", "-q", "origin", sha + ":refs/heads/box-snapshots")
    # commit-tree never materialized the orphan branch ref; force-switch back.
    git(pusher, "checkout", "-qf", "main")
    git(pusher, "clean", "-fdq")
    return sha


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
