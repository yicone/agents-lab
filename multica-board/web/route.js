/** Hash routes for Agent Atlas. Legacy hashes redirect with location.replace. */

export const VIEWS = ["map", "decide", "issues", "agents", "skills", "mcp", "usage", "configure", "runs", "schedules", "health", "settings"];

const LAYER_IDS = ["l1", "l2", "l3", "l4", "l5"];
const MAP_LAYERS = new Set([...LAYER_IDS, "all", "outside", "unassigned"]);
const FILTER_LAYERS = new Set([...LAYER_IDS, "outside", "unassigned"]);
const SKILL_SOURCES = new Set(["skills_cli_managed", "local_custom", "runtime_plugin", "runtime_builtin", "third_party_unmanaged", "unrecorded", "multica_cloud", "cloud", "grok_bot"]);
const EVIDENCE_SOURCES = new Set(["local", "repository", "multica_workspace", "grok_bot", "grok_cli", "memory"]);
const SKILL_STATUSES = new Set(["aligned", "stale", "version_changed", "modified", "unrecorded", "unverified", "missing"]);
// Before the first payload, retain a bounded provider token from a deep link.
// Once loaded, the API's column data is the only tool whitelist.
let skillTools = null;
export function setSkillTools(tools) {
  skillTools = new Set((tools || []).map((tool) => tool.id));
}
const PLAN_BUCKETS = new Set(["approvable", "blocked", "skip", "unchanged"]);
const MCP_SOURCES = new Set(["both", "local_only", "multica_only", "project"]);
const MCP_STATUSES = new Set(["broken", "differs", "same", "single", "incomparable"]);
const MCP_GROUPS = new Set(["source", "tool", "flat"]);
let mcpTools = null;
export function setMcpTools(tools) {
  mcpTools = new Set([...(tools || []).map((tool) => tool.id), "repo"]);
}

function agentsPlan(path) {
  if (path === "agents/plan/new") return { planPage: "new", planId: "" };
  if (path === "agents/plans") return { planPage: "list", planId: "" };
  const review = /^agents\/plans\/([^/]+)$/.exec(path);
  if (review) return { planPage: "review", planId: decodeURIComponent(review[1]) };
  return null;
}

function fieldToken(raw) {
  if (raw === "clear" || raw === "conflict") return raw;
  if (typeof raw === "string" && raw.startsWith("set:")) return raw;
  return "omit";
}

export function viewForKey(key) {
  return { 1: "issues", 2: "agents", 3: "configure", 4: "map" }[key] || null;
}

/** `[` moves up the map (toward L5); `]` moves down (toward L1). Wraps L1↔L5. */
export function stepLayer(layer, dir) {
  const order = ["l5", "l4", "l3", "l2", "l1"];
  const i = order.indexOf(layer);
  if (i < 0) return null;
  const j = dir === "next" ? (i + 1) % order.length : (i - 1 + order.length) % order.length;
  return order[j];
}

