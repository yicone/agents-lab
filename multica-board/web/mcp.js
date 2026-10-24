/** MCP page (ACM-378 schema 2): one row per raw server name across local tools,
 * the repo's project-level files, and every Multica workspace. Read-only. */

export const PROJECT_SOURCE = "project"; // 伪来源：按 in_project 筛选，重叠于其他三类
export const REPO_TOOL = "repo";
export const mcpRowId = (item) => `mcp-row-${item.id}`;

export const READONLY_LINE = "页面只读，不代为执行。复制命令后在自己的终端运行。";
const READONLY_WS = "写命令需要 workspace admin/owner；agent 会话里运行会被拒绝。";
const READONLY_LOCAL = "改本机文件后，下一次扫描生效。";
export const FILE_HINT_WS = "文件里有密钥：建议 chmod 600，用完删除，不要放进 git 仓库。";
export const FILE_HINT_LOCAL = "文件里有密钥：保持 chmod 600，不要放进 git 仓库，共享屏幕时不要打开。";
const WRITE_ONLY = "配置只写不读：Multica 不向任何人返回库条目的配置，无法判断 env / headers";
const GROUP_NOTE = "同一 server 可能重复出现";
const GROUP_LIMIT = 8;

export const BUCKETS = [
  { id: "broken", title: "条目损坏", tone: "bad" },
  { id: "ignored", title: "会被忽略", tone: "bad" },
  { id: "shadowed", title: "同名覆盖", tone: "bad" },
  { id: "may_fail", title: "可能失败", tone: "bad" },
  { id: "drift", title: "配置不一致", tone: "warn" },
];
export const BUCKET_IDS = new Set(BUCKETS.map((b) => b.id));

const STATUS_ORDER = ["broken", "differs", "same", "single", "incomparable"];
const STATUS_LABEL = {
  broken: "损坏",
  differs: "不一致",
  same: "一致",
  single: "仅一处",
  incomparable: "不可比",
};
const STATUS_CLASS = { broken: "bad", differs: "warn", same: "ok", single: "muted", incomparable: "muted" };
const STATUS_OPTIONS = [
  ["", "全部状态"],
  ["broken", "损坏"],
  ["differs", "不一致"],
  ["same", "一致"],
  ["single", "仅一处"],
  ["incomparable", "不可比"],
];
const BROKEN_LABEL = {
  unknown_transport: "类型无法识别",
  bad_field_type: "字段类型不对",
  no_target: "缺 command 和 url",
  missing_target: "缺启动目标",
  not_object: "条目不是对象",
};
const PART_LABEL = { launch: "启动方式", env: "env", headers: "headers" };
const FILE_STATE_UNKNOWN = new Set(["denied", "symlink", "not_regular", "too_large", "parse_error", "unsupported", "io_error", "platform_unsupported"]);
const SOURCE_ORDER = ["both", "local_only", "multica_only"];
const MCP_SOURCES = new Set([...SOURCE_ORDER, PROJECT_SOURCE]);
const CELL_VALUES = new Set(["direct", "off", "missing", "unknown", "not_applicable"]);
const PRESENT_CELLS = new Set(["direct", "off", "missing"]);

export function badgeOf(payload) {
  return payload?.attention?.badge || 0;
}

export function badgeTone(payload) {
  return payload?.attention?.tone || "none";
}

/** 角标 title/aria-label 文案（§5）。 */
export function badgeTitle(payload) {
  const att = payload?.attention || {};
  const tone = att.tone || "none";
  const counts = att.counts || {};
  const red = (counts.broken || 0) + (counts.ignored || 0) + (counts.shadowed || 0) + (counts.may_fail || 0);
  const drift = counts.drift || 0;
  if (tone === "unknown") {
    return `MCP 有 ${unknownCount(payload)} 个来源没读到，是否有待处理未确定`;
  }
  let text = `MCP 待处理 ${att.badge || 0} 条：坏了 ${red} · 待你处理 ${drift}`;
  if (att.partial) text += `（有 ${unknownCount(payload)} 个来源没读到，可能不全）`;
  return text;
}

export function unknownCount(payload) {
  const u = payload?.unknown || {};
  return (u.local_files || []).length + (u.project_files || []).length +
    (u.library || []).length + (u.bindings || []).length +
    (u.own_hidden || 0) + (u.own_invalid || 0) + (u.own_pending || 0) +
    (u.key && u.key !== "ok" ? 1 : 0) + (u.multica && u.multica !== "collected" ? 1 : 0);
}

// ---------- models ----------

/** 来源卡：后端 summary 四块 + 前端合成的仓库项目级块（做法同 Skills 的 CLOUD_SOURCE）。 */
export function sourceBlocks(payload) {
  const items = payload?.items || [];
  const blocks = (payload?.summary || []).map((b) => ({ ...b }));
  const repoLabel = payload?.repos?.[0]?.label || "";
  const inProject = items.filter((i) => i.in_project);
  const statuses = {};
  for (const i of inProject) {
    const s = i.consistency?.state || "";
    statuses[s] = (statuses[s] || 0) + 1;
  }
  // Partial read failures do not erase counts already returned by the backend.
  const localFiles = (payload?.tools || []).flatMap((t) => t.files || []);
  const projectFiles = (payload?.repos || []).flatMap((r) => r.files || []);
  const unread = (files) => !files.some((f) => f.state === "ok" || f.state === "absent");
  const blocksOut = blocks.map((b) => ({
    ...b,
    sideUnknown: b.count === 0 && (
      (b.id === "local_only" && payload?.unknown?.multica !== "collected") ||
      (b.id === "multica_only" && unread(localFiles)) ||
      (["all", "both"].includes(b.id) && unread(localFiles) && payload?.unknown?.multica !== "collected")
    ),
  }));
  blocksOut.push({
    id: PROJECT_SOURCE,
    label: `仓库项目级${repoLabel ? ` · ${repoLabel}` : ""}`,
    count: inProject.length,
    uncertain: 0,
    statuses,
    // 项目级文件没读到时，这张卡的计数同样不可全信
    sideUnknown: inProject.length === 0 && unread(projectFiles),
    partial: (payload?.unknown?.project_files || []).length > 0,
  });
  return blocksOut;
}

function isPresent(value) {
  return PRESENT_CELLS.has(value);
}

