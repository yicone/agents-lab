/** Lane classification for the Issues board. No DOM. */

export const QUEUED_RUN = new Set(["queued", "dispatched", "waiting_local_directory"]);

export function classifyIssue(it, nowMs, staleMs) {
  const run = it.latest_run;
  const activeRun = run && (run.status === "running" || QUEUED_RUN.has(run.status));
  const tags = [];
  const failed = run?.status === "failed";
  const anomaly = Boolean(run?.delivery_anomaly);
  const blocked = it.status === "blocked";
  const review = it.status === "in_review";
  const idle = (it.status === "todo" || it.status === "in_progress") && !activeRun;
  const stale = idle && it.last_activity_at && nowMs - Date.parse(it.last_activity_at) > staleMs;
  const lane = anomaly ? "delivery_anomaly" : failed ? "failed" : blocked ? "blocked" : review ? "review" : stale ? "stale" : null;

  if (failed && lane !== "failed") tags.push(["bad", "运行失败"]);
  if (blocked && lane !== "blocked") tags.push(["warn", "受阻"]);
  if (review && lane !== "review") tags.push(["info", "待验收"]);
  if (activeRun) tags.push(["run", run.status === "running" ? "运行中" : "排队中"]);
  if (!anomaly && it.last_delivery_anomaly) tags.push(["muted", "曾交付异常"]);
  if (!failed && it.last_failed_run) tags.push(["muted", "曾失败·已恢复"]);
  if (it.due_date && Date.parse(`${it.due_date}T23:59:59Z`) < nowMs && it.status_category !== "done") tags.push(["bad", "逾期"]);
  if (it.priority === "urgent" || it.priority === "high") tags.push(["prio", it.priority === "urgent" ? "紧急" : "高优"]);
  return { lane, tags };
}

/** Collection gaps must never become an empty, healthy queue. Count is unfiltered. */
export function attentionSummary(meta, issues, nowMs, staleMs, expected = 11) {
  const workspaces = meta?.workspaces || [];
  const unknown = workspaces.filter(w => w.collect_state !== "ok" || !w.counts).map(w => w.name || w.slug);
  const missing = Math.max(0, expected - workspaces.length);
  const rows = (issues || []).map(it => classifyIssue(it, nowMs, staleMs)).filter(c => c.lane);
  const count = rows.length;
  const bad = rows.some(c => ["failed", "delivery_anomaly"].includes(c.lane));
  const incomplete = !meta || Boolean(meta.collector?.error) || missing > 0 || unknown.length > 0;
  return { count, unknown, missing, incomplete,
    tone: incomplete ? "unknown" : bad ? "bad" : count ? "warn" : "none",
    text: incomplete ? "?" : count ? String(count) : "",
    title: `跨 workspace ${incomplete ? "已知待处理" : "待处理"} ${count} 条${incomplete ? `（数量未知：${[...unknown, ...(meta?.collector?.error ? ["workspace 目录读取失败"] : []), ...(missing ? [`另 ${missing} 个 workspace 未采集`] : [])].join("、")}）` : ""}` };
}
