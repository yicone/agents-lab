/** Lane classification for the Issues board. No DOM. */

export const QUEUED_RUN = new Set(["queued", "dispatched", "waiting_local_directory"]);

export function classifyIssue(it, nowMs, staleMs) {
  const run = it.latest_run;
  const activeRun = run && (run.status === "running" || QUEUED_RUN.has(run.status));
  const tags = [];
  const failed = run?.status === "failed";
  const blocked = it.status === "blocked";
  const review = it.status === "in_review";
  const idle = (it.status === "todo" || it.status === "in_progress") && !activeRun;
  const stale = idle && it.last_activity_at && nowMs - Date.parse(it.last_activity_at) > staleMs;
  const lane = failed ? "failed" : blocked ? "blocked" : review ? "review" : stale ? "stale" : null;

  if (failed && lane !== "failed") tags.push(["bad", "运行失败"]);
  if (blocked && lane !== "blocked") tags.push(["warn", "受阻"]);
  if (review && lane !== "review") tags.push(["info", "待验收"]);
  if (activeRun) tags.push(["run", run.status === "running" ? "运行中" : "排队中"]);
  if (!failed && it.last_failed_run) tags.push(["muted", "曾失败·已恢复"]);
  if (it.due_date && Date.parse(`${it.due_date}T23:59:59Z`) < nowMs && it.status_category !== "done") tags.push(["bad", "逾期"]);
  if (it.priority === "urgent" || it.priority === "high") tags.push(["prio", it.priority === "urgent" ? "紧急" : "高优"]);
  return { lane, tags };
}
