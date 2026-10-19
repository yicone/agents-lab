import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";
import {
  bucketCounts,
  buildCommand,
  collectButtons,
  collectText,
  composeTarget,
  columnHasMixedDefault,
  driftOf,
  findPlan,
  formatSel,
  hashOk,
  listStatus,
  outsideCount,
  planCatalog,
  reasonText,
  remain,
  renderPlanDrawer,
  renderPlanList,
  renderPlanNew,
  renderPlanReview,
  selectionLabel,
  tierSetAllowed,
  workspaceAllowList,
} from "./plan.js";

const NOW = Date.parse("2026-10-19T03:00:00Z");

function vnode(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children: children.flat(Infinity).filter((child) => child != null && child !== false) };
}

function walk(node, found = []) {
  if (!node || typeof node !== "object") return found;
  found.push(node);
  for (const child of node.children || []) walk(child, found);
  return found;
}

const ctxBase = {
  nowMs: NOW,
  ago: () => "12 分钟前",
  route: { model: "omit", effort: "omit", tier: "omit", rt: "", bucket: "", planFix: "", sel: "" },
  setRoute() {},
  copy() {},
  leave() {},
  openList() {},
  backToSelect() {},
  openPlan() {},
  toggleRaw() {},
  rawOpen: false,
  regenerate() {},
  openRow() {},
  closeDrawer() {},
  drawerAgentId: "",
  agentById: new Map(),
  snapshotAge: "12 分钟前",
  plans: planCatalog("", NOW),
};

test("historical fixture is 16 rows: 13 blocked, 3 skipped, none approvable", () => {
  const plan = findPlan(planCatalog("", NOW), "hist");
  const counts = bucketCounts(plan.rows);
  assert.equal(plan.rows.length, 16);
  assert.equal(counts.blocked, 13);
  assert.equal(counts.skip, 3);
  assert.equal(counts.approvable, 0);
  assert.equal(counts.unchanged, 0);
  assert.equal(counts.approvable + counts.blocked + counts.skip + counts.unchanged, plan.rows.length);
});

test("selection count stays explicit when the current filter hides rows", () => {
  assert.equal(outsideCount(["a", "b", "c"], ["a"]), 2);
  assert.equal(outsideCount(["a"], ["a", "b"]), 0);
  assert.equal(selectionLabel(3, 1, 1), "已选 3 个 agent · 1 个 workspace（其中 1 个不在当前筛选中）");
  assert.equal(selectionLabel(2, 1, 0), "已选 2 个 agent · 1 个 workspace（全部可见）");
  assert.equal(formatSel(["b", "a", "a"]), "b,a");
});

test("command lists every agent and workspace id and has no credential switch", () => {
  const agents = ["7c28ebcf-58eb-4791-a027-d3dd52e81d8e", "75b39498-04f8-4714-8b63-baf90462ee16"];
  const workspaces = ["e8659de2-7504-4ee2-9f0a-fd608a9a8bc2", "aa134a90-6b63-413d-8b4e-d4df0384aa16"];
  const command = buildCommand({
    server: "api.multica.ai",
    agentIds: agents,
    workspaceIds: workspaces,
    runtimeId: "dd26ccd7-7e22-4912-ac43-201f9595f31b",
    model: "omit",
    effort: "clear",
    tier: "omit",
  });
  assert.equal(command.ok, true);
  for (const id of [...agents, ...workspaces]) assert.match(command.text, new RegExp(id));
  assert.doesNotMatch(command.text, /--workspace\b|profile|token|secret|MULTICA_/i);
  assert.equal(buildCommand({ ...command, model: "set:  x", agentIds: agents, workspaceIds: workspaces, runtimeId: "rt" }).ok, false);
  assert.equal(tierSetAllowed("claude"), false);
  assert.equal(tierSetAllowed("codex"), true);
});

test("compose page marks cross-workspace scope and disables tier set off Codex", () => {
  const selected = [
    { id: "a", name: "A", workspace_id: "ws-a", workspace_name: "Alpha" },
    { id: "b", name: "B", workspace_id: "ws-b", workspace_name: "Beta" },
  ];
  assert.equal(workspaceAllowList(selected).length, 2);
  const tree = {
    children: renderPlanNew(vnode, {
      ...ctxBase,
      selected,
      runtimes: [{ id: "rt-c", name: "Claude", provider: "claude", workspace_id: "ws-a", status: "online" }],
      route: { ...ctxBase.route, rt: "rt-c", sel: "a,b" },
    }),
  };
  const text = collectText(tree).join("");
  assert.match(text, /跨 2 个 workspace/);
  assert.match(text, /该 workspace 没有此 runtime/);
  assert.match(text, /a/);
  assert.match(text, /b/);
  const tierSet = walk(tree).find((node) => node.tag === "input" && node.attrs.name === "tier" && node.attrs.disabled);
  assert.ok(tierSet);
  const labels = collectButtons(tree);
  assert.equal(labels.includes("复制"), true);
  assert.equal(labels.some((label) => /执行|应用|批准/.test(label)), false);
});

