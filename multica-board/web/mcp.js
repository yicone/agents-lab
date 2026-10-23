/** MCP page (ACM-370): workspace MCP libraries, agent assignments, effective config. Read-only. */

export const BUCKETS = [
  { id: "ignored", title: "会被忽略" },
  { id: "shadowed", title: "同名覆盖" },
  { id: "may_fail", title: "可能失败" },
  { id: "hidden", title: "配置看不到" },
  { id: "unused", title: "无人使用" },
  { id: "disabled_only", title: "只有停用" },
];
export const BUCKET_IDS = new Set(BUCKETS.map((b) => b.id));
const MATRIX_COLUMN_LIMIT = 8;
export const FILE_HINT = "文件里有密钥：建议 chmod 600，用完删除，不要放进 git 仓库。";
export const READONLY_LINE = "页面只读，不代为执行。写命令需要 workspace admin/owner，或者能查看该 agent 密钥的身份；agent 会话里运行会被拒绝。";
const CONFIG_WRITE_ONLY = "配置只写不读：Multica 不向任何人返回库条目的配置，无法判断 env / headers";

export function badgeOf(payload) {
  return payload?.attention?.badge || 0;
}

export function attentionBuckets(payload) {
  const attention = payload?.attention || {};
  return BUCKETS.map((bucket) => ({
    ...bucket,
    count: attention.counts?.[bucket.id] ?? 0,
    items: attention.buckets?.[bucket.id] || [],
  }));
}

function bindingFor(agent, serverId) {
  return (agent?.bindings || []).find((b) => b.server_id === serverId) || null;
}

function unread(agent, workspace) {
  return agent?.bindings_state === "error" || agent?.bindings_state === "never" ||
    workspace?.bindings_state === "error" || workspace?.bindings_state === "never";
}

const CELL_TEXT = {
  "●": "已分配、已启用，runtime 会加载",
  "○": "已分配、已停用",
  "·": "未分配",
  "⊘": "已分配、已启用，但 runtime 不加载或未绑定",
  "◐": "已分配、已启用，但被 agent 自有配置里的同名 server 覆盖",
  "!": "已分配、已启用，runtime 有条件加载",
  "?": "这个 agent 的分配没读到或 runtime 是否加载未知",
};

export function cellMeaning(symbol) {
  return CELL_TEXT[symbol] || "";
}

export function cellSymbol(agent, serverEntry, workspace) {
  if (unread(agent, workspace)) return "?";
  const binding = bindingFor(agent, serverEntry?.id);
  if (!binding) return "·";
  if (!binding.enabled) return "○";
  if (binding.shadowed) return "◐";
  const loads = agent?.runtime_loads;
  if (loads === "no" || loads === "unbound") return "⊘";
  if (loads === "conditional") return "!";
  if (loads === "unknown") return "?";
  return "●";
}

export function loadMark(runtimeLoads) {
  switch (runtimeLoads) {
    case "yes": return "✓";
    case "no": return "✕";
    case "conditional": return "!";
    case "unbound": return "未绑定";
    default: return "?";
  }
}

export function providerLabel(payload, provider) {
  if (!provider) return "";
  return payload?.support?.providers?.[provider] ? provider : `${provider}（未知）`;
}

export function ownConfigLabel(own) {
  switch (own?.state) {
    case "managed": return (own.servers || []).length ? `${own.servers.length} 个` : "托管·空";
    case "hidden": return "看不到";
    case "invalid": return "无法解析";
    default: return "—";
  }
}

function wsBySlug(payload, slug) {
  return (payload?.workspaces || []).filter((w) => !slug || w.slug === slug);
}

const PROBLEM_BUCKETS = ["ignored", "shadowed", "may_fail"];

function attentionKeys(payload, issue) {
  const buckets = issue === "problems" ? PROBLEM_BUCKETS : [issue];
  const items = buckets.flatMap((id) => payload?.attention?.buckets?.[id] || []);
  const keys = new Set();
  for (const item of items) {
    if (item.agent_id) keys.add(`a:${item.agent_id}`);
    if (item.server_id) keys.add(`s:${item.server_id}`);
  }
  return keys;
}

function qHit(q, ...fields) {
  if (!q) return true;
  const needle = String(q).toLowerCase();
  return fields.some((f) => f && String(f).toLowerCase().includes(needle));
}

