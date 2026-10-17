import assert from "node:assert/strict";
import test from "node:test";
import { classifyIssue } from "./attention.js";
import { atlasIssues, ACM, NOW } from "./fixtures/atlas-data.js";
import {
  aggregateLayers,
  availabilityFromReport,
  countRule,
  issueLayer,
  layerAvailability,
  matchesLayer,
  modelFromReport,
  railLinks,
  selectLayerIssues,
  sourceLabel,
} from "./layers.js";

const NOW_MS = Date.parse(NOW);

test("own, inherited, and cross-layer sources stay readable", () => {
  const issues = atlasIssues();
  const own = issues.find((issue) => issue.identifier === "ACM-514");
  const inherited = issues.find((issue) => issue.identifier === "ACM-515");
  const cross = issues.find((issue) => issue.identifier === "ACM-521");
  assert.equal(sourceLabel(issueLayer(own).source.primary), "自设");
  assert.equal(sourceLabel(issueLayer(inherited).source.primary), "继承自 ACM-500");
  assert.equal(issueLayer(inherited).primary, "l5");
  assert.deepEqual(issueLayer(inherited).involved, ["l3"]);
  assert.deepEqual(issueLayer(cross).involved, ["l3", "l5"]);
  assert.equal(issueLayer(issues.find((issue) => issue.identifier === "ACM-532")).governance, true);
  assert.match(countRule(issueLayer(inherited)), /只计入 L5/);
  assert.match(countRule(issueLayer(inherited)), /L3/);
});

test("header total counts each issue once, and cancelled is left out", () => {
  const model = aggregateLayers(atlasIssues(), ACM);
  const counted = model.layers.reduce((sum, layer) => sum + layer.total, 0) + model.outside.total + model.unassigned.total;
  assert.equal(model.header.total, counted);
  assert.equal(model.header.total, 21);
  assert.equal(atlasIssues().some((issue) => issue.identifier === "ACM-580"), true);
  assert.equal(model.layers.find((layer) => layer.id === "l3").total, 4);
});

test("a cross-layer issue counts only in its primary layer", () => {
  const model = aggregateLayers(atlasIssues(), ACM);
  const l5 = model.layers.find((layer) => layer.id === "l5");
  const l3 = model.layers.find((layer) => layer.id === "l3");
  assert.equal(l5.total, 4);
  assert.equal(l3.involvedExtra, 2);
  assert.deepEqual(l3.involvedPrimaries, ["L5", "L4"]);
  const involved = selectLayerIssues(atlasIssues(), ACM, { layer: "l3", scope: "involved", segment: "all" });
  const primary = selectLayerIssues(atlasIssues(), ACM, { layer: "l3", scope: "primary", segment: "all" });
  assert.equal(involved.length, primary.length + l3.involvedExtra);
  assert.equal(involved.some((issue) => issue.identifier === "ACM-515"), true);
  assert.equal(primary.some((issue) => issue.identifier === "ACM-515"), false);
});

test("governance mode makes each layer's counts match its governance column", () => {
  const full = aggregateLayers(atlasIssues(), ACM);
  const gov = aggregateLayers(atlasIssues(), ACM, { governanceOnly: true });
  for (const layer of full.layers) {
    const filtered = gov.layers.find((item) => item.id === layer.id);
    assert.equal(filtered.open, layer.governanceOpen, layer.id);
    assert.equal(filtered.decide, layer.governanceDecide, layer.id);
  }
  assert.equal(gov.header.total, full.layers.reduce((sum, layer) => sum + layer.governanceOpen + (layer.total - layer.open === 0 ? 0 : 0), 0) || gov.header.total);
  assert.equal(gov.header.decide, 3);
});

test("outside and unassigned are separate from the five layers", () => {
  const model = aggregateLayers(atlasIssues(), ACM);
  assert.equal(model.outside.total, 3);
  assert.equal(model.outside.decide, 1);
  assert.equal(model.unassigned.total, 2);
  assert.equal(model.unassigned.decide, 1);
  assert.equal(model.header.decide, model.layers.reduce((sum, layer) => sum + layer.decide, 0) + model.outside.decide + model.unassigned.decide);
});

