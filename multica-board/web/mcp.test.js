import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  badgeOf, badgeTitle, badgeTone, commandsFor, copyWithFallback, filteredItems,
  groupedItems, queueModel, renderMcp, renderMcpDrawer, sourceBlocks,
} from "./mcp.js";

const root = dirname(fileURLToPath(import.meta.url));
const fx = (name) => JSON.parse(readFileSync(join(root, "fixtures", name, "mcp.json"), "utf8"));
const mcpFx = fx("mcp");
const partialFx = fx("mcp-partial");
const unknownFx = fx("mcp-unknown");
const emptyFx = fx("mcp-empty");
const invalidFx = fx("mcp-invalid");
const warnFx = fx("mcp-warn");

function vnode(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children };
}
function flatText(node) {
  if (node == null || node === false) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flatText).join("");
  return flatText(node.children || []);
}
function findAll(node, pred, out = []) {
  if (Array.isArray(node)) node.forEach((n) => findAll(n, pred, out));
  else if (node && typeof node === "object") {
    if (pred(node)) out.push(node);
    (node.children || []).forEach((n) => findAll(n, pred, out));
  }
  return out;
}
const route = { source: "", mcpStatus: "", tool: "", mcpGroup: "source", mcpAssigned: false, mcpTidy: false, q: "" };
const ctx = (over = {}) => ({
  h: vnode, route, mcp: mcpFx, setRoute: () => {}, ago: (s) => s, absTime: (s) => s,
  copyWithFallback: (t) => copyWithFallback(t), openMcp: () => {}, focusMcp: () => {},
  toggleMcpGroup: () => {}, expandedMcp: new Set(), drawerMcp: "", closeDrawer: () => {},
  ...over,
});

// §14 front: filteredItems
test("filteredItems: source/status/tool/assigned/tidy/q", () => {
  const items = filteredItems(mcpFx, route);
  assert.ok(items.length >= 10);
  assert.ok(filteredItems(mcpFx, { ...route, source: "local_only" }).every((i) => i.source_class === "local_only"));
  assert.ok(filteredItems(mcpFx, { ...route, source: "project" }).every((i) => i.in_project));
  assert.ok(filteredItems(mcpFx, { ...route, mcpStatus: "differs" }).every((i) => i.consistency.state === "differs"));
  assert.ok(filteredItems(mcpFx, { ...route, tool: "cursor" }).every((i) => ["direct", "off", "missing"].includes(i.tools.cursor)));
  assert.ok(filteredItems(mcpFx, { ...route, tool: "repo" }).every((i) => Object.values(i.repo_cells).some((v) => ["direct", "off", "missing"].includes(v))));
  assert.ok(filteredItems(mcpFx, { ...route, mcpAssigned: true }).every((i) =>
    i.workspace_entries.some((w) => w.kind === "library" && (w.agents || []).length)));
  assert.ok(filteredItems(mcpFx, { ...route, mcpTidy: true }).every((i) => i.tidy));
  assert.equal(filteredItems(mcpFx, { ...route, q: "acme" }).length, 2); // display name
  assert.equal(filteredItems(mcpFx, { ...route, q: "beta-coach" }).length >= 1, true); // ws slug
  assert.ok(filteredItems(mcpFx, { ...route, q: ".cursor" }).length >= 2); // path
  // never row ids nor fingerprint group letters: a synthetic row named "zz" in group "zzq"
  const stub = { ...mcpFx, items: [{ ...mcpFx.items[0], id: "mzzzzzzzzzzzzzzzzzzzq", display_name: "zz", entries: [{ ...mcpFx.items[0].entries[0], group: "zzq" }], workspace_entries: [] }] };
  assert.equal(filteredItems(stub, { ...route, q: "zzq" }).length, 0);
  assert.equal(filteredItems(stub, { ...route, q: "mzzzzzzzzzzzzzzzzzzzq" }).length, 0);
});

