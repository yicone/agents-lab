import assert from "node:assert/strict";
import test from "node:test";
import { applyPatch, formatHash, historyWrite, parseHash } from "./route.js";
import {
  agentOrigin,
  assessFocus,
  describeOrigin,
  focusFailureError,
  mcpForSkill,
  mcpRef,
  parentRoute,
  parseOrigin,
  resolveMcp,
  resolveSkill,
  skillOrigin,
  skillsForMcp,
  unwindFocus,
} from "./deep-link.js";

const agent = {
  id: "agent-1",
  workspace_id: "ws-1",
  name: "Repo Analyst",
  archived: false,
  skills: [{ id: "skill-1", name: "colgrep-search", enabled: true }],
};
const other = { id: "agent-2", workspace_id: "ws-1", name: "Other", skills: [{ name: "other-skill", enabled: true }] };
const mcp = {
  id: "hmac-salted-id",
  display_name: "github",
  workspace_entries: [{ kind: "library", workspace_id: "ws-1", server_id: "srv/github", agents: [{ id: "agent-1", enabled: true }] }],
};
const unrelated = {
  id: "other-hmac",
  display_name: "solo",
  workspace_entries: [{ kind: "library", workspace_id: "ws-1", server_id: "srv-solo", agents: [{ id: "agent-2", enabled: true }] }],
};

test("skill focus round-trips and does not disturb other views", () => {
  const route = parseHash("#/configure/skills?focus=colgrep-search&from=agent%2Fws-1%2Fagent-1");
  assert.equal(route.view, "skills");
  assert.equal(route.focus, "colgrep-search");
  assert.equal(route.origin, "agent/ws-1/agent-1");
  assert.equal(formatHash(route), "#/configure/skills?focus=colgrep-search&from=agent%2Fws-1%2Fagent-1");
  const agents = parseHash("#/running?from=plan-1");
  assert.equal(agents.focus, "");
  assert.equal(agents.origin, "");
  assert.equal(agents.fromPlan, "plan-1");
});

test("focus changes push one history step and leaving clears the focus", () => {
  const current = "#/configure/skills?focus=colgrep-search";
  const next = formatHash({ ...parseHash(current), focus: "other-skill" });
  assert.equal(historyWrite(current, next, { focus: "other-skill" }), "push");
  const left = applyPatch(parseHash(current), { view: "map" });
  assert.equal(left.focus, "");
  assert.equal(left.origin, "");
  const kept = applyPatch(parseHash(current), { view: "mcp", focus: "ws/ws-1/srv/github", origin: skillOrigin("colgrep-search") });
  assert.equal(kept.focus, "ws/ws-1/srv/github");
  assert.equal(kept.origin, "skill/colgrep-search");
});

test("MCP links use the workspace server id, not the process id", () => {
  const ref = mcpRef(mcp);
  assert.equal(ref.includes("hmac-salted-id"), false);
  assert.equal(ref, "ws/ws-1/srv/srv%2Fgithub");
  assert.equal(resolveMcp([mcp, unrelated], ref), mcp);
  assert.equal(resolveMcp([mcp], mcp.id), null);
  const named = mcpRef({ display_name: "local only", workspace_entries: [] });
  assert.equal(named, "name/local%20only");
});

test("MCP focus matches every library entry, not the first and not the process id", () => {
  const item = {
    id: "hmac-old",
    display_name: "github",
    workspace_entries: [
      { kind: "library", workspace_id: "ws-1", server_id: "srv-first", agents: [] },
      { kind: "library", workspace_id: "ws-1", server_id: "srv-github", agents: [{ id: "agent-1", enabled: true }] },
    ],
  };
  const restarted = { ...item, id: "hmac-new", workspace_entries: [...item.workspace_entries].reverse() };
  const second = "ws/ws-1/srv/srv-github";
  const first = "ws/ws-1/srv/srv-first";
  assert.equal(resolveMcp([item], second), item);
  assert.equal(resolveMcp([restarted], second), restarted);
  assert.equal(resolveMcp([item], first), item);
  assert.equal(resolveMcp([item], "hmac-old"), null);
  assert.equal(mcpForSkill("colgrep-search", [agent], [item]).map((link) => link.ref).includes(first), false);
  assert.equal(mcpForSkill("colgrep-search", [agent], [item])[0].ref, second);
});

