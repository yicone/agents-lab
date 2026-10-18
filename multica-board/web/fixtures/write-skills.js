/** Write Skills mock payloads. Run: node multica-board/web/fixtures/write-skills.js */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const when = "2026-10-16T08:28:00Z";
const tools = [
  { id: "cursor", label: "Cursor" },
  { id: "codex", label: "Codex" },
  { id: "claude", label: "Claude" },
  { id: "hermes", label: "Hermes" },
  { id: "gemini", label: "Gemini" },
  { id: "project", label: "仓库入口" },
];

function marks(partial) {
  return { cursor: null, codex: null, claude: null, hermes: null, gemini: null, project: null, ...partial };
}

function item(overrides) {
  return {
    name: "skill",
    origin: "inventory",
    source_class: "skills_cli_managed",
    source_label: "Skills CLI",
    scope: "machine",
    ownership: "third_party",
    source_authority: "upstream_repository",
    update_authority: "skills_cli",
    canonical_path: null,
    repo_is_source: false,
    channel_label: "Skills CLI",
    channel_detail: "github:example/skill",
    current_version: "1.0.0",
    observed_version: "1.0.0",
    baseline_kind: "upstream_ref",
    baseline_subject: "SKILL.md",
    baseline_ref: "github:example/skill",
    review_time: null,
    reconciliation_status: "stale",
    status_label: "未复核",
    findings: [{ code: "review_time_missing", label: "还没有复核时间" }],
    customization_mode: "none",
    constraints: [],
    constraint_count: 0,
    conflict_status: "unreviewed",
    tools: marks({ cursor: "shared", codex: "shared" }),
    entries: [{ path: "~/.agents/skills/skill", present: true, symlink: false, target: null, tools: ["cursor", "codex"], mark: "shared" }],
    install_path: null,
    capability_gate: null,
    bucket: null,
    tags: [],
    baseline_debt: true,
    reason: "",
    quiet: true,
    command: "python3 skills-governance/scripts/drift_report.py --format text",
    ...overrides,
  };
}

function summary(items) {
  const sources = [
    ["all", "全部", ""],
    ["skills_cli_managed", "Skills CLI", "skills_cli_managed"],
    ["local_custom", "自建", "local_custom"],
    ["runtime_plugin", "运行时插件", "runtime_plugin"],
    ["runtime_builtin", "运行时内置", "runtime_builtin"],
    ["third_party_unmanaged", "第三方未托管", "third_party_unmanaged"],
    ["unrecorded", "未登记", "unrecorded"],
  ];
  return sources.map(([id, label, source]) => {
    const chosen = source ? items.filter((row) => row.source_class === source) : items;
    const statuses = {};
    for (const row of chosen) statuses[row.reconciliation_status] = (statuses[row.reconciliation_status] || 0) + 1;
    return { id, label, count: chosen.length, statuses };
  });
}

function attention(items) {
  const codes = ["missing", "version_changed", "unrecorded", "capability_pending", "modified", "upgradable", "overlap"];
  const buckets = {};
  for (const code of codes) {
    buckets[code] = {
      connected: code !== "upgradable",
      reason: code === "upgradable" ? "升级检查还没有数据源" : "",
      items: items
        .filter((row) => row.bucket === code)
        .map((row) => ({ identifier: row.name, title: row.reason, status: code, time: when, source_class: row.source_class, tags: row.tags })),
    };
  }
  const counts = Object.fromEntries(codes.map((code) => [code, buckets[code].items.length]));
  return {
    generated_at: when,
    inventory_updated_at: "2026-10-16T03:50:00Z",
    inventory_sha: "abc1234",
    baseline_debt: items.filter((row) => row.baseline_debt).length,
    review_days: 90,
    badge: Object.values(counts).reduce((sum, n) => sum + n, 0),
    counts,
    buckets,
  };
}

function envelope(items, sources) {
  return {
    ok: true,
    error: null,
    generated_at: when,
    items,
    summary: summary(items),
    tools,
    attention: attention(items),
    meta: {
      inventory_updated_at: "2026-10-16T03:50:00Z",
      inventory_sha: "abc1234",
      drift_scanned_at: when,
      review_days: 90,
      sources,
      staging: { state: "ok", detail: "未激活 0", pending: 0 },
    },
  };
}

const sourceBar = [
  { id: "inventory", label: "清单", state: "ok", detail: "abc1234" },
  { id: "drift", label: "漂移扫描", state: "ok", detail: "刚刚" },
  { id: "skill_lock", label: "SkilLock", state: "fallback", detail: "本机未安装，门禁回退到风险信号" },
  { id: "staging", label: "暂存区", state: "ok", detail: "未激活 0" },
  { id: "upgrade", label: "升级检查", state: "absent", detail: "未接入" },
  { id: "overlap", label: "重叠检测", state: "absent", detail: "未接入，只读 conflict_status" },
];

