import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
const html = readFileSync(new URL("./index.html", import.meta.url), "utf8");

test("narrow top tabs wrap inside the viewport instead of hiding Skills", () => {
  const start = css.indexOf("@media (max-width: 760px)");
  assert.notEqual(start, -1);
  const slice = css.slice(start, start + 500);
  assert.match(slice, /\.topbar-row\s*\{[^}]*flex-wrap:\s*wrap/);
  assert.match(slice, /\.tabs\s*\{[^}]*flex:\s*1 0 100%/);
  assert.match(slice, /\.tabs\s*\{[^}]*flex-wrap:\s*wrap/);
  assert.match(slice, /\.tabs\s*\{[^}]*max-width:\s*100%/);
  assert.match(css, /\.tab\s*\{[^}]*white-space:\s*nowrap/);
});

test("top tabs stay keyboard links and show a focus ring", () => {
  for (const view of ["map", "decide", "issues", "agents", "skills", "mcp"]) {
    assert.match(html, new RegExp(`<a class="tab" data-view="${view}" href="#/${view}">`));
  }
  assert.match(css, /\.tab:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--accent\)/);
});
