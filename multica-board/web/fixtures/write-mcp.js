/** Regenerate MCP fixtures (schema 2). Run: node multica-board/web/fixtures/write-mcp.js
 * All data is fictional and deterministic — no clocks, no randomness, no real paths
 * beyond the ~/-style declaration strings from config/mcp-sources.json.
 * Row ids are fake 20-char base32 strings starting with "m" (like the backend's
 * process-salted row ids); workspace ids come from fixtures/meta.json.
 * Scenarios: mcp / mcp-empty / mcp-partial / mcp-unknown / mcp-invalid / mcp-warn.
 * (mcp-absent needs no file — the app simulates the 404 itself.) */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const metaFixture = JSON.parse(readFileSync(join(root, "meta.json"), "utf8"));
const W1 = metaFixture.workspaces[0];
const W2 = metaFixture.workspaces[1];

const B32 = "abcdefghijklmnopqrstuvwxyz234567";
// Deterministic fake row id for item number n (same shape as backend HMAC ids).
const rowId = (n) => "m" + Array.from({ length: 20 }, (_, i) => B32[(n * 7 + i * 13) % 32]).join("");

const WHEN_SCAN = "2026-10-24T02:01:00Z";
const WHEN_MULTICA = "2026-10-24T01:58:00Z";
const WHEN_ROW = "2026-10-20T08:00:00Z";

const SUPPORT = {
  source: "multica v0.6.5 packages/core/agents/mcp-support.ts",
  providers: {
    codex: { loads: "yes", channel: "codex_home_config", native_merge: "yes", remote_filter: false, note: null },
    hermes: { loads: "yes", channel: "acp_session", native_merge: "unknown", remote_filter: true, note: null },
    cursor: { loads: "yes", channel: "workdir_cursor_mcp_json", native_merge: "yes", remote_filter: false, note: null },
    mcode: { loads: "yes", channel: "acp_session", native_merge: "unknown", remote_filter: true, note: null },
    claude: { loads: "yes", channel: "claude_mcp_config_file", native_merge: "yes", remote_filter: false, note: null },
    grok: { loads: "yes", channel: "acp_session", native_merge: "unknown", remote_filter: true, note: null },
    opencode: { loads: "conditional", channel: "opencode_config_content", native_merge: "yes", remote_filter: false, note: "v2_task_fails" },
    antigravity: { loads: "no", channel: null, native_merge: null, remote_filter: false, note: null },
    copilot: { loads: "no", channel: null, native_merge: null, remote_filter: false, note: null },
  },
};

const TOOL_FILES = [
  ["cursor", "Cursor", "~/.cursor/mcp.json"],
  ["codex", "Codex", "~/.codex/config.toml"],
  ["claude", "Claude Code", "~/.claude.json"],
  ["hermes", "Hermes Agent", "~/.hermes/config.yaml"],
  ["gemini", "Gemini CLI", "~/.gemini/settings.json"],
  ["mcode", "Devin CLI", "~/.config/devin/mcp_config.json"],
  ["antigravity", "Antigravity", "~/.gemini/antigravity/mcp_config.json"],
  ["windsurf", "Windsurf", "~/.codeium/windsurf/mcp_config.json"],
  ["opencode", "OpenCode", "~/.config/opencode/opencode.json"],
];

const toolsWith = (fileState, fileDetail) =>
  TOOL_FILES.map(([id, label, path], i) => ({
    id,
    label,
    short_label: ["Cursor", "Codex", "Claude", "Hermes", "Gemini", "Devin", "Antigravity", "Windsurf", "OpenCode"][i],
    files: [fileState(path, i, id)],
  }));

const TOOLS_OK = toolsWith((path, i) => ({
  path,
  scope: "global",
  state: i < 4 ? "ok" : "absent",
  detail: i < 4 ? "已读" : "文件不存在：确定没有",
  entry_count: [3, 2, 2, 1, 0, 0, 0, 0, 0][i],
}));

