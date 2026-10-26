import { runtimeIcon } from "./runtime-mark.js";
/** Read-only usage page. Counts and signals come from GET /api/usage; this file only displays them. */

const EMAIL = /[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}/;
const SHORT_TOKEN = /^[a-z][a-z0-9_-]{0,31}$/;

const COUNT_FIELDS = ["runs_total", "completed", "capacity_failed", "true_failed", "cancelled", "quota_failed", "rate_limit_failed"];
const TOKEN_FIELDS = ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd_ticks"];

const COUNT_LABEL = {
  runs_total: "运行",
  completed: "完成",
  capacity_failed: "满载失败",
  true_failed: "真失败",
  cancelled: "取消",
};

const TOKEN_LABEL = {
  input_tokens: "输入 token",
  output_tokens: "输出 token",
  cache_read_tokens: "缓存读 token",
  cache_write_tokens: "缓存写 token",
  cost_usd_ticks: "cost ticks",
};

const SIGNAL_LABEL = {
  dispatchable: "可派活",
  exhausting: "即将耗尽",
  normal: "正常",
};

const SIGNAL_CLASS = {
  dispatchable: "info",
  exhausting: "bad",
  normal: "",
};

const BAND_LABEL = {
  normal: "常规",
  watch: "关注",
  warning: "预警",
  critical: "危急",
  exhausted: "耗尽",
  unknown: "未知",
};

const BAND_CLASS = {
  normal: "ok",
  watch: "info",
  warning: "warn",
  critical: "bad",
  exhausted: "quota",
  unknown: "muted",
};

// Identity is provider:window_key. The tertiary slot is Third Party even when the source label differs.
const WINDOW_ALIAS = {
  "cursor:tertiary": "Third Party",
};

const STATUS_LABEL = {
  ok: "正常",
  error: "失败",
  unconnected: "未接入",
};

const STATUS_CLASS = {
  ok: "",
  error: "bad",
  unconnected: "muted",
};

const SNAPSHOT_LABEL = {
  ok: "快照正常",
  partial: "快照不完整",
  error: "快照采集失败",
  empty: "快照是空的",
  missing: "还没有用量快照",
};

const SPLIT_NOTE = "Multica 的 token / cost 与 CodexBar 额度是两套口径，分栏展示，不相加。";
const FAILURE_GAP = "耗时和原始 error 文本不在 /api/usage。这一栏只显示满载失败和真失败的计数，不补这两项。";
const FAILURE_PARTS = "额度失败和限流失败已经计在满载失败里，这里分开列出，不再加一次。";

export function safeLabel(value) {
  if (typeof value !== "string") return "";
  const text = value.trim();
  if (!text || EMAIL.test(text)) return "";
  return text;
}

function whole(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return 0;
  return value;
}

export function formatCount(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(value);
}

export function formatPercent(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const rounded = Math.round(value * 100) / 100;
  return `${rounded}%`;
}

function trimNumber(value) {
  return String(Math.round(value * 10) / 10);
}

export function formatDeltaPp(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  const rounded = Math.round(value * 100) / 100;
  if (rounded === 0) return "0pp";
  return `${rounded > 0 ? "+" : ""}${rounded}pp`;
}

export function formatSampleAge(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  if (seconds < 48 * 3600) return `${trimNumber(seconds / 3600)} 小时`;
  return `${trimNumber(seconds / 86400)} 天`;
}

function ownField(row, key) {
  if (!row || typeof row !== "object" || !Object.hasOwn(row, key)) return { present: false, value: null };
  return { present: true, value: row[key] };
}

function numberField(row, key) {
  const field = ownField(row, key);
  if (!field.present) return field;
  const value = typeof field.value === "number" && Number.isFinite(field.value) ? field.value : null;
  return { present: true, value };
}

function timeField(row, key) {
  const field = ownField(row, key);
  if (!field.present) return field;
  return { present: true, value: safeLabel(field.value) || null };
}

function knownBand(value) {
  return typeof value === "string" && Object.hasOwn(BAND_LABEL, value) ? value : null;
}

function freshnessOf(row) {
  const age = numberField(row, "sample_age_s");
  const staleField = ownField(row, "stale");
  return {
    present: age.present || staleField.present,
    age_present: age.present,
    sample_age_s: age.value,
    stale: staleField.value === true,
  };
}

function windowIdentity(provider, key) {
  const providerToken = safeLabel(provider);
  const keyToken = safeLabel(key);
  if (!providerToken || !keyToken) return "";
  return `${providerToken}:${keyToken}`;
}

