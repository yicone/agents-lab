import { splitFrontmatter, markdownNodes, lineDiff } from "./skill-viewer.js";
/** Skills governance page. Buckets come from the API; this file only displays them. */

import { contentKey, CONTENT_LABEL } from "./skill-content.js";

/** 伪来源 id：选中时按「已安装到 Multica Workspace」筛选，不是 source_class。 */
export const CLOUD_SOURCE = "cloud";
export const skillKey = item => item.key || item.name;
export const skillRowId = item => `skill-${item.key ? encodeURIComponent(item.key) : item.name}`;
const isGrokBot = item => item.origin === "grok_bot";
const notApplicable = (h, detail) => h("span", { class: "not-applicable", title: `∅ 不适用：${detail}` }, "∅");

export function isCloudItem(item) {
  return Array.isArray(item?.workspace_installs) && item.workspace_installs.length > 0;
}

/** 来源卡片/下拉用的块列表：后端 summary 块 + 末尾合成的 Multica Workspace 块。 */
export function sourceBlocks(payload) {
  const items = payload?.items || [];
  const cloudItems = items.filter(isCloudItem);
  const statuses = {};
  for (const item of cloudItems) {
    const key = item.reconciliation_status || "";
    statuses[key] = (statuses[key] || 0) + 1;
  }
  // 没有任何条目带 workspace_installs 数组 ⇒ Multica Workspace 清单从未采集，不是「装了 0 个」。
  const uncollected = !items.some((item) => Array.isArray(item?.workspace_installs));
  // source_class 里也有叫「Multica Workspace」的来源块（仅 Multica Workspace 条目），这里加后缀区分。
  return [...(payload?.summary || []), { id: CLOUD_SOURCE, label: "Multica Workspace · 已安装", count: cloudItems.length, statuses, uncollected }];
}

export const BUCKETS = [
  { id: "missing", title: "缺失 / 坏链" },
  { id: "version_changed", title: "版本已变" },
  { id: "unrecorded", title: "未登记安装" },
  { id: "capability_pending", title: "能力变化待批" },
  { id: "modified", title: "内容漂移" },
  { id: "upgradable", title: "可升级" },
  { id: "overlap", title: "职责重叠" },
];

export const TOOLS = [
  { id: "cursor", label: "Cursor" },
  { id: "codex", label: "Codex" },
  { id: "claude", label: "Claude Code" },
  { id: "hermes", label: "Hermes Agent" },
  { id: "gemini", label: "Gemini CLI" },
  { id: "grok", label: "Grok CLI" },
  { id: "project", label: "仓库入口" },
];

const STATUS_CLASS = {
  aligned: "ok",
  stale: "review-pending",
  version_changed: "warn",
  modified: "bad",
  unverified: "info",
  unrecorded: "info",
  missing: "bad",
};

export const RUNTIME_LEGEND = "● 直接入口 · ○ 经共享目录 · ✕ 入口或版本文件缺失 · ∅ 不适用（仅 Multica Workspace 或 Grok Bot 名称声明） · ? 无法确定（含未配置技能根） · 空白：已配置根未命中，仍可能从其他目录加载；Multica Workspace 列 — 表示数据未采集。路径证据不代表当前会话实际加载。Codex / Cursor / Hermes 的加载探针只区分：已加载（有证据）、磁盘可见、未验证、未见；显式禁用为未加载（有证据）。";
const MARK = { direct: "●", shared: "○", missing: "✕", unknown: "?" };
const GROUP_LIMIT = 8;

const SHARED_LABEL = {
  unscanned: "未扫描",
  denied: "无权限",
  unknown: "无法确定",
  none: "无入口",
  broken_link: "坏链",
  content_missing: "缺 SKILL.md",
  link: "链接",
  dir: "目录",
  legacy: "旧数据",
};
const SHARED_CLASS = {
  dir: "ok",
  link: "ok",
  none: "muted",
  broken_link: "bad",
  content_missing: "bad",
  denied: "warn",
  unknown: "warn",
  unscanned: "info",
  legacy: "info",
};
const ANOMALY_STATES = [
  ["broken_link", "坏链"],
  ["content_missing", "缺 SKILL.md"],
  ["denied", "无权限"],
  ["unknown", "无法确定"],
];
const ROOT_BANNER_LINE = {
  absent: "共享根目录不存在：以下全部显示无入口",
  denied: "共享根目录无权限，共享入口未检查",
  unknown: "共享根目录无法确定，以下状态以单项探测为准",
  unscanned: "配置里没有共享根，共享入口未检查",
};

export function sharedEntryOf(item, payload) {
  if (isGrokBot(item)) return { state: "not_applicable", label: "∅", detail: "Grok Bot 仅有名称声明，本机共享入口不适用" };
  const schema = payload?.skills_schema;
  if (schema === undefined || schema === null || Number(schema) < 2) {
    return { state: "legacy", label: SHARED_LABEL.legacy, reason: "legacy_payload", path: null, target: null, resolved: null, detail: "后端数据版本较旧，没有共享入口/来源证据，未检查" };
  }
  const entry = item?.shared_entry;
  if (!entry || typeof entry !== "object" || typeof entry.state !== "string" || !SHARED_LABEL[entry.state]) {
    return { state: "unknown", label: SHARED_LABEL.unknown, reason: "field_invalid", path: null, target: null, resolved: null, detail: "共享入口字段缺失或不可识别" };
  }
  return { ...entry, label: entry.label || SHARED_LABEL[entry.state] };
}

export function sharedSummary(payload) {
  const counts = {};
  for (const item of payload?.items || []) {
    const state = sharedEntryOf(item, payload).state;
    counts[state] = (counts[state] || 0) + 1;
  }
  return counts;
}

export function badgeOf(payload) {
  return payload?.attention?.badge || 0;
}

function installStamp(payload) {
  const bits = [];
  for (const item of payload?.items || []) {
    if (isGrokBot(item)) bits.push(`${skillKey(item)}:${JSON.stringify(item.grok_bots || [])}`);
    if (item.local_source) bits.push(`local:${item.name}:${item.local_source.updated_at || ""}:${item.local_source.content_hash || ""}`);
    for (const install of item.workspace_installs || []) {
      const agents = (install.agents || []).map((agent) => `${agent.id}:${agent.enabled === false ? 0 : 1}`).join(",");
      bits.push(`${item.name}:${install.workspace_id}:${install.id}:${install.updated_at || ""}:${install.content_hash || ""}:${agents}`);
    }
  }
  return bits.join("\n");
}

export function skillsRefreshKey(payload) {
  if (!payload) return "";
  const attention = payload.attention || {};
  return [
    payload.ok === false ? "bad" : "ok",
    payload.error?.code || "",
    payload.error?.message || "",
    payload.generated_at || "",
    attention.badge ?? "",
    attention.baseline_debt ?? "",
    installStamp(payload),
  ].join("\n");
}

