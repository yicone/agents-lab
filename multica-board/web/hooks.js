/** Capability-page hooks. Sources are a filter. Rows are never merged across sources. */

import { capabilitySwitch } from "./capability.js";

export const HOOK_SOURCES = [
  ["cursor", "Cursor"],
  ["codex_user", "Codex 用户 hooks"],
  ["grok_cli", "Grok CLI"],
  ["hermes_plugin", "Hermes 插件事件"],
];

const ALLOWED = new Set(HOOK_SOURCES.map(([id]) => id));

const STATE_LABEL = {
  declared: { present: "已声明" },
  reachable: { present: "可达", missing: "路径不存在", unknown: "未知" },
  observed: { unknown: "未知" },
};

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
      h("span", { class: "legend-group" }, h("strong", null, "三态 "), "Declared 表示这份清单里有声明。Reachable 只表示命令里的绝对路径文件在不在，不显示命令。Observed 没有稳定接口，显示未知。同名条目按来源分开，不合并。")),
    table(h, items, route, payload),
  ].filter(Boolean);
}

function table(h, items, route, payload) {
  const head = h("tr", null, ...["事件", "来源", "插件", "Declared", "Reachable", "Observed"].map((label) => h("th", null, label)));
  const body = items.map((item) => h("tr", { class: "skill-row", "data-source": item.source_id },
    h("td", null, h("span", { class: "mono" }, item.event || item.name || "—")),
    h("td", null, item.source_label || "—"),
    h("td", null, item.plugin || "—"),
    h("td", null, STATE_LABEL.declared[item.declared] || "未知"),
    h("td", null, STATE_LABEL.reachable[item.reachable] || "未知"),
    h("td", null, STATE_LABEL.observed[item.observed] || "未知")));
  if (!body.length) {
    const selected = (payload?.sources || []).find((item) => item.id === route?.source);
    const text = selected?.collection_state === "collected" && !selected.error ? "已读取，0 条" : route?.q ? "没有符合筛选的 hook" : "没有符合筛选的 hook";
    body.push(h("tr", null, h("td", { class: "empty", colspan: "6" }, text)));
  }
  return h("div", { class: "table-wrap sticky-head" }, h("table", { class: "skills-table" }, h("thead", null, head), h("tbody", null, body)));
}
