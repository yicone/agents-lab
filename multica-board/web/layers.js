/** Layer model for Agent Atlas. Live counts come from GET /api/layers; fixtures still aggregate issue.layer. */

export const LAYERS = [
  { id: "l5", short: "L5", name: "感知与决策", objects: "监督 · Board · 清单", color: "#6d4fd8" },
  { id: "l4", short: "L4", name: "协作", objects: "workspace · project · issue · 派活", color: "#2f6fde" },
  { id: "l3", short: "L3", name: "能力", objects: "skills · instructions · spec", color: "#0f8f8a" },
  { id: "l2", short: "L2", name: "执行", objects: "runtime · daemon · 运行环境", color: "#b7791f" },
  { id: "l1", short: "L1", name: "资源", objects: "额度 · 算力", color: "#6b7280" },
];

export const LAYER_BY_ID = Object.fromEntries(LAYERS.map((layer) => [layer.id, layer]));

export const OPTION_NAME = {
  l1: "L1 资源",
  l2: "L2 执行",
  l3: "L3 能力",
  l4: "L4 协作",
  l5: "L5 感知与决策",
  outside: "不在地图",
};

const LAYER_SET = new Set(LAYERS.map((layer) => layer.id));

export const MAP_TOOLS = {
  l5: [
    { label: "Issues", kind: "page", href: "#/issues", signal: "issues" },
    { label: "每日清单", kind: "gap", gap: "未建" },
    { label: "状态桥接", kind: "gap", gap: "未建" },
  ],
  l4: [
    { label: "Workspace 健康", kind: "page", href: "#/issues", signal: "workspaces" },
    { label: "Grok Bots & 桥接", kind: "page", href: "#/agents?kind=bots", signal: "bots" },
    { label: "派活与交接", kind: "gap", gap: "未建" },
  ],
  l3: [
    { label: "Skills", kind: "page", href: "#/skills", signal: "skills" },
    { label: "MCP", kind: "page", href: "#/mcp", signal: "mcp" },
    { label: "Instructions", kind: "page", href: "#/agents?has_instructions=1", signal: "instructions" },
    { label: "Spec", kind: "gap", gap: "未建" },
  ],
  l2: [
    { label: "Agents", kind: "page", href: "#/agents", signal: "agents" },
    { label: "Daemon", kind: "page", href: "#/agents", signal: "daemon" },
  ],
  l1: [
    { label: "用量与额度", kind: "page", href: "#/usage", signal: "usage" },
    { label: "额度信号", kind: "page", href: "#/agents?failed=1", signal: "quota" },
  ],
};

export function isCancelled(issue) {
  const status = String(issue?.status || "").toLowerCase();
  const category = String(issue?.status_category || "").toLowerCase();
  return status === "cancelled" || status === "canceled" || category === "cancelled" || category === "canceled";
}

export function isDone(issue) {
  return String(issue?.status || "").toLowerCase() === "done" || String(issue?.status_category || "").toLowerCase() === "done";
}

export function isOpen(issue) {
  return !isCancelled(issue) && !isDone(issue);
}

export function isDecide(issue) {
  const status = String(issue?.status || "").toLowerCase();
  return status === "in_review" || status === "blocked";
}

function canonId(value) {
  const text = String(value || "").trim().toLowerCase();
  if (!text) return null;
  if (text === "outside" || text.startsWith("不在地图")) return "outside";
  const hit = /^l([1-5])\b/.exec(text);
  if (hit) return `l${hit[1]}`;
  return null;
}

/** Read the backend layer object. Missing object means the property is not on this payload. */
export function issueLayer(issue) {
  const raw = issue?.layer;
  if (!raw || typeof raw !== "object") return null;
  const primary = canonId(raw.primary);
  let involved = Array.isArray(raw.involved) ? raw.involved.map(canonId).filter((id) => LAYER_SET.has(id)) : [];
  if (primary === "outside") involved = [];
  involved = [...new Set(involved.filter((id) => id !== primary))];
  return {
    primary,
    involved,
    governance: Boolean(raw.governance),
    source: {
      primary: raw.source?.primary || null,
      involved: raw.source?.involved || null,
      governance: raw.source?.governance || null,
    },
  };
}

