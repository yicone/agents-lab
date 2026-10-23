import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import {
  attentionBuckets, badgeOf, cellSymbol, commandsFor, listRows, loadMark,
  matrixModel, ownConfigLabel, renderMcp, renderMcpAgentDrawer, renderMcpCellDrawer,
  renderMcpServerDrawer, FILE_HINT,
} from "./mcp.js";

const here = new URL(".", import.meta.url).pathname;
const fx = (p) => JSON.parse(readFileSync(join(here, "fixtures", p), "utf8"));
const mcpFx = fx("mcp/mcp.json");
const emptyFx = fx("mcp-empty/mcp.json");
const partialFx = fx("mcp-partial/mcp.json");
const agentsFx = fx("agents.json");

function vnode(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children: children.flat(Infinity).filter((c) => c != null && c !== false) };
}
function texts(node, out = []) {
  if (node == null || node === false) return out;
  if (typeof node !== "object") { out.push(String(node)); return out; }
  for (const c of node.children || []) texts(c, out);
  return out;
}
const flatText = (nodes) => texts({ children: nodes }).join("\n");

const route = { ws: "", q: "", issue: "", mcpView: "", archived: false };
const ctx = (over = {}) => ({
  h: vnode, route, mcp: mcpFx, agentsPayload: agentsFx,
  setRoute() {}, ago: (t) => t, absTime: (t) => t, copyCommand() {},
  openMcpDrawer() {}, closeDrawer() {}, deleteConfirmed: "", ...over,
});

const ws1 = mcpFx.workspaces[0];
const agIgnored = mcpFx.agents.find((a) => a.name === "Steward");
const agShadow = mcpFx.agents.find((a) => a.name === "Reviewer" && a.workspace_id === ws1.id);
const srv = (id) => ws1.servers.find((s) => s.id === id);

test("badge and queue come from payload.attention; empty bucket shows items []", () => {
  assert.equal(badgeOf(mcpFx), mcpFx.attention.badge);
  const buckets = attentionBuckets(mcpFx);
  for (const b of buckets) assert.equal(b.count, (mcpFx.attention.buckets[b.id] || []).length);
  assert.equal(badgeOf(emptyFx), 0);
  assert.equal(attentionBuckets(emptyFx)[0].items.length, 0);
});

test("cellSymbol covers the full table and never renders unread as ·", () => {
  const bound = (over) => ({ runtime_loads: "yes", bindings_state: "ok", ...over });
  const ok = { ...ws1, bindings_state: "ok" };
  const linear = srv("srv-0001-linear");
  assert.equal(cellSymbol(bound({ bindings: [] }), linear, ok), "·");
  assert.equal(cellSymbol(bound({ bindings: [{ server_id: linear.id, enabled: false }] }), linear, ok), "○");
  assert.equal(cellSymbol(bound({ bindings: [{ server_id: linear.id, enabled: true, shadowed: true }] }), linear, ok), "◐");
  assert.equal(cellSymbol(bound({ runtime_loads: "no", bindings: [{ server_id: linear.id, enabled: true }] }), linear, ok), "⊘");
  assert.equal(cellSymbol(bound({ runtime_loads: "unbound", bindings: [{ server_id: linear.id, enabled: true }] }), linear, ok), "⊘");
  assert.equal(cellSymbol(bound({ runtime_loads: "conditional", bindings: [{ server_id: linear.id, enabled: true }] }), linear, ok), "!");
  assert.equal(cellSymbol(bound({ runtime_loads: "unknown", bindings: [{ server_id: linear.id, enabled: true }] }), linear, ok), "?");
  assert.equal(cellSymbol(bound({ bindings: [{ server_id: linear.id, enabled: true }] }), linear, ok), "●");
  assert.equal(cellSymbol(bound({ bindings_state: "error" }), linear, ok), "?");
  assert.equal(cellSymbol(bound({ bindings_state: "never" }), linear, ok), "?");
  assert.equal(cellSymbol(bound({}), linear, { ...ok, bindings_state: "never" }), "?");
  assert.equal(cellSymbol(agIgnored, linear, ws1), "⊘");   // fixture: antigravity ignored
  assert.equal(cellSymbol(agShadow, linear, ws1), "◐");   // fixture: hermes shadowed
});

