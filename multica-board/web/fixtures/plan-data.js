/** Fixture plans for the G1 switch-plan UI. Not a live snapshot and not an API response. */

const ACM = { id: "8ebc87ea-27f2-42c4-90f6-24a04e64d3f5", name: "ACM Lab" };
const BETA = { id: "aa134a90-6b63-413d-8b4e-d4df0384aa16", name: "Beta 辅导" };
const CLAUDE = { id: "11111111-1111-4111-8111-111111111111", name: "Claude (tr-mini-m4)", provider: "claude", status: "online", workspace_id: ACM.id };
const CODEX = { id: "22222222-2222-4222-8222-222222222222", name: "Codex (mini)", provider: "codex", status: "online", workspace_id: ACM.id };
const HERMES = { id: "33333333-3333-4333-8333-333333333333", name: "Hermes", provider: "hermes", status: "offline", workspace_id: ACM.id };

const G1_UNKNOWN = [
  { id: "pagination", label: "分页完整性", state: "unknown" },
  { id: "permission", label: "权限判定", state: "unknown" },
  { id: "catalog", label: "模型目录", state: "unknown" },
  { id: "compat", label: "配置兼容", state: "unknown" },
];

function agentId(n) {
  return `10000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
}

function gates(pagination = "unknown") {
  return { g1: G1_UNKNOWN.map((item) => (item.id === "pagination" ? { ...item, state: pagination } : item)) };
}

function field(op, old, next, extra = {}) {
  return { op, old, next, ...extra };
}

function omitFields() {
  return {
    runtime: field("omit", HERMES, HERMES),
    model: field("omit", null, null),
    effort: field("omit", null, null),
    tier: field("omit", null, null),
  };
}

function row(partial) {
  return {
    workspace_id: ACM.id,
    workspace_name: ACM.name,
    reasons: partial.reasons || [partial.decision],
    fields: partial.fields || omitFields(),
    evidence: partial.evidence || { activity: "unknown", permission: "unknown", capability: "unknown", compat: "unknown", health: "pass" },
    precheck: partial.precheck || [
      { id: "activity", label: "活动", state: "UNKNOWN", source: "agent tasks", at: "2026-10-17T08:00:00Z", summary: "分页未穷尽" },
      { id: "permission", label: "权限", state: "UNKNOWN", source: "未接入", at: "2026-10-17T08:00:00Z", summary: "未知" },
      { id: "capability", label: "能力", state: "UNKNOWN", source: "未接入", at: "2026-10-17T08:00:00Z", summary: "未知" },
      { id: "compat", label: "兼容", state: "UNKNOWN", source: "未接入", at: "2026-10-17T08:00:00Z", summary: "未知" },
      { id: "health", label: "健康", state: "PASS", source: "runtime list", at: "2026-10-17T08:00:00Z", summary: "在线" },
    ],
    activity: partial.activity || { pages: 1, tasks: 0, complete: false, runs: [] },
    impact: partial.impact || "只读计划，不会写入 session 或 workdir。",
    source_version: partial.source_version || "obs-1017",
    fingerprint: partial.fingerprint || "fp-not-cas",
    ...partial,
  };
}

function shell(partial) {
  return {
    schema_version: 1,
    server: "api.multica.ai",
    actor: { name: "Yicone", kind: "member" },
    allow: [ACM],
    mode: "review_only",
    policy: "v1",
    hash_algo: "jcs-sha256",
    hash_verified: true,
    parse_error: "",
    gates: gates("fail"),
    rows: [],
    ...partial,
  };
}

function historicalRows() {
  const names = ["Builder", "Reviewer", "副调度"];
  const skipped = names.map((name, i) =>
    row({
      agent_id: agentId(i + 1),
      agent_name: name,
      decision: "SKIP_ACTIVE",
      fields: {
        runtime: field("set", HERMES, CLAUDE),
        model: field("omit", null, null),
        effort: field("omit", null, null),
        tier: field("omit", null, null),
      },
      evidence: { activity: "fail", permission: "unknown", capability: "unknown", compat: "unknown", health: "pass" },
      activity: {
        pages: 2,
        tasks: 1,
        complete: false,
        runs: [{ status: i === 0 ? "running" : "queued", at: "2026-10-17T08:10:00Z" }],
      },
      impact: "活动 run 还在，计划器会跳过这一行。",
    }),
  );
  const blocked = Array.from({ length: 13 }, (_, i) =>
    row({
      agent_id: agentId(i + 4),
      agent_name: `Agent ${String(i + 4).padStart(2, "0")}`,
      decision: "BLOCKED_ACTIVITY_COMPLETENESS",
      fields: {
        runtime: field("set", HERMES, CLAUDE),
        model: field("omit", null, null),
        effort: field("omit", null, null),
        tier: field("omit", null, null),
      },
      evidence: { activity: "unknown", permission: "unknown", capability: "unknown", compat: "unknown", health: "pass" },
    }),
  );
  return [...skipped, ...blocked];
}

export function buildPlans(nowMs) {
  const now = nowMs ?? Date.now();
  const at = (offset) => new Date(now + offset).toISOString();
  const fresh = at(-12 * 60 * 1000);
  const older = at(-3 * 60 * 60 * 1000);
  return [
    shell({
      plan_id: "hist",
      generated_at: fresh,
      expires_at: at(7 * 60 * 1000),
      plan_hash: "3f9a0c21eabc4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c21e",
      rows: historicalRows(),
    }),
    shell({
      plan_id: "mixed",
      generated_at: at(-40 * 60 * 1000),
      expires_at: at(20 * 60 * 1000),
      mode: "apply_candidate",
      plan_hash: "aa11bb22cc33dd44ee55ff6677889900aabbccddeeff00112233445566778899",
      gates: gates("pass"),
      rows: [
        row({
          agent_id: agentId(21),
          agent_name: "可改甲",
          decision: "APPROVABLE",
          reasons: ["APPROVABLE"],
          fields: {
            runtime: field("set", CODEX, CODEX),
            model: field("set", null, "gpt-5.4"),
            effort: field("set", "low", "medium"),
            tier: field("set", "", "flex"),
          },
          evidence: { activity: "pass", permission: "pass", capability: "pass", compat: "pass", health: "pass" },
          activity: { pages: 4, tasks: 12, complete: true, runs: [] },
          precheck: [
            { id: "activity", label: "活动", state: "PASS", source: "agent tasks", at: fresh, summary: "已穷尽，无活动" },
            { id: "permission", label: "权限", state: "PASS", source: "fixture", at: fresh, summary: "只读预检通过" },
            { id: "capability", label: "能力", state: "PASS", source: "fixture", at: fresh, summary: "目标 runtime 接受该模型" },
            { id: "compat", label: "兼容", state: "PASS", source: "fixture", at: fresh, summary: "字段兼容" },
            { id: "health", label: "健康", state: "PASS", source: "runtime list", at: fresh, summary: "在线" },
          ],
        }),
        row({
          agent_id: agentId(22),
          agent_name: "可改乙",
          decision: "APPROVABLE",
          reasons: ["APPROVABLE"],
          fields: {
            runtime: field("omit", CLAUDE, CLAUDE),
            model: field("set", "old-model", "new-model"),
            effort: field("omit", null, null),
            tier: field("omit", null, null),
          },
          evidence: { activity: "pass", permission: "pass", capability: "pass", compat: "pass", health: "pass" },
          activity: { pages: 2, tasks: 3, complete: true, runs: [] },
        }),
        row({
          agent_id: agentId(23),
          agent_name: "被阻断",
          decision: "BLOCKED_PERMISSION",
          reasons: ["BLOCKED_PERMISSION", "BLOCKED_CAPABILITY", "BLOCKED_COMPAT"],
          evidence: { activity: "pass", permission: "fail", capability: "unknown", compat: "fail", health: "pass" },
        }),
        row({
          agent_id: agentId(24),
          agent_name: "活动中",
          decision: "SKIP_ACTIVE",
          evidence: { activity: "fail", permission: "pass", capability: "pass", compat: "pass", health: "pass" },
          activity: { pages: 1, tasks: 1, complete: true, runs: [{ status: "running", at: fresh }] },
        }),
        row({
          agent_id: agentId(25),
          agent_name: "无变化",
          decision: "UNCHANGED",
          reasons: ["UNCHANGED"],
        }),
      ],
    }),
    shell({
      plan_id: "expired",
      generated_at: older,
      expires_at: at(-5 * 60 * 1000),
      plan_hash: "expiredhash0000000000000000000000000000000000000000000000000000000001",
      rows: [
        row({
          agent_id: agentId(31),
          agent_name: "过期示例",
          decision: "BLOCKED_ACTIVITY_COMPLETENESS",
        }),
      ],
    }),
    shell({
      plan_id: "mismatch",
      generated_at: at(-25 * 60 * 1000),
      expires_at: at(8 * 60 * 1000),
      plan_hash: "deadbeef0000000000000000000000000000000000000000000000000000bad1",
      hash_verified: false,
      rows: [
        row({
          agent_id: agentId(41),
          agent_name: "不该出现",
          decision: "APPROVABLE",
          reasons: ["APPROVABLE"],
        }),
      ],
    }),
    shell({
      plan_id: "broken",
      generated_at: at(-90 * 60 * 1000),
      expires_at: at(30 * 60 * 1000),
      schema_version: null,
      plan_hash: "",
      hash_verified: false,
      parse_error: "schema 未知：缺少 rows",
      rows: [],
    }),
    shell({
      plan_id: "blanks",
      generated_at: at(-15 * 60 * 1000),
      expires_at: at(15 * 60 * 1000),
      plan_hash: "blankhash000000000000000000000000000000000000000000000000000000000002",
      gates: gates("unknown"),
      rows: [
        row({
          agent_id: agentId(51),
          agent_name: "空值 null",
          decision: "APPROVABLE",
          reasons: ["APPROVABLE"],
          fields: {
            runtime: field("omit", CLAUDE, CLAUDE),
            model: field("set", null, "claude-sonnet"),
            effort: field("omit", null, null),
            tier: field("clear", "flex", null),
          },
          evidence: { activity: "pass", permission: "pass", capability: "pass", compat: "pass", health: "pass" },
          activity: { pages: 1, tasks: 0, complete: true, runs: [] },
        }),
        row({
          agent_id: agentId(52),
          agent_name: "空字符串",
          decision: "APPROVABLE",
          reasons: ["APPROVABLE"],
          fields: {
            runtime: field("omit", CLAUDE, CLAUDE),
            model: field("set", "", "claude-haiku"),
            effort: field("set", "", "low"),
            tier: field("omit", "", ""),
          },
          evidence: { activity: "pass", permission: "pass", capability: "unknown", compat: "pass", health: "pass" },
          activity: { pages: 1, tasks: 0, complete: true, runs: [] },
        }),
      ],
    }),
    shell({
      plan_id: "cross",
      generated_at: at(-8 * 60 * 1000),
      expires_at: at(12 * 60 * 1000),
      allow: [ACM, BETA],
      plan_hash: "crosshash000000000000000000000000000000000000000000000000000000000003",
      rows: [
        row({
          agent_id: "7c28ebcf-58eb-4791-a027-d3dd52e81d8e",
          agent_name: "Repo Analyst",
          workspace_id: "e8659de2-7504-4ee2-9f0a-fd608a9a8bc2",
          workspace_name: "Alpha Lab",
          decision: "BLOCKED_ACTIVITY_COMPLETENESS",
          fields: {
            runtime: field("set", { ...HERMES, workspace_id: ACM.id }, { ...CLAUDE, workspace_id: ACM.id }),
            model: field("omit", null, null),
            effort: field("omit", null, null),
            tier: field("omit", null, null),
          },
        }),
        row({
          agent_id: "75b39498-04f8-4714-8b63-baf90462ee16",
          agent_name: "Reviewer",
          workspace_id: BETA.id,
          workspace_name: BETA.name,
          decision: "SKIP_ACTIVE",
          fields: {
            runtime: field("set", { id: "c55b545e-4ef0-4149-abbc-8c0b4928c511", name: "Codex (beta)", provider: "codex", status: "online" }, { ...CLAUDE, workspace_id: ACM.id }),
            model: field("omit", null, null),
            effort: field("omit", null, null),
            tier: field("omit", null, null),
          },
          activity: { pages: 1, tasks: 1, complete: true, runs: [{ status: "queued", at: fresh }] },
          evidence: { activity: "fail", permission: "unknown", capability: "unknown", compat: "unknown", health: "pass" },
        }),
      ],
    }),
    shell({
      plan_id: "drift",
      generated_at: at(-6 * 60 * 1000),
      expires_at: at(18 * 60 * 1000),
      plan_hash: "drifthash000000000000000000000000000000000000000000000000000000000004",
      rows: [
        row({
          agent_id: "7c28ebcf-58eb-4791-a027-d3dd52e81d8e",
          agent_name: "Repo Analyst",
          workspace_id: "e8659de2-7504-4ee2-9f0a-fd608a9a8bc2",
          workspace_name: "Alpha Lab",
          decision: "BLOCKED_ACTIVITY_COMPLETENESS",
          fields: {
            runtime: field("omit", { id: "dd26ccd7-7e22-4912-ac43-201f9595f31b", name: "Claude (dev-mini)", provider: "claude", status: "online" }, { id: "dd26ccd7-7e22-4912-ac43-201f9595f31b", name: "Claude (dev-mini)", provider: "claude", status: "online" }),
            model: field("omit", "gpt-5.4", "gpt-5.4"),
            effort: field("omit", null, null),
            tier: field("omit", null, null),
          },
        }),
        row({
          agent_id: agentId(81),
          agent_name: "快照里没有",
          decision: "UNCHANGED",
          reasons: ["UNCHANGED"],
        }),
      ],
    }),
  ];
}
