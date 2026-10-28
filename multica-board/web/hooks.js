/** Read-only hooks list. Observed is always 未知. Missing data is not an alert. */

import { TRI_LEGEND, hookTriState, renderTriState } from "./capability-state.js";

export const HOOK_SOURCES = ["cursor", "codex", "grok_cli", "hermes"];

const SOURCE_LABEL = {
  cursor: "Cursor",
  codex: "Codex",
  grok_cli: "Grok CLI",
  hermes: "Hermes",
};

export function filteredHooks(payload, route) {
  const source = route?.source || "";
  const items = payload?.items || [];
  if (!source) return items;
  return items.filter((item) => item.source === source);
}

export function renderHooks(ctx) {
  const { h, route } = ctx;
  const payload = ctx.hooks;
  if (!payload) return [h("div", { class: "empty big" }, "正在读取 Hooks 清单…")];
  if (payload.ok === false) {
    return [h("section", { class: "atlas-guide" },
      h("h2", null, "Hooks 清单无效"),
      h("p", null, payload.error?.message || "清单读不出来。缺数据会显示未知，这里是清单本身无效。"))];
  }
  const items = filteredHooks(payload, route);
  const sources = payload.sources || [];
  return [
    h("div", { class: "source-grid" }, sourceCards(ctx, sources)),
    h("div", { class: "toolbar" },
      select(h, "来源", route.source || "", [["", "全部来源"], ...HOOK_SOURCES.map((id) => [id, SOURCE_LABEL[id]])], (value) => ctx.setRoute({ source: value }))),
    h("div", { class: "skills-legend" }, h("span", { class: "legend-group" }, h("strong", null, "能力三态 "), TRI_LEGEND)),
    h("p", { class: "muted tri-note" }, (payload.notes || []).join(" ")),
    table(h, items, sources),
  ];
}

function sourceCards(ctx, sources) {
  const { h, route } = ctx;
  const known = sources.every((source) => source.count != null);
  const cards = [{ id: "", label: "全部", state: known ? "ok" : "unknown", count: known ? sources.reduce((sum, source) => sum + source.count, 0) : null, detail: "四个来源并列，不跨来源合并。有来源没读到时，总数为未知。" }];
  for (const id of HOOK_SOURCES) {
    cards.push(sources.find((source) => source.id === id) || { id, label: SOURCE_LABEL[id], state: "unknown", count: null, detail: "没读到，显示未知。" });
  }
  return cards.map((card) => {
    const active = (route.source || "") === (card.id || "");
    const unknown = card.state === "unknown" || card.count == null;
    return h("button", {
      type: "button",
      class: `source-card${active ? " active" : ""}`,
      title: card.detail || card.label,
      "aria-pressed": active ? "true" : "false",
      onclick: () => ctx.setRoute({ source: active ? "" : card.id }),
    },
      h("span", { class: "source-label" }, card.label || SOURCE_LABEL[card.id] || card.id),
      h("strong", null, unknown ? "未知" : String(card.count ?? 0)),
      h("span", { class: "source-recon" }, h("span", { class: "muted" }, card.state === "empty" ? "空列表，不是异常" : (card.detail || ""))));
  });
}

function select(h, label, value, options, onchange) {
  return h("label", { class: "filter" }, label,
    h("select", { value, onchange: (event) => onchange(event.target.value) },
      options.map(([id, text]) => h("option", { value: id, selected: id === value }, text))));
}

function table(h, items, sources) {
  const head = h("tr", null,
    h("th", null, "事件"),
    h("th", null, "来源"),
    h("th", { class: "tri-col", title: TRI_LEGEND }, "三态"));
  const body = items.map((item) => h("tr", { class: "skill-row", id: `hook-${item.id || item.event}` },
    h("td", null, h("span", { class: "mono" }, item.event || "未知")),
    h("td", null, item.source_label || SOURCE_LABEL[item.source] || "未知"),
    h("td", { class: "tri-col" }, renderTriState(h, hookTriState(item)))));
  if (!body.length) {
    const unread = sources.filter((source) => source.state === "unknown").map((source) => source.label);
    const text = unread.length ? `${unread.join("、")} 没读到，显示未知，不是异常。` : "这个来源没有 hooks。空列表不是异常。";
    body.push(h("tr", null, h("td", { class: "empty", colspan: "3" }, text)));
  }
  return h("div", { class: "table-wrap sticky-head" }, h("table", { class: "skills-table" }, h("thead", null, head), h("tbody", null, body)));
}
