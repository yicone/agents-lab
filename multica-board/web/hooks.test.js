import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { filteredHooks, renderHooks } from "./hooks.js";

const payload = JSON.parse(readFileSync(new URL("./fixtures/hooks.json", import.meta.url), "utf8"));

function h(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children: children.flat(Infinity).filter((child) => child != null && child !== false) };
}

function flatten(node, found = []) {
  if (node == null || node === false) return found;
  if (typeof node === "string") {
    found.push(node);
    return found;
  }
  found.push(node);
  for (const child of node.children || []) flatten(child, found);
  return found;
}

function render(route = {}) {
  return { children: renderHooks({ h, route: { source: "", ...route }, hooks: payload, setRoute() {} }) };
}

test("hooks page explains tri-state in text and never promotes observed", () => {
  const tree = render();
  const visible = flatten(tree).filter((node) => typeof node === "string").join("\n");
  assert.match(visible, /声明了/);
  assert.match(visible, /可达/);
  assert.match(visible, /观察到/);
  assert.match(visible, /未知/);
  assert.match(visible, /缺数据显示未知/);
  assert.match(visible, /不因此告警/);
  const observedLines = flatten(tree).filter((node) => node.attrs?.class === "tri-line" && flatten(node).some((item) => item === "观察到"));
  assert.ok(observedLines.length >= 4);
  for (const line of observedLines) {
    const lineText = flatten(line).filter((item) => typeof item === "string").join("");
    assert.match(lineText, /未知/);
    assert.equal(lineText.includes("已观察到"), false);
  }
  assert.equal(flatten(tree).some((node) => node.attrs?.class === "skills-anomaly"), false);
  assert.equal(visible.includes("Grok Bot"), false);
  assert.equal(visible.includes("Multica"), false);
  const hermes = payload.items.find((item) => item.source === "hermes");
  assert.equal(hermes.declared, undefined);
  assert.match(visible, /nowledge-mem\.prefetch/);
});

test("a selected source does not show another source's rows", () => {
  const grok = filteredHooks(payload, { source: "grok_cli" });
  assert.equal(grok.length, 1);
  assert.equal(grok[0].reachable, "no");
  const tree = render({ source: "grok_cli" });
  const visible = flatten(tree).filter((node) => typeof node === "string").join("\n");
  assert.match(visible, /不可达/);
  assert.match(visible, /未知/);
  assert.equal(visible.includes("nowledge-mem.prefetch"), false);
  assert.equal(visible.includes("stop"), false);
});
