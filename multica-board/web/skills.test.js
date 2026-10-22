import { contentKey, loadSkillContent } from "./skill-content.js";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { badgeOf, CLOUD_SOURCE, cloudCommands, cloudDrift, cloudPending, filteredItems, groupedItems, queueModel, renderSkills, renderSkillDrawer, sharedEntryOf, sharedSummary, skillsRefreshKey, sourceBlocks } from "./skills.js";

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
  assert.ok(findText(queue, "Multica Workspace 待核对"));
  assert.ok(findText(queue, "colgrep-search"));
  assert.ok(findText(queue, "Multica Workspace 较旧（按时间推断）"));
  assert.equal(findText(queue, "fresh-skill"), false);
  const row = findAll(tree, (n) => n.attrs?.id === "skill-colgrep-search")[0];
  assert.ok(findText(row, "Multica Workspace 较旧"));
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
  assert.ok(findText(tree, "Multica Workspace（跨 Workspace）"));
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

const baseRoute = { q: "", source: "", skillStatus: "", tool: "", custom: false, debt: false, skillGroup: "source" };

test("cloud source filters on workspace_installs, never on source_class", () => {
  const cloud = cloudItem();
  const plain = simpleItem({ name: "plain" });
  const emptyInstalls = simpleItem({ name: "empty-installs", workspace_installs: [] });
  const unrecorded = simpleItem({ name: "third", source_class: "unrecorded", source_label: "未登记", workspace_installs: [] });
  const payload = basePayload([cloud, plain, emptyInstalls, unrecorded]);
  const out = filteredItems(payload, { ...baseRoute, source: "cloud" });
  assert.deepEqual(out.map((item) => item.name), ["colgrep-search"]);
  // cloud intersects with the other filters
  assert.equal(filteredItems(payload, { ...baseRoute, source: "cloud", skillStatus: cloud.reconciliation_status }).length, 1);
  assert.equal(filteredItems(payload, { ...baseRoute, source: "cloud", skillStatus: "missing" }).length, 0);
  assert.deepEqual(filteredItems(payload, { ...baseRoute, source: "cloud", q: "没有匹配" }), []);
  const tooled = basePayload([cloudItem({ tools: { codex: "direct" } }), cloudItem({ name: "other-cloud" })]);
  assert.deepEqual(filteredItems(tooled, { ...baseRoute, source: "cloud", tool: "codex" }).map((item) => item.name), ["colgrep-search"]);
  // items without the array are excluded even when the route is not cloud-filtered by class
  assert.equal(filteredItems(payload, { ...baseRoute, source: "local_custom" }).some((item) => item.name === "third"), false);
  // grouping still uses source_class, so cloud items stay under their origin group
  const groups = groupedItems(out, "source", payload.tools);
  assert.deepEqual(groups.map((group) => group.id), ["local_custom"]);
});

test("sourceBlocks appends a synthesized cloud block matching filteredItems", () => {
  const items = [cloudItem({ reconciliation_status: "stale" }), cloudItem({ name: "b", reconciliation_status: "aligned", status_label: "一致" }), simpleItem({ name: "c" })];
  const payload = basePayload(items);
  const blocks = sourceBlocks(payload);
  const cloud = blocks[blocks.length - 1];
  assert.equal(cloud.id, "cloud");
  assert.equal(cloud.label, "Multica Workspace · 已安装");
  assert.equal(cloud.count, filteredItems(payload, { ...baseRoute, source: "cloud" }).length);
  assert.deepEqual(cloud.statuses, { stale: 1, aligned: 1 });
  assert.equal(cloud.uncollected, false);
  // no item carries the array at all ⇒ uncollected, and the card shows — not 0
  const never = basePayload([simpleItem({ name: "x" })]);
  const neverCloud = sourceBlocks(never).pop();
  assert.equal(neverCloud.uncollected, true);
  const card = findAll({ children: renderSkills(renderCtx(never)) }, (n) => String(n.attrs?.class || "").includes("source-card")).pop();
  assert.ok(findText(card, "Multica Workspace"));
  assert.ok(findText(card, "Multica Workspace 数据未采集"));
  const strong = findAll(card, (n) => n.tag === "strong")[0];
  assert.equal(flatten(strong).filter((c) => typeof c === "string").join(""), "—");
  // empty arrays do count as collected
  const collected = sourceBlocks(basePayload([simpleItem({ workspace_installs: [] })])).pop();
  assert.equal(collected.uncollected, false);
  assert.equal(collected.count, 0);
});

