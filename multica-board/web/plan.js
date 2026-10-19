/** G1 switch-plan views. Fixture only: no fetch, no POST, no approve/apply/rollback. */

import { buildPlans } from "./fixtures/plan-data.js";

export const BUCKETS = [
  { id: "approvable", title: "可批准", hint: "满足计划器全部只读预检；G2 未满足时仍不能执行" },
  { id: "blocked", title: "阻断", hint: "计划器判定为阻断" },
  { id: "skip", title: "跳过·活动", hint: "活动任务，计划器跳过" },
  { id: "unchanged", title: "无变化", hint: "全部字段不改，或旧值等于新值" },
];

const REASONS = {
  APPROVABLE: "可批准",
  UNCHANGED: "无变化",
  SKIP_ACTIVE: "活动中跳过",
  BLOCKED_ACTIVITY_COMPLETENESS: "活动未穷尽",
  BLOCKED_PERMISSION: "权限未判定",
  BLOCKED_CAPABILITY: "能力未知",
  BLOCKED_COMPAT: "配置不兼容",
  BLOCKED_TARGET_RUNTIME: "目标未定",
};

const G2 = [
  { id: "approval", label: "批准原子消费" },
  { id: "fence", label: "提交栅栏" },
  { id: "cas", label: "CAS/活动保护" },
];

const EVIDENCE = [
  ["activity", "活动", "活动检查是否穷尽"],
  ["permission", "权限", "是否有权修改该 agent"],
  ["capability", "能力", "目标 runtime 是否接受模型 / effort / tier"],
  ["compat", "兼容", "配置是否兼容"],
  ["health", "健康", "计划里记录的 runtime 健康"],
];

export function planCatalog(fix, nowMs = Date.now()) {
  if (fix === "empty") return [];
  return buildPlans(nowMs);
}

export function findPlan(plans, id) {
  return (plans || []).find((plan) => plan.plan_id === id) || null;
}

export function parseSel(sel) {
  return [...new Set(String(sel || "").split(",").map((part) => part.trim()).filter(Boolean))];
}

export function formatSel(ids) {
  return parseSel(ids.join ? ids.join(",") : ids).join(",");
}

export function outsideCount(selectedIds, visibleIds) {
  const visible = new Set(visibleIds);
  return selectedIds.filter((id) => !visible.has(id)).length;
}

export function selectionLabel(agentCount, workspaceCount, outside) {
  const hidden = outside === 0 ? "全部可见" : `其中 ${outside} 个不在当前筛选中`;
  return `已选 ${agentCount} 个 agent · ${workspaceCount} 个 workspace（${hidden}）`;
}

export function workspaceAllowList(agents) {
  const map = new Map();
  for (const agent of agents || []) {
    if (!agent?.workspace_id || map.has(agent.workspace_id)) continue;
    map.set(agent.workspace_id, { id: agent.workspace_id, name: agent.workspace_name || agent.workspace_id });
  }
  return [...map.values()];
}

function namedAllow(entries) {
  const map = new Map();
  for (const entry of entries || []) {
    if (!entry?.id || map.has(entry.id)) continue;
    map.set(entry.id, { id: entry.id, name: entry.name || entry.id });
  }
  return [...map.values()];
}

function runtimeRecord(field) {
  if (!field || typeof field !== "object") return { error: "缺少 runtime" };
  if (field.op === "set" || field.op === "omit") {
    const record = field.op === "set" ? field.next : field.old;
    if (!record?.id) return { error: "没有可用的 runtime id，没有改成空 runtime" };
    return { record };
  }
  return { error: `runtime 操作是 ${field.op}，不能当成其他行的目标` };
}

function fieldTokenOf(field) {
  if (!field || typeof field !== "object") return { error: "缺少字段" };
  if (field.op === "omit") return { token: "omit" };
  if (field.op === "clear") return { token: "clear" };
  if (field.op === "set") {
    if (field.next == null || field.next === "" || typeof field.next === "object") return { error: "设为空值，不能当成不改" };
    return { token: `set:${field.next}` };
  }
  return { error: "操作无法识别" };
}