function freshnessText(freshness) {
  if (!freshness?.present) return "";
  const age = freshness.age_present ? formatSampleAge(freshness.sample_age_s) : null;
  if (freshness.stale) return age ? `采样陈旧 · ${age}` : "采样陈旧";
  if (age) return `采样 ${age}`;
  if (freshness.age_present) return "采样 —";
  return "";
}

function windowDays(payload) {
  const days = payload?.days;
  if (typeof days === "number" && Number.isInteger(days) && days > 0) return days;
  return null;
}

function snapshotLabel(value) {
  if (typeof value === "string" && SNAPSHOT_LABEL[value]) return SNAPSHOT_LABEL[value];
  if (typeof value === "string" && SHORT_TOKEN.test(value)) return value;
  return "快照状态未知";
}

function sumFields(rows, fields) {
  const totals = {};
  for (const field of fields) totals[field] = 0;
  for (const row of rows) {
    for (const field of fields) totals[field] += whole(row?.[field]);
  }
  return totals;
}

function dayModel(row) {
  if (!row || typeof row !== "object") return null;
  const date = safeLabel(row.date);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  const counts = {};
  for (const field of [...COUNT_FIELDS, ...TOKEN_FIELDS]) counts[field] = whole(row[field]);
  return { date, ...counts };
}

export function runtimeModels(payload) {
  const rows = payload?.multica_daily?.by_runtime;
  if (!Array.isArray(rows)) return [];
  return rows.map((row) => {
    const daily = (Array.isArray(row?.daily) ? row.daily : []).map(dayModel).filter(Boolean);
    daily.sort((a, b) => a.date.localeCompare(b.date));
    return {
      name: safeLabel(row?.name) || "未命名 runtime",
      provider: safeLabel(row?.provider) || "—",
      workspace: safeLabel(row?.workspace_slug) || "—",
      daily,
      ...sumFields(daily, [...COUNT_FIELDS, ...TOKEN_FIELDS]),
    };
  });
}

function signalToken(value) {
  if (value === "dispatchable" || value === "exhausting" || value === "normal") return value;
  if (typeof value === "string" && SHORT_TOKEN.test(value)) return value;
  return "normal";
}

function windowModel(row, providerRow) {
  if (!row || typeof row !== "object") return null;
  const used = row.used_percent;
  const identity = windowIdentity(providerRow?.provider, row.key);
  const ownFreshness = freshnessOf(row);
  const stale = ownFreshness.stale || freshnessOf(providerRow).stale;
  return {
    key: safeLabel(row.key) || "window",
    identity,
    label: WINDOW_ALIAS[identity] || safeLabel(row.label) || safeLabel(row.key) || "窗口",
    used_percent: typeof used === "number" && Number.isFinite(used) ? used : null,
    resets_at: safeLabel(row.resets_at) || null,
    signal: signalToken(row.signal),
    band: stale ? "unknown" : knownBand(row.band),
    stale,
    delta_24h: numberField(row, "delta_24h_percent"),
    daily_delta: numberField(row, "daily_delta"),
    eta_exhausted_at: timeField(row, "eta_exhausted_at"),
    freshness: ownFreshness,
  };
}

export function quotaModels(payload) {
  const rows = payload?.external_quotas;
  if (!Array.isArray(rows)) return [];
  return rows.map((row) => {
    const status = row?.status === "ok" || row?.status === "error" || row?.status === "unconnected" ? row.status : "error";
    return {
      provider: safeLabel(row?.provider) || "—",
      status,
      source: safeLabel(row?.source) || "—",
      updated_at: safeLabel(row?.updated_at) || null,
      last_success_at: safeLabel(row?.last_success_at) || null,
      freshness: freshnessOf(row),
      windows: status === "unconnected" ? [] : (Array.isArray(row?.windows) ? row.windows : []).map((window) => windowModel(window, row)).filter(Boolean),
    };
  });
}

function signalModels(rows) {
  if (!Array.isArray(rows)) return [];
  return rows.map((row) => {
    const identity = windowIdentity(row?.provider, row?.window_key);
    return {
      provider: safeLabel(row?.provider) || "—",
      identity,
      label: WINDOW_ALIAS[identity] || safeLabel(row?.label) || safeLabel(row?.window_key) || "窗口",
      resets_at: safeLabel(row?.resets_at) || null,
      remaining_percent: typeof row?.remaining_percent === "number" && Number.isFinite(row.remaining_percent) ? row.remaining_percent : null,
    };
  });
}

