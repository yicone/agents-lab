/** Agents ↔ Skills ↔ MCP focus locations. The hash is the source of truth. */

function decodePart(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export function decodeRef(token) {
  const [kind, ...rest] = String(token || "").split("/");
  return { kind: kind || "", parts: rest.map(decodePart) };
}

export function agentOrigin(agent) {
  if (!agent?.id) return "";
  return `agent/${encodeURIComponent(agent.workspace_id || "")}/${encodeURIComponent(agent.id)}`;
}

export function skillOrigin(name) {
  if (!name) return "";
  return `skill/${encodeURIComponent(name)}`;
}

export function parseOrigin(token) {
  const decoded = decodeRef(token);
  if (decoded.kind === "agent" && decoded.parts.length >= 2 && decoded.parts[1]) {
    return { kind: "agent", workspaceId: decoded.parts[0], agentId: decoded.parts[1] };
  }
  if (decoded.kind === "skill" && decoded.parts[0]) return { kind: "skill", name: decoded.parts[0] };
  if (decoded.kind === "mcp" && decoded.parts.length) {
    return { kind: "mcp", ref: decoded.parts.map((part) => encodeURIComponent(part)).join("/") };
  }
  return { kind: "" };
}

export function mcpEntryRef(entry) {
  if (!entry?.workspace_id || !entry?.server_id) return "";
  return `ws/${encodeURIComponent(entry.workspace_id)}/srv/${encodeURIComponent(entry.server_id)}`;
}

/** Every workspace/server id on the item. Order does not decide identity. */
export function mcpEntryRefs(item) {
  const refs = [];
  const seen = new Set();
  for (const entry of item?.workspace_entries || []) {
    const ref = mcpEntryRef(entry);
    if (!ref || seen.has(ref)) continue;
    seen.add(ref);
    refs.push({ ref, entry });
  }
  if (!refs.length && item?.display_name) refs.push({ ref: `name/${encodeURIComponent(item.display_name)}`, entry: null });
  return refs;
}

export function mcpRef(item) {
  return mcpEntryRefs(item)[0]?.ref || "";
}

export function mcpMatches(item, focus) {
  const ref = String(focus || "");
  if (!ref) return false;
  return mcpEntryRefs(item).some((itemRef) => itemRef.ref === ref);
}

export function focusLabel(focus) {
  const decoded = decodeRef(focus);
  if (decoded.kind === "name" && decoded.parts[0]) return decoded.parts.join("/");
  if (decoded.kind === "ws") return decoded.parts[2] ? decoded.parts.slice(2).join("/") : String(focus || "");
  return String(focus || "");
}

export function resolveSkill(items, focus) {
  const name = String(focus || "");
  if (!name) return null;
  return (items || []).find((item) => item?.name === name || item?.key === name) || null;
}

export function resolveMcp(items, focus) {
  const ref = String(focus || "");
  if (!ref) return null;
  return (items || []).find((item) => mcpMatches(item, ref)) || null;
}

function enabledSkill(skill, name) {
  if (!skill || skill.enabled === false) return false;
  return skill.name === name || skill.id === name;
}

export function mcpForSkill(skillName, agents, mcpItems) {
  const name = String(skillName || "");
  if (!name) return [];
  const holders = new Set();
  for (const agent of agents || []) {
    if (!agent || agent.archived) continue;
    if ((agent.skills || []).some((skill) => enabledSkill(skill, name))) holders.add(agent.id);
  }
  const links = [];
  const seen = new Set();
  for (const item of mcpItems || []) {
    for (const { ref, entry } of mcpEntryRefs(item)) {
      if (!entry) continue;
      const bound = (entry.agents || []).some((agent) => holders.has(agent.id) && agent.enabled !== false);
      if (!bound || seen.has(ref)) continue;
      seen.add(ref);
      links.push({ ref, label: item.display_name || ref, item });
    }
  }
  return links;
}

export function skillsForMcp(item, agents) {
  const ids = new Set();
  for (const entry of item?.workspace_entries || []) {
    if (entry.kind !== "library") continue;
    for (const agent of entry.agents || []) {
      if (agent?.id && agent.enabled !== false) ids.add(agent.id);
    }
  }
  const links = [];
  const seen = new Set();
  for (const agent of agents || []) {
    if (!agent || agent.archived || !ids.has(agent.id)) continue;
    for (const skill of agent.skills || []) {
      if (skill?.enabled === false) continue;
      const name = skill?.name || skill?.id;
      if (!name || seen.has(name)) continue;
      seen.add(name);
      links.push({ name, agent });
    }
  }
  return links;
}

export function describeOrigin(origin, agents) {
  const parsed = parseOrigin(origin);
  if (parsed.kind === "agent") {
    const agent = (agents || []).find((item) => item.id === parsed.agentId && (!parsed.workspaceId || item.workspace_id === parsed.workspaceId));
    return agent?.name ? `来自 Agent ${agent.name}` : "来自 Agent";
  }
  if (parsed.kind === "skill") return `来自 Skill ${parsed.name}`;
  if (parsed.kind === "mcp") return "来自 MCP";
  return "";
}

const PERMISSION_CODES = new Set(["forbidden", "permission_denied", "unauthorized", "not_a_member", "auth"]);

export function focusFailureError(err) {
  const status = Number(err?.status);
  const code = typeof err?.code === "string" ? err.code : "";
  return {
    status: Number.isFinite(status) && status > 0 ? status : null,
    code,
    message: String(err?.message || err || ""),
  };
}

export function isPermissionFailure(error) {
  if (!error || typeof error !== "object") return false;
  const status = Number(error.status);
  if (status === 401 || status === 403) return true;
  return PERMISSION_CODES.has(String(error.code || "").toLowerCase());
}

export function assessFocus({ focus, payload, error, item }) {
  if (!focus) return null;
  const name = focusLabel(focus);
  if (!payload && !error) return { state: "loading", title: "正在打开", detail: `正在读取「${name}」。`, name };
  if (isPermissionFailure(error)) {
    return { state: "forbidden", title: "没有权限", detail: `没有权限打开「${name}」。可以回到上一层。`, name };
  }
  if (error || payload?.ok === false) {
    return { state: "network", title: "网络失败", detail: `没有取到「${name}」。可以重试，不会丢掉来源。`, name };
  }
  if (!item) return { state: "missing", title: "找不到目标", detail: `没有「${name}」。不会改去打开别的对象。`, name };
  if (item.archived === true || item.lifecycle === "offline") {
    const label = item.name || item.display_name || name;
    return { state: "archived", title: "已下线", detail: `「${label}」已归档或下线。`, name: label };
  }
  return null;
}

/** Walk a direct focus URL back to Agents. A repeat means the return chain loops. */
export function unwindFocus(route) {
  const steps = [];
  let current = { ...(route || {}) };
  for (let i = 0; i < 6; i += 1) {
    const key = `${current.view || ""}|${current.focus || ""}`;
    if (steps.includes(key)) return { cycle: true, steps };
    steps.push(key);
    if (!current.focus) return { cycle: false, steps };
    current = { ...current, ...parentRoute(current) };
  }
  return { cycle: true, steps };
}

/** One layer back for a focus URL opened directly, without browser history. */
export function parentRoute(route) {
  const origin = parseOrigin(route?.origin);
  if (origin.kind === "skill") {
    return { view: "skills", section: "configure", evidence: false, focus: origin.name, origin: "", source: "", skillStatus: "", tool: "", custom: false, debt: false, q: "" };
  }
  if (origin.kind === "mcp") {
    return { view: "mcp", section: "configure", focus: origin.ref, origin: "", source: "", mcpStatus: "", tool: "", mcpAssigned: false, mcpTidy: false, q: "" };
  }
  if (origin.kind === "agent") return { view: "agents", section: "", focus: "", origin: "", sel: origin.agentId, q: "" };
  return { view: "agents", section: "", focus: "", origin: "", q: "" };
}

export function drawerSnap(drawer) {
  if (!drawer) return null;
  if (drawer.kind === "agent" && drawer.agent) {
    return { kind: "agent", id: drawer.agent.id, workspaceId: drawer.agent.workspace_id, section: drawer.section || "skills" };
  }
  if (drawer.kind === "skill" && drawer.skill) return { kind: "skill", name: drawer.skill.name || "" };
  if (drawer.kind === "mcp") return { kind: "mcp", ref: drawer.ref || "", id: drawer.id || "" };
  return null;
}

export function rehydrateDrawer(snap, data) {
  if (!snap) return null;
  if (snap.kind === "agent") {
    const agent = (data?.agents || []).find((item) => item.id === snap.id && item.workspace_id === snap.workspaceId);
    if (!agent) return null;
    return { kind: "agent", agent, section: snap.section || "skills", opener: null };
  }
  if (snap.kind === "skill") {
    const skill = resolveSkill(data?.skills?.items, snap.name);
    if (!skill) return null;
    return { kind: "skill", skill, opener: null };
  }
  if (snap.kind === "mcp") return { kind: "mcp", ref: snap.ref || "", id: snap.id || "", opener: null };
  return null;
}

export function renderFocusState(h, model, actions) {
  const retry = model.state === "network";
  return h(
    "section",
    { class: "focus-state", "data-focus-state": model.state, role: "status", "aria-busy": model.state === "loading" ? "true" : null },
    h("h2", { tabindex: "-1", "data-focus-title": "true" }, model.title),
    h("p", null, model.detail),
    model.origin ? h("p", { class: "muted" }, model.origin) : null,
    h(
      "div",
      { class: "focus-actions" },
      h("button", { type: "button", "aria-label": "返回上一层", onclick: actions?.onBack }, "返回上一层"),
      retry ? h("button", { type: "button", "aria-label": "重试", onclick: actions?.onRetry }, "重试") : null,
    ),
  );
}
