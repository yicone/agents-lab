#!/usr/bin/env python3
"""Host-side request queue worker. Run this outside any agent sandbox."""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, subprocess, sys, time, secrets, datetime as dt, tempfile

PREFLIGHT_TIMEOUT_SECONDS = 90


def stable_failure(payload, failure_code, evidence_ref=None):
    auth = payload.get("authorization") if isinstance(payload, dict) else {}
    auth = auth if isinstance(auth, dict) else {}
    return {
        "schema": "devin-host-review/v1",
        "status": "provider-unavailable",
        "failure_code": failure_code,
        "repository_root": payload.get("repository_root") if isinstance(payload, dict) else None,
        "pr_number": payload.get("pr_number") if isinstance(payload, dict) else None,
        "head_sha": auth.get("head_sha"),
        "round": payload.get("round") if isinstance(payload, dict) else None,
        "findings_count": 0,
        "comments": [],
        "evidence_ref": evidence_ref,
        "retryable": False,
    }


def claim_request(request):
    """Atomically move a request out of the visible queue glob."""
    claimed = request.with_name(f".{request.name}.claimed-{os.getpid()}")
    try:
        request.rename(claimed)
    except (FileNotFoundError, OSError):
        return None
    return claimed


def acquire_worker_lock(queue):
    path = queue / ".worker.lock"
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return path
        except FileExistsError:
            try:
                holder = int(path.read_text().strip())
                os.kill(holder, 0)
            except ProcessLookupError:
                path.unlink(missing_ok=True)
                continue
            except (OSError, ValueError):
                raise RuntimeError("worker_lock_unverified")
            raise RuntimeError("worker_already_running")
    raise RuntimeError("worker_lock_unavailable")


def release_worker_lock(path):
    try:
        if path.read_text().strip() == str(os.getpid()):
            path.unlink(missing_ok=True)
    except (OSError, UnicodeError):
        pass

def startup_check(preflight_path, repo_root, pr_number):
    try:
        proc = subprocess.run([sys.executable, str(preflight_path), "--repo-root", repo_root, "--pr", str(pr_number)], text=True, capture_output=True, timeout=PREFLIGHT_TIMEOUT_SECONDS)
    except subprocess.SubprocessError as exc:
        print(json.dumps({"schema": "devin-host-startup/v1", "status": "host-unavailable", "preflight": {"status": "preflight_timeout", "diagnostics": [{"code": "preflight_timeout", "detail": type(exc).__name__}]}}, ensure_ascii=False))
        return 2
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        data = {"status": "provider-unavailable", "diagnostics": [{"code": "preflight_output_invalid"}]}
    if proc.returncode != 0 or data.get("status") != "ok":
        print(json.dumps({"schema": "devin-host-startup/v1", "status": "host-unavailable", "preflight": data}, ensure_ascii=False))
        return 2
    print(json.dumps({"schema": "devin-host-startup/v1", "status": "ready", "preflight": data}, ensure_ascii=False))
    return 0


