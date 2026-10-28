/** Capability-page hooks. Sources are a filter. Rows are never merged across sources. */

import { capabilitySwitch } from "./capability.js";
import { TRI_LEGEND, renderTriState } from "./capability-state.js";

export const HOOK_SOURCES = [
  ["cursor", "Cursor"],
  ["codex_user", "Codex 用户 hooks"],
  ["grok_cli", "Grok CLI"],
  ["hermes_plugin", "Hermes 插件事件"],
];

const ALLOWED = new Set(HOOK_SOURCES.map(([id]) => id));

const TRI = { present: "yes", missing: "no", yes: "yes", no: "no", unknown: "unknown" };

function rowTriState(item) {
  return {
    declared: TRI[item?.declared] || "unknown",
    reachable: TRI[item?.reachable] || "unknown",
    observed: "unknown",
    detail: item?.detail || "hooks 没有稳定运行日志，观察到为未知。命令不展示。",
  };
}

export function sourceOptions() {
  return HOOK_SOURCES.map(([id, label]) => ({ id, label }));
}

export function filteredItems(payload, route) {
  const source = ALLOWED.has(route?.source) ? route.source : "";
  const query = String(route?.q || "").trim().toLowerCase();
  return (payload?.items || []).filter((item) => {
    if (!ALLOWED.has(item?.source_id)) return false;
    if (source && item.source_id !== source) return false;
    if (!query) return true;
    return [item.name, item.event, item.plugin, item.source_label].join("\n").toLowerCase().includes(query);
  });
}

export function visibleSourceErrors(payload, route) {
  const source = ALLOWED.has(route?.source) ? route.source : "";
  return (payload?.sources || []).filter((item) => {
    if (!ALLOWED.has(item?.id) || !item.error) return false;
    return !source || item.id === source;
  });
}

export function renderHooks(ctx) {
  const { h, route } = ctx;
  const payload = ctx.hooks;
  const kinds = capabilitySwitch(h, "hooks", (view) => ctx.setRoute({ view, source: "", section: "configure" }));
  if (!payload) return [kinds, h("div", { class: "empty big" }, "正在读取 hooks…")];
  if (payload.ok === false && !(payload.sources || []).length) {
    return [kinds, h("section", { class: "atlas-guide" }, h("h2", null, "hooks 没读到"), h("p", null, payload.error?.message || "清单读不出来。"))];
  }
  const items = filteredItems(payload, route);
  const errors = visibleSourceErrors(payload, route);
  const shell = !route?.source ? payload.hermes_shell : null;
  const shellNote = shell && shell.collection_state !== "collected"
    ? "Hermes shell 没读到。空列表才是正常结果；这不影响当前选中的其他来源。"
    : null;
  return [
    kinds,
    h("div", { class: "toolbar" },
      h("span", { class: "muted" }, "来源"),
      h("div", { class: "seg agents-kind", role: "radiogroup", "aria-label": "hooks 来源" },
        [["", "全部"], ...HOOK_SOURCES].map(([id, label]) => h("button", {
          type: "button",
          role: "radio",
          class: (route.source || "") === id ? "active" : "",
          "aria-checked": (route.source || "") === id ? "true" : "false",
          onclick: () => ctx.setRoute({ source: id }),
        }, label)))),
    errors.length ? h("div", { class: "skills-anomaly", role: "status" }, errors.map((item) => h("div", null, `${item.label}：${item.error.message}`))) : null,
    shellNote ? h("p", { class: "muted" }, shellNote) : null,
    h("div", { class: "skills-legend" },
      h("span", { class: "legend-group" }, h("strong", null, "能力三态 "), TRI_LEGEND),
      h("span", { class: "legend-group" }, "同名条目按来源分开，不合并。Hermes shell 空列表不是异常，也不进来源筛选。")),
    table(h, items, route, payload),
  ].filter(Boolean);
}

function table(h, items, route, payload) {
  const head = h("tr", null,
    ...["事件", "来源", "插件"].map((label) => h("th", null, label)),
    h("th", { class: "tri-col", title: TRI_LEGEND }, "三态"));
  const body = items.map((item) => h("tr", { class: "skill-row", "data-source": item.source_id },
    h("td", null, h("span", { class: "mono" }, item.event || item.name || "—")),
    h("td", null, item.source_label || "—"),
    h("td", null, item.plugin || "—"),
    h("td", { class: "tri-col" }, renderTriState(h, rowTriState(item)))));
  if (!body.length) {
    const selected = (payload?.sources || []).find((item) => item.id === route?.source);
    const text = selected?.collection_state === "collected" && !selected.error ? "已读取，0 条" : "没有符合筛选的 hook";
    body.push(h("tr", null, h("td", { class: "empty", colspan: "4" }, text)));
  }
  return h("div", { class: "table-wrap sticky-head" }, h("table", { class: "skills-table" }, h("thead", null, head), h("tbody", null, body)));
}
