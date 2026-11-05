import assert from "node:assert/strict";
import test from "node:test";
import {
  allAligned,
  errorText,
  filteredItems,
  itemKey,
  reasonText,
  renderInstructionDiff,
  shortfallLines,
  warningLines,
} from "./instruction-diff.js";

function h(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children: children.flat(Infinity) };
}
function walk(node, visit) {
  if (node == null || node === false) return;
  if (Array.isArray(node)) return node.forEach((n) => walk(n, visit));
  if (typeof node !== "object") return;
  visit(node);
  for (const child of node.children || []) walk(child, visit);
}
function textOf(node) {
  if (node == null || node === false) return "";
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (typeof node !== "object") return String(node);
  return (node.children || []).map(textOf).join("");
}
function findAll(node, pred, out = []) {
  walk(node, (n) => {
    if (pred(n)) out.push(n);
  });
  return out;
}

const SHA = "a".repeat(64);
const SHA2 = "b".repeat(64);
const SHA3 = "c".repeat(64);

function item(over = {}) {
  return {
    kind: "agent",
    workspace_id: "ws-a",
    id: "a1",
    name: "Builder",
    path: "instructions-governance/sources/agents/ws-a/a1.md",
    git_sha256: SHA,
    board_sha256: SHA,
    live_sha256: SHA2,
    updated_at: "2026-11-05T15:29:00Z",
    drift: "modified",
    comparison: "modified",
    stale_reasons: [],
    ...over,
  };
}

function payload(over = {}) {
  return {
    schema: "instruction-diff/v1",
    generated_at: "2026-11-05T15:30:00Z",
    summary: { total: 2, aligned: 1, modified: 1, stale: 0, split: 0, withheld: 0 },
    freshness: { board: "fresh", agent: "fresh", bot: "fresh" },
    live_sources: { agent: "board-cache", bot: "local-roster-cache" },
    source_errors: {},
    inaccessible_workspaces: [],
    items: [item()],
    ...over,
  };
}

function ctx(over = {}) {
  return {
    route: { q: "" },
    diff: { status: "idle", data: null, error: null, checkedAt: null, lastOkAt: null, filter: "", expanded: new Set() },
    workspaces: [{ id: "ws-a", name: "ACM Lab", slug: "acm-lab" }],
    agents: [{ workspace_id: "ws-a", id: "a1", name: "Builder", instructions: "线上正文\n第二行" }],
    bots: [],
    sources: { agents: { "ws-a/a1": { name: "Builder", sha256: SHA, text: "Git 正文\n第一行" } }, bots: {} },
    liveAt: "2026-11-05T15:28:00Z",
    ago: () => "2 分钟前",
    absTime: (s) => s,
    matchesQuery: (q, ...fields) => !q || fields.some((f) => f && String(f).toLowerCase().includes(q.toLowerCase())),
    onCheck() {},
    setFilter() {},
    toggleRow() {},
    openHref: (i) => `#/running?sel=${i.id}`,
    ...over,
  };
}

function render(c) {
  return { children: renderInstructionDiff(h, c) };
}

test("unchecked state shows the empty prompt and the check button", () => {
  const tree = render(ctx());
  const text = textOf(tree);
  assert.match(text, /检查 \/ 刷新差异/);
  assert.match(text, /还没有检查/);
  const button = findAll(tree, (n) => n.tag === "button" && textOf(n).includes("检查"))[0];
  assert.equal(button.attrs.disabled, false);
});

test("loading disables the button", () => {
  const c = ctx();
  c.diff.status = "loading";
  const tree = render(c);
  const button = findAll(tree, (n) => n.attrs?.class === "diff-check")[0];
  assert.equal(button.attrs.disabled, true);
  assert.match(textOf(tree), /检查中…/);
});

test("total==0 shows the registered-empty state", () => {
  const c = ctx({ diff: { status: "ok", data: payload({ summary: { total: 0, aligned: 0, modified: 0, stale: 0, split: 0, withheld: 0 }, items: [] }), error: null, checkedAt: 1, lastOkAt: 1, filter: "", expanded: new Set() } });
  assert.match(textOf(render(c)), /还没有登记任何 Agent \/ Grok Bot 描述/);
});

test("全部一致 only when total>0, no source errors and all fresh", () => {
  const aligned = payload({ summary: { total: 3, aligned: 3, modified: 0, stale: 0, split: 0, withheld: 0 }, items: [] });
  assert.equal(allAligned(aligned), true);
  assert.equal(allAligned(payload({ summary: { total: 0, aligned: 0, modified: 0, stale: 0, split: 0, withheld: 0 }, items: [] })), false);
  assert.equal(allAligned(payload({ summary: { total: 1, aligned: 1, modified: 0, stale: 0, split: 0, withheld: 0 }, items: [], source_errors: { board: "unavailable_or_invalid" } })), false);
  assert.equal(allAligned(payload({ summary: { total: 1, aligned: 1, modified: 0, stale: 0, split: 0, withheld: 0 }, items: [], freshness: { board: "fresh", agent: "stale", bot: "fresh" } })), false);
  const c = ctx({ diff: { status: "ok", data: aligned, error: null, checkedAt: 1, lastOkAt: 1, filter: "", expanded: new Set() } });
  assert.match(textOf(render(c)), /3 项全部一致/);
});