/** Per-workspace matrix blocks. `route`: {ws, q, issue, archived} */
export function matrixModel(payload, agentsPayload, route = {}) {
  const keys = route.issue ? attentionKeys(payload, route.issue) : null;
  const blocks = [];
  for (const ws of wsBySlug(payload, route.ws)) {
    const agents = (payload.agents || []).filter((a) => a.workspace_id === ws.id);
    const wsAgents = (agentsPayload?.items || []).filter((a) => a.workspace_id === ws.id);
    const rows = agents.filter((a) => {
      if (!route.archived && a.archived) return false;
      if (keys && !keys.has(`a:${a.id}`)) return false;
      if (!qHit(route.q, a.name, ws.name, ...(a.bindings || []).map((b) => b.name),
        ...(a.own_config?.servers || []).map((s) => s.name))) return false;
      return true;
    });
    let columns = ws.servers || [];
    if (keys) columns = columns.filter((s) => keys.has(`s:${s.id}`));
    if (route.q) columns = columns.filter((s) => qHit(route.q, s.name, ws.name) ||
      rows.some((a) => bindingFor(a, s.id)));
    const restCount = wsAgents.filter((a) => !a.archived && !agents.some((m) => m.id === a.id)).length;
    const fallbackToList = columns.length > MATRIX_COLUMN_LIMIT;
    blocks.push({
      workspace: ws,
      rows,
      restCount,
      columns,
      fallbackToList,
      reason: fallbackToList ? `库条目 ${columns.length} 个，超过 ${MATRIX_COLUMN_LIMIT} 列，自动退回清单视图` : "",
    });
  }
  return { blocks };
}

/** Flat 清单 rows: one per agent × server (library bindings + own-config entries). */
export function listRows(payload, route = {}) {
  const keys = route.issue ? attentionKeys(payload, route.issue) : null;
  const wsIndex = new Map((payload?.workspaces || []).map((w) => [w.id, w]));
  const rows = [];
  for (const agent of payload?.agents || []) {
    const ws = wsIndex.get(agent.workspace_id);
    if (!ws) continue;
    if (route.ws && ws.slug !== route.ws) continue;
    if (!route.archived && agent.archived) continue;
    if (keys && !keys.has(`a:${agent.id}`)) continue;
    for (const b of agent.bindings || []) {
      if (keys && !keys.has(`s:${b.server_id}`)) continue;
      if (!qHit(route.q, agent.name, b.name, ws.name)) continue;
      rows.push({
        key: `${agent.key}:${b.server_id}`, workspace: ws, agent,
        server: b.name || b.server_id, source: "库", enabled: b.enabled,
        transport: (ws.servers || []).find((s) => s.id === b.server_id)?.transport || "unknown",
        runtime_loads: agent.runtime_loads,
        note: b.shadowed ? "被自有配置同名覆盖" : unread(agent, ws) ? "分配没读到" : "",
        serverId: b.server_id,
      });
    }
    for (const s of agent.own_config?.servers || []) {
      if (!qHit(route.q, agent.name, s.name, ws.name)) continue;
      rows.push({
        key: `${agent.key}:own:${s.name}`, workspace: ws, agent,
        server: s.name, source: "自有", enabled: true,
        transport: s.transport || "unknown", runtime_loads: agent.runtime_loads,
        note: "自有配置优先于同名库条目", serverId: null,
      });
    }
    if (!(agent.bindings || []).length && !(agent.own_config?.servers || []).length && unread(agent, ws)) {
      if (qHit(route.q, agent.name, ws.name)) {
        rows.push({
          key: `${agent.key}:unread`, workspace: ws, agent,
          server: "—", source: "库", enabled: null, transport: "unknown",
          runtime_loads: agent.runtime_loads, note: "分配读取失败，看不到绑定", serverId: null,
        });
      }
    }
  }
  rows.sort((a, b) => (a.workspace.name || "").localeCompare(b.workspace.name || "") ||
    (a.agent.name || "").localeCompare(b.agent.name || "") || a.server.localeCompare(b.server));
  return rows;
}

export function summaryCards(payload, route = {}) {
  const wss = wsBySlug(payload, route.ws);
  const wsIds = new Set(wss.map((w) => w.id));
  const servers = wss.flatMap((w) => w.servers || []);
  const counts = payload?.attention?.counts || {};
  return [
    { id: "servers", label: "库条目", count: servers.length },
    { id: "assigned", label: "已分配", count: servers.filter((s) => (s.agent_count || 0) > 0).length },
    { id: "unused", label: "无人使用", count: servers.filter((s) => !(s.agent_count || 0)).length, bucket: "unused" },
    { id: "own", label: "有自有配置的 agent", count: (payload?.agents || []).filter((a) => wsIds.has(a.workspace_id) && a.own_config?.state === "managed").length },
    { id: "ignored", label: "会被忽略", count: counts.ignored ?? 0, bucket: "ignored" },
    { id: "shadowed", label: "同名覆盖", count: counts.shadowed ?? 0, bucket: "shadowed" },
  ];
}

