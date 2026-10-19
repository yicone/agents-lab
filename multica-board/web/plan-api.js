/** Map the two read-only plan GETs onto the switch-plan view model. No writes. */

const G1 = [
  ["activity", "活动完整性"],
  ["permission", "权限"],
  ["capability", "能力"],
  ["compatibility", "配置兼容"],
  ["health", "目标健康"],
];

const G2 = [
  ["approval_claim", "批准原子领取"],
  ["commit_fence", "提交栅栏"],
  ["cas_activity_guard", "CAS 与活动保护"],
  ["operation_attribution", "操作归因"],
];

const FIELD_PAIRS = [
  ["runtime", "runtime_id"],
  ["model", "model"],
  ["effort", "thinking_level"],
  ["tier", "service_tier"],
];

const CHECKS = [
  ["activity", "活动"],
  ["permission", "权限"],
  ["capability", "能力"],
  ["compatibility", "兼容"],
  ["health", "健康"],
];

export const PLAN_ERROR_TEXT = {
  permissions: "权限或文件类型异常",
  too_large: "文件超过 2 MiB",
  invalid_json: "JSON 无效",
  invalid_schema: "结构或版本不符",
  read_error: "读取失败",
  directory_limit: "计划超过 1000 份，列表不是全量，可能缺少最新计划",
};

export function planErrorText(code) {
  if (!code) return "列表不完整，不能当成全量";
  return PLAN_ERROR_TEXT[code] || String(code);
}

export function directoryBannerText(code) {
  const text = planErrorText(code);
  if (code === "directory_limit" || text.includes("不是全量") || text.includes("不完整")) return text;
  return `${text}。列表不完整，不能当成全量`;
}

export function decodeBase64(value) {
  const clean = String(value ?? "").replace(/\s/g, "");
  if (!clean || /[^A-Za-z0-9+/=]/.test(clean)) throw new Error("bad base64");
  const bin = atob(clean);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i) & 0xff;
  return bytes;
}

export async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** SHA-256 of the exact decoded canonical bytes. Does not re-encode JSON. */
export async function clientHashMatches(b64, planHash) {
  if (typeof b64 !== "string" || !b64 || typeof planHash !== "string" || !/^[0-9a-f]{64}$/.test(planHash)) return false;
  try {
    return (await sha256Hex(decodeBase64(b64))) === planHash;
  } catch {
    return false;
  }
}

function workspaceName(entry) {
  if (!entry || typeof entry !== "object") return "";
  if (entry.name == null) return entry.id || "";
  return entry.name;
}

function allowList(entries) {
  return (entries || []).map((entry) => ({ id: entry?.id || "", name: workspaceName(entry) }));
}

function runtimeSide(meta, raw) {
  if (meta && typeof meta === "object") return { ...meta };
  if (typeof raw === "string" && raw) return { id: raw, name: raw, provider: null, status: null };
  return raw;
}

function mapFields(row) {
  const fields = {};
  for (const [viewKey, rawKey] of FIELD_PAIRS) {
    const raw = row.fields?.[rawKey] || {};
    fields[viewKey] = {
      op: raw.op || "omit",
      old: viewKey === "runtime" ? runtimeSide(row.source_runtime, raw.old) : raw.old ?? null,
      next: viewKey === "runtime" ? runtimeSide(row.target_runtime, raw.new) : raw.new ?? null,
      old_known: raw.old_known === true,
    };
  }
  return fields;
}

function checkView(row, snapshotKind) {
  const historical = snapshotKind === "historical";
  const evidence = {};
  const precheck = CHECKS.map(([id, label]) => {
    const check = row.checks?.[id] || {};
    let status = check.status || "UNKNOWN";
    let source = check.source || "";
    if (historical && id === "health") {
      status = "UNKNOWN";
      source = "historical_snapshot";
    }
    const evidenceId = id === "compatibility" ? "compat" : id;
    evidence[evidenceId] = status;
    return { id: evidenceId, label, state: status, source, at: check.observed_at || "", summary: "" };
  });
  return { evidence, precheck };
}

