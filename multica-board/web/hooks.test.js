import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { CAPABILITY_KINDS } from "./capability.js";
import { filteredItems, renderHooks, sourceOptions, visibleSourceErrors } from "./hooks.js";
import { formatHash, navGroup, parseHash, redirectHash } from "./route.js";

const payload = JSON.parse(readFileSync(new URL("./fixtures/hooks.json", import.meta.url), "utf8"));

function h(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children: children.flat(Infinity).filter((child) => child != null && child !== false) };
}

function textOf(node) {
  if (node == null || node === false) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  return (node.children || []).map(textOf).join("\n");
}

test("capability kinds are skills, MCP and hooks, with no Memory", () => {
  assert.deepEqual(CAPABILITY_KINDS.map((kind) => kind.id), ["skills", "mcp", "hooks"]);
  assert.equal(JSON.stringify(CAPABILITY_KINDS).toLowerCase().includes("memory"), false);
});

test("hooks route keeps the four sources and drops Grok Bot or Multica", () => {
  assert.equal(redirectHash("#/hooks"), "#/configure/hooks");
  assert.equal(redirectHash("#/configure/hooks"), null);
  const route = parseHash("#/configure/hooks?source=cursor");
  assert.equal(route.view, "hooks");
  assert.equal(route.source, "cursor");
  assert.equal(navGroup(route), "configure");
  assert.equal(formatHash(route), "#/configure/hooks?source=cursor");
  assert.equal(formatHash(parseHash("#/configure/hooks?source=grok_bot")), "#/configure/hooks");
  assert.equal(formatHash(parseHash("#/configure/hooks?source=multica")), "#/configure/hooks");
  assert.deepEqual(sourceOptions().map((item) => item.label), ["Cursor", "Codex 用户 hooks", "Grok CLI", "Hermes 插件事件"]);
});

test("same event stays one row per source, and another source's error stays out", () => {
  assert.equal(filteredItems(payload, {}).filter((item) => item.event === "stop").length, 2);
  assert.equal(filteredItems(payload, {}).filter((item) => item.event === "post_llm_call").length, 2);
  const cursor = filteredItems(payload, { source: "cursor" });
  assert.deepEqual(cursor.map((item) => item.source_id), ["cursor", "cursor"]);
  assert.equal(visibleSourceErrors(payload, { source: "cursor" }).length, 0);
  assert.equal(visibleSourceErrors(payload, { source: "grok_cli" })[0].error.code, "cursor_format_not_run");
  const tree = { children: renderHooks({ h, hooks: payload, route: { source: "cursor", q: "" }, setRoute() {} }) };
  const visible = textOf(tree);
  assert.match(visible, /sessionStart/);
  assert.equal(visible.includes("Grok 再扫到"), false);
  assert.equal(visible.includes("Memory"), false);
  assert.equal(visible.includes("Grok Bot"), false);
  assert.equal(visible.includes("Multica"), false);
  assert.match(visible, /skills/);
  assert.match(visible, /MCP/);
  assert.match(visible, /hooks/);
  const all = textOf({ children: renderHooks({ h, hooks: payload, route: { source: "", q: "" }, setRoute() {} }) });
  assert.match(all, /Grok 再扫到/);
  assert.equal(all.includes("Hermes shell"), false);
});