function hasAssigned(item) {
  return (item.workspace_entries || []).some(
    (w) => w.kind === "library" && Array.isArray(w.agents) && w.agents.length > 0,
  );
}

function haystack(item, payload) {
  const toolLabels = [];
  for (const tool of payload?.tools || []) {
    if (item.tools?.[tool.id]) toolLabels.push(tool.label, tool.short_label);
  }
  const wsById = new Map((payload?.workspaces || []).map((w) => [w.id, w]));
  return [
    item.display_name,
    item.source_label,
    ...toolLabels,
    ...(item.entries || []).map((e) => e.path),
    ...(item.workspace_entries || []).flatMap((w) => [
      w.workspace_name,
      wsById.get(w.workspace_id)?.slug,
      w.agent_name,
      ...(w.agents || []).map((a) => a.name),
    ]),
  ]
    .filter(Boolean)
    .join("\n")
    .toLowerCase();
}

export function filteredItems(payload, route) {
  const q = (route.q || "").trim().toLowerCase();
  const rows = payload?.items || [];
  return rows.filter((item) => {
    if (route.source === PROJECT_SOURCE) {
      if (!item.in_project) return false;
    } else if (route.source && MCP_SOURCES.has(route.source) && item.source_class !== route.source) {
      return false;
    }
    if (route.mcpStatus && item.consistency?.state !== route.mcpStatus) return false;
    if (route.tool === REPO_TOOL) {
      if (!Object.values(item.repo_cells || {}).some(isPresent)) return false;
    } else if (route.tool && !isPresent(item.tools?.[route.tool])) {
      return false;
    }
    if (route.mcpAssigned && !hasAssigned(item)) return false;
    if (route.mcpTidy && !item.tidy) return false;
    if (q && !haystack(item, payload).includes(q)) return false;
    return true;
  });
}

function byAttention(a, b) {
  const rank = (i) => (i.bucket ? 0 : 1);
  return rank(a) - rank(b) || String(a.display_name).localeCompare(String(b.display_name)) || String(a.id).localeCompare(String(b.id));
}

export function groupedItems(items, group, tools, payload) {
  if (group === "flat") {
    return [{ id: "all", label: "全部", note: "", items: [...items].sort(byAttention) }];
  }
  if (group === "tool") {
    const groups = (tools || [])
      .map((tool) => ({
        id: tool.id,
        label: tool.label,
        note: GROUP_NOTE,
        items: items.filter((i) => isPresent(i.tools?.[tool.id])).sort(byAttention),
      }))
      .filter((g) => g.items.length);
    const repo = items.filter((i) => Object.values(i.repo_cells || {}).some(isPresent)).sort(byAttention);
    if (repo.length) groups.push({ id: REPO_TOOL, label: "仓库入口", note: GROUP_NOTE, items: repo });
    const rest = items.filter(
      (i) => !(tools || []).some((t) => isPresent(i.tools?.[t.id])) && !Object.values(i.repo_cells || {}).some(isPresent),
    );
    if (rest.length) groups.push({ id: "multica_only", label: "仅 Multica", note: "", items: rest.sort(byAttention) });
    return groups;
  }
  const labels = Object.fromEntries((payload?.summary || []).map((b) => [b.id, b.label]));
  const order = [...SOURCE_ORDER];
  const map = new Map();
  for (const item of items) {
    const id = item.source_class || "multica_only";
    if (!map.has(id)) map.set(id, { id, label: labels[id] || item.source_label || id, note: "", items: [] });
    map.get(id).items.push(item);
  }
  for (const id of map.keys()) if (!order.includes(id)) order.push(id);
  return order
    .filter((id) => map.has(id))
    .map((id) => ({ ...map.get(id), items: map.get(id).items.sort(byAttention) }));
}

/** 待处理栏：五桶 + 待清理。计数规则 §5：connected:false →「—」，partial →「N?」。 */
export function queueModel(payload) {
  const attention = payload?.attention || {};
  const buckets = BUCKETS.map((bucket) => {
    const raw = attention.buckets?.[bucket.id] || {};
    const connected = raw.connected !== false;
    const partial = raw.partial === true;
    const count = (raw.items || []).length;
    return {
      ...bucket,
      connected,
      partial,
      unknownSources: raw.unknown_sources || [],
      reason: raw.reason || "",
      items: raw.items || [],
      count,
      countText: !connected ? "—" : `${count}${partial ? " ?" : ""}`,
    };
  });
  const tidy = attention.tidy || {};
  const tidyConnected = tidy.connected !== false;
  const tidyPartial = tidy.partial === true;
  return {
    buckets,
    tidy: {
      connected: tidyConnected,
      partial: tidyPartial,
      unknownSources: tidy.unknown_sources || [],
      count: tidy.count || 0,
      countText: !tidyConnected ? "—" : `${tidy.count || 0}${tidyPartial ? " ?" : ""}`,
    },
  };
}

// ---------- copy with fallback（§8）----------

/**
 * 三条复制路径：clipboard → execCommand(textarea) → 返回 false。
 * env 可注入 { clipboard, document } 以便测试。
 */