test("source dropdown contains Multica Workspace and the cloud card is a filter toggle", () => {
  const payload = basePayload([cloudItem()]);
  const tree = { children: renderSkills(renderCtx(payload, { route: { ...baseRoute, source: "cloud" } })) };
  const options = findAll(tree, (n) => n.tag === "option").map((n) => flatten(n).filter((c) => typeof c === "string").join(""));
  assert.ok(options.includes("Multica Workspace · 已安装"), options.join("|"));
  const card = findAll(tree, (n) => String(n.attrs?.class || "").includes("source-card")).pop();
  assert.match(card.attrs.class, /active/);
});

test("empty table text distinguishes uncollected cloud, empty cloud, and no match", () => {
  const route = { ...baseRoute, source: "cloud" };
  const never = { children: renderSkills(renderCtx(basePayload([simpleItem({})]), { route })) };
  assert.ok(findText(never, "Multica Workspace 安装数据未采集，无法筛选"));
  const empty = { children: renderSkills(renderCtx(basePayload([simpleItem({ workspace_installs: [] })]), { route })) };
  assert.ok(findText(empty, "没有已安装到 Multica Workspace 的 skill"));
  const nomatch = { children: renderSkills(renderCtx(basePayload([simpleItem({})]), { route: { ...baseRoute, q: "zzz" } })) };
  assert.ok(findText(nomatch, "没有符合筛选的 skill"));
});

test("skills table wrap is sticky-head and styles.css keeps the header sticky", () => {
  const tree = { children: renderSkills(renderCtx(basePayload([simpleItem({})]))) };
  const wrap = findAll(tree, (n) => String(n.attrs?.class || "") === "table-wrap sticky-head")[0];
  assert.ok(wrap, "sticky-head wrap");
  const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
  const block = css.slice(css.indexOf(".table-wrap.sticky-head"), css.indexOf("}", css.indexOf(".table-wrap.sticky-head")));
  assert.match(block, /max-height/);
  assert.match(block, /overflow:\s*auto/);
  const head = css.slice(css.indexOf(".skills-table thead th"), css.indexOf("}", css.indexOf(".skills-table thead th")));
  assert.match(head, /position:\s*sticky/);
  const group = css.slice(css.indexOf(".skill-group th"), css.indexOf("}", css.indexOf(".skill-group th")));
  assert.doesNotMatch(group, /sticky/);
  assert.match(group, /position:\s*static/);
});

function drawerCtx(payload, skillContent, extra = {}) {
  return renderCtx(payload, {
    skillContent,
    loadSkillContent() {},
    ...extra,
  });
}

test("drawer 正文 section renders the viewer for each state and keeps raw text as text", () => {
  const item = cloudItem();
  const path = item.local_source.path;
  const it = simpleItem({ name: "alpha", local_source: item.local_source, workspace_installs: item.workspace_installs });
  const payload = basePayload([it]);
  const states = [
    [{ state: "loading" }, "读取中"],
    [{ state: "unavailable", message: "后端未提供正文接口" }, "后端未提供正文接口"],
    [{ state: "missing", message: "文件不存在" }, "文件不存在"],
    [{ state: "denied", message: "无权限读取" }, "无权限读取"],
    [{ state: "too_large", message: "文件过大，未载入" }, "文件过大"],
    [{ state: "error", message: "boom" }, "读取失败"],
    [{ state: "empty" }, "文件为空"],
  ];
  for (const [result, needle] of states) {
    const ctx = drawerCtx(payload, new Map([[`alpha\n${path}`, result]]));
    const tree = { children: renderSkillDrawer(ctx, it) };
    assert.ok(findText(tree, "Skill 正文"), String(result.state));
    assert.ok(findText(tree, needle), `${result.state}: ${needle}`);
    if (result.state !== "empty") assert.equal(findAll(tree, (n) => n.tag === "pre").length, 0, `${result.state} must not render a pre`);
  }
  const okCtx = drawerCtx(payload, new Map([[`alpha\n${path}`, { state: "ok", path, bytes: 30, sha256: "0123456789abcdef", format: "markdown", content: "line1\n<script>alert(1)</script>\n<b>bold</b>" }]]));
  const okTree = { children: renderSkillDrawer(okCtx, it) };
  const pre = findAll(okTree, (n) => n.tag === "pre")[0];
  assert.ok(pre, "ok pre");
  assert.match(pre.attrs.class, /instr-body skill-body/);
  assert.deepEqual(pre.children, ["line1\n<script>alert(1)</script>\n<b>bold</b>"]);
  assert.ok(findText(okTree, "30 字节"));
  assert.ok(findText(okTree, "0123456789ab"));
  // Multica Workspace 行只有未采集说明，没有任何正文按钮
  assert.ok(findText(okTree, "Multica Workspace 正文未采集"));
  const cloudLines = findAll(okTree, (n) => n.tag === "p" && flatten(n).some((c) => typeof c === "string" && c.includes("Multica Workspace 正文未采集")));
  assert.ok(cloudLines.length);
  for (const cloudLine of cloudLines) assert.equal(findAll(cloudLine, (n) => n.tag === "button").length, 0);
});