/** Command templates. ids are always UUIDs; file-form only, never inline JSON. */
export function commandsFor(kind, ctx = {}) {
  const ws = ctx.workspace_id || ctx.ws || "";
  const agent = ctx.agent_id || "";
  const server = ctx.server_id || "";
  const name = ctx.name || "name";
  const agentName = ctx.agent_name || "agent";
  const base = `multica --workspace-id ${ws}`;
  const table = {
    assign: [{ label: "分配给 agent", command: `${base} agent mcp add ${agent} ${server}` }],
    enable: [{ label: "启用", command: `${base} agent mcp enable ${agent} ${server}` }],
    disable: [{ label: "停用", command: `${base} agent mcp disable ${agent} ${server}` }],
    unassign: [{ label: "取消分配", command: `${base} agent mcp remove ${agent} ${server}`, note: "只影响这个 agent；库条目和其他 agent 不受影响" }],
    rename: [{ label: "改名", command: `${base} workspace mcp update ${server} --name <新名>`, note: "分配按 id 保留", file: false }],
    "replace-config": [{ label: "替换配置", command: `${base} workspace mcp update ${server} --server-config-file ./${name}.mcp.json`, file: true }],
    "add-server": [{ label: "新增库条目", command: `${base} workspace mcp add <name> --server-config-file ./<name>.mcp.json`, file: true }],
    "delete-server": [{ label: "删除库条目", command: `${base} workspace mcp remove ${server}` }],
    "own-config": [{ label: "自有配置", command: `${base} agent update ${agent} --mcp-config-file ./${agentName}.mcp.json`, file: true }],
    "clear-own": [{ label: "清除自有配置", command: `${base} agent update ${agent} --mcp-config null` }],
  };
  return table[kind] || [];
}

// ---------- rendering ----------

function section(h, title, nodes) {
  return h("section", null, h("h3", { class: "drawer-sub" }, title), ...nodes.filter(Boolean));
}

function line(h, label, value) {
  return h("p", null, h("span", { class: "muted" }, `${label} `), h("span", { class: "mono" }, value || "—"));
}

function commandRows(h, ctx, entries) {
  return entries.flatMap((entry) => [
    entry.note ? h("p", { class: "muted" }, entry.note) : null,
    h("p", { class: "command" },
      h("span", { class: "muted" }, entry.label),
      h("code", null, entry.command),
      h("button", { type: "button", onclick: (e) => ctx.copyCommand(e.currentTarget, entry.command) }, "复制")),
    entry.file ? h("p", { class: "muted" }, FILE_HINT) : null,
  ]).filter(Boolean);
}

function cellTitle(agent, server, workspace, symbol) {
  return [`${agent?.name || ""} × ${server?.name || ""}`, cellMeaning(symbol)].filter(Boolean).join("\n");
}

