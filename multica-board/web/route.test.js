import assert from "node:assert/strict";
import test from "node:test";
import { applyPatch, formatHash, navGroup, historyWrite, parseHash, redirectHash, setSkillTools, stepLayer, viewForKey } from "./route.js";

test("runtime deep links survive initial load and whitelist follows payload columns", () => {
  assert.equal(parseHash("#/skills?tool=grok").tool, "grok");
  setSkillTools([{ id: "codex" }, { id: "grok" }, { id: "new-runtime" }]);
  assert.equal(parseHash("#/skills?tool=grok").tool, "grok");
  assert.equal(parseHash("#/skills?tool=new-runtime").tool, "new-runtime");
  assert.equal(parseHash("#/skills?tool=unconfigured").tool, "");
  setSkillTools([{ id: "codex" }]);
  assert.equal(parseHash("#/skills?tool=grok").tool, "");
});

test("#/issues stays", () => {
  assert.equal(redirectHash("#/issues"), null);
  assert.equal(redirectHash("#/issues?ws=acm-lab&q=桥"), null);
});

test("#/agents redirects to running", () => {
  assert.equal(redirectHash("#/agents"), "#/running");
  assert.equal(redirectHash("#/agents?group=provider&failed=1"), "#/running?group=provider&failed=1");
});

test("#/skills redirects to configure", () => {
  assert.equal(redirectHash("#/skills"), "#/configure/skills");
  assert.equal(redirectHash("#/skills?q=guard"), "#/configure/skills?q=guard");
});

test("#/usage redirects to running usage", () => {
  assert.equal(redirectHash("#/usage"), "#/running/usage");
  assert.equal(redirectHash("#/usage?q=hermes"), "#/running/usage?q=hermes");
  const route = parseHash("#/usage?q=hermes");
  assert.equal(route.view, "usage");
  assert.equal(route.q, "hermes");
  assert.equal(formatHash(route), "#/running/usage?q=hermes");
  assert.equal(applyPatch({ view: "map" }, { view: "usage" }).view, "usage");
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
  assert.equal(formatHash(route), "#/configure/skills?q=vis&source=runtime_plugin&status=version_changed&tool=codex&custom=1&debt=1&group=tool");
});

test("#/l5/attention goes to issues", () => {
  assert.equal(redirectHash("#/l5/attention"), "#/issues");
  assert.equal(redirectHash("#/l5/attention?q=1"), "#/issues");
});

test("#/l2/agents goes to agents", () => {
  assert.equal(redirectHash("#/l2/agents"), "#/running");
});

test("#/l3/skills goes to skills", () => {
  assert.equal(redirectHash("#/l3/skills"), "#/configure/skills");
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
  assert.equal(formatHash(agents), "#/running?group=provider&provider=codex&failed=1&offline=1&archived=1");
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
  assert.equal(historyWrite("#/agents?ws=acm-lab", "#/running", { ws: "" }), "push");
  assert.equal(historyWrite("#/issues?ws=acm-lab", "#/running?ws=acm-lab", { view: "agents" }), "push");
  assert.equal(historyWrite("#/map", "#/configure/skills", { view: "skills" }), "push");
});

test("repeat clicks, refreshes, and equivalent deep links do not add history", () => {
  assert.equal(historyWrite("#/issues?ws=clipvibe", "#/issues?ws=clipvibe", { ws: "clipvibe" }), "noop");
  assert.equal(historyWrite("#/skills", "#/configure/skills", { view: "skills" }), "noop");
  assert.equal(historyWrite("#/issues", "#/issues", { ws: "" }), "noop");
  assert.equal(historyWrite("#/issues?q=a&ws=acm-lab", "#/issues?ws=acm-lab&q=a", { view: "issues" }), "noop");
  assert.equal(historyWrite("#/l2/agents", "#/running", { view: "agents" }), "noop");
});

