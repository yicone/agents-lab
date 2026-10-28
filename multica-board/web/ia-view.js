/** Shared, read-only presentation contracts for the two Agent tasks. */

export const COPY = {
  section: "配置管理",
  agent: "Agent 配置",
  runtime: "运行配置",
  capability: "能力",
};

/** 来源名里的产品前缀不进页面。对象自己的名称不要走这里。 */
export function visibleLabel(value) {
  if (typeof value !== "string" || !value.includes("Multica")) return value;
  return value.replaceAll("Multica Workspace", "Workspace").replaceAll("Multica", "Workspace");
}

export function configCrumb(h, items) {
  const nodes = [h("a", { href: "#/configure" }, COPY.section)];
  for (const item of items) {
    nodes.push(" › ");
    nodes.push(item.href ? h("a", { href: item.href }, item.label) : item.label);
  }
  return h("p", { class: "crumb" }, ...nodes);
}

export function pageHead(h, items, title) {
  return [configCrumb(h, items), h("h2", null, title)];
}

export function agentColumns(configure, planMode, hours = 24) {
  return [...(planMode ? ["选择"] : []), "Agent", ...(configure || planMode
    ? ["Runtime", "模型", "推理", "Tier", "Instructions 来源", "Skills 绑定", "MCP 绑定", COPY.runtime]
    : ["运行或排队", "当前任务", `${hours}h 失败 / 完成`, "最近失败", "最近运行"]), "同一 Agent"];
}
export function recentRuns(issues, workspaces, route, matchesQuery) {
  const byId = new Map((workspaces || []).map(ws => [ws.id, ws]));
  return (issues || []).filter(it => it.latest_run && (!route.ws || byId.get(it.workspace_id)?.slug === route.ws)
    && matchesQuery(route.q, it.identifier, it.title, it.latest_run.agent_name))
    .sort((a, b) => runTimestamp(b.latest_run) - runTimestamp(a.latest_run));
}
export function runTimestamp(run) {
  return Date.parse(run.created_at || run.started_at || run.completed_at || "") || 0;
}
export function timeUTC8(iso) {
  return iso && Number.isFinite(Date.parse(iso)) ? new Date(iso).toLocaleString("zh-CN", {hour12:false, timeZone:"Asia/Singapore"}) : "未知时间";
}

/** Make the same-Agent focus visible and removable in both ordinary views. */
export function agentFocusControl(h, route, agents, onClear) {
  if (!route.sel || route.planMode || route.planPage) return null;
  const ids = route.sel.split(",").filter(Boolean);
  const names = ids.map(id => agents.find(agent => agent.id === id)?.name || id);
  return h("span", { class: "agent-focus", role: "group", "aria-label": "仅此 Agent" },
    `仅此 Agent：${names.join("、")}`,
    h("button", { type: "button", "aria-label": "清除仅此 Agent 筛选", onclick: onClear }, "显示全部 Agent"));
}
