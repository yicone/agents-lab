import { QUEUED_RUN, classifyIssue } from "./attention.js";
import {
  decideQueue,
  filterDecide,
  formatSince,
  formatWait,
  pickAcmLab,
  readStatusChanges,
  recentMotion,
} from "./decide.js";
import { aggregateLayers, availabilityFromReport, issueLayer, layerAvailability, layerLabel, matchesLayer, modelFromReport } from "./layers.js";
import { filterChrome, scopeNote, workspaceFilterBar } from "./filter-bar.js";
import { applyPatch, formatHash, historyWrite, locationHash, parseHash, redirectHash, stepLayer, viewForKey } from "./route.js";
import { badgeOf, renderSkillDrawer, renderSkills, skillsRefreshKey } from "./skills.js";
import { drawerCommands, drawerLayer, layerBar, renderLayer, renderMap } from "./view-atlas.js";

const MOCK_PARAM = new URLSearchParams(location.search).get("mock");
const MOCK = MOCK_PARAM !== null;
const MOCK_SCENARIO = MOCK && /^[a-z][a-z-]*$/.test(MOCK_PARAM) ? MOCK_PARAM : "";
const META_POLL_MS = 15_000;
const STALE_DAY_OPTIONS = [1, 3, 7, 14];
const QUOTA_KINDS = new Set(["quota", "rate_limit"]);
const CLOSED_CATEGORIES = new Set(["done", "cancelled", "canceled"]);
const LANE_LIMIT = 30;

const ERROR_KIND_LABEL = {
  quota: "额度",
  rate_limit: "限流",
  auth: "鉴权",
  network: "网络",
  timeout: "超时",
  model_unavailable: "模型不可用",
  daemon_restart: "daemon 重启",
  cancelled: "已取消",
  process: "进程失败",
  unknown: "未知错误",
};

const COLLECTOR_HINT = {
  auth: "CLI 认证失败。在你自己的终端确认 multica 已登录，且 --profile 指向正确的 profile。",
  process: "CLI 无法运行。确认 multica 已安装，或用 --multica-bin 指定路径。",
  network: "网络不通。检查网络或代理后，后端会在下一轮自动重试。",
  timeout: "CLI 调用超时。检查网络；也可以调大 --timeout。",
  rate_limit: "被限流，后端会在下一轮自动重试。",
  quota: "额度不足，后端会在下一轮自动重试。",
};
const COLLECTOR_HINT_DEFAULT = "查看运行后端的终端输出（加 -v 更详细）。";

const STATUS_LABEL = {
  backlog: "Backlog",
  todo: "Todo",
  in_progress: "进行中",
  in_review: "待验收",
  blocked: "受阻",
  done: "完成",
  cancelled: "取消",
};

const RUN_STATUS_LABEL = {
  queued: "排队",
  dispatched: "已派发",
  waiting_local_directory: "等待目录",
  running: "运行中",
  completed: "完成",
  failed: "失败",
  cancelled: "取消",
};

const LANES = [
  { id: "failed", title: "运行失败", hint: "最近一次 run 失败" },
  { id: "blocked", title: "受阻", hint: "status = blocked" },
  { id: "review", title: "待验收", hint: "status = in_review" },
  { id: "stale", title: "停滞", hint: "todo / 进行中，无活动 run，超过阈值无活动" },
];

const state = {
  meta: null,
  issues: [],
  agents: [],
  runtimes: [],
  recentWindowH: 24,
  fetchError: null,
  route: parseHash(location.hash),
  staleDays: Number(localStorage.getItem("mb.staleDays")) || 3,
  drawer: null,
  expandedLanes: new Set(),
  changes: null,
  changesNote: null,
  layerReport: null,
  layerError: null,
  skills: null,
  reviewDays: [30, 90, 180].includes(Number(localStorage.getItem("aa.skillReviewDays"))) ? Number(localStorage.getItem("aa.skillReviewDays")) : 90,
  expandedSkills: new Set(),
};

// ---------- data ----------

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function fetchJSON(url, label) {
  const res = await fetch(url, { cache: "no-store", headers: { Accept: "application/json" } });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      if (body?.error?.message) detail += ` ${body.error.message}`;
    } catch {}
    throw new HttpError(res.status, `${label}: ${detail}`);
  }
  return res.json();
}

let mockScenarios;

async function getJSON(name) {
  if (!MOCK) return fetchJSON(`/api/${name}`, name);
  if (MOCK_SCENARIO) {
    mockScenarios ??= fetchJSON("fixtures/scenarios.json", "scenarios");
    const overrides = (await mockScenarios)[MOCK_SCENARIO] ?? [];
    if (overrides.includes(name)) return fetchJSON(`fixtures/${MOCK_SCENARIO}/${name}.json`, name);
  }
  return fetchJSON(`fixtures/${name}.json`, name);
}

async function getRuns(it) {
  if (!MOCK) {
    const path = `/api/issues/${encodeURIComponent(it.workspace_id)}/${encodeURIComponent(it.id)}/runs`;
    return fetchJSON(path, "runs");
  }
  if (MOCK_SCENARIO) {
    try {
      const local = await fetchJSON(`fixtures/${MOCK_SCENARIO}/runs.json`, "runs");
      if (local.by_issue?.[it.key]) return { generated_at: local.generated_at, items: local.by_issue[it.key] };
    } catch {}
  }
  const all = await fetchJSON("fixtures/runs.json", "runs");
  const items = all.by_issue?.[it.key];
  if (!items) throw new HttpError(404, "runs: 404 issue not in cache");
  return { generated_at: all.generated_at, items };
}

async function loadSkills() {
  try {
    if (MOCK) return await getJSON("skills");
    return await fetchJSON(`/api/skills?review_days=${state.reviewDays}`, "skills");
  } catch (err) {
    return { ok: false, error: { code: "skills_unavailable", message: String(err.message || err) }, items: [], attention: { badge: 0, baseline_debt: 0, counts: {}, buckets: {} } };
  }
}

async function loadChanges() {
  try {
    if (MOCK) return await getJSON("changes");
    return await readStatusChanges((path) => fetchJSON(path, "changes"), Date.now());
  } catch (err) {
    return { error: String(err.message || err) };
  }
}

function applyChanges(payload) {
  if (!payload || payload.error) {
    state.changes = null;
    state.changesNote = payload?.error || "状态迁移不可用";
    return;
  }
  state.changes = payload;
  if (payload.reset) state.changesNote = "reset";
  else if (payload.incomplete) state.changesNote = "incomplete";
  else state.changesNote = null;
}

let changesToken = 0;

async function ensureChanges() {
  if (MOCK || !state.meta) return;
  const gen = state.meta.generated_at ?? "";
  if (state.changesLoadedFor === gen || state.changesLoadingFor === gen) return;
  state.changesLoadingFor = gen;
  const token = ++changesToken;
  state.changesNote = "loading";
  render();
  const payload = await loadChanges();
  if (token !== changesToken) return;
  applyChanges(payload);
  state.changesLoadedFor = gen;
  state.changesLoadingFor = null;
  render();
}

