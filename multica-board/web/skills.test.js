import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { badgeOf, cloudCommands, cloudDrift, cloudPending, filteredItems, queueModel, renderSkills, renderSkillDrawer, sharedEntryOf, sharedSummary, skillsRefreshKey } from "./skills.js";

const today = JSON.parse(readFileSync(new URL("./fixtures/skills.json", import.meta.url), "utf8"));

test("today fixture: queue count equals attention badge and debt stays out", () => {
  assert.equal(today.attention.baseline_debt, 76);
  assert.equal(badgeOf(today), 4);
  const queued = queueModel(today).reduce((sum, bucket) => sum + bucket.items.length, 0);
  assert.equal(queued, today.attention.badge);
  assert.equal(today.attention.counts.version_changed, 3);
  assert.equal(today.attention.counts.unrecorded, 1);
  assert.ok(today.items.filter((item) => item.baseline_debt).every((item) => !item.bucket));
  assert.equal(filteredItems(today, { debt: true, q: "", source: "", skillStatus: "", tool: "", custom: false }).length, 76);
});

test("skills reload when the local payload changes and recover after a failure", () => {
  const before = { ok: true, generated_at: "t0", error: null, attention: { badge: 1, baseline_debt: 2 } };
  assert.equal(skillsRefreshKey(before), skillsRefreshKey({ ...before }));
  assert.notEqual(skillsRefreshKey(before), skillsRefreshKey({ ...before, generated_at: "t1" }));
  const failed = { ok: false, error: { code: "skills_unavailable", message: "down" }, attention: { badge: 0, baseline_debt: 0 } };
  const recovered = { ok: true, generated_at: "t2", error: null, attention: { badge: 0, baseline_debt: 0 } };
  assert.notEqual(skillsRefreshKey(failed), skillsRefreshKey(recovered));
});

function vnode(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children: children.flat(Infinity).filter((child) => child != null && child !== false) };
}

test("recursive installations show category, package provenance and both same-name entries", () => {
  const item = {
    name: "computer-use", entries: [
      { path: "/skills/computer-use", present: true, scope: "user", symlink: true, target: "../../shared/computer-use", resolved: "/shared/computer-use" },
      { path: "/skills/autonomous-ai-agents/computer-use", present: true, scope: "user", category: "autonomous-ai-agents", package: "hermes-builtin-skills", registration: "bundle_member", manifest_path: "/skills/.bundled_manifest", provider_path: "/hermes-agent/skills/autonomous-ai-agents/computer-use/SKILL.md" },
      { path: "/skills/user-added", present: true, scope: "user", package: "hermes-builtin-skills", registration: "unrecorded" },
    ],
  };
  const nodes = renderSkillDrawer({ h: vnode, closeDrawer() {}, absTime: (value) => value }, item);
  const text = flatten({ children: nodes }).filter((node) => typeof node === "string").join("\n");
  for (const expected of ["/skills/computer-use", "/skills/autonomous-ai-agents/computer-use", "../../shared/computer-use", "分类 autonomous-ai-agents", "hermes-builtin-skills", "/skills/.bundled_manifest", "内容未校验", "来源未核实", "未验证 runtime 当前会话实际加载"]) {
    assert.ok(text.includes(expected), expected);
  }
});

function rowClasses(node, found = []) {
  if (!node || typeof node !== "object") return found;
  if (typeof node.attrs?.class === "string" && node.attrs.class.includes("skill-row")) found.push(node.attrs.class);
  for (const child of node.children || []) rowClasses(child, found);
  return found;
}

