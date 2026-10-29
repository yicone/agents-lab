/** Read-only field groups on the Agent 配置 page. */

export const RUNTIME_COLUMNS = ["Agent", "Runtime", "模型", "推理", "Tier", "额度"];
export const DUTY_COLUMNS = ["Agent", "Instructions 来源", "Skills 绑定", "MCP 绑定", "同一 Agent"];
export const BOUNDARY_COLUMNS = ["Agent", "边界"];

export function configFieldGroups() {
  return {
    runtime: [...RUNTIME_COLUMNS],
    duty: [...DUTY_COLUMNS],
    boundary: [...BOUNDARY_COLUMNS],
  };
}

const SECRET = /account_key|source_path|custom_env|api[_-]?key|\btoken\b|secret|sk-|bearer |ghp_|github_pat_/i;

/** Drop secret-shaped values. Null means the field was empty, not hidden. */
export function safeConfigText(value, hidden = "已隐藏") {
  if (typeof value !== "string" && typeof value !== "number") return null;
  const text = String(value).trim();
  if (!text) return null;
  if (SECRET.test(text) || text.includes("@")) return hidden;
  return text.length > 160 ? `${text.slice(0, 160)}…` : text;
}

/** Per-agent quota is not on /api/agents. Never read account_key or source_path. */
export function quotaText(agent) {
  if (!agent || typeof agent !== "object" || !Object.hasOwn(agent, "quota")) return "未采集";
  return safeConfigText(agent.quota, "已隐藏") || "未采集";
}

export function boundaryText(instructions) {
  if (typeof instructions !== "string" || !instructions.trim()) return "未单列";
  const match = instructions.match(/(?:^|\n)\s*(?:#{1,3}\s*)?(?:硬性边界|硬边界|边界)\s*[:：]\s*([^\n]+)/);
  if (!match) return "未单列";
  return safeConfigText(match[1], "已隐藏") || "未单列";
}