export function renderMcp(ctx) {
  const { h, route } = ctx;
  const payload = ctx.mcp;
  if (!payload || payload.absent) {
    return [h("div", { class: "empty big" }, "后端还没有 MCP 采集")];
  }
  if (payload.ok === false && !payload.workspaces) {
    return [h("div", { class: "empty big" }, `MCP 数据读取失败：${payload.error || "未知错误"}`)];
  }
  const staleNote = payload.ok === false ? h("div", { class: "decide-note", role: "status" }, `本轮读取失败，显示上次数据：${payload.error || ""}`) : null;
  const view = route.mcpView === "list" ? "list" : "matrix";
  const cards = summaryCards(payload, route);
  const allEmpty = (payload.workspaces || []).every((w) => !(w.servers || []).length) &&
    !(payload.agents || []).some((a) => (a.own_config?.servers || []).length);
  return [
    h("p", { class: "muted" }, READONLY_LINE),
    staleNote,
    h("div", { class: "mcp-summary" },
      cards.map((card) =>
        h("button", {
          type: "button",
          class: `mcp-card${route.issue === card.bucket ? " active" : ""}`,
          disabled: !card.bucket,
          title: card.bucket ? "点击按此桶筛选" : card.label,
          onclick: () => card.bucket && ctx.setRoute({ issue: route.issue === card.bucket ? "" : card.bucket }),
        },
          h("strong", null, String(card.count)),
          h("span", null, card.label)))),
    h("div", { class: "toolbar" },
      h("label", { class: "filter" }, "workspace",
        h("select", {
          value: route.ws || "",
          onchange: (e) => ctx.setRoute({ ws: e.target.value }),
        },
          h("option", { value: "", selected: !route.ws }, "全部"),
          (payload.workspaces || []).map((w) => h("option", { value: w.slug, selected: route.ws === w.slug }, w.name)))),
      h("div", { class: "seg", role: "group", "aria-label": "视图" },
        h("button", { type: "button", class: view === "matrix" ? "active" : "", onclick: () => ctx.setRoute({ mcpView: "" }) }, "矩阵"),
        h("button", { type: "button", class: view === "list" ? "active" : "", onclick: () => ctx.setRoute({ mcpView: "list" }) }, "清单")),
      h("label", { class: "check" },
        h("input", { type: "checkbox", checked: route.issue === "problems", onchange: (e) => ctx.setRoute({ issue: e.target.checked ? "problems" : "" }) }),
        "只看有问题"),
      h("label", { class: "check" },
        h("input", { type: "checkbox", checked: route.archived, onchange: (e) => ctx.setRoute({ archived: e.target.checked }) }),
        "显示归档")),
    h("div", { class: "skills-layout" },
      h("div", { class: "skills-main" },
        allEmpty ? emptyAll(h, ctx, payload) : view === "list" ? listView(h, ctx, payload) : matrixView(h, ctx, payload)),
      queueAside(h, ctx, payload)),
  ];
}

function emptyAll(h, ctx, payload) {
  const ws = (payload.workspaces || [])[0];
  const entries = commandsFor("add-server", { workspace_id: ws?.id || "<ws-uuid>" });
  const providers = Object.entries(payload.support?.providers || {});
  return h("div", { class: "empty big mcp-empty" },
    h("p", null, "还没有 MCP server。库条目建好后不会自动给任何 agent，需要逐个分配"),
    ...commandRows(h, ctx, entries),
    h("h3", { class: "drawer-sub" }, "哪些 runtime 会加载（来源：" + (payload.support?.source || "") + "）"),
    h("table", { class: "mcp-support" },
      h("thead", null, h("tr", null, ["provider", "加载", "传递方式", "本机配置", "备注"].map((c) => h("th", null, c)))),
      h("tbody", null, providers.map(([id, p]) =>
        h("tr", null,
          h("td", { class: "mono" }, id),
          h("td", null, loadMark(p.loads)),
          h("td", { class: "muted" }, p.channel || "—"),
          h("td", { class: "muted" }, p.native_merge || "—"),
          h("td", { class: "muted" }, p.note || "—"))))));
}

function wsStateLabel(ws) {
  if (ws.library_state === "never") return { cls: "muted", text: "未加载" };
  if (ws.library_state === "error") return { cls: "bad-n", text: `库读取失败 · ${ws.library_error?.message || "未知原因"}`, stale: true };
  const parts = [];
  if (ws.bindings_state === "error") parts.push("分配读取失败");
  else if (ws.bindings_state === "partial") parts.push("分配部分失败");
  return { cls: parts.length ? "bad-n" : "muted", text: parts.join(" · ") };
}

function howToAdd(h, ctx, ws) {
  return h("details", { class: "mcp-howto" },
    h("summary", null, "如何添加"),
    h("p", { class: "muted" }, "库条目建好后不会自动给任何 agent，需要逐个分配："),
    ...commandRows(h, ctx, [
      ...commandsFor("add-server", { workspace_id: ws.id }),
      ...commandsFor("assign", { workspace_id: ws.id, agent_id: "<agent-uuid>", server_id: "<server-uuid>" }),
    ]));
}