export function queueModel(payload) {
  const attention = payload?.attention;
  return BUCKETS.map((bucket) => {
    const raw = attention?.buckets?.[bucket.id] || {};
    return {
      ...bucket,
      connected: raw.connected !== false,
      reason: raw.reason || "",
      items: raw.items || [],
    };
  });
}

function haystack(item) {
  return [
    item.name,
    item.channel_detail,
    item.channel_label,
    item.source_label,
    item.current_version,
    item.observed_version,
    item.install_path,
    ...(item.grok_bots || []).map(bot => bot.name),
    ...(item.findings || []).map((finding) => finding.code),
    ...(item.entries || []).map((entry) => entry.path),
    ...(item.workspace_installs || []).flatMap((install) => [
      install.workspace_name,
      install.workspace_slug,
      install.description,
      ...(install.agents || []).map((agent) => agent.name),
    ]),
  ]
    .filter(Boolean)
    .join("\n")
    .toLowerCase();
}

export function filteredItems(payload, route) {
  const q = (route.q || "").trim().toLowerCase();
  return (payload?.items || []).filter((item) => {
    if (route.source === CLOUD_SOURCE) {
      if (!isCloudItem(item)) return false;
    } else if (route.source && item.source_class !== route.source) return false;
    if (route.skillStatus && item.reconciliation_status !== route.skillStatus) return false;
    if (route.tool && !item.tools?.[route.tool]) return false;
    if (route.custom && !(item.constraint_count || (item.customization_mode && item.customization_mode !== "none"))) return false;
    if (route.debt && !item.baseline_debt) return false;
    if (q && !haystack(item).includes(q)) return false;
    return true;
  });
}

function byAttention(a, b) {
  const rank = (item) => (item.bucket ? 0 : 1);
  return rank(a) - rank(b) || String(a.name).localeCompare(String(b.name));
}

export function groupedItems(items, group, tools) {
  if (group === "flat") return [{ id: "all", label: "全部", note: "", items: [...items].sort(byAttention) }];
  if (group === "tool") {
    const groups = tools
      .map((tool) => ({
        id: tool.id,
        label: tool.label,
        note: "同一 skill 可能重复出现",
        items: items.filter((item) => item.tools?.[tool.id]).sort(byAttention),
      }))
      .filter((entry) => entry.items.length);
    const bots = items.filter(isGrokBot);
    if (bots.length) groups.push({id: "grok_bot", label: "Grok Bot", note: "仅名称声明，Runtime 不适用", items: bots.sort(byAttention)});
    return groups;
  }
  const order = [];
  const map = new Map();
  for (const item of items) {
    const id = item.source_class || "unknown";
    if (!map.has(id)) {
      map.set(id, { id, label: item.source_label || id, note: "", items: [] });
      order.push(id);
    }
    map.get(id).items.push(item);
  }
  return order.map((id) => {
    const entry = map.get(id);
    entry.items.sort(byAttention);
    return entry;
  });
}

function problemLine(statuses) {
  const labels = [
    ["version_changed", "版本已变"],
    ["missing", "缺失"],
    ["modified", "已修改"],
    ["unverified", "未核实"],
    ["unrecorded", "未登记"],
    ["stale", "未复核"],
    ["aligned", "一致"],
  ];
  return labels
    .filter(([key]) => statuses?.[key])
    .map(([key, label]) => ({ key, label, count: statuses[key], bad: key !== "stale" && key !== "aligned" }));
}

function relTime(iso, ago) {
  if (!iso) return "从未";
  return ago ? ago(iso) : iso;
}

export function renderSkills(ctx) {
  const { h, route } = ctx;
  const payload = ctx.skills;
  if (!payload) return [h("div", { class: "empty big" }, "正在读取 Skills 清单…")];
  if (payload.ok === false) {
    return [
      h(
        "section",
        { class: "atlas-guide" },
        h("h2", null, "Skills 清单无效"),
        h("p", null, payload.error?.message || "清单读不出来，页面不会把没检查显示成没问题。"),
        h("p", { class: "muted" }, "修一下 skills-inventory.json 之后刷新。"),
      ),
    ];
  }
  const tools = payload.tools?.length ? payload.tools : TOOLS;
  const harness = tools.filter((tool) => tool.id !== "project");
  const projectTool = tools.find((tool) => tool.id === "project") || null;
  const items = filteredItems(payload, route);
  const groups = groupedItems(items, route.skillGroup || "source", tools);
  const blocks = sourceBlocks(payload);
  const visible = new Map(tools.map((tool) => [tool.id, (payload.items || []).filter((item) => item.tools?.[tool.id] === "direct" || item.tools?.[tool.id] === "shared").length]));
  return [
    h("div", { class: "source-grid" }, blocks.map((block) => sourceCard(ctx, block))),
    h(
      "div",
      { class: "toolbar" },
      select(h, "来源", route.source, [["", "全部来源"], ...blocks.filter((block) => block.id !== "all").map((block) => [block.id, block.label])], (value) => ctx.setRoute({ source: value })),
      select(h, "状态", route.skillStatus, [["", "全部状态"], ["aligned", "一致"], ["stale", "未复核"], ["version_changed", "版本已变"], ["modified", "已修改"], ["unrecorded", "未登记"], ["unverified", "未核实"], ["missing", "缺失"]], (value) => ctx.setRoute({ skillStatus: value })),
      select(h, "工具", route.tool, [["", "全部工具"], ...tools.map((tool) => [tool.id, tool.label])], (value) => ctx.setRoute({ tool: value })),
      select(h, "分组", route.skillGroup || "source", [["source", "按来源"], ["tool", "按工具"], ["flat", "不分组"]], (value) => ctx.setRoute({ skillGroup: value })),
      h(
        "label",
        { class: "check" },
        h("input", { type: "checkbox", checked: route.custom, onchange: (e) => ctx.setRoute({ custom: e.target.checked }) }),
        "只看定制 / 有约束",
      ),
      h("button", { type: "button", class: "text-btn", onclick: () => ctx.setRoute({ evidence: true, source: "" }) }, "证据清单"),
    ),
    anomalyBanner(h, payload),
    legend(h),
    h(
      "div",
      { class: "skills-layout" },
      h("div", { class: "skills-main" }, table(ctx, tools, harness, projectTool, groups, visible, payload)),
      queue(ctx, payload),
    ),
  ].filter(Boolean);
}

function anomalyBanner(h, payload) {
  const items = payload?.items || [];
  const summary = sharedSummary(payload);
  const parts = ANOMALY_STATES.filter(([state]) => summary[state]).map(([state, label]) => `${label} ${summary[state]}`);
  if (summary.unscanned) parts.push(`未扫描 ${summary.unscanned}（≠ 无入口）`);
  const rootLine = ROOT_BANNER_LINE[payload?.shared_root?.state] || null;
  const lines = [];
  if (rootLine) lines.push(rootLine);
  if (summary.legacy) lines.push("后端数据版本较旧，共享入口与仓库入口未检查");
  if (parts.length) lines.push(`共享入口异常（共 ${items.length} 项）：${parts.join(" · ")}`);
  if (!lines.length) return null;
  return h(
    "div",
    { class: "skills-anomaly", role: "status" },
    lines.map((text) => h("div", null, text)),
  );
}