test("opening a skill selects its row and closing the drawer clears it", () => {
  const payload = {
    ok: true,
    summary: [{ id: "all", label: "全部", count: 2, statuses: {} }],
    tools: [{ id: "codex", label: "Codex" }],
    items: [
      { name: "alpha", source_class: "local_custom", source_label: "自建", tools: {}, reconciliation_status: "stale", status_label: "未复核" },
      { name: "beta", source_class: "local_custom", source_label: "自建", tools: {}, reconciliation_status: "stale", status_label: "未复核" },
    ],
    attention: { badge: 0, baseline_debt: 0, counts: {}, buckets: {} },
  };
  const ctx = {
    h: vnode,
    route: { q: "", source: "", skillStatus: "", tool: "", custom: false, debt: false, skillGroup: "flat" },
    skills: payload,
    expandedSkills: new Set(["local_custom", "all"]),
    setRoute() {},
    openSkill() {},
    focusSkill() {},
    toggleSkillGroup() {},
    setReviewDays() {},
    ago: () => "刚刚",
  };
  const open = rowClasses({ children: renderSkills({ ...ctx, drawerSkill: "beta" }) });
  assert.deepEqual(
    open.filter((cls) => cls.includes("selected")),
    ["skill-row selected"],
  );
  assert.equal(open.filter((cls) => cls.includes("selected")).length, 1);
  const closed = rowClasses({ children: renderSkills({ ...ctx, drawerSkill: "" }) });
  assert.equal(closed.some((cls) => cls.includes("selected")), false);

  const app = readFileSync(new URL("./app.js", import.meta.url), "utf8");
  const openSkill = app.slice(app.indexOf("function openSkill"), app.indexOf("function focusSkill"));
  assert.match(openSkill, /render\(\)/);
  const closeDrawer = app.slice(app.indexOf("function closeDrawer"), app.indexOf("function runRow"));
  assert.match(closeDrawer, /kind === "skill"/);
  assert.match(closeDrawer, /renderMain\(\)/);
  const refresh = app.slice(app.indexOf("async function refresh"), app.indexOf("function now("));
  const beforeBranch = refresh.slice(0, refresh.indexOf("if (changed)"));
  assert.match(beforeBranch, /loadSkills\(\)/);
});

test("upgradable bucket is present but not connected", () => {
  const upgrade = queueModel(today).find((bucket) => bucket.id === "upgradable");
  assert.equal(upgrade.connected, false);
  assert.equal(upgrade.items.length, 0);
});

function flatten(node, found = []) {
  if (node == null || node === false) return found;
  if (typeof node === "string") {
    found.push(node);
    return found;
  }
  found.push(node);
  for (const child of node.children || []) flatten(child, found);
  return found;
}

function findAll(node, pred, found = []) {
  for (const item of flatten(node)) {
    if (typeof item === "object" && pred(item)) found.push(item);
  }
  return found;
}

function findText(node, needle) {
  return flatten(node).some((item) => typeof item === "string" && item.includes(needle));
}

function renderCtx(payload, extra = {}) {
  return {
    h: vnode,
    route: { q: "", source: "", skillStatus: "", tool: "", custom: false, debt: false, skillGroup: "flat" },
    skills: payload,
    expandedSkills: new Set(["all"]),
    setRoute() {},
    openSkill() {},
    focusSkill() {},
    toggleSkillGroup() {},
    setReviewDays() {},
    ago: () => "刚刚",
    absTime: (v) => v,
    closeDrawer() {},
    ...extra,
  };
}

function basePayload(items, tools) {
  return {
    ok: true,
    skills_schema: 2,
    summary: [{ id: "all", label: "全部", count: items.length, statuses: {} }],
    tools: tools || [
      { id: "codex", label: "Codex" },
      { id: "hermes", label: "Hermes" },
      { id: "project", label: "仓库入口" },
    ],
    items,
    repos: [{ id: "/repo", label: "myrepo", path: "/repo" }],
    shared_root: { path: "~/.agents/skills", state: "ok", detail: "" },
    attention: { badge: 0, baseline_debt: 0, counts: {}, buckets: {} },
  };
}

function simpleItem(overrides) {
  return {
    name: "alpha",
    source_class: "local_custom",
    source_label: "自建",
    tools: {},
    entries: [],
    reconciliation_status: "stale",
    status_label: "未复核",
    ...overrides,
  };
}

test("column order: 共享入口 before harness columns, 仓库入口 is the last tool column", () => {
  const payload = basePayload([simpleItem({})]);
  const head = findAll({ children: renderSkills(renderCtx(payload)) }, (n) => n.tag === "th" && n.children?.length);
  const texts = head.map((n) => flatten(n).filter((c) => typeof c === "string").join(""));
  const sharedIdx = texts.findIndex((t) => t === "共享入口");
  const codexIdx = texts.findIndex((t) => t.startsWith("Codex"));
  const repoIdx = texts.findIndex((t) => t.startsWith("仓库入口"));
  const versionIdx = texts.findIndex((t) => t.startsWith("版本"));
  assert.ok(sharedIdx > -1 && codexIdx > -1 && repoIdx > -1, texts.join("|"));
  assert.ok(sharedIdx < codexIdx);
  assert.ok(repoIdx > codexIdx && repoIdx < versionIdx);
});

