import assert from "node:assert/strict";
import test from "node:test";
import { applyPatch, formatHash, historyWrite, parseHash, redirectHash } from "./route.js";
import { collectionMark, emptyInventoryText, evidenceLevel, renderEvidence, sourceCountLabel, visibleItems } from "./evidence.js";

function vnode(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children: children.flat(Infinity).filter((child) => child != null && child !== false) };
}

function textOf(node) {
  if (node == null || node === false) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  return (node.children || []).map(textOf).join("");
}

function walk(node, found = []) {
  if (node == null || node === false) return found;
  if (Array.isArray(node)) {
    node.forEach((child) => walk(child, found));
    return found;
  }
  if (typeof node === "object" && node.attrs?.class?.includes("source-card")) found.push(node);
  if (typeof node === "object" && node.children) node.children.forEach((child) => walk(child, found));
  return found;
}

const payload = {
  ok: true,
  read_only: true,
  sources: [
    { source_id: "local", source_kind: "local_machine", label: "本机", collection_state: "collected", evidence_level: "observed", reason: null },
    { source_id: "repository", source_kind: "repository", label: "仓库声明", collection_state: "collected", evidence_level: "declared", reason: null },
    { source_id: "multica_workspace", source_kind: "multica_workspace", label: "Multica Workspace", collection_state: "uncollected", evidence_level: "unknown", reason: "not_implemented" },
    { source_id: "grok_bot", source_kind: "grok_bot", label: "Grok Bot", collection_state: "collected", evidence_level: "declared", reason: null },
    { source_id: "grok_cli", source_kind: "grok_cli", label: "Grok CLI", collection_state: "uncollected", evidence_level: "unknown", reason: "not_implemented" },
    { source_id: "memory", source_kind: "grok_bot", label: "Memory", collection_state: "uncollected", evidence_level: "unknown", reason: "source_unverified" },
  ],
  items: [
    { object_type: "skill", object_id: "local:demo", display_name: "demo", source_id: "local", source_kind: "local_machine", scope: "shared", collection_state: "collected", evidence_level: "observed", observed_at: "2026-10-26T09:00:00Z", related_name: "demo", evidence: { location: "~/.agents/skills/demo", source_label: "本机目录" } },
    { object_type: "skill", object_id: "repo:demo", display_name: "demo", source_id: "repository", source_kind: "repository", scope: "repository", collection_state: "collected", evidence_level: "declared", observed_at: "2026-10-26T09:00:00Z", related_name: "demo", evidence: { location: "skills/demo", source_label: "声明" } },
    { object_type: "skill", object_id: "bot:demo", display_name: "demo", source_id: "grok_bot", source_kind: "grok_bot", scope: "bot-private", collection_state: "collected", evidence_level: "declared", observed_at: "2026-10-26T09:00:00Z", related_name: "demo", evidence: { location: null, source_label: "名称声明" } },
    { object_type: "skill", object_id: "blocked", display_name: "blocked", source_id: "local", source_kind: "local_machine", scope: "shared", collection_state: "unavailable", evidence_level: "unknown", observed_at: "2026-10-26T09:00:00Z", related_name: null, evidence: { location: null, source_label: "无权限" } },
    { object_type: "memory_collection", object_id: "memory:bot-private", display_name: "Bot 私有 Memory", source_id: "memory", source_kind: "grok_bot", scope: "bot-private", collection_state: "uncollected", evidence_level: "unknown", reason: "source_unverified" },
    { object_type: "memory_collection", object_id: "memory:shared_profile", display_name: "共享 Profile", source_id: "memory", source_kind: "grok_bot", scope: "shared", collection_state: "uncollected", evidence_level: "unknown", reason: "source_unverified" },
    { object_type: "memory_collection", object_id: "memory:shared_journal", display_name: "共享 Journal", source_id: "memory", source_kind: "grok_bot", scope: "shared", collection_state: "uncollected", evidence_level: "unknown", reason: "source_unverified" },
  ],
};

test("uncollected and unavailable marks are not zero", () => {
  assert.equal(sourceCountLabel(payload.sources[2], payload.items).symbol, "—");
  assert.equal(sourceCountLabel(payload.sources[2], payload.items).count, null);
  assert.equal(sourceCountLabel(payload.sources[4], [{ source_id: "grok_cli", object_type: "skill" }]).symbol, "—");
  assert.equal(sourceCountLabel({ collection_state: "collected", source_id: "local" }, []).symbol, "0");
  assert.equal(sourceCountLabel({ collection_state: "collected", source_id: "local" }, []).caption, "已采集，0 条");
  assert.equal(collectionMark("unavailable").symbol, "?");
  assert.equal(collectionMark("not_applicable").symbol, "∅");
  assert.equal(collectionMark("invalid").symbol, "?");
  assert.equal(emptyInventoryText("grok_cli", payload), "— 未采集");
  assert.equal(emptyInventoryText("multica_workspace", payload), "— 未采集");
});