/** Restore one compose target from a plan. Disagreement is an error, never the first row. */
export function composeTarget(plan) {
  const errors = [];
  const agents = [];
  for (const row of plan?.rows || []) {
    const name = row?.agent_name || row?.agent_id || "某一行";
    if (!row?.agent_id) {
      errors.push("有一行缺少 agent id，不能重新生成。");
      continue;
    }
    if (!row.workspace_id) {
      errors.push(`${name} 缺少 workspace，没有改成空 workspace。`);
      agents.push({ id: row.agent_id, name, missing: true });
      continue;
    }
    agents.push({
      id: row.agent_id,
      name,
      workspace_id: row.workspace_id,
      workspace_name: row.workspace_name || row.workspace_id,
    });
  }
  const allow = namedAllow(plan?.allow?.length ? plan.allow : agents);
  for (const agent of agents) {
    if (agent.workspace_id && !allow.some((ws) => ws.id === agent.workspace_id)) {
      errors.push(`${agent.name} 的 workspace 不在 allow-list 里。`);
    }
  }
  if (!(plan?.rows || []).length) errors.push("计划没有行，无法恢复选择。");

  let runtime = null;
  let runtimeConflict = false;
  for (const row of plan?.rows || []) {
    const name = row.agent_name || row.agent_id || "某一行";
    const got = runtimeRecord(row.fields?.runtime);
    if (got.error) {
      errors.push(`${name}：${got.error}。`);
      continue;
    }
    if (!runtime) runtime = got.record;
    else if (runtime.id !== got.record.id) runtimeConflict = true;
  }
  if (runtimeConflict) {
    errors.push("各行目标 runtime 不一致，没有改成第一行。");
    runtime = null;
  }

  const tokens = { model: "omit", effort: "omit", tier: "omit" };
  for (const [key, label] of [["model", "模型"], ["effort", "推理"], ["tier", "Tier"]]) {
    let chosen = null;
    let conflict = false;
    for (const row of plan?.rows || []) {
      const name = row.agent_name || row.agent_id || "某一行";
      const got = fieldTokenOf(row.fields?.[key]);
      if (got.error) {
        errors.push(`${name} 的${label}：${got.error}。`);
        continue;
      }
      if (chosen == null) chosen = got.token;
      else if (chosen !== got.token) conflict = true;
    }
    if (conflict) {
      errors.push(`${label}的目标不一致，没有改成不改或第一行。`);
      tokens[key] = "conflict";
    } else if (chosen) tokens[key] = chosen;
  }

  return { ok: errors.length === 0, errors, agents, allow, runtime, ...tokens };
}

export function fieldProblem(token) {
  if (!token || token === "omit" || token === "clear") return "";
  if (token === "conflict") return "目标不一致，先明确这一项";
  if (!token.startsWith("set:")) return "字段取值无法识别";
  const value = token.slice(4);
  if (!value || value !== value.trim()) return "设为的值不能为空，也不能带首尾空白";
  return "";
}

export function tierSetAllowed(provider) {
  return provider === "codex";
}

export function buildCommand({ server, agentIds, workspaceIds, runtimeId, model, effort, tier }) {
  const problems = [fieldProblem(model), fieldProblem(effort), fieldProblem(tier)].filter(Boolean);
  if (!agentIds?.length) problems.push("先选择 agent");
  if (!workspaceIds?.length) problems.push("没有 workspace allow-list");
  if (!runtimeId) problems.push("先选择 runtime");
  if (problems.length) return { ok: false, error: problems[0], text: "" };
  const text = [
    "atlas-plan",
    "--server",
    server || "api.multica.ai",
    "--agents",
    agentIds.join(","),
    "--allow-list",
    workspaceIds.join(","),
    "--runtime",
    runtimeId,
    "--model",
    model || "omit",
    "--effort",
    effort || "omit",
    "--tier",
    tier || "omit",
  ].join(" ");
  return { ok: true, error: "", text };
}

export function bucketOf(row) {
  const decision = row?.decision || "";
  if (decision === "APPROVABLE") return "approvable";
  if (decision === "SKIP_ACTIVE" || decision.startsWith("SKIP_")) return "skip";
  if (decision === "UNCHANGED") return "unchanged";
  if (decision.startsWith("BLOCKED")) return "blocked";
  const fields = Object.values(row?.fields || {});
  const unchanged = fields.every((field) => field?.op === "omit" || sameRaw(field?.old, field?.next));
  return unchanged ? "unchanged" : "blocked";
}

export function bucketCounts(rows) {
  const counts = { approvable: 0, blocked: 0, skip: 0, unchanged: 0 };
  for (const row of rows || []) counts[bucketOf(row)] += 1;
  return counts;
}