const REPOS_OK = [
  {
    id: "agents-lab",
    label: "agents-lab",
    files: [
      { path: ".mcp.json", location: ".mcp.json", state: "ok", detail: "已读", entry_count: 1 },
      { path: ".cursor/mcp.json", location: ".cursor", state: "absent", detail: "文件不存在", entry_count: 0 },
      { path: ".codex/config.toml", location: ".codex", state: "absent", detail: "文件不存在", entry_count: 0 },
      { path: ".gemini/settings.json", location: ".gemini", state: "absent", detail: "文件不存在", entry_count: 0 },
      { path: "opencode.json", location: "opencode.json", state: "absent", detail: "文件不存在", entry_count: 0 },
    ],
  },
];

const WS_OK = [
  { id: W1.id, slug: W1.slug, name: W1.name, library_state: "ok", library_error: null, bindings_state: "ok", collected_at: WHEN_MULTICA },
  { id: W2.id, slug: W2.slug, name: W2.name, library_state: "ok", library_error: null, bindings_state: "ok", collected_at: WHEN_MULTICA },
];

const U_ALL_KNOWN = {
  local_files: [], project_files: [], library: [], bindings: [],
  own_hidden: 0, own_invalid: 0, own_pending: 0, key: "ok", multica: "collected",
};

const SOURCES_OK = [
  { id: "local_files", label: "本机配置", state: "ok", detail: "4 已读 · 5 不存在" },
  { id: "project_files", label: "仓库项目级", state: "ok", detail: "1 有 · 4 不存在" },
  { id: "fingerprints", label: "配置指纹", state: "ok", detail: "只比哈希" },
  { id: "multica_library", label: "Multica 库", state: "ok", detail: "2 个 Workspace" },
  { id: "agent_bindings", label: "Agent 分配", state: "ok", detail: "2 个 Workspace" },
  { id: "multica_own_config", label: "Agent 自有配置", state: "ok", detail: "全部可见" },
  { id: "runtime_support", label: "Runtime 加载表", state: "ok", detail: "v0.6.5" },
];

// ---------- builders ----------

const flag = (n) => ({ set: n > 0, count: n });

function gEntry(tool, path, group, env = 0, headers = 0, extra = {}) {
  return { scope: "global", tool, path, state: "ok", broken_reason: null, disabled: false, transport: "stdio", env: flag(env), headers: flag(headers), group, ...extra };
}

function pEntry(location, path, group) {
  return { scope: "project", tool: null, location, path, state: "ok", broken_reason: null, disabled: false, transport: "stdio", env: flag(0), headers: flag(0), group };
}

function lib(ws, server_id, agents, transport = "stdio", extra = {}) {
  return {
    kind: "library", workspace_id: ws.id, workspace_name: ws.name, server_id,
    transport, agent_count: (agents || []).length, updated_at: WHEN_ROW,
    stale: false, bindings_known: true, agents, ...extra,
  };
}

function agentRow(i, name, { enabled = true, shadowed = null, provider = "codex", runtime_loads = "yes" } = {}) {
  return {
    id: `a-${String(i).padStart(4, "0")}-0000-4000-8000-${String(i).padStart(12, "0")}`,
    name, enabled, shadowed, provider, runtime_loads,
  };
}

function ownEntry(ws, i, agent_name, { provider = "codex", runtime_loads = "yes", fp_state = "ready", group = null, transport = "stdio" } = {}) {
  return {
    kind: "own", workspace_id: ws.id, workspace_name: ws.name,
    agent_id: agentRow(i).id, agent_name, provider, runtime_loads, fp_state, transport,
    broken_reason: null, env: flag(1), headers: flag(0), group,
  };
}

const SOURCE_LABEL = { both: "本机 + Multica", local_only: "仅本机", multica_only: "仅 Multica" };

function item(n, display_name, source_class, entries, workspace_entries, consistency, {
  transport = "stdio", tools = {}, repo_cells = {}, tidy = null, bucket = null, tags = [],
  reason = null, command = null, display_dup = null, source_uncertain = false,
} = {}) {
  return {
    id: rowId(n), display_name, display_dup, source_class, source_uncertain,
    source_label: SOURCE_LABEL[source_class],
    in_project: Object.values(repo_cells).some((v) => ["direct", "off", "missing"].includes(v)),
    presence: {
      local: entries.filter((e) => e.scope === "global").length,
      project: entries.filter((e) => e.scope === "project").length,
      library: workspace_entries.filter((w) => w.kind === "library").length,
      own: workspace_entries.filter((w) => w.kind === "own").length,
    },
    transports: [transport], tools, repo_cells, entries, workspace_entries, consistency,
    tidy, bucket, tags, reason, quiet: false, command,
  };
}

