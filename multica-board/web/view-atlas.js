/** Map, layer list, and the layer block inside the issue drawer. Skills live in skills.js. */

import { formatWait, waitFor, waitLevel } from "./decide.js";
import {
  LAYERS,
  LAYER_BY_ID,
  MAP_TOOLS,
  aggregateLayers,
  availabilityFromReport,
  countRule,
  groupByStatus,
  issueLayer,
  layerAvailability,
  layerCommands,
  layerLabel,
  modelFromReport,
  previewDecide,
  railLinks,
  selectLayerIssues,
  sourceLabel,
} from "./layers.js";

function waitOf(ctx, issue) {
  return waitFor(issue, ctx.changes, ctx.nowMs, ctx.historyIncomplete);
}

function atlasCounts(ctx) {
  if (ctx.countsFromApi) {
    const availability = availabilityFromReport(ctx.report);
    const ready = availability === "ready";
    return {
      availability,
      model: ready ? modelFromReport(ctx.report, { governanceOnly: ctx.route.gov }) : null,
      full: ready ? modelFromReport(ctx.report) : null,
    };
  }
  const availability = layerAvailability(ctx.issues, ctx.acm?.id);
  const ready = availability === "ready" && ctx.acm;
  return {
    availability,
    model: ready ? aggregateLayers(ctx.issues, ctx.acm.id, { governanceOnly: ctx.route.gov }) : null,
    full: ready ? aggregateLayers(ctx.issues, ctx.acm.id) : null,
  };
}

function issueForItem(ctx, item) {
  return (
    ctx.issues.find((issue) => issue.id === item.id) || {
      id: item.id,
      identifier: item.identifier,
      title: item.title,
      status: item.status,
      url: item.url,
      workspace_id: ctx.acm?.id,
    }
  );
}

function waitFromItem(ctx, item) {
  const at = Date.parse(item.waiting_since || "");
  if (Number.isNaN(at)) return { ms: null, level: "none", approximate: true };
  const ms = Math.max(0, ctx.nowMs - at);
  return { ms, level: waitLevel(ms), approximate: item.waiting_exact === false };
}

function matches(ctx, issue) {
  if (!ctx.route.q) return true;
  return ctx.matchesQuery(ctx.route.q, issue.identifier, issue.title, ctx.assigneeName(issue.assignee), issue.parent_identifier, layerLabel(issueLayer(issue)?.primary));
}

export function layerBar(h, layer, { large = false } = {}) {
  const involved = new Set(layer?.involved || []);
  return h(
    "span",
    { class: `layer-bar${large ? " is-large" : ""}`, title: "实心是主层，空心是涉及层。从左到右是 L1 到 L5。" },
    ["l1", "l2", "l3", "l4", "l5"].map((id) => {
      const primary = layer?.primary === id;
      const hollow = !primary && involved.has(id);
      const color = LAYER_BY_ID[id].color;
      return h("i", {
        class: `cell${primary ? " is-primary" : hollow ? " is-involved" : ""}`,
        style: primary || hollow ? { "--cell": color } : null,
      });
    }),
  );
}

function guideCopy(availability, fromApi) {
  if (availability === "no-workspace") {
    return {
      title: "快照里没有 ACM Lab",
      body: "地图只统计 ACM Lab。这份快照里没有这个 workspace，所以不显示五层数字。",
    };
  }
  if (availability === "no-issues") {
    return {
      title: "ACM Lab 还没有可计数的 issue",
      body: "取消的 issue 不进地图。当前没有其他 issue。",
    };
  }
  if (availability === "unfilled") {
    return {
      title: "主层还没有回填",
      body: "主层、涉及层、治理线已经能读到，但这份 ACM Lab 的 issue 主层都是空的，全部算未归层。回填之前这里不把五层画成 0。",
    };
  }
  return {
    title: "层属性还没接到这份数据",
    body: fromApi
      ? "地图按主层计数。/api/layers 的 ready 还是 false（三个属性还没同时建好），所以这里不显示五层全 0。"
      : "地图按主层计数。当前 issue 上没有 layer 字段，所以这里不显示五层全 0。",
  };
}

function guide(ctx, availability) {
  const { h } = ctx;
  const copy = guideCopy(availability, ctx.countsFromApi);
  return h(
    "section",
    { class: "atlas-guide", "data-availability": availability },
    h("h2", null, copy.title),
    h("p", null, copy.body),
    h("p", { class: "muted" }, "真实采集在回填前会是「全部未归层」。有一份带回填的示例：地址加上 ?mock=atlas。"),
    h("p", null, h("a", { href: "#/issues" }, "先去 Issues 看关注项")),
  );
}

