import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import test from "node:test";
import { COPY, visibleLabel } from "./ia-view.js";

const PAGES = ["skills.js", "mcp.js", "agents-view.js", "runtime-config.js", "evidence.js", "skill-content.js", "plan.js", "ia-view.js"];
const UI_FILES = readdirSync(new URL("./", import.meta.url))
  .filter((name) => (name.endsWith(".js") || name.endsWith(".html") || name.endsWith(".css")) && !name.endsWith(".test.js") && name !== "ia-view.js");

function literals(source) {
  return [...source.matchAll(/`(?:\\.|[^`])*`|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g)].map((match) => match[0].slice(1, -1));
}

test("配置页和能力页不用跑法，也不裸用 profile", () => {
  for (const file of [...PAGES, "app.js"]) {
    const source = readFileSync(new URL(`./${file}`, import.meta.url), "utf8");
    assert.equal(source.includes("跑法"), false, file);
    for (const text of literals(source)) {
      if (text === "共享 Profile") continue;
      const bare = text.replaceAll("--profile", "");
      assert.doesNotMatch(bare, /profile/i, `${file}: ${text}`);
    }
  }
});

test("界面词不含产品名，命令和对象名称不在这层替换", () => {
  for (const file of UI_FILES) {
    const source = readFileSync(new URL(`./${file}`, import.meta.url), "utf8");
    assert.equal(source.includes("Multica"), false, file);
  }
});

test("三个术语和来源名展示固定", () => {
  assert.deepEqual([COPY.agent, COPY.runtime, COPY.capability], ["Agent 配置", "运行配置", "能力"]);
  assert.equal(visibleLabel("Multica Workspace · 已安装"), "Workspace · 已安装");
  assert.equal(visibleLabel("仅 Multica"), "仅 Workspace");
  assert.equal(visibleLabel("本机 + Multica"), "本机 + Workspace");
  assert.equal(visibleLabel("Grok Bots"), "Grok Bots");
});
