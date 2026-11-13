#!/usr/bin/env python3
"""Foreground Ego UI check: isolated fixtures, no account data, server cleanup.

Run: python3 multica-board/web/skill-drift.acceptance.py OUTPUT_DIRECTORY
Requires ego-browser. Screenshots cover 1440/390 in light/dark.
"""
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
from urllib.parse import urlsplit

WEB = Path(__file__).resolve().parent


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = json.loads((WEB / "fixtures/skills.json").read_text())
    payload = json.loads((WEB / "fixtures/skills-digest.json").read_text())
    template = dict(origin="inventory", source_class="local_custom", source_label="验收 fixture", entries=[],
                    tools={}, findings=[], command=None, local_source=None, workspace_installs=[],
                    bucket=None, reconciliation_status="aligned", status_label="fixture", baseline_debt=False,
                    channel_label="验收 fixture", channel_detail="离线合成数据", current_version="fixture", observed_version="fixture")
    base.update(payload, items=[{**template, **row} for row in payload["items"]],
                summary=[{"id": "local_custom", "label": "验收 fixture", "count": len(payload["items"]), "statuses": {}}])
    requests = []

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(WEB), **kwargs)

        def do_GET(self):
            requests.append({"method": self.command, "path": self.path})
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                name = path.removeprefix("/api/")
                fixture = WEB / "fixtures" / (name + ".json")
                data = (base if name in {"skills", "skills/meta"} else base["attention"] if name == "skills/attention"
                        else json.loads(fixture.read_text()) if fixture.is_file() else {"ok": True, "items": []})
                raw = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            else:
                super().do_GET()

        def do_POST(self):
            requests.append({"method": self.command, "path": self.path})
            self.send_error(405)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    source = f"""
const task = await taskSpace("ACM-893 drift acceptance");
console.log({{spaceId:task.spaceId}});
const {{run}} = await import({json.dumps((WEB / 'skill-drift.acceptance.mjs').as_uri())});
await run(task.page("p1"), "http://127.0.0.1:{server.server_port}", {json.dumps(str(output))});
await task.finish({{keep:[]}});
"""
    try:
        result = subprocess.run(["ego-browser", "nodejs"], input=source, text=True, timeout=180)
        assert result.returncode == 0, "browser acceptance failed"
        assert requests and all(row["method"] == "GET" for row in requests), "non-GET request"
        (output / "requests.json").write_text(json.dumps(requests, indent=2) + "\n")
        print(f"PASS: {len(requests)} requests, GET only; 4 screenshots; fixture server stopped on exit.")
    finally:
        server.shutdown()
        worker.join()
        server.server_close()


if __name__ == "__main__":
    main()