test("shared entry tag label and class follow the state, missing field reads 未扫描", () => {
  const cases = [
    [{ state: "dir", label: "目录" }, "目录", "ok"],
    [{ state: "link", label: "链接" }, "链接", "ok"],
    [{ state: "none", label: "无入口" }, "无入口", "muted"],
    [{ state: "broken_link", label: "坏链" }, "坏链", "bad"],
    [{ state: "content_missing", label: "缺 SKILL.md" }, "缺 SKILL.md", "bad"],
    [{ state: "denied", label: "无权限" }, "无权限", "warn"],
    [{ state: "unknown", label: "无法确定" }, "无法确定", "warn"],
    [{ state: "unscanned", label: "未扫描" }, "未扫描", "info"],
    [undefined, "无法确定", "warn"],
  ];
  for (const [entry, label, cls] of cases) {
    const item = simpleItem(entry ? { name: `s-${entry.state}`, shared_entry: entry } : { name: "s-missing" });
    const tree = { children: renderSkills(renderCtx(basePayload([item]))) };
    const cell = findAll(tree, (n) => n.attrs?.class === "shared-entry")[0];
    assert.ok(cell, `cell for ${label}`);
    const tag = findAll(cell, (n) => String(n.attrs?.class || "").includes("tag"))[0];
    assert.equal(flatten(tag).filter((c) => typeof c === "string").join(""), label);
    assert.ok(tag.attrs.class.includes(cls), `${label}: ${tag.attrs.class}`);
  }
});

test("project cell groups entries by repo and shows location chips", () => {
  const item = simpleItem({
    entries: [
      { path: ".agents/skills/alpha", present: true, repo_id: "/repo", location: ".agents/skills", tools: ["project"], mark: "direct" },
      { path: ".cursor/skills/alpha", present: false, repo_id: "/repo", location: ".cursor/skills", tools: ["project"], mark: "missing" },
      { path: "/other/.agents/skills/alpha", present: true, repo_id: "/other", location: ".agents/skills", tools: [], mark: null },
    ],
  });
  const payload = basePayload([item]);
  const tree = { children: renderSkills(renderCtx(payload)) };
  const cell = findAll(tree, (n) => n.attrs?.class === "repo-entries")[0];
  const groups = findAll(cell, (n) => n.attrs?.class === "repo-group");
  assert.equal(groups.length, 2);
  assert.ok(findText(groups[0], "myrepo"));
  const chips = findAll(cell, (n) => String(n.attrs?.class || "").includes("entry-chip"));
  const chipText = chips.map((chip) => flatten(chip).filter((c) => typeof c === "string").join(""));
  assert.ok(chipText.some((t) => t.includes(".agents") && t.includes("●")), chipText.join("|"));
  assert.ok(chipText.some((t) => t.includes(".cursor") && t.includes("✕")), chipText.join("|"));
  // fallback repo label for the unknown repo id
  assert.ok(findText(cell, "other"));
});