// §14 front: groupedItems + group note
test("groupedItems: tool grouping repeats rows and marks the note", () => {
  const byTool = groupedItems(filteredItems(mcpFx, route), "tool", mcpFx.tools, mcpFx);
  const github = byTool.find((g) => g.id === "cursor").items.find((i) => i.display_name === "github");
  assert.ok(github && byTool.find((g) => g.id === "codex").items.includes(github));
  assert.ok(byTool.every((g) => g.id === "multica_only" || g.note === "同一 server 可能重复出现"));
  const bySource = groupedItems(filteredItems(mcpFx, route), "source", mcpFx.tools, mcpFx);
  assert.deepEqual(bySource.map((g) => g.id), ["both", "local_only", "multica_only"]);
});

// §14 front: queueModel partial/?/connected
test("queueModel: counts, partial ?, connected —, tidy", () => {
  const q = queueModel(mcpFx);
  assert.deepEqual(q.buckets.map((b) => b.id), ["broken", "ignored", "shadowed", "may_fail", "drift"]);
  assert.equal(q.buckets[0].countText, "1");
  assert.equal(q.tidy.countText, "2");
  const p = queueModel(partialFx);
  assert.equal(p.buckets[0].countText, "0 ?");
  const u = queueModel(unknownFx);
  assert.equal(u.buckets[0].connected, false);
  assert.equal(u.buckets[0].countText, "—");
});

// §14 front: cell symbols, unknown beats not_applicable
test("cell symbols incl. unknown > not_applicable and display_dup", () => {
  const nodes = renderMcp(ctx());
  const rows = findAll(nodes, (n) => n.tag === "tr" && (n.attrs.class || "").includes("skill-row"));
  const dup = rows.find((r) => findAll(r, (n) => flatText(n).includes("同显示名 1/2")).length);
  assert.ok(dup, "display_dup suffix rendered");
  const pnodes = renderMcp(ctx({ mcp: partialFx }));
  const cells = findAll(pnodes, (n) => n.tag === "td" && n.attrs.class?.includes("tool-mark"));
  const texts = cells.map((c) => flatText(c));
  assert.ok(texts.includes("?"));
  assert.ok(!texts.includes("∅"), "no ∅ anywhere when a source is unknown");
  const unodes = renderMcp(ctx({ mcp: unknownFx }));
  assert.ok(flatText(unodes).includes("所有来源都没读到"));
});

test("badge tone 4 states + title text", () => {
  assert.equal(badgeTone(mcpFx), "bad");
  assert.equal(badgeTone(warnFx), "warn");
  assert.equal(badgeTone(unknownFx), "unknown");
  assert.equal(badgeTone(emptyFx), "none");
  assert.equal(badgeOf(mcpFx), mcpFx.attention.badge);
  assert.match(badgeTitle(mcpFx), /MCP 待处理 6 条：坏了 4 · 待批准 2/);
  assert.match(badgeTitle(unknownFx), /个来源没读到，是否有待处理未确定/);
  const partialTitle = badgeTitle({ attention: { tone: "warn", badge: 1, partial: true, counts: { drift: 1 } }, unknown: { local_files: ["x"] } });
  assert.match(partialTitle, /有 1 个来源没读到，可能不全/);
});

test("empty/absent/invalid pages", () => {
  assert.ok(flatText(renderMcp(ctx({ mcp: emptyFx }))).includes("还没有 MCP server"));
  assert.ok(flatText(renderMcp(ctx({ mcp: { ok: false, absent: true } }))).includes("后端还没有 MCP 采集"));
  const inv = flatText(renderMcp(ctx({ mcp: invalidFx })));
  assert.ok(inv.includes("MCP 数据无效"));
});