export function groupRows(rows) {
  const groups = new Map(BUCKETS.map((bucket) => [bucket.id, []]));
  for (const row of rows || []) groups.get(bucketOf(row)).push(row);
  for (const list of groups.values()) {
    list.sort((a, b) => a.workspace_name.localeCompare(b.workspace_name, "zh") || a.agent_name.localeCompare(b.agent_name, "zh"));
  }
  return BUCKETS.map((bucket) => ({ ...bucket, rows: groups.get(bucket.id) }));
}

export function sameRaw(a, b) {
  if (a && typeof a === "object") return (a.id || "") === (b?.id || "");
  return Object.is(a, b);
}

export function rawMark(value) {
  if (value === null) return "null";
  if (value === "") return '""';
  return "";
}

export function isDefaultRaw(value) {
  return value == null || value === "";
}

export function columnHasMixedDefault(rows, key) {
  const marks = new Set();
  for (const row of rows || []) {
    for (const side of ["old", "next"]) {
      const mark = rawMark(row.fields?.[key]?.[side]);
      if (mark) marks.add(mark);
    }
  }
  return marks.has("null") && marks.has('""');
}

export function reasonText(code) {
  return REASONS[code] || "";
}

export function reasonSummary(codes) {
  const list = codes || [];
  const shown = list.slice(0, 2).map((code) => reasonText(code) || code);
  const extra = list.length > 2 ? list.length - 2 : 0;
  return { shown, extra, unknown: list.filter((code) => !reasonText(code)) };
}

export function remain(expires, nowMs) {
  const ms = Date.parse(expires) - nowMs;
  if (Number.isNaN(ms)) return { expired: false, clock: "时间未知", short: "时间未知" };
  if (ms <= 0) return { expired: true, clock: "已过期", short: "已过期" };
  const total = Math.ceil(ms / 1000);
  const hh = Math.floor(total / 3600);
  const mm = Math.floor((total % 3600) / 60);
  const ss = total % 60;
  const clock = hh ? `${hh}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${mm}:${String(ss).padStart(2, "0")}`;
  return { expired: false, clock: `剩 ${clock}`, short: `剩 ${Math.max(1, Math.ceil(total / 60))} 分` };
}

export function listStatus(plan, nowMs) {
  if (plan.parse_error) return { tone: "bad", text: "无法解析", detail: plan.parse_error };
  if (plan.hash_verified === false) return { tone: "bad", text: "hash 不符", detail: "内容与 hash 不符" };
  const left = remain(plan.expires_at, nowMs);
  if (left.expired) return { tone: "muted", text: "已过期", detail: "" };
  return { tone: "ok", text: `有效 · ${left.short}`, detail: "" };
}

export function hashOk(plan) {
  return !plan?.parse_error && plan?.hash_verified === true;
}

export function driftOf(row, agent) {
  if (!agent) return false;
  const fields = row.fields || {};
  if (fields.runtime?.old && (agent.runtime_id || "") !== (fields.runtime.old.id || "")) return true;
  if (!sameRaw(agent.model, fields.model?.old)) return true;
  if (!sameRaw(agent.thinking_level, fields.effort?.old)) return true;
  if (!sameRaw(agent.service_tier, fields.tier?.old)) return true;
  return false;
}

export function runtimeChoices(runtimes, allow) {
  const ids = new Set((allow || []).map((ws) => ws.id));
  return (runtimes || [])
    .filter((rt) => ids.has(rt.workspace_id))
    .slice()
    .sort((a, b) => a.workspace_id.localeCompare(b.workspace_id) || a.id.localeCompare(b.id));
}

export function shortId(id) {
  return String(id || "").slice(0, 8);
}

export function shortHash(hash) {
  return hash ? String(hash).slice(0, 12) : "—";
}

function stamp(iso) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "时间未知";
  return new Date(t).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function clockLine(plan, nowMs) {
  const left = remain(plan.expires_at, nowMs);
  return {
    expired: left.expired,
    clock: left.clock,
    text: `生成 ${stamp(plan.generated_at)} · 过期 ${stamp(plan.expires_at)}（${left.clock}）· policy ${plan.policy} · schema ${plan.schema_version ?? "未知"}`,
  };
}

function rawText(value) {
  if (value && typeof value === "object") return value.name || value.id || "";
  if (value === null) return "null";
  if (value === "") return '""';
  return String(value);
}

