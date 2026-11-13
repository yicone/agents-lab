import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { driftSignals, driftSuggestions, DIGEST_COMMAND } from "./skill-drift.js";
import { renderSkillDrawer } from "./skills.js";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/skills-digest.json", import.meta.url)));
const h = (tag, attrs, ...children) => ({ tag, attrs, children: children.flat(Infinity).filter(Boolean) });
const flatten = node => typeof node === "object" ? [node, ...(node.children || []).flatMap(flatten)] : [node];

test("fixture shares Python/JS independent signals and GET suggestions", () => {
  const python = spawnSync("python3", ["-c", `
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('digest', sys.argv[1])
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
print(json.dumps([{'signals':module.signals(i), 'commands':module.suggestions(i)} for i in json.load(sys.stdin)['items']]))
`, new URL("../scripts/skills-drift-digest.py", import.meta.url).pathname], { input: JSON.stringify(fixture), encoding: "utf8" });
  assert.equal(python.status, 0, python.stderr);
  assert.deepEqual(JSON.parse(python.stdout), fixture.items.map(item => ({ signals: driftSignals(item), commands: driftSuggestions(item).commands.map(row => row.command) })));
});

test("mismatch drawer copies exactly one suggested command without fetching or executing", async () => {
  const item = fixture.items[0];
  const tree = h("root", null, renderSkillDrawer({ h, closeDrawer() {}, absTime: x => x }, item));
  const region = flatten(tree).find(node => node?.attrs?.class === "skill-drift-suggestions");
  assert.ok(region);
  assert.ok(flatten(region).includes(DIGEST_COMMAND));
  const original = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const copied = [];
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: async text => copied.push(text) } } });
  try {
    const button = flatten(region).find(node => node?.tag === "button");
    const target = {};
    button.attrs.onclick({ currentTarget: target });
    await Promise.resolve();
    assert.deepEqual(copied, [DIGEST_COMMAND]);
    assert.equal(target.textContent, "已复制");
  } finally {
    if (original) Object.defineProperty(globalThis, "navigator", original);
    else delete globalThis.navigator;
  }
});

test("aligned and unknown have explicit no-command branches; stale debt is not drift", () => {
  assert.match(driftSuggestions(fixture.items[3]).note, /无需命令/);
  assert.match(driftSuggestions(fixture.items[4]).note, /信息不足/);
  for (const item of [fixture.items[3], fixture.items[4], { reconciliation_status: "stale", baseline_debt: true }, {}]) {
    assert.deepEqual(driftSuggestions(item).commands, []);
  }
});

test("unknown workspace IDs, shell injection, emails, homes and secrets never enter suggestions", () => {
  for (const value of [null, "undefined", "user@example.test", "/Users/private/skills", "$(touch /tmp/no)", "x; echo token=secret", "sk-secret"]) {
    const item = { ...fixture.items[0], name: value, local_source: { path: value }, command: value,
      workspace_installs: [{ workspace_id: value, id: value }] };
    const commands = driftSuggestions(item).commands.map(row => row.command);
    assert.equal(commands.length, 2);
    assert.ok(commands.every(command => command.startsWith("python3 ")));
  }
  const commands = driftSuggestions(fixture.items[0]).commands.map(row => row.command).join("\n");
  assert.match(commands, /skill get/);
  assert.doesNotMatch(commands, /refresh|update|activate|POST|skills add|--content-file/);
});