def write_evidence(payload):
    directory = pathlib.Path(os.environ.get("DEVIN_PROVIDER_EVIDENCE_DIR", "~/.local/share/devin/host-provider/evidence")).expanduser()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    token = hashlib.sha256(raw + os.urandom(16)).hexdigest()[:24]
    path = directory / f"{token}.json"
    fd, temporary = tempfile.mkstemp(prefix=".preflight-", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return token

def authorize(payload, root, auth_path, preflight_path):
    """Issue a host-owned one-shot authorization for a minimal black-box request."""
    if payload.get("authorization"):
        return payload
    if not isinstance(payload.get("repository_root"), str) or not isinstance(payload.get("pr_number"), int):
        return payload
    round_number = payload.get("round", 1)
    if isinstance(round_number, bool) or not isinstance(round_number, int) or not 1 <= round_number <= 3:
        return payload
    try:
        pf = subprocess.run([sys.executable, str(preflight_path), "--repo-root", payload["repository_root"], "--pr", str(payload["pr_number"])], text=True, capture_output=True, timeout=PREFLIGHT_TIMEOUT_SECONDS)
        data = json.loads(pf.stdout)
        head = data.get("pr", {}).get("head_sha")
        if pf.returncode != 0 or data.get("status") != "ok" or not isinstance(head, str):
            enriched = dict(payload)
            enriched["_authorization_error"] = "await-user"
            enriched["_failure_code"] = data.get("status") or "provider-unavailable"
            enriched["_preflight"] = data
            enriched["_evidence_ref"] = write_evidence({"request": payload, "preflight": data})
            return enriched
        aid = "host-" + secrets.token_urlsafe(18)
        records = {}
        try: records = json.loads(auth_path.read_text())
        except (OSError, json.JSONDecodeError): pass
        if not isinstance(records, dict): records = {}
        records[aid] = {"used": False, "repository_root": str(pathlib.Path(payload["repository_root"]).resolve()), "pr_number": payload["pr_number"], "round": round_number, "head_sha": head, "issued_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        auth_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = auth_path.with_name("." + auth_path.name + ".tmp"); tmp.write_text(json.dumps(records)); os.chmod(tmp, 0o600); os.replace(tmp, auth_path)
        enriched = dict(payload); enriched["round"] = round_number; enriched["authorization"] = {"action": "run", "authorization_id": aid, "head_sha": head}
        return enriched
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        enriched = dict(payload); enriched["_authorization_error"] = "provider-unavailable"; return enriched

def main():
    p = argparse.ArgumentParser(description="Run Devin review requests from a host-side queue")
    p.add_argument("--queue", default="/private/tmp/devin-host-review")
    p.add_argument("--authorizations", default="~/.config/devin/round-authorizations.json")
    p.add_argument("--once", action="store_true")
    p.add_argument("--startup-check", action="store_true", help="run preflight before serving requests")
    p.add_argument("--check-only", action="store_true", help="exit after --startup-check")
    p.add_argument("--repo-root", help="repository root used by --startup-check")
    p.add_argument("--pr", type=int, help="PR number used by --startup-check")
    args = p.parse_args(); queue = pathlib.Path(args.queue); queue.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.startup_check:
        if not args.repo_root or not args.pr:
            p.error("--startup-check requires --repo-root and --pr")
        startup_rc = startup_check(pathlib.Path(__file__).with_name("preflight.py"), args.repo_root, args.pr)
        if startup_rc != 0 or args.check_only:
            return startup_rc
    try:
        worker_lock = acquire_worker_lock(queue)
    except RuntimeError as exc:
        print(json.dumps({"schema": "devin-host-startup/v1", "status": "host-unavailable", "failure_code": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    active = {}
    try:
      while True:
        for request in sorted(queue.glob("*.request.json")):
            response = request.with_name(request.name.replace(".request.json", ".response.json"))
            if response.exists(): continue
            claimed = claim_request(request)
            if claimed is None: continue
            out_path = err_path = None; out = err = proc = None
            payload = None
            try:
                payload = json.loads(claimed.read_text())
                payload = authorize(payload, queue, pathlib.Path(args.authorizations).expanduser(), pathlib.Path(__file__).with_name("preflight.py"))
                if payload.get("_authorization_error"):
                    data = {"schema": "devin-host-review/v1", "status": payload["_authorization_error"], "failure_code": payload.get("_failure_code") or "provider_unavailable", "repository_root": payload.get("repository_root"), "pr_number": payload.get("pr_number"), "head_sha": None, "round": payload.get("round"), "findings_count": 0, "comments": [], "evidence_ref": payload.get("_evidence_ref"), "retryable": False, "preflight": payload.get("_preflight")}
                    tmp = response.with_name("." + response.name + ".tmp"); tmp.write_text(json.dumps(data)); os.chmod(tmp, 0o600); os.replace(tmp, response)
                    claimed.unlink(missing_ok=True); continue
                provider_payload = json.dumps(payload)
                out_path = request.with_name("." + response.name + ".out")
                err_path = request.with_name("." + response.name + ".err")
                out_fd = os.open(out_path, os.O_CREAT | os.O_TRUNC | os.O_RDWR, 0o600)
                err_fd = os.open(err_path, os.O_CREAT | os.O_TRUNC | os.O_RDWR, 0o600)
                out = os.fdopen(out_fd, "w+"); err = os.fdopen(err_fd, "w+")
                proc = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).with_name("host_provider.py"))], stdin=subprocess.PIPE, stdout=out, stderr=err, text=True)
                proc.stdin.write(provider_payload); proc.stdin.close()
                active[claimed] = (proc, response, out, err, out_path, err_path, payload)
                claimed.unlink(missing_ok=True)
            except Exception as exc:
                evidence = write_evidence({"request": payload, "error": "worker_dispatch_failure", "detail": repr(exc)}) if isinstance(payload, dict) else None
                data = stable_failure(payload, "worker_dispatch_failure", evidence)
                tmp = response.with_name("." + response.name + ".tmp"); tmp.write_text(json.dumps(data)); os.chmod(tmp, 0o600); os.replace(tmp, response)
                claimed.unlink(missing_ok=True)
                if proc is not None and proc.poll() is None: proc.kill()
                for handle in (out, err):
                    if handle is not None: handle.close()
                for sidecar in (out_path, err_path):
                    if sidecar is not None: pathlib.Path(sidecar).unlink(missing_ok=True)
        for request, (proc, response, out, err, out_path, err_path, payload) in list(active.items()):
            if proc.poll() is None: continue
            stdout_text = stderr_text = ""
            try:
                out.close(); err.close()
                stdout_text = pathlib.Path(out_path).read_text(encoding="utf-8")
                stderr_text = pathlib.Path(err_path).read_text(encoding="utf-8")
                data = json.loads(stdout_text)
            except (OSError, UnicodeError, json.JSONDecodeError):
                evidence = write_evidence({"request": payload, "error": "provider_output_invalid", "returncode": proc.returncode, "stdout": stdout_text[-4000:], "stderr": stderr_text[-4000:]})
                data = stable_failure(payload, "provider_output_invalid", evidence)
            try:
                tmp = response.with_name("." + response.name + ".tmp"); tmp.write_text(json.dumps(data)); os.chmod(tmp, 0o600); os.replace(tmp, response)
                pathlib.Path(out_path).unlink(missing_ok=True); pathlib.Path(err_path).unlink(missing_ok=True)
            except OSError:
                pass
            del active[request]
        if args.once and not active and not list(queue.glob("*.request.json")): return 0
        time.sleep(1)
    finally:
        release_worker_lock(worker_lock)

if __name__ == "__main__": raise SystemExit(main())
