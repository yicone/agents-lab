import assert from "node:assert/strict";
import test from "node:test";
import { hookTriState, mcpTriState, renderTriState, skillTriState, triLines } from "./capability-state.js";

function h(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children: children.flat(Infinity).filter((child) => child != null && child !== false) };
}

function text(node) {
  const found = [];
  const walk = (item) => {
    if (item == null || item === false) return;
    if (typeof item === "string") found.push(item);
    else (item.children || []).forEach(walk);
  };
  walk(node);
  return found.join("");
}

test("skill tri-state uses files for declared and paths for reachable, not runtime guesses", () => {
  const present = skillTriState({ entries: [{ present: true }], evidence: [] });
  assert.equal(present.declared, "yes");
  assert.equal(present.reachable, "yes");
  assert.equal(present.observed, "unknown");
  const missing = skillTriState({ entries: [{ present: false }] });
  assert.equal(missing.reachable, "no");
  const unchecked = skillTriState({ name: "only-a-name" });
  assert.deepEqual([unchecked.declared, unchecked.reachable, unchecked.observed], ["unknown", "unknown", "unknown"]);
  const loaded = skillTriState({ entries: [{ present: true }], runtime_evidence: { codex: { runtime_loaded: true } } });
  assert.equal(loaded.observed, "yes");
  const disabled = skillTriState({ entries: [{ present: true }], runtime_evidence: { codex: { runtime_loaded: false } } });
  assert.equal(disabled.observed, "no");
});

test("mcp does not treat config presence as reachable or predicted load as observed", () => {
  const row = mcpTriState({ entries: [{ path: "/cfg" }], workspace_entries: [] });
  assert.equal(row.declared, "yes");
  assert.equal(row.reachable, "unknown");
  assert.equal(row.observed, "unknown");
  assert.deepEqual(
    [mcpTriState({}).declared, mcpTriState({}).reachable, mcpTriState({}).observed],
    ["unknown", "unknown", "unknown"],
  );
});

test("hooks observed is always unknown and missing fields stay unknown", () => {
  const forced = hookTriState({ declared: "yes", reachable: "yes", observed: "yes" });
  assert.equal(forced.observed, "unknown");
  const missing = hookTriState({ event: "prefetch" });
  assert.deepEqual(triLines(missing).map(([, value]) => value), ["未知", "未知", "未知"]);
  const node = renderTriState(h, missing);
  assert.match(text(node), /声明了/);
  assert.match(text(node), /可达/);
  assert.match(text(node), /观察到/);
  assert.match(text(node), /未知/);
  assert.equal(JSON.stringify(node).includes("bad"), false);
  assert.equal(JSON.stringify(node).includes("warn"), false);
});