test("drawer 正文 section lists local targets and requests content per path", () => {
  const requested = [];
  const item = simpleItem({
    name: "alpha",
    local_source: { path: "skills/alpha/SKILL.md", updated_at: "t", summary: "s" },
    entries: [
      { path: "skills/alpha/SKILL.md", present: true, scope: "repo_source", location: null, tools: [] },
      { path: "~/.codex/skills/alpha", present: true, scope: "user", location: "~/.codex/skills", tools: ["codex"] },
      { path: "/gone/alpha", present: false, scope: "other", location: null, tools: [] },
    ],
  });
  const payload = basePayload([item]);
  const ctx = drawerCtx(payload, new Map(), { loadSkillContent: (name, path) => requested.push([name, path]) });
  const tree = { children: renderSkillDrawer(ctx, item) };
  const buttons = findAll(tree, (n) => n.tag === "button" && flatten(n).includes("查看正文"));
  // local_source target + the one distinct present entry (duplicate path deduped, absent entry skipped)
  assert.equal(buttons.length, 2);
  assert.ok(findText(tree, "本机 skills/ 源"));
  assert.ok(findText(tree, "用户级入口 · ~/.codex/skills"));
  buttons[0].attrs.onclick();
  buttons[1].attrs.onclick();
  assert.deepEqual(requested, [
    ["alpha", "skills/alpha/SKILL.md"],
    ["alpha", "~/.codex/skills/alpha"],
  ]);
  const noTargets = simpleItem({ name: "beta" });
  const bare = { children: renderSkillDrawer(drawerCtx(basePayload([noTargets]), new Map()), noTargets) };
  assert.ok(findText(bare, "没有可查看的本机入口"));
});

test("runtime roots distinguish unconfigured, unknown and configured no-match with source/time titles", () => {
  const tools = [
    {id:'future', label:'Future', roots_state:'unconfigured', sources:[{source:'runtimes',collected_at:'2026-10-22',evidence_state:'observed'}]},
    {id:'codex',label:'Codex',roots_state:'configured'},
    {id:'other',label:'Other',roots_state:'configured'},
  ];
  const item = simpleItem({tools:{codex:null,other:null},runtime_evidence:{other:{state:'unknown',reason:'local_installation_unobserved'}}});
  const payload = {...basePayload([item]), tools};
  const tree = {children:renderSkills(renderCtx(payload))};
  assert.ok(findText(tree,'未配置技能根'));
  assert.ok(findText(tree,'空白：已配置根未命中'));
  const cells = findAll(tree,n=>n.tag==='td' && n.attrs?.class?.includes('tool-mark'));
  assert.ok(cells.some(n=>n.children.includes('?')));
  assert.ok(cells.some(n=>n.attrs.title.includes('运行时清单 · 2026-10-22')));
  assert.ok(cells.every(n=>!n.attrs.title.includes('local_installation_unobserved')));
  assert.ok(cells.some(n=>n.attrs.title.includes('已配置的根未命中')));
});