function legend(h) {
  return h(
    "div",
    { class: "skills-legend" },
    h(
      "span",
      { class: "legend-group" },
      h("strong", null, "共享入口 "),
      "目录/链接=共享目录里有有效入口 · 无入口=共享目录里没有 · 坏链=链接目标不在 · 缺 SKILL.md=入口在但缺文件 · 无权限/无法确定=读不出来 · 未扫描=配置里没有共享根，未检查 ≠ 没有 · 旧数据=后端版本较旧",
    ),
    h(
      "span",
      { class: "legend-group" },
      h("strong", null, "Harness "),
      RUNTIME_LEGEND,
    ),
    h("span", { class: "legend-group" }, h("strong", null, "仓库入口 "), "仓库名 + 入口位置（.agents / .codex / .cursor）"),
    h(
      "span",
      { class: "legend-group" },
      h("strong", null, "Multica Workspace"),
      "各 Workspace 的安装，以及已启用的 Agent。优先比较内容哈希；不同时提示 Multica Workspace 内容不同，不推断新旧。缺少哈希时才按时间推断 Multica Workspace 较旧，并注明依据；时间未知时仅比较完整描述。— 表示 Multica Workspace 数据未采集。Multica Workspace 筛选包含仅 Multica Workspace 条目；Multica Workspace 正文未采集。",
    ),
  );
}

function sourceCard(ctx, block) {
  const { h, route } = ctx;
  const id = block.id === "all" ? "" : block.id;
  const active = (route.source || "") === id;
  const bits = problemLine(block.statuses);
  return h(
    "button",
    {
      type: "button",
      class: `source-card${active ? " active" : ""}`,
      title: block.detail || block.label,
      "aria-pressed": active ? "true" : "false",
      onclick: () => ctx.setRoute({ source: active ? "" : id }),
    },
    h("span", { class: "source-label" }, block.label),
    h("strong", null, block.uncollected ? "—" : block.count),
    h(
      "span",
      { class: "source-recon" },
      block.uncollected
        ? h("span", { class: "muted" }, block.detail || "Multica Workspace 数据未采集")
        : bits.length
          ? bits.map((bit) => h("span", { class: bit.bad ? "bad-n" : "muted" }, `${bit.label} ${bit.count}`))
          : h("span", { class: "muted" }, "—"),
    ),
  );
}

function select(h, label, value, options, onchange) {
  return h(
    "label",
    { class: "filter" },
    label,
    h(
      "select",
      { value, onchange: (e) => onchange(e.target.value) },
      options.map(([id, text]) => h("option", { value: id, selected: id === value }, text)),
    ),
  );
}

function table(ctx, tools, harness, projectTool, groups, visible, payload) {
  const { h } = ctx;
  const head = h(
    "tr",
    null,
    h("th", null, "Skill"),
    h("th", { class: "channel-col" }, "来源 / 更新通道"),
    h("th", { title: "Multica Workspace（跨 Workspace）" }, "Multica Workspace"),
    h("th", null, "共享入口"),
    ...harness.map((tool) => h("th", { class: "tool-col", title: `${tool.label} 可见 ${visible.get(tool.id) || 0}\n${RUNTIME_LEGEND}\n${columnEvidence(tool)}` }, tool.label, h("small", null, String(visible.get(tool.id) || 0)))),
    projectTool ? h("th", { class: "tool-col repo-col", title: `仓库入口 可见 ${visible.get(projectTool.id) || 0}` }, "仓库入口", h("small", null, String(visible.get(projectTool.id) || 0))) : null,
    h("th", { class: "version-col", title: "版本：记录 → 实际" }, "版本"),
    h("th", null, "对账"),
    h("th", null, "定制 · 约束"),
    h("th", null, "复核"),
  );
  const body = [];
  for (const group of groups) {
    const open = ctx.expandedSkills?.has(group.id);
    const shown = open ? group.items : group.items.slice(0, GROUP_LIMIT);
    const rest = group.items.length - shown.length;
    body.push(
      h(
        "tr",
        { class: "skill-group" },
        h("th", { colspan: String(8 + tools.length) }, group.label, h("span", { class: "muted" }, ` · ${group.items.length}`), group.note ? h("span", { class: "muted" }, ` · ${group.note}`) : null),
      ),
    );
    for (const item of shown) body.push(row(ctx, harness, projectTool, item, payload));
    if (rest > 0) {
      body.push(
        h(
          "tr",
          { class: "skill-more" },
          h(
            "td",
            { colspan: String(8 + tools.length) },
            h("button", { type: "button", class: "text-btn", onclick: () => ctx.toggleSkillGroup(group.id) }, `显示其余 ${rest} 条`),
          ),
        ),
      );
    }
  }
  if (!groups.length) {
    const cloudBlock = sourceBlocks(payload).find((block) => block.id === CLOUD_SOURCE);
    const emptyText =
      ctx.route.source === CLOUD_SOURCE && cloudBlock?.uncollected
        ? "Multica Workspace 安装数据未采集，无法筛选"
        : ctx.route.source === CLOUD_SOURCE
          ? "没有已安装到 Multica Workspace 的 skill"
          : "没有符合筛选的 skill";
    body.push(h("tr", null, h("td", { class: "empty", colspan: String(8 + tools.length) }, emptyText)));
  }
  return h("div", { class: "table-wrap sticky-head" }, h("table", { class: "skills-table" }, h("thead", null, head), h("tbody", null, body)));
}