async function refresh(force = false) {
  try {
    const meta = await getJSON("meta");
    const changed = force || !state.meta || meta.generated_at !== state.meta.generated_at;
    const previousSkills = skillsRefreshKey(state.skills);
    // Skills live on this machine, so they reload even when the Multica snapshot is unchanged.
    const skillsPromise = loadSkills();
    if (changed) {
      const jobs = [getJSON("issues"), getJSON("agents"), getJSON("runtimes")];
      if (MOCK) jobs.push(loadChanges());
      else jobs.push(getJSON("layers").catch((err) => ({ error: String(err.message || err) })));
      const [issues, agents, runtimes, extra] = await Promise.all(jobs);
      state.issues = issues.items ?? [];
      state.agents = agents.items ?? [];
      state.recentWindowH = agents.recent_window_h ?? 24;
      state.runtimes = runtimes.items ?? [];
      if (MOCK) {
        applyChanges(extra);
        state.layerReport = null;
        state.layerError = null;
      } else {
        state.changes = null;
        state.changesNote = null;
        state.changesLoadedFor = null;
        state.changesLoadingFor = null;
        changesToken += 1;
        if (extra?.error) {
          state.layerReport = null;
          state.layerError = extra.error;
        } else {
          state.layerReport = extra;
          state.layerError = null;
        }
      }
    }
    state.skills = await skillsPromise;
    const skillsChanged = skillsRefreshKey(state.skills) !== previousSkills;
    state.meta = meta;
    state.fetchError = null;
    if (changed || skillsChanged) render();
    else renderChrome();
  } catch (err) {
    state.fetchError = { message: String(err.message || err), at: Date.now() };
    renderChrome();
    if (!state.meta) renderMain();
  }
}

function now() {
  if (MOCK && state.meta) {
    const t = Date.parse(state.meta.collector?.last_attempt_at ?? state.meta.generated_at ?? "");
    if (!Number.isNaN(t)) return t;
  }
  return Date.now();
}

// ---------- routing ----------

let adoptedHash = null;

function setRoute(patch) {
  const r = applyPatch(state.route, patch);
  const hash = formatHash(r);
  const action = historyWrite(location.hash, hash, patch);
  if (action === "push") history.pushState(null, "", hash);
  else if (action === "replace") history.replaceState(null, "", hash);
  state.route = r;
  adoptedHash = hash;
  render();
}

function follow(event, patch) {
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button) return;
  event.preventDefault();
  setRoute(patch);
}

function syncFromLocation() {
  const next = redirectHash(location.hash);
  if (next && next !== location.hash) {
    location.replace(locationHash(next));
    return;
  }
  const route = parseHash(location.hash);
  const hash = formatHash(route);
  state.route = route;
  if (hash === adoptedHash) return;
  adoptedHash = hash;
  render();
}

window.addEventListener("hashchange", syncFromLocation);
window.addEventListener("popstate", syncFromLocation);

// ---------- helpers ----------

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k === "style") for (const [p, val] of Object.entries(v)) el.style.setProperty(p, val);
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function extLink(href, attrs, ...children) {
  if (!href) return h("span", attrs, ...children);
  return h("a", { ...attrs, href, target: "_blank", rel: "noopener noreferrer" }, ...children);
}

function ago(iso) {
  if (!iso) return "—";
  const s = Math.max(0, (now() - Date.parse(iso)) / 1000);
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}

function absTime(iso) {
  return iso ? new Date(iso).toLocaleString("zh-CN", { hour12: false }) : "";
}

function duration(fromIso, toIso) {
  if (!fromIso || !toIso) return "";
  const s = Math.max(0, (Date.parse(toIso) - Date.parse(fromIso)) / 1000);
  if (s < 60) return `${Math.round(s)} 秒`;
  if (s < 3600) return `${Math.round(s / 60)} 分钟`;
  return `${(s / 3600).toFixed(1)} 小时`;
}

function hueOf(str) {
  let x = 0;
  for (const ch of str) x = (x * 31 + ch.codePointAt(0)) >>> 0;
  return `${x % 360}`;
}

function wsById() {
  return new Map((state.meta?.workspaces ?? []).map((w) => [w.id, w]));
}

function wsTag(ws) {
  if (!ws) return null;
  return h("span", { class: "ws-tag", style: { "--ws-hue": hueOf(ws.slug) }, title: ws.name }, ws.name);
}

/** With --refresh 0 nothing is collected, so age alone must not mark a workspace as stale. */
function wsState(ws) {
  return ws.collect_state === "stale" && collectInterval() === 0 ? "ok" : ws.collect_state;
}

function isWsDegraded(ws) {
  return ws && (wsState(ws) === "error" || wsState(ws) === "stale");
}

function matchesQuery(q, ...fields) {
  if (!q) return true;
  const needle = q.toLowerCase();
  return fields.some((f) => f && String(f).toLowerCase().includes(needle));
}

function errKind(kind) {
  if (!kind) return "";
  return ERROR_KIND_LABEL[kind] ?? ERROR_KIND_LABEL.unknown;
}

function errKindEl(kind, fallback = "失败") {
  return h("span", { class: `err-kind${QUOTA_KINDS.has(kind) ? " quota" : ""}`, title: kind || "" }, errKind(kind) || fallback);
}

function assigneeName(a) {
  if (!a) return "未分配";
  if (a.name) return a.name;
  return a.type === "squad" ? "Squad" : a.type === "member" ? "未知成员" : "未知 agent";
}

// ---------- health model ----------

function collectInterval() {
  return state.meta?.collect_interval_s ?? 60;
}

function allWorkspacesFailing() {
  const list = state.meta?.workspaces ?? [];
  return list.length > 0 && list.some((w) => w.collect_state === "error") && list.every((w) => w.collect_state === "error" || w.collect_state === "never");
}

/** One status for the snapshot pill: level is ok | warn | bad | idle. */
function health() {
  const m = state.meta;
  if (state.fetchError) {
    return { level: "bad", text: m ? `后端不可达 · 显示 ${ago(m.generated_at)}的数据` : "后端不可达", title: state.fetchError.message };
  }
  if (!m) return { level: "idle", text: "加载中…" };
  const interval = collectInterval();
  const at = m.generated_at;
  if (m.collector?.error) {
    return { level: "bad", text: at ? `采集失败 · 数据停在 ${ago(at)}` : "采集失败 · 尚无数据", title: absTime(at) };
  }
  if (!at) return { level: "idle", text: interval > 0 ? "尚未采集 · 首轮进行中" : "尚未采集" };
  if (allWorkspacesFailing()) return { level: "bad", text: `全部 workspace 采集失败 · ${ago(at)}`, title: absTime(at) };
  if (interval === 0) return { level: "idle", text: `只读缓存 · 快照 ${ago(at)}`, title: `--refresh 0：不采集，只读缓存\n${absTime(at)}` };
  const stale = (now() - Date.parse(at)) / 1000 > interval * 3;
  return { level: stale ? "warn" : "ok", text: `快照 ${ago(at)}${stale ? "（过期）" : ""}`, title: absTime(at) };
}

