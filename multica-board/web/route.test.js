import assert from "node:assert/strict";
import test from "node:test";
import { applyPatch, formatHash, parseHash, redirectHash, stepLayer, viewForKey } from "./route.js";

test("#/issues stays", () => {
  assert.equal(redirectHash("#/issues"), null);
  assert.equal(redirectHash("#/issues?ws=acm-lab&q=桥"), null);
});

test("#/agents stays", () => {
  assert.equal(redirectHash("#/agents"), null);
  assert.equal(redirectHash("#/agents?group=provider&failed=1"), null);
});

test("#/skills stays", () => {
  assert.equal(redirectHash("#/skills"), null);
  assert.equal(redirectHash("#/skills?q=guard"), null);
});

test("skills filters round-trip", () => {
  const route = parseHash("#/skills?source=runtime_plugin&status=version_changed&tool=codex&custom=1&debt=1&group=tool&q=vis");
  assert.equal(route.view, "skills");
  assert.equal(route.source, "runtime_plugin");
  assert.equal(route.skillStatus, "version_changed");
  assert.equal(route.tool, "codex");
  assert.equal(route.custom, true);
  assert.equal(route.debt, true);
  assert.equal(route.skillGroup, "tool");
  assert.equal(formatHash(route), "#/skills?q=vis&source=runtime_plugin&status=version_changed&tool=codex&custom=1&debt=1&group=tool");
});

test("#/l5/attention goes to issues", () => {
  assert.equal(redirectHash("#/l5/attention"), "#/issues");
  assert.equal(redirectHash("#/l5/attention?q=1"), "#/issues");
});

test("#/l2/agents goes to agents", () => {
  assert.equal(redirectHash("#/l2/agents"), "#/agents");
});

test("#/l3/skills goes to skills", () => {
  assert.equal(redirectHash("#/l3/skills"), "#/skills");
});

test("#/lN and #/lN/issues keep scope and land on the layer", () => {
  assert.equal(redirectHash("#/l3"), "#/map/l3");
  assert.equal(redirectHash("#/l4/issues"), "#/map/l4");
  assert.equal(redirectHash("#/l3/issues?scope=involved"), "#/map/l3?scope=involved");
  assert.equal(redirectHash("#/l1/issues?scope=primary"), "#/map/l1?scope=primary");
});

test("#/lN/other drops the unknown subpage", () => {
  assert.equal(redirectHash("#/l2/instructions"), "#/map/l2");
  assert.equal(redirectHash("#/l5/daily"), "#/map/l5");
});

test("#/gov goes to the cross-layer governance list", () => {
  assert.equal(redirectHash("#/gov"), "#/map/all?gov=1");
  assert.equal(redirectHash("#/gov?q=1"), "#/map/all?gov=1");
});

test("empty hash goes to the map", () => {
  assert.equal(redirectHash(""), "#/map");
  assert.equal(redirectHash("#"), "#/map");
  assert.equal(redirectHash("#/"), "#/map");
});

test("unknown hash goes to the map", () => {
  assert.equal(redirectHash("#/nope"), "#/map");
  assert.equal(redirectHash("#/map/l9"), "#/map");
});

test("current map and decide hashes are not redirected", () => {
  assert.equal(redirectHash("#/map"), null);
  assert.equal(redirectHash("#/map?gov=1"), null);
  assert.equal(redirectHash("#/map/l3?scope=involved&gov=1"), null);
  assert.equal(redirectHash("#/decide?type=blocked"), null);
});

test("parse keeps decide, issues, and agent params", () => {
  const decide = parseHash("#/decide?type=review&layer=l3&match=involved&gov=1&group=layer&q=桥");
  assert.equal(decide.view, "decide");
  assert.equal(decide.type, "review");
  assert.equal(decide.layer, "l3");
  assert.equal(decide.match, "involved");
  assert.equal(decide.gov, true);
  assert.equal(decide.group, "layer");
  assert.equal(decide.q, "桥");
  assert.equal(formatHash(decide), "#/decide?q=%E6%A1%A5&type=review&layer=l3&match=involved&gov=1&group=layer");

  const issues = parseHash("#/issues?layer=l3&match=involved&ws=acm-lab");
  assert.equal(issues.view, "issues");
  assert.equal(issues.layer, "l3");
  assert.equal(issues.match, "involved");
  assert.equal(formatHash(issues), "#/issues?ws=acm-lab&layer=l3&match=involved");

  const agents = parseHash("#/agents?group=provider&provider=codex&failed=1&offline=1&archived=1");
  assert.equal(agents.view, "agents");
  assert.equal(agents.group, "provider");
  assert.equal(agents.failedOnly, true);
  assert.equal(formatHash(agents), "#/agents?group=provider&provider=codex&failed=1&offline=1&archived=1");
});

test("legacy hash parses as the destination, so a refresh stays there", () => {
  const route = parseHash("#/l3/issues?scope=involved");
  assert.equal(route.view, "map");
  assert.equal(route.layer, "l3");
  assert.equal(route.scope, "involved");
  assert.equal(formatHash(route), "#/map/l3?scope=involved");
});

test("switching views by number does not keep the previous layer filter", () => {
  const decide = parseHash("#/decide?layer=l3&gov=1");
  const map = applyPatch(decide, { view: "map" });
  assert.equal(map.view, "map");
  assert.equal(map.layer, "");
  assert.equal(map.gov, false);
  assert.equal(formatHash(map), "#/map");
});

test("number keys follow the top bar, and [ ] walk the layers", () => {
  assert.deepEqual([1, 2, 3, 4, 5].map((n) => viewForKey(String(n))), ["map", "decide", "issues", "agents", "skills"]);
  assert.equal(stepLayer("l5", "next"), "l4");
  assert.equal(stepLayer("l1", "next"), "l5");
  assert.equal(stepLayer("l1", "prev"), "l2");
  assert.equal(stepLayer("l5", "prev"), "l1");
  assert.equal(stepLayer("all", "next"), null);
});