export async function copyWithFallback(text, env = {}) {
  const clipboard = env.clipboard !== undefined
    ? env.clipboard
    : (typeof navigator !== "undefined" ? navigator.clipboard : undefined);
  const doc = env.document ?? (typeof document !== "undefined" ? document : undefined);
  try {
    if (clipboard?.writeText) {
      await clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to path 2
  }
  try {
    if (!doc) return false;
    const ta = doc.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    doc.body.appendChild(ta);
    ta.select();
    const ok = doc.execCommand("copy");
    ta.remove();
    return ok === true;
  } catch {
    return false;
  }
}

/** 失败回退：按钮「复制失败」，选中命令 <code>，加灰字提示行。 */
function fallbackCopy(h, ctx, button, commandEl) {
  button.textContent = "复制失败";
  const doc = commandEl?.ownerDocument;
  if (doc && commandEl) {
    const range = doc.createRange();
    range.selectNodeContents(commandEl);
    const sel = doc.defaultView?.getSelection?.();
    sel?.removeAllRanges();
    sel?.addRange(range);
  }
  const host = button.closest(".command");
  if (host && !host.parentElement?.querySelector(".copy-hint")) {
    host.after(h("p", { class: "copy-hint muted" }, "已选中命令，按 ⌘C 复制"));
  }
}

function commandNode(h, ctx, entry) {
  const code = h("code", null, entry.command);
  return [
    entry.label ? h("p", { class: "command" },
      h("span", { class: "muted" }, entry.label),
      code,
      h("button", {
        type: "button",
        onclick: async (e) => {
          const btn = e.currentTarget;
          const ok = await ctx.copyWithFallback(entry.command);
          if (ok) btn.textContent = "已复制";
          else fallbackCopy(h, ctx, btn, code);
        },
      }, "复制")) : h("p", { class: "command" }, code),
    entry.note ? h("p", { class: "muted" }, entry.note) : null,
  ];
}

// ---------- commands（v1 表，全部 --workspace-id + --*-config-file）----------

export function commandsFor(kind, ctx = {}) {
  const ws = ctx.workspace_id || "";
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
    rename: [{ label: "改名", command: `${base} workspace mcp update ${server} --name <新名>` }],
    "replace-config": [{ label: "替换配置", command: `${base} workspace mcp update ${server} --server-config-file ./${name}.mcp.json`, file: true }],
    "add-server": [{ label: "新增库条目", command: `${base} workspace mcp add <name> --server-config-file ./<name>.mcp.json`, file: true }],
    "delete-server": [{ label: "删除库条目", command: `${base} workspace mcp remove ${server}` }],
    "own-config": [{ label: "自有配置", command: `${base} agent update ${agent} --mcp-config-file ./${agentName}.mcp.json`, file: true }],
    "clear-own": [{ label: "清除自有配置", command: `${base} agent update ${agent} --mcp-config null` }],
  };
  return table[kind] || [];
}

export function openConfigCommand(path) {
  return `"${"${EDITOR:-vi"}"} ${path}`;
}

// ---------- rendering ----------

function section(h, title, nodes) {
  return h("section", null, h("h3", { class: "drawer-sub" }, title), ...nodes.filter(Boolean));
}

function line(h, label, value) {
  return h("p", null, h("span", { class: "muted" }, `${label} `),
    typeof value === "string" || value == null ? h("span", { class: "mono" }, value || "—") : value);
}

function commandRows(h, ctx, entries) {
  return entries.flatMap((entry) => commandNode(h, ctx, entry)).filter(Boolean);
}

function fileDetail(file) {
  return file.detail || "";
}

function anomalyBanner(h, payload) {
  const u = payload?.unknown || {};
  const tools = new Map((payload?.tools || []).map((t) => [t.id, t]));
  const wsName = new Map((payload?.workspaces || []).map((w) => [w.id, w.name || w.slug || w.id]));
  const lines = [];
  for (const id of u.local_files || []) {
    const tool = tools.get(id);
    const path = tool?.files?.[0]?.path || "";
    const detail = tool?.files?.[0]?.detail || "没读到";
    lines.push(`本机 ${tool?.label || id}：${path} ${detail}，${tool?.short_label || id} 列显示 ?，这一列的条目没有参与判断`);
  }
  for (const loc of u.project_files || []) {
    const file = (payload?.repos || []).flatMap((r) => r.files || []).find((f) => f.location === loc);
    lines.push(`仓库 ${loc}：${file?.path || loc} ${file?.detail || "没读到"}，仓库入口列显示 ?，这一列的条目没有参与判断`);
  }
  for (const ws of u.library || []) {
    lines.push(`${wsName.get(ws) || ws}：库没读到，行显示的是上次数据，不能证明没有`);
  }
  for (const ws of u.bindings || []) {
    lines.push(`${wsName.get(ws) || ws}：agent 分配没读到，这个 workspace 的库条目不判断是否无人使用`);
  }
  if ((u.own_hidden || 0) + (u.own_invalid || 0) > 0) {
    lines.push(`${(u.own_hidden || 0) + (u.own_invalid || 0)} 个 agent 的自有配置不可见或无效`);
  }
  if (u.own_pending) lines.push(`${u.own_pending} 个 agent 的自有配置指纹尚未就绪`);
  if (u.key && u.key !== "ok") lines.push("指纹密钥不可用，未比对配置是否一致");
  if (u.multica === "never") lines.push("Multica 从未采集 MCP");
  if (!lines.length) return null;
  return h("div", { class: "skills-anomaly", role: "status" }, lines.map((text) => h("div", null, text)));
}

function legend(h) {
  return h("div", { class: "skills-legend" },
    h("span", { class: "legend-group" },
      h("strong", null, "本机工具 "),
      "● 该工具全局配置里有同名条目 · ●停 条目显式停用 · ✕ 条目损坏（有这个条目但不能用：缺 command 和 url、类型无法识别、字段类型不对；对应 Skills 的「入口不在」） · ? 无法确定（文件没读到：无权限、符号链接、非普通文件、解析失败等），这一列不参与判断 · ∅ 不适用（确定只在 Multica） · 空白：已读该文件，没有同名条目，仍可能来自该工具的其他配置层。列头数字是 ● 的个数，? 表示文件没读到。路径证据不代表 runtime 实际加载。"),
    h("span", { class: "legend-group" },
      h("strong", null, "仓库入口 "),
      "项目级文件（.mcp.json / .cursor / .codex / .gemini / opencode.json）"),
    h("span", { class: "legend-group" },
      h("strong", null, "Multica Workspace "),
      "各 Workspace 的库条目和已启用的 Agent；「自有」是 agent 自有配置里的同名 server。— 表示 Multica MCP 未采集。"),
    h("span", { class: "legend-group" },
      h("strong", null, "一致性 "),
      "只比较配置指纹（本机密钥加盐哈希），不显示取值、env、header 或 token。Multica 库条目只写不读，标「不可比」。标签后的 ? 表示有来源没读到，结论只覆盖已读的。"));
}

