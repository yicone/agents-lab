/** 职责说明对账子视图（Agents ?kind=diff）：Git / 看板导出 / 线上三方只读对账。 */

import { lineDiff } from "./agents-view.js";

export const DIFF_SCOPE = "对账范围：已登记到 Git 的 Agent 与 Grok Bot；workspace 筛选不作用于本页";

export const DRIFT_LABEL = {
  aligned: "一致",
  modified: "已修改",
  split: "三方不一致",
  stale: "过期/未采集",
  withheld: "已隐藏",
};
export const DRIFT_TONE = { aligned: "ok", modified: "bad", split: "bad", stale: "warn", withheld: "muted" };
export const DRIFT_ORDER = ["aligned", "modified", "split", "stale", "withheld"];

const REASON_LABEL = {
  board_body_missing: "看板正文缺失",
  board_hash_mismatch: "看板导出校验失败",
  git_hash_missing: "Git 未纳管或缺哈希",
  board_hash_missing: "看板导出缺哈希",
  live_hash_missing: "线上未采集或缺哈希",
  board_stale: "看板导出过期",
  live_stale: "线上数据过期",
  live_unknown: "线上观测时间未知",
  board_outdated: "看板导出落后于 Git",
};
const SOURCE_LABEL = { board: "看板导出", agent: "Agent 线上", bot: "Bot 线上" };
const PREVIEW_LINES = 20;

export function hmOf(iso) {
  const t = Date.parse(iso || "");
  if (!Number.isFinite(t)) return "";
  return new Date(t).toLocaleString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Singapore" });
}

export function shortHash(value) {
  return typeof value === "string" && value ? value.slice(0, 8) : "—";
}

export function itemKey(item) {
  return `${item?.kind || ""}:${item?.workspace_id || ""}:${item?.id || ""}`;
}

export function reasonText(item) {
  const reasons = Array.isArray(item?.stale_reasons) ? item.stale_reasons : [];
  if (!reasons.length) {
    if (item?.drift === "split") return "三方两两不同";
    if (item?.drift === "modified")
      return item.git_sha256 && item.git_sha256 === item.board_sha256 ? "线上与 Git 不同" : "Git 与线上、看板不同";
    if (item?.drift === "withheld") return "内容不公开";
    return "—";
  }
  const shown = reasons.slice(0, 2).map((r) => REASON_LABEL[r] || r);
  return shown.join("、") + (reasons.length > 2 ? ` +${reasons.length - 2}` : "");
}

export function errorText(error) {
  if (!error || typeof error !== "object") return "检查失败";
  return `检查失败：${error.code || "unavailable"}`;
}

/** Partial-read failures shown as a warning strip above the list. */
export function warningLines(data) {
  const lines = [];
  const errors = data?.source_errors || {};
  for (const key of ["board", "agent", "bot"]) {
    if (errors[key]) lines.push(`${SOURCE_LABEL[key]} 不可用`);
  }
  const blocked = Array.isArray(data?.inaccessible_workspaces) ? data.inaccessible_workspaces.length : 0;
  if (blocked) lines.push(`${blocked} 个 workspace 无权读取`);
  return lines;
}

/** 「全部一致」只能在 total>0、无来源错误且三方数据都新鲜时成立。 */
export function allAligned(data) {
  const summary = data?.summary || {};
  const freshness = data?.freshness || {};
  return (summary.total || 0) > 0 && summary.aligned === summary.total
    && Object.keys(data?.source_errors || {}).length === 0
    && freshness.board === "fresh" && freshness.agent === "fresh" && freshness.bot === "fresh";
}

/** 无差异但因来源未达标而不能宣称「全部一致」时的原因行。 */
export function shortfallLines(data) {
  const lines = warningLines(data);
  const freshness = data?.freshness || {};
  for (const key of ["board", "agent", "bot"]) {
    if (freshness[key] && freshness[key] !== "fresh") lines.push(`${SOURCE_LABEL[key]}数据过期`);
  }
  return lines;
}

export function filteredItems(data, filter, q, wsOf, matches) {
  const items = Array.isArray(data?.items) ? data.items : [];
  const test = typeof matches === "function" ? matches : (query, ...fields) => fields.some((f) => f && String(f).toLowerCase().includes(String(query).toLowerCase()));
  return items.filter((item) => {
    if (filter && item?.drift !== filter) return false;
    if (q) {
      const ws = typeof wsOf === "function" ? wsOf(item?.workspace_id) : null;
      if (!test(q, item?.name, item?.id, ws?.name, ws?.slug)) return false;
    }
    return true;
  });
}

