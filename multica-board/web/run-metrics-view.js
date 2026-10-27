/** Read-only Atlas view of a run-metrics.v0.1 report. Rates and coverage come from the report. */

import { formatHash } from "./route.js";

export const SCHEMA = "run-metrics.v0.1";
export const WINDOWS = ["24h", "7d", "14d"];
const EVIDENCE_LIMIT = 40;
const FILTERS = [
  ["mws", "workspace", "by_workspace"],
  ["magent", "agent", "by_agent"],
  ["mrt", "runtime", "by_runtime"],
  ["missue", "issue", "by_issue"],
];
const COVERAGE_LABELS = {
  started_at: "开始时间",
  ended_at: "结束时间",
  duration_ms: "耗时",
  status: "状态",
  agent_id: "agent",
  runtime_id: "runtime",
  failure_class: "失败分类",
};

function count(value) {
  return Number.isInteger(value) ? String(value) : "缺失";
}

function schedulingBox(scheduling) {
  if (!scheduling || typeof scheduling !== "object" || Array.isArray(scheduling)) return null;
  return scheduling;
}

function boolFact(box, key) {
  if (!box || !Object.hasOwn(box, key) || box[key] == null) return { state: "missing", label: "missing" };
  if (box[key] === true) return { state: "true", label: "是" };
  if (box[key] === false) return { state: "false", label: "否" };
  return { state: "unknown", label: "unknown" };
}

function listFact(box, key) {
  if (!box || !Object.hasOwn(box, key) || box[key] == null) return { state: "missing", label: "missing", ids: null };
  if (!Array.isArray(box[key])) return { state: "unknown", label: "unknown", ids: null };
  if (box[key].length === 0) return { state: "empty", label: "0", ids: [] };
  return { state: "present", label: String(box[key].length), ids: box[key] };
}

function textFact(box, key) {
  if (!box || !Object.hasOwn(box, key) || box[key] == null) return { state: "missing", label: "missing" };
  if (typeof box[key] !== "string") return { state: "unknown", label: "unknown" };
  if (box[key] === "") return { state: "empty", label: "空" };
  return { state: "present", label: box[key] };
}

/** Keep true, false, an empty list, and a missing field as different facts. */
export function schedulingFacts(scheduling) {
  const box = schedulingBox(scheduling);
  return {
    unknownHardDisable: boolFact(box, "unknown_hard_disable"),
    historicalQuotaExcludes: boolFact(box, "historical_quota_excludes_agent"),
    excludedAgentIds: listFact(box, "excluded_agent_ids"),
    historicalQuotaAgentIds: listFact(box, "historical_quota_agent_ids"),
    historicalQuotaNote: textFact(box, "historical_quota_note"),
    signals: listFact(box, "signals"),
  };
}

function listLabel(fact, { empty = "0", present } = {}) {
  if (fact.state === "missing" || fact.state === "unknown") return fact.label;
  if (fact.state === "empty") return empty;
  return present ? present(fact) : fact.label;
}

function ratioText(block) {
  if (!block || !Number.isInteger(block.numerator) || !Number.isInteger(block.denominator)) return "缺失";
  const rate = block.rate == null ? "无比率" : block.rate;
  return `${block.numerator} / ${block.denominator} · 报告比率 ${rate}`;
}

export function selectedWindow(payload, route) {
  const report = payload?.report;
  const fallback = WINDOWS.includes(report?.default_window) ? report.default_window : "14d";
  return WINDOWS.includes(route?.win) ? route.win : fallback;
}

function activeFilters(route) {
  return FILTERS.filter(([key]) => route?.[key]).map(([key, label]) => [label, route[key], key]);
}

export function metricsScopeNote(payload, route) {
  if (!payload) return "范围：run-metrics.v0.1 · 正在读取";
  if (payload.ok === false || payload.report?.schema_version !== SCHEMA) return "范围：run-metrics.v0.1 · 快照不可用";
  const filters = activeFilters(route || {});
  const filterText = filters.length ? filters.map(([label, id]) => `${label} ${id}`).join(" · ") : "未按 workspace、agent、runtime、issue 筛选";
  return `范围：run-metrics.v0.1 · 窗口 ${selectedWindow(payload, route)} · ${filterText}`;
}