export function sourceLabel(value) {
  if (!value) return "未回填";
  if (value === "own") return "自设";
  const inherited = /^inherited:(.+)$/.exec(String(value));
  if (inherited) return `继承自 ${inherited[1]}`;
  return String(value);
}

export function layerLabel(id) {
  if (!id) return "未归层";
  if (id === "outside") return "不在地图";
  if (id === "unassigned") return "未归层";
  const layer = LAYER_BY_ID[id];
  return layer ? `${layer.short} ${layer.name}` : id;
}

function boxFromReport(raw, governanceOnly) {
  const open = governanceOnly ? raw.governance_open ?? 0 : raw.open ?? 0;
  const decide = governanceOnly ? raw.governance_decide ?? 0 : raw.decide ?? 0;
  const total = governanceOnly ? open : raw.total ?? 0;
  const from = new Set((raw.involved_from || []).map((code) => String(code).toUpperCase()));
  return {
    total,
    open,
    decide,
    done: Math.max(0, total - open),
    governanceOpen: raw.governance_open ?? 0,
    governanceDecide: raw.governance_decide ?? 0,
    involvedExtra: governanceOnly ? 0 : raw.involved_extra ?? 0,
    involvedPrimaries: LAYERS.map((layer) => layer.short).filter((short) => from.has(short)),
    decideItems: raw.decide_items || [],
  };
}

/** Map GET /api/layers onto the same boxes the map renders from fixtures. */
export function modelFromReport(report, { governanceOnly = false } = {}) {
  const byId = Object.fromEntries((report?.layers || []).map((raw) => [String(raw.id || "").toLowerCase(), raw]));
  const layers = LAYERS.map((layer) => ({ id: layer.id, ...boxFromReport(byId[layer.id] || {}, governanceOnly) }));
  const outside = boxFromReport(report?.outside || {}, governanceOnly);
  const unassigned = boxFromReport(report?.unassigned || {}, governanceOnly);
  const header = { total: 0, open: 0, decide: 0, done: 0, assigned: 0, unassigned: unassigned.total, outside: outside.total };
  if (!governanceOnly && report?.header) {
    header.total = report.header.total ?? 0;
    header.assigned = report.header.assigned ?? 0;
    header.open = report.header.open ?? 0;
    header.decide = report.header.decide ?? 0;
    header.done = Math.max(0, header.total - header.open);
  } else {
    for (const box of [...layers, outside, unassigned]) {
      header.total += box.total;
      header.open += box.open;
      header.decide += box.decide;
      header.done += box.done;
    }
    header.assigned = header.total - unassigned.total;
  }
  return { header, layers, outside, unassigned };
}

/** ready false, or nothing assigned yet, is a guide — not a grid of zeros. */
export function availabilityFromReport(report) {
  if (!report?.workspace) return "no-workspace";
  if (!report.ready) return "missing";
  const total = report.header?.total ?? 0;
  if (!total) return "no-issues";
  if (!(report.header?.assigned > 0)) return "unfilled";
  return "ready";
}

export function layerAvailability(issues, acmId) {
  if (!acmId) return "no-workspace";
  const rows = (issues || []).filter((issue) => issue.workspace_id === acmId && !isCancelled(issue));
  if (!rows.length) return "no-issues";
  const tagged = rows.filter((issue) => issue.layer && typeof issue.layer === "object");
  if (!tagged.length) return "missing";
  if (!tagged.some((issue) => issueLayer(issue)?.primary)) return "unfilled";
  return "ready";
}

