import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { renderMap } from "./view-atlas.js";
const h = (tag, attrs, ...children) => ({ tag, attrs: attrs || {}, children: children.flat(Infinity).filter(x => x != null && x !== false) });
const text = n => typeof n === "object" ? (n.children || []).map(text).join("") : String(n);
const find = (nodes, predicate) => nodes.flatMap(n => typeof n !== "object" ? [] : [...(predicate(n) ? [n] : []), ...find(n.children || [], predicate)]);
const issues = JSON.parse(readFileSync(new URL("./fixtures/atlas/issues.json", import.meta.url))).items;
function render(alert, items = issues) {
  return renderMap({ h, issues: items, acm: {id: issues[0].workspace_id, name: "ACM Lab"}, route: {}, nowMs: Date.now(), changes: [], historyIncomplete: true, toolSignal: () => "signal", skillsAlert: alert, usageAlert: alert, matchesQuery: () => true });
}
test("map distinguishes alerts from healthy pages and keeps navigation", () => {
  for (const alert of [true, false]) {
    const nodes = render(alert);
    for (const href of ["#/skills", "#/usage"]) {
      const [tool] = find(nodes, n => n.attrs.href === href);
      assert.ok(tool);
      assert.equal(tool.children[0].attrs.class, alert ? "dot bad" : "dot live");
    }
    const gaps = find(nodes, n => n.attrs.class === "tool gap");
    assert.ok(gaps.length);
    for (const gap of gaps) {
      assert.equal(gap.tag, "div");
      assert.equal(gap.attrs.href, undefined);
      assert.equal(gap.children[1].attrs.class, "tool-name");
      assert.ok(text(gap.children[1]).includes("未建"));
    }
  }
});
test("empty previews say 无 and long titles retain full accessible text", () => {
  const longIssues = issues.map(issue => ({ ...issue, title: "超长标题".repeat(100) }));
  const nodes = render(true, longIssues);
  assert.ok(find(nodes, n => n.attrs.class === "muted preview-empty").some(n => text(n) === "无"));
  const titles = find(nodes, n => n.attrs.class === "preview-title");
  assert.ok(titles.length);
  for (const title of titles) {
    assert.equal(title.attrs.title, text(title));
    assert.equal(text(title).length, 400);
  }
});
