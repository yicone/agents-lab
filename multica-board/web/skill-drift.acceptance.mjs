/** Called by skill-drift.acceptance.py against its isolated GET-only fixture. */
export async function run(page, origin, outputDir) {
  const results = [];
  const check = (name, pass) => {
    results.push({ name, pass: !!pass });
    if (!pass) throw Error(`FAIL: ${name}`);
  };
  for (const width of [1440, 390]) for (const theme of ["light", "dark"]) {
    await page.cdp("Emulation.setDeviceMetricsOverride", { width, height: 1000, deviceScaleFactor: 1, mobile: false });
    await page.cdp("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }] });
    await page.goto("about:blank");
    await page.goto(`${origin}/#/configure/skills`);
    await page.waitForSelector(".skill-row");
    await page.click("#skill-digest-mismatch");
    await page.waitForSelector(".skill-drift-suggestions");
    check(`${width}/${theme}: mismatch suggests GET and never fabricates IDs`, await page.evaluate(() => {
      const text = document.querySelector(".skill-drift-suggestions").textContent;
      return text.includes("skills-drift-digest.py") && text.includes("skill get 22222222-2222-4222-8222-222222222222") && !text.includes("undefined");
    }));
    await page.click("loc=css:.skill-drift-suggestions button >> nth=0");
    await page.waitForFunction(() => document.querySelector(".skill-drift-suggestions button").textContent === "已复制");
    check(`${width}/${theme}: copy succeeds`, true);
    check(`${width}/${theme}: commands fit drawer`, await page.evaluate(() => {
      const region = document.querySelector(".skill-drift-suggestions");
      return region.scrollWidth <= region.clientWidth && [...region.querySelectorAll("code")].every(node => node.scrollWidth <= node.clientWidth);
    }));
    await page.screenshot({ path: `${outputDir}/skills-drift-${width}-${theme}.png` });
  }
  // Reset the viewport and list scroll before checking the two empty branches.
  await page.cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await page.goto("about:blank");
  await page.goto(`${origin}/#/configure/skills`);
  await page.waitForSelector(".skill-row");
  for (const [name, note] of [["digest-aligned", "无需命令"], ["digest-unknown", "信息不足"]]) {
    await page.click(`#skill-${name}`);
    await page.waitForSelector(".skill-drift-suggestions");
    check(`${name}: explicit no-command branch`, await page.evaluate(expected => {
      const region = document.querySelector(".skill-drift-suggestions");
      return region.textContent.includes(expected) && region.querySelectorAll("button").length === 0;
    }, note));
    await page.keyboard.press("Escape");
    check(`${name}: drawer closes`, await page.evaluate(() => document.querySelector("#drawer").hidden));
  }
  console.log(JSON.stringify(results, null, 2));
}