function consistency(state, groups = [], parts, { partial = false, pending = 0 } = {}) {
  return {
    state, partial, pending,
    groups: groups.map(([id, members]) => ({ id, members })),
    parts: parts || { launch: "same", env: "same", headers: "same" },
  };
}

function attention(byBucket, { tidyCount = 0, tone = "bad", partial = false, partialSources = {} } = {}) {
  const buckets = {};
  for (const code of ["broken", "ignored", "shadowed", "may_fail", "drift"]) {
    const rows = byBucket[code] || [];
    const part = partialSources[code];
    buckets[code] = {
      connected: true,
      partial: !!part,
      unknown_sources: part || [],
      reason: "",
      items: rows.map((i) => ({ id: i.id, title: i.reason || "", status: code, time: WHEN_SCAN, source_class: i.source_class, tags: i.tags })),
    };
  }
  const counts = Object.fromEntries(Object.entries(buckets).map(([k, v]) => [k, v.items.length]));
  return {
    badge: Object.values(counts).reduce((a, b) => a + b, 0), tone, partial, counts, buckets,
    tidy: { count: tidyCount, partial: false, unknown_sources: [], connected: true, reason: "" },
  };
}

function summary(items) {
  const statuses = (sub) => {
    const d = {};
    for (const i of sub) d[i.consistency.state] = (d[i.consistency.state] || 0) + 1;
    return d;
  };
  const by = (sc) => items.filter((i) => i.source_class === sc);
  return [
    { id: "all", label: "全部", count: items.length, uncertain: items.filter((i) => i.source_uncertain).length, statuses: statuses(items) },
    { id: "both", label: "本机 + Multica", count: by("both").length, uncertain: 0, statuses: statuses(by("both")) },
    { id: "local_only", label: "仅本机", count: by("local_only").length, uncertain: by("local_only").filter((i) => i.source_uncertain).length, statuses: statuses(by("local_only")) },
    { id: "multica_only", label: "仅 Multica", count: by("multica_only").length, uncertain: by("multica_only").filter((i) => i.source_uncertain).length, statuses: statuses(by("multica_only")) },
  ];
}

function payload({ items, unknown, attn, tools = TOOLS_OK, repos = REPOS_OK, workspaces = WS_OK, sources = SOURCES_OK }) {
  return {
    ok: true, error: null, mcp_schema: 2,
    generated_at: WHEN_SCAN, multica_collected_at: WHEN_MULTICA,
    tools, repos, workspaces, support: SUPPORT, unknown, items,
    summary: summary(items), attention: attn,
    meta: { sources },
  };
}

const write = (name, data) => {
  mkdirSync(join(root, name), { recursive: true });
  writeFileSync(join(root, name, "mcp.json"), JSON.stringify(data, null, 1) + "\n");
};