function row(ctx, harness, projectTool, item, payload) {
  const { h } = ctx;
  const selected = ctx.drawerSkill === skillKey(item);
  const shared = sharedEntryOf(item, payload);
  const sharedTitle = [shared.path, shared.target ? `→ ${shared.target}` : null, shared.resolved ? `解析到 ${shared.resolved}` : null, shared.detail].filter(Boolean).join("\n");
  return h(
    "tr",
    {
      id: skillRowId(item),
      class: `skill-row${item.quiet ? " quiet" : ""}${selected ? " selected" : ""}`,
      tabindex: "0",
      onclick: (e) => ctx.openSkill(item, e.currentTarget),
      onkeydown: (e) => {
        if (e.key === "Enter") ctx.openSkill(item, e.currentTarget);
      },
    },
    h("td", null, h("span", { class: "mono" }, item.name), item.scope ? h("span", { class: "tag" }, item.scope) : null, cloudDrift(item) ? h("span", { class: "tag warn", title: cloudDrift(item).explanation }, cloudDrift(item).label) : null),
    h("td", { class: "channel-col", title: [item.channel_label, item.channel_detail, item.evidence_detail].filter(Boolean).join("\n") }, h("div", { class: "cell-ellipsis" }, item.channel_label), h("div", { class: "muted mono cell-ellipsis" }, item.channel_detail || "—")),
    h("td", { class: "cloud-cell" }, cloudCell(h, item)),
    h("td", { class: "shared-entry", title: sharedTitle }, h("span", { class: `tag ${SHARED_CLASS[shared.state] || "muted"}` }, shared.label)),
    ...harness.map((tool) => toolCell(h, item, tool)),
    projectTool ? h("td", { class: "repo-entries" }, isGrokBot(item) ? notApplicable(h, "Grok Bot 没有本机仓库入口") : repoCell(h, item, payload)) : null,
    h("td", { class: "mono version-col", title: isGrokBot(item) ? "∅ 不适用：Grok Bot 清单没有版本" : `记录：${item.current_version || "未记录"}\n实际：${item.observed_version || "未采集"}` }, h("div", { class: "cell-ellipsis" }, isGrokBot(item) ? notApplicable(h, "Grok Bot 清单没有版本") : versionCell(h, item))),
    h("td", null, h("span", { class: `tag ${STATUS_CLASS[item.reconciliation_status] || ""}`, title: item.reconciliation_status === "stale" ? (item.review_time ? "复核已过期，需要重新检查" : "从未复核：尚未建立人工复核记录") : "" }, item.reconciliation_status === "stale" && !item.review_time ? "从未复核" : item.status_label || item.reconciliation_status || "—")),
    h("td", null, isGrokBot(item) ? notApplicable(h, "Grok Bot 清单未提供定制或约束") : customCell(h, item)),
    h("td", { class: "muted", title: item.review_time || "" }, isGrokBot(item) ? notApplicable(h, "Grok Bot 名称声明不参与本机基线复核") : relTime(item.review_time, ctx.ago)),
  );
}

const CLOUD_PREVIEW = 3;

function normalizeText(value) {
  return String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
}

function grokPath(path) {
  if (typeof path !== "string" || !path) return false;
  const text = path.replace(/\\/g, "/").replace(/\/+$/, "");
  return text === ".grok/skills" || text === "~/.grok/skills" || text.endsWith("/.grok/skills") || text.includes("/.grok/skills/");
}

function grokSymlinkDirect(item) {
  const evidence = (item.evidence || []).some(
    (record) => record.harness === "grok" && record.kind === "entry" && (record.readlink || record.attribution === "direct") && (grokPath(record.root) || grokPath(record.path) || grokPath(record.lexical)),
  );
  const entry = (item.entries || []).some((row) => row.symlink && (row.tools || []).includes("grok") && (grokPath(row.location) || grokPath(row.path)));
  return evidence || entry;
}

function displayMark(item, tool) {
  if (tool.id === "grok" && grokSymlinkDirect(item)) return "direct";
  return item.tools?.[tool.id];
}

export function cloudDrift(item) {
  const local = item?.local_source;
  const installs = Array.isArray(item?.workspace_installs) ? item.workspace_installs : [];
  if (!local || !installs.length) return null;
  const localMs = Date.parse(local.updated_at || "");
  const localSummary = normalizeText(local.summary);
  const stale = [];
  const comparisons = [];
  for (const install of installs) {
    const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/i.test(value) ? value.toLowerCase() : null;
    const localHash = hash(local.content_hash), cloudHash = hash(install.content_hash);
    let basis = null;
    if (localHash && cloudHash) {
      if (localHash !== cloudHash) basis = "hash";
    } else {
      const cloudMs = Date.parse(install.updated_at || "");
      const timeKnown = !Number.isNaN(localMs) && !Number.isNaN(cloudMs);
      const timeStale = timeKnown && localMs > cloudMs + 1000;
      const summaryStale = !timeKnown && localSummary && normalizeText(install.description) && localSummary !== normalizeText(install.description);
      if (timeStale) basis = "time";
      else if (summaryStale) basis = "description";
    }
    if (basis) { stale.push(install); comparisons.push({ install, basis }); }
  }
  if (!stale.length) return null;
  const onlyTime = comparisons.every(row => row.basis === "time");
  return { local, installs: stale, comparisons,
    label: onlyTime ? "Multica Workspace 较旧（按时间推断）" : "Multica Workspace 内容不同",
    explanation: comparisons.map(({install, basis}) => `${workspaceLabel(install)}：${basis === "hash" ? "内容哈希不同，无法据此判断新旧" : basis === "time" ? "缺少内容哈希，按时间推断；文件修改时间可能受 git pull 影响" : "缺少内容哈希且时间未知，完整描述不同，正文是否相同待确认"}`).join("\n"),
  };
}

export function cloudPending(payload) {
  return (payload?.items || []).filter((item) => cloudDrift(item));
}

function workspaceLabel(install) {
  return install.workspace_name || install.workspace_slug || install.workspace_id || "Workspace";
}

function agentBrief(agents) {
  const list = Array.isArray(agents) ? agents : [];
  const enabled = list.filter((agent) => agent.enabled !== false);
  if (!enabled.length) return list.length ? "未启用" : "未挂载";
  const names = enabled.map((agent) => agent.name || agent.id).filter(Boolean);
  const shown = names.slice(0, 2).join("、");
  const rest = names.length - 2;
  return rest > 0 ? `${shown} +${rest}` : shown || "已启用";
}

function cloudCell(h, item) {
  if (isGrokBot(item)) return notApplicable(h, "Grok Bot 名称声明独立于 Multica Workspace 安装记录");
  const installs = item.workspace_installs;
  if (!Array.isArray(installs)) return h("span", { class: "muted", title: "Multica Workspace 数据未采集" }, "—");
  if (!installs.length) return h("span", { class: "muted" }, "Multica Workspace 未安装");
  const drift = cloudDrift(item);
  const stale = new Set((drift?.installs || []).map((install) => `${install.workspace_id}:${install.id}`));
  const shown = installs.slice(0, CLOUD_PREVIEW);
  const rest = installs.length - shown.length;
  const title = installs
    .map((install) => {
      const agents = (install.agents || []).map((agent) => `${agent.name || agent.id}${agent.enabled === false ? "（未启用）" : ""}`).join("、");
      return `${workspaceLabel(install)}${agents ? `：${agents}` : "：未挂载"}`;
    })
    .join("\n");
  return h(
    "div",
    { title },
    shown.map((install) =>
      h(
        "div",
        { class: stale.has(`${install.workspace_id}:${install.id}`) ? "cloud-ws stale" : "cloud-ws" },
        h("span", null, workspaceLabel(install)),
        h("span", { class: "muted" }, ` · ${agentBrief(install.agents)}`),
      ),
    ),
    rest > 0 ? h("div", { class: "muted" }, `另有 ${rest} 个 Workspace`) : null,
  );
}

