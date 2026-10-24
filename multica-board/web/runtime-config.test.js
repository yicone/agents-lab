import test from "node:test";
import assert from "node:assert/strict";
import { actionValue, collectChanges, canConfirm } from "./runtime-config.js";
import { parseHash, formatHash, redirectHash } from "./route.js";

test("runtime route is isolated from topbar and survives deep linking", () => {
  assert.equal(redirectHash("#/configure/runtime"), null);
  assert.equal(parseHash("#/configure/runtime").view, "configure");
  assert.equal(parseHash("#/configure/runtime").section, "runtime");
  assert.equal(formatHash(parseHash("#/configure/runtime")), "#/configure/runtime");
});
test("omit, set and clear are explicit and reject arbitrary fields", () => {
  assert.deepEqual(collectChanges({ model: { mode: "set", value: "next" }, thinking_level: { mode: "clear" }, custom_env: { mode: "set", value: "secret" } }), { model: "next", thinking_level: null });
  assert.notEqual(actionValue(null), actionValue(""));
});
test("confirmation requires live preview, checkbox, and idle request", () => {
  const p = { expires_at: 100 };
  assert.equal(canConfirm(p, true, false, 99999), true);
  assert.equal(canConfirm(p, true, false, 100000), false);
  assert.equal(canConfirm(p, false, false, 1), false);
  assert.equal(canConfirm(p, true, true, 1), false);
});