function previewBlock(h, label, text) {
  const lines = typeof text === "string" && text.trim()
    ? text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim().split("\n")
    : [];
  const shown = lines.slice(0, PREVIEW_LINES);
  return h(
    "div",
    { class: "diff-side" },
    h("h4", null, label),
    shown.length
      ? h("pre", { class: "diff-preview" }, shown.join("\n") + (lines.length > PREVIEW_LINES ? `\n…（共 ${lines.length} 行）` : ""))
      : h("p", { class: "muted" }, "正文不可用"),
  );
}

function detailPanel(h, ctx, item) {
  const gitEntry = item.kind === "agent"
    ? ctx.sources?.agents?.[`${item.workspace_id}/${item.id}`]
    : ctx.sources?.bots?.[item.id];
  const live = item.kind === "agent"
    ? (ctx.agents || []).find((a) => a.id === item.id && a.workspace_id === item.workspace_id)
    : (ctx.bots || []).find((b) => b.id === item.id);
  const gitText = typeof gitEntry?.text === "string" ? gitEntry.text : null;
  const liveText = item.kind === "agent"
    ? (typeof live?.instructions === "string" ? live.instructions : null)
    : (typeof live?.description === "string" ? live.description : null);
  const rows = gitText != null && liveText != null ? lineDiff(gitText, liveText) : null;
  const adds = rows ? rows.filter((r) => r.op === "+").length : null;
  const dels = rows ? rows.filter((r) => r.op === "-").length : null;
  const hashLine = item.drift === "withheld"
    ? "三方哈希不公开"
    : `Git ${shortHash(item.git_sha256)} · 看板 ${shortHash(item.board_sha256)} · 线上 ${shortHash(item.live_sha256)}`;
  return h(
    "div",
    { class: "diff-detail" },
    h(
      "div",
      { class: "diff-detail-head" },
      h("span", { class: "mono" }, hashLine),
      rows ? h("span", { class: "muted" }, `行差异 +${adds} −${dels} · 各取前 ${PREVIEW_LINES} 行`) : null,
      ctx.openHref ? h("a", { href: ctx.openHref(item) }, "在 Agents 中打开 →") : null,
    ),
    h("div", { class: "diff-sides" }, previewBlock(h, "Git 真源", gitText), previewBlock(h, "当前线上（脱敏缓存）", liveText)),
    h("p", { class: "muted" }, "线上为脱敏缓存，以哈希为准。"),
  );
}

function row(h, ctx, item, wsOf) {
  const key = itemKey(item);
  const open = ctx.diff?.expanded?.has(key) === true;
  const ws = item.kind === "agent" ? wsOf(item.workspace_id) : null;
  const head = h(
    "tr",
    {
      class: `diff-row${open ? " is-selected" : ""}`,
      tabindex: "0",
      "aria-expanded": open ? "true" : "false",
      "aria-label": `${DRIFT_LABEL[item.drift] || item.drift}：${item.name || item.id}`,
      onclick: () => ctx.toggleRow?.(key),
      onkeydown: (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        ctx.toggleRow?.(key);
      },
    },
    h("td", null, item.kind === "agent" ? "Agent" : "Bot"),
    h("td", { class: "agent-name" }, item.name || item.id),
    h("td", null, item.kind === "agent" ? ws?.name || ws?.slug || item.workspace_id || "—" : "—"),
    h("td", null, h("span", { class: `tag ${DRIFT_TONE[item.drift] || "muted"}` }, DRIFT_LABEL[item.drift] || item.drift)),
    h("td", { class: "diff-reason" }, reasonText(item)),
    h("td", { class: "num", title: ctx.absTime ? ctx.absTime(item.updated_at) : "" }, hmOf(item.updated_at) || "—"),
  );
  if (!open) return head;
  return [head, h("tr", { class: "diff-detail-row" }, h("td", { colspan: 6 }, detailPanel(h, ctx, item)))];
}

