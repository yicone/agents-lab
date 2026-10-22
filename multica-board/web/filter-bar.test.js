import assert from "node:assert/strict";
import test from "node:test";
import { applyPatch, formatHash, parseHash } from "./route.js";
import { currentScopeLabel, filterChrome, scopeNote, workspaceFilterBar } from "./filter-bar.js";

function vnode(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children: children.flat(Infinity).filter((child) => child != null && child !== false) };
}

function walk(node, found = []) {
  if (!node || typeof node !== "object") return found;
  found.push(node);
  for (const child of node.children || []) walk(child, found);
  return found;
}

const workspaces = [
  { slug: "acm-lab", name: "ACM Lab" },
  { slug: "beta-coach", name: "Beta 辅导" },
];

test("Issues and Agents share the workspace filter; Decide and Skills do not", () => {
  assert.equal(filterChrome("issues"), "shared");
  assert.equal(filterChrome("agents"), "shared");
  assert.equal(filterChrome("decide"), "decide");
  assert.equal(filterChrome("skills"), "skills");
  assert.equal(filterChrome("map"), "none");
  assert.equal(scopeNote("issues"), "");
  assert.equal(scopeNote("agents"), "");
  assert.match(scopeNote("decide"), /ACM Lab/);
  assert.match(scopeNote("decide"), /不作用于这一页/);
  assert.match(scopeNote("skills"), /本机技能、Multica Workspace 安装与 Grok Bot 名称声明/);
  assert.match(scopeNote("skills"), /不作用于这一页/);
  assert.equal(scopeNote("map"), "");
});

test("the shared bar names the selection and always offers clear", () => {
  const idle = workspaceFilterBar(vnode, {
    workspaces,
    activeSlug: "",
    chip: (ws) => vnode("button", { class: "ws-chip", "data-slug": ws.slug }, ws.name),
  });
  const idleNodes = idle.flatMap((node) => walk(node));
  assert.equal(idleNodes.find((node) => node.attrs.class === "scope-current").children[0], "当前：全部 workspace");
  const idleClear = idleNodes.find((node) => node.attrs.class === "ws-clear");
  assert.equal(idleClear.children[0], "清除");
  assert.equal(idleClear.attrs.disabled, true);
  assert.equal(idleNodes.filter((node) => node.attrs.class === "ws-chip").length, 2);

  const selected = workspaceFilterBar(vnode, {
    workspaces,
    activeSlug: "acm-lab",
    chip: (ws) => vnode("button", { class: `ws-chip${ws.slug === "acm-lab" ? " active" : ""}` }, ws.name),
  });
  const nodes = selected.flatMap((node) => walk(node));
  assert.equal(nodes.find((node) => node.attrs.class === "scope-current").children[0], "当前：ACM Lab");
  assert.equal(nodes.find((node) => node.attrs.class === "ws-clear").attrs.disabled, false);
  assert.equal(nodes.filter((node) => String(node.attrs.class).includes("active")).length, 1);
  assert.equal(currentScopeLabel(workspaces, "missing"), "missing");
});

test("a hidden Agents filter stays in the URL until it is cleared", () => {
  const issues = parseHash("#/issues?ws=acm-lab&layer=l3");
  const agents = applyPatch(issues, { view: "agents" });
  assert.equal(agents.view, "agents");
  assert.equal(agents.ws, "acm-lab");
  assert.equal(filterChrome(agents.view), "shared");
  assert.equal(formatHash(agents), "#/agents?ws=acm-lab");

  const decide = applyPatch(agents, { view: "decide" });
  assert.equal(decide.ws, "acm-lab");
  assert.equal(filterChrome(decide.view), "decide");
  assert.equal(formatHash(decide), "#/decide?ws=acm-lab");

  const skills = applyPatch(decide, { view: "skills" });
  assert.equal(skills.ws, "acm-lab");
  assert.equal(filterChrome(skills.view), "skills");
  assert.equal(formatHash(skills), "#/skills?ws=acm-lab");

  const cleared = applyPatch(parseHash("#/agents?ws=acm-lab&failed=1"), { ws: "" });
  assert.equal(cleared.ws, "");
  assert.equal(cleared.failedOnly, true);
  assert.equal(formatHash(cleared), "#/agents?failed=1");

  const deep = parseHash("#/agents?ws=beta-coach");
  assert.equal(deep.ws, "beta-coach");
  assert.equal(formatHash(deep), "#/agents?ws=beta-coach");
});