test("skill and MCP links are shared agent bindings", () => {
  const links = mcpForSkill("colgrep-search", [agent, other], [mcp, unrelated]);
  assert.deepEqual(links.map((link) => link.label), ["github"]);
  assert.equal(mcpForSkill("missing", [agent], [mcp]).length, 0);
  const back = skillsForMcp(mcp, [agent, other]);
  assert.deepEqual(back.map((link) => link.name), ["colgrep-search"]);
  assert.equal(skillsForMcp(unrelated, [agent, other])[0].name, "other-skill");
});

test("failures stay on the requested object", () => {
  assert.equal(assessFocus({ focus: "colgrep-search", payload: null, error: null, item: null }).state, "loading");
  assert.equal(assessFocus({ focus: "colgrep-search", payload: { ok: false }, error: { code: "forbidden" }, item: { name: "other" } }).state, "forbidden");
  assert.equal(assessFocus({ focus: "colgrep-search", payload: { ok: false }, error: { code: "permission_denied" }, item: null }).state, "forbidden");
  const http403 = focusFailureError({ status: 403, code: "permission_denied", message: "skills: 403" });
  assert.equal(http403.status, 403);
  assert.equal(assessFocus({ focus: "colgrep-search", payload: { ok: false }, error: { ...http403, code: http403.code || "skills_unavailable" }, item: null }).state, "forbidden");
  assert.equal(assessFocus({ focus: "colgrep-search", payload: { ok: false }, error: { code: "skills_unavailable", status: 403, message: "skills: 403" }, item: null }).state, "forbidden");
  assert.equal(assessFocus({ focus: "colgrep-search", payload: { ok: false }, error: { code: "skills_unavailable", status: 500, message: "down" }, item: null }).state, "network");
  assert.equal(assessFocus({ focus: "colgrep-search", payload: { ok: false }, error: { code: "network" }, item: null }).state, "network");
  const missing = assessFocus({ focus: "gone", payload: { ok: true }, error: null, item: null });
  assert.equal(missing.state, "missing");
  assert.match(missing.detail, /gone/);
  assert.equal(assessFocus({ focus: "old", payload: { ok: true }, error: null, item: { name: "old", archived: true } }).state, "archived");
  assert.equal(assessFocus({ focus: "colgrep-search", payload: { ok: true }, error: null, item: { name: "colgrep-search" } }), null);
});

test("direct-url return walks one origin layer", () => {
  const skill = parentRoute({ view: "skills", focus: "colgrep-search", origin: agentOrigin(agent) });
  assert.equal(skill.view, "agents");
  assert.equal(skill.sel, "agent-1");
  assert.equal(skill.focus, "");
  const mcpRoute = parentRoute({ view: "mcp", focus: mcpRef(mcp), origin: skillOrigin("colgrep-search") });
  assert.equal(mcpRoute.view, "skills");
  assert.equal(mcpRoute.focus, "colgrep-search");
  assert.equal(describeOrigin(agentOrigin(agent), [agent]), "来自 Agent Repo Analyst");
  assert.equal(parseOrigin(skillOrigin("a/b")).name, "a/b");
  assert.equal(resolveSkill([{ name: "colgrep-search" }, { name: "other-skill" }], "colgrep-search").name, "colgrep-search");
  const directMcp = { view: "mcp", focus: mcpRef(mcp), origin: skillOrigin("colgrep-search") };
  const trail = unwindFocus(directMcp);
  assert.equal(trail.cycle, false);
  assert.deepEqual(trail.steps.at(-1).split("|")[0], "agents");
  assert.equal(trail.steps.filter((step) => step.startsWith("mcp|")).length, 1);
});
