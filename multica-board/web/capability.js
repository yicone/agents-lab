/** Capability page: skills, MCP and hooks are peers. Memory is not one of them. */

export const CAPABILITY_KINDS = [
  { id: "skills", label: "skills" },
  { id: "mcp", label: "MCP" },
  { id: "hooks", label: "hooks" },
];

export function capabilitySwitch(h, active, onSelect) {
  return h(
    "div",
    { class: "toolbar" },
    h("span", { class: "muted" }, "能力"),
    h(
      "div",
      { class: "seg agents-kind", role: "radiogroup", "aria-label": "能力类别" },
      CAPABILITY_KINDS.map((kind) => h(
        "button",
        {
          type: "button",
          role: "radio",
          class: kind.id === active ? "active" : "",
          "aria-checked": kind.id === active ? "true" : "false",
          onclick: () => {
            if (kind.id !== active) onSelect(kind.id);
          },
        },
        kind.label,
      )),
    ),
  );
}
