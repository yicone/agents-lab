import assert from "node:assert/strict";
import test from "node:test";
import { clientHashMatches, directoryBannerText, mapPlanDetail, mapPlanList, sha256Hex } from "./plan-api.js";
import { collectButtons, collectText, hashOk, renderPlanDrawer, renderPlanList, renderPlanNew, renderPlanReview, sortPlans, summaryCounts } from "./plan.js";

const NOW = Date.parse("2026-10-19T03:00:00Z");
const WS = "22222222-2222-4222-8222-222222222222";
const AGENT = "33333333-3333-4333-8333-333333333333";
const RUNTIME = "44444444-4444-4444-8444-444444444444";

function vnode(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children: children.flat(Infinity).filter((child) => child != null && child !== false) };
}

function b64(bytes) {
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text);
}

const ctx = {
  nowMs: NOW,
  ago: () => "刚刚",
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
  snapshotAge: "刚刚",
};

function row(extra = {}) {
  return {
    workspace_id: WS,
    agent_id: AGENT,
    agent_name: "隐藏行",
    archived: null,
    observed_at: "2026-10-17T08:00:00Z",
    source_version: null,
    source_fingerprint: "abc",
    source_runtime: { id: RUNTIME, name: "Claude", provider: "claude", status: "online", last_seen_at: "2026-10-17T08:00:00Z" },
    target_runtime: { id: RUNTIME, name: "Claude", provider: "claude", status: "online", last_seen_at: "2026-10-17T08:00:00Z" },
    fields: {
      runtime_id: { op: "set", old: RUNTIME, new: RUNTIME, old_known: true },
      model: { op: "set", old: null, new: "", old_known: true },
      thinking_level: { op: "set", old: "low", new: "low", old_known: true },
      service_tier: { op: "set", old: "flex", new: "default", old_known: false },
    },
    activity: { pages_read: 0, tasks_seen: 0, completeness: "UNKNOWN", source: "agent tasks", observed_at: "2026-10-17T08:00:00Z", runs: [] },
    checks: {
      activity: { status: "UNKNOWN", source: "agent tasks", observed_at: "2026-10-17T08:00:00Z" },
      permission: { status: "UNKNOWN", source: "unproven", observed_at: "2026-10-17T08:00:00Z" },
      capability: { status: "UNKNOWN", source: "unproven", observed_at: "2026-10-17T08:00:00Z" },
      compatibility: { status: "UNKNOWN", source: "unproven", observed_at: "2026-10-17T08:00:00Z" },
      health: { status: "PASS", source: "runtime list", observed_at: "2026-10-17T08:00:00Z" },
    },
    decision: "BLOCKED",
    reasons: ["BLOCKED_HISTORICAL", "BLOCKED_HEALTH"],
    impact: ["只读计划"],
    ...extra,
  };
}

function payload(bytes, hash, verified, snapshot = "historical") {
  return {
    generated_at: null,
    hash_verified: verified,
    hash_algo: "sha256-jcs-schema1",
    canonical_payload_b64: b64(bytes),
    apply_enabled: true,
    expired: false,
    gates: {
      g1: { activity: "UNKNOWN", permission: "UNKNOWN", capability: "UNKNOWN", compatibility: "UNKNOWN", health: "PASS" },
      g2: { approval_claim: "PASS", commit_fence: "absent", cas_activity_guard: "absent", operation_attribution: "absent" },
    },
    plan: {
      plan_hash: hash,
      hash_algo: "sha256-jcs-schema1",
      payload: {
        schema_version: 1,
        policy_version: "g1-v1",
        mode: "review_only",
        plan_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        server_identity: null,
        actor: { type: "member", id: "11111111-1111-4111-8111-111111111111", verified: false },
        workspace_allow_list: [{ id: WS, name: null }],
        generated_at: "2026-10-19T01:00:00Z",
        expires_at: "2026-10-19T01:10:00Z",
        snapshot_kind: snapshot,
        rows: [row()],
        counts: { APPROVABLE: 0, BLOCKED: 1, SKIP_ACTIVE: 0, NO_CHANGE: 0 },
      },
    },
  };
}

test("client hash uses the exact decoded bytes and both checks must pass", async () => {
  const bytes = new Uint8Array([1, 2, 3, 255, 0, 65]);
  const hash = await sha256Hex(bytes);
  assert.equal(await clientHashMatches(b64(bytes), hash), true);
  const changed = new Uint8Array(bytes);
  changed[0] = 9;
  assert.equal(await clientHashMatches(b64(changed), hash), false);
  const matched = await mapPlanDetail(payload(bytes, hash, true));
  assert.equal(matched.plan.hash_verified, true);
  assert.equal(matched.plan.apply_enabled, false);
  const serverNo = await mapPlanDetail(payload(bytes, hash, false));
  assert.equal(serverNo.plan.hash_verified, false);
  assert.equal(hashOk(serverNo.plan), false);
});

