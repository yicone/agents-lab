/** Hash routes for Agent Atlas. Legacy hashes redirect with location.replace. */

export const VIEWS = ["map", "decide", "issues", "agents", "skills"];

const LAYER_IDS = ["l1", "l2", "l3", "l4", "l5"];
const MAP_LAYERS = new Set([...LAYER_IDS, "all", "outside", "unassigned"]);
const FILTER_LAYERS = new Set([...LAYER_IDS, "outside", "unassigned"]);
const SKILL_SOURCES = new Set(["skills_cli_managed", "local_custom", "runtime_plugin", "runtime_builtin", "third_party_unmanaged", "unrecorded"]);
const SKILL_STATUSES = new Set(["aligned", "stale", "version_changed", "modified", "unverified", "missing"]);
const SKILL_TOOLS = new Set(["cursor", "codex", "claude", "hermes", "gemini", "project"]);

export function viewForKey(key) {
  return { 1: "map", 2: "decide", 3: "issues", 4: "agents", 5: "skills" }[key] || null;
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
  if (path === "issues" || path === "agents" || path === "skills" || path === "decide") return null;
  if (path === "map") return null;
  if (path.startsWith("map/")) {
    const rest = path.slice(4);
    if (rest === "all" || rest === "outside" || rest === "unassigned" || /^l[1-5]$/.test(rest)) return null;
    return "#/map";
  }
  if (path === "l5/attention") return "#/issues";
  if (path === "l2/agents") return "#/agents";
  if (path === "l3/skills") return "#/skills";
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
  };
}

export function parseHash(hash) {
  const redirected = redirectHash(hash);
  const source = redirected || (hash ? hash : "#/map");
  const { path, params } = splitHash(source.startsWith("#") ? source : `#${source}`);
  const route = emptyRoute();
  route.ws = params.get("ws") || "";
  route.q = params.get("q") || "";

  if (path === "decide") route.view = "decide";
  else if (path === "issues") route.view = "issues";
  else if (path === "agents") route.view = "agents";
  else if (path === "skills") route.view = "skills";
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
    route.source = SKILL_SOURCES.has(source) ? source : "";
    const status = params.get("status");
    route.skillStatus = SKILL_STATUSES.has(status) ? status : "";
    const tool = params.get("tool");
    route.tool = SKILL_TOOLS.has(tool) ? tool : "";
    route.custom = flag(params, "custom");
    route.debt = flag(params, "debt");
    const group = params.get("group");
    route.skillGroup = group === "tool" || group === "flat" ? group : "source";
  } else if (route.view === "agents") {
    route.group = params.get("group") === "provider" ? "provider" : "workspace";
    route.provider = params.get("provider") || "";
    route.failedOnly = flag(params, "failed");
    route.offlineOnly = flag(params, "offline");
    route.archived = flag(params, "archived");
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
    if (r.group === "provider") p.set("group", "provider");
    if (r.provider) p.set("provider", r.provider);
    if (r.failedOnly) p.set("failed", "1");
    if (r.offlineOnly) p.set("offline", "1");
    if (r.archived) p.set("archived", "1");
  } else if (path === "skills") {
    if (r.source) p.set("source", r.source);
    if (r.skillStatus) p.set("status", r.skillStatus);
    if (r.tool) p.set("tool", r.tool);
    if (r.custom) p.set("custom", "1");
    if (r.debt) p.set("debt", "1");
    if (r.skillGroup === "tool" || r.skillGroup === "flat") p.set("group", r.skillGroup);
  }
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
  if (keys.some((key) => key === "view" || key === "ws")) return "push";
  return "replace";
}

export function applyPatch(route, patch) {
  const next = { ...emptyRoute(), ...route, ...patch };
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
  }
  if (!VIEWS.includes(next.view)) next.view = "map";
  return next;
}

export function locationHash(hash) {
  return `${location.pathname}${location.search}${hash}`;
}
