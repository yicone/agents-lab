import test from 'node:test';
import assert from 'node:assert/strict';
import { markdownNodes, splitFrontmatter, lineDiff } from './skill-viewer.js';
import { classifyContentResponse, loadSkillContent, contentKey } from './skill-content.js';
const h = (tag, attrs, ...children) => ({ tag, attrs, children: children.flat(Infinity) });
const walk = nodes => nodes.flatMap(n => typeof n === 'object' ? [n, ...walk(n.children)] : [n]);
test('Markdown renders headings, tables, code and lists while HTML and unsafe URLs stay inert', () => {
  const nodes = walk(markdownNodes(h, '# Title\n\n**bold**\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- item\n\n```js\nalert(1)\n```\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1)) ![image](https://example.com/a.png)'));
  for (const tag of ['h1','strong','table','ul','pre','code']) assert.ok(nodes.some(n => n?.tag === tag), tag);
  assert.ok(!nodes.some(n => ['script','img','iframe'].includes(n?.tag)));
  assert.ok(!nodes.some(n => n?.attrs?.href?.startsWith('javascript:')));
  assert.ok(nodes.some(n => typeof n === 'string' && n.includes('<script>')));
});
test('frontmatter preserves raw text, parses multiline fields and rejects YAML aliases/tags safely', () => {
  const source = '---\r\nname: demo\r\ndescription: |\r\n  first\r\n  second\r\nlabels: [one, two]\r\n---\r\n# Body';
  const parsed = splitFrontmatter(source);
  assert.equal(parsed.body, '# Body');
  assert.equal(parsed.fields.description, 'first\nsecond\n');
  assert.deepEqual(parsed.fields.labels, ['one','two']);
  for (const yaml of ['a: &a [1]\nb: *a', 'a: !!js/function hi', '[not, object]']) {
    const raw = `---\n${yaml}\n---\nbody`;
    assert.equal(splitFrontmatter(raw).body, raw);
    assert.ok(splitFrontmatter(raw).error);
  }
});
test('read-only diff distinguishes insertions/deletions and bounds expensive comparisons', () => {
  const result = lineDiff('same\nold\n', 'same\nnew\n');
  assert.ok(result.parts.some(p => p.removed && p.value === 'old\n'));
  assert.ok(result.parts.some(p => p.added && p.value === 'new\n'));
  assert.ok(lineDiff('a'.repeat(200001), '').error);
});
test('tree/file requests preserve source metadata and distinct cache keys', async () => {
  const urls = [];
  const fetch = async url => { urls.push(url); return { status: 200, json: async () => ({ state:'ok', files:[], source:'local', collected_at:'now', truncated:true }) }; };
  const tree = await loadSkillContent(fetch, 'demo', '/source', '@tree');
  assert.ok(urls[0].endsWith('&tree=1'));
  assert.equal(tree.collected_at, 'now');
  assert.equal(tree.truncated, true);
  await loadSkillContent(fetch, 'demo', '/source', 'references/a b.md');
  assert.ok(urls[1].endsWith('&file=references%2Fa%20b.md'));
  assert.notEqual(contentKey('demo','/source'), contentKey('demo','/source','references/a b.md'));
  for (const state of ['unsupported','uncollected']) assert.equal(classifyContentResponse({ status:200, body:{state, source:'cloud'} }).state, state);
});