function sourceCard(ctx, block) {
  const { h, route } = ctx;
  const id = block.id === "all" ? "" : block.id;
  const active = (route.source || "") === id;
  const recon = [];
  if (block.uncertain) {
    recon.push(h("span", { class: "muted", title: "有来源没读到，这些行可能在另一侧也有" }, `含 ${block.uncertain} 行待确认`));
  }
  if (block.partial) recon.push(h("span", { class: "muted" }, "? 有项目级来源没读到，计数可能不全"));
  for (const key of STATUS_ORDER) {
    const n = block.statuses?.[key];
    if (!n) continue;
    const cls = key === "broken" ? "bad-n" : key === "differs" ? "warn-n" : "muted";
    recon.push(h("span", { class: cls }, `${STATUS_LABEL[key]} ${n}`));
  }
  return h("button", {
    type: "button",
    class: `source-card${active ? " active" : ""}`,
    title: block.detail || block.label,
    "aria-pressed": active ? "true" : "false",
    onclick: () => ctx.setRoute({ source: active ? "" : id }),
  },
    h("span", { class: "source-label" }, block.label),
    h("strong", null, block.sideUnknown ? "—" : String(block.count ?? 0)),
    h("span", { class: "source-recon" },
      block.sideUnknown
        ? h("span", { class: "muted" }, "来源未读到，数量未知")
        : recon.length ? recon : h("span", { class: "muted" }, "—")));
}

function select(h, label, value, options, onchange) {
  return h("label", { class: "filter" }, label,
    h("select", { value, onchange: (e) => onchange(e.target.value) },
      options.map(([id, text]) => h("option", { value: id, selected: id === value }, text))));
}

function cellTitleFor(item, tool, value) {
  const file = tool?.files?.[0];
  const bits = [tool?.label || ""];
  if (file?.path) bits.push(file.path);
  if (file?.detail) bits.push(file.detail);
  const entry = (item.entries || []).find((e) => e.scope === "global" && e.tool === tool.id);
  if (entry?.group) bits.push(`指纹组 ${entry.group}`);
  bits.push("路径证据，未验证 runtime 实际加载");
  return bits.filter(Boolean).join("\n");
}

function cellNode(h, item, tool) {
  const value = item.tools?.[tool.id] ?? null;
  const title = cellTitleFor(item, tool, value);
  if (value === "direct") return h("td", { class: "tool-mark direct", title }, "●");
  if (value === "off") return h("td", { class: "tool-mark off", title }, "●", h("sup", null, "停"));
  if (value === "missing") return h("td", { class: "tool-mark missing", title }, "✕");
  if (value === "unknown") return h("td", { class: "tool-mark unknown", title }, "?");
  if (value === "not_applicable") return h("td", { class: "tool-mark not-applicable", title: "∅ 不适用：确定只在 Multica" }, "∅");
  return h("td", { class: "tool-mark none", title });
}

function repoCellNode(h, item, payload) {
  const cells = item.repo_cells || {};
  const files = (payload?.repos?.[0]?.files) || [];
  const parts = [];
  for (const f of files) {
    const value = cells[f.location];
    if (!value) continue;
    if (value === "not_applicable") {
      parts.push(h("span", { class: "not-applicable", title: "∅ 不适用：确定只在 Multica" }, "∅"));
      continue;
    }
    const cls = value === "missing" ? "missing" : value === "unknown" ? "unknown" : "present";
    const sym = value === "missing" ? "✕" : value === "unknown" ? "?" : "●";
    const title = [f.path, f.detail || "", sym === "●" && cells ? "" : ""].filter(Boolean).join(" · ");
    parts.push(h("span", { class: `entry-chip ${cls}`, title: title || f.path }, `${f.location} ${sym}`));
  }
  if (!parts.length) return h("td", { class: "repo-entries" }, "");
  return h("td", { class: "repo-entries" },
    h("div", { class: "repo-group" },
      h("div", { class: "repo-name muted" }, payload?.repos?.[0]?.label || ""),
      parts));
}

function agentBriefNames(agents) {
  const list = (agents || []).filter((a) => a.enabled !== false);
  if (!list.length) return "未启用";
  const names = list.map((a) => a.name || a.id).filter(Boolean);
  const shown = names.slice(0, 2).join("、");
  const rest = names.length - 2;
  return rest > 0 ? `${shown} +${rest}` : shown || "已启用";
}

function cloudCellNode(h, item, payload) {
  const wsEntries = item.workspace_entries || [];
  if (!wsEntries.length) {
    const u = payload?.unknown || {};
    if (u.multica === "never") return h("span", { class: "muted", title: "Multica MCP 未采集" }, "—");
    const unknownWs = [...(u.library || []), ...(u.bindings || [])];
    if (unknownWs.length) {
      const names = new Map((payload?.workspaces || []).map((w) => [w.id, w.name || w.slug || w.id]));
      return h("div", null, unknownWs.slice(0, 3).map((ws) =>
        h("div", { class: "cloud-ws" }, h("span", { class: "unknown" }, `? ${names.get(ws) || ws} 没读到`))));
    }
    return h("span", { class: "muted" }, "Multica 未配置");
  }
  const nodes = wsEntries.slice(0, 3).map((w) => {
    if (w.kind === "own") {
      return h("div", { class: "cloud-ws" }, h("span", null, w.workspace_name),
        h("span", { class: "muted" }, ` · ${w.agent_name || w.agent_id}（自有）`));
    }
    if (w.bindings_known === false) {
      return h("div", { class: "cloud-ws" }, h("span", null, w.workspace_name),
        h("span", { class: "unknown" }, " · 分配没读到"));
    }
    return h("div", { class: "cloud-ws" },
      h("span", null, w.workspace_name),
      h("span", { class: "muted" }, ` · ${agentBriefNames(w.agents)}`),
      w.stale ? h("span", { class: "tag muted", title: "库读取失败，显示的是上次数据" }, "旧数据") : null);
  });
  const rest = wsEntries.length - 3;
  if (rest > 0) nodes.push(h("div", { class: "muted" }, `另有 ${rest} 处`));
  const u = payload?.unknown || {};
  const missing = [...(u.library || []), ...(u.bindings || [])]
    .filter((id) => !wsEntries.some((w) => w.workspace_id === id));
  const names = new Map((payload?.workspaces || []).map((w) => [w.id, w.name || w.slug || w.id]));
  for (const ws of missing.slice(0, 2)) {
    nodes.push(h("div", { class: "cloud-ws" }, h("span", { class: "unknown" }, `? ${names.get(ws) || ws} 没读到`)));
  }
  return h("div", null, nodes);
}

function transportCell(h, item) {
  const list = item.transports || [];
  const text = list.length ? list.join(" / ") : "unknown";
  if (list.length > 1) return h("span", { class: "warn-n", title: "各处的传输方式不同" }, text);
  return text;
}