function fieldNodes(h, field, kind) {
  if (!field || field.op === "omit") {
    const current = kind === "runtime" ? field?.old?.name || rawText(field?.old) : rawText(field?.old);
    return [h("span", { class: "muted", title: `当前值 ${current}` }, "不改")];
  }
  const oldNode = isDefaultRaw(kind === "runtime" ? null : field.old) && kind !== "runtime"
    ? h("span", null, "默认", rawMark(field.old) ? h("span", { class: "plan-raw" }, rawMark(field.old)) : null)
    : kind === "runtime"
      ? runtimeBits(h, field.old)
      : rawText(field.old);
  if (field.op === "clear") {
    return [
      oldNode,
      " → ",
      h("span", { class: "plan-clear", title: "清除后继承 runtime 默认；实际值未知" }, "默认"),
    ];
  }
  const nextNode = kind === "runtime" ? runtimeBits(h, field.next) : h("strong", null, isDefaultRaw(field.next) ? "默认" : rawText(field.next), rawMark(field.next) ? h("span", { class: "plan-raw" }, rawMark(field.next)) : null);
  const cross = kind === "runtime" && field.old?.provider && field.next?.provider && field.old.provider !== field.next.provider;
  return [oldNode, " → ", nextNode, cross ? h("span", { class: "tag" }, "跨 provider") : null];
}

function runtimeBits(h, value) {
  if (!value || typeof value !== "object") return rawText(value);
  const on = value.status === "online";
  return h("span", { class: "rt" }, h("span", { class: `dot ${on ? "ok" : "off"}`, title: on ? "在线" : "离线" }), value.name || value.id);
}

function evidenceMark(state) {
  if (state === "pass" || state === "PASS") return { mark: "✓", cls: "ok" };
  if (state === "fail" || state === "FAIL") return { mark: "✕", cls: "bad" };
  return { mark: "?", cls: "unk" };
}

function gateDot(state) {
  if (state === "pass") return { cls: "ok", mark: "✓" };
  if (state === "fail") return { cls: "bad", mark: "✕" };
  if (state === "absent") return { cls: "absent", mark: "·" };
  return { cls: "unk", mark: "?" };
}

function crumbs(h, ctx, tail) {
  return h(
    "p",
    { class: "crumb" },
    h("a", { href: "#/agents", onclick: (e) => ctx.leave(e) }, "Agents"),
    " › ",
    h("a", { href: "#/agents/plans", onclick: (e) => ctx.openList(e) }, "切换计划"),
    tail ? ` › ${tail}` : "",
  );
}

function copyButton(h, ctx, text, label = "复制") {
  return h(
    "button",
    { type: "button", disabled: !text, onclick: () => text && ctx.copy(text) },
    label,
  );
}