function matrixView(h, ctx, payload) {
  const model = matrixModel(payload, ctx.agentsPayload, { ...ctx.route, archived: ctx.route.archived });
  return model.blocks.map((block) => {
    const ws = block.workspace;
    const state = wsStateLabel(ws);
    const head = h("header", { class: `mcp-ws-head${state.stale ? " is-error" : ""}` },
      h("strong", null, ws.name),
      h("span", { class: "muted" }, ` · 库 ${ws.servers.length} · 分配 ${ws.servers.reduce((n, s) => n + (s.agent_count || 0), 0)}`),
      state.text ? h("span", { class: state.cls }, ` · ${state.text}`) : null,
      howToAdd(h, ctx, ws));
    if (ws.library_state === "never") {
      return h("section", { class: "mcp-ws" }, head, h("div", { class: "empty" }, "未加载：还没有成功读取过这个 workspace 的 MCP 库"));
    }
    if (!block.columns.length && !block.rows.length) {
      return h("section", { class: `mcp-ws${state.stale ? " is-stale" : ""}` }, head,
        h("div", { class: "empty" }, "这个 workspace 还没有 MCP server 或符合筛选的行"));
    }
    if (block.fallbackToList) {
      return h("section", { class: `mcp-ws${state.stale ? " is-stale" : ""}` }, head,
        h("p", { class: "muted" }, block.reason), listTable(h, ctx, payload, ws.id));
    }
    return h("section", { class: `mcp-ws${state.stale ? " is-stale" : ""}` }, head, matrixTable(h, ctx, block));
  });
}

function matrixTable(h, ctx, block) {
  const ws = block.workspace;
  const head = h("thead", null,
    h("tr", null,
      h("th", { scope: "col" }, "Agent"),
      h("th", { scope: "col" }, "Runtime"),
      h("th", { scope: "col" }, "自有配置"),
      ...block.columns.map((s) =>
        h("th", { scope: "col", class: "mcp-srv", title: `${s.name} · ${s.transport}` },
          h("button", { type: "button", class: "mcp-srv-head", id: `mcp-srv-${ws.id}-${s.id}`, onclick: (e) => ctx.openMcpDrawer("server", { server: s, workspace: ws }, e.currentTarget) },
            s.name, h("small", null, s.transport)),
          h("small", { class: "muted" },
            s.enabled_count == null ? `分配 ${s.agent_count}` : `启用 ${s.enabled_count} · 停用 ${s.disabled_count}`)))));
  const rows = block.rows.map((agent) =>
    h("tr", { class: agent.archived ? "is-archived" : "" },
      h("td", null,
        h("button", { type: "button", class: "mcp-agent", id: `mcp-agent-${ws.id}-${agent.id}`, onclick: (e) => ctx.openMcpDrawer("agent", { agent, workspace: ws }, e.currentTarget) }, agent.name),
        agent.archived ? h("span", { class: "tag muted" }, "已归档") : null),
      h("td", { class: "rt", title: `${providerLabel(ctx.mcp, agent.provider)}\n${agent.runtime_loads}` },
        h("span", { class: `mcp-mark ${agent.runtime_loads}` }, loadMark(agent.runtime_loads)), " ", agent.provider || "—"),
      h("td", { class: "muted" }, ownConfigLabel(agent.own_config)),
      ...block.columns.map((s) => {
        const symbol = cellSymbol(agent, s, ws);
        return h("td", {
          class: `mcp-cell sym-${symbol}`,
          tabindex: "0",
          id: `mcp-cell-${ws.id}-${agent.id}-${s.id}`,
          dataset: { mcpCell: `${ws.id}:${agent.id}:${s.id}` },
          "aria-label": `${agent.name} × ${s.name}：${cellMeaning(symbol)}`,
          title: cellTitle(agent, s, ws, symbol),
          onclick: (e) => ctx.openMcpDrawer("cell", { agent, server: s, workspace: ws }, e.currentTarget),
          onkeydown: (e) => cellKey(e, ctx),
        }, symbol);
      })));
  if (block.restCount && !ctx.route.archived) {
    rows.push(h("tr", { class: "mcp-rest" },
      h("td", { colspan: String(3 + block.columns.length), class: "muted" },
        `其余 ${block.restCount} 个 agent 没有任何 MCP`)));
  }
  return h("div", { class: "table-wrap sticky-head" },
    h("table", { class: "matrix mcp-matrix", role: "grid", "aria-label": `${ws.name} MCP 矩阵` }, head, h("tbody", null, rows)));
}

function cellKey(e, ctx) {
  const td = e.currentTarget;
  if (e.key === "Enter") {
    e.preventDefault();
    td.click();
    return;
  }
  const dirs = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0] };
  const d = dirs[e.key];
  if (!d) return;
  e.preventDefault();
  const tr = td.parentElement;
  const cells = [...tr.children];
  const idx = cells.indexOf(td);
  if (d[1]) {
    const next = cells[idx + d[1]];
    if (next?.dataset?.mcpCell) next.focus();
    return;
  }
  const tbody = tr.parentElement;
  const rows = [...tbody.children];
  const ri = rows.indexOf(tr);
  const targetRow = rows[ri + d[0]];
  const target = targetRow?.children?.[idx];
  if (target?.dataset?.mcpCell) target.focus();
}