test("via-target evidence adds a sup marker and via-target class", () => {
  const item = simpleItem({
    tools: { hermes: "direct", codex: "shared" },
    entries: [
      {
        path: "/h/skills/alpha",
        present: true,
        tools: ["hermes"],
        mark: "direct",
        location: "/h/skills",
        resolved: "/shared/alpha",
      },
    ],
    evidence: [
      { harness: "codex", kind: "target", attribution: "shared", state: "present", path: "/h/skills/alpha", lexical: "/h/skills/alpha", readlink: "/shared/alpha", resolved: "/shared/alpha", root: "/shared", repo: false, repo_id: null, scope: "user", entry_index: 0 },
      { harness: "hermes", kind: "entry", attribution: "direct", state: "present", path: "/h/skills/alpha", lexical: "/h/skills/alpha", readlink: "/shared/alpha", resolved: "/shared/alpha", root: "/h/skills", repo: false, repo_id: null, scope: "user", entry_index: 0 },
    ],
  });
  const payload = basePayload([item], [
    { id: "hermes", label: "Hermes" },
    { id: "codex", label: "Codex" },
  ]);
  const tree = { children: renderSkills(renderCtx(payload)) };
  const codexCell = findAll(tree, (n) => String(n.attrs?.class || "").includes("via-target"))[0];
  assert.ok(codexCell, "via-target cell");
  assert.ok(findAll(codexCell, (n) => n.tag === "sup").length === 1);
  assert.ok(findText(codexCell, "链"));
  assert.match(codexCell.attrs.title, /路径推导，未验证 runtime 实际加载/);
  assert.match(codexCell.attrs.title, /链接目标 \/shared\/alpha/);
  const hermesCell = findAll(tree, (n) => String(n.attrs?.class || "").startsWith("tool-mark") && n !== codexCell)[0];
  assert.match(hermesCell.attrs.title, /入口原路径 \/h\/skills\/alpha/);
});

test("legend is present and anomaly banner only shows for abnormal items", () => {
  const clean = { children: renderSkills(renderCtx(basePayload([simpleItem({ shared_entry: { state: "dir", label: "目录" } })]))) };
  assert.ok(findText(clean, "共享入口"));
  assert.ok(findText(clean, "经共享目录"));
  assert.equal(findAll(clean, (n) => n.attrs?.class === "skills-anomaly").length, 0);
  const dirty = basePayload([
    simpleItem({ name: "a", shared_entry: { state: "broken_link", label: "坏链" } }),
    simpleItem({ name: "b", shared_entry: { state: "denied", label: "无权限" } }),
    simpleItem({ name: "c" }),
    simpleItem({ name: "d", shared_entry: { state: "unscanned", label: "未扫描" } }),
  ]);
  const tree = { children: renderSkills(renderCtx(dirty)) };
  const banner = findAll(tree, (n) => n.attrs?.class === "skills-anomaly")[0];
  assert.ok(banner, "banner");
  assert.equal(banner.attrs.role, "status");
  const text = flatten(banner).filter((c) => typeof c === "string").join("");
  assert.match(text, /共 4 项/);
  assert.match(text, /坏链 1/);
  assert.match(text, /无权限 1/);
  assert.match(text, /无法确定 1/);
  assert.match(text, /未扫描 1（≠ 无入口）/);
});

test("banner shows a root-level line when shared_root is not ok", () => {
  const payload = basePayload([simpleItem({ shared_entry: { state: "none", label: "无入口" } })]);
  payload.shared_root = { path: "/gone", state: "absent", reason: "root_absent", detail: "共享根目录不存在" };
  const tree = { children: renderSkills(renderCtx(payload)) };
  const banner = findAll(tree, (n) => n.attrs?.class === "skills-anomaly")[0];
  assert.ok(banner, "root banner");
  assert.ok(findText(banner, "共享根目录不存在：以下全部显示无入口"));
});

test("schema 2 payload with missing or garbage shared_entry is unknown/field_invalid", () => {
  for (const entry of [undefined, null, "junk", { state: "not-a-state" }]) {
    const item = simpleItem({ shared_entry: entry });
    const resolved = sharedEntryOf(item, basePayload([item]));
    assert.equal(resolved.state, "unknown", JSON.stringify(entry));
    assert.equal(resolved.reason, "field_invalid");
    assert.equal(resolved.label, "无法确定");
  }
});

test("sharedSummary counts every item regardless of filters", () => {
  const payload = basePayload([
    simpleItem({ name: "a", shared_entry: { state: "dir", label: "目录" } }),
    simpleItem({ name: "b", shared_entry: { state: "none", label: "无入口" } }),
    simpleItem({ name: "c", shared_entry: { state: "unscanned", label: "未扫描" } }),
    simpleItem({ name: "d" }),
  ]);
  assert.deepEqual(sharedSummary(payload), { dir: 1, none: 1, unscanned: 1, unknown: 1 });
  // Filtering to one item must not shrink the summary: it always walks payload.items.
  const filtered = filteredItems(payload, { q: "a", source: "", skillStatus: "", tool: "", custom: false, debt: false });
  assert.equal(filtered.length, 1);
  assert.deepEqual(sharedSummary(payload).dir, 1);
});

