import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyIssue } from './attention.js';

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