function listView(h, ctx, payload) {
  return wsBySlug(payload, ctx.route.ws).map((ws) => {
    const state = wsStateLabel(ws);
    return h("section", { class: `mcp-ws${state.stale ? " is-stale" : ""}` },
      h("header", { class: `mcp-ws-head${state.stale ? " is-error" : ""}` },
        h("strong", null, ws.name), state.text ? h("span", { class: state.cls }, ` · ${state.text}`) : null),
      listTable(h, ctx, payload, ws.id));
  });
}

function listTable(h, ctx, payload, wsId) {
  const rows = listRows(payload, ctx.route).filter((r) => r.workspace.id === wsId);
  if (!rows.length) return h("div", { class: "empty" }, "没有符合筛选的行");
  return h("div", { class: "table-wrap sticky-head" },
    h("table", { class: "matrix" },
      h("thead", null, h("tr", null, ["workspace", "agent", "server", "来源", "启用", "传输", "runtime 加载", "状态说明"].map((c) => h("th", { scope: "col" }, c)))),
      h("tbody", null, rows.map((r) =>
        h("tr", null,
          h("td", null, r.workspace.name),
          h("td", null, r.agent.name, r.agent.archived ? h("span", { class: "tag muted" }, "已归档") : null),
          h("td", { class: "mono" }, r.server),
          h("td", null, r.source),
          h("td", null, r.enabled == null ? "?" : r.enabled ? "是" : "否"),
          h("td", { class: "mono" }, r.transport),
          h("td", null, h("span", { class: `mcp-mark ${r.runtime_loads}` }, loadMark(r.runtime_loads))),
          h("td", { class: "muted" }, r.note || "—"))))));
}

function queueAside(h, ctx, payload) {
  const buckets = attentionBuckets(payload);
  return h("aside", { class: "skills-queue mcp-queue", "aria-label": "待处理" },
    h("h2", null, "待处理"),
    ...buckets.map((bucket) => {
      const active = ctx.route.issue === bucket.id;
      return h("section", { class: "skill-bucket" },
        h("header", null,
          h("button", { type: "button", class: `text-btn${active ? " active" : ""}`, onclick: () => ctx.setRoute({ issue: active ? "" : bucket.id }) }, bucket.title),
          h("span", { class: "count" }, String(bucket.count))),
        bucket.items.length
          ? h("ul", null, bucket.items.map((item) =>
              h("li", null,
                h("button", { type: "button", class: "queue-item", id: `mcp-queue-${item.key}`, onclick: (e) => openAttention(h, ctx, item, e.currentTarget) },
                  h("span", null, item.name || item.key),
                  h("span", { class: "muted" }, reasonLabel(item.reason))))))
          : h("p", { class: "muted" }, "无"));
    }));
}

const REASON_LABEL = {
  runtime_no_mcp: "runtime 不加载",
  runtime_unbound: "未绑 runtime",
  own_config_same_name: "与自有配置同名",
  opencode_v2_unsupported: "OpenCode 2.x 会失败",
  config_redacted: "配置看不到",
  no_agents: "没有 agent 使用",
  all_disabled: "全部停用",
};
const reasonLabel = (r) => REASON_LABEL[r] || r || "";

function openAttention(h, ctx, item, opener) {
  const ws = (ctx.mcp.workspaces || []).find((w) => w.id === item.workspace_id);
  if (item.agent_id) {
    const agent = (ctx.mcp.agents || []).find((a) => a.id === item.agent_id && a.workspace_id === item.workspace_id);
    if (agent && item.server_id) {
      const server = (ws?.servers || []).find((s) => s.id === item.server_id);
      if (server) return ctx.openMcpDrawer("cell", { agent, server, workspace: ws }, opener);
      return ctx.openMcpDrawer("agent", { agent, workspace: ws }, opener);
    }
    if (agent) return ctx.openMcpDrawer("agent", { agent, workspace: ws }, opener);
  }
  if (item.server_id && ws) {
    const server = (ws.servers || []).find((s) => s.id === item.server_id);
    if (server) return ctx.openMcpDrawer("server", { server, workspace: ws }, opener);
  }
}

// ---------- drawers ----------

function providerDetail(payload, provider) {
  if (!provider) return null;
  return payload?.support?.providers?.[provider] || null;
}