/** Global problem worth a banner, or null. Workspace-level errors stay on their chips. */
function bannerInfo() {
  const m = state.meta;
  if (!m || state.fetchError) return null;
  const c = m.collector;
  if (c?.error) {
    return {
      title: `采集失败：${errKind(c.error.kind)}`,
      message: c.error.message,
      hint: COLLECTOR_HINT[c.error.kind] ?? COLLECTOR_HINT_DEFAULT,
      facts: [
        `最近尝试 ${ago(c.last_attempt_at)}`,
        c.last_success_at ? `最近成功 ${ago(c.last_success_at)}` : "从未成功",
        m.generated_at ? `页面显示的是 ${ago(m.generated_at)}的旧数据` : null,
      ],
    };
  }
  if (allWorkspacesFailing()) {
    const first = m.workspaces.find((w) => w.error);
    return {
      title: "全部 workspace 本轮采集失败",
      message: first ? `${first.name}：${first.error.message}` : "",
      hint: COLLECTOR_HINT[first?.error?.kind] ?? COLLECTOR_HINT_DEFAULT,
      facts: ["各 workspace 的原因见下方标签（悬停查看详情）"],
    };
  }
  return null;
}

function skillsSnapshot() {
  const payload = state.skills;
  if (!payload) return { level: "idle", text: "Skills …", title: "" };
  if (payload.ok === false) return { level: "bad", text: "清单无效", title: payload.error?.message || "" };
  const meta = payload.meta || {};
  const sha = meta.inventory_sha ? ` ${meta.inventory_sha}` : "";
  return {
    level: "ok",
    text: `清单 ${ago(meta.inventory_updated_at) || "—"}${sha} · 扫描 ${ago(meta.drift_scanned_at) || "刚刚"}`,
    title: `清单 ${absTime(meta.inventory_updated_at) || "—"} · 扫描 ${absTime(meta.drift_scanned_at) || "—"}`,
  };
}

function skillSources() {
  const sources = state.skills?.meta?.sources || [];
  if (!sources.length) return [h("span", { class: "muted" }, state.skills?.ok === false ? "清单无效" : "数据源还没有返回")];
  return sources.map((source) =>
    h(
      "span",
      { class: `src-chip state-${source.state}`, title: source.detail || "" },
      h("span", { class: "dot" }),
      source.label,
      source.detail ? h("span", { class: "chip-detail" }, source.detail) : null,
    ),
  );
}

async function setReviewDays(days) {
  state.reviewDays = days;
  localStorage.setItem("aa.skillReviewDays", String(days));
  state.skills = await loadSkills();
  render();
}

function toggleSkillGroup(id) {
  if (state.expandedSkills.has(id)) state.expandedSkills.delete(id);
  else state.expandedSkills.add(id);
  render();
}

function openSkill(item, opener) {
  state.drawer = { kind: "skill", skill: item, opener: opener || document.activeElement };
  render();
  renderDrawer();
  document.querySelector("#drawer .drawer-close")?.focus();
}

function focusSkill(name, opener) {
  const item = (state.skills?.items || []).find((row) => row.name === name);
  if (!item) return;
  state.expandedSkills.add(item.source_class || "unknown");
  setRoute({ view: "skills", source: "", skillStatus: "", tool: "", custom: false, debt: false, q: "" });
  const row = document.getElementById(`skill-${name}`);
  if (row) row.scrollIntoView({ block: "center" });
  openSkill(item, opener || row);
}

// ---------- chrome (header) ----------

function workspaceChip(ws) {
  const active = state.route.ws === ws.slug;
  const st = wsState(ws);
  let detail = "";
  if (st === "never") detail = ws.error ? `未加载 · ${errKind(ws.error.kind)}` : "未加载";
  else if (st === "error") detail = `${errKind(ws.error?.kind) || "失败"} · ${ago(ws.last_success_at)}`;
  else if (st === "stale") detail = `过期 · ${ago(ws.last_success_at)}`;
  const title = [
    `${ws.name}（${ws.slug}）`,
    `最近成功：${absTime(ws.last_success_at) || "从未"}`,
    ws.error ? `错误：${ws.error.message}` : "",
    ws.counts ? `打开 issue ${ws.counts.open_issues} · agent ${ws.counts.agents} · runtime ${ws.counts.runtimes}` : "",
    active ? "点击取消筛选" : "点击只看此 workspace",
  ].filter(Boolean).join("\n");
  return h(
    "button",
    {
      type: "button",
      class: `ws-chip state-${st}${active ? " active" : ""}`,
      style: { "--ws-hue": hueOf(ws.slug) },
      title,
      "aria-pressed": active ? "true" : "false",
      onclick: () => setRoute({ ws: active ? "" : ws.slug }),
    },
    h("span", { class: "dot" }),
    ws.name,
    detail ? h("span", { class: "chip-detail" }, detail) : null,
  );
}

function renderChrome() {
  document.querySelectorAll(".tab").forEach((t) => {
    const active = t.dataset.view === state.route.view;
    t.classList.toggle("active", active);
    t.setAttribute("aria-current", active ? "page" : "false");
    t.href = formatHash(applyPatch(state.route, { view: t.dataset.view }));
  });
  const badge = document.getElementById("decide-badge");
  if (badge) {
    const total = state.meta ? currentDecide().summary.total : 0;
    badge.hidden = total === 0;
    badge.textContent = total ? String(total) : "";
    badge.title = total ? `ACM Lab 待验收和受阻 ${total} 条` : "";
  }
  const skillsBadge = document.getElementById("skills-badge");
  if (skillsBadge) {
    const total = badgeOf(state.skills);
    skillsBadge.hidden = total === 0;
    skillsBadge.textContent = total ? String(total) : "";
    skillsBadge.title = total ? `Skills 待处理 ${total} 条，基线债不计入` : "";
  }

  const hs = health();
  const snap = document.getElementById("snapshot");
  const skillsHead = state.route.view === "skills" ? skillsSnapshot() : null;
  snap.className = `snapshot ${skillsHead?.level || hs.level}`;
  snap.title = skillsHead?.title || hs.title || "";
  snap.replaceChildren(h("span", { class: "dot" }), skillsHead?.text || hs.text);
  if (MOCK) snap.append(h("span", { class: "mock" }, MOCK_SCENARIO ? `mock:${MOCK_SCENARIO}` : "mock"));

  const b = bannerInfo();
  const banner = document.getElementById("banner");
  banner.hidden = !b;
  if (b) {
    banner.replaceChildren(
      ...[
        h("div", { class: "banner-title" }, b.title),
        b.message ? h("code", { class: "banner-msg" }, b.message) : null,
        h("div", { class: "banner-hint" }, b.hint),
        h("div", { class: "banner-facts" }, b.facts.filter(Boolean).join(" · ")),
      ].filter(Boolean),
    );
  }

  const healthEl = document.getElementById("health");
  const chrome = filterChrome(state.route.view);
  healthEl.dataset.filterChrome = chrome;
  if (chrome === "skills") {
    healthEl.hidden = false;
    healthEl.setAttribute("aria-label", "Skills 清单范围");
    healthEl.replaceChildren(h("p", { class: "scope-note" }, scopeNote("skills")), ...skillSources());
  } else if (chrome === "decide") {
    healthEl.hidden = false;
    healthEl.setAttribute("aria-label", "待你决定范围");
    healthEl.replaceChildren(h("p", { class: "scope-note" }, scopeNote("decide")));
  } else if (chrome === "shared") {
    healthEl.hidden = false;
    healthEl.setAttribute("aria-label", "Workspace 筛选");
    const slug = state.route.ws;
    healthEl.replaceChildren(
      ...workspaceFilterBar(h, {
        workspaces: state.meta?.workspaces ?? [],
        activeSlug: slug,
        chip: workspaceChip,
        onClear: () => setRoute({ ws: "" }),
      }),
    );
  } else {
    healthEl.hidden = true;
    healthEl.replaceChildren();
  }
}