test("loadMark / ownConfigLabel", () => {
  assert.equal(loadMark("yes"), "✓");
  assert.equal(loadMark("no"), "✕");
  assert.equal(loadMark("conditional"), "!");
  assert.equal(loadMark("unknown"), "?");
  assert.equal(loadMark("unbound"), "未绑定");
  assert.equal(ownConfigLabel({ state: "none", servers: [] }), "—");
  assert.equal(ownConfigLabel({ state: "managed", servers: [{}, {}] }), "2 个");
  assert.equal(ownConfigLabel({ state: "managed", servers: [] }), "托管·空");
  assert.equal(ownConfigLabel({ state: "hidden", servers: [] }), "看不到");
  assert.equal(ownConfigLabel({ state: "invalid", servers: [] }), "无法解析");
});

test("matrixModel: >8 servers falls back to list with a reason; restCount counts agents without MCP", () => {
  const model = matrixModel(mcpFx, agentsFx, route);
  const w1 = model.blocks.find((b) => b.workspace.id === ws1.id);
  const w2 = model.blocks.find((b) => b.workspace.slug === "beta-coach");
  assert.equal(w1.fallbackToList, false);
  assert.equal(w2.fallbackToList, true);
  assert.match(w2.reason, /超过 8 列/);
  assert.ok(w1.restCount >= 0);
});

test("listRows include own-config entries and unread rows", () => {
  const rows = listRows(mcpFx, route);
  assert.ok(rows.some((r) => r.source === "自有" && r.server === "linear"));
  assert.ok(rows.some((r) => r.source === "库" && r.server === "sentry"));
  const partial = listRows(partialFx, route);
  assert.ok(partial.some((r) => r.note.includes("读取失败") || r.enabled === null));
});