test("search and other controls replace the current entry", () => {
  assert.equal(historyWrite("#/issues?ws=acm-lab", "#/issues?ws=acm-lab&q=a", { q: "a" }), "replace");
  assert.equal(historyWrite("#/issues", "#/issues?layer=l3", { layer: "l3" }), "replace");
  assert.equal(historyWrite("#/agents", "#/running?failed=1", { failedOnly: true }), "replace");
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
  assert.equal(stack[index], "#/running?q=%E6%A1%A5");

  back();
  assert.equal(formatHash(route), "#/running?ws=acm-lab");
  back();
  assert.equal(formatHash(route), "#/issues?ws=acm-lab");
  back();
  assert.equal(formatHash(route), "#/issues");
  forward();
  assert.equal(route.view, "issues");
  assert.equal(route.ws, "acm-lab");

  assert.equal(commit({ view: "skills" }), "push");
  assert.equal(stack[index], "#/configure/skills?ws=acm-lab");
  assert.equal(stack.includes("#/agents?q=%E6%A1%A5"), false);
  assert.equal(index, stack.length - 1);
});

test("switch-plan routes stay on Agents and round-trip", () => {
  assert.equal(redirectHash("#/agents/plan/new"), "#/running/changes/new");
  assert.equal(redirectHash("#/agents/plans"), "#/running/changes");
  assert.equal(redirectHash("#/agents/plans/hist"), "#/running/changes/hist");
  assert.equal(redirectHash("#/agents?plan=1&sel=aaa,bbb"), "#/running?plan=1&sel=aaa%2Cbbb");

  const selecting = parseHash("#/agents?plan=1&group=provider&failed=1&sel=aaa,bbb");
  assert.equal(selecting.view, "agents");
  assert.equal(selecting.planMode, true);
  assert.equal(selecting.planPage, "");
  assert.equal(selecting.sel, "aaa,bbb");
  assert.equal(selecting.failedOnly, true);
  assert.equal(selecting.group, "provider");
  const kept = parseHash(formatHash(applyPatch(selecting, { failedOnly: false })));
  assert.equal(kept.sel, "aaa,bbb");
  assert.equal(kept.planMode, true);
  assert.equal(kept.failedOnly, false);

  const compose = parseHash("#/agents/plan/new?sel=aaa,bbb&rt=rt-1&model=set:gpt&effort=clear&tier=omit");
  assert.equal(compose.planPage, "new");
  assert.equal(compose.view, "agents");
  assert.equal(compose.sel, "aaa,bbb");
  assert.equal(compose.rt, "rt-1");
  assert.equal(compose.model, "set:gpt");
  assert.equal(compose.effort, "clear");
  assert.equal(compose.tier, "omit");
  assert.equal(formatHash(compose), "#/running/changes/new?sel=aaa%2Cbbb&rt=rt-1&model=set%3Agpt&effort=clear");
  const restored = parseHash("#/agents/plan/new?from=hist&sel=aaa&model=conflict");
  assert.equal(restored.fromPlan, "hist");
  assert.equal(restored.model, "conflict");
  assert.match(formatHash(restored), /from=hist/);
  assert.match(formatHash(restored), /model=conflict/);

  const review = parseHash("#/agents/plans/hist?bucket=blocked");
  assert.equal(review.planPage, "review");
  assert.equal(review.planId, "hist");
  assert.equal(review.bucket, "blocked");
  assert.equal(formatHash(review), "#/running/changes/hist?bucket=blocked");
  assert.equal(parseHash("#/agents/plans?fix=empty").planFix, "empty");
  assert.equal(formatHash(parseHash("#/agents/plans?fix=empty")), "#/running/changes?fix=empty");
});

test("leaving Agents clears the plan subview, and plan steps push history", () => {
  const review = parseHash("#/agents/plans/hist?sel=aaa");
  const map = applyPatch({ ...review, fromPlan: "hist" }, { view: "map" });
  assert.equal(map.planPage, "");
  assert.equal(map.planId, "");
  assert.equal(map.fromPlan, "");
  assert.equal(formatHash(map), "#/map");
  assert.equal(historyWrite("#/agents?plan=1&sel=aaa", "#/running/changes/new?sel=aaa", { planPage: "new", planMode: false }), "push");
  assert.equal(historyWrite("#/agents/plan/new?sel=aaa", "#/running?plan=1&sel=aaa", { planPage: "", planMode: true }), "push");
  assert.equal(historyWrite("#/agents/plans", "#/running/changes/hist", { planPage: "review", planId: "hist" }), "push");
  assert.equal(historyWrite("#/agents?plan=1&sel=aaa", "#/running?plan=1&sel=aaa,bbb", { sel: "aaa,bbb" }), "replace");
});