export function runMetricsRefreshKey(payload) {
  if (!payload) return "";
  const report = payload.report;
  const windowName = report?.default_window;
  return [payload.ok, payload.reason, report?.schema_version, report?.generated_at, report?.rule_version, report?.windows?.[windowName]?.run_count, report?.runs?.length].join("|");
}

function coverageRows(window) {
  const fields = window?.field_coverage || {};
  return Object.keys(COVERAGE_LABELS).map((key) => {
    const row = fields[key] || {};
    return {
      key,
      label: COVERAGE_LABELS[key],
      known: count(row.known_count),
      unknown: count(row.unknown_count),
      missing: count(row.missing_count),
      population: count(row.population),
      missingRate: row.missing_rate == null ? "无比率" : String(row.missing_rate),
      unknownRate: row.unknown_rate == null ? "无比率" : String(row.unknown_rate),
      definition: row.definition || "口径缺失",
    };
  });
}

function runsInWindow(report, window) {
  const runs = Array.isArray(report?.runs) ? report.runs : [];
  if (!window?.start || !window?.end) return [];
  return runs.filter((run) => run?.anchor_at && run.anchor_at >= window.start && run.anchor_at <= window.end);
}

function filteredRuns(runs, route) {
  return runs.filter((run) => {
    if (route.mws && run.workspace_id !== route.mws) return false;
    if (route.magent && run.agent_id !== route.magent) return false;
    if (route.mrt && run.runtime_id !== route.mrt) return false;
    if (route.missue && run.issue_id !== route.missue) return false;
    return true;
  });
}

function textHit(query, parts) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return true;
  return parts.some((part) => String(part ?? "").toLowerCase().includes(q));
}

function evidenceParts(run) {
  const evidence = run.failure_evidence || {};
  return [run.run_id, run.issue_id, run.agent_id, run.runtime_id, run.workspace_id, run.status, run.failure_class, run.failure_rule_id, evidence.rule_id, evidence.source_field, evidence.condition, evidence.excerpt, run.raw_reason_redacted];
}

