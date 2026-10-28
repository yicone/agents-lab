/** Read-only evidence inventory. Layout A list; no write actions and no skill body. */

import { COPY, pageHead, visibleLabel } from "./ia-view.js";

export const EVIDENCE_SCOPE = "范围：只读证据清单，不按 workspace 筛选";

export const COLLECTION_STATES = ["collected", "uncollected", "unavailable", "invalid", "not_applicable"];
export const EVIDENCE_LEVELS = ["observed", "declared", "derived", "historical", "unknown"];

const REASONS = {
  not_implemented: "尚未接入",
  source_not_configured: "来源未配置",
  not_logged_in: "未登录",
  permission_denied: "无权限",
  read_failed: "读取失败",
  schema_invalid: "内容不符合契约",
  source_unverified: "来源未证实",
  private_rpc_not_supported: "不读取私有接口",
};

const LEVELS = {
  observed: { label: "直接观察", cls: "ok" },
  declared: { label: "声明", cls: "info" },
  derived: { label: "推导", cls: "muted" },
  historical: { label: "历史", cls: "muted" },
  unknown: { label: "未知", cls: "warn" },
};

const CHIP_STATE = {
  collected: "ok",
  uncollected: "absent",
  unavailable: "warn",
  invalid: "bad",
  not_applicable: "fallback",
};

export function reasonText(reason) {
  if (!reason) return "";
  return REASONS[reason] || "无法确定";
}

export function collectionMark(state) {
  if (state === "uncollected") return { symbol: "—", caption: "未采集" };
  if (state === "unavailable") return { symbol: "?", caption: "无法确定" };
  if (state === "not_applicable") return { symbol: "∅", caption: "不适用" };
  if (state === "invalid") return { symbol: "?", caption: "来源无效" };
  if (state === "collected") return { symbol: null, caption: "已采集" };
  return { symbol: "?", caption: "无法确定" };
}

export function sourceChipState(source) {
  return CHIP_STATE[source?.collection_state] || "warn";
}

export function evidenceLevel(level) {
  return LEVELS[level] || { label: "未知", cls: "warn" };
}

/** Collected sources show a count. Every other state keeps its symbol and never shows 0. */
export function sourceCountLabel(source, items) {
  const mark = collectionMark(source?.collection_state);
  if (source?.collection_state !== "collected") {
    return { symbol: mark.symbol, caption: mark.caption, count: null };
  }
  const count = (items || []).filter((item) => item?.source_id === source.source_id && item?.object_type === "skill").length;
  return { symbol: String(count), caption: count === 0 ? "已采集，0 条" : "已采集", count };
}

export function visibleItems(payload, route) {
  const q = String(route?.q || "").trim();
  const source = route?.source || "";
  return (payload?.items || []).filter((item) => {
    if (item?.object_type === "memory_collection") return false;
    if (source && item?.source_id !== source) return false;
    if (!q) return true;
    const haystack = [item.display_name, item.object_id, item.related_name, item.source_kind, item.evidence?.source_label].join("\n");
    return haystack.toLowerCase().includes(q.toLowerCase());
  });
}

export function memorySlots(payload) {
  return (payload?.items || []).filter((item) => item?.object_type === "memory_collection");
}

function skillRecords(payload, sourceId) {
  return (payload?.items || []).filter((item) => item?.object_type === "skill" && (!sourceId || item.source_id === sourceId));
}

/** Empty table copy. A search miss is not the same as a collected source that really has zero records. */
export function emptyInventoryText(sourceId, payload, route) {
  const q = String(route?.q || "").trim();
  const source = (payload?.sources || []).find((item) => item.source_id === sourceId);
  const records = skillRecords(payload, sourceId);
  if (q && records.length > 0) return "没有符合筛选的证据";
  if (!q && source?.collection_state === "collected" && records.length === 0) return "已采集，0 条";
  if (!q && !source) {
    const collected = (payload?.sources || []).some((item) => item.collection_state === "collected");
    if (collected && records.length === 0) return "已采集，0 条";
    return "没有符合筛选的证据";
  }
  if (!source) return "没有符合筛选的证据";
  const mark = collectionMark(source.collection_state);
  if (mark.symbol) return `${mark.symbol} ${mark.caption}`;
  return "没有符合筛选的证据";
}

function capabilityHead(h) {
  return pageHead(h, [
    { href: "#/configure/skills", label: COPY.capability },
    { label: "skills" },
    { label: "证据清单" },
  ], COPY.capability);
}

