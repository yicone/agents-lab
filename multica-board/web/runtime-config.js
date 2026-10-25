/** Runtime configuration is the only Atlas write surface. No arbitrary CLI input. */
export const WRITE_FIELDS = [
  ["runtime_id", "Runtime"], ["model", "模型"], ["thinking_level", "推理级别"],
  ["service_tier", "Tier"], ["custom_model", "custom_args · --model"],
];
const MODEL_AVAILABILITY_WARNING = "未校验当前账号的模型可用性；保存成功不代表能运行，下一次任务可能失败，请先核对该 runtime 和账号支持的模型。Runtime 在线仅表示运行时在线，不代表模型可用。";
export const actionValue = value => value === null ? "默认（null）" : value === "" ? "空字符串（显式清除）" : String(value);
export const actionMessages = {
  permission_denied: "没有写入权限，请核对授权码和目标范围。",
  method_not_allowed: "写入口未启用，请由本机管理员配置写入策略。",
  not_found: "后端尚未接入 runtime 写入接口。",
  identity_changed: "CLI 登录身份或服务已变化，请重新配置授权。",
  approval_invalid: "确认单已消费或失效，请重新预览。",
  approval_expired: "预览已过期，请重新预览。",
  cas_conflict: "配置在预览后发生变化，已停止。请重新预览。",
  active_or_unknown: "目标有活动任务，或无法证明空闲，已停止。",
  activity_unknown: "任务清单不完整，无法证明空闲，已停止。",
  reconciliation_required: "上次结果未知，请先对账。",
  readback_mismatch: "写入后的读回与目标不符，请先对账。",
  runtime_unavailable: "目标 runtime 不可用，请刷新后重试。",
  custom_model_unsupported: "此 runtime 不支持 custom_args 中的 --model，请使用模型字段。",
  custom_args_protected: "现有 custom_args 含其他参数，不能安全改写。请只修改独立模型字段。",
  no_changes: "没有配置变化。",
  availability_confirmation_required: "模型不可用，需要重新预览并额外确认。",
  availability_changed: "模型可用性或账号上下文已变化，请重新预览。",
  model_conflict: "模型字段与 custom_args 的 --model 冲突，请统一或清除其中一个。",
  snapshot_incomplete: "实时配置缺少必需字段，无法安全预览。",
  audit_scope_invalid: "审计 issue 不在目标 workspace。",
  invalid_field: "字段值无效，请输入模型标识或使用恢复默认。",
  no_unknown_action: "该目标没有待对账操作。",
  reconciliation_conflict: "当前配置既非原值也非目标值，需要人工核对。目标继续锁定。",
};
export function collectChanges(operations) {
  return Object.fromEntries(WRITE_FIELDS.flatMap(([key]) => {
    const op = operations[key];
    return !op || op.mode === "omit" ? [] : [[key, op.mode === "clear" ? null : op.value]];
  }));
}
export function canConfirm(plan, checked, busy, now = Date.now(), availabilityChecked = false) {
  return Boolean(plan && checked && !busy && plan.expires_at * 1000 > now
    && (!plan.availability?.requires_extra_confirmation || availabilityChecked));
}

export function availabilityText(value) {
  if (value?.status === "available") return "模型可用性：可用（账号目录）。目录允许不保证剩余额度或下一次运行成功。";
  if (value?.status === "unavailable") return "模型可用性：不可用（账号证据）。继续写入后，下一次任务可能失败。";
  return "模型可用性：未知。" + MODEL_AVAILABILITY_WARNING;
}