test("number keys follow the top bar, and [ ] walk the layers", () => {
  assert.deepEqual([1, 2, 3, 4, 5, 6].map((n) => viewForKey(String(n))), ["issues", "agents", "configure", "map", null, null]);
  assert.equal(stepLayer("l5", "next"), "l4");
  assert.equal(stepLayer("l1", "next"), "l5");
  assert.equal(stepLayer("l1", "prev"), "l2");
  assert.equal(stepLayer("l5", "prev"), "l1");
  assert.equal(stepLayer("all", "next"), null);
});

test("agents subviews round-trip and leaving the page clears them", () => {
  const bots = parseHash("#/agents?kind=bots&ws=alpha-lab&q=职责&linked=1");
  assert.equal(bots.view, "agents");
  assert.equal(bots.kind, "bots");
  assert.equal(bots.botLinked, true);
  assert.equal(bots.q, "职责");
  assert.equal(formatHash(bots), "#/running?ws=alpha-lab&q=%E8%81%8C%E8%B4%A3&kind=bots&linked=1");
  const instructions = parseHash("#/agents?has_instructions=1");
  assert.equal(instructions.hasInstructions, true);
  assert.equal(instructions.kind, "");
  assert.equal(formatHash(instructions), "#/running?has_instructions=1");
  assert.equal(formatHash(parseHash("#/agents?plan=1")), "#/running?plan=1");
  const left = applyPatch(bots, { view: "map" });
  assert.equal(left.kind, "");
  assert.equal(left.botLinked, false);
  assert.equal(formatHash(left), "#/map?ws=alpha-lab&q=%E8%81%8C%E8%B4%A3");
  assert.equal(historyWrite("#/agents", "#/running?kind=bots", { kind: "bots" }), "push");
  assert.equal(historyWrite("#/agents", "#/running?has_instructions=1", { hasInstructions: true }), "replace");
});

test("cloud pseudo source round-trips and bogus sources still drop", () => {
  const route = parseHash("#/skills?source=cloud");
  assert.equal(route.source, "cloud");
  assert.equal(formatHash(route), "#/configure/skills?source=cloud");
  assert.equal(parseHash("#/skills?source=cloud&status=stale").skillStatus, "stale");
  assert.equal(parseHash("#/skills?source=bogus").source, "");
  assert.equal(formatHash(parseHash("#/skills?source=bogus")), "#/configure/skills");
});

test("unrecorded reconciliation filter round trips through a deep link", () => {
  const route = parseHash("#/skills?status=unrecorded&source=unrecorded");
  assert.equal(route.skillStatus,"unrecorded");
  assert.equal(route.source,"unrecorded");
  assert.match(formatHash(route),/status=unrecorded/);
});

test("Grok Bot source filter survives routing and does not become a runtime",()=>{
  const route=parseHash("#/skills?source=grok_bot");
  assert.equal(route.source,"grok_bot");
  assert.match(formatHash(route),/source=grok_bot/);
});

