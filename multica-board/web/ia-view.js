/** Shared, read-only presentation contracts for the two Agent tasks. */
export function agentColumns(configure, planMode, hours = 24) {
  return [...(planMode ? ["选择"] : []), "Agent", ...(configure || planMode
    ? ["Runtime", "模型", "推理", "Tier", "Instructions 来源", "Skills 绑定", "MCP 绑定", "调整入口"]
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
