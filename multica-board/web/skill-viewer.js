/** Read-only rendering. Dependencies are vendored: markdown-it 14.1.0,
 * js-yaml 4.1.1, diff 8.0.2 (MIT/BSD licenses alongside bundle).
 * Rebuild: esbuild an entry exporting MarkdownIt, loadYaml, JSON_SCHEMA,
 * diffLines from those packages with --bundle --format=esm --minify.
 */
import { MarkdownIt, loadYaml, JSON_SCHEMA, diffLines } from './vendor/skill-viewer.js';
const md = new MarkdownIt({ html: false, linkify: false, typographer: false });
export function splitFrontmatter(raw) {
  const match = /^(?:\uFEFF)?---\r?\n([\s\S]*?)\r?\n(?:---|\.\.\.)(?:\r?\n|$)/.exec(raw);
  if (!match) return { fields: {}, body: raw };
  try {
    if (match[1].length > 16384 || /(^|\s)[&*][\w-]+/.test(match[1])) throw Error('字段过大或包含 YAML 引用');
    const fields = loadYaml(match[1], { schema: JSON_SCHEMA });
    if (!fields || typeof fields !== 'object' || Array.isArray(fields)) throw Error('frontmatter 必须是字段对象');
    return { fields, body: raw.slice(match[0].length) };
  } catch (err) { return { fields: {}, body: raw, error: `frontmatter 无法解析：${err.message}` }; }
}

// Build DOM through the app's text-safe h(), never insert source HTML.
export function markdownNodes(h, raw) {
  function nodes(tokens) {
    const root = { children: [] }, stack = [root];
    const push = node => stack.at(-1).children.push(node);
    for (const t of tokens) {
      if (t.nesting === 1) { const n = { tag: t.tag, attrs: {}, children: [] };
        if (t.type === 'link_open') n.attrs = { href: t.attrGet('href'), title: t.attrGet('title'), target: '_blank', rel: 'noopener noreferrer' };
        if (t.type === 'ordered_list_open') n.attrs.start = t.attrGet('start');
        push(n); stack.push(n);
      } else if (t.nesting === -1) stack.pop();
      else if (t.type === 'inline') push(nodes(t.children || []));
      else if (t.type === 'fence' || t.type === 'code_block') push(h('pre', null, h('code', null, t.content)));
      else if (t.type === 'code_inline') push(h('code', null, t.content));
      else if (t.type === 'softbreak') push('\n');
      else if (t.type === 'hardbreak' || t.type === 'hr') push(h(t.tag, null));
      else if (t.type === 'image') push(`[图片：${t.content}]`);
      else push(t.content || '');
    }
    const materialize = n => Array.isArray(n) ? n.map(materialize) : n && typeof n === 'object' && n.tag && n.children ? h(n.tag, n.attrs, ...n.children.map(materialize)) : n;
    return root.children.map(materialize).flat(Infinity);
  }
  return nodes(md.parse(raw, {}));
}
export function lineDiff(before, after) {
  // A bounded diff keeps a pair of large, unrelated files from blocking the drawer.
  if (before.length + after.length > 200000) return { error: '对比内容超过 200 KB，请分别查看原文' };
  const parts = diffLines(before, after, { timeout: 150, maxEditLength: 2000 });
  return parts ? { parts } : { error: '差异过多，请分别查看原文' };
}