test("mcp route: parse, serialize, whitelist, legacy drop and l3 redirect", () => {
  const r = parseHash("#/mcp?ws=alpha-lab&source=both&status=differs&tool=cursor&group=tool&assigned=1&tidy=1&q=lin");
  assert.equal(r.view, "mcp");
  assert.equal(r.source, "both");
  assert.equal(r.mcpStatus, "differs");
  assert.equal(r.tool, "cursor");
  assert.equal(r.mcpGroup, "tool");
  assert.equal(r.mcpAssigned, true);
  assert.equal(r.mcpTidy, true);
  assert.equal(r.ws, "alpha-lab");
  assert.equal(r.q, "lin");
  assert.equal(formatHash(r), "#/configure/mcp?ws=alpha-lab&q=lin&source=both&status=differs&tool=cursor&group=tool&assigned=1&tidy=1");
  const legacy = parseHash("#/mcp?view=list&issue=shadowed&archived=1");
  assert.equal(legacy.mcpView, undefined);
  assert.equal(formatHash(legacy), "#/configure/mcp");
  const bad = parseHash("#/mcp?source=nope&status=nope&group=nope");
  assert.equal(bad.source, "");
  assert.equal(bad.mcpStatus, "");
  assert.equal(bad.mcpGroup, "source");
  assert.equal(formatHash(bad), "#/configure/mcp");
  assert.equal(redirectHash("#/l3/mcp"), "#/configure/mcp");
  const left = applyPatch(parseHash("#/mcp?source=both&status=differs&tool=cursor&assigned=1&tidy=1"), { view: "skills" });
  // source is shared between boards; MCP-specific filters are cleared
  assert.equal(left.source, "both");
  assert.equal(left.mcpStatus, "");
  assert.equal(left.mcpGroup, "source");
  assert.equal(left.mcpAssigned, false);
  assert.equal(left.mcpTidy, false);
});

test("IA v2 canonical routes round trip without alias loops", () => {
  for (const hash of ["#/running", "#/running/changes", "#/running/changes/new", "#/running/changes/hist", "#/running/usage", "#/running/runs", "#/running/metrics", "#/running/schedules", "#/running/health", "#/configure/agents", "#/configure/skills", "#/configure/mcp", "#/configure/hooks", "#/configure/runtime", "#/settings"]) {
    assert.equal(redirectHash(hash), null, hash);
    assert.equal(formatHash(parseHash(hash)), hash, hash);
  }
  assert.equal(navGroup(parseHash("#/configure/agents")), "configure");
  assert.equal(navGroup(parseHash("#/running")), "running");
  assert.equal(navGroup(parseHash("#/decide")), "issues");
});
test("legacy bookmarks preserve filters and become canonical in one replacement", () => {
  for (const [old, current] of [["configure","configure/agents"],["agents","running"],["skills","configure/skills"],["mcp","configure/mcp"],["usage","running/usage"]]) {
    const redirected = redirectHash(`#/${old}?ws=alpha&q=test`);
    assert.equal(redirected, `#/${current}?ws=alpha&q=test`);
    assert.equal(redirectHash(redirected), null);
  }
  const route = parseHash("#/configure/agents?ws=alpha&has_instructions=1");
  assert.equal(route.hasInstructions, true);
  assert.equal(formatHash(route), "#/configure/agents?ws=alpha&has_instructions=1");
});

test("switching status/configuration preserves identity and filters", () => {
  const route = parseHash("#/configure/agents?ws=alpha&has_instructions=1");
  const running = applyPatch(route, {view:"agents", section:""});
  assert.equal(formatHash(running), "#/running?ws=alpha&has_instructions=1");
  assert.equal(historyWrite(formatHash(route), formatHash(running), {section:""}), "push");
  assert.equal(formatHash(applyPatch(route, {view:"issues"})), "#/issues?ws=alpha");
});
test("run metrics route keeps window filters and leaves them behind other views", () => {
  const hash = "#/running/metrics?q=quota&win=24h&mws=ws-1&magent=agent-quota&mrt=runtime-1&missue=b";
  assert.equal(redirectHash(hash), null);
  assert.equal(formatHash(parseHash(hash)), hash);
  assert.equal(navGroup(parseHash(hash)), "running");
  assert.equal(parseHash("#/running/metrics?win=2d&mws=../secret").win, "");
  assert.equal(parseHash("#/running/metrics?win=2d&mws=../secret").mws, "");
  const left = applyPatch(parseHash(hash), { view: "usage" });
  assert.equal(left.win, "");
  assert.equal(left.mws, "");
  assert.equal(formatHash(left), "#/running/usage?q=quota");
});

test("Grok Bot export source round trips independently of name declarations", () => {
  const route = parseHash("#/configure/skills?source=grok_bot_export");
  assert.equal(route.source, "grok_bot_export");
  assert.match(formatHash(route), /source=grok_bot_export/);
});
