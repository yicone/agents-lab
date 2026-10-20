import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  HERMES_SKILL,
  agentQueryFields,
  attentionSummary,
  botAttention,
  botGroupName,
  botQueryFields,
  compareGroups,
  dutyExcerpt,
  driftStatus,
  instructionCommand,
  instructionStat,
  lineDiff,
  missingHermesSkill,
  renderAgentDrawer,
  renderBotDrawer,
  renderBotPage,
  skillCommands,
  usageParts,
} from "./agents-view.js";

function h(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children: children.flat() };
}

function walk(node, visit) {
  if (node == null || node === false || typeof node !== "object") return;
  visit(node);
  for (const child of node.children || []) walk(child, visit);
}

function textOf(node) {
  if (node == null || node === false) return "";
  if (typeof node !== "object") return String(node);
  return (node.children || []).map(textOf).join("");
}

test("instruction length is empty, or characters plus a token estimate", () => {
  assert.equal(instructionStat(null).empty, true);
  assert.equal(instructionStat("  ").empty, true);
  const stat = instructionStat("一二三四五");
  assert.equal(stat.chars, 5);
  assert.equal(stat.tokens, 2);
});

test("Hermes without the delegation skill is flagged, and other runtimes are not", () => {
  const bare = { archived: false, skills: [{ id: "skill-review", name: "pr-review-fix", enabled: true }] };
  assert.equal(missingHermesSkill(bare, "Hermes"), true);
  assert.equal(missingHermesSkill({ ...bare, archived: true }, "hermes"), false);
  assert.equal(missingHermesSkill(bare, "cursor"), false);
  assert.equal(missingHermesSkill({ archived: false, skills: [{ id: "x", name: HERMES_SKILL, enabled: true }] }, "hermes"), false);
  assert.equal(missingHermesSkill({ archived: false, skills: [{ id: HERMES_SKILL, name: "other", enabled: false }] }, "hermes"), true);
});

test("search fields include description, instructions, skill names, and workspace", () => {
  const agent = { name: "Repo", description: "阅读仓库", instructions: "用 colgrep-search", model: null, skills: [{ name: "colgrep-search", id: "skill-colgrep" }] };
  const fields = agentQueryFields(agent, { name: "Alpha Lab", slug: "alpha-lab" }, { name: "Claude", provider: "claude" });
  assert.ok(fields.includes("阅读仓库"));
  assert.ok(fields.includes("用 colgrep-search"));
  assert.match(fields.join("\n"), /colgrep-search/);
  assert.ok(fields.includes("alpha-lab"));
});

test("copied commands use the real update and skill-id flags", () => {
  const agent = { id: "agent-1", instructions: "一行\n第二行", skills: [{ id: "skill-a", name: "alpha", enabled: true }, { id: "skill-b", name: "beta", enabled: false }] };
  const command = instructionCommand(agent);
  assert.match(command, /^multica agent update agent-1 --instructions/);
  assert.match(command, /一行\n第二行/);
  assert.deepEqual(skillCommands(agent), [
    "multica agent skills add agent-1 --skill-ids skill-a",
    "multica agent skills set agent-1 --skill-ids skill-a",
  ]);
});

test("duty text keeps 60 characters and empty copy is marked", () => {
  assert.equal(dutyExcerpt("").empty, true);
  assert.equal(dutyExcerpt(null).empty, true);
  const long = "字".repeat(61);
  assert.equal([...dutyExcerpt(long).text].length, 60);
  assert.equal(dutyExcerpt("短说明").text, "短说明");
});

test("bot groups stay in sidebar order and ungrouped is last", () => {
  assert.deepEqual(["未分组", "基建", "其他", "🔝", "D4L", "D4B"].sort(compareGroups), ["🔝", "D4B", "D4L", "基建", "其他", "未分组"]);
  assert.equal(botGroupName({ section_name: "D4B" }), "D4B");
  assert.equal(botGroupName({ section_name: "  " }), "未分组");
  assert.equal(botGroupName({}), "未分组");
});

test("unread and waiting come from the roster fields and stay unknown when absent", () => {
  assert.deepEqual(botAttention({ unread_count: 2, has_unread: true, awaiting_user_response: false }), { known: true, unread: 2, awaiting: false, hasUnread: true });
  assert.equal(botAttention({ awaiting_user_response: true }).awaiting, true);
  assert.equal(botAttention({ description: "x" }).known, false);
  assert.deepEqual(attentionSummary([{ unread_count: 1, awaiting_user_response: true }, { has_unread: false, awaiting_user_response: false }, {}]), {
    known: true,
    unreadBots: 1,
    waiting: 1,
  });
});

test("bot search sees description, routine, and linked workspace", () => {
  const fields = botQueryFields({
    name: "Agent Steward",
    description: "盯着待验收",
    bridge: { routine: "Multica 状态提醒", workspaces: [{ slug: "alpha-lab" }] },
  });
  assert.ok(fields.includes("盯着待验收"));
  assert.ok(fields.includes("Multica 状态提醒"));
  assert.ok(fields.includes("alpha-lab"));
});

