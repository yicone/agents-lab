/** Invoke through ego-browser nodejs with an owned Page and a local static web server. */
export async function mobileSmoke(page, { baseUrl, artifactDir }) {
  const assert = (await import("node:assert/strict")).default;
  const results = [];
  await page.cdp("Network.enable", {});
  await page.cdp("Network.setCacheDisabled", { cacheDisabled: true });
  await page.goto(`${baseUrl}/?mock#/issues`);
  await page.reload();
  await page.waitForLoadState();
  for (const width of [390, 1440]) {
    await page.cdp("Emulation.setDeviceMetricsOverride", { width, height: width === 390 ? 844 : 1000, deviceScaleFactor: 1, mobile: width === 390 });
    for (const [name, route, selector] of [["issues", "issues", ".card"], ["agents", "running", ".provider"], ["config-agents", "configure/agents", ".matrix"]]) {
      await page.goto(`${baseUrl}/?mock#/${route}`);
      await page.waitForLoadState();
      await page.waitForSelector(selector);
      await page.waitForFunction(expected => document.querySelector("main h2")?.textContent === expected, name === "issues" ? "全部事项" : name === "agents" ? "Agent 状态" : "Agent 配置");
      const metrics = await page.evaluate(() => {
        const scroller = document.querySelector(".table-wrap");
        return { viewport: innerWidth, scroll: document.documentElement.scrollWidth,
          widths: [...document.querySelectorAll(".provider")].map(e => e.getBoundingClientRect().width),
          table: scroller && { width: scroller.clientWidth, scroll: scroller.scrollWidth, overflow: getComputedStyle(scroller).overflowX, tabIndex: scroller.tabIndex } };
      });
      assert.equal(metrics.viewport, width);
      assert.ok(metrics.scroll <= width, `${name} overflows at ${width}`);
      if (metrics.widths.length) assert.ok(Math.max(...metrics.widths) - Math.min(...metrics.widths) < 1);
      if (name !== "issues") {
        assert.equal(metrics.table.overflow, "auto");
        assert.equal(metrics.table.tabIndex, 0);
        await page.focus(".table-wrap");
        await page.press(".table-wrap", "End");
        if (width === 390) {
          await page.evaluate(() => { const el = document.querySelector(".table-wrap"); el.scrollLeft = el.scrollWidth; });
          assert.ok(await page.evaluate(() => document.querySelector(".table-wrap").scrollLeft > 0));
        }
      }
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: `${artifactDir}/after-${name}-${width}.png`, fullPage: true });
      results.push({ name, width, ...metrics });
    }
  }
  for (const [name, enabled, required] of [["readonly", false, false], ["token-off", true, false], ["token-on", true, true]]) {
    await page.cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    await page.goto(`${baseUrl}/?mock#/configure/runtime`);
    await page.evaluate(async ({ enabled, required }) => {
      const { createRuntimeConfig } = await import("./runtime-config.js");
      const calls = [];
      function h(tag, attrs, ...children) {
        const el = document.createElement(tag);
        for (const [key, value] of Object.entries(attrs || {})) {
          if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
          else if (["disabled", "checked", "selected", "value"].includes(key)) el[key] = value;
          else el.setAttribute(key, value);
        }
        el.append(...children.flat(Infinity).filter(x => x != null));
        return el;
      }
      const view = createRuntimeConfig(h, () => ({ agents: [], runtimes: [], workspaces: [] }), async url => {
        calls.push(url);
        return { ok: true, json: async () => url === "/api/runtime-actions" ? { enabled } : url === "/api/features" ? { write_token_required: required } : { targets: [] } };
      });
      document.querySelector("main").replaceChildren(view.element);
      window.smokeCalls = calls;
    }, { enabled, required });
    await page.waitForFunction(() => !document.querySelector(".runtime-config [role=status]")?.textContent.includes("正在"));
    const state = await page.evaluate(() => ({ password: !!document.querySelector(".runtime-config input[type=password]"), text: document.querySelector(".runtime-config").textContent, status: document.querySelector(".runtime-config [role=status]")?.textContent, calls: window.smokeCalls }));
    assert.equal(state.password, enabled && required);
    assert.equal(state.calls.includes("/api/runtime-actions/catalog"), enabled && !required);
    if (!enabled) assert.equal(state.status, "本实例只读。");
    await page.screenshot({ path: `${artifactDir}/after-runtime-${name}-390.png` });
    results.push({ name, ...state });
  }
  return results;
}