function pageHead(ctx, model) {
  const { h, acm } = ctx;
  const header = model?.header;
  return h(
    "div",
    { class: "atlas-head" },
    h("div", null, h("h2", null, "用好 agent · 五层地图"), h("p", { class: "atlas-sub" }, acm ? `${acm.name} · 只按主层计数。跨层 issue 只计一次。` : "只统计 ACM Lab。")),
    header
      ? h(
          "p",
          { class: "atlas-total", "data-map-total": header.total },
          `${acm?.name || "ACM Lab"} · ${header.total} 条 · 已归层 ${header.assigned} · 未完成 ${header.open} · 待批准 ${header.decide}`,
        )
      : null,
  );
}

function mixBar(h, box) {
  if (!box.total) return h("span", { class: "mix is-empty" });
  const part = (n, cls) => (n ? h("i", { class: cls, style: { width: `${(n / box.total) * 100}%` } }) : null);
  return h("span", { class: "mix", title: `已完成 ${box.done} · 未完成 ${box.open - box.decide} · 待批准 ${box.decide}` }, part(box.done, "done"), part(Math.max(0, box.open - box.decide), "open"), part(box.decide, "decide"));
}

function countTrio(h, box, { openOnly = false } = {}) {
  const decideCls = box.decide ? "is-decide" : "is-zero";
  return h(
    "div",
    { class: "trio" },
    openOnly ? null : h("span", null, h("b", null, box.total), h("small", null, "总数")),
    h("span", null, h("b", null, box.open), h("small", null, "未完成")),
    h("span", { class: decideCls }, h("b", null, box.decide), h("small", null, "待批准")),
    openOnly ? null : mixBar(h, box),
  );
}

function toolLine(ctx, tool) {
  const { h } = ctx;
  if (tool.kind === "gap") {
    return h(
      "div",
      { class: "tool gap" },
      h("i", { class: "dot" }),
      h("span", { class: "tool-name" }, tool.label, " ", h("span", { class: "gap-label" }, tool.gap)),
      tool.note ? h("span", { class: "muted" }, tool.note) : null,
    );
  }
  const alert = (tool.signal === "skills" && ctx.skillsAlert) || (tool.signal === "usage" && ctx.usageAlert) || (tool.signal === "mcp" && ctx.mcpAlert);
  const absent = tool.signal === "mcp" && ctx.mcpAbsent;
  return h(
    "a",
    { class: "tool", href: tool.href },
    h("i", { class: alert ? "dot bad" : absent ? "dot off" : "dot live" }),
    h("span", null, tool.label),
    h("span", { class: "muted" }, ctx.toolSignal(tool.signal)),
  );
}

function previewList(ctx, box, governanceOnly) {
  const { h } = ctx;
  const layerId = box.id;
  let rows;
  let total;
  if (box.decideItems) {
    rows = box.decideItems
      .filter((item) => !governanceOnly || item.governance)
      .filter((item) => !ctx.route.q || ctx.matchesQuery(ctx.route.q, item.identifier, item.title))
      .slice(0, 3)
      .map((item) => ({ issue: issueForItem(ctx, item), wait: waitFromItem(ctx, item) }));
    total = governanceOnly ? box.governanceDecide : box.decide;
  } else {
    rows = previewDecide(ctx.issues, ctx.acm.id, layerId, (issue) => waitOf(ctx, issue), { governanceOnly }).filter((row) => matches(ctx, row.issue));
    total = previewDecide(ctx.issues, ctx.acm.id, layerId, (issue) => waitOf(ctx, issue), { governanceOnly, limit: 100 }).length;
  }
  if (!rows.length) return h("p", { class: "muted preview-empty" }, "无");
  const items = rows.map((row) =>
    h(
      "button",
      { type: "button", class: "preview", onclick: (e) => ctx.openDrawer(row.issue, e.currentTarget) },
      h("span", { class: "ident" }, row.issue.identifier),
      h("span", { class: "preview-title", title: row.issue.title }, row.issue.title),
      h("span", { class: `wait ${row.wait.level}` }, formatWait(row.wait.ms, row.wait.approximate)),
    ),
  );
  if (total > 3) {
    items.push(h("a", { class: "more", href: `#/decide?layer=${layerId}` }, `查看全部 ${total} 条`));
  }
  return h("div", { class: "previews" }, items);
}

function extraLine(h, layer) {
  if (!layer.involvedExtra) return null;
  const where = layer.involvedPrimaries.length ? `（主层在 ${layer.involvedPrimaries.join(" · ")}）` : "";
  return h("p", { class: "extra" }, `另有 +${layer.involvedExtra} 条涉及本层${where}，不计入`);
}

