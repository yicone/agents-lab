import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { MAP_TOOLS } from "./layers.js";
import { formatCount, modelUsage, renderUsage, usageAlert, usageSignal } from "./usage.js";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/usage.json", import.meta.url), "utf8"));

function vnode(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children: children.flat(Infinity).filter((child) => child != null && child !== false) };
}

function textOf(node) {
  if (node == null || node === false) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  return (node.children || []).map(textOf).join("");
}

function column(nodes, label) {
  const found = [];
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    if (node.attrs?.["aria-label"] === label) found.push(node);
    for (const child of node.children || []) walk(child);
  };
  for (const node of nodes) walk(node);
  assert.equal(found.length, 1, label);
  return textOf(found[0]);
}

function render(payload, route = { q: "" }) {
  return renderUsage({
    h: vnode,
    route,
    usage: payload,
    ago: () => "刚刚",
    absTime: (iso) => iso,
  });
}

test("fixture keeps the three layers apart and follows API signals", () => {
  const model = modelUsage(fixture);
  assert.equal(model.days, 14);
  assert.equal(model.collectedAt, "2026-10-16T16:43:00Z");
  assert.equal(model.runtimes.length, 1);
  const runtime = model.runtimes[0];
  assert.equal(runtime.runs_total, 7);
  assert.equal(runtime.completed, 1);
  assert.equal(runtime.capacity_failed, 2);
  assert.equal(runtime.quota_failed, 1);
  assert.equal(runtime.rate_limit_failed, 1);
  assert.equal(runtime.true_failed, 1);
  assert.equal(runtime.cancelled, 2);
  assert.equal(runtime.input_tokens, 30);
  assert.equal(runtime.output_tokens, 8);
  assert.equal(runtime.cache_read_tokens, 4);
  assert.equal(runtime.cache_write_tokens, 1);
  assert.equal(runtime.cost_usd_ticks, 1200);
  assert.equal(runtime.name, "Hermes");
  assert.equal(Object.hasOwn(runtime, "workspace_id"), false);
  assert.equal(Object.hasOwn(runtime, "runtime_id"), false);

  const providers = model.quotas.map((item) => `${item.provider}:${item.status}`);
  assert.deepEqual(providers, ["cursor:ok", "grok:error", "codex:ok", "9router:unconnected", "orca:unconnected"]);
  assert.equal(model.quotas[0].windows[0].signal, "dispatchable");
  assert.equal(model.quotas[0].windows[0].used_percent, 36.43);
  assert.equal(model.quotas[1].windows[0].signal, "exhausting");
  assert.equal(model.quotas[1].windows[0].used_percent, 50);
  assert.equal(model.signals.dispatchable[0].provider, "cursor");
  assert.equal(model.signals.exhausting[0].provider, "grok");
  assert.equal(model.quotas[3].windows.length, 0);
  assert.equal(model.quotas[4].windows.length, 0);
});

test("the page shows three columns and does not add token counts to quota percents", () => {
  const nodes = render(fixture);
  const page = textOf({ children: nodes });
  const runtime = column(nodes, "Multica 运行层");
  const quota = column(nodes, "外部额度");
  const failure = column(nodes, "失败");

  assert.match(page, /近 14 天/);
  assert.match(page, /2026-10-16T16:43:00Z/);
  assert.match(page, /分栏展示，不相加/);
  assert.match(page, /must not be summed/);

  assert.match(runtime, /Hermes/);
  assert.match(runtime, new RegExp(formatCount(7)));
  assert.match(runtime, /输入 token/);
  assert.match(runtime, new RegExp(formatCount(30)));
  assert.match(runtime, new RegExp(formatCount(1200)));
  assert.match(runtime, /2026-10-15/);
  assert.match(runtime, /2026-10-16/);
  assert.doesNotMatch(runtime, /%|used_percent|36\.43|ws-should-hide|rt-secret-id/);

  assert.match(quota, /36\.43%/);
  assert.match(quota, /可派活/);
  assert.match(quota, /即将耗尽/);
  assert.match(quota, /codexbar/);
  assert.match(quota, /9router/);
  assert.match(quota, /orca/);
  assert.match(quota, /未接入/);
  assert.doesNotMatch(quota, /输入 token|cost ticks|ws-should-hide|rt-secret-id|timeout after 30s|signal_reason/);

  assert.match(failure, /满载失败/);
  assert.match(failure, /额度失败/);
  assert.match(failure, /限流失败/);
  assert.match(failure, /真失败/);
  assert.match(failure, /耗时和原始 error 文本不在 \/api\/usage/);
  assert.doesNotMatch(failure, /%|输入 token|cost ticks/);
  assert.doesNotMatch(page, /失败合计|1236|36\.43\s*\+\s*30|30\s*\+\s*36/);
});

test("dispatchable and exhausting stay on the API flags when percents would not cross a local threshold", () => {
  const nodes = render(fixture);
  const quota = column(nodes, "外部额度");
  assert.match(quota, /36\.43%可派活/);
  assert.match(quota, /50%即将耗尽/);
  const cursorWindow = quota.slice(quota.indexOf("36.43%"), quota.indexOf("36.43%") + 12);
  assert.doesNotMatch(cursorWindow, /即将耗尽/);
  const source = readFileSync(new URL("./usage.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /used_percent\s*>=\s*8\d|used_percent\s*>=\s*9\d|remaining\s*>=\s*30/);
});

test("a missing snapshot still names the gap and leaves 9router and orca unconnected", () => {
  const payload = {
    generated_at: "2026-10-16T00:00:00Z",
    usage_collected_at: null,
    days: 14,
    snapshot_state: "missing",
    external_quotas: [
      { provider: "9router", status: "unconnected", label: "未接入", windows: [] },
      { provider: "orca", status: "unconnected", label: "未接入", windows: [] },
    ],
    signals: { dispatchable: [], exhausting: [] },
    multica_daily: { note: "Multica token usage and codexbar quota percentages use independent accounting and must not be summed.", by_provider: [], by_runtime: [] },
  };
  const nodes = render(payload);
  const page = textOf({ children: nodes });
  assert.match(page, /尚未采集/);
  assert.match(page, /还没有用量快照/);
  assert.match(column(nodes, "外部额度"), /9router[\s\S]*未接入/);
  assert.match(column(nodes, "外部额度"), /orca[\s\S]*未接入/);
  assert.match(column(nodes, "失败"), /耗时和原始 error 文本不在 \/api\/usage/);
  assert.match(column(nodes, "Multica 运行层"), /没有 runtime 用量/);
  assert.equal(usageSignal(payload), "快照未采集");
  assert.equal(usageAlert(payload), false);
});

test("account-shaped strings and quota error text stay off the page", () => {
  const leaked = structuredClone(fixture);
  leaked.multica_daily.by_runtime[0].name = "owner@example.com";
  leaked.external_quotas[0].source = "user@example.com";
  const page = textOf({ children: render(leaked) });
  assert.doesNotMatch(page, /example\.com|@/);
  assert.match(page, /未命名 runtime/);
  assert.equal(usageAlert(fixture), true);
  assert.match(usageSignal(fixture), /1 个窗口即将耗尽/);
});

test("L1 usage tool points at this page", () => {
  const tool = MAP_TOOLS.l1.find((item) => item.label === "用量与额度");
  assert.equal(tool.kind, "page");
  assert.equal(tool.href, "#/usage");
  assert.equal(tool.gap, undefined);
  const app = readFileSync(new URL("./app.js", import.meta.url), "utf8");
  assert.match(app, /getJSON\("usage"\)/);
  assert.match(app, /renderUsage/);
});
