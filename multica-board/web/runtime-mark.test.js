import assert from "node:assert/strict";
import test from "node:test";
import { runtimeMark, runtimeIcon, runtimeOption } from "./runtime-mark.js";
const h = (tag, attrs, ...children) => ({ tag, attrs, children });
test("providers share stable identity marks while new providers retain their names", () => {
  for (const provider of ["claude", "codex", "cursor", "hermes", "antigravity", "gemini", "grok"]) {
    const icon = runtimeIcon(h, provider);
    assert.equal(icon.attrs.class, "runtime-mark");
    assert.equal(icon.attrs.title, provider);
    assert.ok(icon.children[0]);
    assert.ok(runtimeOption(provider, "Same name").startsWith(`[${runtimeMark(provider).glyph}] Same name`));
  }
  assert.deepEqual(runtimeMark("New provider <script>"), { glyph: "R", label: "New provider <script>" });
  assert.equal(runtimeMark(" CODEX ").glyph, "Cx");
});
test("unbound and missing runtime identities remain text states", () => {
  for (const provider of [undefined, null, "", "未绑定", "未知", "未知 runtime"]) assert.equal(runtimeIcon(h, provider), null);
  assert.equal(runtimeOption(null, "Target"), "Target · 未知");
});