// ---------- issues view ----------

function acmWorkspace() {
  return pickAcmLab(state.meta?.workspaces ?? []) || state.layerReport?.workspace || null;
}

function layersReady() {
  if (MOCK) return layerAvailability(state.issues, acmWorkspace()?.id) === "ready";
  return availabilityFromReport(state.layerReport) === "ready";
}

function layerCounts() {
  if (!layersReady()) return null;
  if (MOCK) return aggregateLayers(state.issues, acmWorkspace().id);
  return modelFromReport(state.layerReport);
}

function issueCard(it, ws, cls) {
  const run = it.latest_run;
  const degraded = isWsDegraded(ws);
  const failedLine =
    run?.status === "failed"
      ? h("div", { class: "card-error", title: run.error_summary || "" }, errKindEl(run.error_kind), " ", run.error_summary || "")
      : null;
  const context = [it.project?.title, it.parent_identifier ? `↑ ${it.parent_identifier}` : null].filter(Boolean).join(" · ");

  const acm = acmWorkspace();
  const layer = issueLayer(it);
  const showBar = Boolean(acm && it.workspace_id === acm.id && layer);
  return h(
    "article",
    {
      class: `card${degraded ? " is-stale" : ""}`,
      tabindex: "0",
      onclick: (e) => {
        if (e.target.closest("a, button")) return;
        openDrawer(it, e.currentTarget);
      },
    },
    h(
      "div",
      { class: "card-head" },
      wsTag(ws),
      h("span", { class: "ident" }, it.identifier),
      h("span", { class: "status" }, STATUS_LABEL[it.status] ?? it.status),
      showBar ? layerBar(h, layer) : null,
    ),
    extLink(it.url, { class: "card-title", title: it.url ? "在 Multica 打开" : "无法生成链接" }, it.title),
    context ? h("div", { class: "card-context" }, context) : null,
    failedLine,
    h(
      "div",
      { class: "card-foot" },
      h("span", { class: "who" }, assigneeName(it.assignee)),
      run
        ? h("button", { type: "button", class: "runs-btn", title: "查看运行历史", onclick: (e) => openDrawer(it, e.currentTarget) }, "运行记录")
        : h("span", { class: "runs-none", title: "后端只缓存最近 14 天的 run" }, "14 天无 run"),
      h(
        "span",
        { class: "when", title: `最后活动 ${absTime(it.last_activity_at)}` },
        cls.lane === "failed" && run?.completed_at ? `失败于 ${ago(run.completed_at)}` : ago(it.last_activity_at),
      ),
    ),
    cls.tags.length || degraded
      ? h(
          "div",
          { class: "tags" },
          degraded ? h("span", { class: "tag warn" }, "旧数据") : null,
          cls.tags.map(([k, t]) => h("span", { class: `tag ${k}` }, t)),
        )
      : null,
  );
}

function laneBody(id, entries) {
  const expanded = state.expandedLanes.has(id);
  const shown = expanded ? entries : entries.slice(0, LANE_LIMIT);
  const rest = entries.length - shown.length;
  const toggle = (on) => () => {
    if (on) state.expandedLanes.add(id);
    else state.expandedLanes.delete(id);
    renderMain();
    if (!on) {
      const head = document.querySelector(`.lane-${id} .lane-head`);
      const offset = head ? head.getBoundingClientRect().top - document.querySelector(".topbar").offsetHeight - 8 : 0;
      if (offset < 0) window.scrollBy(0, offset);
    }
  };
  return h(
    "div",
    { class: "lane-body" },
    shown.map((e) => issueCard(e.it, e.ws, e.cls)),
    rest > 0
      ? h("button", { type: "button", class: "lane-more", onclick: toggle(true) }, `显示其余 ${rest} 条`)
      : expanded && entries.length > LANE_LIMIT
        ? h("button", { type: "button", class: "lane-more", onclick: toggle(false) }, `收起到 ${LANE_LIMIT} 条`)
        : null,
  );
}

function layerField(ready) {
  const pending = "层属性还没建或还没回填";
  const options = [
    ["", "全部"],
    ["l5", "L5 感知与决策"],
    ["l4", "L4 协作"],
    ["l3", "L3 能力"],
    ["l2", "L2 执行"],
    ["l1", "L1 资源"],
    ["outside", "不在地图"],
    ["unassigned", "未归层"],
  ];
  return h(
    "label",
    { class: `field${ready ? "" : " is-pending"}`, title: ready ? "按层筛选 ACM Lab" : pending },
    "层",
    h(
      "select",
      {
        disabled: !ready,
        onchange: (e) => setRoute({ layer: e.target.value }),
      },
      options.map(([value, label]) => h("option", { value, selected: state.route.layer === value }, label)),
    ),
  );
}

function matchField(ready) {
  const pending = "层属性还没建或还没回填";
  const btn = (match, label) =>
    h("button", {
      type: "button",
      disabled: !ready,
      class: state.route.match === match ? "active" : "",
      onclick: () => ready && setRoute({ match }),
    }, label);
  return h(
    "div",
    { class: `seg${ready ? "" : " is-pending"}`, role: "group", "aria-label": "层匹配", title: ready ? "按涉及层时，主层或涉及层命中都算" : pending },
    btn("primary", "按主层"),
    btn("involved", "按涉及层"),
  );
}

function renderIssues() {
  const wsMap = wsById();
  const { ws: wsSlug, q } = state.route;
  const staleMs = state.staleDays * 86400_000;
  const lanes = Object.fromEntries(LANES.map((l) => [l.id, []]));
  const others = new Map();

  const ready = layersReady();
  const layerOn = ready && state.route.layer;
  const acm = acmWorkspace();
  for (const it of state.issues) {
    const ws = wsMap.get(it.workspace_id);
    if (wsSlug && ws?.slug !== wsSlug) continue;
    if (layerOn) {
      if (it.workspace_id !== acm?.id) continue;
      if (!matchesLayer(it, state.route.layer, state.route.match)) continue;
    }
    if (!matchesQuery(q, it.identifier, it.title, it.assignee?.name, it.latest_run?.agent_name, ws?.name, it.project?.title, it.parent_identifier)) continue;
    const cls = classifyIssue(it, now(), staleMs);
    if (cls.lane) lanes[cls.lane].push({ it, ws, cls });
    else if (!CLOSED_CATEGORIES.has(it.status_category)) others.set(it.status, (others.get(it.status) ?? 0) + 1);
  }
  const othersTotal = [...others.values()].reduce((a, b) => a + b, 0);
  const othersDetail = [...others].sort((a, b) => b[1] - a[1]).map(([s, n]) => `${STATUS_LABEL[s] ?? s} ${n}`).join(" · ");

  const sortKey = (e) =>
    Date.parse((e.cls.lane === "failed" && e.it.latest_run?.completed_at) || e.it.last_activity_at || 0);
  for (const id in lanes) lanes[id].sort((a, b) => sortKey(b) - sortKey(a));
  lanes.stale.reverse();

  const toolbar = h(
    "div",
    { class: "toolbar" },
    h(
      "label",
      { class: "field" },
      "停滞阈值",
      h(
        "select",
        {
          onchange: (e) => {
            state.staleDays = Number(e.target.value);
            localStorage.setItem("mb.staleDays", String(state.staleDays));
            render();
          },
        },
        STALE_DAY_OPTIONS.map((d) => h("option", { value: d, selected: d === state.staleDays }, `${d} 天`)),
      ),
    ),
    h(
      "span",
      { class: "muted", title: "不在四栏里的打开 issue：有近期活动或有运行中的 run，以及 backlog" },
      othersTotal ? `其余 ${othersTotal} 条打开 issue 不在关注范围（${othersDetail}）` : "所有打开 issue 都在关注范围内",
    ),
    layerField(ready),
    matchField(ready),
  );

  const board = h(
    "div",
    { class: "lanes" },
    LANES.map((l) =>
      h(
        "section",
        { class: `lane lane-${l.id}`, "aria-label": l.title },
        h(
          "header",
          { class: "lane-head", title: l.hint },
          h("span", { class: "lane-title" }, l.title),
          h("span", { class: "count" }, lanes[l.id].length),
        ),
        lanes[l.id].length ? laneBody(l.id, lanes[l.id]) : h("div", { class: "empty" }, "无"),
      ),
    ),
  );

  return [toolbar, board];
}