test("list tag label equals drawer label for every state", () => {
  const states = ["dir", "link", "none", "broken_link", "content_missing", "denied", "unknown", "unscanned"];
  const labels = { dir: "目录", link: "链接", none: "无入口", broken_link: "坏链", content_missing: "缺 SKILL.md", denied: "无权限", unknown: "无法确定", unscanned: "未扫描" };
  for (const state of states) {
    const item = simpleItem({ shared_entry: { state, label: labels[state] } });
    const payload = basePayload([item]);
    const listTag = findAll({ children: renderSkills(renderCtx(payload)) }, (n) => n.attrs?.class === "shared-entry")[0];
    const listLabel = flatten(listTag).filter((c) => typeof c === "string").join("");
    const drawer = { children: renderSkillDrawer(renderCtx(payload), item) };
    const drawerSection = findAll(drawer, (n) => String(n.attrs?.class || "").includes("tag")).find((n) => flatten(n).includes(labels[state]));
    assert.ok(drawerSection, `drawer label for ${state}`);
    assert.equal(listLabel, labels[state]);
    // and it is the same bucket sharedSummary reports
    assert.equal(sharedSummary(payload)[state], 1);
  }
});

test("unknown mark renders ? and all evidence records appear in title", () => {
  const item = simpleItem({
    tools: { hermes: "unknown" },
    evidence: [
      { harness: "hermes", kind: "entry", attribution: "unknown", state: "denied", path: "/h/skills/alpha", root: "/h/skills", entry_index: 0 },
      { harness: "codex", kind: "target", attribution: "shared", state: "present", path: "/h/skills/alpha", resolved: "/shared/alpha", root: "/shared", entry_index: 0 },
    ],
  });
  const payload = basePayload([item], [
    { id: "hermes", label: "Hermes" },
    { id: "codex", label: "Codex" },
  ]);
  const tree = { children: renderSkills(renderCtx(payload)) };
  const cells = findAll(tree, (n) => String(n.attrs?.class || "").startsWith("tool-mark"));
  const hermesCell = cells.find((n) => flatten(n).includes("?"));
  assert.ok(hermesCell, "? cell");
  assert.match(hermesCell.attrs.title, /路径无法确定/);
  // via-target only when ALL of a harness's evidence is target-kind: codex qualifies.
  const codexCell = cells.find((n) => String(n.attrs?.class || "").includes("via-target"));
  assert.ok(codexCell, "codex via-target");
  assert.match(codexCell.attrs.title, /链接目标 \/shared\/alpha/);
});

test("repo chips show 未知仓库 for unknown repo identity", () => {
  const item = simpleItem({
    entries: [{ path: ".agents/skills/alpha", present: true, repo_id: "unknown", location: ".agents/skills", tools: ["project"], mark: "direct", scope: "repo_entry" }],
  });
  const payload = basePayload([item]);
  payload.repos = [{ id: "unknown", label: "未知仓库", path: "/x", identity_state: "unknown", reason: "repo_root_absent" }];
  const tree = { children: renderSkills(renderCtx(payload)) };
  const cell = findAll(tree, (n) => n.attrs?.class === "repo-entries")[0];
  assert.ok(findText(cell, "未知仓库"));
});

