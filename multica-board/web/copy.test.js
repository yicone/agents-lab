import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { COPY, visibleLabel } from "./ia-view.js";

const PAGES = ["skills.js", "mcp.js", "agents-view.js", "runtime-config.js", "evidence.js", "skill-content.js", "plan.js", "ia-view.js"];

function literals(source) {
  return [...source.matchAll(/`(?:\\.|[^`])*`|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g)].map((match) => match[0].slice(1, -1));
}

test("配置页和能力页不用跑法，也不裸用 profile", () => {
  for (const file of [...PAGES, "app.js"]) {
    const source = readFileSync(new URL(`./${file}`, import.meta.url), "utf8");
    assert.equal(source.includes("跑法"), false, file);
    for (const text of literals(source)) {
      const bare = text.replaceAll("--profile", "");
      assert.doesNotMatch(bare, /profile/i, `${file}: ${text}`);
    }
  }
});

test("能力页、Agent 配置抽屉和运行配置的界面词不含产品名", () => {
  for (const file of PAGES) {
    if (file === "ia-view.js") continue;
    const source = readFileSync(new URL(`./${file}`, import.meta.url), "utf8");
    for (const text of literals(source)) {
      assert.equal(text.includes("Multica"), false, `${file}: ${text}`);
    }
  }
});

test("三个术语和来源名展示固定", () => {
  assert.deepEqual([COPY.agent, COPY.runtime, COPY.capability], ["Agent 配置", "运行配置", "能力"]);
  assert.equal(visibleLabel("Multica Workspace · 已安装"), "Workspace · 已安装");
  assert.equal(visibleLabel("仅 Multica"), "仅 Workspace");
  assert.equal(visibleLabel("本机 + Multica"), "本机 + Workspace");
  assert.equal(visibleLabel("Grok Bots"), "Grok Bots");
});
