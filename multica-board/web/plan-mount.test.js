import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname } from "node:path";
import test from "node:test";
import { renderPlanList } from "./plan.js";
import { placeChildren } from "./view-mount.js";

const NOW = Date.parse("2026-10-19T03:00:00Z");
const WS = "22222222-2222-4222-8222-222222222222";

class DomNode {}

class TextNode extends DomNode {
  constructor(value) {
    super();
    this.nodeType = 3;
    this.data = String(value);
  }
}

class ElementNode extends DomNode {
  constructor(tag) {
    super();
    this.nodeType = 1;
    this.tag = tag;
    this.attrs = {};
    this.childNodes = [];
  }

  setAttribute(name, value) {
    this.attrs[name] = String(value);
  }

  addEventListener() {}

  append(child) {
    this.childNodes.push(child instanceof DomNode ? child : new TextNode(child));
  }

  /** ParentNode.replaceChildren turns a non-node, including null, into text. */
  replaceChildren(...nodes) {
    this.childNodes = nodes.map((node) => (node instanceof DomNode ? node : new TextNode(node)));
  }
}

function h(tag, attrs, ...children) {
  const el = new ElementNode(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false || key.startsWith("on")) continue;
      el.setAttribute(key, value === true ? "" : value);
    }
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    el.append(child instanceof DomNode ? child : new TextNode(child));
  }
  return el;
}

function textNodes(node, found = []) {
  if (node instanceof TextNode) found.push(node.data);
  for (const child of node.childNodes || []) textNodes(child, found);
  return found;
}

const ctx = {
  nowMs: NOW,
  ago: () => "刚刚",
  route: { model: "omit", effort: "omit", tier: "omit", rt: "", bucket: "", planFix: "", sel: "" },
  setRoute() {},
  copy() {},
  leave() {},
  openList() {},
  backToSelect() {},
  openPlan() {},
  toggleRaw() {},
  rawOpen: false,
  regenerate() {},
  openRow() {},
  closeDrawer() {},
  drawerAgentId: "",
  agentById: new Map(),
  snapshotAge: "刚刚",
};

function visiblePlan() {
  return {
    plan_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    generated_at: "2026-10-19T01:00:00Z",
    expires_at: "2026-10-19T04:00:00Z",
    expired: false,
    mode: "review_only",
    allow: [{ id: WS, name: "ACM Lab" }],
    counts: { APPROVABLE: 1, BLOCKED: 0, SKIP_ACTIVE: 0, NO_CHANGE: 0 },
    agent_count: 1,
    plan_hash: "abc123abc123abc123abc123abc123abc123abc123abc123abc123abc123abcd",
    hash_verified: true,
    parse_error: "",
    snapshot_kind: "live_cli",
    observed_at: "2026-10-19T01:00:00Z",
  };
}

test("a complete plan list does not mount the text null", () => {
  const raw = new ElementNode("main");
  raw.replaceChildren(null);
  assert.deepEqual(textNodes(raw), ["null"]);

  const parts = renderPlanList(h, { ...ctx, plans: [visiblePlan()], planComplete: true, directoryError: null, planError: "" });
  const main = new ElementNode("main");
  placeChildren(main, parts);
  const texts = textNodes(main);
  assert.equal(texts.some((text) => text === "null"), false);
  assert.match(texts.join(""), /review_only/);
  assert.match(texts.join(""), /可批准 1/);
});

function chromeBin() {
  return process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
}

function mime(path) {
  return { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".json": "application/json" }[extname(path)] || "application/octet-stream";
}

const HARNESS = `<!doctype html>
<meta charset="utf-8">
<main id="main"></main>
<script type="module">
import { renderPlanList } from "./plan.js";
import { placeChildren } from "./view-mount.js";
const NOW = Date.parse("2026-10-19T03:00:00Z");
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") el.className = value;
      else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value === true ? "" : value);
    }
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}
const plan = {
  plan_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  generated_at: "2026-10-19T01:00:00Z",
  expires_at: "2026-10-19T04:00:00Z",
  expired: false,
  mode: "review_only",
  allow: [{ id: "22222222-2222-4222-8222-222222222222", name: "ACM Lab" }],
  counts: { APPROVABLE: 1, BLOCKED: 0, SKIP_ACTIVE: 0, NO_CHANGE: 0 },
  agent_count: 1,
  plan_hash: "abc123abc123abc123abc123abc123abc123abc123abc123abc123abc123abcd",
  hash_verified: true,
  parse_error: "",
  snapshot_kind: "live_cli",
  observed_at: "2026-10-19T01:00:00Z",
};
const ctx = {
  nowMs: NOW,
  ago: () => "刚刚",
  route: { bucket: "" },
  leave() {},
  openList() {},
  openPlan() {},
};
const main = document.getElementById("main");
placeChildren(main, renderPlanList(h, { ...ctx, plans: [plan], planComplete: true, directoryError: null, planError: "" }));
const texts = [];
const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
while (walker.nextNode()) texts.push(walker.currentNode.data);
document.documentElement.dataset.ready = "1";
document.documentElement.dataset.nullText = texts.some((text) => text === "null") ? "1" : "0";
document.documentElement.dataset.sawPlan = texts.join("").includes("review_only") ? "1" : "0";
</script>`;

test("a real document mounts the plan list without a null text node", async () => {
  const server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, "http://127.0.0.1");
      if (url.pathname === "/harness.html") {
        res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        res.end(HARNESS);
        return;
      }
      const name = url.pathname === "/" ? "/index.html" : url.pathname;
      const body = await readFile(new URL(`.${name}`, import.meta.url));
      res.writeHead(200, { "content-type": mime(name) });
      res.end(body);
    } catch {
      res.writeHead(404);
      res.end();
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  try {
    const html = await new Promise((resolve, reject) => {
      const child = spawn(chromeBin(), [
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--virtual-time-budget=8000",
        "--dump-dom",
        `http://127.0.0.1:${port}/harness.html`,
      ], { stdio: ["ignore", "pipe", "pipe"] });
      let out = "";
      let err = "";
      child.stdout.on("data", (chunk) => { out += chunk; });
      child.stderr.on("data", (chunk) => { err += chunk; });
      child.on("error", reject);
      child.on("close", (code) => {
        if (!out.includes("data-ready")) reject(new Error(err || `chrome exited ${code}`));
        else resolve(out);
      });
    });
    const mainHtml = html.match(/<main id="main">([\s\S]*?)<\/main>/)?.[1] || "";
    assert.match(html, /data-ready="1"/);
    assert.match(html, /data-null-text="0"/);
    assert.match(html, /data-saw-plan="1"/);
    assert.doesNotMatch(mainHtml, />null</);
    assert.match(mainHtml, /review_only/);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
