#!/usr/bin/env python3
"""Host-side request queue worker. Run this outside any agent sandbox."""
from __future__ import annotations
import argparse, json, os, pathlib, subprocess, sys, time

def main():
    p = argparse.ArgumentParser(description="Run Devin review requests from a host-side queue")
    p.add_argument("--queue", default="/private/tmp/devin-host-review")
    p.add_argument("--once", action="store_true")
    args = p.parse_args(); queue = pathlib.Path(args.queue); queue.mkdir(mode=0o700, parents=True, exist_ok=True)
    while True:
        for request in sorted(queue.glob("*.request.json")):
            response = request.with_suffix(".response.json")
            if response.exists(): continue
            try:
                payload = request.read_text()
                proc = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name("host_provider.py"))], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, input=payload)
                data = json.loads(proc.stdout)
            except Exception:
                data = {"schema": "devin-host-review/v1", "status": "provider-unavailable", "repository_root": None, "pr_number": None, "head_sha": None, "round": None, "findings_count": 0, "comments": [], "evidence_ref": None, "retryable": False}
            tmp = response.with_name("." + response.name + ".tmp"); tmp.write_text(json.dumps(data)); os.chmod(tmp, 0o600); os.replace(tmp, response)
            request.unlink(missing_ok=True)
        if args.once: return 0
        time.sleep(1)

if __name__ == "__main__": raise SystemExit(main())
