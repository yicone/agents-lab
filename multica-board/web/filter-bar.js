/** Shared workspace filter visibility. Issues and Agents use route.ws; the other pages do not. */

export const DECIDE_SCOPE = "范围：ACM Lab";
export const SKILLS_SCOPE = "范围：全部来源";
export const MCP_SCOPE = "范围：全部来源";

/** "shared" shows the filter. "decide" / "skills" / "mcp" show a fixed scope. "none" shows neither. */
export function filterChrome(view) {
  if (view === "issues" || view === "agents" || view === "runs") return "shared";
  if (view === "decide") return "decide";
  if (view === "skills") return "skills";
  if (view === "mcp") return "mcp";
  return "none";
}

export function scopeNote(view) {
  if (view === "decide") return DECIDE_SCOPE;
  if (view === "skills") return SKILLS_SCOPE;
  if (view === "mcp") return MCP_SCOPE;
  return "";
}

export function currentScopeLabel(workspaces, slug) {
  if (!slug) return "全部 workspace";
  const found = (workspaces ?? []).find((ws) => ws.slug === slug);
  return found?.name || slug;
}

/** Current selection, an explicit clear control, then one chip per workspace. */
export function workspaceFilterBar(h, { workspaces, activeSlug, chip, onClear }) {
  const label = currentScopeLabel(workspaces, activeSlug);
  return [
    h("span", { class: "scope-current" }, `当前：${label}`),
    h(
      "button",
      {
        type: "button",
        class: "ws-clear",
        disabled: !activeSlug,
        "aria-label": "清除 workspace 筛选",
        title: activeSlug ? "清除当前 workspace 筛选，显示全部" : "当前已是全部 workspace",
        onclick: onClear,
      },
      "清除",
    ),
    ...(workspaces ?? []).map((ws) => chip(ws)),
  ];
}
