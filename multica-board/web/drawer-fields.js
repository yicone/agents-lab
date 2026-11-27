/** Drawer section, label/value line, and one diff block shared by Skills and Agents. */

export function section(h, title, nodes) {
  return h("section", null, h("h3", { class: "drawer-sub" }, title), ...nodes.filter(Boolean));
}

export function line(h, label, value) {
  const body = value != null && typeof value === "object"
    ? value
    : h("span", { class: "mono" }, value || "—");
  return h("p", null, h("span", { class: "muted" }, `${label} `), body);
}

/** kind is add | del | same. File diffs and instruction diffs render the same nodes. */
export function diffBlock(h, lines) {
  return h(
    "div",
    { class: "diff-block" },
    lines.map((row) => h("div", { class: `diff-line diff-${row.kind}` }, row.text)),
  );
}