// ---------- mcp: all sources known, red badge ----------
{
  const github = item(1, "github", "both",
    [gEntry("cursor", "~/.cursor/mcp.json", "A", 2), gEntry("codex", "~/.codex/config.toml", "B", 3),
     gEntry("claude", "~/.claude.json", "A", 2), gEntry("hermes", "~/.hermes/config.yaml", "B", 3)],
    [lib(W1, "srv-github", [agentRow(1, "Builder · Codex"), agentRow(2, "Reviewer · Hermes")])],
    consistency("differs", [["A", ["Cursor", "Claude Code"]], ["B", ["Codex", "Hermes Agent"]]],
      { launch: "same", env: "differs", headers: "same" }),
    { tools: { cursor: "direct", codex: "direct", claude: "direct", hermes: "direct" },
      bucket: "drift", reason: "Cursor、Claude Code 与 Codex、Hermes Agent 的 env 不同",
      command: '"${EDITOR:-vi}" ~/.codex/config.toml' });
  const linear = item(2, "linear", "both",
    [gEntry("cursor", "~/.cursor/mcp.json", "A"), gEntry("codex", "~/.codex/config.toml", "A")],
    [lib(W1, "srv-linear", [agentRow(3, "调研 · Antigravity", { provider: "antigravity", runtime_loads: "no" })], "http")],
    consistency("same"),
    { transport: "http", tools: { cursor: "direct", codex: "direct" },
      bucket: "ignored", reason: "调研 · Antigravity 的 runtime 不加载 MCP（Alpha Lab）",
      command: '"${EDITOR:-vi}" ~/.cursor/mcp.json' });
  const sentry = item(3, "sentry", "both",
    [gEntry("cursor", "~/.cursor/mcp.json", "A", 0, 0, { disabled: true })],
    [lib(W1, "srv-sentry", [agentRow(2, "Reviewer · Hermes", { shadowed: true })], "http"),
     ownEntry(W1, 2, "Reviewer · Hermes", { provider: "hermes", group: "A", transport: "http" })],
    consistency("same"),
    { transport: "http", tools: { cursor: "off" },
      bucket: "shadowed", reason: "Reviewer · Hermes 的自有配置同名，库条目不生效" });
  const notion = item(4, "notion", "local_only",
    [gEntry("cursor", "~/.cursor/mcp.json", null),
     gEntry("hermes", "~/.hermes/config.yaml", null, 0, 0, { state: "broken", broken_reason: "no_target" })],
    [], consistency("broken"),
    { transport: "http", tools: { cursor: "direct", hermes: "missing" },
      bucket: "broken", reason: "Hermes Agent 的条目缺 command 和 url" });
  const playwright = item(5, "playwright", "local_only",
    [gEntry("cursor", "~/.cursor/mcp.json", "A"), gEntry("claude", "~/.claude.json", "B"),
     pEntry(".mcp.json", ".mcp.json", "B")],
    [], consistency("differs", [["A", ["Cursor"]], ["B", ["Claude Code", "仓库 .mcp.json"]]],
      { launch: "differs", env: "same", headers: "same" }),
    { tools: { cursor: "direct", claude: "direct" }, repo_cells: { ".mcp.json": "direct" },
      bucket: "drift", reason: "Cursor 与 Claude Code、仓库 .mcp.json 的启动方式不同" });
  const filesystem = item(6, "filesystem", "local_only",
    [["cursor", "~/.cursor/mcp.json"], ["codex", "~/.codex/config.toml"], ["claude", "~/.claude.json"],
     ["gemini", "~/.gemini/settings.json"], ["mcode", "~/.config/devin/mcp_config.json"],
     ["antigravity", "~/.gemini/antigravity/mcp_config.json"], ["windsurf", "~/.codeium/windsurf/mcp_config.json"]]
      .map(([t, p]) => gEntry(t, p, "A")).concat([pEntry(".cursor", ".cursor/mcp.json", "A")]),
    [], consistency("same"),
    { tools: { cursor: "direct", codex: "direct", claude: "direct", gemini: "direct", mcode: "direct", antigravity: "direct", windsurf: "direct" },
      repo_cells: { ".cursor": "direct" } });
  const slack = item(7, "slack", "local_only", [gEntry("claude", "~/.claude.json", "A")], [],
    consistency("single"), { tools: { claude: "direct" } });
  const acme1 = item(8, "acme tools", "local_only", [gEntry("cursor", "~/.cursor/mcp.json", "A")], [],
    consistency("single"), { tools: { cursor: "direct" }, display_dup: { index: 1, total: 2 } });
  const acme2 = item(9, "acme tools", "local_only", [gEntry("claude", "~/.claude.json", "A")], [],
    consistency("single"), { tools: { claude: "direct" }, display_dup: { index: 2, total: 2 } });
  const unverified = item(10, "kv store", "local_only", [],
    [ownEntry(W1, 4, "调研 · Antigravity", { provider: "antigravity", runtime_loads: "no", fp_state: "pending" })],
    consistency("incomparable", [], undefined, { partial: true, pending: 1 }),
    { tags: ["name_unverified"] });
  const docsSearch = item(11, "docs-search", "multica_only", [],
    [lib(W1, "srv-docs", [agentRow(5, "实现 · OpenCode", { provider: "opencode", runtime_loads: "conditional" })], "http"),
     lib(W2, "srv-docs2", [], "http")],
    consistency("incomparable"),
    { transport: "http", tools: Object.fromEntries(TOOL_FILES.map(([id]) => [id, "not_applicable"])),
      repo_cells: { ".mcp.json": "not_applicable" },
      bucket: "may_fail", reason: "实现 · OpenCode：OpenCode 2.x 有托管 MCP 时任务直接失败" });
  const issueBridge = item(12, "issue-bridge", "multica_only", [],
    [lib(W2, "srv-ib", [agentRow(6, "Coach · Codex", { enabled: false })])],
    consistency("incomparable"),
    { tools: Object.fromEntries(TOOL_FILES.map(([id]) => [id, "not_applicable"])),
      repo_cells: { ".mcp.json": "not_applicable" },
      tidy: { reason: "all_disabled", workspace_id: W2.id } });
  const kbReader = item(13, "kb-reader", "multica_only", [],
    [lib(W1, "srv-kb", [])], consistency("incomparable"),
    { tools: Object.fromEntries(TOOL_FILES.map(([id]) => [id, "not_applicable"])),
      repo_cells: { ".mcp.json": "not_applicable" },
      tidy: { reason: "no_agents", workspace_id: W1.id } });
  const items = [github, linear, sentry, notion, playwright, filesystem, slack, acme1, acme2, unverified, docsSearch, issueBridge, kbReader];
  write("mcp", payload({
    items,
    unknown: U_ALL_KNOWN,
    attn: attention(
      { broken: [notion], ignored: [linear], shadowed: [sentry], may_fail: [docsSearch], drift: [github, playwright] },
      { tidyCount: 2, tone: "bad" }),
  }));
}

