// Run against the local mock server: ego-browser nodejs < multica-board/scripts/naming-smoke.mjs
const task = await taskSpace("Atlas navigation naming correction smoke");
const page = task.page("p1");
const { strict: assert } = await import("node:assert");
const base = "http://127.0.0.1:8875/";
for (const [scenario, expected] of [["decide", "7"], ["atlas-quiet", ""], ["decide-down", "7"], ["decide-gap", "7"], ["1", "?"]]) {
  await page.goto(`${base}?mock=${scenario}#/decide`);
  await page.waitForFunction(s => document.querySelector(".mock")?.textContent === (s === "1" ? "mock" : `mock:${s}`) && document.querySelector("[data-decide-total]"), scenario);
  const result = await page.evaluate(() => ({
    badge: document.querySelector("#issues-badge").textContent,
    sub: document.querySelector("#decide-badge").textContent,
    total: document.querySelector("[data-decide-total]").dataset.decideTotal,
    hidden: document.querySelector("#issues-badge").hidden,
  }));
  assert.equal(result.badge, expected);
  assert.equal(result.sub, expected);
  if (expected === "7") assert.equal(result.badge, result.total);
  if (expected === "") assert.equal(result.hidden, true);
  console.log(scenario, result);
}
await page.goto(`${base}?mock=decide#/issues`);
await page.waitForFunction(() => document.querySelector("h2")?.textContent === "全部事项");
await page.click('#subnav a[data-path="#/decide"]');
await page.waitForFunction(() => document.querySelector("h2")?.textContent === "待批准");
console.log(await page.screenshot());
await page.goto(`${base}?mock=decide#/issues`);
await page.waitForFunction(() => document.querySelector("h2")?.textContent === "全部事项");
console.log(await page.screenshot());
await page.goto(`${base}?mock=decide#/running/health`);
await page.waitForFunction(() => document.querySelector("h2")?.textContent === "服务健康");
assert.match(await page.evaluate(() => document.body.textContent), /未接入/);
console.log(await page.screenshot());
await page.goto(`${base}?mock=atlas#/map`);
await page.waitForFunction(() => [...document.querySelectorAll(".map-tools a span")].some(n => n.textContent === "全部事项"));
console.log(await page.screenshot());
await page.goto(`${base}?mock=atlas#/map/l3`);
await page.waitForFunction(() => [...document.querySelectorAll("a")].some(n => n.textContent === "全部事项中本层的关注项"));
console.log(await page.screenshot());
await page.goto(`${base}?mock=atlas-unfilled#/map`);
await page.waitForFunction(() => [...document.querySelectorAll("a")].some(n => n.textContent === "先去全部事项看关注项"));
console.log(await page.screenshot());
await task.finish({ keep: [] });
console.log("Naming smoke passed: badge, zero, unknown, navigation, old bookmarks.");
