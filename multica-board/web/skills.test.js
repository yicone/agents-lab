import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { badgeOf, filteredItems, queueModel, renderSkills, skillsRefreshKey } from "./skills.js";

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
