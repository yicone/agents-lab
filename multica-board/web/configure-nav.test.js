import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import test from "node:test";
import { renderEvidence } from "./evidence.js";
import { renderHooks } from "./hooks.js";
import { COPY } from "./ia-view.js";
import { renderMcp } from "./mcp.js";
import { renderSkills } from "./skills.js";

function h(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children: children.flat(Infinity).filter((child) => child != null && child !== false) };
}

function walk(node, visit) {
  if (node == null || node === false) return;
  if (typeof node === "string" || typeof node === "number") {
    visit(node);
    return;
  }
  visit(node);
  for (const child of node.children || []) walk(child, visit);
}

function texts(node) {
  const out = [];
  walk(node, (item) => {
    if (typeof item === "string" || typeof item === "number") out.push(String(item));
  });
  return out.join("");
}

function collect(nodes, pred) {
  const found = [];
  walk({ children: nodes }, (node) => {
    if (node && typeof node === "object" && pred(node)) found.push(node);
  });
  return found;
}

const noop = () => {};

test("configure secondary tabs are the five item names", () => {
  const app = readFileSync(new URL("./app.js", import.meta.url), "utf8");
  const line = app.split("\n").find((row) => row.includes("#/configure/skills"));
  assert.ok(line, "configure subnav");
  assert.match(line, /\["#\/configure\/agents", COPY\.agent\]/);
  assert.match(line, /\["#\/configure\/skills", "skills", "skills-badge"\]/);
  assert.match(line, /\["#\/configure\/mcp", "MCP", "mcp-badge"\]/);
  assert.match(line, /\["#\/configure\/hooks", "hooks", "hooks-badge"\]/);
  assert.match(line, /\["#\/configure\/runtime", COPY\.runtime\]/);
  assert.equal(line.includes("能力"), false);
  assert.equal(line.includes("COPY.capability"), false);
  assert.equal(COPY.section, "配置管理");
  assert.deepEqual([COPY.agent, COPY.runtime], ["Agent 配置", "运行配置"]);
});

test("skills, MCP and hooks pages use the item name and have no third switch", () => {
  const skills = renderSkills({
    h,
    route: { q: "", source: "", skillStatus: "", tool: "", custom: false, debt: false, skillGroup: "flat" },
    skills: {
      ok: true,
      summary: [{ id: "all", label: "全部", count: 0, statuses: {} }],
      tools: [{ id: "codex", label: "Codex" }],
      items: [],
      attention: { badge: 0, baseline_debt: 0, counts: {}, buckets: {} },
    },
    expandedSkills: new Set(["all"]),
    setRoute: noop,
    openSkill: noop,
    focusSkill: noop,
    toggleSkillGroup: noop,
    setReviewDays: noop,
    ago: () => "刚刚",
  });
  const mcp = renderMcp({
    h,
    route: { source: "", mcpStatus: "", tool: "", mcpGroup: "source", mcpAssigned: false, q: "" },
    mcp: { ok: true, tools: [], items: [], summary: [], repos: [], unknown: {} },
    setRoute: noop,
    ago: () => "",
    absTime: (value) => value,
    openMcp: noop,
    focusMcp: noop,
    toggleMcpGroup: noop,
    expandedMcp: new Set(),
    copyWithFallback: () => true,
  });
  const hooks = renderHooks({
    h,
    hooks: { ok: true, items: [], sources: [] },
    route: { source: "", q: "" },
    setRoute: noop,
  });
  const pages = [
    ["skills", skills, "配置管理 › skills"],
    ["MCP", mcp, "配置管理 › MCP"],
    ["hooks", hooks, "配置管理 › hooks"],
  ];
  for (const [title, nodes, crumb] of pages) {
    assert.equal(texts(collect(nodes, (node) => node.tag === "h2")[0]), title);
    assert.equal(texts(collect(nodes, (node) => node.attrs?.class === "crumb")[0]), crumb);
    assert.equal(collect(nodes, (node) => node.attrs?.["aria-label"] === "能力类别").length, 0, title);
    assert.equal(texts({ children: nodes }).includes("能力 ·"), false, title);
  }
  assert.ok(collect(hooks, (node) => node.attrs?.["aria-label"] === "hooks 来源").length === 1);
});

test("evidence returns to skills and does not use the capability umbrella", () => {
  const evidence = renderEvidence({
    h,
    route: { source: "", q: "" },
    evidence: { ok: true, sources: [], items: [] },
    setRoute: noop,
  });
  assert.equal(texts(collect(evidence, (node) => node.tag === "h2")[0]), "证据清单");
  assert.equal(texts(collect(evidence, (node) => node.attrs?.class === "crumb")[0]), "配置管理 › skills › 证据清单");
  const body = texts({ children: evidence });
  assert.match(body, /返回 skills/);
  assert.equal(body.includes("返回能力"), false);
  assert.equal(body.includes("能力"), false);
});

test("visible sources drop the capability switch and umbrella labels", () => {
  const files = readdirSync(new URL("./", import.meta.url)).filter((name) => name.endsWith(".js") && !name.endsWith(".test.js"));
  for (const file of files) {
    const source = readFileSync(new URL(`./${file}`, import.meta.url), "utf8");
    assert.equal(source.includes("capabilitySwitch"), false, file);
    assert.equal(source.includes("返回能力"), false, file);
    assert.equal(source.includes("能力 ·"), false, file);
    assert.equal(source.includes("能力类别"), false, file);
    assert.equal(source.includes("COPY.capability"), false, file);
  }
});
