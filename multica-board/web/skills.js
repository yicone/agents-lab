/** Skills governance page. Buckets come from the API; this file only displays them. */

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
  { id: "claude", label: "Claude" },
  { id: "hermes", label: "Hermes" },
  { id: "gemini", label: "Gemini" },
  { id: "project", label: "项目" },
];

const STATUS_CLASS = {
  aligned: "ok",
  stale: "muted",
  version_changed: "warn",
  modified: "bad",
  unverified: "info",
  missing: "bad",
};

const MARK = { direct: "●", shared: "○", missing: "✕" };
const GROUP_LIMIT = 8;

export function badgeOf(payload) {
  return payload?.attention?.badge || 0;
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
    ...(item.findings || []).map((finding) => finding.code),
    ...(item.entries || []).map((entry) => entry.path),
  ]
    .filter(Boolean)
    .join("\n")
    .toLowerCase();
}

export function filteredItems(payload, route) {
  const q = (route.q || "").trim().toLowerCase();
  return (payload?.items || []).filter((item) => {
    if (route.source && item.source_class !== route.source) return false;
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
    return tools
      .map((tool) => ({
        id: tool.id,
        label: tool.label,
        note: "同一 skill 可能重复出现",
        items: items.filter((item) => item.tools?.[tool.id]).sort(byAttention),
      }))
      .filter((entry) => entry.items.length);
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
  const items = filteredItems(payload, route);
  const groups = groupedItems(items, route.skillGroup || "source", tools);
  const visible = new Map(tools.map((tool) => [tool.id, (payload.items || []).filter((item) => item.tools?.[tool.id] === "direct" || item.tools?.[tool.id] === "shared").length]));
  return [
    h("div", { class: "source-grid" }, (payload.summary || []).map((block) => sourceCard(ctx, block))),
    h(
      "div",
      { class: "toolbar" },
      select(h, "来源", route.source, [["", "全部来源"], ...(payload.summary || []).filter((block) => block.id !== "all").map((block) => [block.id, block.label])], (value) => ctx.setRoute({ source: value })),
      select(h, "状态", route.skillStatus, [["", "全部状态"], ["aligned", "一致"], ["stale", "未复核"], ["version_changed", "版本已变"], ["modified", "已修改"], ["unverified", "未核实"], ["missing", "缺失"]], (value) => ctx.setRoute({ skillStatus: value })),
      select(h, "工具", route.tool, [["", "全部工具"], ...tools.map((tool) => [tool.id, tool.label])], (value) => ctx.setRoute({ tool: value })),
      select(h, "分组", route.skillGroup || "source", [["source", "按来源"], ["tool", "按工具"], ["flat", "不分组"]], (value) => ctx.setRoute({ skillGroup: value })),
      h(
        "label",
        { class: "check" },
        h("input", { type: "checkbox", checked: route.custom, onchange: (e) => ctx.setRoute({ custom: e.target.checked }) }),
        "只看定制 / 有约束",
      ),
    ),
    h(
      "div",
      { class: "skills-layout" },
      h("div", { class: "skills-main" }, table(ctx, tools, groups, visible)),
      queue(ctx, payload),
    ),
  ];
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
      "aria-pressed": active ? "true" : "false",
      onclick: () => ctx.setRoute({ source: active ? "" : id }),
    },
    h("span", { class: "source-label" }, block.label),
    h("strong", null, block.count),
    h(
      "span",
      { class: "source-recon" },
      bits.length
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

function table(ctx, tools, groups, visible) {
  const { h } = ctx;
  const head = h(
    "tr",
    null,
    h("th", null, "Skill"),
    h("th", null, "来源 / 更新通道"),
    ...tools.map((tool) => h("th", { class: "tool-col", title: `${tool.label} 可见 ${visible.get(tool.id) || 0}` }, tool.label, h("small", null, String(visible.get(tool.id) || 0)))),
    h("th", null, "版本（记录 → 实际）"),
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
        h("th", { colspan: String(7 + tools.length) }, group.label, h("span", { class: "muted" }, ` · ${group.items.length}`), group.note ? h("span", { class: "muted" }, ` · ${group.note}`) : null),
      ),
    );
    for (const item of shown) body.push(row(ctx, tools, item));
    if (rest > 0) {
      body.push(
        h(
          "tr",
          { class: "skill-more" },
          h(
            "td",
            { colspan: String(7 + tools.length) },
            h("button", { type: "button", class: "text-btn", onclick: () => ctx.toggleSkillGroup(group.id) }, `显示其余 ${rest} 条`),
          ),
        ),
      );
    }
  }
  if (!groups.length) body.push(h("tr", null, h("td", { class: "empty", colspan: String(7 + tools.length) }, "没有符合筛选的 skill")));
  return h("div", { class: "table-wrap" }, h("table", { class: "skills-table" }, h("thead", null, head), h("tbody", null, body)));
}

function row(ctx, tools, item) {
  const { h } = ctx;
  const selected = ctx.drawerSkill === item.name;
  return h(
    "tr",
    {
      id: `skill-${item.name}`,
      class: `skill-row${item.quiet ? " quiet" : ""}${selected ? " selected" : ""}`,
      tabindex: "0",
      onclick: (e) => ctx.openSkill(item, e.currentTarget),
      onkeydown: (e) => {
        if (e.key === "Enter") ctx.openSkill(item, e.currentTarget);
      },
    },
    h("td", null, h("span", { class: "mono" }, item.name), item.scope ? h("span", { class: "tag" }, item.scope) : null),
    h("td", null, h("div", null, item.channel_label), h("div", { class: "muted mono" }, item.channel_detail || "—")),
    ...tools.map((tool) => h("td", { class: `tool-mark ${item.tools?.[tool.id] || "none"}`, title: markTitle(item.tools?.[tool.id]) }, MARK[item.tools?.[tool.id]] || "")),
    h("td", { class: "mono" }, versionCell(h, item)),
    h("td", null, h("span", { class: `tag ${STATUS_CLASS[item.reconciliation_status] || ""}` }, item.status_label || item.reconciliation_status || "—")),
    h("td", null, customCell(h, item)),
    h("td", { class: "muted", title: item.review_time || "" }, relTime(item.review_time, ctx.ago)),
  );
}

function markTitle(mark) {
  if (mark === "direct") return "有直接入口";
  if (mark === "shared") return "经共享目录可见";
  if (mark === "missing") return "入口或版本文件不在";
  return "不可见";
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
    h("div", { class: "skills-compact" }, buckets.map((bucket) => h("span", null, bucket.title, " ", bucket.connected ? String(bucket.items.length) : "—"))),
    h("h2", null, "待处理"),
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

export function renderSkillDrawer(ctx, item) {
  const { h } = ctx;
  const close = h("button", { type: "button", class: "drawer-close", "aria-label": "关闭", onclick: ctx.closeDrawer }, "×");
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
    section(h, "入口", item.entries?.length ? item.entries.map((entry) => h("p", { class: "skill-entry" }, entry.present ? "✓" : "✕", " ", h("span", { class: "mono" }, entry.path), entry.tools?.length ? h("span", { class: "muted" }, ` · ${entry.tools.join(" / ")}`) : null, entry.symlink ? h("span", { class: "muted" }, ` → ${entry.target}`) : null)) : [h("p", { class: "muted" }, "没有记录入口")]),
    section(h, "对账发现", item.findings?.length ? item.findings.map((finding) => h("p", null, h("code", { title: finding.label }, finding.code), " ", h("span", { class: "muted" }, finding.label))) : [h("p", { class: "muted" }, "没有额外发现")]),
    section(h, "定制与兼容约束", customSection(h, item)),
    section(h, "在终端处理", [
      h("p", { class: "muted" }, "页面只读，不替代命令行。"),
      item.command ? h("p", { class: "command" }, h("code", null, item.command), h("button", { type: "button", onclick: (e) => copyCommand(e.currentTarget, item.command) }, "复制")) : null,
    ]),
  ];
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
