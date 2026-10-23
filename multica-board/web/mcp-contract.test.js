import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));

// Generate a real schema-2 payload from the backend (temp fake data, no real configs).
function backendPayload() {
  const py = `
import sys, json, tempfile
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(join(root, "..", "backend"))})
sys.path.insert(0, ${JSON.stringify(join(root, "..", "backend", "tests"))})
from fake_cli import FakeMultica, agent, runtime
from multica_board.cli_runner import CliRunner
from multica_board.collector import Collector, CollectOptions
from multica_board.store import Store
from multica_board.views import Views
from multica_board.mcp_local import McpContext
tmp = Path(tempfile.mkdtemp()); (tmp/"home"/".cursor").mkdir(parents=True)
(tmp/"home"/".cursor"/"mcp.json").write_text(json.dumps({"mcpServers":{"n":{"command":"x"}}}))
(tmp/"repo").mkdir(); (tmp/"data").mkdir(mode=0o700)
data = {"w1":{"meta":{"id":"w1","name":"A","slug":"a"},"issues":[],"runtimes":[runtime("w1")],
 "agents":[agent("w1","ag1",mcp_config={"mcpServers":{"n":{"command":"x"}}})],"tasks":{},
 "mcp_servers":[{"id":"s1","workspace_id":"w1","name":"n","transport":"stdio","agent_count":1,"created_at":None,"updated_at":"t"}],
 "agent_mcp":{"ag1":[{"id":"s1","name":"n","enabled":True}]}}}
store = Store(tmp/"data"/"b.sqlite")
r = CliRunner(profile="p", _exec=FakeMultica(data), _sleep=lambda s:None, retries=0)
ctx = McpContext(home=tmp/"home", repo_root=tmp/"repo", data_dir=tmp/"data")
Collector(r, store, "p", CollectOptions(all_workspaces=True), mcp_ctx=ctx).collect()
print(json.dumps(Views(store, "p", app_url="x", collect_interval_s=60, mcp_ctx=ctx).mcp()))
`;
  return JSON.parse(execFileSync("python3", ["-c", py], { encoding: "utf8" }));
}

test("backend schema-2 field names match what the frontend relies on", () => {
  const body = backendPayload();
  assert.equal(body.mcp_schema, 2);
  for (const k of ["ok","error","generated_at","multica_collected_at","tools","repos","workspaces","support","unknown","items","summary","attention","meta"]) assert.ok(k in body, k);
  const item = body.items[0];
  for (const k of ["id","display_name","display_dup","source_class","source_uncertain","source_label","in_project","presence","transports","tools","repo_cells","entries","workspace_entries","consistency","tidy","bucket","tags","reason","quiet","command"]) assert.ok(k in item, `items[].${k}`);
  for (const k of ["state","partial","pending","groups","parts"]) assert.ok(k in item.consistency);
  for (const k of ["badge","tone","partial","counts","buckets","tidy"]) assert.ok(k in body.attention);
  for (const k of ["connected","partial","unknown_sources","reason","items"]) assert.ok(k in body.attention.buckets.drift);
  for (const k of ["local_files","project_files","library","bindings","own_hidden","own_invalid","own_pending","key","multica"]) assert.ok(k in body.unknown);
  assert.equal(body.meta.sources.length, 7);
  for (const chip of body.meta.sources) for (const k of ["id","label","state","detail"]) assert.ok(k in chip);
  for (const t of body.tools) for (const k of ["id","label","short_label","files"]) assert.ok(k in t);
  const entry = item.entries[0];
  for (const k of ["scope","tool","path","state","broken_reason","disabled","transport","env","headers","group"]) assert.ok(k in entry, `entries[].${k}`);
  for (const v of Object.values(item.tools)) assert.ok(v == null || ["direct","off","missing","unknown","not_applicable"].includes(v));
});