test("aligned rows without fresh sources get a shortfall note, never 全部一致", () => {
  const staleBoard = payload({
    summary: { total: 1, aligned: 1, modified: 0, stale: 0, split: 0, withheld: 0 },
    items: [],
    source_errors: { board: "unavailable_or_invalid" },
    freshness: { board: "stale", agent: "fresh", bot: "fresh" },
  });
  assert.deepEqual(shortfallLines(staleBoard), ["看板导出 不可用", "看板导出数据过期"]);
  const c = ctx({ diff: { status: "ok", data: staleBoard, error: null, checkedAt: 1, lastOkAt: 1, filter: "", expanded: new Set() } });
  const text = textOf(render(c));
  assert.match(text, /未达「全部一致」条件/);
  assert.equal(text.includes("全部一致。"), false);
});

test("partial failures show a warning strip above the list", () => {
  assert.deepEqual(warningLines(payload({ source_errors: { bot: "unavailable_or_invalid" }, inaccessible_workspaces: [{ workspace_id: "ws-x" }] })), [
    "Bot 线上 不可用",
    "1 个 workspace 无权读取",
  ]);
  const c = ctx({ diff: { status: "ok", data: payload({ source_errors: { bot: "unavailable_or_invalid" } }), error: null, checkedAt: 1, lastOkAt: 1, filter: "", expanded: new Set() } });
  const text = textOf(render(c));
  assert.match(text, /部分来源不可用/);
  assert.match(text, /Bot 线上 不可用/);
});

test("failed check keeps the last result dimmed with a success timestamp", () => {
  const c = ctx({
    diff: {
      status: "error",
      data: payload(),
      error: { code: "http_503", message: "HTTP 503" },
      checkedAt: 2,
      lastOkAt: Date.parse("2026-11-05T15:30:00Z"),
      filter: "",
      expanded: new Set(),
    },
  });
  const tree = render(c);
  const text = textOf(tree);
  assert.match(text, /检查失败：http_503/);
  assert.match(text, /上次成功 23:30/);
  const dimmed = findAll(tree, (n) => n.attrs?.class === "diff-outdated");
  assert.equal(dimmed.length, 1);
});

test("git_source_unreadable shows the server message and never claims all-aligned", () => {
  const message = "部署目录缺少 instructions-governance/，需随看板一起检出或单独挂载（只读），由用户另批；本接口不会改写部署目录。";
  const c = ctx({
    diff: { status: "error", data: null, error: { code: "git_source_unreadable", message }, checkedAt: 1, lastOkAt: null, filter: "", expanded: new Set() },
  });
  const tree = render(c);
  const text = textOf(tree);
  assert.match(text, /检查失败：git_source_unreadable/);
  assert.ok(text.includes(message));
  assert.equal(text.includes("全部一致"), false);
  const button = findAll(tree, (n) => n.attrs?.class === "diff-check")[0];
  assert.equal(button.attrs.disabled, false);
});

test("errorText maps the code only", () => {
  assert.equal(errorText({ code: "git_source_unreadable" }), "检查失败：git_source_unreadable");
  assert.equal(errorText(null), "检查失败");
  assert.equal(errorText({}), "检查失败：unavailable");
});

test("reason labels map codes, show two then +n, and fall back per drift", () => {
  assert.equal(reasonText(item({ stale_reasons: ["live_hash_missing", "board_stale", "live_stale", "board_outdated"] })), "线上未采集或缺哈希、看板导出过期 +2");
  assert.equal(reasonText(item({ stale_reasons: ["board_body_missing"] })), "看板正文缺失");
  assert.equal(reasonText(item({ drift: "split", stale_reasons: [] })), "三方两两不同");
  assert.equal(reasonText(item({ drift: "modified", stale_reasons: [] })), "线上与 Git 不同");
  assert.equal(reasonText(item({ drift: "modified", stale_reasons: [], board_sha256: SHA2 })), "Git 与线上、看板不同");
  assert.equal(reasonText(item({ drift: "withheld", stale_reasons: [] })), "内容不公开");
  assert.equal(reasonText(item({ drift: "stale", stale_reasons: ["unknown_reason"] })), "unknown_reason");
});

test("status chips filter the list and toggle off", () => {
  const data = payload({
    summary: { total: 3, aligned: 0, modified: 1, stale: 1, split: 1, withheld: 0 },
    items: [item(), item({ id: "a2", drift: "split", comparison: "split", board_sha256: SHA3 }), item({ id: "a3", drift: "stale", stale_reasons: ["live_stale"] })],
  });
  assert.equal(filteredItems(data, "", "").length, 3);
  assert.deepEqual(filteredItems(data, "split", "").map((i) => i.id), ["a2"]);
  const c = ctx({ diff: { status: "ok", data, error: null, checkedAt: 1, lastOkAt: 1, filter: "split", expanded: new Set() } });
  const rows = findAll(render(c), (n) => n.attrs?.class?.includes("diff-row"));
  assert.equal(rows.length, 1);
  const chips = findAll(render(c), (n) => n.tag === "button" && textOf(n) === "三方不一致 1");
  assert.equal(chips[0].attrs["aria-pressed"], "true");
});

