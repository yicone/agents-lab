import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { MAP_TOOLS } from "./layers.js";
import { formatCount, formatDeltaPp, formatSampleAge, modelUsage, poolModels, renderUsage, usageAlert, usageRefreshKey, usageSignal } from "./usage.js";

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

function render(payload, route = { q: "" }, nowMs) {
  return renderUsage({
    h: vnode,
    route,
    usage: payload,
    ago: () => "刚刚",
    absTime: (iso) => iso,
    nowMs,
  });
}

function findWindow(nodes, identity) {
  let found = null;
  const walk = (node) => {
    if (!node || typeof node !== "object" || found) return;
    if (node.attrs?.["data-window"] === identity) found = node;
    for (const child of node.children || []) walk(child);
  };
  for (const node of nodes) walk(node);
  return found;
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

test("forecast fields render after the existing signal and stay absent when the payload omits them", () => {
  const model = modelUsage(fixture);
  const cursor = model.quotas[0];
  const tertiary = cursor.windows[1];
  assert.equal(tertiary.identity, "cursor:tertiary");
  assert.equal(tertiary.label, "Third Party");
  assert.equal(tertiary.signal, "normal");
  assert.equal(tertiary.band, "warning");
  assert.equal(tertiary.band_present, true);
  assert.equal(cursor.windows[0].band, null);
  assert.equal(cursor.windows[0].band_present, false);
  assert.equal(cursor.windows[0].label, "Total");
  const grok = model.quotas[1].windows[0];
  assert.equal(grok.signal, "exhausting");
  assert.equal(grok.band, "unknown");
  assert.equal(grok.stale, true);

  const nodes = render(fixture, { q: "" }, Date.parse("2026-10-16T16:30:00Z"));
  const quota = column(nodes, "外部额度");
  assert.match(quota, /36\.43%可派活/);
  assert.match(quota, /86\.98%正常/);
  assert.match(quota, /50%即将耗尽/);
  assert.match(quota, /Third Party/);
  assert.match(quota, /cursor:tertiary/);
  assert.match(quota, /预警/);
  assert.match(quota, /24h \+16\.3pp · 今日 —/);
  assert.match(quota, /预计 4\.5 小时后耗尽 · 2026-10-16T21:00:00Z/);
  assert.match(quota, /采样 3 分钟/);
  assert.match(quota, /采样陈旧 · 16\.7 小时/);
  assert.match(quota, /未知/);
  assert.doesNotMatch(quota, /slot-label-hidden|危急|signal_reason/);
  const primaryAt = quota.indexOf("36.43%");
  const tertiaryAt = quota.indexOf("Third Party");
  assert.doesNotMatch(quota.slice(primaryAt, tertiaryAt), /预警|24h|预计耗尽/);

  const windowNode = findWindow(nodes, "cursor:tertiary");
  assert.equal(windowNode.children[3].attrs.title, undefined);
  const band = windowNode.children.find((child) => child.attrs?.title === "warning");
  assert.equal(textOf(band), "预警");
  assert.equal(usageRefreshKey(fixture) === usageRefreshKey(structuredClone(fixture)), true);
  const shifted = structuredClone(fixture);
  shifted.external_quotas[0].windows[1].band = "watch";
  assert.notEqual(usageRefreshKey(shifted), usageRefreshKey(fixture));
});

test("search uses provider:window_key and the Third Party alias", () => {
  for (const q of ["third party", "cursor:tertiary", "预警"]) {
    const quota = column(render(fixture, { q }), "外部额度");
    assert.match(quota, /Third Party/);
    assert.doesNotMatch(quota, /50%/);
  }
  const hidden = column(render(fixture, { q: "slot-label-hidden" }), "外部额度");
  assert.match(hidden, /没有匹配的额度/);
  assert.doesNotMatch(hidden, /Third Party/);
});

test("null forecast fields stay visible as gaps and a stale window does not keep its old band", () => {
  const payload = {
    generated_at: "2026-10-16T00:00:00Z",
    days: 14,
    snapshot_state: "ok",
    external_quotas: [
      {
        provider: "codex",
        status: "ok",
        source: "codexbar",
        updated_at: "2026-10-16T00:00:00Z",
        last_success_at: "2026-10-16T00:00:00Z",
        sample_age_s: null,
        stale: false,
        windows: [
          {
            key: "primary",
            label: "5h",
            used_percent: 12,
            resets_at: "2026-10-16T21:00:00Z",
            signal: "dispatchable",
            band: null,
            delta_24h_percent: null,
            daily_delta: null,
            eta_exhausted_at: null,
          },
        ],
      },
      {
        provider: "cursor",
        status: "ok",
        source: "codexbar",
        windows: [
          {
            key: "tertiary",
            label: "slot-label-hidden",
            used_percent: 96,
            signal: "normal",
            band: "critical",
            stale: true,
            sample_age_s: 5400,
            delta_24h_percent: -2,
          },
        ],
      },
    ],
    signals: { dispatchable: [], exhausting: [] },
    multica_daily: { by_runtime: [] },
  };
  const quota = column(render(payload), "外部额度");
  assert.match(quota, /12%可派活/);
  assert.match(quota, /24h — · 今日 —/);
  assert.match(quota, /预计耗尽 —/);
  assert.match(quota, /采样 —/);
  assert.match(quota, /Third Party/);
  assert.match(quota, /cursor:tertiary/);
  assert.match(quota, /96%正常/);
  assert.match(quota, /未知/);
  assert.match(quota, /采样陈旧 · 1\.5 小时/);
  assert.match(quota, /24h -2pp/);
  assert.doesNotMatch(quota, /危急|slot-label-hidden/);
  assert.equal(formatDeltaPp(0), "0pp");
  assert.equal(formatDeltaPp(null), null);
  assert.equal(formatSampleAge(-1), null);
});

test("a window without forecast fields does not invent a band or a sample age", () => {
  const payload = {
    generated_at: "2026-10-16T00:00:00Z",
    days: 14,
    snapshot_state: "ok",
    external_quotas: [
      {
        provider: "codex",
        status: "ok",
        source: "codexbar",
        updated_at: "2026-10-16T00:00:00Z",
        last_success_at: "2026-10-16T00:00:00Z",
        windows: [{ key: "primary", label: "5h", used_percent: 12, resets_at: "2026-10-16T21:00:00Z", signal: "normal" }],
      },
    ],
    signals: { dispatchable: [], exhausting: [] },
    multica_daily: { by_runtime: [] },
  };
  const nodes = render(payload);
  const row = textOf(findWindow(nodes, "codex:primary"));
  assert.match(row, /^5h12%正常2026-10-16T21:00:00Z$/);
  const windowModel = modelUsage(payload).quotas[0].windows[0];
  assert.equal(windowModel.band, null);
  assert.equal(windowModel.band_present, false);
  assert.equal(modelUsage(payload).quotas[0].freshness.present, false);
  assert.equal(modelUsage(payload).quotas[0].freshness.stale_present, false);
});

test("a missing band stays off the row and an explicit null band shows a gap", () => {
  const base = {
    generated_at: "2026-10-16T00:00:00Z",
    days: 14,
    snapshot_state: "ok",
    signals: { dispatchable: [], exhausting: [] },
    multica_daily: { by_runtime: [] },
  };
  const omitted = {
    ...base,
    external_quotas: [
      {
        provider: "codex",
        status: "ok",
        source: "codexbar",
        windows: [{ key: "primary", label: "5h", used_percent: 12, resets_at: "2026-10-16T21:00:00Z", signal: "normal" }],
      },
    ],
  };
  const explicit = {
    ...base,
    external_quotas: [
      {
        provider: "codex",
        status: "ok",
        source: "codexbar",
        windows: [{ key: "primary", label: "5h", used_percent: 12, resets_at: "2026-10-16T21:00:00Z", signal: "normal", band: null }],
      },
    ],
  };
  const omittedNodes = render(omitted);
  const omittedRow = findWindow(omittedNodes, "codex:primary");
  assert.equal(textOf(omittedRow), "5h12%正常2026-10-16T21:00:00Z");
  assert.equal(omittedRow.children.some((child) => child.attrs?.title === "band"), false);
  assert.equal(modelUsage(omitted).quotas[0].windows[0].band_present, false);

  const explicitNodes = render(explicit);
  const explicitModel = modelUsage(explicit).quotas[0].windows[0];
  const explicitRow = findWindow(explicitNodes, "codex:primary");
  const gap = explicitRow.children.find((child) => child.attrs?.title === "band");
  assert.equal(explicitModel.band_present, true);
  assert.equal(explicitModel.band, null);
  assert.equal(textOf(gap), "—");
  assert.doesNotMatch(textOf(explicitRow), /常规|关注|预警|危急|耗尽|未知/);
  assert.notEqual(usageRefreshKey(omitted), usageRefreshKey(explicit));
});

test("stale null shows a gap and a missing stale field stays hidden", () => {
  const base = {
    generated_at: "2026-10-16T00:00:00Z",
    days: 14,
    snapshot_state: "ok",
    signals: { dispatchable: [], exhausting: [] },
    multica_daily: { by_runtime: [] },
  };
  const omitted = {
    ...base,
    external_quotas: [
      {
        provider: "codex",
        status: "ok",
        source: "codexbar",
        windows: [{ key: "primary", label: "5h", used_percent: 40, signal: "normal", band: "watch" }],
      },
    ],
  };
  const explicit = {
    ...base,
    external_quotas: [
      {
        provider: "codex",
        status: "ok",
        source: "codexbar",
        stale: null,
        windows: [{ key: "primary", label: "5h", used_percent: 40, signal: "normal", band: "watch" }],
      },
      {
        provider: "cursor",
        status: "ok",
        source: "codexbar",
        windows: [{ key: "tertiary", label: "slot-label-hidden", used_percent: 40, signal: "normal", band: "watch", stale: null }],
      },
    ],
  };
  const omittedText = textOf(findWindow(render(omitted), "codex:primary"));
  assert.match(omittedText, /关注/);
  assert.doesNotMatch(omittedText, /陈旧|未知/);
  assert.equal(modelUsage(omitted).quotas[0].freshness.stale_present, false);
  assert.equal(modelUsage(omitted).quotas[0].windows[0].freshness.stale_present, false);
  assert.equal(modelUsage(omitted).quotas[0].windows[0].stale, false);

  const explicitNodes = render(explicit);
  const explicitQuota = column(explicitNodes, "外部额度");
  const providerModel = modelUsage(explicit).quotas[0];
  const windowModel = modelUsage(explicit).quotas[1].windows[0];
  const windowText = textOf(findWindow(explicitNodes, "cursor:tertiary"));
  assert.match(explicitQuota, /陈旧 —/);
  assert.match(windowText, /关注/);
  assert.match(windowText, /陈旧 —/);
  assert.doesNotMatch(windowText, /未知|采样陈旧/);
  assert.equal(providerModel.freshness.stale_present, true);
  assert.equal(providerModel.freshness.stale_value, null);
  assert.equal(providerModel.freshness.stale, false);
  assert.equal(providerModel.windows[0].band, "watch");
  assert.equal(windowModel.freshness.stale_present, true);
  assert.equal(windowModel.freshness.stale_value, null);
  assert.equal(windowModel.stale, false);
  assert.equal(windowModel.band, "watch");
  assert.notEqual(usageRefreshKey(omitted), usageRefreshKey(explicit));
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

function poolSectionNode(nodes) {
  let found = null;
  const walk = (node) => {
    if (!node || typeof node !== "object" || found) return;
    if (node.attrs?.["aria-label"] === "额度池利用") found = node;
    for (const child of node.children || []) walk(child);
  };
  for (const node of nodes) walk(node);
  return found;
}

function poolRow(nodes, alias) {
  let found = null;
  const walk = (node) => {
    if (!node || typeof node !== "object" || found) return;
    if (node.tag === "tr" && node.children?.length && textOf(node.children[0]) === alias) found = node;
    for (const child of node.children || []) walk(child);
  };
  for (const node of nodes) walk(node);
  return found;
}

function classTokens(node, out = []) {
  if (!node || typeof node !== "object") return out;
  if (typeof node.attrs?.class === "string") out.push(...node.attrs.class.split(/\s+/).filter(Boolean));
  for (const child of node.children || []) classTokens(child, out);
  return out;
}

test("quota pool lists five alias rows with unknowns, stale text and no color tags", () => {
  const model = poolModels(fixture);
  assert.equal(model.status, "ok");
  assert.equal(model.accounts.map((account) => account.alias).join(","), "6e,aq,he,mg,pe");
  assert.equal(model.accounts[0].pace_applicable, false);
  assert.equal(model.accounts[1].pace.ratio, 0.14);
  assert.equal(model.accounts[4].credits.present, false);

  const nodes = render(fixture);
  const pool = column(nodes, "额度池利用");
  assert.match(pool, /额度池利用/);
  assert.match(pool, /只读展示，不下结论/);
  assert.match(pool, /不跨账号相加/);
  assert.match(pool, /共 5 个别名有快照：6e \/ aq \/ he \/ mg \/ pe/);
  assert.match(pool, /无该字段/);
  assert.match(pool, /系统账号，不参与/);
  assert.match(pool, /节奏比 0\.14（当前快照）/);
  assert.match(pool, /节奏比 3\.01（当前快照）/);
  assert.match(pool, /节奏比 3\.26（当前快照）/);
  assert.match(pool, /窗口刚开始，无法计算/);
  assert.match(pool, /≤72 小时 · 建议阈值（未批准）/);
  assert.match(pool, /≤24 小时 · 建议阈值（未批准）/);
  assert.match(pool, /≤6 小时 · 建议阈值（未批准）/);
  assert.match(pool, /只提示，不建议使用/);
  assert.match(pool, /Tibo 是否预告：未核查/);
  assert.match(pool, /消耗速率：待积累/);
  assert.match(pool, /陈旧/);
  assert.match(pool, /最后更新 2026-10-16T00:40:00Z/);
  assert.match(pool, /Q1/);
  assert.match(pool, /Q3 闲置率/);
  assert.match(pool, /Q4 冷热并存/);
  assert.match(pool, /待积累（n=—，预计 11-02 后）/);
  assert.doesNotMatch(pool, /⚠|相加后的|总用量|百分比合计|\$|美元/);

  const free = poolRow(nodes, "6e");
  assert.equal(textOf(free.children[2]), "无该字段");
  assert.equal(textOf(free.children[3]), "无该字段");
  assert.match(textOf(free.children[5]), /62,500 Credits/);
  assert.equal(textOf(free.children[6]), "系统账号，不参与");

  const aq = poolRow(nodes, "aq");
  assert.match(textOf(aq.children[2]), /已用 21%/);
  assert.match(textOf(aq.children[3]), /已用 6% · 重置 2026-10-20T16:41:00Z/);
  assert.equal(textOf(aq.children[5]), "无该字段");

  const he = poolRow(nodes, "he");
  const creditsCell = textOf(he.children[5]);
  assert.match(creditsCell, /0 Credits/);
  assert.match(creditsCell, /采样 2026-10-16T16:42:30Z/);
  assert.doesNotMatch(creditsCell, /\$|美元|到期/);

  const mg = poolRow(nodes, "mg");
  assert.match(textOf(mg.children[7]), /陈旧/);
  assert.match(textOf(mg.children[7]), /最后更新 2026-10-16T00:40:00Z/);
  assert.match(textOf(mg.children[4]), /0 张/);

  const pe = poolRow(nodes, "pe");
  assert.equal(textOf(pe.children[5]), "无该字段");
  assert.match(textOf(pe.children[6]), /窗口刚开始，无法计算/);
  assert.match(textOf(pe.children[7]), /采样 2026-10-16T16:30:00Z/);

  const classes = classTokens(poolSectionNode(nodes));
  for (const tone of ["ok", "warn", "bad", "info", "quota", "tag"]) {
    assert.equal(classes.includes(tone), false, `class ${tone} must stay out of the pool section`);
  }
});

test("quota pool renders five alias rows with missing fields when the block is absent or failed", () => {
  const base = structuredClone(fixture);
  delete base.quota_pool;
  const nodes = render(base);
  const pool = column(nodes, "额度池利用");
  assert.match(pool, /用量快照里还没有 Codex 账号块/);
  assert.match(pool, /共 0 个别名有快照/);
  for (const alias of ["6e", "aq", "he", "mg", "pe"]) {
    const row = poolRow(nodes, alias);
    assert.ok(row, alias);
    assert.equal(textOf(row), `${alias}${"无该字段".repeat(7)}`);
  }

  const errored = structuredClone(fixture);
  errored.quota_pool.status = "error";
  const epool = column(render(errored), "额度池利用");
  assert.match(epool, /Codex 账号快照采集失败/);
  assert.doesNotMatch(epool, /已用 \d|节奏比 \d|系统账号，不参与|Credits\b.*\d 张/s);
});

test("quota pool drops email-shaped strings and unknown aliases", () => {
  const leaked = structuredClone(fixture);
  leaked.quota_pool.accounts[0].alias = "6e@example.com";
  leaked.quota_pool.accounts[0].plan = "owner@example.com";
  leaked.quota_pool.accounts.push({ alias: "zz", present: true });
  const model = poolModels(leaked);
  assert.equal(model.accounts.map((account) => account.alias).join(","), "aq,he,mg,pe");
  const pool = column(render(leaked), "额度池利用");
  assert.doesNotMatch(pool, /example\.com|@|zz/);
});

test("usageRefreshKey tracks quota pool fields", () => {
  for (const mutate of [
    (copy) => { copy.quota_pool.accounts[0].source_updated_at = "2026-10-16T16:45:00Z"; },
    (copy) => { copy.quota_pool.accounts[3].stale = false; },
    (copy) => { copy.quota_pool.accounts[4].credits = { present: true, remaining: 5 }; },
    (copy) => { copy.quota_pool.accounts[1].windows["5h"].used_percent = 30; },
    (copy) => { copy.quota_pool.status = "error"; },
  ]) {
    const shifted = structuredClone(fixture);
    mutate(shifted);
    assert.notEqual(usageRefreshKey(shifted), usageRefreshKey(fixture));
  }
});