function statusChips(h, ctx, data, current) {
  const summary = data?.summary || {};
  const items = Array.isArray(data?.items) ? data.items : [];
  const options = [["", `全部 ${items.length}`], ...DRIFT_ORDER.map((d) => [d, `${DRIFT_LABEL[d]} ${summary[d] || 0}`])];
  return h(
    "div",
    { class: "seg diff-chips", role: "group", "aria-label": "状态筛选" },
    options.map(([id, label]) =>
      h(
        "button",
        {
          type: "button",
          class: (current || "") === id ? "active" : "",
          "aria-pressed": (current || "") === id ? "true" : "false",
          onclick: () => ctx.setFilter?.(current === id ? "" : id),
        },
        label,
      ),
    ),
  );
}

function resultSection(h, ctx, data, diff) {
  const warnings = warningLines(data);
  const items = filteredItems(data, diff.filter, ctx.route?.q, (id) => (ctx.workspaces || []).find((w) => w.id === id), ctx.matchesQuery);
  const parts = [];
  if (warnings.length) {
    parts.push(
      h(
        "div",
        { class: "diff-warning", role: "status" },
        h("span", { class: "tag warn" }, "部分来源不可用"),
        " ",
        warnings.join(" · "),
        "；受影响行按过期/未采集显示，不计入已修改。",
      ),
    );
  }
  parts.push(statusChips(h, ctx, data, diff.filter));
  const total = data?.summary?.total || 0;
  if (total === 0) {
    parts.push(h("div", { class: "empty" }, "Git 里还没有登记任何 Agent / Grok Bot 描述。"));
  } else if (allAligned(data) && !diff.filter) {
    parts.push(h("div", { class: "empty" }, `${total} 项全部一致`));
  } else if (!items.length) {
    const shortfall = shortfallLines(data);
    parts.push(
      h(
        "div",
        { class: "empty" },
        data?.items?.length === 0
          ? `${data.summary.aligned || 0} 项无差异${shortfall.length ? `；未达「全部一致」条件：${shortfall.join(" · ")}` : ""}`
          : "没有符合条件的差异项",
      ),
    );
  } else {
    parts.push(
      h(
        "div",
        { class: "table-wrap sticky-head", tabindex: 0, role: "region", "aria-label": "职责说明对账" },
        h(
          "table",
          { class: "matrix diff-table" },
          h("thead", null, h("tr", null, ["类型", "名称", "workspace", "状态", "原因", "观测"].map((c) => h("th", { scope: "col" }, c)))),
          h("tbody", null, items.flatMap((item) => row(h, ctx, item, (id) => (ctx.workspaces || []).find((w) => w.id === id)))),
        ),
      ),
    );
  }
  return parts;
}

export function renderInstructionDiff(h, ctx) {
  const diff = ctx.diff || { status: "idle" };
  const data = diff.data;
  const button = h(
    "button",
    { type: "button", class: "diff-check", disabled: diff.status === "loading", onclick: () => ctx.onCheck?.() },
    diff.status === "loading" ? "检查中…" : "检查 / 刷新差异",
  );
  const bits = [];
  if (diff.status === "idle") bits.push("还没有检查。点击左侧按钮读取差异。");
  else if (diff.status === "loading") bits.push("检查中…");
  if (data?.generated_at && diff.status !== "error") {
    bits.push(`上次检查 ${hmOf(data.generated_at)}`);
    bits.push(`线上数据 ${ctx.liveAt ? ctx.ago(ctx.liveAt) : "未知"}`);
  }
  const bar = h("div", { class: "toolbar diff-bar" }, button, h("span", { class: "muted" }, bits.join(" · ")));

  if (diff.status === "error") {
    const stale = data ? h("div", { class: "diff-outdated" }, ...resultSection(h, ctx, data, diff)) : null;
    return [
      bar,
      h(
        "div",
        { class: "diff-error", role: "alert" },
        h("p", null, h("span", { class: "tag bad" }, errorText(diff.error))),
        diff.error?.message ? h("p", { class: "diff-error-msg" }, diff.error.message) : null,
        diff.lastOkAt ? h("p", { class: "muted" }, `上次成功 ${hmOf(new Date(diff.lastOkAt).toISOString())}；以下为上次成功结果（已过期）`) : null,
      ),
      stale || h("div", { class: "empty" }, "没有可显示的历史结果。"),
    ];
  }
  if (!data) {
    return [bar, h("div", { class: "empty" }, "还没有检查。点击上方按钮读取差异。")];
  }
  return [bar, ...resultSection(h, ctx, data, diff)];
}