// ---------- runs drawer ----------

async function openDrawer(it, opener) {
  const token = {};
  state.drawer = { it, token, loading: true, items: null, error: null, notFound: false, opener: opener || document.activeElement };
  renderDrawer();
  try {
    const res = await getRuns(it);
    if (state.drawer?.token !== token) return;
    state.drawer = { ...state.drawer, loading: false, items: res.items ?? [] };
  } catch (err) {
    if (state.drawer?.token !== token) return;
    state.drawer = { ...state.drawer, loading: false, notFound: err.status === 404, error: err.status === 404 ? null : String(err.message || err) };
  }
  renderDrawer();
}

function closeDrawer() {
  if (!state.drawer) return;
  const opener = state.drawer.opener;
  const skill = state.drawer.kind === "skill";
  state.drawer = null;
  renderDrawer();
  if (skill) renderMain();
  opener?.focus();
}

function runRow(r) {
  const cls = r.status === "failed" ? "bad" : r.status === "running" ? "run" : QUEUED_RUN.has(r.status) ? "muted" : r.status === "completed" ? "ok" : "muted";
  return h(
    "li",
    { class: "run-row" },
    h(
      "div",
      { class: "run-line" },
      h("span", { class: `tag ${cls}` }, RUN_STATUS_LABEL[r.status] ?? r.status),
      h("span", { class: "run-agent" }, r.agent_name ?? "未知 agent"),
      h("span", { class: "muted", title: `开始 ${absTime(r.started_at)}\n结束 ${absTime(r.completed_at)}` }, ago(r.completed_at ?? r.started_at)),
      r.started_at && r.completed_at ? h("span", { class: "muted" }, `· ${duration(r.started_at, r.completed_at)}`) : null,
    ),
    r.status === "failed" || r.error_kind
      ? h("div", { class: "run-error" }, errKindEl(r.error_kind), " ", r.error_summary || "")
      : null,
  );
}

function renderDrawer() {
  const el = document.getElementById("drawer");
  const d = state.drawer;
  el.hidden = !d;
  el.setAttribute("aria-label", d?.kind === "skill" ? "skill 详情" : "issue 详情");
  document.body.classList.toggle("drawer-open", !!d);
  if (!d) {
    el.replaceChildren();
    return;
  }
  if (d.kind === "skill") {
    el.replaceChildren(...renderSkillDrawer({ ...atlasCtx(), closeDrawer }, d.skill));
    return;
  }
  if (!d.opener) d.opener = document.activeElement;
  const ws = wsById().get(d.it.workspace_id);
  const layer = issueLayer(d.it);
  let body;
  if (d.loading) body = h("div", { class: "empty loading", "aria-busy": "true" }, "加载中…");
  else if (d.error) body = h("div", { class: "empty bad-n" }, `加载失败：${d.error}`);
  else if (d.notFound || !d.items.length) body = h("div", { class: "empty" }, "14 天内没有 run");
  else body = h("ol", { class: "runs" }, d.items.map(runRow));

  const close = h("button", { type: "button", class: "drawer-close", "aria-label": "关闭", onclick: closeDrawer }, "×");
  el.replaceChildren(
    ...[
      h(
        "header",
        { class: "drawer-head" },
        h(
          "div",
          { class: "card-head" },
          wsTag(ws),
          h("span", { class: "ident" }, d.it.identifier),
          h("span", { class: "status" }, STATUS_LABEL[d.it.status] ?? d.it.status),
          layer?.governance ? h("span", { class: "tag warn" }, "治理") : null,
        ),
        close,
      ),
      h("h2", { class: "drawer-title" }, d.it.title),
      d.it.url ? extLink(d.it.url, { class: "drawer-link" }, "在 Multica 打开 ↗") : null,
      h(
        "p",
        { class: "muted" },
        `${assigneeName(d.it.assignee)}${d.it.parent_identifier ? ` · 父 ${d.it.parent_identifier}` : ""} · 最后活动 ${ago(d.it.last_activity_at)}`,
      ),
      ...drawerLayer(atlasCtx(), d.it),
      h("div", { class: "drawer-sub" }, "运行记录（缓存只保留最近 14 天，最多 20 次）"),
      body,
      drawerCommands(atlasCtx(), d.it),
    ].filter(Boolean),
  );
  close.focus();
}

// ---------- agents view ----------

function runtimeIndex() {
  return new Map(state.runtimes.map((r) => [`${r.workspace_id}:${r.id}`, r]));
}

function agentRows() {
  const rtMap = runtimeIndex();
  const wsMap = wsById();
  return state.agents.map((a) => ({
    a,
    ws: wsMap.get(a.workspace_id),
    rt: a.runtime_id ? rtMap.get(`${a.workspace_id}:${a.runtime_id}`) ?? null : null,
  }));
}

function providerOf({ a, rt }) {
  return rt?.provider ?? (a.runtime_id ? "未知" : "未绑定");
}

function providerSummary(rows) {
  const byP = new Map();
  for (const row of rows) {
    const { a, ws, rt } = row;
    if (a.archived) continue;
    const p = providerOf(row);
    const s = byP.get(p) ?? { provider: p, agents: 0, ws: new Set(), rts: new Map(), failed: 0, quota: 0, running: 0 };
    s.agents += 1;
    if (ws) s.ws.add(ws.id);
    if (rt) s.rts.set(rt.key, rt.status === "online");
    s.failed += a.recent?.failed ?? 0;
    if (a.recent?.failed && QUOTA_KINDS.has(a.recent.last_error_kind)) s.quota += 1;
    s.running += a.tasks?.running ?? 0;
    byP.set(p, s);
  }
  return [...byP.values()].sort((x, y) => y.agents - x.agents);
}

function dimCell(v) {
  return v ? h("td", { class: "mono" }, v) : h("td", { class: "inherit", title: "未设置，继承 runtime 默认；实际值未知" }, "默认");
}

