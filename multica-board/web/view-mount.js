/** Mount a view. Null and false are empty slots, not text. */
export function placeChildren(parent, parts) {
  const nodes = [];
  for (const part of parts) {
    if (part == null || part === false) continue;
    nodes.push(part);
  }
  parent.replaceChildren(...nodes);
}

/** Offsets of nodes a full replace would destroy. Index matches querySelectorAll order. */
export function captureScroll(root, selector) {
  if (!root || typeof root.querySelectorAll !== "function" || !selector) return [];
  return [...root.querySelectorAll(selector)].map((el, index) => ({
    index,
    top: Number(el.scrollTop) || 0,
    left: Number(el.scrollLeft) || 0,
  }));
}

export function restoreScroll(root, saved, selector) {
  if (!root || typeof root.querySelectorAll !== "function" || !selector || !saved?.length) return;
  const nodes = [...root.querySelectorAll(selector)];
  for (const item of saved) {
    const el = nodes[item.index];
    if (!el) continue;
    if (item.top) el.scrollTop = item.top;
    if (item.left) el.scrollLeft = item.left;
  }
}
