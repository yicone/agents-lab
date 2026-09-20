#!/usr/bin/env python3
"""Host-side JSON broker; never expose Devin/gh internals to sandbox callers."""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, os, pathlib, sys, tempfile, subprocess
from contextlib import contextmanager

SCHEMA = "devin-host-review/v1"
ALLOWED_REQUEST = {"schema", "repository_root", "pr_number", "round", "authorization", "timeout_seconds"}

def response(status, req=None, *, evidence_ref=None, retryable=False, findings_count=0, comments=None):
    req = req or {}
    auth = req.get("authorization") or {}
    return {"schema": SCHEMA, "status": status, "repository_root": req.get("repository_root"),
            "pr_number": req.get("pr_number"), "head_sha": auth.get("head_sha"),
            "round": req.get("round"), "findings_count": findings_count,
            "comments": comments or [], "evidence_ref": evidence_ref, "retryable": retryable}

def load_json(path):
    try: return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError): return None

def evidence_dir():
    p = pathlib.Path(os.environ.get("DEVIN_PROVIDER_EVIDENCE_DIR", "~/.local/share/devin/host-provider/evidence")).expanduser()
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    return p

def write_evidence(payload):
    raw = json.dumps(payload, ensure_ascii=False).encode()
    token = hashlib.sha256(raw + os.urandom(16)).hexdigest()[:24]
    path = evidence_dir() / f"{token}.json"
    fd, tmp = tempfile.mkstemp(prefix=".evidence-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f: f.write(raw)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return token

def validate_request(req, allowlist, authorizations):
    if not isinstance(req, dict) or set(req) - ALLOWED_REQUEST or req.get("schema") != SCHEMA: return "invalid-request"
    root = req.get("repository_root")
    if not isinstance(root, str) or not os.path.isabs(root): return "invalid-request"
    root = str(pathlib.Path(root).resolve())
    if root not in allowlist: return "invalid-request"
    if not isinstance(req.get("pr_number"), int) or req["pr_number"] <= 0: return "invalid-request"
    if not isinstance(req.get("round"), int) or req["round"] <= 0: return "invalid-request"
    timeout = req.get("timeout_seconds", 900)
    if not isinstance(timeout, int) or not 30 <= timeout <= 1800: return "invalid-request"
    auth = req.get("authorization")
    if not isinstance(auth, dict) or set(auth) != {"action", "authorization_id", "head_sha"} or auth.get("action") != "run": return "invalid-request"
    aid = auth.get("authorization_id")
    record = authorizations.get(aid) if isinstance(aid, str) else None
    if not isinstance(record, dict) or record.get("used") or record.get("repository_root") != root or record.get("pr_number") != req["pr_number"] or record.get("round") != req["round"] or record.get("head_sha") != auth.get("head_sha"): return "invalid-request"
    return None

@contextmanager
def root_lock(root):
    lock = pathlib.Path(os.environ.get("DEVIN_PROVIDER_LOCK_DIR", "~/.local/share/devin/host-provider/locks")).expanduser()
    lock.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = lock / (hashlib.sha256(root.encode()).hexdigest() + ".lock")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError("busy")
    try:
        os.write(fd, str(os.getpid()).encode()); os.close(fd); yield
    finally:
        try: path.unlink()
        except FileNotFoundError: pass

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--allowlist", default="~/.config/devin/provider-allowlist.json"); parser.add_argument("--authorizations", default="~/.config/devin/round-authorizations.json"); parser.add_argument("--ledger", default="~/.local/share/devin/host-provider/ledger.json")
    args = parser.parse_args(); raw = sys.stdin.read()
    try: req = json.loads(raw)
    except json.JSONDecodeError: print(json.dumps(response("invalid-request"))); return 2
    allow_data = load_json(pathlib.Path(args.allowlist).expanduser()) or {}
    allowlist = {str(pathlib.Path(k).resolve()): v for k, v in allow_data.items()} if isinstance(allow_data, dict) else {}
    auth = load_json(pathlib.Path(args.authorizations).expanduser()) or {}
    error = validate_request(req, allowlist, auth if isinstance(auth, dict) else {})
    if error: print(json.dumps(response(error, req))); return 2
    aid = req["authorization"]["authorization_id"]
    ledger_path = pathlib.Path(args.ledger).expanduser(); ledger = load_json(ledger_path) or {}
    prior = ledger.get(aid) if isinstance(ledger, dict) else None
    if isinstance(prior, dict) and prior.get("status") == "complete":
        print(json.dumps(prior["response"])); return 0
    try:
        with root_lock(str(pathlib.Path(req["repository_root"]).resolve())):
            ledger[aid] = {"status": "running", "started_at": dt.datetime.now(dt.timezone.utc).isoformat()}
            ledger_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True); ledger_path.write_text(json.dumps(ledger))
            preflight = pathlib.Path(__file__).with_name("preflight.py")
            proc = subprocess.run([sys.executable, str(preflight), "--repo-root", req["repository_root"], "--pr", str(req["pr_number"])], text=True, capture_output=True, timeout=30)
            try: pf = json.loads(proc.stdout)
            except json.JSONDecodeError: pf = {"status": "provider-unavailable"}
            if proc.returncode != 0 or pf.get("status") != "ok":
                out = response("await-user", req, retryable=False)
                evidence = write_evidence({"request": req, "preflight": pf, "stderr": proc.stderr[-2000:]})
            else:
                # Devin execution/publication is the next host integration seam; never fabricate a review result.
                out = response("provider-unavailable", req, retryable=False)
                evidence = write_evidence({"request": req, "preflight": pf, "status": "provider-unavailable"})
            out["evidence_ref"] = evidence
            ledger[aid] = {"status": "complete", "response": out}; ledger_path.write_text(json.dumps(ledger))
    except RuntimeError:
        out = response("provider-unavailable", req, retryable=False)
    print(json.dumps(out, ensure_ascii=False)); return 0

if __name__ == "__main__": raise SystemExit(main())