test("drawer shows 共享入口 section and splits 仓库入口 from 用户级入口", () => {
  const item = simpleItem({
    shared_entry: { state: "link", label: "链接", path: "~/.agents/skills/alpha", target: "/t/alpha", resolved: "/t/alpha", detail: "链接到共享目录" },
    entries: [
      { path: ".agents/skills/alpha", present: true, repo_id: "/repo", location: ".agents/skills", tools: ["project", "hermes"], mark: "direct", scope: "repo_entry" },
      { path: "skills/alpha-src", present: true, repo_id: "/repo", location: null, tools: [], mark: null, scope: "repo_source" },
      { path: "~/.hermes/skills/alpha", present: true, location: "~/.hermes/skills", tools: ["hermes"], target: "/t/alpha", resolved: "/t/alpha", symlink: true, mark: "direct", scope: "user" },
      { path: "/other/skills/alpha", present: true, location: null, tools: [], mark: null, scope: "other" },
    ],
    evidence: [
      { harness: "hermes", kind: "entry", attribution: "direct", state: "present", path: "~/.hermes/skills/alpha", lexical: "~/.hermes/skills/alpha", readlink: "/t/alpha", resolved: "/t/alpha", root: "~/.hermes/skills", repo: false, repo_id: null, scope: "user", entry_index: 2 },
      { harness: "codex", kind: "target", attribution: "shared", state: "present", path: "~/.hermes/skills/alpha", lexical: "~/.hermes/skills/alpha", readlink: "/t/alpha", resolved: "/t/alpha", root: "~/.agents/skills", repo: false, repo_id: null, scope: "user", entry_index: 2 },
    ],
  });
  const payload = basePayload([item]);
  const tree = { children: renderSkillDrawer(renderCtx(payload), item) };
  assert.ok(findText(tree, "共享入口"));
  assert.ok(findText(tree, "仓库入口"));
  assert.ok(findText(tree, "仓库源目录"));
  assert.ok(findText(tree, "用户级入口"));
  assert.ok(findText(tree, "其他位置"));
  assert.ok(findText(tree, "链接到共享目录"));
  assert.ok(findText(tree, "myrepo"));
  assert.ok(findText(tree, "hermes（原路径）"));
  assert.ok(findText(tree, "codex（链接目标）"));
});

test("render does not throw on an old payload without the new fields", () => {
  const ctx = renderCtx(today);
  let tree;
  assert.doesNotThrow(() => (tree = { children: renderSkills(ctx) }));
  const item = today.items[0];
  assert.doesNotThrow(() => renderSkillDrawer(ctx, item));
  // old payload has no skills_schema: every item is legacy, never counted as 未扫描.
  assert.equal(sharedEntryOf(item, today).state, "legacy");
  assert.equal(sharedEntryOf(item, today).label, "旧数据");
  const summary = sharedSummary(today);
  assert.equal(summary.legacy, today.items.length);
  assert.equal(summary.unscanned || 0, 0);
  const banner = findAll(tree, (n) => n.attrs?.class === "skills-anomaly")[0];
  assert.ok(banner, "legacy banner");
  assert.ok(findText(banner, "后端数据版本较旧，共享入口与仓库入口未检查"));
  // legacy is not folded into the anomaly counts.
  assert.equal(findText(banner, "未扫描"), false);
});

test("duplicated entries render once in repoCell chips and drawer sections", () => {
  const entry = { path: ".agents/skills/alpha", present: true, repo_id: "/repo", location: ".agents/skills", tools: ["project"], mark: "direct", scope: "repo_entry" };
  const userEntry = { path: "~/.codex/skills/alpha", present: true, location: "~/.codex/skills", tools: ["codex"], mark: "direct", scope: "user" };
  const item = simpleItem({ entries: [entry, { ...entry }, { ...entry }, userEntry, { ...userEntry }] });
  const payload = basePayload([item]);
  const tree = { children: renderSkills(renderCtx(payload)) };
  const chips = findAll(tree, (n) => String(n.attrs?.class || "").includes("entry-chip"));
  assert.equal(chips.length, 1);
  const drawer = { children: renderSkillDrawer(renderCtx(payload), item) };
  const rows = findAll(drawer, (n) => String(n.attrs?.class || "") === "skill-entry");
  assert.equal(rows.length, 2);
  assert.equal(rows.filter((n) => findText(n, ".agents/skills/alpha")).length, 1);
  assert.equal(rows.filter((n) => findText(n, "~/.codex/skills/alpha")).length, 1);
});

