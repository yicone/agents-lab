import test from "node:test";
import assert from "node:assert/strict";
import { actionValue, collectChanges, canConfirm, createRuntimeConfig, availabilityText } from "./runtime-config.js";
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

// Minimal DOM harness exercises the actual editor and confirmation render paths.
function h(tag, attrs, ...children) {
  return {
    tag, attrs: attrs || {}, children: children.flat().filter(x => x != null),
    replaceChildren(...nodes) { this.children = nodes; },
    querySelector() { return null; },
  };
}
function nodes(root) {
  return [root, ...(root.children || []).flatMap(x => typeof x === "object" ? nodes(x) : [])];
}
function text(root) {
  return (root.children || []).map(x => typeof x === "object" ? text(x) : x).join(" ");
}
const warning = "未校验当前账号的模型可用性；保存成功不代表能运行，下一次任务可能失败，请先核对该 runtime 和账号支持的模型";
test("three availability states and the extra checkbox fail closed", () => {
  for (const [status, label] of [["available", "可用"], ["unavailable", "不可用"], ["unknown", "未知"]]) {
    assert.ok(availabilityText({ status }).includes(`模型可用性：${label}`));
  }
  assert.ok(availabilityText(null).includes("未知"));
  assert.ok(availabilityText({ status: "unexpected" }).includes("未知"));
  const p = { expires_at: 100, availability: { requires_extra_confirmation: true } };
  assert.equal(canConfirm(p, true, false, 1), false);
  assert.equal(canConfirm(p, true, false, 1, true), true);
  assert.equal(canConfirm(p, false, false, 1, true), false);
  assert.equal(canConfirm(p, true, false, 100000, true), false);
});

test("unavailable preview sends an acknowledgement bound to its id only after both checks", async () => {
  const target = { workspace_id: "ws", agent_id: "agent" };
  const calls = [];
  let seq = 0;
  const view = createRuntimeConfig(h, () => ({ agents: [], runtimes: [], workspaces: [] }), async (url, options) => {
    const body = JSON.parse(options.body); calls.push({ url, body });
    return { ok: true, json: async () => url.endsWith("catalog") ? { targets: [target] }
      : url.endsWith("preview") ? { id: `preview-${++seq}`, confirmation: "fixture-confirmation",
        state: "awaiting_confirmation", target: ["ws", "agent"], changes: body.changes,
        before: {}, after: body.changes, expires_at: Date.now() / 1000 + 60,
        availability: { status: "unavailable", requires_extra_confirmation: true } }
      : { state: "applied" } };
  });
  const find = predicate => nodes(view.element).find(predicate);
  const button = label => find(n => n.tag === "button" && text(n) === label);
  const settle = () => new Promise(resolve => setImmediate(resolve));
  const checkboxes = () => nodes(find(n => n.attrs.role === "dialog")).filter(n => n.tag === "input");
  try {
    find(n => n.tag === "form").attrs.onsubmit({ preventDefault() {} }); await settle();
    button("调整配置").attrs.onclick();
    find(n => n.attrs["aria-label"] === "模型 操作").attrs.onchange({ target: { value: "set" } });
    find(n => n.attrs["aria-label"] === "模型 目标").attrs.oninput({ target: { value: "denied-model" } });
    button("预览差异").attrs.onclick({}); await settle();
    assert.ok(text(view.element).includes("模型可用性：不可用"));
    const [extra, normal] = checkboxes();
    normal.attrs.onchange({ target: { checked: true } });
    assert.equal(button("确认写入").disabled, true);
    extra.attrs.onchange({ target: { checked: true } });
    assert.equal(button("确认写入").disabled, false);
    button("取消").attrs.onclick();
    button("预览差异").attrs.onclick({}); await settle();
    assert.equal(checkboxes()[0].attrs.checked, false);
    assert.equal(button("确认写入").attrs.disabled, true);
    checkboxes().forEach(n => n.attrs.onchange({ target: { checked: true } }));
    button("确认写入").attrs.onclick(); await settle();
    assert.deepEqual(calls.at(-1).body, { id: "preview-2", confirmation: "fixture-confirmation", availability_confirmation: "preview-2" });
  } finally { view.dispose(); }
});
for (const field of ["model", "runtime_id", "custom_model"]) {
  test(`availability warning is visible before preview and confirmation for ${field}`, async () => {
    const target = { workspace_id: "ws", agent_id: "agent" };
    const calls = [];
    const view = createRuntimeConfig(h, () => ({
      agents: [{ id: "agent", workspace_id: "ws", name: "Builder" }],
      runtimes: [{ id: "rt", workspace_id: "ws", name: "Runtime", status: "online" }],
      workspaces: [{ id: "ws", name: "Workspace" }],
    }), async (url, options) => {
      calls.push(url);
      const changes = JSON.parse(options.body).changes;
      return { ok: true, json: async () => url.endsWith("catalog") ? { targets: [target] } : {
        state: "awaiting_confirmation", target: ["ws", "agent"], changes,
        before: { [field]: null }, after: changes, expires_at: Date.now() / 1000 + 60,
      } };
    });
    const find = predicate => nodes(view.element).find(predicate);
    const settle = () => new Promise(resolve => setImmediate(resolve));
    try {
      find(n => n.tag === "form").attrs.onsubmit({ preventDefault() {} });
      await settle();
      find(n => n.tag === "button" && text(n) === "调整配置").attrs.onclick();
      const label = field === "model" ? "模型" : field === "runtime_id" ? "Runtime" : "custom_args · --model";
      find(n => n.attrs["aria-label"] === label + " 操作").attrs.onchange({ target: { value: "set" } });
      const input = find(n => n.attrs["aria-label"] === label + " 目标");
      (input.attrs.oninput || input.attrs.onchange)({ target: { value: field === "runtime_id" ? "rt" : "next-model" } });
      assert.ok(text(view.element).includes(warning));
      const preview = find(n => n.tag === "button" && text(n) === "预览差异");
      preview.attrs.onclick({ currentTarget: preview });
      await settle();
      const dialog = find(n => n.attrs.role === "dialog");
      assert.ok(dialog);
      assert.ok(text(dialog).includes(warning));
      assert.ok(text(dialog).includes("Runtime 在线仅表示运行时在线，不代表模型可用"));
      assert.ok(text(dialog).includes("提交前检查发现配置变化或活动任务时会阻止"));
      assert.equal(nodes(dialog).find(n => n.tag === "button" && text(n) === "确认写入").attrs.disabled, true);
      assert.deepEqual(calls, ["/api/runtime-actions/catalog", "/api/runtime-actions/preview"]);
    } finally { view.dispose(); }
  });
}