function splitHash(hash) {
  const raw = String(hash || "").replace(/^#/, "");
  const [pathPart, query = ""] = raw.split("?");
  const path = pathPart.replace(/^\/+|\/+$/g, "");
  return { path, params: new URLSearchParams(query) };
}

function withQuery(path, params) {
  const qs = params.toString();
  return `#/${path}${qs ? `?${qs}` : ""}`;
}

/**
 * Return the canonical hash when `hash` is a legacy or empty address.
 * Return null when the address is already a current route (query preserved).
 */
export function redirectHash(hash) {
  const { path, params } = splitHash(hash);
  const aliases = { configure: "configure/agents", agents: "running", usage: "running/usage", skills: "configure/skills", mcp: "configure/mcp",
    "l2/agents": "running", "l3/skills": "configure/skills", "l3/mcp": "configure/mcp" };
  if (Object.hasOwn(aliases, path)) return withQuery(aliases[path], params);
  if (agentsPlan(path)) return withQuery(path === "agents/plan/new" ? "running/changes/new" : path.replace("agents/plans", "running/changes"), params);
  if (path === "running/changes" || path === "running/changes/new" || /^running\/changes\/[^/]+$/.test(path)) return null;
  if (["running", "running/agents", "running/usage", "running/runs", "running/schedules", "running/health", "configure", "configure/agents", "configure/skills", "configure/skills/evidence", "configure/mcp", "configure/runtime", "settings"].includes(path)) return null;
  if (path === "issues" || path === "decide") return null;
  if (path === "map") return null;
  if (path.startsWith("map/")) {
    const rest = path.slice(4);
    if (rest === "all" || rest === "outside" || rest === "unassigned" || /^l[1-5]$/.test(rest)) return null;
    return "#/map";
  }
  if (path === "l5/attention") return "#/issues";
  if (path === "gov") return "#/map/all?gov=1";

  const legacy = /^l([1-5])(?:\/(.*))?$/i.exec(path);
  if (legacy) {
    const next = new URLSearchParams();
    const scope = params.get("scope");
    if (scope === "primary" || scope === "involved") next.set("scope", scope);
    return withQuery(`map/l${legacy[1]}`, next);
  }
  return "#/map";
}

function flag(params, key) {
  return params.get(key) === "1";
}

function pick(value, allowed, fallback) {
  return allowed.has(value) ? value : fallback;
}

export function emptyRoute() {
  return {
    view: "map",
    section: "",
    layer: "",
    scope: "primary",
    match: "primary",
    gov: false,
    openOnly: false,
    seg: "open",
    type: "",
    group: "workspace",
    ws: "",
    q: "",
    provider: "",
    failedOnly: false,
    offlineOnly: false,
    archived: false,
    source: "",
    skillStatus: "",
    tool: "",
    custom: false,
    debt: false,
    skillGroup: "source",
    evidence: false,
    kind: "",
    mcpStatus: "",
    mcpGroup: "source",
    mcpAssigned: false,
    mcpTidy: false,
    hasInstructions: false,
    botLinked: false,
    planMode: false,
    planPage: "",
    planId: "",
    fromPlan: "",
    sel: "",
    rt: "",
    model: "omit",
    effort: "omit",
    tier: "omit",
    bucket: "",
    planFix: "",
  };
}

export function parseHash(hash) {
  const redirected = redirectHash(hash);
  const source = redirected || (hash ? hash : "#/map");
  let { path, params } = splitHash(source.startsWith("#") ? source : `#${source}`);
  const route = emptyRoute();
  route.ws = params.get("ws") || "";
  route.q = params.get("q") || "";

  if (path.startsWith("configure/")) route.section = "configure";
  const evidence = path === "configure/skills/evidence";
  if (evidence) path = "configure/skills";
  const currentPaths = { running: "agents", "running/agents": "agents", "running/usage": "usage", "running/runs": "runs", "running/schedules": "schedules", "running/health": "health", "configure/agents": "agents", "configure/skills": "skills", "configure/mcp": "mcp", "configure/runtime": "configure" };
  if (path === "configure/runtime") route.section = "runtime";
  if (path === "running/changes/new") path = "agents/plan/new";
  else if (path === "running/changes" || path.startsWith("running/changes/")) path = path.replace("running/changes", "agents/plans");
  path = currentPaths[path] || path;
  const sub = agentsPlan(path);
  if (sub) {
    route.view = "agents";
    route.planPage = sub.planPage;
    route.planId = sub.planId;
  } else if (path === "decide") route.view = "decide";
  else if (path === "issues") route.view = "issues";
  else if (path === "agents") route.view = "agents";
  else if (path === "skills") route.view = "skills";
  else if (path === "mcp") route.view = "mcp";
  else if (path === "usage") route.view = "usage";
  else if (["configure", "runs", "schedules", "health", "settings"].includes(path)) route.view = path;
  else route.view = "map";

  if (route.view === "map") {
    if (path.startsWith("map/")) {
      const rest = path.slice(4);
      route.layer = MAP_LAYERS.has(rest) ? rest : "";
    }
    const scope = params.get("scope");
    route.scope = scope === "involved" ? "involved" : "primary";
    route.gov = flag(params, "gov");
    route.openOnly = flag(params, "open");
    const seg = params.get("seg");
    route.seg = seg === "decide" || seg === "all" ? seg : "open";
  } else if (route.view === "decide") {
    const type = params.get("type");
    route.type = type === "review" || type === "blocked" ? type : "";
    const layer = params.get("layer");
    route.layer = FILTER_LAYERS.has(layer) ? layer : "";
    route.match = params.get("match") === "involved" ? "involved" : "primary";
    route.gov = flag(params, "gov");
    route.group = params.get("group") === "layer" ? "layer" : "";
  } else if (route.view === "issues") {
    const layer = params.get("layer");
    route.layer = FILTER_LAYERS.has(layer) ? layer : "";
    route.match = params.get("match") === "involved" ? "involved" : "primary";
  } else if (route.view === "skills") {
    const source = params.get("source");
    route.evidence = evidence;
    if (evidence) {
      route.source = EVIDENCE_SOURCES.has(source) ? source : "";
    } else {
      route.source = SKILL_SOURCES.has(source) ? source : "";
      const status = params.get("status");
      route.skillStatus = SKILL_STATUSES.has(status) ? status : "";
      const tool = params.get("tool");
      route.tool = (skillTools ? skillTools.has(tool) : /^[a-z0-9][a-z0-9_.-]{0,127}$/i.test(tool || "")) ? tool : "";
      route.custom = flag(params, "custom");
      route.debt = flag(params, "debt");
      const group = params.get("group");
      route.skillGroup = group === "tool" || group === "flat" ? group : "source";
    }
  } else if (route.view === "mcp") {
    // Legacy view=list / issue= / archived=1 are dropped on normalize (§9).
    const source = params.get("source");
    route.source = MCP_SOURCES.has(source) ? source : "";
    const status = params.get("status");
    route.mcpStatus = MCP_STATUSES.has(status) ? status : "";
    const tool = params.get("tool");
    route.tool = (mcpTools ? mcpTools.has(tool) : /^[a-z0-9][a-z0-9_.-]{0,127}$/i.test(tool || "")) ? tool : "";
    const group = params.get("group");
    route.mcpGroup = MCP_GROUPS.has(group) ? group : "source";
    route.mcpAssigned = flag(params, "assigned");
    route.mcpTidy = flag(params, "tidy");
  } else if (route.view === "agents") {
    route.sel = params.get("sel") || "";
    route.fromPlan = params.get("from") || "";
    route.rt = params.get("rt") || "";
    route.model = fieldToken(params.get("model"));
    route.effort = fieldToken(params.get("effort"));
    route.tier = fieldToken(params.get("tier"));
    route.bucket = PLAN_BUCKETS.has(params.get("bucket")) ? params.get("bucket") : "";
    route.planFix = params.get("fix") === "empty" ? "empty" : "";
    if (!route.planPage) {
      route.kind = params.get("kind") === "bots" ? "bots" : "";
      route.hasInstructions = flag(params, "has_instructions");
      route.botLinked = flag(params, "linked");
      route.planMode = flag(params, "plan");
      route.group = params.get("group") === "provider" ? "provider" : "workspace";
      route.provider = params.get("provider") || "";
      route.failedOnly = flag(params, "failed");
      route.offlineOnly = flag(params, "offline");
      route.archived = flag(params, "archived");
    }
  }
  return route;
}

export function formatHash(route) {
  const r = route || emptyRoute();
  const p = new URLSearchParams();
  if (r.ws) p.set("ws", r.ws);
  if (r.q) p.set("q", r.q);
  let path = r.view || "map";
  if (path === "map") {
    if (r.layer && MAP_LAYERS.has(r.layer)) path = `map/${r.layer}`;
    if (r.scope === "involved") p.set("scope", "involved");
    if (r.gov) p.set("gov", "1");
    if (r.openOnly) p.set("open", "1");
    if (r.seg === "decide" || r.seg === "all") p.set("seg", r.seg);
  } else if (path === "decide") {
    if (r.type) p.set("type", r.type);
    if (r.layer && FILTER_LAYERS.has(r.layer)) p.set("layer", r.layer);
    if (r.match === "involved") p.set("match", "involved");
    if (r.gov) p.set("gov", "1");
    if (r.group === "layer") p.set("group", "layer");
  } else if (path === "issues") {
    if (r.layer && FILTER_LAYERS.has(r.layer)) p.set("layer", r.layer);
    if (r.match === "involved") p.set("match", "involved");
  } else if (path === "agents") {
    if (r.planPage === "new") path = "agents/plan/new";
    else if (r.planPage === "list") path = "agents/plans";
    else if (r.planPage === "review" && r.planId) path = `agents/plans/${encodeURIComponent(r.planId)}`;
    if (r.planPage) {
      if (r.planPage === "new" && r.sel) p.set("sel", r.sel);
      if (r.planPage === "new" && r.fromPlan) p.set("from", r.fromPlan);
      if (r.rt) p.set("rt", r.rt);
      if (r.model && r.model !== "omit") p.set("model", r.model);
      if (r.effort && r.effort !== "omit") p.set("effort", r.effort);
      if (r.tier && r.tier !== "omit") p.set("tier", r.tier);
      if (r.planPage === "review" && r.bucket) p.set("bucket", r.bucket);
      if (r.planFix === "empty") p.set("fix", "empty");
    } else {
      if (r.group === "provider") p.set("group", "provider");
      if (r.provider) p.set("provider", r.provider);
      if (r.failedOnly) p.set("failed", "1");
      if (r.offlineOnly) p.set("offline", "1");
      if (r.archived) p.set("archived", "1");
      if (r.kind === "bots") p.set("kind", "bots");
      if (r.hasInstructions) p.set("has_instructions", "1");
      if (r.botLinked) p.set("linked", "1");
      if (r.planMode) p.set("plan", "1");
      if (r.sel) p.set("sel", r.sel);
    }
  } else if (path === "skills") {
    if (r.evidence) {
      path = "configure/skills/evidence";
      if (r.source && EVIDENCE_SOURCES.has(r.source)) p.set("source", r.source);
    } else {
      if (r.source) p.set("source", r.source);
      if (r.skillStatus) p.set("status", r.skillStatus);
      if (r.tool) p.set("tool", r.tool);
      if (r.custom) p.set("custom", "1");
      if (r.debt) p.set("debt", "1");
      if (r.skillGroup === "tool" || r.skillGroup === "flat") p.set("group", r.skillGroup);
    }
  } else if (path === "mcp") {
    if (r.source && MCP_SOURCES.has(r.source)) p.set("source", r.source);
    if (r.mcpStatus && MCP_STATUSES.has(r.mcpStatus)) p.set("status", r.mcpStatus);
    if (r.tool) p.set("tool", r.tool);
    if (r.mcpGroup && r.mcpGroup !== "source") p.set("group", r.mcpGroup);
    if (r.mcpAssigned) p.set("assigned", "1");
    if (r.mcpTidy) p.set("tidy", "1");
  }
  if (path === "agents/plan/new") path = "running/changes/new";
  else if (path === "agents/plans" || path.startsWith("agents/plans/")) path = path.replace("agents/plans", "running/changes");
  else if (path === "agents") path = r.section === "configure" ? "configure/agents" : "running";
  else if (path === "skills" || path === "mcp") path = `configure/${path}`;
  else if (["usage", "runs", "schedules", "health"].includes(path)) path = `running/${path}`;
  else if (path === "configure" && r.section === "runtime") path = "configure/runtime";
  return withQuery(path, p);
}

/** Canonical hash, so reordered queries and legacy aliases compare equal. */
export function canonicalHash(hash) {
  return formatHash(parseHash(hash));
}

/**
 * How an in-app route change is recorded.
 * Workspace chips, clear, and view/tab changes push one step.
 * Repeats and equivalent hashes do nothing, so they cannot loop or pile up.
 * Search and other controls replace the current entry.
 * Load, refresh, hashchange, and popstate do not call this; they only read the URL.
 */
export function historyWrite(currentHash, nextHash, patch) {
  if (canonicalHash(currentHash) === canonicalHash(nextHash)) return "noop";
  const keys = patch && typeof patch === "object" ? Object.keys(patch) : [];
  if (keys.some((key) => key === "view" || key === "section" || key === "ws" || key === "planPage" || key === "planMode" || key === "planId" || key === "kind" || key === "evidence")) return "push";
  return "replace";
}

export function applyPatch(route, patch) {
  const next = { ...emptyRoute(), ...route, ...patch };
  if (patch.view && patch.view !== route.view && !("section" in patch)) next.section = "";
  if (patch.view && patch.view !== route.view && !Object.prototype.hasOwnProperty.call(patch, "layer")) {
    next.layer = "";
    next.gov = false;
    next.openOnly = false;
    next.scope = "primary";
    next.match = "primary";
    next.seg = "open";
    next.type = "";
    if (patch.view !== "decide") next.group = patch.view === "agents" ? route.group || "workspace" : route.group;
    if (patch.view === "decide") next.group = "";
    if (patch.view !== "skills") {
      next.source = "";
      next.skillStatus = "";
      next.tool = "";
      next.custom = false;
      next.debt = false;
      next.skillGroup = "source";
    }
    if (patch.view !== "mcp") {
      next.mcpStatus = "";
      next.mcpGroup = "source";
      next.mcpAssigned = false;
      next.mcpTidy = false;
    }
    if (patch.view !== "agents") {
      next.kind = "";
      next.hasInstructions = false;
      next.botLinked = false;
      next.planMode = false;
      next.planPage = "";
      next.planId = "";
      next.fromPlan = "";
      next.sel = "";
      next.rt = "";
      next.model = "omit";
      next.effort = "omit";
      next.tier = "omit";
      next.bucket = "";
      next.planFix = "";
    }
  }
  // Ordinary Agent focus belongs to the current workspace. Plan selections are cross-workspace.
  if (Object.hasOwn(patch, "ws") && patch.ws !== route.ws && route.view === "agents" && !route.planMode && !route.planPage) next.sel = "";
  if (!VIEWS.includes(next.view)) next.view = "map";
  if (next.view !== "skills") next.evidence = false;
  return next;
}

export function locationHash(hash) {
  return `${location.pathname}${location.search}${hash}`;
}

export function navGroup(route) {
  if (["issues", "decide"].includes(route.view)) return "issues";
  if (["skills", "mcp", "configure"].includes(route.view) || route.section === "configure") return "configure";
  if (["agents", "usage", "runs", "schedules", "health"].includes(route.view)) return "running";
  return route.view;
}