export function renderPlanNew(h, ctx) {
  const selected = ctx.selected || [];
  const allow = ctx.allow || workspaceAllowList(selected);
  let choices = runtimeChoices(ctx.runtimes || [], allow);
  let runtime = choices.find((item) => item.id === ctx.route.rt) || null;
  if (ctx.route.rt && !runtime) {
    runtime = { id: ctx.route.rt, name: "计划中的 runtime", provider: "未知", workspace_id: "" };
    choices = [...choices, runtime];
  }
  const tierAllowed = tierSetAllowed(runtime?.provider);
  const tier = tierAllowed ? ctx.route.tier : "omit";
  const command = buildCommand({
    server: "api.multica.ai",
    agentIds: selected.map((agent) => agent.id),
    workspaceIds: allow.map((ws) => ws.id),
    runtimeId: runtime?.id || "",
    model: ctx.route.model,
    effort: ctx.route.effort,
    tier,
  });
  const missing = new Set();
  if (runtime) {
    for (const agent of selected) {
      if (agent.workspace_id && agent.workspace_id !== runtime.workspace_id) missing.add(agent.workspace_id);
    }
  }
  const fieldRow = (key, label, token, disabledSet) =>
    h(
      "div",
      { class: "plan-field" },
      h("span", { class: "plan-field-label" }, label),
      ["omit", "clear", "set"].map((op) => {
        const set = op === "set";
        const disabled = set && disabledSet;
        const checked = (set ? token.startsWith("set:") : token === op) && !disabled;
        return h(
          "label",
          { class: disabled ? "muted" : "" },
          h("input", {
            type: "radio",
            name: key,
            disabled,
            checked,
            onchange: () => ctx.setRoute({ [key]: set ? (token.startsWith("set:") ? token : "set:") : op }),
          }),
          op === "omit" ? "不改" : op === "clear" ? "清除为默认" : "设为",
        );
      }),
      h("input", {
        type: "text",
        value: token.startsWith("set:") ? token.slice(4) : "",
        disabled: disabledSet || !token.startsWith("set:"),
        spellcheck: "false",
        "aria-label": `${label} 设为`,
        oninput: (e) => ctx.setRoute({ [key]: `set:${e.target.value}` }),
      }),
      fieldProblem(token) ? h("span", { class: "bad-n" }, fieldProblem(token)) : null,
    );

  const restoreErrors = ctx.restoreErrors || [];
  return [
    crumbs(h, ctx, "新计划"),
    restoreErrors.length
      ? h(
          "div",
          { class: "plan-card plan-bad", role: "alert", "data-restore-error": "1" },
          restoreErrors.map((text) => h("p", null, text)),
        )
      : null,
    h(
      "section",
      { class: `plan-card${allow.length > 1 ? " plan-cross" : ""}` },
      h("h2", null, "范围"),
      h(
        "p",
        null,
        "server ",
        h("span", { class: "mono" }, "api.multica.ai"),
        " · workspace allow-list ",
        allow.length
          ? allow.map((ws) => `${ws.name}（${shortId(ws.id)}）`).join("、")
          : "还没有",
        allow.length > 1 ? h("span", { class: "tag warn" }, `跨 ${allow.length} 个 workspace`) : null,
      ),
      h(
        "p",
        null,
        `已选 ${selected.length} 个 agent`,
        selected.length ? `：${selected.map((agent) => agent.name || agent.id).join("、")}` : "",
        " ",
        h("button", { type: "button", class: "text-btn", onclick: (e) => ctx.backToSelect(e) }, "改选择"),
      ),
      missing.size
        ? h("p", { class: "bad-n" }, "该 workspace 没有此 runtime。页面只提示，判定仍归计划器。")
        : null,
    ),
    h(
      "section",
      { class: "plan-card" },
      h("h2", null, "目标"),
      h(
        "label",
        { class: "plan-field" },
        "Runtime ",
        h(
          "select",
          {
            "aria-label": "目标 runtime",
            value: runtime?.id || "",
            onchange: (e) => {
              const next = choices.find((item) => item.id === e.target.value);
              const patch = { rt: e.target.value };
              if (!tierSetAllowed(next?.provider) && ctx.route.tier.startsWith("set:")) patch.tier = "omit";
              ctx.setRoute(patch);
            },
          },
          h("option", { value: "", selected: !runtime }, "选择 runtime"),
          choices.map((item) => h("option", { value: item.id, selected: item.id === runtime?.id }, `${item.name} · ${item.provider}`)),
        ),
        h("span", { class: "muted" }, " 只列 allow-list 内 workspace 的 runtime"),
      ),
      fieldRow("model", "模型", ctx.route.model, false),
      fieldRow("effort", "推理", ctx.route.effort, false),
      fieldRow("tier", "Tier", tier, !tierAllowed),
      !tierAllowed ? h("p", { class: "muted" }, "Codex 以外的 runtime 不能设 Tier。") : null,
      h("p", { class: "muted" }, "页面不校验模型目录；目录、权限、活动任务由计划器实时判定，未知即阻断。"),
    ),
    h(
      "section",
      { class: "plan-card" },
      h(
        "header",
        { class: "plan-card-head" },
        h("h2", null, "在你的终端运行"),
        copyButton(h, ctx, command.ok ? command.text : "", "复制"),
      ),
      command.ok
        ? h("pre", { class: "plan-command" }, command.text)
        : h("p", { class: "bad-n" }, command.error),
      h("p", { class: "muted" }, "参数名未定稿，这只是 fixture 预览。页面不会发送，也没有运行按钮。命令里不包含凭证或 profile。"),
    ),
    h(
      "p",
      null,
      "生成后回到切换计划列表查看。 ",
      h("a", { href: "#/agents/plans", onclick: (e) => ctx.openList(e) }, "去计划列表"),
    ),
  ].filter((node) => node != null && node !== false);
}

