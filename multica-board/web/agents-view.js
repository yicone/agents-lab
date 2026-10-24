import { runtimeIcon } from "./runtime-mark.js";
/** Agents subviews: instruction/skill columns, Grok Bots, and the read-only drawers. */

export const HERMES_SKILL = "hermes-async-delegation";
export const DUTY_LIMIT = 60;

const GROUP_RANK = new Map([
  ["🔝", 0],
  ["D4B", 1],
  ["D4L", 2],
  ["基建", 3],
]);

export function instructionStat(text) {
  const value = typeof text === "string" ? text.trim() : "";
  if (!value) return { empty: true, chars: 0, tokens: 0, text: "" };
  const chars = [...value].length;
  return { empty: false, chars, tokens: Math.max(1, Math.ceil(chars / 4)), text: value };
}

export function skillRecords(agent) {
  return Array.isArray(agent?.skills) ? agent.skills.filter((skill) => skill && typeof skill === "object") : [];
}

export function enabledSkills(agent) {
  return skillRecords(agent).filter((skill) => skill.enabled !== false && (skill.name || skill.id));
}

export function missingHermesSkill(agent, provider) {
  if (!agent || agent.archived) return false;
  if (String(provider || "").toLowerCase() !== "hermes") return false;
  return !enabledSkills(agent).some((skill) => skill.name === HERMES_SKILL || skill.id === HERMES_SKILL);
}

export function skillLabel(skill) {
  if (typeof skill === "string") return skill;
  return skill?.name || skill?.id || "";
}

export function agentQueryFields(agent, ws, rt) {
  const skills = skillRecords(agent).map((skill) => `${skill.name || ""} ${skill.id || ""}`).join(" ");
  return [agent?.name, agent?.description, agent?.instructions, agent?.model, agent?.thinking_level, agent?.service_tier, rt?.name, rt?.provider, ws?.name, ws?.slug, skills];
}

function heredoc(body) {
  const text = typeof body === "string" ? body : "";
  let marker = "EOF";
  while (text.includes(`\n${marker}\n`) || text.endsWith(`\n${marker}`) || text === marker) marker += "_END";
  return `$(cat <<'${marker}'\n${text}\n${marker}\n)`;
}

export function instructionCommand(agent, body) {
  const id = agent?.id || "";
  const text = body === undefined ? instructionStat(agent?.instructions).text : instructionStat(body).text;
  return `multica agent update ${id} --instructions ${heredoc(text)}`;
}

export function normalizeInstruction(text) {
  if (typeof text !== "string") return "";
  return text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").normalize("NFC").trim();
}

export function driftStatus(liveHash, gitHash, liveText, gitText) {
  const hasGit = gitHash != null || gitText != null;
  if (!hasGit) return "untracked";
  if (typeof liveHash === "string" && liveHash && typeof gitHash === "string" && gitHash) {
    return liveHash === gitHash ? "aligned" : "modified";
  }
  return normalizeInstruction(liveText) === normalizeInstruction(gitText) ? "aligned" : "modified";
}

