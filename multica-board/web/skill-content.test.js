import assert from "node:assert/strict";
import test from "node:test";
import { classifyContentResponse, contentKey, loadSkillContent } from "./skill-content.js";

test("classifyContentResponse: missing endpoint is unavailable, not an error", () => {
  const notFound = { error: { code: "not_found", message: "unknown endpoint" } };
  assert.equal(classifyContentResponse({ status: 404, body: notFound }).state, "unavailable");
  assert.equal(classifyContentResponse({ status: 405, body: null }).state, "unavailable");
  assert.equal(classifyContentResponse({ status: 404, body: { error: { code: "not_found", message: "no such skill" } } }).state, "error");
  assert.equal(classifyContentResponse({ status: 404, body: { error: { code: "other" } } }).state, "error");
});

test("classifyContentResponse: non-2xx, network, and malformed bodies are error, never empty", () => {
  assert.equal(classifyContentResponse({ status: 500, body: {} }).state, "error");
  assert.equal(classifyContentResponse({ status: 200, body: null }).state, "error");
  assert.equal(classifyContentResponse({ status: 200, body: "oops" }).state, "error");
  const net = classifyContentResponse({ networkError: "socket hangup" });
  assert.equal(net.state, "error");
  assert.match(net.message, /socket hangup/);
  assert.equal(classifyContentResponse({ status: 200, body: { state: "bogus" } }).state, "error");
});

test("classifyContentResponse: ok requires a string content; empty string is empty", () => {
  assert.equal(classifyContentResponse({ status: 200, body: { state: "ok", content: 42 } }).state, "error");
  assert.equal(classifyContentResponse({ status: 200, body: { state: "ok" } }).state, "error");
  assert.equal(classifyContentResponse({ status: 200, body: { state: "ok", content: "" } }).state, "empty");
  const ok = classifyContentResponse({
    status: 200,
    body: { ok: true, state: "ok", name: "a", path: "/p/SKILL.md", format: "markdown", bytes: 12, sha256: "abcdef0123456789", content: "# hi" },
  });
  assert.equal(ok.state, "ok");
  assert.equal(ok.content, "# hi");
  assert.equal(ok.bytes, 12);
  assert.equal(ok.sha256, "abcdef0123456789");
  assert.equal(classifyContentResponse({ status: 200, body: { state: "empty" } }).state, "empty");
});

test("classifyContentResponse: backend failure states map through with their message", () => {
  for (const state of ["missing", "denied", "too_large", "error"]) {
    const res = classifyContentResponse({ status: 200, body: { ok: false, state, message: `m-${state}` } });
    assert.equal(res.state, state);
    assert.equal(res.message, `m-${state}`);
  }
});

test("loadSkillContent encodes name and path and classifies the response", async () => {
  const seen = [];
  const stub = async (url, init) => {
    seen.push([url, init]);
    return { status: 200, json: async () => ({ state: "ok", content: "body" }) };
  };
  const res = await loadSkillContent(stub, "my skill", "/tmp/a b/SKILL #1&.md");
  assert.equal(res.state, "ok");
  assert.equal(seen.length, 1);
  const [url, init] = seen[0];
  assert.equal(url, `/api/skills/content?name=${encodeURIComponent("my skill")}&path=${encodeURIComponent("/tmp/a b/SKILL #1&.md")}`);
  assert.ok(!url.includes("#"), url);
  assert.equal(init.cache, "no-store");
  assert.equal(init.headers.Accept, "application/json");
});

test("loadSkillContent turns fetch exceptions into error results", async () => {
  const res = await loadSkillContent(async () => {
    throw new Error("connection refused");
  }, "a", "/p");
  assert.equal(res.state, "error");
  assert.match(res.message, /connection refused/);
});

test("contentKey joins name and path", () => {
  assert.equal(contentKey("alpha", "/x/SKILL.md"), "alpha\n/x/SKILL.md");
});