function agentRow({ a, ws, rt }, showWs) {
  const recent = a.recent ?? {};
  const degraded = isWsDegraded(ws);
  let rtCell;
  if (!a.runtime_id) rtCell = h("td", { class: "rt" }, h("span", { class: "tag warn" }, "未绑定"));
  else if (!rt) rtCell = h("td", { class: "rt muted" }, "未知 runtime");
  else
    rtCell = h(
      "td",
      { class: "rt", title: `${rt.device_info ?? ""}\n最后在线 ${absTime(rt.last_seen_at)}` },
      h("span", { class: `dot ${rt.status === "online" ? "ok" : "off"}` }),
      rt.name,
      rt.status !== "online" ? h("span", { class: "tag bad" }, `离线 ${ago(rt.last_seen_at)}`) : null,
    );

  const statusCell = h(
    "td",
    null,
    a.archived
      ? h("span", { class: "tag muted" }, "已归档")
      : h(
          "span",
          { class: `agent-status s-${a.status}` },
          a.status === "working" ? "工作中" : a.status === "idle" ? "空闲" : a.status,
        ),
    a.tasks?.running ? h("span", { class: "tag run" }, `运行 ${a.tasks.running}`) : null,
    a.tasks?.queued ? h("span", { class: "tag muted" }, `排队 ${a.tasks.queued}`) : null,
  );

  const failCell = recent.last_failed_at
    ? h(
        "td",
        { title: absTime(recent.last_failed_at) },
        errKindEl(recent.last_error_kind),
        " ",
        ago(recent.last_failed_at),
        recent.last_failed_issue_identifier ? h("span", { class: "mono muted" }, ` ${recent.last_failed_issue_identifier}`) : null,
      )
    : h("td", { class: "muted" }, "—");

  return h(
    "tr",
    { class: `${degraded ? "is-stale" : ""}${a.archived ? " is-archived" : ""}` },
    h(
      "td",
      { class: "agent-name" },
      showWs ? wsTag(ws) : null,
      a.url ? extLink(a.url, null, a.name) : a.name,
      degraded ? h("span", { class: "tag warn" }, "旧数据") : null,
    ),
    statusCell,
    rtCell,
    dimCell(a.model),
    dimCell(a.thinking_level),
    dimCell(a.service_tier),
    h(
      "td",
      { class: "num" },
      h("span", { class: "ok-n" }, `✓${recent.completed ?? 0}`),
      " ",
      h("span", { class: recent.failed ? "bad-n" : "muted" }, `✗${recent.failed ?? 0}`),
    ),
    failCell,
  );
}

function renderAgents() {
  const r = state.route;
  const all = agentRows();
  const scoped = all.filter(({ ws }) => !r.ws || ws?.slug === r.ws);
  const summary = providerSummary(scoped);

  const rows = scoped.filter((row) => {
    const { a, ws, rt } = row;
    if (!r.archived && a.archived) return false;
    if (r.provider && providerOf(row) !== r.provider) return false;
    if (r.failedOnly && !a.recent?.failed) return false;
    if (r.offlineOnly && a.runtime_id && rt?.status === "online") return false;
    return matchesQuery(r.q, a.name, a.model, a.thinking_level, a.service_tier, rt?.name, rt?.provider, ws?.name);
  });

  const strip = h(
    "div",
    { class: "providers" },
    summary.map((s) => {
      const active = r.provider === s.provider;
      const online = [...s.rts.values()].filter(Boolean).length;
      return h(
        "button",
        {
          type: "button",
          class: `provider${active ? " active" : ""}`,
          "aria-pressed": active ? "true" : "false",
          onclick: () => setRoute({ provider: active ? "" : s.provider }),
        },
        h("span", { class: "p-name" }, s.provider),
        h("span", { class: "p-main" }, `${s.agents} agent · ${s.ws.size} ws`),
        h(
          "span",
          { class: "p-sub" },
          h("span", { class: online < s.rts.size ? "bad-n" : "" }, `runtime ${online}/${s.rts.size} 在线`),
          " · ",
          h("span", { class: s.failed ? "bad-n" : "muted" }, `${state.recentWindowH}h 失败 ${s.failed}`),
          s.quota ? h("span", { class: "tag quota" }, `额度/限流 ${s.quota}`) : null,
        ),
      );
    }),
  );

  const toggle = (key, label) =>
    h(
      "label",
      { class: "check" },
      h("input", { type: "checkbox", checked: r[key], onchange: (e) => setRoute({ [key]: e.target.checked }) }),
      label,
    );

  const toolbar = h(
    "div",
    { class: "toolbar" },
    h(
      "div",
      { class: "seg", role: "group", "aria-label": "分组" },
      ["workspace", "provider"].map((g) =>
        h(
          "button",
          { type: "button", class: r.group === g ? "active" : "", "aria-pressed": r.group === g ? "true" : "false", onclick: () => setRoute({ group: g }) },
          g === "workspace" ? "按 workspace" : "按 provider",
        ),
      ),
    ),
    toggle("failedOnly", `只看 ${state.recentWindowH}h 有失败`),
    toggle("offlineOnly", "只看离线 / 未绑定"),
    toggle("archived", "显示已归档"),
    h("span", { class: "muted" }, `${rows.length} / ${all.length} agent`),
  );

  const groups = new Map();
  for (const row of rows) {
    const key = r.group === "provider" ? providerOf(row) : row.ws?.name ?? row.a.workspace_id;
    if (!groups.has(key)) groups.set(key, { key, ws: r.group === "workspace" ? row.ws : null, rows: [] });
    groups.get(key).rows.push(row);
  }
  for (const g of groups.values())
    g.rows.sort((x, y) => (y.a.recent?.failed ?? 0) - (x.a.recent?.failed ?? 0) || x.a.name.localeCompare(y.a.name));

  const head = h(
    "thead",
    null,
    h(
      "tr",
      null,
      ["Agent", "状态", "Runtime", "模型", "推理", "Tier", `${state.recentWindowH}h`, "最近失败"].map((c) => h("th", { scope: "col" }, c)),
    ),
  );
  const bodies = [...groups.values()].map((g) =>
    h(
      "tbody",
      null,
      h(
        "tr",
        { class: "group-row" },
        h(
          "th",
          { colspan: 8, scope: "rowgroup" },
          g.ws ? wsTag(g.ws) : h("span", { class: "p-name" }, g.key),
          h("span", { class: "muted" }, ` ${g.rows.length}`),
        ),
      ),
      g.rows.map((row) => agentRow(row, r.group === "provider")),
    ),
  );

  const table = rows.length
    ? h("div", { class: "table-wrap" }, h("table", { class: "matrix" }, head, bodies))
    : h("div", { class: "empty" }, "没有符合条件的 agent");

  return [strip, toolbar, table];
}

// ---------- render ----------

/** Full-page placeholder when there is nothing to show yet, or null. */
function emptyState() {
  const m = state.meta;
  if (!m) return state.fetchError ? `无法连接后端：${state.fetchError.message}` : "加载中…";
  if (m.workspaces?.length || m.generated_at) return null;
  if (m.collector?.error) return "还没有任何数据：采集没有成功过，原因见上方提示。";
  if (collectInterval() === 0) return "缓存为空：--refresh 0 只读缓存，不会采集。先运行一次 collect，或去掉 --refresh 0。";
  return "首轮采集进行中，完成后自动显示。";
}