function cloudQueue(ctx, payload) {
  const { h } = ctx;
  const pending = cloudPending(payload);
  const body = pending.length
    ? h(
        "ul",
        null,
        pending.map((item) =>
          h(
            "li",
            null,
            h(
              "button",
              { type: "button", class: "queue-item", onclick: (e) => ctx.focusSkill(item.name, e.currentTarget) },
              h("span", { class: "mono" }, item.name),
              h("span", { title: cloudDrift(item).explanation }, cloudDrift(item).label),
              h("span", { class: "muted" }, item.local_source?.updated_at ? relTime(item.local_source.updated_at, ctx.ago) : ""),
            ),
          ),
        ),
      )
    : h("p", { class: "muted" }, "无");
  return h("section", { class: "skill-bucket cloud-pending" }, h("header", null, h("span", null, "Multica Workspace 待核对"), h("span", { class: "count" }, String(pending.length))), body);
}

export function cloudCommands(item, install) {
  const ws = install.workspace_id;
  const id = install.id;
  const commands = [
    { label: "从导入源刷新", command: `multica --workspace-id ${ws} skill refresh ${id}` },
    { label: "更新这条 Multica Workspace Skill", command: `multica --workspace-id ${ws} skill update ${id}` },
  ];
  if (item.local_source?.path) {
    commands.push({
      label: "用本机 SKILL.md 更新",
      command: `multica --workspace-id ${ws} skill update ${id} --content-file ${item.local_source.path}`,
    });
  }
  for (const agent of install.agents || []) {
    if (!agent?.id) continue;
    const name = agent.name || agent.id;
    commands.push({
      label: agent.enabled === false ? `挂给 ${name}` : `已启用 ${name}`,
      command: `multica --workspace-id ${ws} agent skills add ${agent.id} --skill-ids ${id}`,
    });
  }
  return commands;
}

function cloudSection(h, item) {
  const installs = item.workspace_installs;
  if (!Array.isArray(installs)) return [h("p", { class: "muted" }, "这份清单还没有 Multica Workspace 安装数据。")];
  if (!installs.length) return [h("p", { class: "muted" }, "已采集的 Workspace 里没有同名 Multica Workspace Skill。")];
  const drift = cloudDrift(item);
  const nodes = [
    h("p", { class: "muted" }, "页面只读，不代为执行。复制命令后在自己的终端运行。"),
  ];
  if (item.local_source) {
    nodes.push(h("p", null, h("span", { class: "muted" }, "本机 "), h("span", { class: "mono" }, item.local_source.path), item.local_source.updated_at ? h("span", { class: "muted" }, ` · ${item.local_source.updated_at}`) : null));
    if (item.local_source.summary) nodes.push(h("p", null, h("span", { class: "muted" }, "本机摘要 "), item.local_source.summary));
  }
  if (drift) nodes.push(h("p", { class: "tag warn", title: drift.explanation }, `${drift.label} · ${drift.installs.length} 个 Workspace`), h("p", null, drift.explanation));
  for (const install of installs) {
    const agents = install.agents || [];
    const enabled = agents.filter((agent) => agent.enabled !== false);
    const disabled = agents.filter((agent) => agent.enabled === false);
    nodes.push(
      h(
        "div",
        { class: "cloud-install" },
        h("p", null, h("strong", null, workspaceLabel(install)), h("span", { class: "muted" }, install.updated_at ? ` · ${install.updated_at}` : "")),
        install.description ? h("p", null, install.description) : null,
        h("p", { class: "muted" }, enabled.length ? `已启用 ${enabled.map((agent) => agent.name || agent.id).join("、")}` : "没有已启用的 Agent"),
        disabled.length ? h("p", { class: "muted" }, `未启用 ${disabled.map((agent) => agent.name || agent.id).join("、")}`) : null,
        ...cloudCommands(item, install).map((entry) => h("p", { class: "command" }, h("span", { class: "muted" }, entry.label), h("code", null, entry.command), h("button", { type: "button", onclick: (e) => copyCommand(e.currentTarget, entry.command) }, "复制"))),
      ),
    );
  }
  return nodes;
}

const EVIDENCE_TEXT = { tool_roots: "技能根配置", runtimes: "运行时清单", skill_evidence: "技能路径证据", disk_installations: "本机安装目录", observed: "已观测", unknown: "无法确定", no_match: "未命中", not_applicable: "不适用", local_installation_unobserved: "尚未观测到本机安装", skill_roots_unconfigured: "未配置技能根", cloud_only: "仅存在于 Multica Workspace，本机运行时不适用", explicit_opt_out: "显式禁用", none: "无加载证据" };

export function loadStateText(evidence, mark) {
  if (evidence?.runtime_loaded === true) return "已加载（有证据）";
  if (evidence?.runtime_loaded === false) return "未加载（有证据）";
  if (!evidence?.load_checked) return "";
  if (mark === "direct" || mark === "shared") return "磁盘可见、未验证";
  if (mark === "unknown") return "无法确定，未验证 runtime 加载";
  return "未见";
}
const evidenceText = value => EVIDENCE_TEXT[value] || "证据待确认";
function findingText(finding) {
  const label = finding?.label;
  // The API supplies Chinese descriptions. Older/unknown codes must never leak
  // through visible text or tooltip attributes when that description is absent.
  if (typeof label === "string" && /[\u3400-\u9fff]/.test(label) && !/[a-z][a-z0-9]*(?:_[a-z0-9]+)+/i.test(label)) return label;
  return EVIDENCE_TEXT[finding?.code] || "对账发现待复核（原因说明待补充）";
}
function columnEvidence(tool) {
  const probe = tool.load_probe?.detail ? `加载探针 · ${evidenceText(tool.load_probe.credibility)} · ${tool.load_probe.detail}` : "";
  return [tool.roots_state === "unconfigured" ? "未配置技能根，待确认" : "", probe, ...(tool.sources || []).map(s => `${evidenceText(s.source)} · ${s.collected_at || "采集时间未知"} · ${evidenceText(s.evidence_state)}`)].filter(Boolean).join("\n");
}

function toolCell(h, item, tool) {
  const evidence = item.runtime_evidence?.[tool.id];
  if (isGrokBot(item)) return h("td", { class: "tool-mark not-applicable", title: `∅ 不适用：Grok Bot 只有技能名称声明，没有本机 Runtime 安装证据\n${RUNTIME_LEGEND}` }, "∅");
  if (evidence?.state === "not_applicable" || (item.origin === "cloud" && !item.entries?.length && !item.local_source)) return h("td", { class: "tool-mark not-applicable", title: `不适用：仅 Multica Workspace 条目，没有本机安装记录\n${RUNTIME_LEGEND}` }, "∅");
  if (tool.roots_state === "unconfigured" && !displayMark(item, tool)) return h("td", { class: "tool-mark unknown", title: `${RUNTIME_LEGEND}\n${columnEvidence(tool)}` }, "?");
  if (evidence?.state === "unknown" && !displayMark(item, tool)) return h("td", { class: "tool-mark unknown", title: [`${evidenceText(evidence.reason)}`, columnEvidence(tool), RUNTIME_LEGEND, loadStateText(evidence, "unknown"), evidence?.load_detail].filter(Boolean).join("\n") }, "?");
  const mark = displayMark(item, tool);
  const records = (item.evidence || []).filter((record) => record.harness === tool.id);
  const lines = records.map((record) =>
    record.kind === "entry"
      ? `入口原路径 ${record.path}${record.root ? ` (${record.root})` : ""}`
      : `链接目标 ${record.resolved || record.readlink || ""}${record.root ? ` (${record.root})` : ""}`,
  );
  const onlyTarget = records.length > 0 && records.every((record) => record.kind === "target");
  const loadText = loadStateText(evidence, mark);
  const pathNote = lines.length ? (evidence?.runtime_loaded == null ? "路径推导，未验证 runtime 实际加载" : "路径推导") : null;
  const title = [markTitle(mark), columnEvidence(tool), evidence ? `${evidenceText(evidence.source)} · ${evidence.collected_at || "采集时间未知"} · ${evidenceText(evidence.state)}` : null, ...lines, pathNote, loadText, evidence?.load_detail].filter(Boolean).join("\n");
  return h("td", { class: `tool-mark ${mark || "none"}${onlyTarget ? " via-target" : ""}`, title }, MARK[mark] || "", onlyTarget ? h("sup", null, "链") : null);
}