export function modelUsage(payload) {
  const signals = payload?.signals && typeof payload.signals === "object" ? payload.signals : {};
  return {
    days: windowDays(payload),
    collectedAt: safeLabel(payload?.usage_collected_at) || null,
    snapshot: snapshotLabel(payload?.snapshot_state),
    note: safeLabel(payload?.multica_daily?.note) || "",
    runtimes: runtimeModels(payload),
    quotas: quotaModels(payload),
    signals: {
      dispatchable: signalModels(signals.dispatchable),
      exhausting: signalModels(signals.exhausting),
    },
  };
}

function keyedField(field) {
  if (!field?.present) return "";
  return field.value == null ? "null" : String(field.value);
}

function quotaKey(item) {
  const windows = item.windows
    .map((window) =>
      [
        window.identity,
        window.signal,
        window.band || "",
        window.stale ? "stale" : "",
        keyedField(window.delta_24h),
        keyedField(window.daily_delta),
        keyedField(window.eta_exhausted_at),
        window.freshness.age_present ? String(window.freshness.sample_age_s) : "",
      ].join("~"),
    )
    .join(";");
  return [
    item.provider,
    item.status,
    item.updated_at || "",
    item.freshness.stale ? "stale" : "",
    item.freshness.age_present ? String(item.freshness.sample_age_s) : "",
    windows,
  ].join(":");
}

export function usageRefreshKey(payload) {
  if (!payload) return "";
  const model = payload.error ? null : modelUsage(payload);
  return [
    payload.error || "",
    payload.usage_collected_at || "",
    payload.generated_at || "",
    payload.snapshot_state || "",
    payload.days ?? "",
    model ? model.signals.exhausting.length : 0,
    model ? model.signals.dispatchable.length : 0,
    model ? model.runtimes.length : 0,
    model ? model.quotas.map(quotaKey).join(",") : "",
  ].join("\n");
}

export function usageSignal(payload) {
  if (!payload) return "用量加载中";
  if (payload.error) return "用量还没接上";
  const model = modelUsage(payload);
  if (model.signals.exhausting.length) return `${model.signals.exhausting.length} 个窗口即将耗尽`;
  if (payload.snapshot_state === "missing") return "快照未采集";
  const days = model.days ?? "—";
  return `近 ${days} 天 · ${model.runtimes.length} 个 runtime`;
}

export function usageAlert(payload) {
  if (!payload || payload.error) return false;
  return modelUsage(payload).signals.exhausting.length > 0;
}

function queryOf(route) {
  return String(route?.q || "").trim().toLowerCase();
}

function matches(query, ...parts) {
  if (!query) return true;
  return parts.join(" ").toLowerCase().includes(query);
}

function when(ctx, iso, { absolute = false } = {}) {
  if (!iso) return "—";
  try {
    const abs = ctx.absTime ? ctx.absTime(iso) : "";
    if (absolute) return abs || iso;
    const rel = ctx.ago ? ctx.ago(iso) : "";
    if (abs && rel && rel !== "—") return `${abs}（${rel}）`;
    return abs || iso;
  } catch {
    return iso;
  }
}

function metric(h, label, value, extra) {
  return h(
    "div",
    { class: "usage-metric" },
    h("span", null, label),
    h("b", extra || null, value),
  );
}

function countLine(h, totals, fields) {
  return h(
    "div",
    { class: "usage-metrics" },
    ...fields.map((field) => metric(h, COUNT_LABEL[field] || field, formatCount(totals[field]))),
  );
}

function tokenLine(h, totals) {
  return h(
    "div",
    { class: "usage-metrics" },
    ...TOKEN_FIELDS.map((field) =>
      metric(h, TOKEN_LABEL[field], formatCount(totals[field]), field === "cost_usd_ticks" ? { title: "接口字段 cost_usd_ticks，不换算成美元" } : null),
    ),
  );
}