for (const failure of ["missing", "denied", "unsupported", "too_large", "error", "unavailable", "uncollected"]) {
  test(`attachment retry retains request file after ${failure} without response.file`, async () => {
    const path = "/fixture/alpha", file = "references/note.md";
    const item = simpleItem({ name: "alpha", local_source: { path } });
    const payload = basePayload([item]);
    const cache = new Map([[contentKey(item.name, path, "@tree"), {state:"ok", files:[{path:file,type:"file",bytes:12,readable:true}]}]]);
    const urls = [];
    const fetch = async url => {
      urls.push(url);
      return urls.length === 1
        ? {status: failure === "unavailable" ? 405 : 200, json: async () => ({state:failure,message:"first attempt failed"})}
        : {status:200,json:async () => ({state:"ok",format:"text",content:"Attachment recovered"})};
    };
    const ctx = drawerCtx(payload, cache, { async loadSkillContent(name, root, requestedFile) {
      cache.set(contentKey(name, root, requestedFile), await loadSkillContent(fetch, name, root, requestedFile));
    }});
    const draw = () => ({children:renderSkillDrawer(ctx, item)});
    const open = findAll(draw(), n=>n.tag === "button" && n.children.includes(file))[0];
    await open.attrs.onclick();
    assert.equal(cache.get(contentKey(item.name,path,file)).state, failure);
    assert.equal(cache.get(contentKey(item.name,path,file)).file, undefined, "error response deliberately omits file");
    const retry = findAll(draw(), n=>n.tag === "button" && n.children.includes("重试"))[0];
    assert.ok(retry);
    await retry.attrs.onclick();
    assert.equal(urls.length, 2);
    assert.equal(urls[1], urls[0], "retry must request the same attachment URL");
    assert.equal(new URL(urls[1], "http://fixture").searchParams.get("file"), file);
    assert.equal(cache.has(contentKey(item.name,path)), false, "main document must remain unloaded");
    assert.ok(findText(draw(), "Attachment recovered"));
    assert.equal(findText(draw(), "Viewer demo"), false);
  });
}

test("loading attachment has no retry until its request settles", () => {
  const path="/fixture/alpha", file="scripts/demo.py";
  const item=simpleItem({name:"alpha",local_source:{path}});
  const cache=new Map([
    [contentKey(item.name,path,"@tree"),{state:"ok",files:[{path:file,type:"file",bytes:10,readable:true}]}],
    [contentKey(item.name,path,file),{state:"loading"}],
  ]);
  const tree={children:renderSkillDrawer(drawerCtx(basePayload([item]),cache),item)};
  assert.equal(findAll(tree,n=>n.tag==="button" && n.children.includes("重试")).length,0);
});

test("equal hashes suppress checkout mtime drift; differing hashes outrank timestamps", () => {
  const item = cloudItem({
    local_source: { path: "skills/demo/SKILL.md", content_hash: "a".repeat(64), updated_at: "2026-10-22T00:00:00Z" },
    workspace_installs: [{ id: "s", workspace_id: "w", content_hash: "a".repeat(64), updated_at: "2026-01-01T00:00:00Z" }],
  });
  assert.equal(cloudDrift(item), null);
  item.workspace_installs[0].content_hash = "b".repeat(64);
  item.workspace_installs[0].updated_at = "2026-12-01T00:00:00Z";
  assert.equal(cloudDrift(item).comparisons[0].basis, "hash");
  assert.equal(cloudDrift(item).label, "Multica Workspace 内容不同");
  assert.match(cloudDrift(item).explanation, /无法据此判断新旧/);
  const before = skillsRefreshKey(basePayload([item]));
  item.workspace_installs[0].content_hash = "a".repeat(64);
  assert.notEqual(skillsRefreshKey(basePayload([item])), before);
});

test("hashless fallbacks disclose inference and compare complete descriptions", () => {
  const description = "Long description ".repeat(20);
  const item = cloudItem({
    local_source: {path: "skills/demo/SKILL.md", summary: description, updated_at: "2026-10-22T00:00:00Z"},
    workspace_installs: [{id:"s",workspace_id:"w",description,updated_at:"2026-10-21T00:00:00Z"}],
  });
  assert.match(cloudDrift(item).label, /按时间推断/);
  assert.match(cloudDrift(item).explanation, /git pull/);
  delete item.workspace_installs[0].updated_at;
  assert.equal(cloudDrift(item), null);
  item.workspace_installs[0].description += " different tail";
  assert.equal(cloudDrift(item).comparisons[0].basis, "description");
  delete item.workspace_installs[0].description;
  assert.equal(cloudDrift(item), null); // Unknown time alone is not evidence of staleness.
});