function consistencyTag(h, item) {
  const c = item.consistency || {};
  const state = c.state || "incomparable";
  const label = STATUS_LABEL[state] || state;
  const text = state === "differs" ? `${label} · ${(c.groups || []).length} 组` : label;
  const parts = [];
  for (const key of Object.keys(PART_LABEL)) {
    if (c.parts?.[key] === "differs") parts.push(`${PART_LABEL[key]}不同`);
  }
  const title = [
    parts.length ? parts.join("、") : null,
    c.pending ? `自有配置待采集 ${c.pending} 个，未参与比对` : null,
    c.partial ? "部分来源未读到或待采集，结论只覆盖已读的" : null,
    state === "incomparable" ? "Multica 库条目只写不读，不参与比对" : null,
  ].filter(Boolean).join("\n");
  return h("span", { class: `tag ${STATUS_CLASS[state] || "muted"}`, title }, `${text}${c.partial ? " ?" : ""}`);
}

function presenceLine(item) {
  const p = item.presence || {};
  const bits = [];
  if (p.local) bits.push(`本机 ${p.local}`);
  if (p.project) bits.push(`仓库 ${p.project}`);
  if (p.library) bits.push(`库 ${p.library}`);
  if (p.own) bits.push(`自有 ${p.own}`);
  return bits.join(" · ") || "—";
}

function bucketTag(h, item) {
  if (item.tidy && !item.bucket) {
    return h("span", { class: "tag muted", title: "只有停用或无人使用，不计入角标" }, "待清理");
  }
  const bucket = BUCKETS.find((b) => b.id === item.bucket);
  if (!bucket) return null;
  return h("span", { class: `tag ${bucket.tone === "bad" ? "bad" : "warn"}`, title: item.reason || "" }, bucket.title);
}

function row(ctx, tools, item, payload) {
  const { h } = ctx;
  const selected = ctx.drawerMcp === item.id;
  return h("tr", {
    id: mcpRowId(item),
    class: `skill-row${item.quiet ? " quiet" : ""}${selected ? " selected" : ""}`,
    tabindex: "0",
    onclick: (e) => ctx.openMcp(item.id, e.currentTarget),
    onkeydown: (e) => {
      if (e.key === "Enter") ctx.openMcp(item.id, e.currentTarget);
    },
  },
    h("td", null,
      h("span", { class: "mono" }, item.display_name),
      item.display_dup
        ? h("span", { class: "muted", title: "显示名相同，原名不同（空白、长度或脱敏后相同）" },
            `（同显示名 ${item.display_dup.index}/${item.display_dup.total}）`)
        : null,
      (item.tags || []).includes("name_unverified")
        ? h("span", { class: "tag muted", title: "名字含被清洗的字符，本次启动后尚未重新采集，暂不合并" }, "原名待确认")
        : null,
      bucketTag(h, item)),
    h("td", { class: "channel-col", title: `${item.source_label} · ${presenceLine(item)}` },
      h("div", { class: "cell-ellipsis" }, item.source_label),
      h("div", { class: "muted mono cell-ellipsis" }, presenceLine(item)),
      item.source_uncertain ? h("span", { class: "warn-n", title: "有来源没读到，可能不止这里" }, "待确认") : null),
    h("td", { class: "cloud-cell" }, cloudCellNode(h, item, payload)),
    ...tools.map((tool) => cellNode(h, item, tool)),
    repoCellNode(h, item, payload),
    h("td", { class: "mono version-col" }, h("div", { class: "cell-ellipsis" }, transportCell(h, item))),
    h("td", null, consistencyTag(h, item)));
}

function columnTitle(tool) {
  const file = tool.files?.[0];
  return [tool.label, file?.path ? `${file.path} · ${file.detail || ""}` : ""].filter(Boolean).join("\n");
}

function table(ctx, tools, groups, payload) {
  const { h, route } = ctx;
  const colspan = String(4 + tools.length + 1);
  const visible = new Map(tools.map((tool) => [
    tool.id,
    (payload.items || []).filter((i) => i.tools?.[tool.id] === "direct" || i.tools?.[tool.id] === "off").length,
  ]));
  const head = h("tr", null,
    h("th", null, "Server"),
    h("th", { class: "channel-col" }, "来源 / 出现位置"),
    h("th", { title: "Multica Workspace（跨 Workspace）" }, "Multica Workspace"),
    ...tools.map((tool) =>
      h("th", { class: "tool-col", title: columnTitle(tool) },
        tool.short_label || tool.label,
        h("small", null, tool.files?.[0] && FILE_STATE_UNKNOWN.has(tool.files[0].state) ? "?" : String(visible.get(tool.id) || 0)))),
    h("th", { class: "tool-col repo-col", title: "仓库入口" }, "仓库入口"),
    h("th", { class: "version-col", title: "传输方式；各处不同时全部列出" }, "传输"),
    h("th", null, "一致性"));
  const body = [];
  for (const group of groups) {
    const open = ctx.expandedMcp?.has(group.id);
    const shown = open ? group.items : group.items.slice(0, GROUP_LIMIT);
    const rest = group.items.length - shown.length;
    body.push(h("tr", { class: "skill-group" },
      h("th", { colspan }, group.label,
        h("span", { class: "muted" }, ` · ${group.items.length}`),
        group.note ? h("span", { class: "muted" }, ` · ${group.note}`) : null)));
    for (const item of shown) body.push(row(ctx, tools, item, payload));
    if (rest > 0) {
      body.push(h("tr", { class: "skill-more" },
        h("td", { colspan },
          h("button", { type: "button", class: "text-btn", onclick: () => ctx.toggleMcpGroup(group.id) },
            `显示其余 ${rest} 条`))));
    }
  }
  if (!groups.length) {
    body.push(h("tr", null, h("td", { class: "empty", colspan }, "没有符合筛选的 server")));
  }
  return h("div", { class: "table-wrap sticky-head", tabindex: "-1" },
    h("table", { class: "skills-table" }, h("thead", null, head), h("tbody", null, body)));
}