test("historical health, unread tasks, null archive, and equal values follow the contract", async () => {
  const bytes = new Uint8Array([7, 8, 9]);
  const hash = await sha256Hex(bytes);
  const { plan } = await mapPlanDetail(payload(bytes, hash, true));
  const line = plan.rows[0];
  assert.equal(line.archived, null);
  assert.equal(line.evidence.health, "UNKNOWN");
  assert.equal(line.precheck.find((item) => item.id === "health").source, "historical_snapshot");
  assert.equal(line.fields.model.old, null);
  assert.equal(line.fields.model.next, "");
  assert.equal(plan.gates.g1.find((item) => item.id === "health").state, "unknown");
  assert.equal(plan.gates.g2.every((item) => item.state === "absent"), true);
  assert.equal(plan.server, null);

  const review = { children: renderPlanReview(vnode, { ...ctx, plan }) };
  const text = collectText(review).join("\n");
  assert.match(text, /隐藏行/);
  assert.match(text, /归档状态未知/);
  assert.match(text, /历史快照，仅供审阅/);
  assert.match(text, /<server>/);
  assert.match(text, /声明 · 未验证/);
  assert.match(text, /不变/);
  assert.match(text, /未知/);
  assert.match(text, /null/);
  assert.match(text, /""/);
  assert.match(text, /操作归因/);
  assert.match(text, /apply 关闭/);
  assert.doesNotMatch(text, /api\.multica\.ai/);
  assert.equal(collectButtons(review).some((label) => /执行|应用/.test(label)), false);

  const drawer = { children: renderPlanDrawer(vnode, { ...ctx, closeDrawer() {} }, line) };
  const drawerText = collectText(drawer).join("\n");
  assert.match(drawerText, /不表示没有任务/);
  assert.doesNotMatch(drawerText, /已穷尽|未穷尽|0 个任务/);
  assert.match(drawerText, /historical_snapshot/);
});

test("hash mismatch hides the row table even when the server marked it verified", async () => {
  const bytes = new Uint8Array([4, 5, 6]);
  const { plan } = await mapPlanDetail(payload(bytes, "ab".repeat(32), true));
  assert.equal(hashOk(plan), false);
  const review = { children: renderPlanReview(vnode, { ...ctx, plan }) };
  const text = collectText(review).join("");
  assert.match(text, /勿据此审阅/);
  assert.equal(text.includes("隐藏行"), false);
});

test("list uses summary counts, keeps an incomplete directory visible, and does not call an empty dir empty of plans when the read failed", () => {
  const mapped = mapPlanList({
    generated_at: null,
    complete: false,
    directory_error: "directory_limit",
    items: [
      {
        plan_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        generated_at: null,
        expires_at: null,
        mode: null,
        schema_version: null,
        server_identity: null,
        snapshot_kind: null,
        observed_at: null,
        workspace_allow_list: null,
        counts: null,
        agent_count: null,
        plan_hash: null,
        hash_verified: false,
        parse_error: "invalid_schema",
        expired: false,
      },
      {
        plan_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        generated_at: "2026-10-19T01:00:00Z",
        expires_at: "2026-10-19T01:10:00Z",
        mode: "review_only",
        schema_version: 1,
        server_identity: null,
        snapshot_kind: "live_cli",
        observed_at: "2026-10-19T01:00:00Z",
        workspace_allow_list: [{ id: WS, name: "ACM Lab" }],
        counts: { APPROVABLE: 0, BLOCKED: 2, SKIP_ACTIVE: 1, NO_CHANGE: 0 },
        agent_count: 3,
        plan_hash: "abc123abc123abc123abc123abc123abc123abc123abc123abc123abc123abcd",
        hash_verified: true,
        parse_error: null,
        expired: true,
      },
    ],
  });
  assert.equal(mapped.generated_at, null);
  assert.equal(mapped.complete, false);
  const ordered = sortPlans(mapped.plans);
  assert.equal(ordered[0].plan_id.startsWith("aaaaaaaa"), true);
  assert.equal(summaryCounts(ordered[0]).blocked, 2);
  assert.equal(ordered[0].agent_count, 3);
  const tree = { children: renderPlanList(vnode, { ...ctx, plans: mapped.plans, planComplete: false, directoryError: "directory_limit" }) };
  const text = collectText(tree).join("\n");
  assert.match(text, /不是全量/);
  assert.match(text, /可能缺少最新计划/);
  assert.match(text, /无法解析/);
  assert.match(text, /结构或版本不符/);
  assert.match(text, /已过期/);
  assert.match(text, /阻断 2/);
  assert.equal(directoryBannerText("permissions").includes("不能当成全量"), true);

  const empty = { children: renderPlanList(vnode, { ...ctx, plans: [], planComplete: true, directoryError: null }) };
  assert.match(collectText(empty).join(""), /还没有计划/);
  const failed = { children: renderPlanList(vnode, { ...ctx, plans: [], planComplete: false, directoryError: "permissions" }) };
  assert.match(collectText(failed).join(""), /不能当成没有计划/);
  assert.doesNotMatch(collectText(failed).join(""), /还没有计划/);
});

test("a missing server identity stays a yellow placeholder in the local command", () => {
  const tree = {
    children: renderPlanNew(vnode, {
      ...ctx,
      serverIdentity: null,
      selected: [{ id: AGENT, name: "A", workspace_id: WS, workspace_name: "ACM Lab" }],
      allow: [{ id: WS, name: "ACM Lab" }],
      runtimes: [{ id: RUNTIME, name: "Claude", provider: "claude", workspace_id: WS, status: "online" }],
      route: { ...ctx.route, rt: RUNTIME },
    }),
  };
  const text = collectText(tree).join("\n");
  assert.match(text, /<server>/);
  assert.doesNotMatch(text, /api\.multica\.ai/);
  assert.equal(JSON.stringify(tree).includes('"class":"tag warn"'), true);
});