test("cloud-only runtime cells use a distinct symbol even without configured roots", () => {
  const item = simpleItem({name:"cloud",origin:"cloud",entries:[],workspace_installs:[{id:"s",workspace_id:"w"}]});
  const payload = {...basePayload([item]),tools:[{id:"future",label:"Future",roots_state:"unconfigured"}]};
  const tree = {children:renderSkills(renderCtx(payload))};
  const cells = findAll(tree,n=>n.tag==="td" && n.attrs?.class?.includes("tool-mark"));
  assert.equal(cells.length,1);
  assert.deepEqual(cells[0].children,["∅"]);
  assert.match(cells[0].attrs.title,/不适用：仅 Multica Workspace /);
  assert.match(cells[0].attrs.title,/— 表示数据未采集/);
});

test("unrecorded source card, row and status filter use the same term and value", () => {
  const item = simpleItem({source_class:"unrecorded",source_label:"未登记",channel_label:"未登记",reconciliation_status:"unrecorded",status_label:"未登记"});
  const payload = basePayload([item]);
  payload.summary = [{id:"all",label:"全部",count:1,statuses:{unrecorded:1}},{id:"unrecorded",label:"未登记",count:1,statuses:{unrecorded:1}}];
  const tree={children:renderSkills(renderCtx(payload))};
  assert.ok(findAll(tree,n=>n.tag==="option" && n.attrs.value==="unrecorded").every(n=>findText(n,"未登记")));
  assert.equal(filteredItems(payload,{skillStatus:"unrecorded"}).length,1);
  assert.equal(filteredItems(payload,{skillStatus:"unverified"}).length,0);
  assert.ok(findText(tree,"未登记 1"));
});

test("never-reviewed rows have an explicit treatment and compact columns retain full titles", () => {
  const detail="github:owner/very-long-source-channel".repeat(8), version="2.0.0-preview-very-long-version";
  const item=simpleItem({reconciliation_status:"stale",status_label:"未复核",review_time:null,quiet:true,channel_detail:detail,current_version:version});
  const tree={children:renderSkills(renderCtx(basePayload([item])))};
  const status=findAll(tree,n=>n.attrs?.class==="tag review-pending")[0];
  assert.ok(findText(status,"从未复核"));
  assert.match(status.attrs.title,/尚未建立人工复核记录/);
  assert.equal(findAll(tree,n=>n.tag==="td"&&n.attrs?.class==="channel-col")[0].attrs.title.includes(detail),true);
  assert.ok(findAll(tree,n=>n.tag==="td"&&n.attrs?.class==="mono version-col")[0].attrs.title.includes(version));
});

test("Grok Bot source keeps same-name declarations separate and shows historical evidence", () => {
  const payload=JSON.parse(readFileSync(new URL("./fixtures/skills-grok/skills.json",import.meta.url),"utf8"));
  const bot=payload.items.find(item=>item.origin==="grok_bot"&&item.name==="video-to-longform");
  const peers=payload.items.filter(item=>item.name===bot.name);
  assert.equal(peers.length,2);
  const tree={children:renderSkills(renderCtx(payload,{drawerSkill:bot.key}))};
  const rows=findAll(tree,n=>n.tag==="tr"&&n.attrs?.class?.includes("skill-row"));
  assert.equal(new Set(rows.map(row=>row.attrs.id)).size,rows.length);
  assert.equal(rows.filter(row=>row.attrs.class.includes("selected")).length,1);
  const grokRows=filteredItems(payload,{source:"grok_bot"});
  assert.equal(grokRows.length,8);
  assert.equal(groupedItems(grokRows,"tool",payload.tools)[0].items.length,8);
  assert.equal(filteredItems(payload,{source:"grok_bot",q:"EO-PM"}).length,8);
  assert.equal(filteredItems(payload,{source:"cloud"}).length,8); // Bot declarations are not Workspace installations.
  assert.ok(findAll(tree,n=>n.tag==="option"&&n.attrs.value==="grok_bot").some(n=>findText(n,"Grok Bot")));
  const selected=rows.find(row=>row.attrs.class.includes("selected"));
  const runtimeCells=findAll(selected,n=>n.tag==="td"&&n.attrs?.class?.includes("tool-mark"));
  assert.ok(runtimeCells.length>0);
  assert.ok(runtimeCells.every(cell=>cell.children.length===1&&cell.children[0]==="∅"));
  assert.ok(findAll(selected,n=>n.attrs?.class==="channel-col")[0].attrs.title.includes("未逐一实测"));
  const drawer={children:renderSkillDrawer({...drawerCtx(payload,new Map()),focusSkill(){}},bot)};
  assert.ok(findText(drawer,"Agent Steward"));
  assert.ok(findText(drawer,"EO-PM"));
  assert.ok(findText(drawer,"ACM-298 已实测（历史证据）"));
  assert.ok(findText(drawer,"按共享机制推断，未逐一实测"));
  assert.ok(findText(drawer,"同名关联"));
  assert.ok(findText(drawer,"∅"));
  assert.equal(findAll(drawer,n=>n.tag==="button"&&findText(n,"查看正文")).length,0);
  assert.equal(findText(drawer,"--"),false);
});