test("commandsFor emits only whitelisted forms, always with file templates and hint", () => {
  const ctxArgs = { workspace_id: "ws-uuid", agent_id: "ag-uuid", server_id: "srv-uuid", name: "linear", agent_name: "builder" };
  const kinds = ["assign", "enable", "disable", "unassign", "rename", "replace-config", "add-server", "delete-server", "own-config", "clear-own"];
  const forbidden = [/--server-config '/, /--mcp-config '\{/, /--server-config-stdin/, /--mcp-config-stdin/, /--mcp-config '\{/, /echo .*\|/, /--server-config [^-]/];
  for (const kind of kinds) {
    const cmds = commandsFor(kind, ctxArgs);
    assert.ok(cmds.length, kind);
    for (const c of cmds) {
      for (const bad of forbidden) assert.ok(!bad.test(c.command), `${kind}: ${c.command}`);
      assert.ok(!/agent mcp (add|enable|disable|remove) [^ ]+ [a-z]+$/i.test(c.command.replace(/uuid/g, "x")));
    }
  }
  assert.match(commandsFor("assign", ctxArgs)[0].command,
    /^multica --workspace-id ws-uuid agent mcp add ag-uuid srv-uuid$/);
  assert.match(commandsFor("clear-own", ctxArgs)[0].command, /--mcp-config null$/);
});

test("rendered drawers never contain forbidden command forms or env/headers 未设置 on server drawer", () => {
  const serverNodes = renderMcpServerDrawer(vnode, ctx({ deleteConfirmed: "" }), { server: srv("srv-0001-linear"), workspace: ws1 });
  const stext = flatText(serverNodes);
  for (const bad of ["--server-config '", "--mcp-config '{", "stdin", "未设置"]) {
    if (bad === "未设置") assert.ok(!/env \/ headers 未设置|无法判断 env \/ headers.*未设置/.test(stext) && stext.includes("无法判断 env / headers"), stext);
    else assert.ok(!stext.includes(bad), `${bad} in server drawer`);
  }
  const agentNodes = renderMcpAgentDrawer(vnode, ctx(), { agent: agShadow, workspace: ws1 });
  const atext = flatText(agentNodes);
  assert.ok(atext.includes("env 已设置 2 项"));
  assert.ok(atext.includes("headers 未设置"));
  assert.ok(atext.includes("自有"));
  for (const bad of ["--server-config '", "--mcp-config '{", "stdin"]) assert.ok(!atext.includes(bad));
  const cellNodes = renderMcpCellDrawer(vnode, ctx(), { agent: agIgnored, server: srv("srv-0001-linear"), workspace: ws1 });
  const ctext = flatText(cellNodes);
  assert.ok(ctext.includes("不会加载 MCP"));   // yellow note, command still shown
  assert.ok(ctext.includes("agent mcp"));
});

test("server drawer delete reveals the command only after confirmation", () => {
  const c1 = ctx({ deleteConfirmed: "" });
  const closed = flatText(renderMcpServerDrawer(vnode, c1, { server: srv("srv-0001-linear"), workspace: ws1 }));
  assert.ok(closed.includes("我知道会同时从这"));
  assert.ok(!closed.includes("workspace mcp remove"));
  const c2 = ctx({ deleteConfirmed: `${ws1.id}:srv-0001-linear` });
  const open = flatText(renderMcpServerDrawer(vnode, c2, { server: srv("srv-0001-linear"), workspace: ws1 }));
  assert.ok(open.includes("workspace mcp remove srv-0001-linear"));
});

test("renderMcp: absent renders 后端还没有 MCP 采集; never/error/partial never fake 0 or ·", () => {
  const absent = flatText(renderMcp(ctx({ mcp: { ok: false, absent: true } })));
  assert.ok(absent.includes("后端还没有 MCP 采集"));
  const empty = flatText(renderMcp(ctx({ mcp: emptyFx })));
  assert.ok(empty.includes("还没有 MCP server"));
  assert.ok(empty.includes("逐个分配"));
  const partial = flatText(renderMcp(ctx({ mcp: partialFx })));
  assert.ok(partial.includes("库读取失败") || partial.includes("timeout"));
  assert.ok(partial.includes("未加载"));
  const matrix = matrixModel(partialFx, agentsFx, route);
  const errWs = matrix.blocks.find((b) => b.workspace.library_state === "error");
  assert.ok(errWs.workspace.servers.length === 1); // stale rows kept
});

test("problems pseudo-bucket unions ignored+shadowed+may_fail agents", () => {
  const model = matrixModel(mcpFx, agentsFx, { ...route, issue: "problems" });
  const kept = new Set(model.blocks.flatMap((b) => b.rows.map((a) => a.id)));
  for (const bucket of ["ignored", "shadowed", "may_fail"]) {
    for (const item of mcpFx.attention.buckets[bucket] || []) {
      if (item.agent_id) assert.ok(kept.has(item.agent_id), `${bucket}: ${item.agent_id}`);
    }
  }
  const hiddenOnly = mcpFx.attention.buckets.hidden?.[0]?.agent_id;
  if (hiddenOnly) assert.ok(!kept.has(hiddenOnly));
  const clean = mcpFx.agents.find((a) => a.provider === "codex" && a.runtime_loads === "yes");
  assert.ok(!kept.has(clean.id)); // bound + enabled, not in any badge bucket
  const rows = listRows(mcpFx, { ...route, issue: "problems" });
  assert.ok(rows.length && rows.every((r) => kept.has(r.agent.id)));
});

test("fixtures: contract keys, badge math, no token-like strings", () => {
  for (const [dir, file] of [["", "mcp.json"], ["mcp", "mcp.json"], ["mcp-empty", "mcp.json"], ["mcp-partial", "mcp.json"]]) {
    const path = join(here, "fixtures", dir, file);
    const raw = readFileSync(path, "utf8");
    const data = JSON.parse(raw);
    for (const k of ["generated_at", "mcp_schema", "mcp_every_rounds", "support", "workspaces", "agents", "attention"]) {
      assert.ok(k in data, `${dir}/${file}: ${k}`);
    }
    const c = data.attention.counts;
    assert.equal(data.attention.badge, (c.ignored || 0) + (c.shadowed || 0) + (c.may_fail || 0));
    assert.doesNotMatch(raw, /(mul_|sk-|ghp_|xox[bp]-|Bearer )/);
    assert.doesNotMatch(raw, /https?:\/\/[^/\s"]*:[^@\s"]*@/);
  }
});
