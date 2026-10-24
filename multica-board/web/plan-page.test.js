import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { extname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const web = fileURLToPath(new URL("./", import.meta.url));
const mime = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".json": "application/json",
  ".png": "image/png",
};

function chromeBin() {
  return process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

test("the mock plan list route mounts no null text node in the real document", async () => {
  const server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, "http://127.0.0.1");
      const name = url.pathname === "/" ? "index.html" : url.pathname.replace(/^\/+/, "");
      const body = await readFile(join(web, name));
      res.writeHead(200, { "content-type": mime[extname(name)] || "application/octet-stream" });
      res.end(body);
    } catch {
      res.writeHead(404);
      res.end();
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const profile = await mkdtemp(join(tmpdir(), "acm286-plan-page-"));
  const chrome = spawn(chromeBin(), [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    `--user-data-dir=${profile}`,
    "--remote-debugging-port=0",
    "about:blank",
  ], { stdio: ["ignore", "pipe", "pipe"] });
  let debugLog = "";
  const collect = (chunk) => {
    debugLog += chunk;
  };
  chrome.stdout.on("data", collect);
  chrome.stderr.on("data", collect);
  try {
    let debugPort = 0;
    for (let i = 0; i < 50 && !debugPort; i += 1) {
      debugPort = Number(debugLog.match(/127\.0\.0\.1:(\d+)\//)?.[1] || 0);
      if (!debugPort) await sleep(100);
    }
    assert.ok(debugPort, "Chrome did not open a debugging port");
    let socketUrl = "";
    for (let i = 0; i < 50 && !socketUrl; i += 1) {
      try {
        const listed = await fetch(`http://127.0.0.1:${debugPort}/json/list`).then((res) => res.json());
        socketUrl = listed.find((item) => item.type === "page")?.webSocketDebuggerUrl || "";
      } catch {
        await sleep(100);
      }
    }
    assert.ok(socketUrl, "Chrome did not open a page target");
    const ws = new WebSocket(socketUrl);
    await new Promise((resolve, reject) => {
      ws.addEventListener("open", resolve);
      ws.addEventListener("error", reject);
    });
    let nextId = 0;
    const pending = new Map();
    const send = (call, params = {}) => {
      const id = ++nextId;
      ws.send(JSON.stringify({ id, method: call, params }));
      return new Promise((resolve) => pending.set(id, resolve));
    };
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) {
        pending.get(msg.id)(msg.result);
        pending.delete(msg.id);
      }
    });
    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: `http://127.0.0.1:${port}/index.html?mock#/agents/plans` });
    let view = null;
    for (let i = 0; i < 40; i += 1) {
      const result = await send("Runtime.evaluate", {
        expression: `(() => {
          const main = document.getElementById("main");
          if (!main) return null;
          const texts = [];
          const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
          while (walker.nextNode()) texts.push(walker.currentNode.data);
          const direct = [...main.childNodes].filter((node) => node.nodeType === 3).map((node) => node.data);
          return { hash: location.hash, texts, direct };
        })()`,
        returnByValue: true,
      });
      view = result.result?.value || null;
      if (view?.texts?.some((text) => text.includes("计划目录")) && view.texts.some((text) => text.includes("生成"))) break;
      await sleep(150);
      view = null;
    }
    ws.close();
    assert.ok(view, "mock plan list did not render");
    assert.equal(view.hash, "#/running/changes");
    assert.equal(view.direct.some((text) => text === "null"), false);
    assert.equal(view.texts.some((text) => text === "null"), false);
    assert.ok(view.texts.some((text) => text.includes("计划目录 · 最近一份")));
  } finally {
    chrome.kill();
    await new Promise((resolve) => server.close(resolve));
  }
});