function queue(ctx, payload) {
  const { h, route } = ctx;
  const model = queueModel(payload);
  return h("aside", { class: "skills-queue", "aria-label": "待处理" },
    h("div", { class: "skills-compact" },
      model.buckets.map((b) => h("span", null, `${b.title} ${b.countText}`)),
      h("span", null, `待清理 ${model.tidy.countText}`)),
    h("h2", null, "待处理"),
    ...model.buckets.map((bucket) => bucketBlock(ctx, bucket)),
    h("button", {
      type: "button",
      class: `debt${route.mcpTidy ? " active" : ""}`,
      title: model.tidy.partial ? `另有来源没读到：${model.tidy.unknownSources.join("、")}` : "无人使用或只有停用，不计入角标",
      onclick: () => ctx.setRoute({ mcpTidy: !route.mcpTidy }),
    },
      h("span", null, "待清理 · 无人使用 / 只有停用"),
      h("strong", null, model.tidy.countText)));
}

function bucketBlock(ctx, bucket) {
  const { h } = ctx;
  const countCls = bucket.count === 0 ? "muted" : bucket.tone === "bad" ? "bad-n" : bucket.tone === "warn" ? "warn-n" : "muted";
  let body;
  if (!bucket.connected) {
    body = h("p", { class: "muted" }, `— ${bucket.reason}`);
  } else {
    const nodes = [];
    if (bucket.items.length) {
      nodes.push(h("ul", null, bucket.items.map((item) =>
        h("li", null,
          h("button", {
            type: "button",
            class: "queue-item",
            onclick: (e) => ctx.focusMcp(item.id, e.currentTarget),
          },
            h("span", { class: "mono" }, item.title ? (payloadDisplay(ctx, item)) : ""),
            h("span", null, item.title || ""),
            h("span", { class: "muted" }, relTime(item.time, ctx.ago)))))));
    } else if (!bucket.partial) {
      nodes.push(h("p", { class: "muted" }, "无"));
    }
    if (bucket.partial) {
      nodes.push(h("p", { class: "muted" }, `另有来源没读到：${bucket.unknownSources.join("、") || "部分来源"}，可能还有`));
    }
    body = nodes;
  }
  return h("section", { class: "skill-bucket" },
    h("header", null,
      h("span", null, bucket.title),
      h("span", { class: `count ${bucket.connected ? countCls : "muted"}` }, bucket.countText)),
    body);
}

function payloadDisplay(ctx, bucketItem) {
  const item = (ctx.mcp?.items || []).find((i) => i.id === bucketItem.id);
  return item?.display_name || "";
}

function relTime(iso, ago) {
  if (!iso) return "";
  return ago ? ago(iso) : iso;
}

// ---------- empty states（5.1）----------

function emptyGuide(h, ctx, payload) {
  const u = payload?.unknown || {};
  const anyKnown = !(u.local_files || []).length && !(u.project_files || []).length &&
    u.multica === "collected" && !(u.own_hidden || 0) && !(u.own_invalid || 0) && u.key === "ok";
  const allUnknown = u.multica !== "collected" &&
    ((payload?.tools || []).every((t) => FILE_STATE_UNKNOWN.has(t.files?.[0]?.state)) || !(payload?.tools || []).length) &&
    ((payload?.repos || []).flatMap((r) => r.files || []).every((f) => FILE_STATE_UNKNOWN.has(f.state)) || true);
  if (allUnknown) {
    return h("div", { class: "atlas-guide" },
      h("h2", null, "所有来源都没读到"),
      h("p", null, "本机配置、项目级文件和 Multica 都没读到，无法判断有没有 MCP server。"),
      h("p", { class: "muted" }, "修好上面的异常提示后刷新。"));
  }
  if (anyKnown) {
    const wsCmds = (payload?.workspaces || []).filter((w) => w.library_state === "ok")
      .flatMap((w) => commandsFor("add-server", { workspace_id: w.id }));
    return h("div", { class: "atlas-guide" },
      h("h2", null, "还没有 MCP server"),
      h("p", null, "本机各工具的全局配置和 agents-lab 项目级文件里都没有 MCP 条目，Multica 库也为空。库条目建好后不会自动给任何 agent，需要逐个分配。"),
      h("details", null,
        h("summary", null, "新增库条目命令"),
        h("p", { class: "muted" }, `${READONLY_LINE}${READONLY_WS}`),
        ...commandRows(h, ctx, wsCmds),
        wsCmds.length ? h("p", { class: "muted" }, FILE_HINT_WS) : null));
  }
  const bits = [];
  const tools = new Map((payload?.tools || []).map((t) => [t.id, t]));
  for (const id of u.local_files || []) bits.push(`本机 ${tools.get(id)?.label || id}：${tools.get(id)?.files?.[0]?.detail || "没读到"}`);
  for (const loc of u.project_files || []) bits.push(`仓库 ${loc}：没读到`);
  for (const ws of u.library || []) bits.push(`${ws}：库没读到`);
  for (const ws of u.bindings || []) bits.push(`${ws}：分配没读到`);
  if (u.multica !== "collected") bits.push("Multica：从未采集");
  if (u.key && u.key !== "ok") bits.push("指纹密钥：不可用");
  return h("div", { class: "atlas-guide" },
    h("h2", null, "没有读到 MCP server"),
    h("p", null, "已读的来源里没有 MCP 条目。以下来源没读到，不能确定没有："),
    h("ul", null, bits.map((b) => h("li", null, b))),
    h("p", { class: "muted" }, "修好对应来源后刷新。"));
}