export function renderPlanList(h, ctx) {
  const plans = ctx.plans || [];
  const latest = plans.slice().sort((a, b) => Date.parse(b.generated_at) - Date.parse(a.generated_at))[0];
  const body = plans.length
    ? h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          { class: "plan-table" },
          h(
            "thead",
            null,
            h("tr", null, ["生成", "状态", "模式", "范围", "结果", "hash"].map((name) => h("th", null, name))),
          ),
          h(
            "tbody",
            null,
            plans
              .slice()
              .sort((a, b) => Date.parse(b.generated_at) - Date.parse(a.generated_at))
              .map((plan) => {
                const status = listStatus(plan, ctx.nowMs);
                const counts = bucketCounts(plan.rows);
                const expired = remain(plan.expires_at, ctx.nowMs).expired;
                return h(
                  "tr",
                  {
                    class: expired ? "plan-expired" : "",
                    tabindex: "0",
                    onclick: () => ctx.openPlan(plan.plan_id),
                  },
                  h("td", { title: plan.generated_at }, ctx.ago(plan.generated_at)),
                  h("td", { class: status.tone === "bad" ? "bad-n" : status.tone === "ok" ? "ok-n" : "muted" }, status.text, status.detail ? h("div", { class: "muted" }, status.detail) : null),
                  h(
                    "td",
                    null,
                    h("span", { class: plan.mode === "apply_candidate" ? "tag info" : "tag" }, plan.mode || "未知"),
                    plan.mode === "apply_candidate" ? h("div", { class: "muted" }, "Atlas 不执行") : null,
                  ),
                  h("td", null, plan.allow?.length > 1 ? `${plan.allow.length} 个 ws` : plan.allow?.[0]?.name || "—", h("div", { class: "muted" }, `${plan.rows.length} agent`)),
                  h(
                    "td",
                    null,
                    ["approvable", "skip", "blocked", "unchanged"].map((id) => {
                      const n = counts[id];
                      const title = BUCKETS.find((bucket) => bucket.id === id).title;
                      return h("span", { class: n ? "" : "muted" }, `${title} ${n} `);
                    }),
                  ),
                  h("td", null, h("span", { class: "mono" }, shortHash(plan.plan_hash)), h("div", { class: "muted" }, "计划 hash")),
                );
              }),
          ),
        ),
      )
    : h("div", { class: "empty" }, "还没有计划。在 Agents 页点「规划切换…」开始");
  return [
    crumbs(h, ctx, ""),
    h("p", { class: "muted" }, latest ? `计划目录 · 最近一份 ${ctx.ago(latest.generated_at)}` : "计划目录 · 空"),
    body,
  ];
}