test("drawer: tri-state then the existing sections, readonly line only in command areas", () => {
  const item = mcpFx.items.find((i) => i.display_name === "github");
  const nodes = renderMcpDrawer(ctx(), item);
  const subs = findAll(nodes, (n) => n.tag === "h3").map((n) => flatText(n));
  assert.deepEqual(subs, ["能力三态", "来源与范围", "一致性", "本机工具配置", "仓库项目级", "Workspace（跨 Workspace）", "在终端处理"]);
  assert.match(flatText(nodes), /声明了/);
  assert.match(flatText(nodes), /未知/);
  const text = flatText(nodes);
  const needle = "页面只读，不代为执行。复制命令后在自己的终端运行。";
  assert.equal(text.split(needle).length - 1, 2, "once per command area (Multica + terminal)");
  assert.ok(!flatText(renderMcp(ctx())).includes(needle), "never at page top");
});

// §14 front: copyWithFallback three paths
test("copyWithFallback: clipboard ok / execCommand ok / both fail", async () => {
  assert.equal(await copyWithFallback("x", { clipboard: { writeText: async () => {} } }), true);
  const calls = [];
  const doc = {
    createElement: () => ({ value: "", style: {}, setAttribute() {}, select() {}, remove() {} }),
    body: { appendChild() {} },
    execCommand: (c) => { calls.push(c); return true; },
  };
  assert.equal(await copyWithFallback("x", { clipboard: { writeText: async () => { throw new Error("denied"); } }, document: doc }), true);
  assert.deepEqual(calls, ["copy"]);
  const docBad = { ...doc, execCommand: () => false };
  assert.equal(await copyWithFallback("x", { clipboard: null, document: docBad }), false);
});