test("weekly usage turns red at 100 percent", () => {
  const now = Date.parse("2026-10-14T09:00:00Z");
  const usage = usageParts({ percent_used: 100, plan: "Grok Bot Plan", next_reset_ms: now + 6 * 86400000, on_demand: { used_cents: 0, limit_cents: 100 } }, now);
  assert.equal(usage.tone, "bad");
  assert.match(usage.text, /100%/);
  assert.match(usage.text, /6 天后重置/);
  assert.equal(usageParts({ percent_used: 80 }, now).tone, "warn");
});

test("agent drawer renders instructions as text and bot rows keep duty and room labels", () => {
  const drawer = renderAgentDrawer(h, { closeDrawer() {}, copy() {}, setSection() {}, wsTag: null, extLink: h, provider: "hermes", recentWindowH: 24, ago: () => "", errKind: () => "" }, {
    section: "brief",
    agent: { id: "a", name: "Reviewer", description: "审阅", instructions: "<b>不要当 HTML</b>", skills: [], url: null },
  });
  const pre = [];
  for (const part of drawer) walk(part, (node) => { if (node.tag === "pre") pre.push(node); });
  assert.equal(pre.length, 1);
  assert.equal(pre[0].children[0], "<b>不要当 HTML</b>");
  assert.equal(textOf({ children: drawer }).includes("innerHTML"), false);

  const page = renderBotPage(h, {
    route: { ws: "alpha-lab", q: "", botLinked: false },
    payload: {
      ok: true,
      account_slot: "12345678",
      delivery: "shadow",
      weekly_usage: { percent_used: 100, plan: "Grok Bot Plan" },
      items: [
        { id: "room", name: "macOS 执委会", is_group: true, section_name: null, description: "一起看", member_ids: [], has_unread: false, unread_count: 0, awaiting_user_response: false, bridge: { workspaces: [], routine: null } },
        { id: "eo", name: "EO-PM", section_name: "D4B", description: "", has_unread: false, unread_count: 0, awaiting_user_response: false, bridge: { workspaces: [], routine: null } },
      ],
    },
    nowMs: Date.parse("2026-10-14T09:00:00Z"),
    ago: () => "1 小时前",
    absTime: () => "",
    matchesQuery: (q, ...fields) => !q || fields.some((field) => field && String(field).toLowerCase().includes(q.toLowerCase())),
    setRoute() {},
    kindSwitch: h("div"),
    selectedId: "",
    openBot() {},
  });
  const pageText = textOf({ children: page });
  assert.match(pageText, /Room/);
  assert.match(pageText, /未填职责说明/);
  assert.match(pageText, /12345678/);
  assert.match(pageText, /shadow/);
  assert.match(pageText, /D4B/);
  assert.match(pageText, /未分组/);
});

test("agents view source does not assign innerHTML", () => {
  const src = readFileSync(new URL("./agents-view.js", import.meta.url), "utf8");
  assert.doesNotMatch(src, /innerHTML/);
});

test("drawer shows a line diff and copies the Git source text", () => {
  assert.equal(driftStatus("abc", "abc", "live", "git"), "aligned");
  assert.equal(driftStatus("abc", "def", "live", "git"), "modified");
  assert.equal(driftStatus(null, null, "same", "same"), "aligned");
  assert.equal(driftStatus(null, null, "live", null), "untracked");
  const rows = lineDiff("alpha\nbeta", "alpha\nBETA\nmore");
  assert.deepEqual(rows.map((row) => `${row.op}${row.text}`), [" alpha", "-beta", "+BETA", "+more"]);

  const drawer = renderAgentDrawer(
    h,
    { closeDrawer() {}, copy() {}, setSection() {}, wsTag: null, extLink: h, provider: "cursor", recentWindowH: 24, ago: () => "", errKind: () => "", gitSource: { sha256: "def", text: "git <source>" } },
    { section: "brief", agent: { id: "a", name: "Reviewer", description: "", instructions: "live <text>", instructions_sha256: "abc", skills: [] } },
  );
  const text = textOf({ children: drawer });
  assert.match(text, /已修改/);
  assert.match(text, /复制 Git 真源/);
  assert.match(text, /- git <source>/);
  assert.match(text, /\+ live <text>/);
  const blocks = [];
  for (const part of drawer) walk(part, (node) => { if (String(node.attrs?.class || "").includes("diff-block")) blocks.push(node); });
  assert.equal(blocks.length, 1);
  assert.equal(JSON.stringify(blocks[0]).includes("innerHTML"), false);

  const bot = renderBotDrawer(
    h,
    { closeDrawer() {}, copy() {}, memberName: (id) => id, gitSource: { sha256: "same", text: "盯着待验收", skills: [] } },
    { bot: { id: "b", name: "Steward", description: "盯着待验收", description_sha256: "same", is_group: false, member_ids: [], bridge: {} } },
  );
  const botText = textOf({ children: bot });
  assert.match(botText, /一致/);
  assert.match(botText, /复制 Git 真源/);
  assert.match(botText, /云端 Skills：未在 Git 声明/);
});