function shortLocation(location) {
  if (!location) return "";
  return location.endsWith("/skills") ? location.slice(0, -"/skills".length) : location;
}

function dedupeEntries(entries) {
  const seen = new Set();
  return (entries || []).filter((entry) => {
    const key = `${scopeOf(entry)}|${entry.repo_id || ""}|${entry.path || ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function repoCell(h, item, payload) {
  const reposById = new Map((payload?.repos || []).map((repo) => [repo.id, repo]));
  const groups = new Map();
  for (const entry of dedupeEntries(item.entries)) {
    if (!entry.repo_id) continue;
    if (!groups.has(entry.repo_id)) groups.set(entry.repo_id, []);
    groups.get(entry.repo_id).push(entry);
  }
  if (!groups.size) return "";
  return [...groups.entries()].map(([repoId, entries]) => {
    const repo = reposById.get(repoId);
    const label = repo?.label || repoId.split("/").filter(Boolean).pop() || repoId;
    return h(
      "div",
      { class: "repo-group" },
      h("div", { class: "repo-name muted" }, label),
      entries.map((entry) =>
        h(
          "span",
          { class: `entry-chip ${entry.mark === "missing" ? "missing" : entry.mark === "unknown" ? "unknown" : "present"}`, title: entry.path },
          `${shortLocation(entry.location) || (entry.scope === "repo_source" ? "源目录" : entry.location || "?")} ${entry.mark === "missing" ? "✕" : entry.mark === "unknown" ? "?" : "●"}`,
        ),
      ),
    );
  });
}

function markTitle(mark) {
  if (mark === "direct") return "有直接入口";
  if (mark === "shared") return "经共享目录可见";
  if (mark === "missing") return "入口或版本文件不在";
  if (mark === "unknown") return "路径无法确定（权限或读取失败）";
  return "已配置的根未命中，仍可能从其他目录加载";
}

function versionCell(h, item) {
  if (item.observed_version && item.observed_version !== item.current_version) {
    return h("span", null, item.current_version || "—", " → ", h("span", { class: "bad-n" }, item.observed_version));
  }
  if (item.current_version) return item.current_version;
  return h("span", { class: "muted" }, "—");
}

function customCell(h, item) {
  const mode = item.customization_mode && item.customization_mode !== "none" ? item.customization_mode : "";
  const count = item.constraint_count || 0;
  if (!mode && !count) return h("span", { class: "muted" }, "—");
  return h("span", null, mode || "有约束", count ? h("span", { class: "muted" }, ` · ${count}`) : null);
}

function queue(ctx, payload) {
  const { h } = ctx;
  const buckets = queueModel(payload);
  const debt = payload.attention?.baseline_debt || 0;
  return h(
    "aside",
    { class: "skills-queue", "aria-label": "待处理" },
    h(
      "div",
      { class: "skills-compact" },
      h("span", null, "Multica Workspace 待核对 ", String(cloudPending(payload).length)),
      buckets.map((bucket) => h("span", null, bucket.title, " ", bucket.connected ? String(bucket.items.length) : "—")),
    ),
    h("h2", null, "待处理"),
    cloudQueue(ctx, payload),
    ...buckets.map((bucket) => bucketBlock(ctx, bucket)),
    h(
      "button",
      {
        type: "button",
        class: `debt${ctx.route.debt ? " active" : ""}`,
        onclick: () => ctx.setRoute({ debt: !ctx.route.debt }),
      },
      h("span", null, "基线债 · 从未复核"),
      h("strong", null, debt),
    ),
    h(
      "label",
      { class: "filter" },
      "复核过期",
      h(
        "select",
        {
          value: String(ctx.reviewDays || 90),
          onchange: (e) => ctx.setReviewDays(Number(e.target.value)),
        },
        [30, 90, 180].map((days) => h("option", { value: String(days), selected: days === (ctx.reviewDays || 90) }, `${days} 天`)),
      ),
    ),
  );
}

function bucketBlock(ctx, bucket) {
  const { h } = ctx;
  let body;
  if (!bucket.connected) body = h("p", { class: "muted" }, `— ${bucket.reason}`);
  else if (!bucket.items.length) body = h("p", { class: "muted" }, "无");
  else {
    body = h(
      "ul",
      null,
      bucket.items.map((item) =>
        h(
          "li",
          null,
          h(
            "button",
            { type: "button", class: "queue-item", onclick: (e) => ctx.focusSkill(item.identifier, e.currentTarget) },
            h("span", { class: "mono" }, item.identifier),
            h("span", null, item.title),
            h("span", { class: "muted" }, relTime(item.time, ctx.ago)),
          ),
        ),
      ),
    );
  }
  return h("section", { class: "skill-bucket" }, h("header", null, h("span", null, bucket.title), h("span", { class: "count" }, bucket.connected ? String(bucket.items.length) : "—")), body);
}

function runtimeLoadSection(h, item) {
  const rows = Object.entries(item.runtime_evidence || {}).filter(([, row]) => row?.load_checked || row?.runtime_loaded != null);
  if (!rows.length) return [h("p", { class: "muted" }, "没有逐条加载探针。磁盘入口保持未验证。")];
  return rows.map(([id, row]) => h("p", null, `${id} · ${loadStateText(row, item.tools?.[id]) || "未验证"}`, row.load_detail ? h("span", { class: "muted" }, ` · ${row.load_detail}`) : null));
}

export function renderSkillDrawer(ctx, item) {
  const { h } = ctx;
  const close = h("button", { type: "button", class: "drawer-close", "aria-label": "关闭", onclick: ctx.closeDrawer }, "×");
  if (isGrokBot(item)) return [
    h("header", { class: "drawer-head" }, h("span", { class: "mono" }, item.name), close),
    h("h2", { class: "drawer-title" }, "Grok Bot"),
    section(h, "证据范围", [h("p", null, item.evidence_detail), line(h, "只读来源", item.manifest_path), h("p", { class: "muted" }, "仅展示 Git 清单中的名称声明；不调用 Grok 接口，不表示所有 Bot 均已实测。")]),
    section(h, "使用此技能的 Bot", (item.grok_bots || []).map(bot => h("p", { title: bot.evidence_label }, h("strong", null, bot.name), " · ", h("span", { class: bot.evidence_state === "observed" ? "info" : "muted" }, bot.evidence_label)))),
    section(h, "Skill 正文", [notApplicable(h, "Grok Bot 清单只有名称，没有正文")]),
    section(h, "版本", [notApplicable(h, "Grok Bot 清单没有版本")]),
    section(h, "Runtime", [notApplicable(h, "Grok Bot 名称声明不是本机 Runtime 安装证据")]),
    item.related_skill_name ? section(h, "同名关联", [h("p", null, `本机或 Multica Workspace 另有同名技能 ${item.related_skill_name}；保留独立条目。`), h("button", { type: "button", class: "text-btn", onclick: e => ctx.focusSkill(item.related_skill_name, e.currentTarget) }, "查看同名技能")]) : null,
  ].filter(Boolean);
  return [
    h("header", { class: "drawer-head" }, h("div", null, h("span", { class: "mono" }, item.name), item.scope ? h("span", { class: "tag" }, item.scope) : null), close),
    h("h2", { class: "drawer-title" }, item.channel_label || "Skill"),
    section(h, "来源与权责", [
      line(h, "来源", item.source_label),
      line(h, "归属", item.ownership),
      line(h, "更新权", item.update_authority),
      line(h, "权威", item.source_authority),
      line(h, "canonical", item.canonical_path || "—"),
      item.repo_is_source ? null : h("p", { class: "muted" }, "本仓库不是源头"),
      line(h, "scope", item.scope),
    ]),
    section(h, "基线与版本", [
      line(h, "基线", [item.baseline_kind, item.baseline_subject].filter(Boolean).join(" · ") || "—"),
      line(h, "baseline_ref", item.baseline_ref),
      line(h, "记录版本", item.current_version),
      line(h, "实际版本", item.observed_version),
      line(h, "复核", item.review_time ? ctx.absTime(item.review_time) : "从未"),
    ]),
    section(h, "共享入口", sharedSection(h, item, ctx)),
    section(h, "Runtime 加载", runtimeLoadSection(h, item)),
    h("p", { class: "muted" }, "以下为磁盘安装入口；未验证 runtime 当前会话实际加载。"),
    ...SCOPE_GROUPS.map(([scope, title]) =>
      section(h, title, entriesSection(h, item, dedupeEntries((item.entries || []).map((entry, index) => ({ ...entry, _index: index }))).filter((entry) => scopeOf(entry) === scope), ctx)),
    ),
    section(h, "对账发现", item.findings?.length ? item.findings.map((finding) => h("p", null, findingText(finding))) : [h("p", { class: "muted" }, "没有额外发现")]),
    section(h, "定制与兼容约束", customSection(h, item)),
    section(h, "Skill 正文", contentSection(h, ctx, item)),
    section(h, "Multica Workspace（跨 Workspace）", cloudSection(h, item)),
    section(h, "在终端处理", [
      h("p", { class: "muted" }, "页面只读，不替代命令行。"),
      item.command ? h("p", { class: "command" }, h("code", null, item.command), h("button", { type: "button", onclick: (e) => copyCommand(e.currentTarget, item.command) }, "复制")) : null,
    ]),
  ].filter(Boolean);
}

const SCOPE_GROUPS = [
  ["repo_entry", "仓库入口"],
  ["repo_source", "仓库源目录"],
  ["user", "用户级入口"],
  ["other", "其他位置"],
];

function scopeOf(entry) {
  if (entry.scope) return entry.scope;
  return entry.repo_id ? "repo_entry" : "user";
}

function sharedSection(h, item, ctx) {
  const shared = sharedEntryOf(item, ctx?.skills);
  return [
    h("p", null, h("span", { class: `tag ${SHARED_CLASS[shared.state] || "muted"}` }, shared.label), shared.detail ? h("span", { class: "muted" }, ` ${shared.detail}`) : null),
    line(h, "路径", shared.path),
    shared.target ? line(h, "链接目标", shared.target) : null,
    shared.resolved ? line(h, "解析到", shared.resolved) : null,
  ];
}

function entriesSection(h, item, entries, ctx) {
  if (!entries.length) return [h("p", { class: "muted" }, "没有记录入口")];
  const reposById = new Map((ctx?.skills?.repos || []).map((repo) => [repo.id, repo]));
  return entries.map((entry) => {
    const repo = entry.repo_id ? reposById.get(entry.repo_id) : null;
    const records = (item.evidence || []).filter((record) => record.entry_index === entry._index);
    const tools = records.length
      ? records.map((record) => `${record.harness}（${record.kind === "entry" ? "原路径" : "链接目标"}）`)
      : [...(entry.tools || []).map((tool) => `${tool}（原路径）`), ...(entry.target_tools || []).map((tool) => `${tool}（链接目标）`)];
    return h(
      "p",
      { class: "skill-entry" },
      entry.present ? "✓" : "✕",
      " ",
      h("span", { class: "mono" }, entry.path),
      repo ? h("span", { class: "muted" }, ` · ${repo.label || repo.id}`) : null,
      entry.location ? h("span", { class: "muted" }, ` · ${entry.location}`) : null,
      entry.category && entry.category !== "." ? h("span", { class: "muted" }, ` · 分类 ${entry.category}`) : null,
      entry.package ? h("span", { class: "muted" }, ` · 包 ${entry.package}（${entry.registration === "bundle_member" ? "来源由包清单及分发路径佐证；内容未校验" : "来源未核实"}）`) : null,
      entry.manifest_path ? h("span", { class: "mono" }, ` · 清单 ${entry.manifest_path}`) : null,
      entry.provider_path ? h("span", { class: "mono" }, ` · 分发源 ${entry.provider_path}`) : null,
      ...(entry.symlink_chain || []).map((link) => h("span", { class: "mono" }, ` · 上级链接 ${link.path} → ${link.target} (${link.resolved})`)),
      entry.symlink ? h("span", { class: "muted" }, ` → ${entry.target}`) : null,
      entry.resolved && !entry.symlink ? h("span", { class: "muted" }, ` → ${entry.resolved}`) : null,
      entry.symlink && entry.resolved ? h("span", { class: "muted" }, ` (${entry.resolved})`) : null,
      tools.length ? h("span", { class: "muted" }, ` · ${tools.join(" / ")}`) : null,
    );
  });
}

const SCOPE_TITLE = new Map(SCOPE_GROUPS);

function contentTargets(item) {
  const targets = [];
  const seen = new Set();
  if (item.local_source?.path) {
    seen.add(item.local_source.path);
    targets.push({ label: "本机 skills/ 源", path: item.local_source.path });
  }
  for (const entry of dedupeEntries(item.entries)) {
    if (!entry.present || !entry.path || seen.has(entry.path)) continue;
    seen.add(entry.path);
    const scope = SCOPE_TITLE.get(scopeOf(entry)) || "入口";
    targets.push({ label: entry.location ? `${scope} · ${entry.location}` : scope, path: entry.path });
  }
  return targets;
}

function contentPanel(h, ctx, item, target, result, requestedFile = "SKILL.md") {
  const meta = CONTENT_LABEL[result.state] || CONTENT_LABEL.error;
  if (result.state === "ok" || result.state === "empty") {
    const raw = result.content || "";
    const { fields, body, error } = splitFrontmatter(raw);
    const fieldNodes = Object.entries(fields).map(([key, value]) => line(h, key, typeof value === "string" ? value : JSON.stringify(value)));
    return h("div", null,
      h("p", { class: "muted" }, `${result.source || "local"} · ${target.path} · ${requestedFile} · ${result.bytes ?? 0} 字节 · ${result.collected_at || "采集时间未知"} · ${result.evidence_state || "证据状态未知"}${result.sha256 ? ` · ${result.sha256.slice(0, 12)}` : ""}`),
      result.state === "empty" ? h("p", { class: "muted" }, "文件为空") : null,
      ...fieldNodes,
      error ? h("p", { class: "warn" }, error) : null,
      result.format === "text" ? h("pre", { class: "instr-body skill-body" }, raw) : h("div", { class: "skill-markdown" }, ...markdownNodes(h, body)),
      h("details", { dataset: { skillDetail: `raw:${target.path}:${requestedFile}` } }, h("summary", null, "查看原文"), h("pre", { class: "instr-body skill-body" }, raw)),
    );
  }
  const retry = result.state === "loading" ? null : h("button", { type: "button", class: "text-btn", onclick: () => ctx.loadSkillContent(item.name, target.path, requestedFile) }, "重试");
  return h("p", null, h("span", { class: `tag ${meta.cls}` }, meta.label), result.message ? h("span", { class: "muted" }, ` ${result.message}`) : null, retry ? " " : null, retry);
}

function contentSection(h, ctx, item) {
  const nodes = [];
  const targets = contentTargets(item);
  if (!targets.length) nodes.push(h("p", { class: "muted" }, "没有可查看的本机入口"));
  for (const target of targets) {
    const result = ctx.skillContent?.get(contentKey(item.name, target.path));
    nodes.push(
      h(
        "div",
        { class: "skill-content" },
        h(
          "p",
          null,
          h("span", { class: "muted" }, `${target.label} `),
          h("span", { class: "mono" }, target.path),
          " ",
          h("button", { type: "button", class: "text-btn", onclick: () => ctx.loadSkillContent(item.name, target.path) }, "查看正文"),
        ),
        result ? contentPanel(h, ctx, item, target, result) : null,
        fileTree(h, ctx, item, target),
      ),
    );
  }
  const loaded = targets.map(target => ({ target, result: ctx.skillContent?.get(contentKey(item.name, target.path)) })).filter(row => ["ok", "empty"].includes(row.result?.state));
  if (targets.length > 1) {
    nodes.push(h("p", { class: "muted" }, "同名多来源只读对比：先加载两份正文；仅对比本机已读取内容。"));
    if (loaded.length > 1) nodes.push(h("div", { class: "skill-comparisons" }, ...loaded.slice(1).map(row => {
      const before = loaded[0];
      const diff = lineDiff(before.result.content || "", row.result.content || "");
      return h("details", { dataset: { skillDetail: `diff:${before.target.path}:${row.target.path}` } }, h("summary", null, `只读 diff：${before.target.path} → ${row.target.path}`),
        diff.error ? h("p", null, diff.error) : h("pre", { class: "diff-block" }, ...(diff.parts || []).map(part => h("span", { class: part.added ? "diff-add" : part.removed ? "diff-del" : "diff-same" }, `${part.added ? "+ " : part.removed ? "- " : "  "}${part.value}`))));
    })));
  }
  const installs = item.workspace_installs;
  if (!Array.isArray(installs)) nodes.push(h("p", { class: "muted" }, "这份清单还没有 Multica Workspace 安装数据。"));
  else for (const install of installs) nodes.push(h("p", { class: "muted" }, `${workspaceLabel(install)} · Multica Workspace 正文未采集（Multica Workspace 清单只含元数据，后端尚未采集正文）${install.id ? ` · ${install.id}` : ""}`));
  return nodes;
}

function fileTree(h, ctx, item, target) {
  const tree = ctx.skillContent?.get(contentKey(item.name, target.path, "@tree"));
  const request = file => ctx.loadSkillContent(item.name, target.path, file);
  return h("details", { class: "skill-files", dataset: { skillDetail: `tree:${target.path}` } }, h("summary", null, "目录文件"),
    h("p", { class: "muted" }, `来源：local · ${target.path}${tree?.collected_at ? ` · ${tree.collected_at}` : ""}`),
    h("button", { type: "button", onclick: () => request("@tree") }, tree ? "刷新文件树" : "读取文件树"),
    tree?.state === "ok" ? h("ul", { class: "skill-file-tree" }, ...(tree.files || []).map(file => {
      const content = ctx.skillContent?.get(contentKey(item.name, target.path, file.path));
      return h("li", null,
        file.type === "file" && file.readable ? h("button", { type: "button", class: "text-btn mono", onclick: () => request(file.path) }, file.path) : h("span", { class: "mono" }, file.path),
        h("span", { class: "muted" }, ` · ${file.type} · ${file.bytes ?? "未知"} 字节${file.type === "directory" ? "" : file.readable ? " · UTF-8 文本按需读取，二进制不预览" : " · 不预览"}${file.state ? ` · ${file.state}` : ""}`),
        content && file.path !== "SKILL.md" ? contentPanel(h, ctx, item, target, content, file.path) : null);
    })) : tree ? h("p", null, (CONTENT_LABEL[tree.state] || CONTENT_LABEL.error).label, tree.message || "") : null,
    tree?.truncated ? h("p", { class: "warn" }, "文件树已达上限，仅显示部分条目") : null);
}

function customSection(h, item) {
  const mode = item.customization_mode && item.customization_mode !== "none" ? item.customization_mode : "";
  if (!mode && !(item.constraints || []).length) return [h("p", { class: "muted" }, "—")];
  return [mode ? line(h, "模式", mode) : null, ...(item.constraints || []).map((constraint) => h("p", null, `${constraint.kind} · ${constraint.value}`))];
}

function section(h, title, nodes) {
  return h("section", null, h("h3", { class: "drawer-sub" }, title), ...nodes.filter(Boolean));
}

function line(h, label, value) {
  return h("p", null, h("span", { class: "muted" }, `${label} `), h("span", { class: "mono" }, value || "—"));
}

function copyCommand(button, command) {
  navigator.clipboard?.writeText(command).then(
    () => {
      button.textContent = "已复制";
    },
    () => {
      button.textContent = "复制失败";
    },
  );
}
