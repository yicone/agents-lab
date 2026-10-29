import test from "node:test";
import assert from "node:assert/strict";
import { boundaryText, configFieldGroups, quotaText, safeConfigText } from "./agent-config.js";

test("runtime fields are not in the duty or boundary columns", () => {
  const groups = configFieldGroups();
  assert.deepEqual(groups.runtime, ["Agent", "Runtime", "模型", "推理", "Tier", "额度"]);
  assert.deepEqual(groups.duty, ["Agent", "Instructions 来源", "Skills 绑定", "MCP 绑定", "同一 Agent"]);
  assert.deepEqual(groups.boundary, ["Agent", "边界"]);
  for (const column of ["模型", "推理", "Tier", "额度", "Runtime"]) {
    assert.equal(groups.duty.includes(column), false);
    assert.equal(groups.boundary.includes(column), false);
  }
  for (const column of ["Instructions 来源", "Skills 绑定", "MCP 绑定", "边界"]) {
    assert.equal(groups.runtime.includes(column), false);
  }
});

test("quota and boundary text never surface secret fields", () => {
  assert.equal(quotaText({}), "未采集");
  assert.equal(quotaText({ quota: "周窗口 40%" }), "周窗口 40%");
  assert.equal(quotaText({ quota: "account_key=abc", source_path: "/tmp/secret", account_key: "k" }), "已隐藏");
  assert.equal(quotaText({ source_path: "/Users/me/.config", account_key: "k" }), "未采集");
  assert.equal(safeConfigText("sk-live-secret"), "已隐藏");
  assert.equal(boundaryText("职责：写代码\n边界：不改产品方向"), "不改产品方向");
  assert.equal(boundaryText("先看目录，再写调用关系。"), "未单列");
  assert.equal(boundaryText("边界：token=abc account_key=1"), "已隐藏");
  assert.equal(boundaryText("硬性边界：只读本仓库"), "只读本仓库");
});