test("search matches name, id, and workspace slug", () => {
  const wsOf = (id) => (id === "ws-a" ? { name: "ACM Lab", slug: "acm-lab" } : null);
  const data = payload({ items: [item(), item({ id: "a9", name: "Reviewer" })] });
  assert.deepEqual(filteredItems(data, "", "builder", wsOf, (q, ...f) => f.some((x) => x && String(x).toLowerCase().includes(q))).map((i) => i.id), ["a1"]);
  assert.deepEqual(filteredItems(data, "", "acm-lab", wsOf, (q, ...f) => f.some((x) => x && String(x).toLowerCase().includes(q))).length, 2);
  assert.deepEqual(filteredItems(data, "", "a9", wsOf, (q, ...f) => f.some((x) => x && String(x).toLowerCase().includes(q))).map((i) => i.id), ["a9"]);
  assert.equal(filteredItems(data, "", "beta", wsOf, (q, ...f) => f.some((x) => x && String(x).toLowerCase().includes(q))).length, 0);
});

test("expanded rows never leak nested arrays through the real one-level h()", () => {
  // app.js h() flattens children exactly once and stringifies everything that
  // is not a Node; a nested row array would render as "[object ...]".
  function appH(tag, attrs, ...children) {
    return {
      tag,
      attrs: attrs || {},
      children: children
        .flat()
        .map((c) => (c == null || c === false ? "" : typeof c === "object" && c.tag ? c : String(c))),
    };
  }
  const c = ctx();
  const held = item({ id: "a2", drift: "stale", stale_reasons: ["live_stale"] });
  c.diff = {
    status: "ok",
    data: payload({ items: [item(), held] }),
    error: null,
    checkedAt: 1,
    lastOkAt: 1,
    filter: "",
    expanded: new Set([itemKey(item())]),
  };
  const tree = { children: renderInstructionDiff(appH, c) };
  const text = textOf(tree);
  assert.equal(text.includes("[object"), false);
  const rows = findAll(tree, (n) => n.tag === "tr" && String(n.attrs?.class || "").startsWith("diff-"));
  assert.equal(rows.length, 3); // expanded head + its detail row + collapsed head
  assert.equal(findAll(tree, (n) => n.attrs?.class === "diff-detail").length, 1);
  assert.equal(findAll(tree, (n) => n.attrs?.["aria-expanded"] === "true").length, 1);
});

test("expanded row reuses loaded texts, caps at 20 lines, and shows three short hashes", () => {
  const longGit = Array.from({ length: 30 }, (_, i) => `git-${i}`).join("\n");
  const longLive = Array.from({ length: 30 }, (_, i) => `live-${i}`).join("\n");
  const c = ctx({
    agents: [{ workspace_id: "ws-a", id: "a1", name: "Builder", instructions: longLive }],
    sources: { agents: { "ws-a/a1": { text: longGit } }, bots: {} },
  });
  c.diff = { status: "ok", data: payload(), error: null, checkedAt: 1, lastOkAt: 1, filter: "", expanded: new Set([itemKey(item())]) };
  const tree = render(c);
  const text = textOf(tree);
  assert.ok(text.includes("Git aaaaaaaa"));
  assert.ok(text.includes("线上 bbbbbbbb"));
  assert.match(text, /各取前 20 行/);
  assert.match(text, /共 30 行/);
  assert.equal(text.includes("git-25"), false);
  assert.match(text, /线上为脱敏缓存，以哈希为准/);
  assert.match(text, /在 Agents 中打开/);
  const expandedRow = findAll(tree, (n) => n.attrs?.["aria-expanded"] === "true");
  assert.equal(expandedRow.length, 1);
});

test("missing bodies fall back to hashes and 正文不可用", () => {
  const c = ctx({ agents: [], sources: { agents: {}, bots: {} } });
  c.diff = { status: "ok", data: payload(), error: null, checkedAt: 1, lastOkAt: 1, filter: "", expanded: new Set([itemKey(item())]) };
  const text = textOf(render(c));
  assert.match(text, /正文不可用/);
  assert.ok(text.includes("Git aaaaaaaa"));
});

test("withheld rows show no hashes", () => {
  const held = item({ drift: "withheld", comparison: "withheld", git_sha256: null, board_sha256: null, live_sha256: null });
  const c = ctx();
  c.diff = { status: "ok", data: payload({ items: [held] }), error: null, checkedAt: 1, lastOkAt: 1, filter: "", expanded: new Set([itemKey(held)]) };
  const text = textOf(render(c));
  assert.match(text, /已隐藏/);
  assert.match(text, /三方哈希不公开/);
});
