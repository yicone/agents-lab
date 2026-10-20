/** Runtime identity only. Availability remains a separate status dot. */
const MARKS = Object.freeze({
  claude: "Cl", codex: "Cx", cursor: "Cu", hermes: "H", antigravity: "A",
  gemini: "G", grok: "Gk", windsurf: "W", opencode: "O", copilot: "Co", devin: "D",
});

export function runtimeMark(provider) {
  const label = typeof provider === "string" ? provider.trim() : "";
  if (!label || ["未绑定", "未知", "未知 runtime"].includes(label)) return null;
  return { glyph: MARKS[label.toLowerCase()] || "R", label };
}

export function runtimeIcon(h, provider) {
  const mark = runtimeMark(provider);
  return mark ? h("span", { class: "runtime-mark", title: mark.label, "aria-label": `Runtime provider: ${mark.label}` }, mark.glyph) : null;
}

// Native option elements only support text reliably across browsers.
export function runtimeOption(provider, name) {
  const mark = runtimeMark(provider);
  return `${mark ? `[${mark.glyph}] ` : ""}${name} · ${provider || "未知"}`;
}