function runtimeCard(ctx, row) {
  const { h } = ctx;
  const dates = row.daily.map((day) => day.date);
  const span = dates.length > 1 ? `${dates[0]} – ${dates[dates.length - 1]}` : dates[0] || "这个窗里没有日计数";
  return h(
    "article",
    { class: "usage-card" },
    h("h4", null, runtimeIcon(h, row.provider), row.name),
    h("p", { class: "muted" }, `${row.provider} · ${row.workspace}`),
    h("p", { class: "muted" }, span),
    countLine(h, row, ["runs_total", "completed", "capacity_failed", "true_failed", "cancelled"]),
    tokenLine(h, row),
    row.daily.length > 1
      ? h(
          "div",
          { class: "usage-days" },
          h("p", { class: "muted" }, "按日"),
          ...row.daily.map((day) =>
            h(
              "p",
              { class: "usage-day" },
              h("span", { class: "mono" }, day.date),
              ` 运行 ${formatCount(day.runs_total)} · 满载失败 ${formatCount(day.capacity_failed)} · 真失败 ${formatCount(day.true_failed)}`,
            ),
          ),
        )
      : null,
  );
}

function signalList(ctx, title, rows, tone) {
  const { h } = ctx;
  return h(
    "div",
    { class: "usage-signals" },
    h("h4", null, title),
    rows.length
      ? rows.map((row) =>
          h(
            "p",
            { class: "usage-signal", title: row.identity || undefined },
            h("span", { class: `tag ${SIGNAL_CLASS[tone] || ""}`.trim() }, SIGNAL_LABEL[tone] || tone),
            runtimeIcon(h, row.provider),
            ` ${row.provider} · ${row.label}`,
            row.remaining_percent == null ? null : ` · 剩余 ${formatPercent(row.remaining_percent)}`,
            row.resets_at ? h("span", { class: "muted" }, ` · ${when(ctx, row.resets_at, { absolute: true })}`) : null,
          ),
        )
      : h("p", { class: "muted" }, "无"),
  );
}

function deltaText(window) {
  const parts = [];
  if (window.delta_24h.present) parts.push(`24h ${formatDeltaPp(window.delta_24h.value) || "—"}`);
  if (window.daily_delta.present) parts.push(`今日 ${formatDeltaPp(window.daily_delta.value) || "—"}`);
  return parts.join(" · ");
}

function etaText(ctx, field) {
  if (!field.present) return "";
  if (!field.value) return "预计耗尽 —";
  const abs = when(ctx, field.value, { absolute: true });
  const at = Date.parse(field.value);
  const nowMs = ctx.nowMs;
  if (typeof nowMs !== "number" || !Number.isFinite(nowMs) || !Number.isFinite(at) || at <= nowMs) return `预计耗尽 ${abs}`;
  const hours = (at - nowMs) / 3600000;
  const lead = hours < 48 ? `${trimNumber(hours)} 小时` : `${trimNumber(hours / 24)} 天`;
  return `预计 ${lead}后耗尽 · ${abs}`;
}

function windowRow(ctx, window, providerFreshness) {
  const { h } = ctx;
  const ownText = freshnessText(window.freshness);
  const providerText = freshnessText(providerFreshness);
  const ageText = ownText && ownText !== providerText ? ownText : "";
  return h(
    "div",
    { class: "usage-window", "data-window": window.identity || undefined },
    h("span", { title: window.identity || undefined }, window.label),
    WINDOW_ALIAS[window.identity] ? h("span", { class: "muted mono" }, window.identity) : null,
    h("b", null, formatPercent(window.used_percent)),
    h("span", { class: `tag ${SIGNAL_CLASS[window.signal] || ""}`.trim() }, SIGNAL_LABEL[window.signal] || window.signal),
    h("span", { class: "muted" }, when(ctx, window.resets_at, { absolute: true })),
    window.band
      ? h("span", { class: `tag ${BAND_CLASS[window.band] || "muted"}`.trim(), title: window.band }, BAND_LABEL[window.band] || window.band)
      : null,
    deltaText(window) ? h("span", { class: "muted" }, deltaText(window)) : null,
    etaText(ctx, window.eta_exhausted_at) ? h("span", { class: "muted" }, etaText(ctx, window.eta_exhausted_at)) : null,
    ageText ? h("span", { class: "muted" }, ageText) : null,
  );
}

function quotaCard(ctx, row) {
  const { h } = ctx;
  return h(
    "article",
    { class: "usage-card" },
    h("h4", null, runtimeIcon(h, row.provider), row.provider),
    h("p", null, h("span", { class: `tag ${STATUS_CLASS[row.status] || ""}`.trim() }, STATUS_LABEL[row.status] || row.status)),
    row.status === "unconnected"
      ? null
      : h(
          "div",
          null,
          h("p", { class: "muted" }, `来源 ${row.source}`),
          h("p", { class: "muted" }, `更新 ${when(ctx, row.updated_at)}`),
          h("p", { class: "muted" }, `最近成功 ${when(ctx, row.last_success_at)}`),
          freshnessText(row.freshness) ? h("p", { class: "muted" }, freshnessText(row.freshness)) : null,
          row.windows.length
            ? row.windows.map((window) => windowRow(ctx, window, row.freshness))
            : h("p", { class: "muted" }, "没有窗口"),
        ),
  );
}

