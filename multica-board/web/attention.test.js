import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyIssue, attentionSummary } from './attention.js';

test('completed upstream error has an independent attention lane', () => {
  const issue = {status:'in_progress', latest_run:{status:'completed', delivery_anomaly:'upstream_error'}};
  assert.equal(classifyIssue(issue, Date.now(), 1000).lane, 'delivery_anomaly');
  issue.latest_run = {status:'failed'};
  assert.equal(classifyIssue(issue, Date.now(), 1000).lane, 'failed');
  issue.latest_run = {status:'completed'};
  issue.last_delivery_anomaly = {task_id:'older'};
  const cls = classifyIssue(issue, Date.now(), 1000);
  assert.equal(cls.lane, null);
  assert.deepEqual(cls.tags, [['muted','曾交付异常']]);
});

const allWorkspaces = {workspaces: Array.from({length:11}, (_,i) => ({id:`w${i}`, name:`Workspace ${i}`, collect_state:"ok", counts:{open_issues:0}}))};
const at = Date.parse("2026-10-24T12:00:00Z");
const age = 3 * 86400_000;
test("attention counts every workspace and each issue only once", () => {
  const issues = allWorkspaces.workspaces.map((w,i) => ({workspace_id:w.id, status:i%2 ? "blocked":"in_review", latest_run:i===0 ? {status:"failed"}:null}));
  const summary = attentionSummary(allWorkspaces, issues, at, age);
  assert.equal(summary.count, 11);
  assert.equal(summary.tone, "bad");
  assert.equal(summary.text, "11");
});
test("pending is yellow; confirmed zero hides; missing/stale/error are gray unknown", () => {
  assert.equal(attentionSummary(allWorkspaces, [{status:"in_review"}], at, age).tone, "warn");
  assert.equal(attentionSummary(allWorkspaces, [], at, age).tone, "none");
  assert.equal(attentionSummary(allWorkspaces, [], at, age).text, "");
  for (const collect_state of ["never", "error", "stale"]) {
    const meta = structuredClone(allWorkspaces);
    meta.workspaces[10].collect_state = collect_state;
    const summary = attentionSummary(meta, [], at, age);
    assert.equal(summary.tone, "unknown");
    assert.equal(summary.text, "?");
    assert.match(summary.title, /Workspace 10/);
  }
  assert.equal(attentionSummary({workspaces:[]}, [], at, age).missing, 11);
  assert.equal(attentionSummary(null, [], at, age).text, "?");
  assert.equal(attentionSummary({...allWorkspaces, collector:{error:{kind:"auth"}}}, [], at, age).text, "?");
});