export function lineDiff(gitText, liveText) {
  const left = instructionLines(gitText);
  const right = instructionLines(liveText);
  const rows = [];
  const dp = Array.from({ length: left.length + 1 }, () => new Uint32Array(right.length + 1));
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      dp[i][j] = left[i] === right[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  let i = 0;
  let j = 0;
  while (i < left.length && j < right.length) {
    if (left[i] === right[j]) {
      rows.push({ op: " ", text: left[i] });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      rows.push({ op: "-", text: left[i] });
      i += 1;
    } else {
      rows.push({ op: "+", text: right[j] });
      j += 1;
    }
  }
  while (i < left.length) rows.push({ op: "-", text: left[i++] });
  while (j < right.length) rows.push({ op: "+", text: right[j++] });
  return rows;
}

function instructionLines(text) {
  const norm = normalizeInstruction(text);
  return norm ? norm.split("\n") : [];
}

export function skillCommands(agent) {
  const id = agent?.id || "";
  const ids = enabledSkills(agent).map((skill) => skill.id).filter(Boolean);
  const value = ids.length ? ids.join(",") : "<skill-id>";
  return [
    `multica agent skills add ${id} --skill-ids ${value}`,
    `multica agent skills set ${id} --skill-ids ${value}`,
  ];
}

export function instructionCell(h, agent) {
  const stat = instructionStat(agent?.instructions);
  if (stat.empty) return h("td", null, h("span", { class: "instr-empty", title: "没有 instructions" }, "无"));
  return h("td", { class: "num", title: `约 ${stat.tokens.toLocaleString("zh-CN")} token` }, `${stat.chars.toLocaleString("zh-CN")} 字`);
}

export function skillsCell(h, agent, provider) {
  const count = enabledSkills(agent).length;
  const missing = missingHermesSkill(agent, provider);
  return h(
    "td",
    { class: missing ? "skill-missing" : "num", title: missing ? `Hermes 未启用 ${HERMES_SKILL}` : `${count} 个已启用` },
    String(count),
  );
}

export function kindSwitch(h, { route, planMode, onKind }) {
  const current = planMode ? "plan" : route?.kind === "bots" ? "bots" : "agents";
  const items = [
    ["agents", "Multica Agents"],
    ["bots", "Grok Bots"],
    ["plan", "规划切换…"],
  ];
  return h(
    "div",
    { class: "seg agents-kind", role: "group", "aria-label": "Agents 子视图" },
    items.map(([id, label]) =>
      h(
        "button",
        {
          type: "button",
          class: current === id ? "active" : "",
          "aria-pressed": current === id ? "true" : "false",
          onclick: () => onKind(id),
        },
        label,
      ),
    ),
  );
}

export function botGroupName(bot) {
  const name = typeof bot?.section_name === "string" ? bot.section_name.trim() : "";
  return name || "未分组";
}

export function compareGroups(a, b) {
  const rank = (name) => (GROUP_RANK.has(name) ? GROUP_RANK.get(name) : name === "未分组" ? 1000 : 100);
  const delta = rank(a) - rank(b);
  if (delta) return delta;
  return String(a).localeCompare(String(b), "zh");
}

export function dutyExcerpt(text) {
  const value = typeof text === "string" ? text.trim() : "";
  if (!value) return { empty: true, text: "" };
  const chars = [...value];
  if (chars.length <= DUTY_LIMIT) return { empty: false, text: value };
  return { empty: false, text: chars.slice(0, DUTY_LIMIT).join("") };
}

export function botAttention(bot) {
  const unread = Number.isFinite(bot?.unread_count) ? bot.unread_count : null;
  const awaiting = bot?.awaiting_user_response === true;
  const hasUnread = bot?.has_unread === true || (unread != null && unread > 0);
  const known = bot?.unread_count != null || bot?.has_unread != null || bot?.awaiting_user_response != null;
  return { known, unread, awaiting, hasUnread };
}

export function attentionSummary(items) {
  const summary = { known: false, unreadBots: 0, waiting: 0 };
  for (const bot of items || []) {
    const attention = botAttention(bot);
    if (!attention.known) continue;
    summary.known = true;
    if (attention.hasUnread) summary.unreadBots += 1;
    if (attention.awaiting) summary.waiting += 1;
  }
  return summary;
}

export function botWorkspaces(bot) {
  const rows = bot?.bridge?.workspaces;
  if (!Array.isArray(rows)) return [];
  return rows.map((row) => (typeof row?.slug === "string" ? row.slug : "")).filter(Boolean);
}

export function botQueryFields(bot) {
  const skills = skillRecords(bot).map((skill) => skill.name || skill.id || "").join(" ");
  return [bot?.name, bot?.title, bot?.description, bot?.bridge?.routine, ...botWorkspaces(bot), skills];
}

export function lastActiveAt(bot) {
  return bot?.last_activity_at || bot?.updated_at || null;
}

function cents(value) {
  if (!Number.isFinite(value)) return "—";
  return `$${(value / 100).toFixed(2)}`;
}

export function usageParts(usage, nowMs) {
  if (!usage || typeof usage !== "object") return { text: "周额度 —", tone: "" };
  const pct = usage.percent_used;
  const tone = pct >= 100 ? "bad" : pct >= 80 ? "warn" : "";
  const pctText = pct == null ? "—" : `${pct}%`;
  const bits = [`周额度 ${pctText}`];
  if (usage.plan) bits.push(usage.plan);
  if (Number.isFinite(usage.next_reset_ms)) {
    const delta = usage.next_reset_ms - nowMs;
    if (delta <= 0) bits.push("已到重置点");
    else if (delta < 48 * 3600000) bits.push(`${Math.max(1, Math.round(delta / 3600000))} 小时后重置`);
    else bits.push(`${Math.round(delta / 86400000)} 天后重置`);
  }
  const demand = usage.on_demand;
  if (demand && (demand.used_cents != null || demand.limit_cents != null)) bits.push(`按量 ${cents(demand.used_cents)} / ${cents(demand.limit_cents)}`);
  return { text: bits.join(" · "), tone };
}

function copyButton(h, copy, text, label) {
  return h(
    "button",
    {
      type: "button",
      onclick: (event) => {
        const button = event.currentTarget;
        Promise.resolve(copy(text)).then(
          () => {
            button.textContent = "已复制";
          },
          () => {
            button.textContent = "请手抄";
          },
        );
      },
    },
    label,
  );
}

function commandBlock(h, copy, command) {
  return h("p", { class: "command" }, h("code", null, command), copyButton(h, copy, command, "复制"));
}

function renderGitDrift(h, copy, { liveHash, liveText, git, noun }) {
  const gitText = git && typeof git.text === "string" ? git.text : null;
  const status = driftStatus(liveHash, git?.sha256, liveText, gitText);
  const label = status === "aligned" ? "一致" : status === "modified" ? "已修改" : "未纳管";
  const tone = status === "aligned" ? "ok" : status === "modified" ? "bad" : "warn";
  const nodes = [h("h3", { class: "drawer-sub" }, "Git 真源"), h("p", null, h("span", { class: `tag ${tone}` }, label))];
  if (status === "untracked") {
    nodes.push(h("p", { class: "muted" }, `Git 里还没有这份${noun}。`));
    return nodes;
  }
  if (status === "aligned") nodes.push(h("p", { class: "muted" }, "和 Git 真源的规范化哈希一致。"));
  else {
    const rows = lineDiff(gitText || "", liveText || "");
    nodes.push(h("p", { class: "muted" }, "逐行对比：减号是 Git 真源，加号是当前缓存。"));
    nodes.push(
      rows.length
        ? h(
            "div",
            { class: "diff-block" },
            rows.map((row) => h("div", { class: `diff-line diff-${row.op === "+" ? "add" : row.op === "-" ? "del" : "same"}` }, `${row.op} ${row.text}`)),
          )
        : h("p", { class: "muted" }, "哈希不一致，缓存正文和 Git 规范化后相同。"),
    );
  }
  const skills = Array.isArray(git?.skills) ? git.skills.filter((name) => typeof name === "string" && name.trim()) : [];
  if (noun === "职责说明") nodes.push(h("p", null, skills.length ? `云端 Skills：${skills.join("、")}` : "云端 Skills：未在 Git 声明"));
  nodes.push(h("p", { class: "drawer-actions" }, copyButton(h, copy, gitText || "", "复制 Git 真源")));
  return nodes;
}

export function renderAgentDrawer(h, ctx, drawer) {
  const agent = drawer.agent;
  const section = drawer.section || "brief";
  const stat = instructionStat(agent.instructions);
  const close = h("button", { type: "button", class: "drawer-close", "aria-label": "关闭", onclick: ctx.closeDrawer }, "×");
  const tabs = h(
    "div",
    { class: "seg drawer-tabs", role: "group", "aria-label": "Agent 详情" },
    [
      ["brief", "指令与说明"],
      ["skills", "Skills 绑定"],
      ["runs", "最近运行"],
    ].map(([id, label]) =>
      h(
        "button",
        {
          type: "button",
          class: section === id ? "active" : "",
          "aria-pressed": section === id ? "true" : "false",
          onclick: () => ctx.setSection(id),
        },
        label,
      ),
    ),
  );
  let body;
  if (section === "skills") {
    const enabled = enabledSkills(agent);
    const disabled = skillRecords(agent).filter((skill) => skill.enabled === false);
    const runtimeOff = Array.isArray(agent.disabled_runtime_skills) ? agent.disabled_runtime_skills : [];
    body = h(
      "section",
      null,
      h("h3", { class: "drawer-sub" }, "Skills 绑定"),
      enabled.length
        ? h(
            "ul",
            { class: "skill-list" },
            enabled.map((skill) => h("li", null, skill.name || skill.id, skill.description ? h("span", { class: "muted" }, ` · ${skill.description}`) : null)),
          )
        : h("p", { class: "muted" }, "没有已启用的 skill"),
      disabled.length ? h("p", { class: "muted" }, `未启用 ${disabled.map(skillLabel).filter(Boolean).join("、")}`) : null,
      runtimeOff.length ? h("p", { class: "muted" }, `关掉的 runtime skill ${runtimeOff.map(skillLabel).filter(Boolean).join("、")}`) : null,
      missingHermesSkill(agent, ctx.provider) ? h("p", { class: "skill-missing" }, `缺少 ${HERMES_SKILL}`) : null,
      h("h3", { class: "drawer-sub" }, "复制命令"),
      h("p", { class: "muted" }, "页面只读。复制后在自己的终端执行。"),
      ...skillCommands(agent).map((command) => commandBlock(h, ctx.copy, command)),
    );
  } else if (section === "runs") {
    const recent = agent.recent || {};
    body = h(
      "section",
      null,
      h("h3", { class: "drawer-sub" }, "最近运行"),
      h("p", null, `${ctx.recentWindowH}h 完成 ${recent.completed ?? 0} · 失败 ${recent.failed ?? 0} · 交付异常 ${recent.delivery_anomalies ?? 0}（完成数中的异常）`),
      h(
        "p",
        null,
        recent.last_failed_at
          ? `最近失败 ${ctx.errKind(recent.last_error_kind) || "失败"} · ${ctx.ago(recent.last_failed_at)}${recent.last_failed_issue_identifier ? ` ${recent.last_failed_issue_identifier}` : ""}`
          : "这个窗口里没有失败",
      ),
      h("p", { class: "muted" }, `当前运行 ${agent.tasks?.running ?? 0} · 排队 ${agent.tasks?.queued ?? 0}`),
      h("p", { class: "muted" }, "缓存只有这段汇总，没有按 agent 展开的逐条 run。"),
    );
  } else {
    const full = stat.text || (typeof agent.description === "string" ? agent.description : "");
    body = h(
      "section",
      null,
      h("h3", { class: "drawer-sub" }, "指令与说明"),
      agent.inventory_source
        ? h("p", { class: "muted" }, `采集来源：${agent.inventory_source} · 采集时间：${agent.inventory_collected_at || "未知"}（用户补采快照）`)
        : null,
      agent.description ? h("p", null, agent.description) : h("p", { class: "muted" }, "没有 description"),
      stat.empty
        ? h("p", null, h("span", { class: "instr-empty" }, "无"), h("span", { class: "muted" }, " instructions"))
        : h("pre", { class: "instr-body" }, stat.text),
      h("p", { class: "drawer-actions" }, copyButton(h, ctx.copy, full, "复制全文"), copyButton(h, ctx.copy, instructionCommand(agent), "复制更新命令")),
      ...renderGitDrift(h, ctx.copy, { liveHash: agent.instructions_sha256, liveText: stat.text, git: ctx.gitSource, noun: "instructions" }),
      ctx.gitSource && typeof ctx.gitSource.text === "string"
        ? commandBlock(h, ctx.copy, instructionCommand(agent, ctx.gitSource.text))
        : null,
      h("h3", { class: "drawer-sub" }, "CLI"),
      commandBlock(h, ctx.copy, instructionCommand(agent)),
    );
  }
  return [
    h(
      "header",
      { class: "drawer-head" },
      h("div", null, ctx.wsTag, h("span", { class: "mono" }, agent.name)),
      close,
    ),
    h("h2", { class: "drawer-title" }, agent.name),
    agent.url ? ctx.extLink(agent.url, { class: "drawer-link" }, "在 Multica 打开 ↗") : null,
    h("p", { class: "muted" }, runtimeIcon(h, ctx.provider), ctx.provider ? `runtime ${ctx.provider}` : "未绑定 runtime"),
    tabs,
    body,
  ].filter(Boolean);
}

function attentionCell(h, bot) {
  const attention = botAttention(bot);
  const parts = [];
  if (!attention.known) parts.push("—");
  else {
    if (attention.unread) parts.push(`未读 ${attention.unread}`);
    else if (attention.hasUnread) parts.push("未读");
    if (attention.awaiting) parts.push("等待回复");
    if (!attention.hasUnread && !attention.awaiting) parts.push("无");
  }
  if (bot.hidden) parts.push("隐藏");
  const title = attention.known ? "" : "花名册接口还没有未读或等待字段";
  return h("td", { title }, parts.join(" · "));
}

function dutyCell(h, bot) {
  const duty = dutyExcerpt(bot.description);
  if (duty.empty) return h("td", null, h("span", { class: "duty-empty" }, "未填职责说明"));
  return h("td", { class: "duty", title: typeof bot.description === "string" ? bot.description : "" }, duty.text);
}

export function renderBotPage(h, ctx) {
  const payload = ctx.payload || { ok: false, reason: "absent", items: [] };
  const items = Array.isArray(payload.items) ? payload.items : [];
  const route = ctx.route;
  const linkedSlug = route.ws || "";
  const summary = attentionSummary(items);
  const usage = usageParts(payload.weekly_usage, ctx.nowMs);
  const filtered = items.filter((bot) => {
    if (route.botLinked && linkedSlug && !botWorkspaces(bot).includes(linkedSlug)) return false;
    return ctx.matchesQuery(route.q, ...botQueryFields(bot));
  });
  const groups = new Map();
  for (const bot of filtered) {
    const key = botGroupName(bot);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(bot);
  }
  for (const rows of groups.values()) {
    rows.sort((a, b) => Number(b.pinned) - Number(a.pinned) || String(a.name || "").localeCompare(String(b.name || ""), "zh"));
  }
  const ordered = [...groups.keys()].sort(compareGroups);

  const bar = h(
    "div",
    { class: "bot-bar" },
    h("div", { class: "bot-stat" }, h("span", { class: "p-name" }, "账号"), h("span", null, payload.account_slot || "—")),
    h("div", { class: `bot-stat${usage.tone ? ` is-${usage.tone}` : ""}` }, h("span", null, usage.text)),
    h(
      "div",
      { class: "bot-stat", title: summary.known ? "" : "花名册接口还没有未读或等待字段" },
      summary.known ? `等待回复 ${summary.waiting} · 未读 ${summary.unreadBots}` : "等待回复 / 未读 —",
    ),
    h("div", { class: "bot-stat" }, h("span", { class: "p-name" }, "Bridge"), h("span", null, payload.delivery || "—")),
  );

  let table;
  if (payload.ok === false && !items.length) {
    const reason = payload.reason === "absent" ? "本机没有 Grok Bot 花名册（未登录或目录不存在）。" : `花名册不可用${payload.error ? `：${payload.error}` : ""}。`;
    table = h("div", { class: "empty" }, reason);
  } else if (!filtered.length) {
    table = h("div", { class: "empty" }, "没有符合条件的 bot");
  } else {
    table = h(
      "div",
      { class: "table-wrap sticky-head" },
      h(
        "table",
        { class: "matrix" },
        h("thead", null, h("tr", null, ["名称", "未读 / 等待", "Workspace / routine", "职责说明", "最后活跃"].map((label) => h("th", { scope: "col" }, label)))),
        ordered.map((key) =>
          h(
            "tbody",
            null,
            h("tr", { class: "group-row" }, h("th", { colspan: 5, scope: "rowgroup" }, key, h("span", { class: "muted" }, ` ${groups.get(key).length}`))),
            groups.get(key).map((bot) => {
              const workspaces = botWorkspaces(bot);
              const linked = linkedSlug && workspaces.includes(linkedSlug);
              const selected = ctx.selectedId === bot.id;
              return h(
                "tr",
                {
                  id: `bot-row-${bot.id}`,
                  class: `agent-row${linked ? " is-linked" : ""}${selected ? " is-selected" : ""}`,
                  tabindex: "0",
                  "aria-label": `打开 ${bot.name || bot.id}`,
                  onclick: (event) => {
                    if (event.target.closest("a, button, input")) return;
                    ctx.openBot(bot, event.currentTarget);
                  },
                  onkeydown: (event) => {
                    if (event.key !== "Enter" && event.key !== " ") return;
                    event.preventDefault();
                    ctx.openBot(bot, event.currentTarget);
                  },
                },
                h(
                  "td",
                  { class: "agent-name" },
                  bot.name || bot.id,
                  bot.is_group ? h("span", { class: "tag room" }, "Room") : null,
                  bot.pinned ? h("span", { class: "tag muted" }, "置顶") : null,
                ),
                attentionCell(h, bot),
                h(
                  "td",
                  null,
                  workspaces.length ? workspaces.join("、") : h("span", { class: "muted" }, "未关联"),
                  bot.bridge?.routine ? h("div", { class: "muted" }, bot.bridge.routine) : h("div", { class: "muted" }, "无 routine"),
                ),
                dutyCell(h, bot),
                h("td", { title: ctx.absTime(lastActiveAt(bot)) }, ctx.ago(lastActiveAt(bot))),
              );
            }),
          ),
        ),
      ),
    );
  }

  const scope = linkedSlug
    ? h(
        "label",
        { class: "check" },
        h("input", { type: "checkbox", checked: !!route.botLinked, onchange: (event) => ctx.setRoute({ botLinked: event.target.checked }) }),
        `只看关联 ${linkedSlug}`,
      )
    : h("span", { class: "muted" }, "未选 workspace 时全部显示，选中后高亮关联 bot");

  const toolbar = h(
    "div",
    { class: "toolbar" },
    ctx.kindSwitch,
    scope,
    h("span", { class: "muted" }, `${filtered.length} / ${items.length} bot`),
  );
  return [bar, toolbar, table];
}

export function renderBotDrawer(h, ctx, drawer) {
  const bot = drawer.bot;
  const names = ctx.memberName || ((id) => id);
  const members = Array.isArray(bot.member_ids) ? bot.member_ids : [];
  const bridge = bot.bridge || {};
  const outbox = bridge.outbox || {};
  const duty = typeof bot.description === "string" ? bot.description : "";
  const close = h("button", { type: "button", class: "drawer-close", "aria-label": "关闭", onclick: ctx.closeDrawer }, "×");
  const workspaces = (bridge.workspaces || []).map((row) => row?.slug).filter(Boolean);
  return [
    h("header", { class: "drawer-head" }, h("div", null, bot.is_group ? h("span", { class: "tag room" }, "Room") : null, h("span", { class: "mono" }, bot.name || bot.id)), close),
    h("h2", { class: "drawer-title" }, bot.name || bot.id),
    h("h3", { class: "drawer-sub" }, "职责说明"),
    duty ? h("pre", { class: "instr-body" }, duty) : h("p", null, h("span", { class: "duty-empty" }, "未填职责说明")),
    h("p", { class: "drawer-actions" }, copyButton(h, ctx.copy, duty, "复制职责说明")),
    ...renderGitDrift(h, ctx.copy, { liveHash: bot.description_sha256, liveText: duty, git: ctx.gitSource, noun: "职责说明" }),
    h("h3", { class: "drawer-sub" }, "Room 成员"),
    bot.is_group ? (members.length ? h("ul", { class: "skill-list" }, members.map((id) => h("li", null, names(id)))) : h("p", { class: "muted" }, "没有成员")) : h("p", { class: "muted" }, "不是 Room"),
    h("h3", { class: "drawer-sub" }, "Bridge 路由与 outbox"),
    h("p", null, `投递 ${bridge.delivery || ctx.delivery || "—"}`),
    h("p", null, `Workspace ${workspaces.length ? workspaces.join("、") : "未关联"}`),
    h("p", null, `routine ${bridge.routine || "无"}`),
    h("p", null, `outbox pending ${outbox.pending ?? 0} · dead_letter ${outbox.dead_letter ?? 0} · shadow_record ${outbox.shadow_record ?? 0}`),
    h("p", null, `断路器 ${bridge.circuit?.state || "—"}`),
    h("p", { class: "muted" }, "只展示本机缓存。页面不调用私有写接口。"),
  ];
}
