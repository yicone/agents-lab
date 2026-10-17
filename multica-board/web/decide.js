/** Pure helpers for the「待你决定」queue. No DOM, so node:test can import them. */

export const DAY_MS = 86_400_000;
export const WARN_AFTER_MS = 7 * DAY_MS;
export const BAD_AFTER_MS = 14 * DAY_MS;

const SHANGHAI = "Asia/Shanghai";

function statusOf(issue) {
  return String(issue?.status || "").toLowerCase();
}

export function isDecideStatus(issue) {
  const status = statusOf(issue);
  return status === "in_review" || status === "blocked";
}

/** ACM Lab is the only workspace this page lists. Slug wins over display name. */
export function pickAcmLab(workspaces) {
  const list = workspaces ?? [];
  return (
    list.find((w) => String(w?.slug || "").toLowerCase() === "acm-lab") ||
    list.find((w) => String(w?.name || "").trim().toLowerCase() === "acm lab") ||
    null
  );
}

export function waitLevel(ms) {
  if (ms == null) return "none";
  if (ms > BAD_AFTER_MS) return "bad";
  if (ms > WARN_AFTER_MS) return "warn";
  return "ok";
}

function eventAt(ev) {
  const ms = Date.parse(ev?.at ?? "");
  return Number.isNaN(ms) ? null : ms;
}

/** Latest status transition into the issue's current status, or null if the log has none. */
export function latestStatusEntry(issue, changes) {
  const want = statusOf(issue);
  if (!want || !issue?.id) return null;
  let best = null;
  for (const ev of changes ?? []) {
    if (ev?.kind !== "issue_status") continue;
    if (ev.workspace_id !== issue.workspace_id || ev.id !== issue.id) continue;
    if (statusOf(ev) !== want) continue;
    if (String(ev.previous_status || "").toLowerCase() === want) continue;
    const at = eventAt(ev);
    if (at == null) continue;
    if (!best || at >= best.at) best = { at, iso: ev.at };
  }
  return best;
}

function fallbackIso(issue) {
  return issue?.last_activity_at || issue?.updated_at || issue?.created_at || null;
}

export function waitFor(issue, changes, nowMs, historyIncomplete = false) {
  const entry = historyIncomplete ? null : latestStatusEntry(issue, changes);
  const iso = entry?.iso ?? fallbackIso(issue);
  const start = iso ? Date.parse(iso) : NaN;
  const ms = Number.isNaN(start) ? null : Math.max(0, nowMs - start);
  return { ms, iso, approximate: !entry, level: waitLevel(ms) };
}

function identKey(issue) {
  return String(issue?.identifier || issue?.id || "");
}

export function compareWait(a, b) {
  const am = a.wait?.ms;
  const bm = b.wait?.ms;
  if (am == null && bm == null) return identKey(a.issue).localeCompare(identKey(b.issue));
  if (am == null) return 1;
  if (bm == null) return -1;
  if (am !== bm) return bm - am;
  return identKey(a.issue).localeCompare(identKey(b.issue));
}

function summarize(rows) {
  let review = 0;
  let blocked = 0;
  let over7 = 0;
  for (const row of rows) {
    const status = statusOf(row.issue);
    if (status === "in_review") review += 1;
    else if (status === "blocked") blocked += 1;
    if (row.wait.level === "warn" || row.wait.level === "bad") over7 += 1;
  }
  return {
    total: rows.length,
    review,
    blocked,
    over7,
    longest: rows.find((row) => row.wait.ms != null)?.wait ?? null,
  };
}

export function decideQueue({ issues, workspaces, changes, nowMs, historyIncomplete = false }) {
  const acm = pickAcmLab(workspaces);
  const rows = [];
  let outside = 0;
  for (const issue of issues ?? []) {
    if (!isDecideStatus(issue)) continue;
    if (acm && issue.workspace_id === acm.id) {
      rows.push({ issue, wait: waitFor(issue, changes, nowMs, historyIncomplete) });
    } else {
      outside += 1;
    }
  }
  rows.sort(compareWait);
  return {
    acm,
    rows,
    outside,
    summary: summarize(rows),
    historyIncomplete: Boolean(historyIncomplete),
  };
}

export function filterDecide(rows, { type = "", query = "" } = {}) {
  const q = String(query || "").trim().toLowerCase();
  return rows.filter((row) => {
    const status = statusOf(row.issue);
    if (type === "review" && status !== "in_review") return false;
    if (type === "blocked" && status !== "blocked") return false;
    if (!q) return true;
    const blob = [row.issue?.identifier, row.issue?.title, row.assignee, row.issue?.status]
      .filter(Boolean)
      .join("\n")
      .toLowerCase();
    return blob.includes(q);
  });
}

export function formatWait(ms, approximate = false) {
  if (ms == null) return "—";
  const prefix = approximate ? "≥" : "";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${prefix}刚刚`;
  if (s < 3600) return `${prefix}${Math.floor(s / 60)} 分钟`;
  if (s < DAY_MS / 1000) return `${prefix}${Math.floor(s / 3600)} 小时`;
  return `${prefix}${Math.floor(s / 86400)} 天`;
}

function shanghaiParts(ms) {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: SHANGHAI,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const map = {};
  for (const part of fmt.formatToParts(new Date(ms))) map[part.type] = part.value;
  if (map.hour === "24") map.hour = "00";
  return map;
}

export function formatSince(iso, nowMs) {
  if (!iso) return "";
  const start = Date.parse(iso);
  if (Number.isNaN(start)) return "";
  const a = shanghaiParts(start);
  const b = shanghaiParts(nowMs);
  if (a.year === b.year && a.month === b.month && a.day === b.day) return `今天 ${a.hour}:${a.minute} 起`;
  return `${a.month}-${a.day} 起`;
}

const RUN_MOTION = {
  completed: "运行完成",
  failed: "运行失败",
  running: "运行中",
  queued: "排队",
  dispatched: "排队",
  waiting_local_directory: "排队",
  cancelled: "运行取消",
};

export function recentMotion(issue) {
  const run = issue?.latest_run;
  const label = run?.status ? RUN_MOTION[run.status] : null;
  if (label) return { label, at: run.completed_at || run.started_at || issue.last_activity_at || null };
  return { label: "最后活动", at: issue?.last_activity_at || null };
}

/** Stay one minute inside the server retention horizon so a small clock skew does not force `reset`. */
export function changesSince(nowMs, retainedDays = 7) {
  const ms = nowMs - retainedDays * DAY_MS + 60_000;
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** Status transitions only. `kind=status` is the alias for stored `issue_status`. */
export function changesPath(since) {
  return `/api/changes?${new URLSearchParams({ since, kind: "status" })}`;
}

const CHANGE_PAGE_CAP = 16;

/**
 * Read the status-change window. Run events are not part of this request, so a
 * full page of runs can no longer hide the transition into the current status.
 * An unfinished read is still not a partial answer.
 */
export async function readStatusChanges(fetchPage, nowMs) {
  let cursor = changesSince(nowMs);
  const items = [];
  for (let page = 0; page < CHANGE_PAGE_CAP; page++) {
    const data = await fetchPage(changesPath(cursor));
    if (data.reset) return { ...data, items: [], incomplete: false };
    for (const item of data.items ?? []) {
      if (item.kind === "issue_status") items.push(item);
    }
    if (!data.has_more) return { ...data, items, incomplete: false };
    if (!data.cursor || data.cursor === cursor) return { ...data, items, incomplete: true };
    cursor = data.cursor;
  }
  return { items, reset: false, retained_days: 7, incomplete: true };
}