test("list keeps expired, hash mismatch, and unreadable plans, and empty dir has no table", () => {
  const plans = planCatalog("", NOW);
  const tree = { children: renderPlanList(vnode, { ...ctxBase, plans }) };
  const text = collectText(tree).join("\n");
  assert.match(text, /已过期/);
  assert.match(text, /hash 不符/);
  assert.match(text, /无法解析/);
  assert.match(text, /schema 未知/);
  const empty = { children: renderPlanList(vnode, { ...ctxBase, plans: planCatalog("empty", NOW) }) };
  assert.match(collectText(empty).join(""), /还没有计划/);
  assert.equal(walk(empty).some((node) => node.attrs?.class === "plan-table"), false);
  const expired = listStatus(findPlan(plans, "expired"), NOW);
  assert.equal(expired.text, "已过期");
});

test("hash mismatch hides the row table and null versus empty stays distinct", () => {
  const plans = planCatalog("", NOW);
  const mismatch = { children: renderPlanReview(vnode, { ...ctxBase, plan: findPlan(plans, "mismatch") }) };
  assert.equal(hashOk(findPlan(plans, "mismatch")), false);
  assert.equal(walk(mismatch).some((node) => node.attrs?.class === "plan-table"), false);
  assert.match(collectText(mismatch).join(""), /勿据此审阅/);
  assert.equal(collectText(mismatch).join("").includes("不该出现"), false);

  const blanks = findPlan(plans, "blanks");
  assert.equal(columnHasMixedDefault(blanks.rows, "model"), true);
  const review = { children: renderPlanReview(vnode, { ...ctxBase, plan: blanks }) };
  const text = collectText(review).join("");
  assert.match(text, /null/);
  assert.match(text, /""/);
  assert.equal(walk(review).some((node) => String(node.attrs?.class || "").includes("plan-mix")), true);
  const sum = walk(review).find((node) => node.attrs?.["data-bucket-sum"]);
  assert.equal(sum.attrs["data-bucket-sum"], sum.attrs["data-row-total"]);
  assert.match(text, /apply 关闭/);
  assert.match(text, /未接入|批准原子消费/);
  const labels = collectButtons(review).filter((label) => !label.startsWith("可批准"));
  assert.equal(labels.some((label) => /执行|应用|批准/.test(label)), false);
});

test("drift tag follows the atlas snapshot and unknown agents stay untagged", () => {
  const plan = findPlan(planCatalog("", NOW), "drift");
  const [known, missing] = plan.rows;
  assert.equal(driftOf(known, { runtime_id: "dd26ccd7-7e22-4912-ac43-201f9595f31b", model: null, thinking_level: null, service_tier: null }), true);
  assert.equal(driftOf(known, { runtime_id: "dd26ccd7-7e22-4912-ac43-201f9595f31b", model: "gpt-5.4", thinking_level: null, service_tier: null }), false);
  assert.equal(driftOf(missing, null), false);
  assert.equal(reasonText("BLOCKED_ACTIVITY_COMPLETENESS"), "活动未穷尽");
  assert.equal(reasonText("NO_SUCH_CODE"), "");
  const tree = {
    children: renderPlanReview(vnode, {
      ...ctxBase,
      plan,
      agentById: new Map([[known.agent_id, { runtime_id: "dd26ccd7-7e22-4912-ac43-201f9595f31b", model: null, thinking_level: null, service_tier: null }]]),
    }),
  };
  const tags = collectText(tree).join("");
  assert.equal(tags.split("现值已变").length - 1, 1);
});

test("row drawer shows raw null and empty string and does not render an approval section", () => {
  const row = findPlan(planCatalog("", NOW), "blanks").rows[0];
  const tree = { children: renderPlanDrawer(vnode, { ...ctxBase, closeDrawer() {} }, row) };
  const text = collectText(tree).join("\n");
  assert.match(text, /null/);
  assert.match(text, /不是 CAS/);
  assert.doesNotMatch(text, /G2 预留|批准区/);
  const close = walk(tree).find((node) => node.attrs?.class === "drawer-close");
  assert.equal(close.attrs["aria-label"], "关闭");
});

