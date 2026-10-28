"""ACM-664 design checklist against the static mock. Does not call a live backend.

From multica-board/web:
  python3 capability-tri-state-acceptance.py /path/to/output
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent
PORT = 8794
BASE = f"http://127.0.0.1:{PORT}"


async def open_page(page, url: str, selector: str) -> None:
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_selector(selector, timeout=10000)


async def checks(page) -> dict[str, bool]:
    return await page.evaluate(
        """() => {
          const text = document.body.innerText;
          const lines = [...document.querySelectorAll(".tri-line")].map((node) => node.innerText.replace(/\\s+/g, " "));
          const observed = lines.filter((line) => line.startsWith("观察到"));
          const hooksBadge = document.querySelector('#subnav a[data-path="#/configure/hooks"] .tab-badge');
          return {
            hasDeclared: text.includes("声明了"),
            hasReachable: text.includes("可达"),
            hasObserved: text.includes("观察到"),
            hasUnknown: text.includes("未知"),
            legend: text.includes("不只靠颜色") && text.includes("缺数据显示未知"),
            observedAllUnknown: observed.length > 0 && observed.every((line) => line.endsWith("未知")),
            noAnomaly: document.querySelectorAll(".skills-anomaly").length === 0,
            noHooksBadge: hooksBadge == null,
            topbar: ["收件箱", "运行监控", "配置管理", "地图"].every((label) => text.includes(label)),
          };
        }"""
    )


async def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "acceptance-out")
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as play:
        browser = await play.chromium.launch(channel="chrome")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda err: print("PAGEERROR", err))
        await open_page(page, f"{BASE}/?mock=1#/configure/hooks", ".tri-line")
        hooks = await checks(page)
        await page.locator("#main").screenshot(path=str(out / "hooks-all-1440.png"))
        await page.get_by_role("radio", name="Cursor").click()
        await page.wait_for_url("**source=cursor**")
        cursor_text = await page.inner_text("#main")
        await page.get_by_role("radio", name="Grok CLI").click()
        await page.wait_for_url("**source=grok_cli**")
        await page.wait_for_selector(".skill-row")
        grok_text = await page.inner_text("#main")
        await page.locator("#main").screenshot(path=str(out / "hooks-grok-1440.png"))
        await open_page(page, f"{BASE}/?mock=1#/configure/skills", ".skills-table .tri-line")
        skills = await checks(page)
        await page.locator(".skills-legend").screenshot(path=str(out / "skills-legend-1440.png"))
        await page.locator(".skill-row .tri-col").first.screenshot(path=str(out / "skills-row-1440.png"))
        await open_page(page, f"{BASE}/?mock=mcp#/configure/mcp", ".skills-table .tri-line")
        mcp = await checks(page)
        await page.locator(".skills-legend").screenshot(path=str(out / "mcp-legend-1440.png"))
        await page.locator(".skill-row .tri-col").first.screenshot(path=str(out / "mcp-row-1440.png"))
        await browser.close()
    rows = [
        ("三种状态用文字说明", hooks["hasDeclared"] and hooks["hasReachable"] and hooks["hasObserved"] and hooks["legend"]),
        ("缺数据显示未知，不当成异常", hooks["hasUnknown"] and "Grok 再扫到" not in cursor_text and "不是异常" in grok_text),
        ("hooks 的 Observed 全部为未知", hooks["observedAllUnknown"]),
        ("选定来源后不把其他来源的读取问题算进来", "Grok 再扫到" not in cursor_text and "sessionStart" not in grok_text),
        ("Grok 加载错误显示不可达", "不可达" in grok_text and "pre_tool_use" in grok_text),
        ("Skills 行有三态文字", skills["hasDeclared"] and skills["hasReachable"] and skills["hasUnknown"]),
        ("MCP 缺探测时可达和观察到为未知", mcp["hasUnknown"] and mcp["hasDeclared"]),
        ("顶栏未改", hooks["topbar"]),
    ]
    failed = False
    for name, ok in rows:
        print(("PASS" if ok else "FAIL") + "\t" + name)
        failed = failed or not ok
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