export function renderMap(ctx) {
  const { h, route, acm } = ctx;
  const { availability, model, full } = atlasCounts(ctx);
  const head = pageHead(ctx, model);
  if (!model) return [head, guide(ctx, availability)];

  const toolbar = h(
    "div",
    { class: "toolbar" },
    h(
      "div",
      { class: "seg", role: "group", "aria-label": "地图范围" },
      h("button", { type: "button", class: route.gov ? "" : "active", onclick: () => ctx.setRoute({ gov: false }) }, "全部 issue"),
      h("button", { type: "button", class: route.gov ? "active" : "", onclick: () => ctx.setRoute({ gov: true }) }, "只看治理线"),
    ),
    h(
      "label",
      { class: "check" },
      h("input", { type: "checkbox", checked: route.openOnly, onchange: (e) => ctx.setRoute({ openOnly: e.target.checked }) }),
      "只看未完成",
    ),
    h("span", { class: "muted" }, ctx.countsFromApi ? "计数来自 /api/layers。已完成 / 未完成 / 待批准。" : "数字为示意口径：带 layer 的 issue 汇总。已完成 / 未完成 / 待批准。"),
  );

  const govHead = h(
    "a",
    { class: "gov-head", href: "#/map/all?gov=1", onclick: (e) => ctx.follow(e, { view: "map", layer: "all", gov: true, scope: "primary" }) },
    "治理线",
    h("small", null, "点击跨层"),
  );

  const rows = model.layers.map((box) => {
    const meta = LAYER_BY_ID[box.id];
    const column = full.layers.find((layer) => layer.id === box.id);
    return h(
      "div",
      {
        class: `map-row layer-${box.id}`,
        onclick: (e) => {
          if (e.target.closest("a, button")) return;
          ctx.setRoute({ layer: box.id, scope: "primary" });
        },
      },
      h(
        "div",
        { class: "map-layer" },
        h("span", { class: "layer-badge", style: { "--cell": meta.color } }, meta.short),
        h("div", null, h("strong", null, meta.name), h("p", { class: "muted" }, meta.objects), h("span", { class: "enter" }, "进入本层 →")),
      ),
      h("div", { class: "map-counts" }, countTrio(h, box, { openOnly: route.openOnly }), extraLine(h, box)),
      h("div", { class: "map-tools" }, MAP_TOOLS[box.id].map((tool) => toolLine(ctx, tool))),
      h("div", { class: "map-preview" }, previewList(ctx, box, route.gov)),
      h(
        "a",
        {
          class: "gov-cell",
          href: `#/map/${box.id}?gov=1`,
          onclick: (e) => ctx.follow(e, { layer: box.id, gov: true, scope: "primary" }),
        },
        h("b", null, column.governanceOpen),
        h("small", null, "未完成"),
        h("small", { class: column.governanceDecide ? "is-decide" : "is-zero" }, column.governanceDecide ? `待批准 ${column.governanceDecide}` : "待批准 0"),
      ),
    );
  });

  const foot = h(
    "div",
    { class: "map-foot" },
    h(
      "button",
      { type: "button", class: "foot-bucket", onclick: () => ctx.setRoute({ layer: "outside", scope: "primary" }) },
      h("span", null, "不在地图"),
      h("b", null, model.outside.total),
      h("small", null, `未完成 ${model.outside.open} · 待批准 ${model.outside.decide}`),
    ),
    h(
      "button",
      { type: "button", class: `foot-bucket${model.unassigned.total ? " is-alert" : ""}`, onclick: () => ctx.setRoute({ layer: "unassigned", scope: "primary" }) },
      h("span", null, "未归层"),
      h("b", null, model.unassigned.total),
      h("small", null, model.unassigned.total ? "主层为空。点进去逐条处理。" : "没有遗漏"),
    ),
  );

  const legend = h(
    "p",
    { class: "legend" },
    h("i", { class: "swatch done" }),
    " 已完成 ",
    h("i", { class: "swatch open" }),
    " 未完成 ",
    h("i", { class: "swatch decide" }),
    " 待批准 ",
    h("span", { class: "muted" }, " · 工具行：绿点是已有页面且无告警，红点是已有页面且有告警，灰点是未建或未接入，只出现在地图上。"),
  );

  return [
    head,
    toolbar,
    h("div", { class: "map-table" }, h("div", { class: "map-head" }, h("span", null, "层"), h("span", null, "ACM issue"), h("span", null, "本层工具与信号"), h("span", null, "待批准"), govHead), ...rows),
    foot,
    legend,
  ];
}