export function matchesLayer(issue, layerId, match = "primary") {
  if (!layerId || layerId === "all") return true;
  const layer = issueLayer(issue);
  if (layerId === "unassigned") return !layer?.primary;
  if (layerId === "outside") return layer?.primary === "outside";
  if (!layer) return false;
  if (match === "involved") return layer.primary === layerId || layer.involved.includes(layerId);
  return layer.primary === layerId;
}

function tally(issue, box) {
  box.total += 1;
  if (isOpen(issue)) box.open += 1;
  if (isDone(issue)) box.done += 1;
  if (isDecide(issue)) box.decide += 1;
}

function blankBox() {
  return { total: 0, open: 0, decide: 0, done: 0 };
}

function acmRows(issues, acmId) {
  return (issues || []).filter((issue) => issue.workspace_id === acmId && !isCancelled(issue));
}

export function aggregateLayers(issues, acmId, { governanceOnly = false } = {}) {
  const rows = acmRows(issues, acmId);
  const layers = LAYERS.map((layer) => ({
    id: layer.id,
    ...blankBox(),
    governanceOpen: 0,
    governanceDecide: 0,
    involvedExtra: 0,
    involvedPrimaries: [],
  }));
  const byId = Object.fromEntries(layers.map((layer) => [layer.id, layer]));
  const outside = blankBox();
  const unassigned = blankBox();
  const extraByLayer = Object.fromEntries(LAYERS.map((layer) => [layer.id, new Map()]));

  for (const issue of rows) {
    const layer = issueLayer(issue);
    const primary = layer?.primary;
    if (layer?.governance && LAYER_SET.has(primary)) {
      if (isOpen(issue)) byId[primary].governanceOpen += 1;
      if (isDecide(issue)) byId[primary].governanceDecide += 1;
    }
    if (layer && LAYER_SET.has(primary)) {
      for (const other of layer.involved) {
        if (!LAYER_SET.has(other)) continue;
        extraByLayer[other].set(primary, (extraByLayer[other].get(primary) || 0) + 1);
      }
    }
    if (governanceOnly && !layer?.governance) continue;
    if (LAYER_SET.has(primary)) tally(issue, byId[primary]);
    else if (primary === "outside") tally(issue, outside);
    else tally(issue, unassigned);
  }

  for (const layer of layers) {
    const primaries = [...extraByLayer[layer.id].entries()]
      .filter(([, n]) => n > 0)
      .sort((a, b) => LAYERS.findIndex((item) => item.id === a[0]) - LAYERS.findIndex((item) => item.id === b[0]));
    if (!governanceOnly) {
      layer.involvedExtra = primaries.reduce((sum, [, n]) => sum + n, 0);
      layer.involvedPrimaries = primaries.map(([id]) => LAYER_BY_ID[id].short);
    } else {
      let extra = 0;
      const names = [];
      for (const issue of rows) {
        const parsed = issueLayer(issue);
        if (!parsed?.governance || !LAYER_SET.has(parsed.primary) || parsed.primary === layer.id) continue;
        if (!parsed.involved.includes(layer.id)) continue;
        extra += 1;
        if (!names.includes(LAYER_BY_ID[parsed.primary].short)) names.push(LAYER_BY_ID[parsed.primary].short);
      }
      layer.involvedExtra = extra;
      layer.involvedPrimaries = names;
    }
  }

  const header = blankBox();
  header.unassigned = unassigned.total;
  header.outside = outside.total;
  for (const box of [...layers, outside, unassigned]) {
    header.total += box.total;
    header.open += box.open;
    header.decide += box.decide;
    header.done += box.done;
  }
  header.assigned = header.total - unassigned.total;
  return { header, layers, outside, unassigned };
}