function currentDecide() {
  return decideQueue({
    issues: state.issues,
    workspaces: state.meta?.workspaces ?? [],
    changes: state.changes?.items ?? [],
    nowMs: now(),
    historyIncomplete: state.changesNote != null,
  });
}

function decideNote() {
  if (state.changesNote === "loading") return "正在读取状态迁移。读完前，等待时长按最后活动估算，并标 ≥。";
  if (state.changesNote === "reset") return "状态迁移超出 7 天保留期，历史不完整。等待时长改按最后活动时间计算，并标 ≥。";
  if (state.changesNote === "incomplete") return "状态迁移日志太长，这次没有读完。等待时长改按最后活动时间计算，并标 ≥。";
  if (state.changesNote) return `状态迁移不可用（${state.changesNote}）。等待时长改按最后活动时间计算，并标 ≥。`;
  return null;
}

function renderDecide() {
  const model = currentDecide();
  const ready = layersReady();
  const visible = filterDecide(
    model.rows.map((row) => ({ ...row, assignee: assigneeName(row.issue.assignee) })),
    ready
      ? { type: state.route.type, query: state.route.q, layer: state.route.layer, match: state.route.match, gov: state.route.gov }
      : { type: state.route.type, query: state.route.q },
  );
  const { summary } = model;
  const longest = summary.longest;
  const note = decideNote();
  const degraded = isWsDegraded(model.acm);

  const stat = (n, label, level) =>
    h(
      "div",
      { class: `decide-stat${level ? ` ${level}` : ""}` },
      h("div", { class: "n" }, n),
      h("div", { class: "l" }, label),
    );

  const typeBtn = (type, label, count) =>
    h(
      "button",
      {
        type: "button",
        class: state.route.type === type ? "active" : "",
        "aria-pressed": state.route.type === type ? "true" : "false",
        onclick: () => setRoute({ type }),
      },
      `${label} ${count}`,
    );

  const pendingTitle = "层属性还没建或还没回填";
  const chipIds = [
    ["l5", "L5"],
    ["l4", "L4"],
    ["l3", "L3"],
    ["l2", "L2"],
    ["l1", "L1"],
    ["outside", "不在地图"],
    ["unassigned", "未归层"],
  ];
  const counts = ready ? layerCounts() : null;
  const chipCount = (id) => {
    if (!counts) return 0;
    if (id === "outside") return counts.outside.decide;
    if (id === "unassigned") return counts.unassigned.decide;
    return counts.layers.find((layer) => layer.id === id)?.decide ?? 0;
  };

  const head = h(
    "div",
    { class: "decide-head" },
    h(
      "div",
      null,
      h("h2", { class: "decide-title" }, "待你决定"),
      h(
        "p",
        { class: "decide-sub" },
        model.acm
          ? `范围：${model.acm.name}。待验收和受阻，等待最久的排在最前`
          : "范围：ACM Lab。快照里没有这个 workspace，所以这一页是空的。",
      ),
      h(
        "div",
        { class: "decide-stats", "data-decide-total": summary.total },
        stat(summary.total, "合计"),
        stat(summary.review, "待验收"),
        stat(summary.blocked, "受阻"),
        stat(summary.over7, "等待超过 7 天", summary.over7 ? "warn" : ""),
        stat(
          longest ? formatWait(longest.ms, longest.approximate) : "—",
          "最久",
          longest?.level === "bad" || longest?.level === "warn" ? longest.level : "",
        ),
      ),
    ),
    h(
      "div",
      { class: `decide-layers${ready ? " is-live" : ""}`, title: ready ? "点击按该层筛选" : pendingTitle },
      h("div", { class: "decide-layers-label" }, "按主层"),
      ...chipIds.map(([id, name]) =>
        h(
          "button",
          {
            type: "button",
            class: `decide-layer${state.route.layer === id ? " active" : ""}`,
            disabled: !ready,
            title: ready ? "" : pendingTitle,
            onclick: () => ready && setRoute({ layer: state.route.layer === id ? "" : id }),
          },
          ready ? `${name} ${chipCount(id)}` : name,
        ),
      ),
    ),
  );

  const toolbar = h(
    "div",
    { class: "toolbar decide-toolbar" },
    h("div", { class: "seg", role: "group", "aria-label": "类型" }, typeBtn("", "全部", summary.total), typeBtn("review", "待验收", summary.review), typeBtn("blocked", "受阻", summary.blocked)),
    layerField(ready),
    matchField(ready),
    h(
      "label",
      { class: `check${ready ? "" : " is-pending"}`, title: ready ? "" : pendingTitle },
      h("input", { type: "checkbox", disabled: !ready, checked: state.route.gov, onchange: (e) => setRoute({ gov: e.target.checked }) }),
      "只看治理线",
    ),
    h(
      "div",
      { class: "seg", role: "group", "aria-label": "分组" },
      h("button", { type: "button", class: state.route.group === "layer" ? "" : "active", "aria-pressed": state.route.group === "layer" ? "false" : "true", onclick: () => setRoute({ group: "" }) }, "不分组"),
      h("button", { type: "button", disabled: !ready, class: state.route.group === "layer" ? "active" : "", title: ready ? "" : pendingTitle, onclick: () => ready && setRoute({ group: "layer" }) }, "按主层"),
    ),
    h("span", { class: "muted decide-hint" }, "点行打开运行记录，点标题在 Multica 打开"),
  );

  const table = h(
    "div",
    { class: "decide-wrap" },
    h(
      "table",
      { class: "decide-table" },
      h(
        "thead",
        null,
        h(
          "tr",
          null,
          ...["已等待", "Issue", "标题", "类型", "层", "负责人", "最近动静", "线"].map((name) => h("th", null, name)),
        ),
      ),
      h(
        "tbody",
        { "data-decide-rows": visible.length },
        visible.length
          ? decideRows(visible, degraded, ready)
          : h(
              "tr",
              null,
              h(
                "td",
                { colspan: "8", class: "empty" },
                model.acm
                  ? state.route.q || state.route.type || state.route.layer || state.route.gov
                    ? "没有符合筛选的 issue"
                    : "ACM Lab 现在没有待验收或受阻的 issue"
                  : "快照里没有 ACM Lab",
              ),
            ),
      ),
    ),
  );

  const foot = h(
    "div",
    { class: "decide-foot" },
    h("span", null, "已等待 = 进入当前状态至今；超过 14 天标红，超过 7 天标黄。≥ 表示超出状态迁移保留期，改用最后活动时间。"),
    h(
      "span",
      null,
      model.outside
        ? `其他 workspace 还有 ${model.outside} 条待验收 / 受阻，不在这一页。`
        : "其他 workspace 没有待验收或受阻。",
      " ",
      h("a", { href: "#/issues" }, "在 Issues 中查看"),
    ),
  );

  return [
    head,
    note ? h("div", { class: "decide-note", role: "status" }, note) : null,
    degraded ? h("div", { class: "decide-note" }, "ACM Lab 这次快照是旧数据。") : null,
    toolbar,
    table,
    foot,
  ].filter(Boolean);
}

