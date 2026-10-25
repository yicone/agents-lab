import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyIssue, attentionSummary, scopedAttentionSummary } from './attention.js';

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
  assert.equal(attentionSummary({workspaces:[]}, [], at, age).missing, 0);
  assert.equal(attentionSummary(null, [], at, age).text, "?");
  assert.equal(attentionSummary({...allWorkspaces, collector:{error:{kind:"auth"}}}, [], at, age).text, "?");
});

test("workspace inventory determines coverage without a fixed eleven", () => {
 const meta={workspaces:allWorkspaces.workspaces.slice(0,2)};
 assert.equal(attentionSummary(meta, [], at, age).incomplete,false);
 assert.equal(attentionSummary({...meta,workspaces:[]}, [], at, age).incomplete,true);
});

test('healthy selected workspace stays complete when other sources or directory fail', () => {
 const meta={workspaces:[{id:'clip',slug:'clipvibe',name:'ClipVibe',collect_state:'ok',counts:{}},{id:'acm',slug:'acm-lab',name:'ACM Lab',collect_state:'error',counts:{}}],collector:{error:{kind:'network'}}};
 const issues=[{workspace_id:'clip',status:'in_review'},{workspace_id:'acm',status:'blocked'}];
 const global=attentionSummary(meta,issues,at,age);
 const local=scopedAttentionSummary(meta,issues,at,age,'clipvibe');
 assert.equal(global.incomplete,true);assert.equal(global.count,2);
 assert.equal(local.incomplete,false);assert.equal(local.count,1);assert.deepEqual(local.unknown,[]);
});
test('selected unhealthy or absent workspace stays unknown when other sources are healthy', () => {
 const healthy={id:'acm',slug:'acm-lab',collect_state:'ok',counts:{}};
 for(const collect_state of ['never','error','stale']){
  const meta={workspaces:[healthy,{id:'clip',slug:'clipvibe',name:'ClipVibe',collect_state,counts:{}}]};
  const local=scopedAttentionSummary(meta,[{workspace_id:'clip',status:'in_review'},{workspace_id:'acm',status:'blocked'}],at,age,'clipvibe');
  assert.equal(local.incomplete,true);assert.equal(local.count,1);assert.deepEqual(local.unknown,['ClipVibe']);
 }
 assert.equal(scopedAttentionSummary({workspaces:[healthy]},[],at,age,'missing').incomplete,true);
 assert.equal(scopedAttentionSummary(null,[],at,age,'clipvibe').incomplete,true);
});
test('unfiltered page preserves global completeness',()=>{
 const meta={workspaces:[{id:'a',collect_state:'error'}]};
 assert.deepEqual(scopedAttentionSummary(meta,[],at,age,''),attentionSummary(meta,[],at,age));
});