export function presentRunMetrics(payload, route = {}) {
  const report = payload?.report;
  if (!payload || payload.ok === false || report?.schema_version !== SCHEMA) {
    return {
      ok: false,
      readonly: true,
      writes: [],
      message: payload?.message || "运行指标还没接上。",
      scopeNote: metricsScopeNote(payload, route),
    };
  }
  const windowName = selectedWindow(payload, route);
  const window = report.windows?.[windowName];
  const filters = activeFilters(route);
  const single = filters.length === 1 ? filters[0] : null;
  const spec = single ? FILTERS.find(([key]) => key === single[2]) : null;
  const group = spec ? window?.groups?.[spec[2]]?.[single[1]] : null;
  let groupRates = null;
  let groupNote = null;
  if (filters.length > 1) groupNote = "交叉筛选没有预聚合成功率。页面不重算。";
  else if (single && !group) groupNote = `${single[0]} ${single[1]} 在这个窗口没有预聚合行。页面不补 0。`;
  else if (group) {
    groupRates = {
      label: `${single[0]} ${single[1]}`,
      including: group.success_rate?.including_cancelled,
      excluding: group.success_rate?.excluding_cancelled,
    };
  }
  const pool = runsInWindow(report, window);
  const matched = filteredRuns(pool, route).filter((run) => textHit(route.q, evidenceParts(run)));
  const chains = Array.isArray(window?.rework?.chains) ? window.rework.chains : [];
  const chainRows = chains.filter((chain) => (!route.missue || chain.issue_id === route.missue) && textHit(route.q, [chain.issue_id, chain.issue_identifier, chain.confidence, chain.reason, chain.note, ...(chain.evidence_ids || []), chain.source_run_id, chain.rework_run_id]));
  const gaps = [];
  const collection = report.collection;
  if (!collection || typeof collection !== "object") gaps.push("collection 字段缺失。");
  else {
    for (const [key, label] of [
      ["run_missing_ids", "run 明细缺失"],
      ["timeline_missing_ids", "timeline 缺失"],
      ["timeline_truncated_ids", "timeline 被截断"],
      ["comment_missing_ids", "评论摘要缺失"],
      ["runs_missing_anchor", "run 缺少 anchor"],
    ]) {
      const value = collection[key];
      if (!Array.isArray(value)) gaps.push(`${label}：字段缺失`);
      else if (value.length) gaps.push(`${label}：${value.length}`);
    }
    if (Array.isArray(collection.errors) && collection.errors.length) gaps.push(`采集错误：${collection.errors.length}`);
    if (collection.partial) gaps.push("collection.partial 为 true，窗口数字可能不完整。");
  }
  if (!Array.isArray(report.runs)) gaps.push("报告没有逐条 runs，evidence 明细缺失。");
  else if (windowName !== report.default_window) gaps.push("逐条 evidence 只附在默认窗口的 runs 里；更短窗口只显示其中 anchor 落在窗口内的记录。");
  if (filters.length) gaps.push("已知、未知、缺失和返工下限保持窗口口径，不按筛选重算。");
  if (route.q) gaps.push("搜索只收窄 evidence 和返工链，不改变成功率、覆盖率或返工下限。");
  if (groupNote) gaps.push(groupNote);
  const unstable = report.provider_unstable_class || {};
  if (unstable.emitted !== false || unstable.run_count !== 0) gaps.push("报告产出了未批准的 provider_unstable。页面只展示，不把它当成可用分类。");
  if (window?.failure_class_counts && Object.hasOwn(window.failure_class_counts, "provider_unstable")) gaps.push("窗口计数里出现 provider_unstable。");
  const schedulingFactsForReport = schedulingFacts(report.scheduling);
  if (schedulingFactsForReport.unknownHardDisable.state === "true") gaps.push("报告把 unknown 标成硬禁用，和已冻结口径冲突。页面没有开关可以改它。");
  if (schedulingFactsForReport.unknownHardDisable.state === "missing") gaps.push("scheduling.unknown_hard_disable 缺失。页面不把它当成否。");
  if (schedulingFactsForReport.historicalQuotaExcludes.state === "true" || schedulingFactsForReport.excludedAgentIds.state === "present") gaps.push("报告含历史 quota 排除名单。页面没有入口执行排除。");
  if (schedulingFactsForReport.historicalQuotaExcludes.state === "missing") gaps.push("scheduling.historical_quota_excludes_agent 缺失。页面不把它当成是。");
  if (schedulingFactsForReport.excludedAgentIds.state === "missing") gaps.push("scheduling.excluded_agent_ids 缺失。页面不把它当成 0。");
  if (schedulingFactsForReport.historicalQuotaAgentIds.state === "missing") gaps.push("scheduling.historical_quota_agent_ids 缺失。页面不把它当成无。");
  if (schedulingFactsForReport.signals.state === "missing") gaps.push("scheduling.signals 缺失。页面不把它当成 0。");
  const rework = window?.rework || {};
  const unknownRework = rework.rework_coverage?.unknown_count;
  if (Number.isInteger(unknownRework) && unknownRework > 0 && rework.rework_rate != null) gaps.push("覆盖不足时报告仍给了返工率点估计。页面只展示下限。");
  const recon = report.runtime_reconciliation || {};
  if (recon.atlas_gemini_display?.rechecked_in_atlas_ui !== true) gaps.push("gemini 在 Atlas 上的 0/1 显示尚未在本页复核。");
  if (recon.quota_remaining_present !== true) gaps.push("runtime usage 没有剩余额度字段。");
  return {
    ok: true,
    readonly: true,
    writes: [],
    schemaVersion: report.schema_version,
    ruleVersion: report.rule_version || "缺失",
    generatedAt: report.generated_at || "缺失",
    windowName,
    defaultWindow: report.default_window,
    scopeNote: metricsScopeNote(payload, route),
    windowRates: {
      including: window?.success_rate?.including_cancelled,
      excluding: window?.success_rate?.excluding_cancelled,
    },
    groupRates,
    groupNote,
    failureCounts: window?.failure_class_counts || {},
    groupFailureCounts: group?.failure_class_counts || null,
    coverage: coverageRows(window),
    rework,
    chains: chainRows.slice(0, EVIDENCE_LIMIT),
    chainHidden: Math.max(0, chainRows.length - EVIDENCE_LIMIT),
    evidence: matched.slice(0, EVIDENCE_LIMIT),
    evidenceHidden: Math.max(0, matched.length - EVIDENCE_LIMIT),
    evidenceTotal: matched.length,
    rules: Array.isArray(window?.failure_rules) ? window.failure_rules : [],
    reconciliation: recon,
    timeline: report.timeline_from_to || {},
    scheduling: report.scheduling,
    schedulingView: schedulingFactsForReport,
    unstable,
    gaps,
    filterOptions: Object.fromEntries(FILTERS.map(([key, , groupName]) => [key, optionIds(window, groupName, route[key])])),
  };
}