export function createRuntimeConfig(h, data, request = fetch, disabledReason = "") {
  const root = h("section", { class: "runtime-config", "aria-label": "模型与 Runtime 配置" });
  let token = "", catalog = null, selected = null, plan = null, result = null;
  let message = "", busy = false, checked = false, generation = 0, expiry = null;
  let operations = {}, lastFocus = null;
  let availabilityChecked = false;
  let connection = "checking", tokenRequired = null;
  const call = async (op, body) => {
    const response = await request("/api/runtime-actions/" + op, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Atlas-Write-Token": token },
      body: JSON.stringify(body), cache: "no-store", credentials: "omit",
    });
    const payload = await response.json();
    if (!response.ok) throw Object.assign(new Error(payload.error?.code || "action_unavailable"), { code: payload.error?.code, status: response.status });
    return payload;
  };
  const run = async (job, { executing = false } = {}) => {
    if (busy) return;
    const gen = generation;
    busy = true; message = ""; draw();
    try {
      const response = await job();
      if (gen !== generation) return;
      if (response.state === "awaiting_confirmation") {
        plan = response; checked = false; availabilityChecked = false;
        clearTimeout(expiry);
        expiry = setTimeout(() => { if (gen === generation) { message = "预览已过期，请重新预览。"; draw(); } }, Math.max(0, plan.expires_at * 1000 - Date.now()));
      } else if (response.targets) {
        catalog = response;
      } else {
        result = response; plan = null;
        if (response.state === "unknown") message = actionMessages[response.code] || "执行结果未知，请先对账，不能直接重试。";
      }
    } catch (error) {
      if (gen !== generation) return;
      message = actionMessages[error.code] || "接口不可用，请核对服务；已提交的操作请先对账。";
      if (executing) { plan = null; result = error.status && error.status < 500 ? null : { state: "unknown", actual_effect: "unknown" }; }
    } finally {
      if (gen === generation) { busy = false; draw(); }
    }
  };
  const cancel = () => {
    if (busy) return;
    clearTimeout(expiry); plan = null; checked = false; availabilityChecked = false; draw();
    lastFocus?.focus();
  };
  const button = (text, onclick, disabled = busy, attrs = {}) => h("button", { type: "button", onclick, disabled, ...attrs }, text);
  const cachedAgent = target => data().agents.find(a => a.id === target.agent_id && a.workspace_id === target.workspace_id);
  const displayField = (key, value) => key === "runtime_id" && value
    ? data().runtimes.find(r => r.id === value && r.workspace_id === selected?.workspace_id)?.name || "未采集 Runtime"
    : actionValue(value);
  const wsName = target => data().workspaces.find(w => w.id === target.workspace_id)?.name || "授权 Workspace";
  function editor() {
    const a = cachedAgent(selected);
    const rows = WRITE_FIELDS.map(([key, label]) => {
      const op = operations[key] || { mode: "omit", value: "" };
      const select = h("select", { "aria-label": label + " 操作", disabled: busy, onchange: e => {
        operations[key] = { ...op, mode: e.target.value }; plan = null; draw();
      } }, ...[["omit", "保持"], ["set", "设为"], ...(key === "runtime_id" ? [] : [["clear", "恢复默认"]])].map(([v, t]) => h("option", { value: v, selected: op.mode === v }, t)));
      let input = null;
      if (op.mode === "set") {
        input = key === "runtime_id"
          ? h("select", { "aria-label": label + " 目标", disabled: busy, onchange: e => { operations[key].value = e.target.value; } },
              h("option", { value: "" }, "请选择 runtime"),
              ...data().runtimes.filter(r => r.workspace_id === selected.workspace_id).map(r =>
                h("option", { value: r.id, selected: op.value === r.id }, r.name + (r.status === "online" ? "" : " · 离线"))))
          : h("input", { "aria-label": label + " 目标", value: op.value, maxlength: 160, disabled: busy, autocomplete: "off", oninput: e => { operations[key].value = e.target.value; } });
      }
      return h("tr", null, h("th", { scope: "row" }, label),
        h("td", { class: "mono muted" }, key === "custom_model" ? "实时预览时读取" : a ? displayField(key, a[key]) : "未采集"),
        h("td", null, select, input));
    });
    return h("section", { class: "plan-card" }, h("h3", null, a?.name || "已授权 Agent"),
      h("p", { class: "muted" }, wsName(selected), " · 当前列来自缓存，确认前会重新读取。"),
      h("div", { class: "table-wrap" }, h("table", { class: "matrix" },
        h("thead", null, h("tr", null, ...["字段", "当前（缓存）", "目标操作"].map(t => h("th", null, t)))),
        h("tbody", null, ...rows))),
      h("p", { class: "runtime-warning" }, MODEL_AVAILABILITY_WARNING),
      h("div", { class: "runtime-actions" },
        button("预览差异", e => { lastFocus = e.currentTarget; void run(() => call("preview", { ...selected, changes: collectChanges(operations) })); }),
        button("对账上次操作", () => void run(() => call("reconcile", selected)))));
  }
  function confirmation() {
    const target = catalog.targets.find(t => t.agent_id === plan.target[1] && t.workspace_id === plan.target[0]);
    const diff = Object.keys(plan.changes).map(key => h("tr", null,
      h("th", { scope: "row" }, WRITE_FIELDS.find(([f]) => f === key)[1]),
      h("td", { class: "mono" }, displayField(key, plan.before[key])),
      h("td", { class: "mono" }, displayField(key, plan.after[key]))));
    const confirm = button(plan.rollback_of ? "确认回滚" : "确认写入", () => {
      const approved = plan; checked = false;
      void run(() => call("execute", { id: approved.id, confirmation: approved.confirmation,
        ...(approved.availability?.requires_extra_confirmation && availabilityChecked
          ? { availability_confirmation: approved.id } : {}) }), { executing: true });
    }, !canConfirm(plan, checked, busy, Date.now(), availabilityChecked));
    return h("div", { class: "runtime-overlay" }, h("section", { class: "runtime-confirm plan-card", role: "dialog", "aria-modal": "true", "aria-labelledby": "runtime-confirm-title", onkeydown: e => {
      e.stopPropagation();
      if (e.key === "Escape") { e.stopPropagation(); cancel(); }
      if (e.key === "Tab") {
        const nodes = [...e.currentTarget.querySelectorAll("button:not(:disabled),input:not(:disabled)")];
        const first = nodes[0], last = nodes.at(-1);
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }
    } },
      h("h2", { id: "runtime-confirm-title" }, plan.rollback_of ? "回滚预览 · 需单独确认" : "确认配置变更"),
      h("p", null, wsName(target), " / ", cachedAgent(target)?.name || "已授权 Agent"),
      h("div", { class: "table-wrap" }, h("table", { class: "matrix" },
        h("thead", null, h("tr", null, ...["字段", "修改前", "修改后"].map(t => h("th", null, t)))), h("tbody", null, ...diff))),
      h("p", { class: "muted" }, "有效至 ", new Date(plan.expires_at * 1000).toLocaleTimeString(), "。提交前检查发现配置变化或活动任务时会阻止。"),
      h("p", { class: "runtime-warning", role: "status" }, availabilityText(plan.availability)),
      plan.availability?.observed_at != null ? h("p", { class: "muted" },
        "证据时间：", new Date(plan.availability.observed_at * 1000).toLocaleString()) : null,
      plan.availability?.requires_extra_confirmation ? h("label", { class: "check" },
        h("input", { type: "checkbox", checked: availabilityChecked, disabled: busy,
          "aria-label": "仍要写入不可用模型", onchange: e => {
            availabilityChecked = e.target.checked;
            confirm.disabled = !canConfirm(plan, checked, busy, Date.now(), availabilityChecked);
          } }), "我已知该账号模型不可用，仍要写入本次配置") : null,
      h("p", { class: "runtime-warning" }, "仅影响下一次运行。会话与工作目录不会迁移。提交前会复读核对。Multica CLI 没有条件更新，复读和提交之间仍可能被并发写入。读回不一致时会锁定目标，须先对账。并非所有并发改动都能检出。"),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked, disabled: busy, onchange: e => {
        checked = e.target.checked; confirm.disabled = !canConfirm(plan, checked, busy, Date.now(), availabilityChecked);
      } }), "我已核对本次范围和逐项差异"),
      h("div", { class: "runtime-actions" }, button("取消", cancel), confirm),
      message ? h("p", { role: "alert", class: "runtime-warning" }, message) : null));
  }
  function draw() {
    const hadDialog = !!root.querySelector('[role="dialog"]');
    const children = [
      h("h2", null, "模型与 Runtime"),
      h("p", { class: "muted" }, "配置管理 / 单 Agent · 手动切换。批量切换尚未接入。"),
      h("p", { class: "runtime-warning" }, "写入前预览并确认，写入后读回；结果未知时先对账。回滚需要新的确认。"),
    ];
    if (disabledReason) {
      children.push(h("p", { class: "muted" }, disabledReason));
    } else if (connection === "readonly") {
      children.push(h("p", { class: "muted", role: "status" }, "本实例只读。"));
    } else if (connection === "checking") {
      children.push(h("p", { role: "status" }, "正在连接…"));
    } else if (connection === "error" || (!catalog && tokenRequired === false)) {
      children.push(button("连接", () => void connect()));
    } else if (!catalog && tokenRequired === true) {
      const password = h("input", { type: "password", "aria-label": "写入授权码", autocomplete: "off", oninput: e => { token = e.target.value; } });
      children.push(h("section", { class: "plan-card" },
        h("h3", null, "连接写入权限"),
        h("p", { class: "muted" }, "需要本机管理员启用写入策略。授权码只用于当前页面，离开后清除。"),
        h("form", { onsubmit: e => { e.preventDefault(); void run(() => call("catalog", {})); } },
          h("label", null, "写入授权码 ", password), h("button", { type: "submit", disabled: busy }, busy ? "正在连接…" : "连接"))));
    } else {
      children.push(h("div", { class: "table-wrap" }, h("table", { class: "matrix" },
        h("thead", null, h("tr", null, ...["Agent", "Workspace", "当前模型（缓存）", "操作"].map(t => h("th", null, t)))),
        h("tbody", null, ...catalog.targets.map(target => h("tr", null,
          h("th", { scope: "row" }, cachedAgent(target)?.name || "未采集 Agent"),
          h("td", null, wsName(target)),
          h("td", { class: "mono" }, cachedAgent(target) ? actionValue(cachedAgent(target).model) : "未采集"),
          h("td", null, button("调整配置", () => { selected = { workspace_id: target.workspace_id, agent_id: target.agent_id }; operations = {}; result = null; message = ""; draw(); }))))))));
      if (catalog.recent_actions?.length) children.push(h("details", null,
        h("summary", null, "最近操作与审计 ID"),
        ...catalog.recent_actions.map(item => h("p", { class: "runtime-actions" },
          h("span", { class: "mono" }, item.id),
          button("查看结果", () => void run(() => call("status", { id: item.id })))))));
      if (!catalog.targets.length) children.push(h("p", null, "策略中没有授权目标。"));
      if (selected) children.push(editor());
    }
    if (message && !plan) children.push(h("p", { role: "alert", class: "runtime-warning" }, message));
    if (busy) children.push(h("p", { role: "status" }, "正在检查与处理，请勿重复提交…"));
    if (result) children.push(h("section", { class: "plan-card", "aria-live": "polite" },
      h("h3", null, ["applied", "reconciled_applied"].includes(result.state) ? "配置已写入并读回" : result.state === "reconciled_unapplied" ? "对账完成 · 当前为原配置" : "结果未知 · 需先对账"),
      result.after ? h("div", { class: "table-wrap" }, h("table", { class: "matrix" },
        h("thead", null, h("tr", null, ...["字段", "修改前", "目标配置"].map(t => h("th", null, t)))),
        h("tbody", null, ...Object.keys(result.changes).map(key => h("tr", null,
          h("th", { scope: "row" }, WRITE_FIELDS.find(([f]) => f === key)[1]),
          h("td", { class: "mono" }, displayField(key, result.before[key])),
          h("td", { class: "mono" }, displayField(key, result.after[key]))))))) : null,
      h("p", null, "实际生效：未知。尚无下一次运行使用模型的证据。"),
      h("p", { class: "muted" }, "审计 ID：", result.id || "回执缺失，按目标对账"),
      result.audit === "unknown" ? h("p", { class: "runtime-warning" }, "本地审计已保留，Multica 审计评论回执未知，请人工核对，勿盲目重发。") : null,
      ["applied", "reconciled_applied"].includes(result.state) ? button("预览回滚", e => { lastFocus = e.currentTarget; void run(() => call("rollback", { id: result.id })); }) : null));
    if (plan) children.push(confirmation());
    root.replaceChildren(...children);
    if (plan && !hadDialog) root.querySelector('[role="dialog"] button')?.focus();
  }
  async function connect() {
    if (busy) return;
    const gen = generation;
    connection = "checking"; busy = true; message = ""; draw();
    try {
      const capabilityResponse = await request("/api/runtime-actions", { cache: "no-store", credentials: "omit" });
      if (!capabilityResponse.ok) throw new Error("capability_unavailable");
      const capability = await capabilityResponse.json();
      if (gen !== generation) return;
      if (capability.enabled === false) { connection = "readonly"; return; }
      if (capability.enabled !== true) throw new Error("capability_unknown");
      const featureResponse = await request("/api/features", { cache: "no-store", credentials: "omit" });
      if (!featureResponse.ok) throw new Error("features_unavailable");
      const features = await featureResponse.json();
      if (gen !== generation) return;
      if (typeof features.write_token_required !== "boolean") throw new Error("features_unknown");
      tokenRequired = features.write_token_required;
      connection = "ready";
      if (!tokenRequired) catalog = await call("catalog", {});
      if (gen !== generation) { catalog = null; return; }
    } catch (error) {
      if (gen !== generation) return;
      connection = "error";
      message = actionMessages[error.code] || "接口不可用，请核对服务；已提交的操作请先对账。";
    } finally {
      if (gen === generation) { busy = false; draw(); }
    }
  }
  draw();
  if (!disabledReason) void connect();
  return { element: root, dispose() { generation++; clearTimeout(expiry); token = ""; plan = null; catalog = null; result = null; root.replaceChildren(); } };
}