function layerTitle(route) {
  if (route.layer === "all") return route.gov ? "治理线" : "全部层";
  if (route.layer === "outside") return "不在地图";
  if (route.layer === "unassigned") return "未归层";
  const meta = LAYER_BY_ID[route.layer];
  return meta ? `${meta.short} ${meta.name}` : "层";
}

function issueTable(ctx, issues, { foreignLayer = "" } = {}) {
  const { h } = ctx;
  const groups = groupByStatus(issues.filter((issue) => matches(ctx, issue)));
  if (!issues.length) return h("p", { class: "empty" }, "这一层没有符合范围的 issue");
  if (!groups.length) {
    return h(
      "p",
      { class: "empty" },
      "本层没有匹配的 issue。",
      " ",
      h("button", { type: "button", onclick: () => ctx.setRoute({ layer: "all", gov: false }) }, "在全部层中搜索"),
    );
  }
  return h(
    "div",
    { class: "layer-table" },
    groups.map((group) =>
      h(
        "section",
        null,
        h("h3", null, `${group.title} · ${group.items.length}`),
        h(
          "table",
          null,
          h("thead", null, h("tr", null, ...["Issue", "标题", "层", "状态", "线", "负责人", "主层来源", "最后活动"].map((name) => h("th", null, name)))),
          h(
            "tbody",
            null,
            group.items.map((issue) => {
              const layer = issueLayer(issue);
              const foreign = foreignLayer && layer?.primary && layer.primary !== foreignLayer;
              return h(
                "tr",
                {
                  class: foreign ? "is-foreign" : "",
                  tabindex: "0",
                  onclick: (e) => {
                    if (e.target.closest("a, button")) return;
                    ctx.openDrawer(issue, e.currentTarget);
                  },
                },
                h("td", { class: "ident" }, issue.identifier),
                h(
                  "td",
                  null,
                  ctx.extLink(issue.url, { class: "issue-link" }, issue.title),
                  foreign ? h("span", { class: "foreign-tag" }, `主层 ${layerLabel(layer.primary)}`) : null,
                ),
                h("td", null, layerBar(h, layer)),
                h("td", null, ctx.statusLabel(issue.status)),
                h("td", null, layer?.governance ? h("span", { class: "tag warn" }, "治理") : "—"),
                h("td", null, ctx.assigneeName(issue.assignee)),
                h("td", null, sourceLabel(layer?.source?.primary)),
                h("td", { class: "muted" }, ctx.ago(issue.last_activity_at)),
              );
            }),
          ),
        ),
      ),
    ),
  );
}