function failureCard(ctx, row) {
  const { h } = ctx;
  return h(
    "article",
    { class: "usage-card" },
    h("h4", null, runtimeIcon(h, row.provider), row.name),
    h("p", { class: "muted" }, `${row.provider} · ${row.workspace}`),
    metric(h, "满载失败", formatCount(row.capacity_failed)),
    metric(h, "额度失败", formatCount(row.quota_failed)),
    metric(h, "限流失败", formatCount(row.rate_limit_failed)),
    metric(h, "真失败", formatCount(row.true_failed)),
  );
}

export function renderUsage(ctx) {
  const { h } = ctx;
  const payload = ctx.usage;
  if (!payload) return [h("div", { class: "empty big" }, "正在读取用量…")];
  if (payload.error) {
    return [h("section", { class: "atlas-guide" }, h("h2", null, "用量还没接上"), h("p", null, safeLabel(payload.error) || "用量接口读不出来。"))];
  }
  const model = modelUsage(payload);
  const query = queryOf(ctx.route);
  const runtimes = model.runtimes.filter((row) => matches(query, row.name, row.provider, row.workspace));
  const quotas = model.quotas.filter((row) =>
    matches(
      query,
      row.provider,
      STATUS_LABEL[row.status] || "",
      freshnessText(row.freshness),
      ...row.windows.flatMap((window) => [window.label, window.identity, BAND_LABEL[window.band] || "", deltaText(window)]),
    ),
  );
  const signalHit = (row) => matches(query, row.provider, row.label, row.identity);
  const dispatchable = model.signals.dispatchable.filter(signalHit);
  const exhausting = model.signals.exhausting.filter(signalHit);
  const windowText = model.days ? `近 ${model.days} 天` : "时间窗未知";
  const collected = model.collectedAt ? when(ctx, model.collectedAt) : "尚未采集";

  return [
    h(
      "header",
      { class: "usage-head" },
      h("h2", null, "用量与额度"),
      h("p", null, h("a", { href: "#/map/l1" }, "返回 L1 地图")),
      h("p", null, `${windowText} · 采集于 ${collected} · ${model.snapshot}`),
      h("p", { class: "usage-split" }, SPLIT_NOTE),
      model.note ? h("p", { class: "muted" }, model.note) : null,
    ),
    h(
      "div",
      { class: "usage-columns" },
      h(
        "section",
        { class: "usage-col", "aria-label": "Multica 运行层" },
        h("h3", null, "Multica 运行层"),
        h("p", { class: "muted" }, "按 runtime 汇总这个时间窗。token 计数和 cost ticks 只在这一栏。"),
        runtimes.length ? runtimes.map((row) => runtimeCard(ctx, row)) : h("p", { class: "empty" }, query ? "没有匹配的 runtime" : "这个时间窗里没有 runtime 用量。"),
      ),
      h(
        "section",
        { class: "usage-col", "aria-label": "外部额度" },
        h("h3", null, "外部额度"),
        h("p", { class: "muted" }, "按 provider 显示窗口用量。可派活和即将耗尽直接用接口给出的标记。"),
        h("p", { class: "muted" }, "分带、24h 增量、今日增量和预计耗尽只展示接口字段。采样陈旧时分带显示未知，不沿用旧分带。"),
        signalList(ctx, "可派活", dispatchable, "dispatchable"),
        signalList(ctx, "即将耗尽", exhausting, "exhausting"),
        quotas.length ? quotas.map((row) => quotaCard(ctx, row)) : h("p", { class: "empty" }, query ? "没有匹配的额度" : "没有额度数据。"),
      ),
      h(
        "section",
        { class: "usage-col", "aria-label": "失败" },
        h("h3", null, "失败"),
        h("p", { class: "muted" }, "同一时间窗里的失败计数，按 runtime 列出。"),
        h("p", { class: "usage-gap" }, FAILURE_GAP),
        h("p", { class: "muted" }, FAILURE_PARTS),
        runtimes.length ? runtimes.map((row) => failureCard(ctx, row)) : h("p", { class: "empty" }, query ? "没有匹配的 runtime" : "这个时间窗里没有失败计数。"),
      ),
    ),
  ];
}