// commands never carry values
test("commands are file-form only, all with --workspace-id", () => {
  const ctxArgs = { workspace_id: "w-uuid", agent_id: "a-uuid", server_id: "s-uuid", name: "n", agent_name: "an" };
  const forbidden = [/--server-config '/, /--mcp-config '\{/, /--server-config-stdin/, /--mcp-config-stdin/];
  for (const kind of ["assign", "enable", "disable", "unassign", "rename", "replace-config", "add-server", "delete-server", "own-config", "clear-own"]) {
    for (const c of commandsFor(kind, ctxArgs)) {
      assert.ok(c.command.includes("--workspace-id w-uuid"), c.command);
      for (const bad of forbidden) assert.ok(!bad.test(c.command), c.command);
    }
  }
});

// §14 fixtures: sentinel sweep over generated scenario JSONs
test("fixtures contain no secrets/tokens/emails/hex-blobs", () => {
  const dir = join(root, "fixtures");
  const scen = JSON.parse(readFileSync(join(dir, "scenarios.json"), "utf8"));
  const files = ["mcp", "mcp-empty", "mcp-partial", "mcp-unknown", "mcp-invalid", "mcp-warn"]
    .map((n) => join(dir, n, "mcp.json"));
  const banned = [/ghp_/, /sk-/, /xox/, /Bearer\s/i, /@[a-z0-9-]+\.[a-z]/i, /\b[0-9a-f]{32,}\b/i, /\b[A-Za-z0-9+/]{40,}={0,2}\b/];
  for (const f of files) {
    const text = readFileSync(f, "utf8");
    for (const re of banned) assert.ok(!re.test(text), `${f}: ${re}`);
    const payload = JSON.parse(text);
    if (payload.items?.length) {
      for (const item of payload.items) assert.match(item.id, /^m[0-9a-z]{20}(-\d+)?$/);
    }
  }
  for (const n of ["mcp", "mcp-empty", "mcp-partial", "mcp-unknown", "mcp-invalid", "mcp-warn", "mcp-absent"]) {
    assert.ok(scen[n], `scenarios.json has ${n}`);
  }
});

test("no .mcp- selectors remain in styles.css or JS", () => {
  const css = readFileSync(join(root, "styles.css"), "utf8");
  assert.ok(!/\.mcp-/.test(css));
  for (const f of ["mcp.js", "app.js"]) {
    const js = readFileSync(join(root, f), "utf8");
    // class names like "mcp-card"/"mcp-queue" are banned; element ids like "mcp-row-" are fine
    const classes = [...js.matchAll(/class:\s*["'`]([^"'`]+)["'`]/g)].flatMap((m) => m[1].split(/\s+/));
    assert.ok(!classes.some((c) => c.startsWith("mcp-")), `${f}: ${classes.filter((c) => c.startsWith("mcp-"))}`);
  }
});

// ACM-454: assert rendered counts, not only the model or the all-sources card.
const cardsOf = (payload) => findAll(renderMcp(ctx({ mcp: payload })),
  (n) => n.tag === "button" && (n.attrs.class || "").split(" ").includes("source-card"));
const cardOf = (payload, label) => cardsOf(payload).find((n) =>
  flatText(n.children[0]).startsWith(label));
const cardCount = (card) => flatText(findAll(card, (n) => n.tag === "strong")[0]);

test("source cards keep partial counts and backend uncertain rows, including a known zero", () => {
  const local = cardOf(partialFx, "仅本机");
  assert.equal(cardCount(local), "1");
  assert.match(flatText(local), /含 1 行待确认/);
  assert.equal(findAll(local, (n) => flatText(n) === "含 1 行待确认").at(-1).attrs.class, "muted");
  assert.equal(cardCount(cardOf(partialFx, "仅 Workspace")), "0");
  // Opposite-side failures alone must not replace a confirmed zero.
  const zero = structuredClone(partialFx);
  zero.summary.find((b) => b.id === "local_only").count = 0;
  zero.summary.find((b) => b.id === "local_only").uncertain = 0;
  assert.equal(cardCount(cardOf(zero, "仅本机")), "0");
  const remote = structuredClone(partialFx);
  Object.assign(remote.summary.find((b) => b.id === "multica_only"), { count: 3, uncertain: 2 });
  assert.equal(cardCount(cardOf(remote, "仅 Workspace")), "3");
  assert.match(flatText(cardOf(remote, "仅 Workspace")), /含 2 行待确认/);
});

test("project partial reads preserve discovered counts and show uncertainty", () => {
  const payload = structuredClone(mcpFx);
  payload.unknown.project_files = [".cursor"];
  payload.repos[0].files[1].state = "denied";
  const card = cardOf(payload, "仓库项目级");
  assert.equal(cardCount(card), String(payload.items.filter((i) => i.in_project).length));
  assert.match(flatText(card), /\? 有项目级来源没读到/);
  assert.equal(cardCount(cardOf(partialFx, "仓库项目级")), "0");
});

test("unread sources use a dash while fully read empty sources show zero", () => {
  assert.ok(cardsOf(unknownFx).every((card) => cardCount(card) === "—"));
  assert.ok(cardsOf(emptyFx).every((card) => cardCount(card) === "0"));
});

test("queue zero counts are muted, partial question marks and nonzero tones survive", () => {
  for (const payload of [emptyFx, warnFx, partialFx, unknownFx, mcpFx]) {
    const buckets = queueModel(payload).buckets;
    const sections = findAll(renderMcp(ctx({ mcp: payload })),
      (n) => n.tag === "section" && n.attrs.class === "skill-bucket");
    for (const [index, section] of sections.entries()) {
      const count = findAll(section, (n) => n.attrs.class?.startsWith("count "))[0];
      const bucket = buckets[index];
      assert.equal(flatText(count), bucket.countText);
      const tone = !bucket.connected || bucket.items.length === 0 ? "muted"
        : bucket.tone === "bad" ? "bad-n" : "warn-n";
      assert.equal(count.attrs.class, `count ${tone}`);
    }
  }
});

test("invalid MCP badge explains the failure even without detail", () => {
  assert.equal(badgeTone(invalidFx), "bad");
  assert.match(badgeTitle(invalidFx), /mcp-sources.json 无效/);
  assert.match(badgeTitle({ok:false}), /来源内容未通过校验/);
});
