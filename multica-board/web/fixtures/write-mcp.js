/** Write MCP mock payloads. Run: node multica-board/web/fixtures/write-mcp.js
 * All data is fictional; ids come from fixtures/agents.json + fixtures/meta.json.
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const agentsFixture = JSON.parse(readFileSync(join(root, "agents.json"), "utf8"));
const metaFixture = JSON.parse(readFileSync(join(root, "meta.json"), "utf8"));
const WHEN = "2026-10-23T08:59:40Z";

// support.providers mirrors backend/multica_board/mcp_support.py SUPPORT (v0.6.5).
const UNVERIFIED = { loads: "yes", channel: null, native_merge: "unknown", remote_filter: false, note: "backend_unverified" };
const SUPPORT = {
  source: "multica v0.6.5 packages/core/agents/mcp-support.ts",
  providers: {
    codex: { loads: "yes", channel: "codex_home_config", native_merge: "yes", remote_filter: false, note: null },
    hermes: { loads: "yes", channel: "acp_session", native_merge: "unknown", remote_filter: true, note: null },
    cursor: { loads: "yes", channel: "workdir_cursor_mcp_json", native_merge: "yes", remote_filter: false, note: "oauth_needs_auth_source" },
    mcode: { loads: "yes", channel: "acp_session", native_merge: "unknown", remote_filter: true, note: null },
    claude: { loads: "yes", channel: "claude_mcp_config_file", native_merge: "yes", remote_filter: false, note: null },
    grok: { loads: "yes", channel: "acp_session", native_merge: "unknown", remote_filter: true, note: null },
    opencode: { loads: "conditional", channel: "opencode_config_content", native_merge: "yes", remote_filter: false, note: "v2_task_fails" },
    omp: { loads: "yes", channel: "workdir_config", native_merge: "no", remote_filter: false, note: null },
    antigravity: { loads: "no", channel: null, native_merge: null, remote_filter: false, note: null },
    copilot: { loads: "no", channel: null, native_merge: null, remote_filter: false, note: null },
    codebuddy: { ...UNVERIFIED }, codearts: { ...UNVERIFIED }, kimi: { ...UNVERIFIED },
    reasonix: { ...UNVERIFIED }, dsh: { ...UNVERIFIED }, kiro: { ...UNVERIFIED },
    openclaw: { ...UNVERIFIED }, pi: { ...UNVERIFIED }, qoder: { ...UNVERIFIED },
    qoderclicn: { ...UNVERIFIED }, qwen: { ...UNVERIFIED }, qwenpaw: { ...UNVERIFIED },
    traecli: { ...UNVERIFIED }, dim: { ...UNVERIFIED },
  },
};

const WS = Object.fromEntries(metaFixture.workspaces.map((w) => [w.slug, w]));
const AG = Object.fromEntries(agentsFixture.items.map((a) => [`${a.workspace_id}/${a.name}`, a]));
const W1 = WS["alpha-lab"].id;
const W2 = WS["beta-coach"].id;

function srv(ws, id, name, transport, agent_count, extra = {}) {
  return {
    key: `${ws}:${id}`, id, name, transport, agent_count,
    enabled_count: null, disabled_count: null, config_visible: false,
    created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-20T00:00:00Z",
    ...extra,
  };
}

function agent(ws, agent, { provider, loads, own, bstate, bindings, effective, archived = false }) {
  return {
    key: `${ws}:${agent.id}`, workspace_id: ws, id: agent.id, name: agent.name,
    archived, runtime_id: agent.runtime_id, provider, runtime_loads: loads,
    own_config: own, bindings_state: bstate, bindings, effective,
  };
}

const NO_OWN = { state: "none", servers: [] };
const managed = (servers) => ({ state: "managed", servers });
const flag = (n) => ({ set: n > 0, count: n });
const ownSrv = (name, transport, env = 0, headers = 0) => ({ name, transport, env: flag(env), headers: flag(headers) });

function attentionFor(workspaces, agents) {
  const buckets = { ignored: [], shadowed: [], may_fail: [], hidden: [], unused: [], disabled_only: [] };
  for (const a of agents) {
    if (a.archived) continue;
    const managedAny = (a.bindings || []).some((b) => b.enabled) ||
      (a.own_config?.state === "managed" && (a.own_config.servers || []).length > 0);
    if (managedAny && (a.runtime_loads === "no" || a.runtime_loads === "unbound")) {
      buckets.ignored.push({ key: a.key, workspace_id: a.workspace_id, agent_id: a.id, server_id: null, name: a.name, reason: a.runtime_loads === "unbound" ? "runtime_unbound" : "runtime_no_mcp" });
    }
    for (const b of a.bindings || []) {
      if (b.enabled && b.shadowed) {
        buckets.shadowed.push({ key: `${a.key}:${b.server_id}`, workspace_id: a.workspace_id, agent_id: a.id, server_id: b.server_id, name: b.name, reason: "own_config_same_name" });
      }
    }
    if (managedAny && a.provider === "opencode") {
      buckets.may_fail.push({ key: a.key, workspace_id: a.workspace_id, agent_id: a.id, server_id: null, name: a.name, reason: "opencode_v2_unsupported" });
    }
    if (a.own_config?.state === "hidden") {
      buckets.hidden.push({ key: a.key, workspace_id: a.workspace_id, agent_id: a.id, server_id: null, name: a.name, reason: "config_redacted" });
    }
  }
  for (const ws of workspaces) {
    for (const s of ws.servers || []) {
      if (!s.agent_count) {
        buckets.unused.push({ key: s.key, workspace_id: ws.id, agent_id: null, server_id: s.id, name: s.name, reason: "no_agents" });
      } else if (ws.bindings_state === "ok" && s.enabled_count === 0) {
        buckets.disabled_only.push({ key: s.key, workspace_id: ws.id, agent_id: null, server_id: s.id, name: s.name, reason: "all_disabled" });
      }
    }
  }
  const counts = Object.fromEntries(Object.entries(buckets).map(([k, v]) => [k, v.length]));
  return {
    badge: counts.ignored + counts.shadowed + counts.may_fail,
    counts,
    buckets: Object.fromEntries(Object.entries(buckets).filter(([, v]) => v.length)),
  };
}

function payload(workspaces, agents) {
  return {
    generated_at: WHEN,
    mcp_schema: 1,
    mcp_every_rounds: 5,
    support: SUPPORT,
    workspaces,
    agents,
    attention: attentionFor(workspaces, agents),
  };
}

function wsBlock(ws, { library_state = "ok", bindings_state = "none", servers = [], error = null, collected = true } = {}) {
  return {
    id: ws.id, slug: ws.slug, name: ws.name,
    library_state,
    library_collected_at: collected ? WHEN : null,
    library_error: error,
    bindings_state,
    bindings_collected_at: bindings_state === "ok" || bindings_state === "partial" ? WHEN : null,
    servers,
  };
}

function write(dir, name, data) {
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, name), JSON.stringify(data, null, 1) + "\n");
}

// --- default fixtures/mcp.json: today's real state (everything empty) ---
write(root, "mcp.json", payload(
  metaFixture.workspaces.slice(0, 2).map((ws) => wsBlock(ws)),
  [],
));

// --- scenario mcp: two libraries covering every bucket + a >8-column workspace ---
const reviewer1 = AG[`${W1}/Reviewer`];
const steward1 = AG[`${W1}/Steward`];
const analyst1 = AG[`${W1}/Repo Analyst`];
const archivist1 = AG[`${W1}/Knowledge Archivist`];
const reviewer2 = AG[`${W2}/Reviewer`];
const steward2 = AG[`${W2}/Steward`];

const w1servers = [
  srv(W1, "srv-0001-linear", "linear", "http", 3, { enabled_count: 2, disabled_count: 1 }),
  srv(W1, "srv-0002-sentry", "sentry", "http", 1, { enabled_count: 1, disabled_count: 0 }),
  srv(W1, "srv-0003-fs", "fs", "stdio", 2, { enabled_count: 0, disabled_count: 2 }),
  srv(W1, "srv-0004-idle", "idle-srv", "stdio", 0, { enabled_count: 0, disabled_count: 0 }),
];
const w2servers = Array.from({ length: 9 }, (_, i) =>
  srv(W2, `srv-010${i}-tool${i}`, `tool${i}`, i % 3 ? "http" : "stdio", 1, { enabled_count: 1, disabled_count: 0 }));

const w1agents = [
  agent(W1, steward1, {
    provider: "antigravity", loads: "no",
    own: NO_OWN, bstate: "ok",
    bindings: [{ server_id: "srv-0001-linear", name: "linear", enabled: true, shadowed: false }],
    effective: { state: "known", servers: [{ name: "linear", from: "library", transport: "http" }] },
  }),
  agent(W1, reviewer1, {
    provider: "hermes", loads: "yes",
    own: managed([ownSrv("linear", "stdio", 2, 0)]),
    bstate: "ok",
    bindings: [
      { server_id: "srv-0001-linear", name: "linear", enabled: true, shadowed: true },
      { server_id: "srv-0002-sentry", name: "sentry", enabled: true, shadowed: false },
      { server_id: "srv-0003-fs", name: "fs", enabled: false, shadowed: false },
    ],
    effective: { state: "known", servers: [{ name: "linear", from: "own", transport: "stdio" }, { name: "sentry", from: "library", transport: "http" }] },
  }),
  agent(W1, analyst1, {
    provider: "opencode", loads: "conditional",
    own: NO_OWN, bstate: "ok",
    bindings: [{ server_id: "srv-0001-linear", name: "linear", enabled: true, shadowed: false }],
    effective: { state: "known", servers: [{ name: "linear", from: "library", transport: "http" }] },
  }),
  agent(W1, archivist1, {
    provider: "claude", loads: "yes",
    own: { state: "hidden", servers: [] }, bstate: "ok",
    bindings: [{ server_id: "srv-0003-fs", name: "fs", enabled: false, shadowed: null }],
    effective: { state: "partial", servers: [] },
  }),
];
const w2agents = [
  agent(W2, reviewer2, {
    provider: "codex", loads: "yes",
    own: NO_OWN, bstate: "ok",
    bindings: w2servers.map((s) => ({ server_id: s.id, name: s.name, enabled: true, shadowed: false })),
    effective: { state: "known", servers: w2servers.map((s) => ({ name: s.name, from: "library", transport: s.transport })) },
  }),
  agent(W2, steward2, {
    provider: null, loads: "unbound",
    own: managed([ownSrv("notes", "stdio", 1, 0)]),
    bstate: "ok", bindings: [],
    effective: { state: "known", servers: [{ name: "notes", from: "own", transport: "stdio" }] },
  }),
];
write(join(root, "mcp"), "mcp.json", payload(
  [wsBlock(WS["alpha-lab"], { bindings_state: "ok", servers: w1servers }),
   wsBlock(WS["beta-coach"], { bindings_state: "ok", servers: w2servers })],
  [...w1agents, ...w2agents],
));

// --- scenario mcp-empty: same as default ---
write(join(root, "mcp-empty"), "mcp.json", payload(
  metaFixture.workspaces.map((ws) => wsBlock(ws)),
  [],
));

// --- scenario mcp-partial: error / partial / never ---
write(join(root, "mcp-partial"), "mcp.json", payload(
  [
    wsBlock(WS["alpha-lab"], {
      library_state: "error", bindings_state: "ok",
      servers: [srv(W1, "srv-0001-linear", "linear", "http", 1, { enabled_count: 1, disabled_count: 0 })],
      error: { kind: "timeout", message: "CLI timed out after 60s" },
    }),
    wsBlock(WS["beta-coach"], {
      bindings_state: "partial",
      servers: [srv(W2, "srv-0201-wiki", "wiki", "http", 2, { enabled_count: null, disabled_count: null })],
    }),
    wsBlock(WS["gamma-oasis"], { library_state: "never", collected: false }),
  ],
  [
    agent(W1, reviewer1, {
      provider: "hermes", loads: "yes", own: NO_OWN, bstate: "ok",
      bindings: [{ server_id: "srv-0001-linear", name: "linear", enabled: true, shadowed: false }],
      effective: { state: "known", servers: [{ name: "linear", from: "library", transport: "http" }] },
    }),
    agent(W2, reviewer2, {
      provider: "codex", loads: "yes", own: NO_OWN, bstate: "ok",
      bindings: [{ server_id: "srv-0201-wiki", name: "wiki", enabled: true, shadowed: false }],
      effective: { state: "partial", servers: [{ name: "wiki", from: "library", transport: "http" }] },
    }),
    agent(W2, steward2, {
      provider: "codex", loads: "yes", own: NO_OWN, bstate: "error",
      bindings: [],
      effective: { state: "partial", servers: [] },
    }),
  ],
));

console.log("wrote mcp fixtures");
