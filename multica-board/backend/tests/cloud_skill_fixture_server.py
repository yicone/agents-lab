"""Disposable cloud preview UI, using only synthetic files and a fake CLI.

python3 multica-board/backend/tests/cloud_skill_fixture_server.py --port 18795
Select a scenario by skill name: observed, empty, withheld_secret, too_large,
denied, error, uncollected. Nothing contacts a real workspace.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.cli_runner import CliRunner
from multica_board.server import BoardServer
from multica_board.store import Store
from multica_board.views import Views

WEB = Path(__file__).resolve().parents[2] / "web"
WS = "00000000-0000-4000-8000-000000000003"
STATES = ("observed", "empty", "withheld_secret", "too_large", "denied", "error", "uncollected")


def fixture(name):
    path = WEB / "fixtures" / (name + ".json")
    return json.loads(path.read_text()) if path.exists() else {"items": []}


class FixtureViews:
    def __init__(self, views):
        self.views = views
        self.installs = {state: [{"id": f"00000000-0000-4000-8000-{i:012d}", "workspace_id": WS,
                                  "workspace_name": "Fixture Workspace", "name": state, "agents": []}]
                         for i, state in enumerate(STATES, 10)}
        views._workspace_installs = lambda: self.installs

    def skills_content(self, **kwargs):
        return self.views.skills_content(**kwargs)

    def skills(self, *_):
        from multica_board.views import _cloud_only_item
        doc = fixture("skills")
        doc["items"] = [_cloud_only_item(name, rows) for name, rows in self.installs.items()]
        doc["attention"] = {"items": [], "badge": 0}
        return doc

    def meta(self): return fixture("meta")
    def agents(self): return {"items": []}
    def runtimes(self): return {"items": []}
    def issues(self): return {"items": []}
    def bots(self): return {"items": []}
    def hooks(self): return {"items": []}
    def run_metrics(self): return {"items": []}
    def usage(self): return fixture("usage")
    def layers(self): return fixture("layers")
    def mcp(self): return fixture("mcp")
    def changes(self, *_): return fixture("changes")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18795)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        binary = root / "fake-cli"
        binary.write_text('''#!/usr/bin/env python3
import sys,json
skill = sys.argv[sys.argv.index("get")+1]
i = int(skill[-12:])-10
if i == 4:
    print("unauthorized",file=sys.stderr); sys.exit(1)
if i == 5:
    print("invalid fixture",file=sys.stderr); sys.exit(1)
data={"id":skill}
if i != 6:
    data["content"] = ["# Cloud fixture\\n\\nRead only preview.", "", "password=fictional", "x"*(512*1024+1)][i]
print(json.dumps(data))
''')
        binary.chmod(0o700)
        store = Store(root / "board.sqlite")
        runner = CliRunner(profile="synthetic-fixture", binary=str(binary))
        views = Views(store, "synthetic-fixture", app_url="http://localhost", collect_interval_s=60, cloud_runner=runner)
        server = BoardServer("127.0.0.1", args.port, FixtureViews(views), static_dir=WEB)
        print(f"http://127.0.0.1:{args.port}/#/configure/skills", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            store.close()


if __name__ == "__main__":
    main()