function debtRows(count) {
  const classes = [
    ["skills_cli_managed", "Skills CLI", 37],
    ["local_custom", "自建", 19],
    ["runtime_plugin", "运行时插件", 13],
    ["runtime_builtin", "运行时内置", 4],
    ["third_party_unmanaged", "第三方未托管", 3],
  ];
  const rows = [];
  let n = 0;
  for (const [source, label, take] of classes) {
    for (let i = 0; i < take && rows.length < count; i += 1) {
      n += 1;
      rows.push(
        item({
          name: `baseline-${String(n).padStart(2, "0")}`,
          source_class: source,
          source_label: label,
          channel_label: label,
          channel_detail: source === "local_custom" ? `skills/baseline-${n}` : `github:example/baseline-${n}`,
          scope: source === "runtime_plugin" ? "plugin-managed" : "machine",
        }),
      );
    }
  }
  while (rows.length < count) {
    n += 1;
    rows.push(item({ name: `baseline-${String(n).padStart(2, "0")}` }));
  }
  return rows.slice(0, count);
}

const changed = [
  ["codex-plugin-nowledge-mem", "0.1.40", "0.1.41"],
  ["codex-plugin-record-and-replay", "1.0.1001425", "1.0.1001501"],
  ["codex-plugin-visualize", "1.0.49", "1.0.50"],
].map(([name, current, observed]) =>
  item({
    name,
    source_class: "runtime_plugin",
    source_label: "运行时插件",
    scope: "plugin-managed",
    channel_label: "运行时插件",
    channel_detail: `codex/${name.replace("codex-plugin-", "")}@openai-bundled`,
    current_version: current,
    observed_version: observed,
    reconciliation_status: "version_changed",
    status_label: "版本已变",
    findings: [{ code: "version_changed", label: "版本目录已换成同级新版本" }],
    tools: marks({ codex: "missing" }),
    entries: [{ path: `~/.codex/plugins/cache/openai-bundled/${name}/${current}/skills`, present: false, symlink: false, target: null, tools: ["codex"], mark: "missing" }],
    bucket: "version_changed",
    baseline_debt: false,
    quiet: false,
    reason: `${current} → ${observed}`,
  }),
);

const unrecorded = item({
  name: "colgrep-search",
  origin: "unrecorded",
  source_class: "unrecorded",
  source_label: "未登记",
  channel_label: "未登记",
  channel_detail: "~/.agents/skills/colgrep-search",
  install_path: "~/.agents/skills/colgrep-search",
  current_version: null,
  observed_version: null,
  reconciliation_status: "missing",
  status_label: "未登记",
  findings: [{ code: "unrecorded", label: "清单里没有这条安装" }],
  bucket: "unrecorded",
  baseline_debt: false,
  quiet: false,
  reason: "~/.agents/skills/colgrep-search",
});

const today = envelope([...changed, unrecorded, ...debtRows(76)], sourceBar);

function write(name, payload) {
  const path = name.includes("/") ? join(root, name) : join(root, name);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(payload, null, 2) + "\n");
}

write("skills.json", today);

write(
  "skills-missing/skills.json",
  envelope(
    [
      item({
        name: "broken-link",
        source_class: "local_custom",
        source_label: "自建",
        channel_label: "自建",
        channel_detail: "skills/broken-link",
        reconciliation_status: "missing",
        status_label: "缺失",
        bucket: "missing",
        baseline_debt: false,
        quiet: false,
        reason: "记录的路径不在",
        tools: marks({ project: "missing" }),
        findings: [{ code: "source_missing", label: "源头文件不在" }],
      }),
    ],
    sourceBar,
  ),
);

write(
  "skills-drift/skills.json",
  envelope(
    [
      item({
        name: "edited-skill",
        reconciliation_status: "modified",
        status_label: "已修改",
        bucket: "modified",
        baseline_debt: false,
        quiet: false,
        reason: "内容或链接和基线不一致",
        findings: [{ code: "content_hash_mismatch", label: "内容哈希和基线不一致" }],
      }),
    ],
    sourceBar,
  ),
);

write(
  "skills-gate/skills.json",
  envelope(
    [
      item({
        name: "writing-plans",
        bucket: "capability_pending",
        baseline_debt: false,
        quiet: false,
        reason: "能力变化被拦住",
        reconciliation_status: "aligned",
        status_label: "一致",
        capability_gate: { backend: "skil-lock", blocked: true, reason: "BLOCK: network" },
      }),
    ],
    sourceBar.map((source) => (source.id === "skill_lock" ? { ...source, state: "ok", detail: "已安装" } : source)),
  ),
);

write(
  "skills-fallback/skills.json",
  envelope(
    [
      item({
        name: "writing-plans",
        bucket: "capability_pending",
        baseline_debt: false,
        quiet: false,
        reason: "门禁走了回退，不能当成通过",
        reconciliation_status: "aligned",
        status_label: "一致",
        capability_gate: { backend: "fallback", blocked: false, reason: "skil-lock is not on PATH" },
      }),
    ],
    sourceBar,
  ),
);

write("skills-invalid/skills.json", {
  ok: false,
  error: { code: "inventory_invalid", message: "skills inventory has no skills array" },
  generated_at: when,
  items: [],
  summary: [],
  tools,
  attention: attention([]),
  meta: { sources: [{ id: "inventory", label: "清单", state: "bad", detail: "skills inventory has no skills array" }] },
});

console.log(`skills fixtures: today badge ${today.attention.badge}, debt ${today.attention.baseline_debt}, items ${today.items.length}`);
