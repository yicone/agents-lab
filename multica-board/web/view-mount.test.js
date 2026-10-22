import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { captureScroll, placeChildren, restoreScroll } from "./view-mount.js";

test("captureScroll and restoreScroll round-trip the skills list scroller", () => {
  const scroller = { scrollTop: 240, scrollLeft: 36 };
  const root = { querySelectorAll: () => [scroller] };
  const saved = captureScroll(root, ".table-wrap.sticky-head");
  scroller.scrollTop = 0;
  scroller.scrollLeft = 0;
  restoreScroll(root, saved, ".table-wrap.sticky-head");
  assert.equal(scroller.scrollTop, 240);
  assert.equal(scroller.scrollLeft, 36);
});

test("restoreScroll ignores a missing scroller and an empty capture", () => {
  const scroller = { scrollTop: 5, scrollLeft: 0 };
  const root = { querySelectorAll: () => [] };
  restoreScroll(root, [{ index: 0, top: 80, left: 4 }], ".table-wrap.sticky-head");
  restoreScroll({ querySelectorAll: () => [scroller] }, [], ".table-wrap.sticky-head");
  assert.equal(scroller.scrollTop, 5);
});

test("placeChildren still drops null slots", () => {
  const parent = { childNodes: null, replaceChildren(...nodes) { this.childNodes = nodes; } };
  placeChildren(parent, ["a", null, false, "b"]);
  assert.deepEqual(parent.childNodes, ["a", "b"]);
});

test("skills refresh keeps the list scroller when the same view renders again", () => {
  const app = readFileSync(new URL("./app.js", import.meta.url), "utf8");
  const body = app.slice(app.indexOf("function renderMain"), app.indexOf("function render("));
  assert.match(body, /captureScroll\(main, SKILLS_LIST_SCROLL\)/);
  assert.match(body, /restoreScroll\(main, listScroll, SKILLS_LIST_SCROLL\)/);
  assert.match(body, /renderedView === view/);
  assert.match(app, /SKILLS_LIST_SCROLL = "\.table-wrap\.sticky-head"/);
});
