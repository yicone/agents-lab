/** Mount a view. Null and false are empty slots, not text. */
export function placeChildren(parent, parts) {
  const nodes = [];
  for (const part of parts) {
    if (part == null || part === false) continue;
    nodes.push(part);
  }
  parent.replaceChildren(...nodes);
}
