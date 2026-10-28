/** Text tri-state for a capability row. Missing data is 未知, never an alert. */

export const TRI_LEGEND = "每一行用文字写出三种状态，不只靠颜色。声明了：文件或 grok inspect 里有这条。未声明：读过且没有这条。可达：命令里的绝对路径文件存在。不可达：读过且文件不在，或 Grok 报告不会运行。兼容状态对不上、命令里没有绝对路径，都写未知。enabled 和 trusted_hash 不算可达，也不算观察到。已观察到：运行里看到过。未观察到：有记录说明这次没在跑。没有记录就写未知，不当成异常。hooks 的已观察到目前都是未知。";

const TEXT = {
  declared: { yes: "声明了", no: "未声明", unknown: "未知" },
  reachable: { yes: "可达", no: "不可达", unknown: "未知" },
  observed: { yes: "已观察到", no: "未观察到", unknown: "未知" },
};

export function triValue(value) {
  return value === "yes" || value === "no" || value === "unknown" ? value : "unknown";
}

export function triLines(state) {
  const declared = triValue(state?.declared);
  const reachable = triValue(state?.reachable);
  const observed = triValue(state?.observed);
  return [
    ["声明了", TEXT.declared[declared]],
    ["可达", TEXT.reachable[reachable]],
    ["观察到", TEXT.observed[observed]],
  ];
}

export function renderTriState(h, state) {
  const lines = triLines(state);
  const title = [
    ...lines.map(([name, value]) => `${name}：${value}`),
    state?.detail || "",
    "缺数据显示未知，不是异常。",
  ].filter(Boolean).join("\n");
  return h(
    "div",
    { class: "tri-state", title },
    lines.map(([name, value]) => h("div", { class: "tri-line" }, h("span", { class: "tri-key" }, name), " ", value)),
  );
}

export function skillTriState(item) {
  return {
    declared: skillDeclared(item),
    reachable: skillReachable(item),
    observed: skillObserved(item),
    detail: "Skills 的观察到只看 runtime 加载记录；没有记录就是未知，不从路径推断。",
  };
}

function skillDeclared(item) {
  if (!item) return "unknown";
  if (item.export || item.origin === "grok_bot" || item.source_class === "grok_bot" || item.source_class === "grok_bot_export") return "yes";
  if ((item.entries || []).length || (item.evidence || []).length || item.manifest_path) return "yes";
  return "unknown";
}

function skillReachable(item) {
  const marks = [];
  for (const entry of item?.entries || []) {
    if (entry?.present === true) marks.push("yes");
    else if (entry?.present === false) marks.push("no");
  }
  for (const record of item?.evidence || []) {
    if (record?.state === "present") marks.push("yes");
    else if (record?.state === "missing" || record?.state === "denied") marks.push("no");
  }
  if (!marks.length) return "unknown";
  if (marks.includes("yes")) return "yes";
  if (marks.every((mark) => mark === "no")) return "no";
  return "unknown";
}

function skillObserved(item) {
  const rows = Object.values(item?.runtime_evidence || {});
  if (rows.some((row) => row?.runtime_loaded === true)) return "yes";
  if (rows.some((row) => row?.runtime_loaded === false)) return "no";
  return "unknown";
}

export function mcpTriState(item) {
  const declared = (item?.entries || []).length || (item?.workspace_entries || []).length ? "yes" : "unknown";
  return {
    declared,
    reachable: "unknown",
    observed: "unknown",
    detail: "MCP 没有连接探测。配置文件在不在不算可达，会加载的预测也不算观察到。",
  };
}

export function hookTriState(item) {
  return {
    declared: triValue(item?.declared),
    reachable: triValue(item?.reachable),
    observed: "unknown",
    detail: item?.detail || "hooks 没有稳定运行日志，观察到为未知。",
  };
}