test("same-name entries in two repos keep separate chips and drawer rows", () => {
  const item = simpleItem({
    entries: [
      { path: ".agents/skills/alpha", present: true, repo_id: "/repo", location: ".agents/skills", tools: ["project"], mark: "direct", scope: "repo_entry" },
      { path: ".agents/skills/alpha", present: true, repo_id: "/repo2", location: ".agents/skills", tools: ["project"], mark: "direct", scope: "repo_entry" },
    ],
  });
  const payload = basePayload([item]);
  payload.repos = [
    { id: "/repo", label: "one", path: "/repo" },
    { id: "/repo2", label: "two", path: "/repo2" },
  ];
  const tree = { children: renderSkills(renderCtx(payload)) };
  const chips = findAll(tree, (n) => String(n.attrs?.class || "").includes("entry-chip"));
  assert.equal(chips.length, 2);
  assert.ok(findText(tree, "one"));
  assert.ok(findText(tree, "two"));
  const rows = findAll({ children: renderSkillDrawer(renderCtx(payload), item) }, (n) => String(n.attrs?.class || "") === "skill-entry");
  assert.equal(rows.length, 2);
});

test("renderSkills and renderSkillDrawer never emit null members for replaceChildren", () => {
  const payloads = [
    basePayload([simpleItem({ shared_entry: { state: "dir", label: "目录" } })]), // healthy: no banner at all
    basePayload([simpleItem({ shared_entry: { state: "none", label: "无入口" }, name: "b" })]),
    today, // legacy fixture
    { ...basePayload([simpleItem({ shared_entry: { state: "none", label: "无入口" } })]), shared_root: { path: "/gone", state: "absent", reason: "root_absent", detail: "x" } },
  ];
  for (const payload of payloads) {
    const parts = renderSkills(renderCtx(payload));
    assert.ok(parts.every((part) => part != null && typeof part === "object"), `null member for ${payload.shared_root?.state}/${payload.skills_schema}`);
  }
  const item = simpleItem({ shared_entry: { state: "dir", label: "目录" } });
  const drawerParts = renderSkillDrawer(renderCtx(basePayload([item])), item);
  assert.ok(drawerParts.every((part) => part != null && typeof part === "object"));
});

function cloudItem(overrides = {}) {
  return simpleItem({
    name: "colgrep-search",
    local_source: {
      path: "skills/colgrep-search/SKILL.md",
      updated_at: "2026-10-20T01:00:00Z",
      summary: "Use when a coding agent needs to find code by behavior or meaning.",
    },
    workspace_installs: [
      {
        workspace_id: "ws-acm",
        workspace_slug: "acm-lab",
        workspace_name: "ACM Lab",
        id: "skill-acm",
        name: "colgrep-search",
        description: "Use when a coding agent needs to find code by behavior or meaning.",
        updated_at: "2026-10-16T08:08:13Z",
        agents: [
          { id: "agent-a", name: "前端实现", enabled: true },
          { id: "agent-b", name: "调度", enabled: false },
        ],
      },
      {
        workspace_id: "ws-clip",
        workspace_slug: "clipvibe",
        workspace_name: "ClipVibe",
        id: "skill-clip",
        name: "colgrep-search",
        description: "Use when a coding agent needs to find code by behavior or meaning.",
        updated_at: "2026-10-16T08:08:13Z",
        agents: [],
      },
    ],
    ...overrides,
  });
}

test("cloud column lists workspaces and enabled agents, and omits the column data when the API has none", () => {
  const payload = basePayload([cloudItem(), simpleItem({ name: "plain" })]);
  const tree = { children: renderSkills(renderCtx(payload)) };
  const cells = findAll(tree, (n) => n.attrs?.class === "cloud-cell");
  assert.equal(cells.length, 2);
  assert.ok(findText(cells[0], "ACM Lab"));
  assert.ok(findText(cells[0], "前端实现"));
  assert.ok(findText(cells[0], "ClipVibe"));
  assert.ok(findText(cells[0], "未挂载"));
  assert.ok(findText(cells[1], "—"));
  assert.equal(findText(cells[1], "未安装"), false);
  const empty = { children: renderSkills(renderCtx(basePayload([simpleItem({ workspace_installs: [] })]))) };
  const emptyCell = findAll(empty, (n) => n.attrs?.class === "cloud-cell")[0];
  assert.ok(findText(emptyCell, "未安装"));
});