test("plan page does not call the network, and web scripts do not send writes", () => {
  const planSrc = readFileSync(new URL("./plan.js", import.meta.url), "utf8");
  const appSrc = readFileSync(new URL("./app.js", import.meta.url), "utf8");
  assert.doesNotMatch(planSrc, /\/api\/|fetch\s*\(/);
  assert.doesNotMatch(appSrc, /\/api\/plans/);
  const dir = new URL("./", import.meta.url);
  for (const name of readdirSync(dir)) {
    if (!name.endsWith(".js")) continue;
    const src = readFileSync(new URL(name, dir), "utf8");
    assert.doesNotMatch(src, /method\s*:\s*["'](?!GET|HEAD)[A-Z]+["']/);
  }
  assert.match(appSrc, /已归档/);
  assert.match(appSrc, /全选本 provider/);
  assert.match(appSrc, /kind === "plan"/);
  assert.match(appSrc, /opener\?\.focus\(\)/);
  assert.match(appSrc, /planAnchor/);
  assert.match(appSrc, /syncPlanClock/);
  assert.match(appSrc, /getElementById\(activeId\)\?\.focus\(\)/);
  assert.equal(appSrc.includes("planCatalog(state.route.planFix, Date.now())"), false);
  assert.doesNotMatch(appSrc, /planPage === "review"\) render\(\)/);
  assert.match(planSrc, /data-plan-clock/);
});

test("one fixture anchor keeps hist at 7:00 and mixed at 20:00 until the live clock moves", () => {
  const plans = planCatalog("", NOW);
  const hist = findPlan(plans, "hist");
  const mixed = findPlan(plans, "mixed");
  assert.equal(remain(hist.expires_at, NOW).clock, "剩 7:00");
  assert.equal(remain(mixed.expires_at, NOW).clock, "剩 20:00");
  const moved = remain(hist.expires_at, NOW + 2155);
  assert.equal(moved.expired, false);
  assert.equal(moved.clock, "剩 6:58");
  assert.notEqual(remain(mixed.expires_at, NOW + 2155).clock, "剩 20:00");
  const expiredAt = NOW + 7 * 60 * 1000 + 1;
  assert.equal(remain(hist.expires_at, expiredAt).expired, true);
  const tree = { children: renderPlanReview(vnode, { ...ctxBase, nowMs: expiredAt, plan: hist }) };
  assert.match(collectText(tree).join("\n"), /已过期/);
  const fresh = walk({ children: renderPlanReview(vnode, { ...ctxBase, nowMs: NOW, plan: hist }) });
  const clock = fresh.find((node) => node.attrs?.["data-plan-clock"]);
  assert.equal(clock.attrs["data-plan-clock"], "剩 7:00");
  assert.equal(clock.attrs["data-plan-expired"], "0");
});

test("regenerate restores a uniform plan and reports disagreement instead of the first row", () => {
  const hist = composeTarget(findPlan(planCatalog("", NOW), "hist"));
  assert.equal(hist.ok, true);
  assert.equal(hist.agents.length, 16);
  assert.equal(hist.allow[0].name, "ACM Lab");
  assert.equal(hist.runtime.id, "11111111-1111-4111-8111-111111111111");
  assert.equal(hist.runtime.workspace_id, "8ebc87ea-27f2-42c4-90f6-24a04e64d3f5");
  assert.equal(hist.model, "omit");
  assert.equal(hist.effort, "omit");
  assert.equal(hist.tier, "omit");
  const command = buildCommand({
    server: "api.multica.ai",
    agentIds: hist.agents.map((agent) => agent.id),
    workspaceIds: hist.allow.map((ws) => ws.id),
    runtimeId: hist.runtime.id,
    model: hist.model,
    effort: hist.effort,
    tier: hist.tier,
  });
  assert.equal(command.ok, true);
  assert.match(command.text, /11111111-1111-4111-8111-111111111111/);
  assert.match(command.text, /8ebc87ea-27f2-42c4-90f6-24a04e64d3f5/);

  const mixed = composeTarget(findPlan(planCatalog("", NOW), "mixed"));
  assert.equal(mixed.ok, false);
  assert.equal(mixed.runtime, null);
  assert.equal(mixed.model, "conflict");
  assert.match(mixed.errors.join("\n"), /没有改成第一行/);
  assert.equal(mixed.agents.some((agent) => agent.name === "可改甲"), true);
  assert.equal(mixed.agents.some((agent) => agent.name === "无变化"), true);
  assert.equal(mixed.allow[0].name, "ACM Lab");
  const tree = {
    children: renderPlanNew(vnode, {
      ...ctxBase,
      selected: mixed.agents,
      allow: mixed.allow,
      restoreErrors: mixed.errors,
      route: { ...ctxBase.route, rt: "", model: mixed.model, effort: mixed.effort, tier: mixed.tier },
    }),
  };
  const text = collectText(tree).join("\n");
  assert.match(text, /各行目标 runtime 不一致/);
  assert.match(text, /可改甲/);
  assert.match(text, /无变化/);
  assert.match(text, /ACM Lab/);

  const missing = composeTarget({
    plan_id: "gap",
    allow: [],
    rows: [{ agent_id: "missing-1", agent_name: "缺席", workspace_id: "", fields: {} }],
  });
  assert.equal(missing.ok, false);
  assert.equal(missing.agents[0].workspace_id, undefined);
  assert.match(missing.errors.join("\n"), /没有改成空 workspace/);
});