export function renderMcpAgentDrawer(h, ctx, { agent, workspace }) {
  const close = h("button", { type: "button", class: "drawer-close", "aria-label": "关闭", onclick: ctx.closeDrawer }, "×");
  const detail = providerDetail(ctx.mcp, agent.provider);
  const own = agent.own_config || { state: "none", servers: [] };
  const eff = agent.effective || { state: "unknown", servers: [] };
  return [
    h("header", { class: "drawer-head" },
      h("span", { class: "mono" }, agent.name),
      workspace ? h("span", { class: "muted" }, ` · ${workspace.name}`) : null, close),
    section(h, "Runtime", [
      line(h, "provider", providerLabel(ctx.mcp, agent.provider) || "—"),
      h("p", null, "加载 ", h("span", { class: `mcp-mark ${agent.runtime_loads}` }, loadMark(agent.runtime_loads)),
        detail?.channel ? h("span", { class: "muted" }, ` · ${detail.channel}`) : null),
      detail?.note ? h("p", { class: "muted" }, `备注：${detail.note}`) : null,
      detail?.remote_filter ? h("p", { class: "muted" }, "runtime 未声明能力时会丢掉 http/sse 条目") : null,
    ]),
    section(h, "下次任务实际生效", [
      eff.state !== "known" ? h("p", { class: "tag warn" }, eff.state === "unknown" ? "库未加载过，无法确定" : "分配没读到或配置看不到，列表可能不全") : null,
      eff.servers.length
        ? h("ul", null, eff.servers.map((s) => h("li", null, h("span", { class: "mono" }, s.name), ` · ${s.transport} · ${s.from === "own" ? "自有" : "库"}`)))
        : h("p", { class: "muted" }, "没有生效的 server"),
      h("p", { class: "muted" }, "Atlas 看不到：runtime 本机自己的 MCP 配置，以及单次任务的集成 overlay。"),
    ]),
    section(h, "自有配置", [
      own.state === "hidden" ? h("p", null, "Multica 不向当前身份返回这份配置") : null,
      own.state === "invalid" ? h("p", null, "配置无法解析") : null,
      own.state === "managed" && !own.servers.length ? h("p", { class: "muted" }, "托管，空配置") : null,
      ...(own.servers || []).map((s) =>
        h("p", null,
          h("span", { class: "mono" }, s.name), ` · ${s.transport}`,
          h("span", { class: "muted" }, ` · env ${s.env?.set ? `已设置 ${s.env.count} 项` : "未设置"} · headers ${s.headers?.set ? `已设置 ${s.headers.count} 项` : "未设置"}`))),
      ...commandRows(h, ctx, [
        ...commandsFor("own-config", { workspace_id: agent.workspace_id, agent_id: agent.id, agent_name: agent.name }),
        ...commandsFor("clear-own", { workspace_id: agent.workspace_id, agent_id: agent.id }),
      ]),
    ]),
  ].filter(Boolean);
}

export function renderMcpServerDrawer(h, ctx, { server, workspace, agentsPayload, mcp }) {
  const close = h("button", { type: "button", class: "drawer-close", "aria-label": "关闭", onclick: ctx.closeDrawer }, "×");
  const payload = mcp || ctx.mcp;
  const agents = (payload?.agents || []).filter((a) => a.workspace_id === workspace.id);
  const assigned = agents.filter((a) => bindingFor(a, server.id));
  const enabled = assigned.filter((a) => bindingFor(a, server.id)?.enabled);
  const disabled = assigned.filter((a) => !bindingFor(a, server.id)?.enabled);
  const unassigned = (agentsPayload?.items || []).filter((a) =>
    a.workspace_id === workspace.id && !a.archived && !assigned.some((m) => m.id === a.id));
  const unassignedKnown = unassigned.map((a) => {
    const mcpAgent = agents.find((m) => m.id === a.id);
    return { a, mcpAgent };
  }).filter(({ mcpAgent }) => !mcpAgent || mcpAgent.runtime_loads !== "no");
  return [
    h("header", { class: "drawer-head" },
      h("span", { class: "mono" }, server.name), h("span", { class: "muted" }, ` · ${workspace.name}`), close),
    section(h, "库条目", [
      line(h, "传输", server.transport),
      line(h, "创建", server.created_at ? ctx.absTime(server.created_at) : "—"),
      line(h, "更新", server.updated_at ? ctx.absTime(server.updated_at) : "—"),
      h("p", { class: "muted" }, CONFIG_WRITE_ONLY),
    ]),
    section(h, "已分配的 agent", [
      enabled.length ? h("p", null, "启用：", enabled.map((a) => agentWithMark(h, a)).flatMap((n, i) => i ? [", ", n] : [n])) : h("p", { class: "muted" }, "没有已启用的 agent"),
      disabled.length ? h("p", { class: "muted" }, `停用：${disabled.map((a) => a.name).join("、")}`) : null,
    ]),
    section(h, "未分配但 runtime 支持的 agent", [
      unassignedKnown.length
        ? unassignedKnown.map(({ a, mcpAgent }) => {
            const unreadNote = mcpAgent && unread(mcpAgent, workspace) ? h("span", { class: "muted" }, "（分配状态未读到）") : null;
            return h("p", null, agentWithMark(h, { name: a.name, runtime_loads: mcpAgent?.runtime_loads || "unknown" }), unreadNote,
              ...commandRows(h, ctx, commandsFor("assign", { workspace_id: workspace.id, agent_id: a.id, server_id: server.id })));
          })
        : h("p", { class: "muted" }, "没有可分配的 agent"),
    ]),
    section(h, "改名", [
      ...commandRows(h, ctx, commandsFor("rename", { workspace_id: workspace.id, server_id: server.id })),
      collisionNote(h, server, agents),
    ]),
    section(h, "替换配置", [
      enabled.length ? h("p", { class: "tag warn" }, `先列出受影响的已启用 agent：${enabled.map((a) => a.name).join("、")}，共 ${enabled.length} 个，下一次任务就生效`) : null,
      ...commandRows(h, ctx, commandsFor("replace-config", { workspace_id: workspace.id, server_id: server.id, name: server.name })),
    ]),
    section(h, "删除库条目", deleteFlow(h, ctx, server, workspace, assigned)),
  ].filter(Boolean);
}