export function renderLayer(ctx) {
  const { h, route, acm } = ctx;
  const { availability, full } = atlasCounts(ctx);
  if (!full) return [pageHead(ctx, null), guide(ctx, availability)];
  const meta = LAYER_BY_ID[route.layer];
  const box = meta ? full.layers.find((layer) => layer.id === route.layer) : null;
  const primaryRows = meta ? selectLayerIssues(ctx.issues, acm.id, { layer: route.layer, scope: "primary", segment: "all" }) : [];
  const rows = selectLayerIssues(ctx.issues, acm.id, {
    layer: route.layer,
    scope: route.scope,
    governanceOnly: route.gov,
    segment: route.seg,
  });
  const crumb = h(
    "p",
    { class: "crumb" },
    h("a", { href: "#/map", onclick: (e) => ctx.follow(e, { view: "map", layer: "", gov: false }) }, "地图"),
    " › ",
    layerTitle(route),
  );
  const stats = box
    ? h(
        "p",
        { class: "atlas-total" },
        `${box.total} 主层 · ${box.open} 未完成 · ${box.decide} 待批准 · ${box.governanceOpen} 治理线未完成 · +${box.involvedExtra} 另涉及本层`,
      )
    : h(
        "p",
        { class: "atlas-total" },
        route.layer === "outside" ? `不在地图 ${full.outside.total} 条` : route.layer === "unassigned" ? `未归层 ${full.unassigned.total} 条` : `全部 ${full.header.total} 条`,
      );

  const single = Boolean(meta);
  const toolbar = h(
    "div",
    { class: "toolbar" },
    single
      ? h(
          "div",
          { class: "seg", role: "group", "aria-label": "范围" },
          h("button", { type: "button", class: route.scope === "primary" ? "active" : "", onclick: () => ctx.setRoute({ scope: "primary" }) }, `主层 ${ctx.countsFromApi && box ? box.total : primaryRows.length}`),
          h("button", { type: "button", class: route.scope === "involved" ? "active" : "", onclick: () => ctx.setRoute({ scope: "involved" }) }, `涉及本层 ${ctx.countsFromApi && box ? box.total + box.involvedExtra : primaryRows.length + (box?.involvedExtra || 0)}`),
        )
      : null,
    h(
      "div",
      { class: "seg", role: "group", "aria-label": "分段" },
      h("button", { type: "button", class: route.seg === "decide" ? "active" : "", onclick: () => ctx.setRoute({ seg: "decide" }) }, "待批准"),
      h("button", { type: "button", class: route.seg === "open" ? "active" : "", onclick: () => ctx.setRoute({ seg: "open" }) }, "未完成"),
      h("button", { type: "button", class: route.seg === "all" ? "active" : "", onclick: () => ctx.setRoute({ seg: "all" }) }, "全部"),
    ),
    h(
      "label",
      { class: "check" },
      h("input", { type: "checkbox", checked: route.gov, onchange: (e) => ctx.setRoute({ gov: e.target.checked }) }),
      "只看治理线",
    ),
  );

  const grouped = route.layer === "all"
    ? ["l5", "l4", "l3", "l2", "l1", "outside", "unassigned"].map((id) => ({
        id,
        title: layerLabel(id),
        items: rows.filter((issue) => (issueLayer(issue)?.primary || "unassigned") === id),
      })).filter((group) => group.items.length)
    : null;

  const list = grouped
    ? h(
        "div",
        { class: "layer-groups" },
        grouped.map((group) => h("section", null, h("h3", null, `${group.title} · ${group.items.length}`), issueTable(ctx, group.items))),
      )
    : issueTable(ctx, rows, { foreignLayer: route.scope === "involved" ? route.layer : "" });

  const rail = single
    ? h(
        "aside",
        { class: "layer-rail" },
        h("h3", null, "本层工具"),
        railLinks(route.layer).map((link) =>
          h(
            "a",
            { href: link.href },
            link.label,
            link.signal ? h("small", null, ctx.toolSignal(link.signal)) : null,
          ),
        ),
        h("h3", null, `另涉及本层 · ${box?.involvedExtra || 0}`),
        box?.involvedPrimaries?.length
          ? h(
              "ul",
              null,
              box.involvedPrimaries.map((name) => h("li", null, name)),
            )
          : h("p", { class: "muted" }, "没有主层在别处、涉及本层的 issue。"),
        h("p", { class: "muted" }, "这里只列已经有页面的入口。"),
      )
    : null;

  return [
    h("div", { class: "atlas-head" }, h("div", null, crumb, h("h2", null, layerTitle(route)), meta ? h("p", { class: "atlas-sub" }, meta.objects) : null, stats)),
    toolbar,
    h("div", { class: "layer-layout" }, h("div", { class: "layer-main" }, list), rail),
  ];
}

export function drawerLayer(ctx, issue) {
  const { h, acm } = ctx;
  const layer = issueLayer(issue);
  const commands = layerCommands(issue);
  const inAcm = acm && issue.workspace_id === acm.id;
  if (!inAcm) return [];
  if (!layer) {
    return [h("h3", { class: "drawer-sub" }, "所在层"), h("p", { class: "muted" }, "这份数据里还没有 layer 字段。")];
  }
  return [
    h("h3", { class: "drawer-sub" }, "所在层"),
    layerBar(h, layer, { large: true }),
    h("p", { class: "layer-line" }, "主层 ", h("b", null, layerLabel(layer.primary)), " · ", sourceLabel(layer.source.primary)),
    h("p", { class: "layer-line" }, "涉及层 ", h("b", null, layer.involved.length ? layer.involved.map(layerLabel).join("、") : "无"), " · ", sourceLabel(layer.source.involved)),
    h("p", { class: "layer-line" }, "治理线 ", h("b", null, layer.governance ? "是" : "否"), " · ", sourceLabel(layer.source.governance)),
    h("p", { class: "muted" }, countRule(layer)),
  ];
}

export function drawerCommands(ctx, issue) {
  const { h } = ctx;
  if (!issueLayer(issue)) return null;
  return h(
    "div",
    { class: "layer-commands" },
    h("h3", { class: "drawer-sub" }, "改层"),
    h("p", { class: "muted" }, "页面只读。复制命令改属性，下一轮采集后生效。"),
    ...layerCommands(issue).map((command) =>
      h(
        "p",
        { class: "command" },
        h("code", null, command),
        h("button", {
          type: "button",
          onclick: (e) => {
            const button = e.currentTarget;
            navigator.clipboard?.writeText(command).then(
              () => {
                button.textContent = "已复制";
              },
              () => {
                button.textContent = "请手抄";
              },
            );
          },
        }, "复制"),
      ),
    ),
  );
}
