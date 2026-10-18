import assert from "node:assert/strict";
import test from "node:test";
import { applyPatch, formatHash, historyWrite, parseHash, redirectHash, stepLayer, viewForKey } from "./route.js";

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

test("workspace chip, clear, and tab changes push one history step", () => {
  assert.equal(historyWrite("#/issues", "#/issues?ws=acm-lab", { ws: "acm-lab" }), "push");
  assert.equal(historyWrite("#/agents?ws=acm-lab", "#/agents", { ws: "" }), "push");
  assert.equal(historyWrite("#/issues?ws=acm-lab", "#/agents?ws=acm-lab", { view: "agents" }), "push");
  assert.equal(historyWrite("#/map", "#/skills", { view: "skills" }), "push");
});

test("repeat clicks, refreshes, and equivalent deep links do not add history", () => {
  assert.equal(historyWrite("#/issues?ws=clipvibe", "#/issues?ws=clipvibe", { ws: "clipvibe" }), "noop");
  assert.equal(historyWrite("#/skills", "#/skills", { view: "skills" }), "noop");
  assert.equal(historyWrite("#/issues", "#/issues", { ws: "" }), "noop");
  assert.equal(historyWrite("#/issues?q=a&ws=acm-lab", "#/issues?ws=acm-lab&q=a", { view: "issues" }), "noop");
  assert.equal(historyWrite("#/l2/agents", "#/agents", { view: "agents" }), "noop");
});

test("search and other controls replace the current entry", () => {
  assert.equal(historyWrite("#/issues?ws=acm-lab", "#/issues?ws=acm-lab&q=a", { q: "a" }), "replace");
  assert.equal(historyWrite("#/issues", "#/issues?layer=l3", { layer: "l3" }), "replace");
  assert.equal(historyWrite("#/agents", "#/agents?failed=1", { failedOnly: true }), "replace");
});

test("back and forward restore each chip, clear, and tab step", () => {
  let route = parseHash("#/issues");
  const stack = [formatHash(route)];
  let index = 0;
  const commit = (patch) => {
    const nextRoute = applyPatch(route, patch);
    const next = formatHash(nextRoute);
    const action = historyWrite(stack[index], next, patch);
    if (action === "push") {
      stack.splice(index + 1);
      stack.push(next);
      index += 1;
      route = nextRoute;
    } else if (action === "replace") {
      stack[index] = next;
      route = nextRoute;
    }
    return action;
  };
  const back = () => {
    index -= 1;
    route = parseHash(stack[index]);
  };
  const forward = () => {
    index += 1;
    route = parseHash(stack[index]);
  };

  assert.equal(commit({ ws: "acm-lab" }), "push");
  assert.equal(commit({ view: "agents" }), "push");
  assert.equal(commit({ ws: "" }), "push");
  assert.equal(commit({ view: "agents" }), "noop");
  assert.equal(commit({ q: "桥" }), "replace");
  assert.equal(stack[index], "#/agents?q=%E6%A1%A5");

  back();
  assert.equal(formatHash(route), "#/agents?ws=acm-lab");
  back();
  assert.equal(formatHash(route), "#/issues?ws=acm-lab");
  back();
  assert.equal(formatHash(route), "#/issues");
  forward();
  assert.equal(route.view, "issues");
  assert.equal(route.ws, "acm-lab");

  assert.equal(commit({ view: "skills" }), "push");
  assert.equal(stack[index], "#/skills?ws=acm-lab");
  assert.equal(stack.includes("#/agents?q=%E6%A1%A5"), false);
  assert.equal(index, stack.length - 1);
});

test("number keys follow the top bar, and [ ] walk the layers", () => {
  assert.deepEqual([1, 2, 3, 4, 5].map((n) => viewForKey(String(n))), ["map", "decide", "issues", "agents", "skills"]);
  assert.equal(stepLayer("l5", "next"), "l4");
  assert.equal(stepLayer("l1", "next"), "l5");
  assert.equal(stepLayer("l1", "prev"), "l2");
  assert.equal(stepLayer("l5", "prev"), "l1");
  assert.equal(stepLayer("all", "next"), null);
});