function collisionNote(h, server, agents) {
  const hit = agents.some((a) => (a.own_config?.servers || []).some((s) => s.name === server.name));
  return hit ? h("p", { class: "tag warn" }, "新名和某个 agent 自有配置同名时会产生覆盖") : null;
}

function agentWithMark(h, agent) {
  return h("span", null, agent.name, " ", h("span", { class: `mcp-mark ${agent.runtime_loads}`, title: agent.runtime_loads || "" }, loadMark(agent.runtime_loads)));
}

function deleteFlow(h, ctx, server, workspace, assigned) {
  const n = assigned.length;
  if (!n) {
    return commandRows(h, ctx, commandsFor("delete-server", { workspace_id: workspace.id, server_id: server.id }));
  }
  const checked = ctx.deleteConfirmed === `${workspace.id}:${server.id}`;
  return [
    h("p", null, `删除会同时从 ${n} 个 agent 上移除：${assigned.map((a) => a.name).join("、")}`),
    h("label", { class: "check" },
      h("input", {
        type: "checkbox",
        checked,
        onchange: (e) => ctx.confirmDelete?.(e.target.checked ? `${workspace.id}:${server.id}` : ""),
      }),
      `我知道会同时从这 ${n} 个 agent 上移除`),
    checked ? commandRows(h, ctx, commandsFor("delete-server", { workspace_id: workspace.id, server_id: server.id })) : null,
  ].filter(Boolean);
}

export function renderMcpCellDrawer(h, ctx, { agent, server, workspace }) {
  const close = h("button", { type: "button", class: "drawer-close", "aria-label": "关闭", onclick: ctx.closeDrawer }, "×");
  const symbol = cellSymbol(agent, server, workspace);
  const binding = bindingFor(agent, server?.id);
  const args = { workspace_id: workspace.id, agent_id: agent.id, server_id: server.id, name: server.name };
  const commands = [];
  if (!binding) commands.push(...commandsFor("assign", args));
  else {
    commands.push(...commandsFor(binding.enabled ? "disable" : "enable", args), ...commandsFor("unassign", args));
  }
  const ignored = agent.runtime_loads === "no" || agent.runtime_loads === "unbound";
  return [
    h("header", { class: "drawer-head" },
      h("span", { class: "mono" }, `${agent.name} × ${server.name}`), close),
    h("p", null, h("span", { class: `mcp-cell sym-${symbol}` }, symbol), " ", cellMeaning(symbol)),
    ignored ? h("p", { class: "tag warn" }, `这个 runtime ${agent.runtime_loads === "unbound" ? "未绑定" : "不会加载 MCP"}，分配了也不会生效；命令照样给出`) : null,
    ...commandRows(h, ctx, commands),
    binding
      ? h("p", { class: "muted" }, "改库条目名：分配按 id 保留。", collisionNote(h, server, [agent]))
      : null,
  ].filter(Boolean);
}