function decideRows(rows, degraded, ready) {
  if (!ready || state.route.group !== "layer") return rows.map((row) => decideRow(row, degraded, ready));
  const order = ["l5", "l4", "l3", "l2", "l1", "outside", "unassigned"];
  const out = [];
  for (const id of order) {
    const group = rows.filter((row) => (issueLayer(row.issue)?.primary || "unassigned") === id);
    if (!group.length) continue;
    out.push(h("tr", { class: "decide-group" }, h("th", { colspan: "8" }, layerLabel(id))));
    out.push(...group.map((row) => decideRow(row, degraded, ready)));
  }
  return out;
}

function decideRow(row, degraded, ready) {
  const it = row.issue;
  const wait = row.wait;
  const motion = recentMotion(it);
  const typeClass = it.status === "blocked" ? "warn" : "info";
  const layer = issueLayer(it);
  return h(
    "tr",
    {
      class: `decide-row${degraded ? " is-stale" : ""}`,
      tabindex: "0",
      "data-level": wait.level,
      "data-approx": wait.approximate ? "1" : "0",
      onclick: (e) => {
        if (e.target.closest("a")) return;
        openDrawer(it, e.currentTarget);
      },
    },
    h(
      "td",
      { class: "decide-wait" },
      h("span", { class: `wait-main ${wait.level}` }, formatWait(wait.ms, wait.approximate)),
      h("span", { class: "wait-since" }, formatSince(wait.iso, now())),
    ),
    h("td", { class: "ident" }, it.identifier || it.id),
    h("td", null, extLink(it.url, { class: "decide-title", title: it.url ? "在 Multica 打开" : "无法生成链接" }, it.title || "")),
    h("td", null, h("span", { class: `tag ${typeClass}` }, STATUS_LABEL[it.status] ?? it.status)),
    ready
      ? h("td", null, layerBar(h, layer), " ", layerLabel(layer?.primary))
      : h("td", { class: "decide-pending", title: "层属性还没建" }, h("span", { class: "layer-bar is-placeholder", "aria-hidden": "true" }), "未建"),
    h("td", null, row.assignee),
    h(
      "td",
      { class: "decide-motion", title: motion.at ? absTime(motion.at) : "" },
      h("span", null, motion.label),
      h("span", { class: "muted" }, motion.at ? ` · ${ago(motion.at)}` : ""),
    ),
    ready
      ? h("td", null, layer?.governance ? h("span", { class: "tag warn" }, "治理") : "—")
      : h("td", { class: "decide-pending", title: "层属性还没建" }, "—"),
  );
}

function skillAlert() {
  const counts = state.skills?.attention?.counts || {};
  return (counts.missing || 0) + (counts.modified || 0) + (counts.version_changed || 0) > 0;
}

function toolSignal(signal) {
  if (signal === "issues") {
    const summary = currentDecide().summary;
    return `待验收 ${summary.review} · 受阻 ${summary.blocked}`;
  }
  if (signal === "workspaces") {
    const list = state.meta?.workspaces ?? [];
    const bad = list.find((ws) => ws.collect_state === "error" || ws.collect_state === "stale");
    const detail = bad ? ` · ${bad.name} ${bad.collect_state === "error" ? errKind(bad.error?.kind) || "失败" : "过期"}` : "";
    return `${list.length} 个${detail}`;
  }
  if (signal === "skills") {
    const payload = state.skills;
    if (!payload || payload.ok === false) return "清单还没接上";
    const count = payload.items?.length ?? 0;
    const badge = badgeOf(payload);
    const debt = payload.attention?.baseline_debt ?? 0;
    return `${count} 条 · 待处理 ${badge} · 基线债 ${debt}`;
  }
  if (signal === "agents") return `${state.agents.filter((agent) => !agent.archived).length} 个 agent`;
  if (signal === "daemon") {
    const online = state.runtimes.filter((rt) => rt.status === "online").length;
    return online ? `${online} 个在线` : "没有在线 runtime";
  }
  if (signal === "quota") {
    const n = state.agents.filter((agent) => QUOTA_KINDS.has(agent.recent?.last_error_kind)).length;
    return n ? `${n} 个最近额度或限流失败` : "最近没有额度失败";
  }
  return "";
}

function atlasCtx() {
  return {
    h,
    extLink,
    setRoute,
    follow,
    openDrawer,
    route: state.route,
    issues: state.issues,
    meta: state.meta,
    agents: state.agents,
    runtimes: state.runtimes,
    acm: acmWorkspace(),
    nowMs: now(),
    changes: state.changes?.items ?? [],
    historyIncomplete: state.changesNote != null,
    report: MOCK ? null : state.layerReport,
    countsFromApi: !MOCK,
    assigneeName,
    ago,
    absTime,
    matchesQuery,
    statusLabel: (status) => STATUS_LABEL[status] ?? status,
    toolSignal,
    skillsAlert: skillAlert(),
    skills: state.skills,
    reviewDays: state.reviewDays,
    expandedSkills: state.expandedSkills,
    drawerSkill: state.drawer?.kind === "skill" ? state.drawer.skill?.name : "",
    openSkill,
    focusSkill,
    toggleSkillGroup,
    setReviewDays,
    closeDrawer,
  };
}

function renderMain() {
  const main = document.getElementById("main");
  const empty = emptyState();
  if (empty) {
    main.replaceChildren(h("div", { class: "empty big" }, empty));
    return;
  }
  const view = state.route.view;
  const ctx = atlasCtx();
  const parts =
    view === "agents"
      ? renderAgents()
      : view === "decide"
        ? renderDecide()
        : view === "skills"
          ? renderSkills(ctx)
          : view === "map" && state.route.layer
            ? renderLayer(ctx)
            : view === "map"
              ? renderMap(ctx)
              : renderIssues();
  main.replaceChildren(...parts);
}

function render() {
  const search = document.getElementById("search");
  if (document.activeElement !== search && search.value !== state.route.q) search.value = state.route.q;
  const view = state.route.view;
  search.placeholder =
    view === "decide" ? "搜索待决定的 issue" : view === "map" && state.route.layer ? "搜索本层 issue" : view === "skills" ? "搜索 skill" : "搜索 identifier / 标题 / agent / 模型";
  renderChrome();
  renderMain();
  if (!MOCK && (view === "decide" || view === "map")) void ensureChanges();
}

// ---------- boot ----------

const search = document.getElementById("search");
let searchTimer;
search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => setRoute({ q: search.value.trim() }), 120);
});

document.addEventListener("keydown", (e) => {
  const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement;
  if (e.key === "Escape") {
    if (state.drawer) {
      closeDrawer();
      return;
    }
    if (e.target === search) {
      search.value = "";
      setRoute({ q: "" });
      search.blur();
    }
    return;
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "/") {
    e.preventDefault();
    search.focus();
    search.select();
  } else if (viewForKey(e.key)) setRoute({ view: viewForKey(e.key) });
  else if ((e.key === "[" || e.key === "]") && state.route.view === "map" && /^l[1-5]$/.test(state.route.layer)) {
    const layer = stepLayer(state.route.layer, e.key === "]" ? "next" : "prev");
    if (layer) setRoute({ layer });
  }
});

document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", (e) => {
    e.preventDefault();
    setRoute({ view: t.dataset.view });
  }),
);

syncFromLocation();
refresh(true);
if (!MOCK) setInterval(refresh, META_POLL_MS);
setInterval(renderChrome, 5_000);