test("all Skills Workspace labels and tooltips use the approved name",()=>{
  const source=readFileSync(new URL("./skills.js",import.meta.url),"utf8");
  assert.equal(source.includes("云端"),false);
  const payload=JSON.parse(readFileSync(new URL("./fixtures/skills-grok/skills.json",import.meta.url),"utf8"));
  const tree={children:renderSkills(renderCtx(payload))};
  assert.ok(findAll(tree,n=>n.tag==="th").some(n=>findText(n,"Multica Workspace")));
  const card=sourceBlocks(payload).find(block=>block.id==="cloud");
  assert.equal(card.label,"Multica Workspace · 已安装");
  const bot=payload.items.find(item=>item.origin==="grok_bot");
  const before=skillsRefreshKey(payload);
  bot.grok_bots[0].evidence_state="inferred";
  assert.notEqual(skillsRefreshKey(payload),before);
});

test("drawer findings render Chinese descriptions without codes in text or titles",()=>{
  const item=simpleItem({findings:[
    {code:"content_hash_mismatch",label:"内容哈希和基线不一致"},
    {code:"local_installation_unobserved",label:"local_installation_unobserved"},
    {code:"future_internal_reason",label:"未知原因 future_internal_reason"},
  ]});
  const tree={children:renderSkillDrawer(renderCtx(basePayload([item])),item)};
  const visible=flatten(tree).filter(node=>typeof node==="string").join("\n");
  const titles=findAll(tree,node=>node.attrs?.title).map(node=>node.attrs.title).join("\n");
  assert.match(visible,/内容哈希和基线不一致/);
  assert.match(visible,/尚未观测到本机安装/);
  assert.match(visible,/原因说明待补充/);
  for(const code of item.findings.map(f=>f.code)) {
    assert.equal(visible.includes(code),false);
    assert.equal(titles.includes(code),false);
  }
});

test("Workspace-only rows use unrecorded consistently in status chips, filter and row",()=>{
  const payload=JSON.parse(readFileSync(new URL("./fixtures/skills-grok/skills.json",import.meta.url),"utf8"));
  const only=payload.items.filter(item=>item.origin==="cloud");
  assert.equal(only.length,7);
  assert.ok(only.every(item=>item.reconciliation_status==="unrecorded"&&item.status_label==="未登记"));
  const filtered=filteredItems(payload,{source:"cloud",skillStatus:"unrecorded"});
  assert.equal(filtered.length,7);
  assert.equal(filteredItems(payload,{source:"cloud",skillStatus:"unverified"}).length,0);
  assert.equal(sourceBlocks(payload).find(block=>block.id==="cloud").statuses.unrecorded,7);
  assert.equal(payload.summary.find(block=>block.id==="all").statuses.unrecorded,7);
  const tree={children:renderSkills(renderCtx(payload,{route:{source:"cloud",skillStatus:"unrecorded",skillGroup:"flat"}}))};
  const rows=findAll(tree,node=>node.tag==="tr"&&node.attrs?.class?.includes("skill-row"));
  assert.equal(rows.length,7);
  assert.ok(rows.every(row=>findText(row,"未登记")));
});