export function renderPlanReview(h, ctx) {
  const plan = ctx.plan;
  if (!plan) return [crumbs(h, ctx, "未找到"), h("div", { class: "empty" }, "fixture 里没有这份计划")];
  const left = remain(plan.expires_at, ctx.nowMs);
  const line = clockLine(plan, ctx.nowMs);
  const verified = hashOk(plan);
  const counts = bucketCounts(plan.rows);
  const total = plan.rows.length;
  const sum = counts.approvable + counts.blocked + counts.skip + counts.unchanged;
  const groups = groupRows(plan.rows);
  const visibleGroups = ctx.route.bucket ? groups.filter((group) => group.id === ctx.route.bucket) : groups;
  const hidden = ctx.route.bucket ? total - (counts[ctx.route.bucket] || 0) : 0;
  const showTable = verified && !plan.parse_error;
  return [
    crumbs(h, ctx, stamp(plan.generated_at)),
    h(
      "section",
      { class: `plan-scope${verified ? "" : " plan-bad"}${left.expired ? " is-expired" : ""}` },
      left.expired ? h("div", { class: "plan-expired-bar" }, "已过期 · 仅供参考，需重新生成") : null,
      h(
        "p",
        null,
        h("span", { class: plan.mode === "apply_candidate" ? "tag info" : "tag" }, (plan.mode || "review_only").toUpperCase()),
        plan.mode === "apply_candidate" ? h("span", { class: "muted" }, " Atlas 不执行；批准与执行不在本页") : null,
        " server ",
        h("span", { class: "mono" }, plan.server),
        " · ws ",
        (plan.allow || []).map((ws) => ws.name).join("、") || "—",
        plan.allow?.length > 1 ? h("span", { class: "tag warn" }, `跨 ${plan.allow.length} 个 workspace`) : null,
        " · actor ",
        `${plan.actor?.name || "未知"}（${plan.actor?.kind || "未知"}）`,
      ),
      h(
        "p",
        null,
        h("span", { "data-plan-clock": line.clock, "data-plan-expired": line.expired ? "1" : "0" }, line.text),
        h("span", { class: "muted" }, " · 本机时钟，仅作提示"),
      ),
      h(
        "p",
        null,
        "计划 hash（不是批准） ",
        h("span", { class: "mono" }, plan.plan_hash ? `${shortHash(plan.plan_hash)}…` : "—"),
        " ",
        verified ? h("span", { class: "ok-n" }, "✓ 后端复算一致") : h("span", { class: "bad-n" }, "内容与 hash 不符，文件可能被改过，勿据此审阅"),
        " ",
        copyButton(h, ctx, plan.plan_hash || "", "复制完整 hash"),
      ),
      plan.parse_error ? h("p", { class: "bad-n" }, plan.parse_error) : null,
    ),
    h(
      "section",
      { class: `plan-gates${left.expired ? " is-dim" : ""}` },
      (plan.gates?.g1 || []).map((item) => {
        const dot = gateDot(item.state);
        return h("span", { class: `plan-gate ${dot.cls}` }, `${dot.mark} ${item.label}`);
      }),
      h("span", { class: "plan-gate-split" }),
      h("span", { class: "muted" }, "G2："),
      G2.map((item) => h("span", { class: "plan-gate absent", title: "未接入" }, `· ${item.label}`)),
      h("p", { class: "plan-apply" }, "apply 关闭：G2 硬门槛未满足。本页只用于审阅。"),
    ),
    showTable
      ? h(
          "div",
          { class: left.expired ? "is-dim" : "" },
          h(
            "div",
            { class: "plan-counts", "data-bucket-sum": String(sum), "data-row-total": String(total) },
            BUCKETS.map((bucket) =>
              h(
                "button",
                {
                  type: "button",
                  class: ctx.route.bucket === bucket.id ? "active" : "",
                  title: bucket.hint,
                  "aria-pressed": ctx.route.bucket === bucket.id ? "true" : "false",
                  onclick: () => ctx.setRoute({ bucket: ctx.route.bucket === bucket.id ? "" : bucket.id }),
                },
                `${bucket.title} ${counts[bucket.id]}`,
              ),
            ),
          ),
          h("p", { class: "muted" }, `当前筛选隐藏 ${hidden} 行`),
          h("p", { class: "muted" }, "展示顺序不是 hash 顺序"),
          ...visibleGroups.map((group) => renderGroup(h, ctx, group)),
        )
      : h(
          "div",
          { class: "plan-card" },
          h("button", { type: "button", onclick: () => ctx.toggleRaw() }, "查看原始 JSON"),
          ctx.rawOpen ? h("pre", { class: "plan-command" }, JSON.stringify(plan, null, 2)) : null,
        ),
    h(
      "p",
      { class: "plan-foot" },
      copyButton(h, ctx, plan.plan_hash || "", "复制完整 hash"),
      " ",
      copyButton(h, ctx, JSON.stringify(plan), "复制原始计划 JSON"),
      " ",
      h("button", { type: "button", onclick: () => ctx.regenerate(plan) }, "用相同选择重新生成"),
    ),
  ];
}

function renderGroup(h, ctx, group) {
  const headMix = ["runtime", "model", "effort", "tier"].map((key) => columnHasMixedDefault(group.rows, key));
  return h(
    "section",
    { class: "plan-group" },
    h("h3", null, `${group.title} ${group.rows.length || ""}`.trim(), group.rows.length ? null : " 无"),
    group.rows.length
      ? h(
          "div",
          { class: "table-wrap" },
          h(
            "table",
            { class: "plan-table" },
            h(
              "thead",
              null,
              h(
                "tr",
                null,
                h("th", null, "Agent"),
                h("th", null, "Runtime", headMix[0] ? h("span", { class: "plan-mix", title: "原始值不同" }) : null),
                h("th", null, "模型", headMix[1] ? h("span", { class: "plan-mix", title: "原始值不同" }) : null),
                h("th", null, "推理", headMix[2] ? h("span", { class: "plan-mix", title: "原始值不同" }) : null),
                h("th", null, "Tier", headMix[3] ? h("span", { class: "plan-mix", title: "原始值不同" }) : null),
                ...EVIDENCE.map(([, label, hint]) => h("th", { class: "plan-ev", title: hint }, label)),
                h("th", null, "原因"),
              ),
            ),
            h(
              "tbody",
              null,
              group.rows.map((row) => renderPlanRow(h, ctx, row)),
            ),
          ),
        )
      : null,
  );
}