test("evidence levels keep distinct labels", () => {
  for (const [level, label] of [
    ["observed", "直接观察"],
    ["declared", "声明"],
    ["derived", "推导"],
    ["historical", "历史"],
    ["unknown", "未知"],
  ]) {
    assert.equal(evidenceLevel(level).label, label);
  }
  assert.equal(evidenceLevel("historical").cls, "muted");
  assert.equal(evidenceLevel("unknown").cls, "warn");
});

test("evidence page shows symbols, keeps same names apart, and has no write control", () => {
  const tree = renderEvidence({ h: vnode, route: { source: "", q: "" }, evidence: payload, setRoute() {} });
  const rendered = walk(tree);
  const byLabel = Object.fromEntries(rendered.map((card) => [textOf(card.children[0]), textOf(card.children[1])]));
  assert.equal(byLabel["Workspace"], "—");
  assert.equal(byLabel["Grok CLI"], "—");
  assert.equal(byLabel["Memory"], "—");
  assert.equal(byLabel["本机"], "2");
  const page = textOf({ children: tree });
  assert.equal(page.includes("只读证据清单"), true);
  assert.equal(page.includes("demo"), true);
  assert.equal((page.match(/local_machine/g) || []).length > 0, true);
  assert.equal((page.match(/grok_bot/g) || []).length > 0, true);
  assert.equal(page.includes("直接观察"), true);
  assert.equal(page.includes("声明"), true);
  assert.equal(page.includes("未知"), true);
  assert.equal(page.includes("Bot 私有 Memory"), true);
  assert.equal(page.includes("共享登录配置"), true);
  assert.equal(/\bProfile\b/.test(page), false);
  assert.equal(payload.items.find((item) => item.object_id === "memory:shared_profile").display_name, "共享 Profile");
  assert.equal(page.includes("当前实现只列出未采集槽位"), true);
  assert.equal(page.includes("不能相加成数量"), true);
  assert.equal(page.includes("来源未证实"), true);
  assert.equal(page.includes("没有出口"), false);
  assert.equal(page.includes("没有可用导出"), false);
  assert.equal(page.includes("不写回"), true);
  assert.equal(page.includes("批准"), false);
  assert.equal(visibleItems(payload, { source: "grok_bot", q: "" }).map((item) => item.object_id).join(), "bot:demo");
  assert.deepEqual(visibleItems(payload, { source: "", q: "demo" }).map((item) => item.object_id), ["local:demo", "repo:demo", "bot:demo"]);
});

function findClass(node, className, found = []) {
  if (node == null || node === false) return found;
  if (Array.isArray(node)) {
    node.forEach((child) => findClass(child, className, found));
    return found;
  }
  if (typeof node === "object") {
    if (String(node.attrs?.class || "").split(/\s+/).includes(className)) found.push(node);
    (node.children || []).forEach((child) => findClass(child, className, found));
  }
  return found;
}

test("empty table distinguishes a search miss from a collected source with no records", () => {
  const miss = emptyInventoryText("local", payload, { q: "no-such-skill" });
  assert.equal(miss, "没有符合筛选的证据");
  assert.equal(emptyInventoryText("", payload, { q: "no-such-skill" }), "没有符合筛选的证据");

  const emptyCollected = {
    ok: true,
    sources: [{ source_id: "local", source_kind: "local_machine", label: "本机", collection_state: "collected", evidence_level: "observed", reason: null }],
    items: [],
  };
  assert.equal(emptyInventoryText("local", emptyCollected, { q: "" }), "已采集，0 条");
  assert.equal(emptyInventoryText("local", emptyCollected, { q: "demo" }), "没有符合筛选的证据");

  const searched = renderEvidence({ h: vnode, route: { source: "local", q: "zzzz-not-a-skill" }, evidence: payload, setRoute() {} });
  assert.equal(textOf(findClass(searched, "empty")[0]), "没有符合筛选的证据");
  const emptyPage = renderEvidence({ h: vnode, route: { source: "local", q: "" }, evidence: emptyCollected, setRoute() {} });
  assert.equal(textOf(findClass(emptyPage, "empty")[0]), "已采集，0 条");
  assert.equal(sourceCountLabel(emptyCollected.sources[0], emptyCollected.items).caption, "已采集，0 条");
});

test("evidence route round-trips and does not keep a workspace filter", () => {
  assert.equal(redirectHash("#/configure/skills/evidence"), null);
  const route = parseHash("#/configure/skills/evidence?source=grok_cli&q=demo");
  assert.equal(route.view, "skills");
  assert.equal(route.evidence, true);
  assert.equal(route.source, "grok_cli");
  assert.equal(route.q, "demo");
  assert.equal(formatHash(route), "#/configure/skills/evidence?q=demo&source=grok_cli");
  const back = applyPatch(route, { evidence: false, source: "" });
  assert.equal(back.evidence, false);
  assert.equal(formatHash(back), "#/configure/skills?q=demo");
  assert.equal(historyWrite(formatHash(route), formatHash(back), { evidence: false }), "push");
  const issues = applyPatch(route, { view: "issues" });
  assert.equal(issues.evidence, false);
  assert.equal(formatHash(issues), "#/issues?q=demo");
});