export function renderMcp(ctx) {
  const { h, route } = ctx;
  const payload = ctx.mcp;
  if (!payload || payload.absent) {
    return [h("div", { class: "atlas-guide" }, h("h2", null, "后端还没有 MCP 采集"), h("p", { class: "muted" }, "升级后端后刷新。"))];
  }
  if (payload.ok === false) {
    return [h("div", { class: "atlas-guide" },
      h("h2", null, "MCP 数据无效"),
      h("p", null, payload.error?.message || payload.error?.code || payload.error || "读取失败"),
      h("p", { class: "muted" }, "检查 mcp-sources.json 声明是否有效，修好后刷新。"))];
  }
  const tools = payload.tools || [];
  const items = filteredItems(payload, route);
  const groups = groupedItems(items, route.mcpGroup || "source", tools, payload);
  const blocks = sourceBlocks(payload);
  const noItems = !(payload.items || []).length;
  return [
    h("div", { class: "source-grid" }, blocks.map((block) => sourceCard(ctx, block))),
    h("div", { class: "toolbar" },
      select(h, "来源", route.source, [["", "全部来源"], ...blocks.filter((b) => b.id !== "all").map((b) => [b.id, b.label])], (v) => ctx.setRoute({ source: v })),
      select(h, "状态", route.mcpStatus, STATUS_OPTIONS, (v) => ctx.setRoute({ mcpStatus: v })),
      select(h, "工具", route.tool, [["", "全部工具"], ...tools.map((t) => [t.id, t.label]), [REPO_TOOL, "仓库入口"]], (v) => ctx.setRoute({ tool: v })),
      select(h, "分组", route.mcpGroup || "source", [["source", "按来源"], ["tool", "按工具"], ["flat", "不分组"]], (v) => ctx.setRoute({ mcpGroup: v })),
      h("label", { class: "check" },
        h("input", { type: "checkbox", checked: route.mcpAssigned, onchange: (e) => ctx.setRoute({ mcpAssigned: e.target.checked }) }),
        "只看已分配给 agent")),
    anomalyBanner(h, payload),
    legend(h),
    h("div", { class: "skills-layout" },
      h("div", { class: "skills-main" },
        noItems ? emptyGuide(h, ctx, payload) : table(ctx, tools, groups, payload)),
      queue(ctx, payload)),
  ].filter(Boolean);
}

// ---------- drawer（§6）----------

function loadTag(h, agent) {
  const loads = agent.runtime_loads;
  const map = {
    yes: ["ok", "会加载"],
    no: ["bad", "不加载"],
    conditional: ["warn", "有条件"],
    unbound: ["muted", "未绑定"],
  };
  const [cls, text] = map[loads] || ["muted", "未知"];
  return h("span", { class: `tag ${cls}`, title: agent.provider ? `provider：${agent.provider}` : "provider 未知" }, text);
}

function drawerSourceScope(h, item) {
  const nodes = [
    line(h, "名称", item.display_name),
    item.display_dup
      ? h("p", { class: "muted" }, `显示名相同，原名不同，这是第 ${item.display_dup.index}/${item.display_dup.total} 个`)
      : null,
    (item.tags || []).includes("name_unverified")
      ? h("p", { class: "muted" }, "名字含被清洗的字符，本次启动后尚未重新采集，暂不合并")
      : null,
    line(h, "来源", item.source_label),
    item.source_uncertain ? h("p", { class: "muted" }, "有来源没读到，可能不止这里") : null,
    line(h, "传输", (item.transports || []).join(" / ") || "unknown"),
    line(h, "本机工具", String((item.entries || []).filter((e) => e.scope === "global").length)),
    line(h, "仓库项目级", String((item.entries || []).filter((e) => e.scope === "project").length)),
    line(h, "Multica", `库条目 ${item.presence?.library || 0} · 自有 ${item.presence?.own || 0}`),
  ];
  return section(h, "来源与范围", nodes);
}

function drawerConsistency(h, item) {
  const c = item.consistency || {};
  const nodes = [
    h("p", null, consistencyTag(h, item), h("span", { class: "muted" }, " 只比较配置指纹，不显示取值")),
    ...(c.groups || []).map((g) => line(h, `指纹组 ${g.id}`, (g.members || []).join("、"))),
  ];
  for (const key of ["launch", "env", "headers"]) {
    if (c.parts?.[key]) nodes.push(line(h, PART_LABEL[key], c.parts[key] === "same" ? "一致" : "不同"));
  }
  const pending = (item.workspace_entries || []).filter((w) => w.kind === "own" && w.fp_state === "pending");
  if (pending.length) {
    nodes.push(h("p", { class: "muted" },
      `未参与比对：${pending.map((w) => w.agent_name || w.agent_id).join("、")}（本次启动后尚未采集）`));
  }
  if (c.partial) {
    nodes.push(h("p", { class: "muted" }, "有来源没读到或待采集，结论只覆盖已读的"));
  }
  nodes.push(h("p", { class: "muted" }, "Multica 库条目只写不读，不参与比对。停用、超时、工具白名单等字段不参与比对。"));
  return section(h, "一致性", nodes);
}

function entryLine(h, e, tools) {
  const tool = tools.find((t) => t.id === e.tool);
  const name = tool?.label || e.tool || "仓库";
  const mark = e.state === "broken" ? "✕" : e.disabled ? "●停" : "●";
  const bits = [name];
  if (e.group) bits.push(`组 ${e.group}`);
  if (e.broken_reason) bits.push(BROKEN_LABEL[e.broken_reason] || "损坏");
  else {
    bits.push(e.env?.set ? `env 已设置 ${e.env.count} 项` : "env 未设置");
    bits.push(e.headers?.set ? `headers 已设置 ${e.headers.count} 项` : "headers 未设置");
  }
  if (e.disabled) bits.push("已停用");
  return h("p", { class: "skill-entry" }, `${mark} `,
    h("span", { class: "mono" }, e.path),
    h("span", { class: "muted" }, ` · ${bits.join(" · ")}`));
}

function drawerFiles(h, item, payload, scope, title) {
  const tools = payload?.tools || [];
  const entries = (item.entries || []).filter((e) => e.scope === scope);
  const nodes = entries.map((e) => entryLine(h, e, tools));
  // 没读到的文件也列一行：? + 路径 + 固定原因
  const listed = new Set(entries.map((e) => (e.scope === "project" ? e.location : e.tool)));
  if (scope === "global") {
    for (const t of tools) {
      if (listed.has(t.id)) continue;
      const f = t.files?.[0];
      if (f && FILE_STATE_UNKNOWN.has(f.state)) {
        nodes.push(h("p", { class: "skill-entry" }, "? ",
          h("span", { class: "mono" }, f.path),
          h("span", { class: "muted" }, ` · ${t.label} · ${f.detail || "没读到"}`)));
      }
    }
  } else {
    for (const repo of payload?.repos || []) {
      for (const f of repo.files || []) {
        if (listed.has(f.location)) continue;
        if (FILE_STATE_UNKNOWN.has(f.state)) {
          nodes.push(h("p", { class: "skill-entry" }, "? ",
            h("span", { class: "mono" }, f.path),
            h("span", { class: "muted" }, ` · ${f.detail || "没读到"}`)));
        }
      }
    }
  }
  if (!nodes.length) {
    nodes.push(h("p", { class: "muted" }, scope === "project" ? "项目级文件里没有同名条目" : "本机工具的全局配置里没有同名条目"));
  }
  nodes.push(h("p", { class: "muted" }, "路径证据，未验证 runtime 当前会话实际加载。"));
  if (scope === "project" && entries.some((e) => e.env?.set)) {
    nodes.push(h("p", { class: "muted" }, "项目级文件在仓库工作区里；env 有值时确认它没有被提交。"));
  }
  return section(h, title, nodes);
}