function renderPlanRow(h, ctx, row) {
  const reasons = reasonSummary(row.reasons);
  const drifted = driftOf(row, ctx.agentById?.get(row.agent_id));
  const selected = ctx.drawerAgentId === row.agent_id;
  return h(
    "tr",
    {
      id: `plan-row-${row.agent_id}`,
      class: selected ? "plan-row selected" : "plan-row",
      tabindex: "0",
      onclick: (e) => ctx.openRow(row, e.currentTarget),
    },
    h(
      "td",
      null,
      row.agent_name,
      h("div", { class: "muted" }, row.workspace_name),
      drifted ? h("span", { class: "tag warn", title: `依据 Atlas 快照 ${ctx.snapshotAge}，不作执行依据；请重新生成计划` }, "现值已变") : null,
    ),
    h("td", null, ...fieldNodes(h, row.fields.runtime, "runtime")),
    h("td", null, ...fieldNodes(h, row.fields.model, "model")),
    h("td", null, ...fieldNodes(h, row.fields.effort, "effort")),
    h("td", null, ...fieldNodes(h, row.fields.tier, "tier")),
    ...EVIDENCE.map(([key]) => {
      const mark = evidenceMark(row.evidence?.[key]);
      return h("td", { class: `plan-ev ${mark.cls}` }, mark.mark);
    }),
    h(
      "td",
      null,
      reasons.shown.map((text, i) => h("span", { class: reasons.unknown.includes(row.reasons[i]) ? "mono" : "" }, i ? `、${text}` : text)),
      reasons.extra ? ` +${reasons.extra}` : "",
    ),
  );
}

export function renderPlanDrawer(h, ctx, row) {
  const fields = [
    ["Runtime", "runtime"],
    ["模型", "model"],
    ["推理", "effort"],
    ["Tier", "tier"],
  ];
  return [
    h(
      "header",
      { class: "drawer-head" },
      h("strong", null, row.agent_name),
      h("button", { type: "button", class: "drawer-close", "aria-label": "关闭", onclick: ctx.closeDrawer }, "×"),
    ),
    h("h3", { class: "drawer-sub" }, "身份"),
    h("p", null, `${row.workspace_name} · ${row.workspace_id}`),
    h("p", { class: "mono" }, row.agent_id, " ", copyButton(h, ctx, row.agent_id, "复制")),
    h("p", null, "所在 runtime ", rawText(row.fields.runtime?.old)),
    h("h3", { class: "drawer-sub" }, "字段 diff"),
    h(
      "table",
      { class: "plan-table" },
      h("tbody", null, fields.map(([label, key]) => {
        const field = row.fields[key] || {};
        return h(
          "tr",
          null,
          h("th", null, label),
          h("td", null, field.op || "omit"),
          h("td", { class: "mono" }, rawText(field.old)),
          h("td", { class: "mono" }, rawText(field.next)),
        );
      })),
    ),
    h("h3", { class: "drawer-sub" }, "预检"),
    ...(row.precheck || []).map((item) => h("p", null, `${item.label} ${item.state} · ${item.source || "未知"} · ${item.at || ""} · ${item.summary || ""}`)),
    h("h3", { class: "drawer-sub" }, "活动检查"),
    h("p", null, `读取 ${row.activity?.pages ?? "?"} 页 · 去重后 ${row.activity?.tasks ?? "?"} 个任务 · ${row.activity?.complete ? "已穷尽" : "未穷尽"}`),
    ...(row.activity?.runs || []).map((run) => h("p", null, `${run.status} · ${run.at}`)),
    h("h3", { class: "drawer-sub" }, "影响说明"),
    h("p", null, row.impact || ""),
    h("h3", { class: "drawer-sub" }, "源版本 / 指纹"),
    h("p", { class: "mono" }, `${row.source_version || ""} · ${row.fingerprint || ""}`),
    h("p", { class: "muted" }, "不是 CAS"),
    h("h3", { class: "drawer-sub" }, "原始行 JSON"),
    h("pre", { class: "plan-command" }, JSON.stringify(row, null, 2)),
    copyButton(h, ctx, JSON.stringify(row), "复制"),
  ];
}

export function collectText(node, found = []) {
  if (node == null || node === false) return found;
  if (typeof node === "string" || typeof node === "number") {
    found.push(String(node));
    return found;
  }
  for (const child of node.children || []) collectText(child, found);
  return found;
}

export function collectButtons(node, found = []) {
  if (!node || typeof node !== "object") return found;
  if (node.tag === "button") found.push(collectText(node).join("").replace(/\s+/g, " ").trim());
  for (const child of node.children || []) collectButtons(child, found);
  return found;
}