export function renderEvidence(ctx) {
  const { h, route } = ctx;
  const payload = ctx.evidence;
  if (!payload) return [...capabilityHead(h), h("div", { class: "empty big" }, "正在读取证据清单…")];
  if (payload.ok === false && !(payload.sources || []).length) {
    return [...capabilityHead(h), h("section", { class: "atlas-guide" }, h("h2", null, "证据清单无效"), h("p", null, payload.error?.message || "清单读不出来，页面不会把没检查显示成 0。"))];
  }
  const items = visibleItems(payload, route);
  const slots = route?.source && route.source !== "memory" ? [] : memorySlots(payload);
  const invalid = (payload.sources || []).filter((source) => source.collection_state === "invalid");
  return [
    ...capabilityHead(h),
    h("p", { class: "muted" }, "只读证据清单。不写回、不同步、不删除，也不读取正文。"),
    h("div", { class: "toolbar" }, h("button", { type: "button", class: "text-btn", onclick: () => ctx.setRoute({ evidence: false, source: "" }) }, `返回${COPY.capability}`)),
    h("div", { class: "source-grid" }, (payload.sources || []).map((source) => sourceCard(ctx, source, payload.items))),
    invalid.length ? h("p", { class: "skills-anomaly" }, "有来源无效，页面不会把没检查显示成 0。") : null,
    legend(h),
    table(ctx, items, route, payload),
    slots.length ? memoryBlock(h, slots) : null,
  ];
}

function sourceCard(ctx, source, items) {
  const { h, route } = ctx;
  const label = sourceCountLabel(source, items);
  const active = (route.source || "") === source.source_id;
  const reason = reasonText(source.reason);
  return h(
    "button",
    {
      type: "button",
      class: `source-card${active ? " active" : ""}`,
      title: [visibleLabel(source.label), label.caption, reason].filter(Boolean).join(" · "),
      "aria-pressed": active ? "true" : "false",
      onclick: () => ctx.setRoute({ source: active ? "" : source.source_id }),
    },
    h("span", { class: "source-label" }, visibleLabel(source.label)),
    h("strong", null, label.symbol),
    h("span", { class: "source-recon" }, h("span", { class: "muted" }, reason ? `${label.caption} · ${reason}` : label.caption)),
  );
}

function legend(h) {
  return h(
    "div",
    { class: "skills-legend" },
    h("span", { class: "legend-group" }, h("strong", null, "采集 "), "— 未采集，不是 0，也不是无。? 无法确定。∅ 不适用。已采集才显示条数；扫过但没有对象时写「已采集，0 条」。"),
    h("span", { class: "legend-group" }, h("strong", null, "证据等级 "), "直接观察、声明、推导、历史、未知。颜色只表示证据强弱，不表示风险，也不表示当前已加载。"),
    h("span", { class: "legend-group" }, h("strong", null, "同名 "), "related_name 只把同名记录对照起来，不合并。Grok CLI 和 Grok Bot 是两个来源。"),
  );
}

function table(ctx, items, route, payload) {
  const { h } = ctx;
  const head = h("tr", null, ...["名称", "来源", "scope", "采集", "证据等级", "观察时间", "同名关联", "位置"].map((label) => h("th", null, label)));
  const body = items.map((item) => row(h, item));
  if (!body.length) {
    body.push(h("tr", null, h("td", { class: "empty", colspan: "8" }, emptyInventoryText(route?.source, payload, route))));
  }
  return h("div", { class: "table-wrap sticky-head" }, h("table", { class: "skills-table" }, h("thead", null, head), h("tbody", null, body)));
}

function row(h, item) {
  const mark = collectionMark(item.collection_state);
  const level = evidenceLevel(item.evidence_level);
  const collected = item.collection_state === "collected" ? "已采集" : `${mark.symbol} ${mark.caption}`;
  return h(
    "tr",
    { class: "skill-row" },
    h("td", null, h("span", { class: "mono" }, item.display_name || "—")),
    h("td", null, item.source_kind || "—"),
    h("td", null, item.scope || "—"),
    h("td", null, h("span", { class: mark.symbol === "∅" ? "not-applicable" : mark.symbol === "?" ? "unknown" : "" }, collected)),
    h("td", null, h("span", { class: `tag ${level.cls}` }, level.label)),
    h("td", { class: "muted" }, item.observed_at || "—"),
    h("td", null, item.related_name || "—"),
    h("td", { class: "mono", title: item.evidence?.source_label || "" }, item.evidence?.location || item.evidence?.source_label || "—"),
  );
}

function memoryBlock(h, slots) {
  return h(
    "section",
    { class: "atlas-guide" },
    h("h2", null, "Memory 元数据槽位"),
    h("p", null, "当前实现只列出未采集槽位，不能相加成数量，也不能当成已有记忆。"),
    h(
      "ul",
      null,
      slots.map((slot) => h("li", null, `${collectionMark(slot.collection_state).symbol} ${slot.display_name} · ${slot.scope} · ${reasonText(slot.reason)}`)),
    ),
  );
}