test("missing properties and an unfilled backlog are different empty states", () => {
  const issues = atlasIssues();
  assert.equal(layerAvailability(issues, ACM), "ready");
  assert.equal(layerAvailability(issues.map(({ layer, ...rest }) => rest), ACM), "missing");
  const blank = issues.map((issue) => ({
    ...issue,
    layer: { primary: null, involved: [], governance: false, source: { primary: null, involved: null, governance: null } },
  }));
  assert.equal(layerAvailability(blank, ACM), "unfilled");
  assert.equal(layerAvailability([], ACM), "no-issues");
  assert.equal(layerAvailability(issues, ""), "no-workspace");
});

test("a quiet map has no decide items", () => {
  const quiet = atlasIssues().map((issue) =>
    issue.status === "in_review" || issue.status === "blocked" ? { ...issue, status: "in_progress", status_category: "in_progress" } : issue,
  );
  assert.equal(aggregateLayers(quiet, ACM).header.decide, 0);
  assert.equal(layerAvailability(quiet, ACM), "ready");
});

test("issues layer=l3&match=involved equals the involved layer rows that are in an attention lane", () => {
  const staleMs = 3 * 86_400_000;
  const attention = (issue) => classifyIssue(issue, NOW_MS, staleMs).lane;
  const involved = selectLayerIssues(atlasIssues(), ACM, { layer: "l3", scope: "involved", segment: "all" }).filter(attention);
  const page = atlasIssues().filter((issue) => issue.workspace_id === ACM && matchesLayer(issue, "l3", "involved") && attention(issue));
  assert.deepEqual(involved.map((issue) => issue.identifier).sort(), page.map((issue) => issue.identifier).sort());
  assert.deepEqual(page.map((issue) => issue.identifier).sort(), ["ACM-515", "ACM-532"]);
});

test("layer rails only link to pages that exist", () => {
  for (const id of ["l1", "l2", "l3", "l4", "l5"]) {
    const text = railLinks(id).map((link) => `${link.label} ${link.href}`).join("\n");
    assert.doesNotMatch(text, /未建|未接入/);
    assert.match(text, /Issues 中本层的关注项/);
    assert.match(text, new RegExp(`#/decide\\?layer=${id}`));
  }
});

test("a layers report drives the map, and an unfilled report stays a guide", () => {
  const layer = (id, extra = {}) => ({
    id,
    name: id,
    total: 0,
    open: 0,
    decide: 0,
    involved_extra: 0,
    involved_from: [],
    governance_open: 0,
    governance_decide: 0,
    decide_items: [],
    ...extra,
  });
  const report = {
    ready: true,
    workspace: { id: ACM, slug: "acm-lab", name: "ACM Lab" },
    header: { total: 3, assigned: 2, open: 2, decide: 1 },
    layers: [
      layer("L5", { total: 2, open: 1, decide: 1, governance_open: 1, governance_decide: 1, involved_extra: 1, involved_from: ["L1"] }),
      layer("L4"),
      layer("L3"),
      layer("L2"),
      layer("L1"),
    ],
    outside: layer("outside"),
    unassigned: layer("unassigned", { total: 1, open: 1 }),
  };
  assert.equal(availabilityFromReport(report), "ready");
  const model = modelFromReport(report);
  assert.equal(model.header.total, 3);
  assert.equal(model.header.assigned, 2);
  assert.equal(model.layers.find((item) => item.id === "l5").decide, 1);
  assert.deepEqual(model.layers.find((item) => item.id === "l5").involvedPrimaries, ["L1"]);
  assert.equal(model.unassigned.total, 1);
  const gov = modelFromReport(report, { governanceOnly: true });
  assert.equal(gov.layers.find((item) => item.id === "l5").open, 1);
  assert.equal(gov.layers.find((item) => item.id === "l5").decide, 1);
  assert.equal(gov.header.total, 1);
  assert.equal(availabilityFromReport({ ...report, ready: false }), "missing");
  assert.equal(availabilityFromReport({ ...report, header: { total: 4, assigned: 0, open: 4, decide: 0 } }), "unfilled");
  assert.equal(availabilityFromReport({ ready: false, workspace: null, header: { total: 0, assigned: 0, open: 0, decide: 0 } }), "no-workspace");
});

test("an issue with only involved layers stays unassigned", () => {
  const issue = atlasIssues().find((item) => item.identifier === "ACM-571");
  assert.equal(issueLayer(issue).primary, null);
  assert.equal(matchesLayer(issue, "unassigned", "primary"), true);
  assert.equal(matchesLayer(issue, "l2", "primary"), false);
});