function drawerMultica(h, ctx, item) {
  const nodes = [h("p", { class: "muted" }, `${READONLY_LINE}${READONLY_WS}`)];
  const byWs = new Map();
  for (const w of item.workspace_entries || []) {
    if (!byWs.has(w.workspace_id)) byWs.set(w.workspace_id, []);
    byWs.get(w.workspace_id).push(w);
  }
  if (!byWs.size) {
    nodes.push(h("p", { class: "muted" }, "Multica 没有同名条目。"));
  }
  for (const [wsId, entries] of byWs) {
    const wsName = entries[0]?.workspace_name || wsId;
    const lib = entries.find((e) => e.kind === "library");
    const owns = entries.filter((e) => e.kind === "own");
    const block = [
      h("p", null, h("strong", null, wsName),
        lib ? h("span", { class: "muted" }, ` · 库条目 · ${lib.transport || "unknown"}`) : null,
        lib?.stale ? h("span", { class: "tag muted" }, " 旧数据") : null),
    ];
    if (lib) {
      block.push(h("p", { class: "muted" }, WRITE_ONLY));
      if (lib.updated_at) block.push(line(h, "更新时间", lib.updated_at));
      if (lib.bindings_known === false || lib.agents == null) {
        block.push(h("p", { class: "muted" }, "分配没读到，无法列出 agent，也不判断是否无人使用"));
      } else {
        const enabled = (lib.agents || []).filter((a) => a.enabled !== false);
        const disabled = (lib.agents || []).filter((a) => a.enabled === false);
        if (enabled.length) {
          block.push(h("p", null, "已启用 ", enabled.flatMap((a, i) => [
            i ? "、" : null,
            h("span", null, a.name || a.id),
            " ",
            loadTag(h, a),
          ]).filter(Boolean)));
        }
        if (disabled.length) {
          block.push(h("p", { class: "muted" }, `未启用 ${disabled.map((a) => a.name || a.id).join("、")}`));
        }
        for (const a of enabled) {
          block.push(...commandRows(h, ctx, [
            ...commandsFor("disable", { workspace_id: wsId, agent_id: a.id, server_id: lib.server_id }),
            ...commandsFor("unassign", { workspace_id: wsId, agent_id: a.id, server_id: lib.server_id }),
          ]));
        }
        for (const a of disabled) {
          block.push(...commandRows(h, ctx,
            commandsFor("enable", { workspace_id: wsId, agent_id: a.id, server_id: lib.server_id })));
        }
      }
      block.push(h("details", null,
        h("summary", null, "替换配置 · 删除库条目"),
        h("p", { class: "muted" }, `影响范围：这个 workspace 的库条目会影响已分配的所有 agent（${lib.agent_count ?? 0} 个）。`),
        ...commandRows(h, ctx, [
          ...commandsFor("replace-config", { workspace_id: wsId, server_id: lib.server_id, name: item.display_name }),
          ...commandsFor("delete-server", { workspace_id: wsId, server_id: lib.server_id }),
        ]),
        h("p", { class: "muted" }, FILE_HINT_WS)));
    }
    for (const own of owns) {
      block.push(h("p", { class: "skill-entry" },
        h("span", null, own.agent_name || own.agent_id),
        h("span", { class: "muted" },
          `（自有） · ${own.group ? `组 ${own.group}` : "未参与比对"} · ${own.env?.set ? `env 已设置 ${own.env.count} 项` : "env 未设置"} · ${own.headers?.set ? `headers 已设置 ${own.headers.count} 项` : "headers 未设置"}`)));
      block.push(...commandRows(h, ctx,
        commandsFor("clear-own", { workspace_id: wsId, agent_id: own.agent_id, agent_name: own.agent_name })));
    }
    nodes.push(h("div", { class: "cloud-install" }, block));
  }
  return section(h, "Multica Workspace（跨 Workspace）", nodes);
}

function drawerTerminal(h, ctx, item, payload) {
  const nodes = [h("p", { class: "muted" }, `${READONLY_LINE}${READONLY_LOCAL}`)];
  const seen = new Set();
  const tools = new Map((payload?.tools || []).map((t) => [t.id, t]));
  for (const e of item.entries || []) {
    if (seen.has(e.path)) continue;
    seen.add(e.path);
    const file = e.scope === "global"
      ? tools.get(e.tool)?.files?.[0]
      : (payload?.repos || []).flatMap((r) => r.files || []).find((f) => f.location === e.location);
    if (file?.state === "symlink") {
      nodes.push(h("p", { class: "muted" }, "这个路径是符号链接，看板没有跟随；请自己确认链接目标"));
      continue;
    }
    const label = e.scope === "project" ? `打开仓库 ${e.location} 配置` : `打开 ${tools.get(e.tool)?.label || e.tool} 配置`;
    nodes.push(...commandNode(h, ctx, { label, command: openConfigCommand(e.path) }));
  }
  nodes.push(h("p", { class: "muted" }, FILE_HINT_LOCAL));
  return section(h, "在终端处理", nodes);
}

export function renderMcpDrawer(ctx, item) {
  const { h } = ctx;
  const close = h("button", { type: "button", class: "drawer-close", "aria-label": "关闭", onclick: ctx.closeDrawer }, "×");
  return [
    h("header", { class: "drawer-head" },
      h("div", null,
        h("span", { class: "mono" }, item.display_name),
        (item.transports || []).map((t) => h("span", { class: "tag" }, t))),
      close),
    h("h2", { class: "drawer-title" }, item.source_label),
    drawerSourceScope(h, item),
    drawerConsistency(h, item),
    drawerFiles(h, item, ctx.mcp, "global", "本机工具配置"),
    drawerFiles(h, item, ctx.mcp, "project", "仓库项目级"),
    drawerMultica(h, ctx, item),
    drawerTerminal(h, ctx, item, ctx.mcp),
  ].filter(Boolean);
}