export function selectLayerIssues(issues, acmId, { layer = "all", scope = "primary", governanceOnly = false, segment = "open" } = {}) {
  return acmRows(issues, acmId).filter((issue) => {
    const parsed = issueLayer(issue);
    if (governanceOnly && !parsed?.governance) return false;
    if (layer === "all") {
      /* keep */
    } else if (layer === "unassigned") {
      if (parsed?.primary) return false;
    } else if (layer === "outside") {
      if (parsed?.primary !== "outside") return false;
    } else if (scope === "involved") {
      if (!matchesLayer(issue, layer, "involved")) return false;
    } else if (parsed?.primary !== layer) return false;
    if (segment === "decide" && !isDecide(issue)) return false;
    if (segment === "open" && !isOpen(issue)) return false;
    return true;
  });
}

export function previewDecide(issues, acmId, layerId, waitOf, { governanceOnly = false, limit = 3 } = {}) {
  return selectLayerIssues(issues, acmId, { layer: layerId, scope: "primary", governanceOnly, segment: "decide" })
    .map((issue) => ({ issue, wait: waitOf(issue) }))
    .sort((a, b) => {
      const am = a.wait?.ms;
      const bm = b.wait?.ms;
      if (am == null && bm == null) return String(a.issue.identifier).localeCompare(String(b.issue.identifier));
      if (am == null) return 1;
      if (bm == null) return -1;
      return bm - am;
    })
    .slice(0, limit);
}

/** Pages that already exist. Layer views must not mention roadmap gaps. */
export function railLinks(layerId) {
  const links = [];
  for (const tool of MAP_TOOLS[layerId] || []) {
    if (tool.kind !== "page") continue;
    links.push({ label: tool.label, href: tool.href, signal: tool.signal });
  }
  if (LAYER_SET.has(layerId)) {
    links.push({ label: "Issues 中本层的关注项", href: `#/issues?layer=${layerId}` });
    links.push({ label: "本层待决事项", href: `#/decide?layer=${layerId}` });
  }
  return links;
}

export function countRule(layer) {
  if (!layer?.primary) return "还没有主层，不计入任何一层。";
  if (layer.primary === "outside") return "主层是不在地图，不计入 L1–L5。";
  const name = LAYER_BY_ID[layer.primary]?.short || layer.primary;
  if (!layer.involved.length) return `只计入 ${name}。`;
  const others = layer.involved.map((id) => LAYER_BY_ID[id]?.short || id).join("、");
  return `只计入 ${name}。在 ${others} 打开「涉及本层」时也会列出。`;
}

export function layerCommands(issue) {
  const id = issue?.identifier || issue?.id || "";
  const layer = issueLayer(issue);
  const primary = layer?.primary ? OPTION_NAME[layer.primary] : "L3 能力";
  const involved = (layer?.involved || []).map((item) => OPTION_NAME[item]).filter(Boolean);
  const involvedValue = involved.length ? involved.join(",") : "L1 资源,L2 执行";
  return [
    `multica issue property set ${id} --name 主层 --value "${primary}"`,
    `multica issue property set ${id} --name 涉及层 --value "${involvedValue}"`,
    `multica issue property set ${id} --name 治理线 --value ${layer?.governance ? "true" : "false"}`,
  ];
}

export function groupByStatus(issues) {
  const groups = [
    { id: "decide", title: "待决事项", items: [] },
    { id: "progress", title: "进行中", items: [] },
    { id: "todo", title: "Todo", items: [] },
    { id: "backlog", title: "Backlog", items: [] },
    { id: "done", title: "已完成", items: [] },
    { id: "other", title: "其他", items: [] },
  ];
  const byId = Object.fromEntries(groups.map((group) => [group.id, group]));
  for (const issue of issues) {
    if (isDecide(issue)) byId.decide.items.push(issue);
    else if (issue.status === "in_progress") byId.progress.items.push(issue);
    else if (issue.status === "todo") byId.todo.items.push(issue);
    else if (issue.status === "backlog") byId.backlog.items.push(issue);
    else if (isDone(issue)) byId.done.items.push(issue);
    else byId.other.items.push(issue);
  }
  return groups.filter((group) => group.items.length);
}
