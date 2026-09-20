#!/usr/bin/env python3
"""Host-only enrollment for a repository allowed to use the Devin provider."""
from __future__ import annotations
import argparse, json, os, pathlib, subprocess, tempfile

def run(argv):
    p = subprocess.run(argv, text=True, capture_output=True, timeout=15)
    if p.returncode: raise RuntimeError(p.stderr.strip() or "command failed")
    return p.stdout.strip()

def main():
    p = argparse.ArgumentParser(); p.add_argument("--repo-root", required=True); p.add_argument("--allowlist", default="~/.config/devin/provider-allowlist.json")
    args = p.parse_args(); root = pathlib.Path(run(["git", "-C", args.repo_root, "rev-parse", "--show-toplevel"])).resolve()
    remote = run(["git", "-C", str(root), "config", "--get", "remote.origin.url"]).removesuffix(".git")
    if remote.startswith("git@") and ":" in remote: origin = remote.split(":", 1)[1]
    elif "/github.com/" in remote: origin = remote.split("/github.com/", 1)[1]
    else: raise RuntimeError("origin is not a GitHub repository")
    if origin.count("/") != 1: raise RuntimeError("origin identity is ambiguous")
    path = pathlib.Path(args.allowlist).expanduser(); path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try: data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError): data = {}
    if not isinstance(data, dict): raise RuntimeError("allowlist is not an object")
    existing = data.get(str(root))
    if existing and existing != origin: raise RuntimeError("repository is already bound to a different origin")
    data[str(root)] = origin
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f: json.dump(data, f, indent=2, sort_keys=True); f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    print(json.dumps({"schema":"devin-host-enrollment/v1","status":"enrolled","repository_root":str(root),"origin":origin}))

if __name__ == "__main__":
    try: main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"schema":"devin-host-enrollment/v1","status":"failed","error":str(exc)})); raise SystemExit(2)