// ---------- mcp-empty: all known, nothing declared ----------
write("mcp-empty", payload({ items: [], unknown: U_ALL_KNOWN, attn: attention({}, { tone: "none" }) }));

// ---------- mcp-partial: hermes parse_error + one workspace's bindings unknown ----------
{
  const tools = toolsWith((path, i, id) => ({
    path, scope: "global",
    state: id === "hermes" ? "parse_error" : i < 4 ? "ok" : "absent",
    detail: id === "hermes" ? "解析失败，未读取" : i < 4 ? "已读" : "文件不存在：确定没有",
    entry_count: [3, 2, 2, 0, 0, 0, 0, 0, 0][i],
  }));
  const workspaces = [WS_OK[0], { ...WS_OK[1], bindings_state: "partial" }];
  const github = item(1, "github", "both",
    [gEntry("cursor", "~/.cursor/mcp.json", "A"), gEntry("codex", "~/.codex/config.toml", "A")],
    [lib(W1, "srv-github", [agentRow(1, "Builder · Codex")])],
    consistency("same", [], undefined, { partial: true }),
    { tools: { cursor: "direct", codex: "direct", hermes: "unknown" } });
  const docsSearch = item(2, "docs-search", "local_only",
    [{ scope: "global", tool: "cursor", path: "~/.cursor/mcp.json", state: "ok", broken_reason: null, disabled: false, transport: "http", env: flag(0), headers: flag(0), group: "A" }],
    [{ kind: "library", workspace_id: W2.id, workspace_name: W2.name, server_id: "srv-docs", transport: "stdio", agent_count: 0, updated_at: WHEN_ROW, stale: false, bindings_known: false, agents: null }],
    consistency("incomparable", [], undefined, { partial: true }),
    { source_uncertain: true,
      tools: Object.fromEntries(TOOL_FILES.map(([id]) => [id, id === "hermes" ? "unknown" : id === "cursor" ? "direct" : null])) });
  const attn = attention({}, { tone: "unknown", partial: true });
  attn.tidy = { count: 0, partial: true, unknown_sources: [W2.name], connected: true, reason: "" };
  for (const c of ["ignored", "shadowed", "may_fail"]) {
    attn.buckets[c].partial = true;
    attn.buckets[c].unknown_sources = [W2.name];
  }
  for (const c of ["broken", "drift"]) {
    attn.buckets[c].partial = true;
    attn.buckets[c].unknown_sources = ["本机 Hermes Agent"];
  }
  const p = payload({
    items: [github, docsSearch],
    unknown: { ...U_ALL_KNOWN, local_files: ["hermes"], bindings: [W2.id] },
    attn, tools, workspaces,
  });
  p.summary = [
    { id: "all", label: "全部", count: 2, uncertain: 1, statuses: { same: 1, incomparable: 1 } },
    { id: "both", label: "本机 + Multica", count: 1, uncertain: 0, statuses: { same: 1 } },
    { id: "local_only", label: "仅本机", count: 1, uncertain: 1, statuses: { incomparable: 1 } },
    { id: "multica_only", label: "仅 Multica", count: 0, uncertain: 0, statuses: {} },
  ];
  p.meta.sources[0] = { id: "local_files", label: "本机配置", state: "warn", detail: "8 已读 · 1 读不出" };
  p.meta.sources[4] = { id: "agent_bindings", label: "Agent 分配", state: "warn", detail: "2 个 Workspace · 1 个没读到" };
  write("mcp-partial", p);
}

