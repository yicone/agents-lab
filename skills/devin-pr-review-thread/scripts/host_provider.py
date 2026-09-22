#!/usr/bin/env python3
"""Host-side JSON broker; never expose Devin/gh internals to sandbox callers."""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, os, pathlib, sys, tempfile, subprocess, re
from contextlib import contextmanager
from repository_binding import BindingError, normalize_origin

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

def atomic_write(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f: json.dump(value, f, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

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
    enrolled = allowlist_origin(root, allowlist)
    if enrolled is None: return "await-user"
    try:
        remote = subprocess.run(["git", "-C", root, "config", "--get", "remote.origin.url"], text=True, capture_output=True, timeout=15)
        if remote.returncode == 0 and remote.stdout.strip() and normalize_origin(remote.stdout.strip()) != enrolled:
            return "invalid-request"
    except (OSError, subprocess.SubprocessError, BindingError):
        pass
    if isinstance(req.get("pr_number"), bool) or not isinstance(req.get("pr_number"), int) or req["pr_number"] <= 0: return "invalid-request"
    if isinstance(req.get("round"), bool) or not isinstance(req.get("round"), int) or req["round"] <= 0: return "invalid-request"
    timeout = req.get("timeout_seconds", 900)
    if not isinstance(timeout, int) or not 30 <= timeout <= 1800: return "invalid-request"
    auth = req.get("authorization")
    if not isinstance(auth, dict) or set(auth) != {"action", "authorization_id", "head_sha"} or auth.get("action") != "run": return "invalid-request"
    aid = auth.get("authorization_id")
    record = authorizations.get(aid) if isinstance(aid, str) else None
    if not isinstance(record, dict) or record.get("used") or record.get("repository_root") != root or record.get("pr_number") != req["pr_number"] or record.get("round") != req["round"] or record.get("head_sha") != auth.get("head_sha"): return "invalid-request"
    return None

def allowlist_origin(root, allowlist):
    if root in allowlist: return allowlist[root]
    matches = [(key[:-2], value) for key, value in allowlist.items() if key.endswith("/*") and pathlib.Path(root).parent == pathlib.Path(key[:-2]).resolve()]
    if not matches: return None
    try:
        remote = subprocess.run(["git", "-C", root, "config", "--get", "remote.origin.url"], text=True, capture_output=True, timeout=15)
        actual = normalize_origin(remote.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None
    return next((origin for _, origin in matches if origin == actual), None)

@contextmanager
def root_lock(root):
    lock = pathlib.Path(os.environ.get("DEVIN_PROVIDER_LOCK_DIR", "~/.local/share/devin/host-provider/locks")).expanduser()
    lock.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = lock / (hashlib.sha256(root.encode()).hexdigest() + ".lock")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            holder = int(path.read_text().strip())
            os.kill(holder, 0)
        except ProcessLookupError:
            try: path.unlink()
            except FileNotFoundError: raise RuntimeError("busy")
            try: fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError: raise RuntimeError("busy")
        except (PermissionError, ValueError, OSError):
            raise RuntimeError("busy")
        else:
            raise RuntimeError("busy")
    try:
        os.write(fd, str(os.getpid()).encode()); os.close(fd); yield
    finally:
        try: path.unlink()
        except FileNotFoundError: pass

def changed_lines(root, origin, pr):
    try:
        p = subprocess.run(["gh", "pr", "diff", str(pr), "--repo", origin, "--patch"], cwd=root, text=True, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode: return None
    result, path, line = {}, None, 0
    for raw in p.stdout.splitlines():
        if raw.startswith("+++ b/"): path = raw[6:]; result.setdefault(path, [])
        elif raw.startswith("@@") and path:
            m = re.search(r"\+(\d+)(?:,(\d+))?", raw)
            if m: line = int(m.group(1))
        elif path and raw.startswith("+") and not raw.startswith("+++"):
            result[path].append(line); line += 1
        elif path and not raw.startswith("-"):
            line += 1
    return result

def run_review(root, origin, req, pf, evidence_payload):
    sid = pf["session"]["id"]; head = pf["pr"]["head_sha"]; pr = req["pr_number"]
    lines = changed_lines(root, origin, pr)
    if not lines: return "await-user", [], evidence_payload | {"error": "diff unavailable"}
    prompt = ("Return exactly one JSON document matching devin-pr-review/v1, no Markdown. "
              f"Review PR {pr} at head {head} in repository {root}. "
              f"Your session_id is {sid}; repository_root is {root}. "
              "Do not edit, commit, push, or call GitHub. Report only actionable correctness, security, reliability, or maintainability findings on added RIGHT lines.")
    devin_args = ["devin"]
    config = os.environ.get("DEVIN_REVIEW_CONFIG")
    if config: devin_args += ["--config", config]
    devin_args += ["-r", sid, "--model", "swe-2-high", "--permission-mode", "auto", "--respect-workspace-trust", "true", "-p", "--", prompt]
    try:
        p = subprocess.run(devin_args, cwd=root, text=True, capture_output=True, timeout=req.get("timeout_seconds", 900))
    except (OSError, subprocess.SubprocessError) as exc:
        return "await-user", [], evidence_payload | {"error": "deving_process_failure", "detail": type(exc).__name__}
    evidence_payload |= {"returncode": p.returncode, "stderr": p.stderr[-4000:], "stdout": p.stdout[-4000:]}
    if p.returncode != 0 or "requires confirmation" in p.stderr.lower(): return "await-user", [], evidence_payload | {"error": "permission_or_process_failure"}
    start = p.stdout.find("{")
    cleaned = p.stdout[start:] if start >= 0 else p.stdout
    try:
        decoder = json.JSONDecoder()
        parsed, end = decoder.raw_decode(cleaned)
        cleaned = cleaned[:end]
    except json.JSONDecodeError:
        parsed = None
    fd, path = tempfile.mkstemp(prefix="devin-review-", suffix=".json"); os.close(fd); pathlib.Path(path).write_text(cleaned); os.chmod(path, 0o600)
    linefile = path + ".lines"; pathlib.Path(linefile).write_text(json.dumps(lines)); os.chmod(linefile, 0o600)
    dropped = []
    for _ in range(len(parsed.get("findings", [])) + 1 if isinstance(parsed, dict) else 1):
        pathlib.Path(path).write_text(json.dumps(parsed, ensure_ascii=False))
        try:
            valid = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name("validate_review.py")), path, "--root", root, "--head-sha", head, "--session-id", sid, "--pr-number", str(pr), "--changed-lines", linefile], text=True, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            return "await-user", [], evidence_payload | {"error": "validator_failure"}
        if valid.returncode == 0:
            break
        bad_line = re.search(r"invalid review: ([A-Za-z0-9._-]+): path/line is not an added RIGHT line", valid.stderr)
        if not bad_line or not isinstance(parsed, dict):
            return "await-user", [], evidence_payload | {"error": "invalid_review_output", "validator_stderr": valid.stderr[-4000:]}
        bad_id = bad_line.group(1); dropped.append(bad_id)
        parsed["findings"] = [f for f in parsed.get("findings", []) if not isinstance(f, dict) or f.get("id") != bad_id]
    else:
        return "await-user", [], evidence_payload | {"error": "invalid_review_output", "validator_stderr": "validator did not converge"}
    if dropped:
        evidence_payload["dropped_invalid_findings"] = dropped
    if parsed is None:
        return "await-user", [], evidence_payload | {"error": "invalid_review_output", "parse_error": "no_complete_json_document"}
    review = parsed
    if not review.get("findings"): return "no-findings", [], evidence_payload
    comments = []
    existing = set()
    try:
        listed = subprocess.run(["gh", "api", f"repos/{origin}/pulls/{pr}/comments"], text=True, capture_output=True, timeout=30)
        if listed.returncode == 0:
            data = json.loads(listed.stdout)
            for item in (data if isinstance(data, list) else []):
                body = item.get("body", "") if isinstance(item, dict) else ""
                marker_match = re.search(r"session=([^ ]+) head=([^ ]+) finding=([^ >]+)", body)
                if marker_match and marker_match.group(1) == sid and marker_match.group(2) == head:
                    existing.add(marker_match.group(3))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    for f in review["findings"]:
        marker = f"<!-- devin-pr-review session={sid} head={head} finding={f['id']} -->"
        if f["id"] in existing:
            continue
        body = marker + "\n**[" + f["severity"] + "] " + f["title"] + "**\n\n" + f["body"]
        try:
            c = subprocess.run(["gh", "api", f"repos/{origin}/pulls/{pr}/comments", "-f", f"body={body}", "-f", f"commit_id={head}", "-f", f"path={f['path']}", "-F", f"line={f['line']}", "-f", "side=RIGHT"], text=True, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return "await-user", comments, evidence_payload | {"error": "github_publish_failed"}
        if c.returncode != 0: return "await-user", comments, evidence_payload | {"error": "github_publish_failed"}
        try:
            obj = json.loads(c.stdout); comments.append({"id": str(obj.get("id")), "url": obj.get("html_url")})
        except json.JSONDecodeError: return "await-user", comments, evidence_payload | {"error": "github_publish_invalid"}
    return "review-published", comments, evidence_payload

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
            ledger_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            atomic_write(ledger_path, ledger)
            preflight = pathlib.Path(__file__).with_name("preflight.py")
            try:
                proc = subprocess.run([sys.executable, str(preflight), "--repo-root", req["repository_root"], "--pr", str(req["pr_number"])], text=True, capture_output=True, timeout=30)
                try: pf = json.loads(proc.stdout)
                except json.JSONDecodeError: pf = {"status": "provider-unavailable"}
            except (OSError, subprocess.SubprocessError) as exc:
                proc = None; pf = {"status": "provider-unavailable", "error": type(exc).__name__}
            if proc is None or proc.returncode != 0 or pf.get("status") != "ok":
                out = response("await-user", req, retryable=False)
                evidence = write_evidence({"request": req, "preflight": pf, "stderr": proc.stderr[-2000:] if proc else ""})
            elif req["authorization"]["head_sha"] != pf.get("pr", {}).get("head_sha"):
                out = response("await-user", req, retryable=False)
                evidence = write_evidence({"request": req, "preflight": pf, "error": "head_mismatch"})
            else:
                origin = allowlist_origin(str(pathlib.Path(req["repository_root"]).resolve()), allowlist)
                status, comments, evidence_payload = run_review(req["repository_root"], origin, req, pf, {"request": req, "preflight": pf})
                out = response(status, req, retryable=False, findings_count=len(comments), comments=comments)
                evidence = write_evidence(evidence_payload)
            out["evidence_ref"] = evidence
            ledger[aid] = {"status": "complete", "response": out}; atomic_write(ledger_path, ledger)
    except RuntimeError:
        out = response("provider-unavailable", req, retryable=False)
    print(json.dumps(out, ensure_ascii=False)); return 0

if __name__ == "__main__": raise SystemExit(main())