function optionIds(window, groupName, selected) {
  const keys = Object.keys(window?.groups?.[groupName] || {});
  if (selected && !keys.includes(selected)) keys.push(selected);
  return keys.sort();
}

function metric(h, label, value) {
  return h("div", { class: "usage-metric" }, h("span", null, label), h("b", null, value));
}

function ratioBlock(h, title, block) {
  return h("p", null, `${title} ${ratioText(block)}`, block?.definition ? h("span", { class: "muted" }, ` ${block.definition}`) : null);
}

export function writeControlTags(nodes) {
  const found = [];
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    if (["form", "button", "textarea"].includes(node.tag)) found.push(node.tag);
    if (node.tag === "input") found.push("input");
    if (node.attrs?.action || node.attrs?.formaction) found.push("action");
    for (const child of node.children || []) walk(child);
  };
  for (const node of [].concat(nodes || [])) walk(node);
  return found;
}

export function renderRunMetrics(ctx) {
  const { h } = ctx;
  const route = ctx.route || {};
  const payload = ctx.runMetrics;
  if (!payload) return [h("div", { class: "empty big" }, "正在读取运行指标…")];
  const model = presentRunMetrics(payload, route);
  if (!model.ok) {
    return [h("section", { class: "atlas-guide", "data-readonly": "true", "data-writes": "0", "aria-label": "只读边界" }, h("h2", null, "运行指标还没接上"), h("p", null, model.message), h("p", { class: "muted" }, "只读。没有写入、派单或排除入口。"))];
  }
  const setFilter = (key) => (event) => ctx.setRoute?.({ [key]: event.target.value });
  return [
    h(
      "section",
      { class: "usage-head", "data-readonly": "true", "data-writes": "0", "aria-label": "只读边界" },
      h("h2", null, "运行指标 / 返工"),
      h("p", null, `${model.schemaVersion} · ${model.ruleVersion} · 生成于 ${model.generatedAt}`),
      h("p", { class: "muted" }, "只读。数字来自 S3 报告。本页不采集、不重算口径、不改调度。"),
      h("div", { class: "metrics-windows", "aria-label": "时间窗" }, WINDOWS.map((name) => h("a", {
        href: formatHash({ ...route, view: "metrics", win: name }),
        "aria-current": model.windowName === name ? "true" : "false",
        onclick: (event) => { event.preventDefault(); ctx.setRoute?.({ view: "metrics", win: name }); },
      }, name === model.defaultWindow ? `${name} 默认` : name))),
      h("div", { class: "metrics-filters" }, FILTERS.map(([key, label]) => h(
        "label",
        null,
        label,
        h("select", { "aria-label": label, onchange: setFilter(key) }, h("option", { value: "", selected: !route[key] }, "未选"), ...(model.filterOptions[key] || []).map((id) => h("option", { value: id, selected: route[key] === id }, id))),
      ))),
    ),
    h(
      "div",
      { class: "usage-columns" },
      h(
        "section",
        { class: "usage-col", "aria-label": "双成功率" },
        h("h3", null, `双成功率 · 窗口 ${model.windowName}`),
        ratioBlock(h, "含取消", model.windowRates.including),
        ratioBlock(h, "不含取消", model.windowRates.excluding),
        model.groupRates ? h("div", { class: "usage-card" }, h("h4", null, `预聚合 ${model.groupRates.label}`), ratioBlock(h, "含取消", model.groupRates.including), ratioBlock(h, "不含取消", model.groupRates.excluding)) : null,
        model.groupNote ? h("p", { class: "usage-gap" }, model.groupNote) : null,
        h("p", { class: "muted" }, "失败分类计数（窗口）"),
        countList(h, model.failureCounts),
        model.groupFailureCounts ? h("p", { class: "muted" }, "失败分类计数（预聚合）") : null,
        model.groupFailureCounts ? countList(h, model.groupFailureCounts) : null,
      ),
      h(
        "section",
        { class: "usage-col", "aria-label": "已知未知缺失" },
        h("h3", null, "已知 / 未知 / 缺失"),
        h("p", { class: "muted" }, "窗口 run 口径。筛选不改变这些数。"),
        ...model.coverage.map((row) => h("div", { class: "usage-card" }, h("h4", null, row.label), metric(h, "已知", row.known), metric(h, "未知", row.unknown), metric(h, "缺失", row.missing), metric(h, "分母", row.population), metric(h, "缺失率", row.missingRate), metric(h, "未知率", row.unknownRate), h("p", { class: "muted" }, row.definition))),
      ),
      h(
        "section",
        { class: "usage-col", "aria-label": "返工下限" },
        h("h3", null, "返工下限"),
        metric(h, "explicit", count(model.rework.rework_coverage?.explicit_count)),
        metric(h, "inferred", count(model.rework.rework_coverage?.inferred_count)),
        metric(h, "unknown", count(model.rework.rework_coverage?.unknown_count)),
        metric(h, "下限", ratioText(model.rework.rework_rate_lower_bound)),
        metric(h, "点估计", model.rework.rework_rate == null ? "不提供" : "报告含点估计，页面不采用"),
        h("p", { class: "muted" }, model.rework.rework_rate_note || "返工说明缺失"),
        h("p", { class: "muted" }, model.rework.rework_rate_claim || "claim 缺失"),
      ),
    ),
    h(
      "section",
      { class: "usage-col", "aria-label": "runtime reconciliation" },
      h("h3", null, "runtime reconciliation"),
      metric(h, "观察到", count(model.reconciliation.observed_count)),
      metric(h, "online", count(model.reconciliation.online_count)),
      metric(h, "非 online", count(model.reconciliation.offline_count)),
      metric(h, "S1 记录", count(model.reconciliation.s1_claim?.runtime_count)),
      h("p", { class: "muted" }, model.reconciliation.s1_claim?.all_online === true ? "S1 记录全部 online。" : "S1 并未记录全部 online。"),
      ...(model.reconciliation.discrepancies || []).map((line) => h("p", null, line)),
      ...(model.reconciliation.unverified || []).map((line) => h("p", { class: "usage-gap" }, line)),
      h("p", { class: "muted" }, `gemini 显示复核：${model.reconciliation.atlas_gemini_display?.rechecked_in_atlas_ui === true ? "已复核" : "未复核"}`),
      h("p", { class: "muted" }, model.timeline.compared_with_s1 || "没有 from/to 对照说明。"),
      h("h4", null, "调度信号（只展示）"),
      metric(h, "unknown 硬禁用", model.schedulingView.unknownHardDisable.label),
      metric(h, "历史 quota 排除", model.schedulingView.historicalQuotaExcludes.label),
      metric(h, "排除名单", listLabel(model.schedulingView.excludedAgentIds)),
      h("p", { class: "muted" }, model.schedulingView.historicalQuotaNote.label),
      h("p", { class: "muted" }, `历史 quota agent：${listLabel(model.schedulingView.historicalQuotaAgentIds, { empty: "空", present: (fact) => fact.ids.join("、") })}`),
      h("p", { class: "muted" }, `信号 ${listLabel(model.schedulingView.signals, { empty: "0 条", present: (fact) => `${fact.label} 条` })}。健康度来自报告，本页不能修改。`),
    ),
    h(
      "section",
      { class: "usage-col", "aria-label": "evidence" },
      h("h3", null, "evidence"),
      h("p", { class: "muted" }, `provider_unstable 产出：${model.unstable.emitted === false ? "否" : "是"} · run ${count(model.unstable.run_count)}`),
      ...(model.unstable.candidates || []).map((row) => h("p", null, `${row.id || "未知候选"} · 样本 ${count(row.sample_count)}`)),
      h("p", { class: "muted" }, "规则命中（本窗口预聚合）"),
      ...model.rules.map((rule) => h("p", null, `${rule.id || "缺失"} · ${rule.failure_class || "无分类"} · 命中 ${count(rule.hit_count)}`)),
      h("p", { class: "muted" }, `逐条 evidence ${model.evidenceTotal} 条，下面展开 ${model.evidence.length} 条。`),
      ...model.evidence.map((run) => evidenceRow(h, run)),
      model.evidenceHidden ? h("p", { class: "usage-gap" }, `其余 ${model.evidenceHidden} 条留在报告里，页面不另算。`) : null,
      h("h4", null, "返工链"),
      ...model.chains.map((chain) => h("details", { class: "usage-card" }, h("summary", null, `${chain.issue_identifier || chain.issue_id || "未知 issue"} · ${chain.confidence || "缺失"} · ${chain.reason || "原因缺失"}`), h("p", null, `evidence ${Array.isArray(chain.evidence_ids) ? chain.evidence_ids.join("、") || "无" : "字段缺失"}`), h("p", { class: "muted" }, chain.note || "没有补充说明。"))),
      model.chainHidden ? h("p", { class: "usage-gap" }, `其余 ${model.chainHidden} 条返工链留在报告里。`) : null,
      model.chains.length ? null : h("p", { class: "muted" }, "当前窗口和筛选下没有返工链。"),
    ),
    h(
      "section",
      { class: "usage-col", "aria-label": "数据缺口" },
      h("h3", null, "数据缺口"),
      ...model.gaps.map((line) => h("p", null, line)),
      model.gaps.length ? null : h("p", { class: "muted" }, "这份快照没有额外缺口标记。"),
    ),
  ];
}

function countList(h, counts) {
  const entries = Object.entries(counts || {});
  if (!entries.length) return h("p", { class: "muted" }, "没有失败分类计数。");
  return h("div", null, ...entries.map(([name, value]) => metric(h, name, count(value))));
}

function evidenceRow(h, run) {
  const evidence = run.failure_evidence || {};
  const ruleId = run.failure_rule_id || evidence.rule_id || "无规则";
  return h(
    "details",
    { class: "usage-card" },
    h("summary", null, `${run.run_id || "未知 run"} · ${run.status || "状态缺失"} · ${run.failure_class || "无失败分类"} · ${ruleId}`),
    h("p", null, `规则 ${ruleId} · 版本 ${run.failure_rule_version || evidence.rule_version || "缺失"}`),
    h("p", null, `字段 ${evidence.source_field || "缺失"}`),
    h("p", null, evidence.condition || "条件缺失"),
    h("p", null, evidence.excerpt || "没有摘录"),
    h("p", { class: "muted" }, `可恢复 ${run.failure_recoverable == null ? "未知" : run.failure_recoverable} · recovery_at ${run.recovery_at || "无"}`),
  );
}
