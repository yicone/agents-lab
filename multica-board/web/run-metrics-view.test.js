import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { presentRunMetrics, renderRunMetrics, writeControlTags } from "./run-metrics-view.js";

const payload = JSON.parse(readFileSync(new URL("./fixtures/run-metrics.json", import.meta.url), "utf8"));

function vnode(tag, attrs, ...children) {
  return { tag, attrs: attrs || {}, children: children.flat(Infinity).filter((child) => child != null && child !== false) };
}

function textOf(node) {
  if (node == null || node === false) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  return (node.children || []).map(textOf).join("");
}

function section(nodes, label) {
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

function render(route = {}) {
  return renderRunMetrics({ h: vnode, route, runMetrics: payload, setRoute() {} });
}

test("the page shows the report rates, coverage, rework bound, and reconciliation", () => {
  const window = payload.report.windows["14d"];
  const nodes = render();
  const page = textOf({ children: nodes });
  const rates = section(nodes, "双成功率");
  assert.match(rates, new RegExp(`${window.success_rate.including_cancelled.numerator} / ${window.success_rate.including_cancelled.denominator}`));
  assert.match(rates, new RegExp(`${window.success_rate.excluding_cancelled.numerator} / ${window.success_rate.excluding_cancelled.denominator}`));
  assert.match(rates, /含取消/);
  assert.match(rates, /不含取消/);
  assert.match(page, /已知/);
  assert.match(page, /未知/);
  assert.match(page, /缺失/);
  const rework = section(nodes, "返工下限");
  const lower = window.rework.rework_rate_lower_bound;
  assert.match(rework, new RegExp(`${lower.numerator} / ${lower.denominator}`));
  assert.match(rework, /不提供/);
  assert.match(rework, /lower_bound_only|下限/);
  const recon = section(nodes, "runtime reconciliation");
  assert.match(recon, /10/);
  assert.match(recon, /12/);
  assert.match(recon, /未复核/);
  assert.match(recon, /unknown 硬禁用否/);
  assert.match(recon, /历史 quota 排除否/);
  assert.match(recon, /排除名单0/);
  assert.match(recon, /agent-quota/);
  assert.match(section(nodes, "evidence"), /FR-004-QUOTA-TEXT/);
  assert.match(section(nodes, "数据缺口"), /gemini/);
  assert.equal(writeControlTags(nodes).length, 0);
  assert.match(page, /data-readonly|只读/);
  assert.doesNotMatch(page, /全部 workspace/);
});

test("a single pre-aggregated filter is shown and a cross filter is not recomputed", () => {
  const group = payload.report.windows["14d"].groups.by_agent["agent-quota"].success_rate;
  const one = textOf({ children: render({ magent: "agent-quota" }) });
  assert.match(one, new RegExp(`${group.including_cancelled.numerator} / ${group.including_cancelled.denominator}`));
  assert.match(one, /预聚合 agent agent-quota/);
  const crossed = section(render({ mws: "ws-1", magent: "agent-1" }), "双成功率");
  assert.match(crossed, /不重算/);
  assert.match(crossed, /7 \/ 11/);
  assert.doesNotMatch(crossed, /预聚合 agent/);
});

test("search narrows evidence and leaves the window rate in place", () => {
  const nodes = render({ q: "this-matches-nothing" });
  assert.match(section(nodes, "双成功率"), /7 \/ 11/);
  assert.match(section(nodes, "evidence"), /逐条 evidence 0 条/);
});

test("missing scheduling fields stay missing instead of yes, zero, or none", () => {
  const report = structuredClone(payload.report);
  delete report.scheduling;
  const nodes = renderRunMetrics({ h: vnode, route: {}, runMetrics: { ...payload, report } });
  const recon = section(nodes, "runtime reconciliation");
  const gaps = section(nodes, "数据缺口");
  assert.match(recon, /unknown 硬禁用missing/);
  assert.match(recon, /历史 quota 排除missing/);
  assert.match(recon, /排除名单missing/);
  assert.match(recon, /历史 quota agent：missing/);
  assert.match(recon, /信号 missing/);
  assert.doesNotMatch(recon, /历史 quota 排除是/);
  assert.doesNotMatch(recon, /排除名单0/);
  assert.doesNotMatch(recon, /历史 quota agent：无/);
  assert.doesNotMatch(recon, /信号 0/);
  assert.match(gaps, /historical_quota_excludes_agent 缺失/);
  assert.doesNotMatch(gaps, /报告含历史 quota 排除名单/);
});

test("explicit false and empty scheduling lists stay distinct from missing", () => {
  const report = structuredClone(payload.report);
  report.scheduling = {
    unknown_hard_disable: false,
    historical_quota_excludes_agent: false,
    excluded_agent_ids: [],
    historical_quota_agent_ids: [],
    historical_quota_note: "",
    signals: [],
  };
  const recon = section(renderRunMetrics({
    h: vnode,
    route: {},
    runMetrics: { ...payload, report },
  }), "runtime reconciliation");
  assert.match(recon, /unknown 硬禁用否/);
  assert.match(recon, /历史 quota 排除否/);
  assert.match(recon, /排除名单0/);
  assert.match(recon, /历史 quota agent：空/);
  assert.match(recon, /信号 0 条/);
  const scheduling = recon.slice(recon.indexOf("调度信号")).replace("unknown 硬禁用", "");
  assert.doesNotMatch(scheduling, /missing|unknown|无/);
});

test("a missing snapshot has no write controls", () => {
  const nodes = renderRunMetrics({
    h: vnode,
    route: {},
    runMetrics: { ok: false, readonly: true, writes: [], message: "还没有快照。", report: null },
  });
  assert.match(textOf({ children: nodes }), /还没有快照/);
  assert.equal(writeControlTags(nodes).length, 0);
});

test("the running subnav points at the read-only page", () => {
  const app = readFileSync(new URL("./app.js", import.meta.url), "utf8");
  assert.match(app, /#\/running\/metrics", "运行指标"/);
  assert.match(app, /renderRunMetrics/);
  assert.match(app, /getJSON\("run-metrics"\)/);
});