// ---------- mcp-unknown: every source failed, grey ? ----------
{
  const tools = toolsWith((path) => ({ path, scope: "global", state: "denied", detail: "没有权限，未读取", entry_count: 0 }));
  const repos = REPOS_OK.map((r) => ({ id: r.id, label: r.label, files: r.files.map((f) => ({ ...f, state: "denied", detail: "没有权限，未读取", entry_count: 0 })) }));
  const workspaces = WS_OK.map((w) => ({ ...w, library_state: "never", bindings_state: "never" }));
  const attn = attention({}, { tone: "unknown", partial: true });
  for (const c of Object.keys(attn.buckets)) {
    attn.buckets[c].connected = false;
    attn.buckets[c].reason = "本机未扫描且 Multica 从未采集";
  }
  const unknown = {
    local_files: TOOL_FILES.map(([id]) => id),
    project_files: REPOS_OK[0].files.map((f) => f.location),
    library: [W1.id, W2.id], bindings: [W1.id, W2.id],
    own_hidden: 0, own_invalid: 0, own_pending: 0, key: "key_mode", multica: "never",
  };
  const p = payload({
    items: [], unknown, attn, tools, repos, workspaces,
    sources: [
      { id: "local_files", label: "本机配置", state: "bad", detail: "9 个都没读到" },
      { id: "project_files", label: "仓库项目级", state: "bad", detail: "5 个都没读到" },
      { id: "fingerprints", label: "配置指纹", state: "bad", detail: "密钥文件不可用（key_mode），未比对" },
      { id: "multica_library", label: "Multica 库", state: "warn", detail: "从未采集" },
      { id: "agent_bindings", label: "Agent 分配", state: "warn", detail: "从未采集" },
      { id: "multica_own_config", label: "Agent 自有配置", state: "warn", detail: "未采集" },
      { id: "runtime_support", label: "Runtime 加载表", state: "ok", detail: "v0.6.5" },
    ],
  });
  write("mcp-unknown", p);
}

// ---------- mcp-invalid: ok:false ----------
write("mcp-invalid", {
  ok: false, error: { code: "sources_invalid", message: "mcp-sources.json 无效" }, mcp_schema: 2,
  generated_at: WHEN_SCAN, multica_collected_at: null, tools: [], repos: [], workspaces: [],
  support: SUPPORT, unknown: U_ALL_KNOWN, items: [], summary: [],
  attention: attention({}, { tone: "unknown", partial: true }),
  meta: { sources: [{ id: "sources", label: "文件清单", state: "bad", detail: "mcp-sources.json 无效" }] },
});

// ---------- mcp-warn: only drift, yellow badge ----------
{
  const github = item(1, "github", "both",
    [gEntry("cursor", "~/.cursor/mcp.json", "A", 2), gEntry("codex", "~/.codex/config.toml", "B", 3)],
    [lib(W1, "srv-github", [agentRow(1, "Builder · Codex")])],
    consistency("differs", [["A", ["Cursor"]], ["B", ["Codex"]]], { launch: "same", env: "differs", headers: "same" }),
    { tools: { cursor: "direct", codex: "direct" }, bucket: "drift",
      reason: "Cursor 与 Codex 的 env 不同", command: '"${EDITOR:-vi}" ~/.codex/config.toml' });
  const slack = item(2, "slack", "local_only", [gEntry("claude", "~/.claude.json", "A")], [],
    consistency("single"), { tools: { claude: "direct" } });
  write("mcp-warn", payload({
    items: [github, slack], unknown: U_ALL_KNOWN,
    attn: attention({ drift: [github] }, { tone: "warn" }),
  }));
}

console.log("wrote fixtures: mcp, mcp-empty, mcp-partial, mcp-unknown, mcp-invalid, mcp-warn");
