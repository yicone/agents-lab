import test from 'node:test';
import assert from 'node:assert/strict';
import {agentColumns,recentRuns,timeUTC8,agentFocusControl} from './ia-view.js';
import {parseHash,formatHash,applyPatch} from './route.js';
test('status and configuration have distinct task columns; selection keeps config columns',()=>{
 assert.deepEqual(agentColumns(false,false),['Agent','运行或排队','当前任务','24h 失败 / 完成','最近失败','最近运行','同一 Agent']);
 const config=agentColumns(true,false);
 for(const column of ['Runtime','模型','Instructions 来源','Skills 绑定','MCP 绑定','运行配置']) assert.ok(config.includes(column));
 assert.ok(!config.includes('当前任务'));
 assert.deepEqual(agentColumns(false,true),['选择',...config]);
});
test('recent runs filter workspace and agent/title query then sort newest first',()=>{
 const rows=[{workspace_id:'a',title:'old',latest_run:{created_at:'2026-10-20T00:00Z'}},{workspace_id:'a',title:'new',latest_run:{started_at:'2026-10-24T00:00Z'}},{workspace_id:'b',title:'other',latest_run:{created_at:'2026-10-25T00:00Z'}},{workspace_id:'a',title:'no run'}];
 const ws=[{id:'a',slug:'alpha'},{id:'b',slug:'beta'}];
 const match=(q,...values)=>!q||values.some(v=>v?.includes(q));
 assert.deepEqual(recentRuns(rows,ws,{ws:'alpha'},match).map(it=>it.title),['new','old']);
 assert.deepEqual(recentRuns(rows,ws,{ws:'alpha',q:'old'},match).map(it=>it.title),['old']);
 assert.equal(rows[0].title,'old');
});
test('UTC+8 is independent of browser timezone and invalid dates stay unknown',()=>{
 assert.match(timeUTC8('2026-10-24T20:30:00Z'),/2026\/10\/25 04:30:00/);
 assert.equal(timeUTC8(null),'未知时间');
 assert.equal(timeUTC8('invalid'),'未知时间');
});
test('same-agent view links retain workspace, agent and query through canonical routes',()=>{
 const r=parseHash('#/running?ws=clipvibe&sel=agent1&q=hello');
 const config=formatHash({...r,section:'configure'});
 assert.equal(config,'#/configure/agents?ws=clipvibe&q=hello&sel=agent1');
 assert.equal(formatHash({...parseHash(config),section:''}),'#/running?ws=clipvibe&q=hello&sel=agent1');
});

test('ordinary Agent focus clears on workspace switch and clear, in both views',()=>{
 for(const section of ['', 'configure']){
  const route={...parseHash('#/running?ws=clipvibe&sel=clip-agent'),section};
  assert.equal(applyPatch(route,{ws:'acm-lab'}).sel,'');
  assert.equal(applyPatch(route,{ws:''}).sel,'');
  assert.equal(applyPatch(route,{ws:'clipvibe'}).sel,'clip-agent');
  assert.equal(applyPatch({...route,planMode:true},{ws:'acm-lab'}).sel,'clip-agent');
  assert.equal(applyPatch({...route,planPage:'new'},{ws:'acm-lab'}).sel,'clip-agent');
 }
});
test('both ordinary views expose removable Agent focus; planner keeps existing selection UI',()=>{
 const h=(tag,attrs,...children)=>({tag,attrs,children});
 let clears=0;
 for(const section of ['', 'configure']){
  const node=agentFocusControl(h,{sel:'clip-agent',section},[{id:'clip-agent',name:'ClipVibe 产品'}],()=>clears++);
  assert.match(node.children[0],/仅此 Agent：ClipVibe 产品/);
  assert.equal(node.children[1].attrs['aria-label'],'清除仅此 Agent 筛选');
  node.children[1].attrs.onclick();
 }
 assert.equal(clears,2);
 assert.equal(agentFocusControl(h,{sel:'a',planMode:true},[],()=>{}),null);
 assert.equal(agentFocusControl(h,{sel:''},[],()=>{}),null);
});