test("local skills directory newer than the cloud copy enters the pending queue", () => {
  const stale = cloudItem();
  const fresh = cloudItem({
    name: "fresh-skill",
    local_source: { path: "skills/fresh-skill/SKILL.md", updated_at: "2026-10-01T00:00:00Z", summary: "same" },
    workspace_installs: [
      {
        workspace_id: "ws-acm",
        workspace_name: "ACM Lab",
        id: "skill-fresh",
        updated_at: "2026-10-18T00:00:00Z",
        description: "same",
        agents: [{ id: "agent-a", name: "前端实现", enabled: true }],
      },
    ],
  });
  const summaryOnly = cloudItem({
    name: "summary-skill",
    local_source: { path: "skills/summary-skill/SKILL.md", summary: "local wording" },
    workspace_installs: [
      {
        workspace_id: "ws-acm",
        workspace_name: "ACM Lab",
        id: "skill-summary",
        description: "cloud wording",
        agents: [],
      },
    ],
  });
  assert.ok(cloudDrift(stale));
  assert.equal(cloudDrift(fresh), null);
  assert.ok(cloudDrift(summaryOnly));
  const payload = basePayload([stale, fresh, summaryOnly]);
  assert.deepEqual(cloudPending(payload).map((item) => item.name), ["colgrep-search", "summary-skill"]);
  const tree = { children: renderSkills(renderCtx(payload)) };
  const queue = findAll(tree, (n) => n.attrs?.class === "skill-bucket cloud-pending")[0];
  assert.ok(findText(queue, "云端未刷新"));
  assert.ok(findText(queue, "colgrep-search"));
  assert.ok(findText(queue, "本机 skills/ 新于云端"));
  assert.equal(findText(queue, "fresh-skill"), false);
  const row = findAll(tree, (n) => n.attrs?.id === "skill-colgrep-search")[0];
  assert.ok(findText(row, "云端较旧"));
});

test("Grok CLI symlinks under ~/.grok/skills render as a filled circle", () => {
  const item = simpleItem({
    name: "computer-use",
    tools: { grok: "shared" },
    entries: [
      {
        path: "/Users/tr/.grok/skills/computer-use",
        present: true,
        symlink: true,
        target: "../../.agents/skills/computer-use",
        tools: ["grok"],
        mark: "shared",
        location: "~/.grok/skills",
      },
    ],
    evidence: [
      {
        harness: "grok",
        kind: "entry",
        attribution: "direct",
        state: "present",
        path: "/Users/tr/.grok/skills/computer-use",
        lexical: "/Users/tr/.grok/skills/computer-use",
        readlink: "../../.agents/skills/computer-use",
        root: "~/.grok/skills",
      },
    ],
  });
  const payload = basePayload([item], [{ id: "grok", label: "Grok CLI" }]);
  const tree = { children: renderSkills(renderCtx(payload)) };
  const cell = findAll(tree, (n) => String(n.attrs?.class || "").includes("tool-mark"))[0];
  assert.ok(findText(cell, "●"));
  assert.equal(findText(cell, "○"), false);
  assert.match(cell.attrs.class, /direct/);
});

test("skill drawer copies refresh, update, and agent skill add commands without running them", () => {
  const item = cloudItem();
  const tree = { children: renderSkillDrawer(renderCtx(basePayload([item])), item) };
  assert.ok(findText(tree, "Multica 云端（跨 Workspace）"));
  assert.ok(findText(tree, "multica --workspace-id ws-acm skill refresh skill-acm"));
  assert.ok(findText(tree, "multica --workspace-id ws-acm skill update skill-acm"));
  assert.ok(findText(tree, "multica --workspace-id ws-acm skill update skill-acm --content-file skills/colgrep-search/SKILL.md"));
  assert.ok(findText(tree, "multica --workspace-id ws-acm agent skills add agent-a --skill-ids skill-acm"));
  assert.ok(findText(tree, "multica --workspace-id ws-clip skill refresh skill-clip"));
  assert.ok(findText(tree, "已启用 前端实现"));
  assert.ok(findText(tree, "挂给 调度"));
  assert.ok(findText(tree, "页面只读，不代为执行"));
  const commands = cloudCommands(item, item.workspace_installs[0]).map((entry) => entry.command);
  assert.ok(commands.every((command) => !command.includes("&&") && !command.includes("|")));
  const src = readFileSync(new URL("./skills.js", import.meta.url), "utf8");
  assert.doesNotMatch(src, /innerHTML/);
});