function mapRow(row, payload) {
  const name = allowList(payload.workspace_allow_list).find((item) => item.id === row.workspace_id);
  const { evidence, precheck } = checkView(row, payload.snapshot_kind);
  const activity = row.activity || {};
  return {
    contract: true,
    workspace_id: row.workspace_id || "",
    workspace_name: name?.name || row.workspace_id || "",
    agent_id: row.agent_id || "",
    agent_name: row.agent_name == null ? "" : row.agent_name,
    archived: row.archived,
    decision: row.decision || "",
    reasons: row.reasons || [],
    fields: mapFields(row),
    evidence,
    precheck,
    activity: {
      pages: activity.pages_read,
      tasks: activity.tasks_seen,
      complete: false,
      completeness: activity.completeness || "UNKNOWN",
      unavailable: activity.pages_read === 0,
      runs: (activity.runs || []).map((run) => ({ status: run.status, at: run.created_at })),
    },
    impact: Array.isArray(row.impact) ? row.impact : [],
    source_version: row.source_version,
    fingerprint: row.source_fingerprint || "",
    observed_at: row.observed_at || "",
  };
}

function mapGates(gates, snapshotKind) {
  const g1 = G1.map(([id, label]) => {
    let state = gates?.g1?.[id] || "UNKNOWN";
    if (snapshotKind === "historical" && id === "health") state = "UNKNOWN";
    return { id, label, state: String(state).toLowerCase() };
  });
  const g2 = G2.map(([id, label]) => ({ id, label, state: "absent" }));
  return { g1, g2 };
}

export function mapPlanList(body) {
  const items = Array.isArray(body?.items) ? body.items : [];
  return {
    generated_at: null,
    complete: body?.complete === true,
    directory_error: body?.directory_error ?? null,
    plans: items.map((item) => ({
      plan_id: item?.plan_id || "",
      generated_at: item?.generated_at ?? null,
      expires_at: item?.expires_at ?? null,
      expired: item?.expired === true,
      mode: item?.mode ?? null,
      schema_version: item?.schema_version ?? null,
      server: item?.server_identity ?? null,
      snapshot_kind: item?.snapshot_kind ?? null,
      observed_at: item?.observed_at ?? null,
      allow: allowList(item?.workspace_allow_list),
      counts: item?.counts ?? null,
      agent_count: item?.agent_count ?? null,
      plan_hash: item?.plan_hash || "",
      hash_verified: item?.hash_verified === true,
      parse_error: item?.parse_error ? planErrorText(item.parse_error) : "",
      policy: "g1-v1",
    })),
  };
}

export async function mapPlanDetail(body) {
  const envelope = body?.plan;
  const payload = envelope?.payload;
  if (!payload || typeof payload !== "object") return { ok: false, error: "计划无法读取" };
  const planHash = typeof envelope.plan_hash === "string" ? envelope.plan_hash : "";
  const serverOk = body?.hash_verified === true;
  const clientOk = await clientHashMatches(body?.canonical_payload_b64, planHash);
  const rows = Array.isArray(payload.rows) ? payload.rows.map((row) => mapRow(row, payload)) : [];
  return {
    ok: true,
    plan: {
      plan_id: payload.plan_id || "",
      generated_at: payload.generated_at || null,
      expires_at: payload.expires_at || null,
      expired: body?.expired === true,
      mode: payload.mode || "review_only",
      schema_version: payload.schema_version,
      policy: payload.policy_version || "g1-v1",
      server: payload.server_identity ?? null,
      snapshot_kind: payload.snapshot_kind || "",
      actor: {
        name: payload.actor?.id || "未知",
        kind: payload.actor?.type || "未知",
        verified: payload.actor?.verified === true,
      },
      allow: allowList(payload.workspace_allow_list),
      plan_hash: planHash,
      hash_algo: body?.hash_algo || envelope.hash_algo || "",
      hash_verified: serverOk && clientOk,
      parse_error: "",
      counts: payload.counts || null,
      agent_count: rows.length,
      gates: mapGates(body?.gates, payload.snapshot_kind),
      apply_enabled: false,
      rows,
      raw: envelope,
    },
  };
}
