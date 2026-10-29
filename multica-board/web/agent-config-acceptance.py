"""ACM-661 design checklist against the static mock. Does not call a live backend.

From multica-board/web:
  python3 agent-config-acceptance.py /path/to/output
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent
PORT = 8797
BASE = f"http://127.0.0.1:{PORT}"


async def open_agents(page) -> None:
    await page.goto(f"{BASE}/?mock=1#/configure/agents", wait_until="domcontentloaded")
    await page.wait_for_selector('[data-block="runtime"] [data-field="model"]', timeout=10000)


async def checklist(page) -> dict:
    return await page.evaluate(
        """() => {
          const block = (name) => document.querySelector(`[data-block="${name}"]`);
          const fields = (root, name) => root ? [...root.querySelectorAll(`[data-field="${name}"]`)] : [];
          const runtime = block("runtime");
          const duty = block("duty");
          const boundary = block("boundary");
          const controls = (root) => root ? root.querySelectorAll("button, input, select, textarea").length : -1;
          const text = document.querySelector("#main")?.innerText || "";
          const nav = [...document.querySelectorAll("#subnav a")].map((node) => (node.childNodes[0]?.textContent || "").trim());
          const edit = [...document.querySelectorAll("#main button")].map((node) => node.textContent.trim()).filter((label) => /编辑|调整|保存|写入|确认/.test(label));
          return {
            title: document.querySelector("#main h2")?.textContent || "",
            runtimeModel: fields(runtime, "model").length,
            dutyModel: fields(duty, "model").length,
            boundaryModel: fields(boundary, "model").length,
            runtimeThinking: fields(runtime, "thinking").length,
            runtimeTier: fields(runtime, "tier").length,
            runtimeQuota: fields(runtime, "quota").length,
            dutyInstructions: fields(duty, "instructions").length,
            dutySkills: fields(duty, "skills").length,
            boundaryField: fields(boundary, "boundary").length,
            runtimeControls: controls(runtime),
            dutyControls: controls(duty),
            boundaryControls: controls(boundary),
            runtimeWrites: runtime?.getAttribute("data-writes") || "",
            dutyTextHasModel: (duty?.innerText || "").includes("claude-opus"),
            runtimeHasModel: (runtime?.innerText || "").includes("claude-opus"),
            runtimeHasQuota: (runtime?.innerText || "").includes("未采集") || (runtime?.innerText || "").includes("已隐藏"),
            secrets: text.includes("account_key") || text.includes("source_path"),
            edit,
            nav,
            scroll: document.documentElement.scrollWidth,
            viewport: window.innerWidth,
          };
        }"""
    )


async def shoot(page, out: Path) -> None:
    await page.set_viewport_size({"width": 1440, "height": 900})
    await page.emulate_media(color_scheme="light")
    await open_agents(page)
    await page.locator("#main").screenshot(path=str(out / "agent-config-1440.png"))
    await page.screenshot(path=str(out / "agent-config-1440-full.png"), full_page=True)
    await page.emulate_media(color_scheme="dark")
    await page.locator('[data-block="runtime"]').screenshot(path=str(out / "agent-config-dark-1440.png"))
    await page.emulate_media(color_scheme="light")
    await page.set_viewport_size({"width": 390, "height": 844})
    await open_agents(page)
    await page.screenshot(path=str(out / "agent-config-390.png"), full_page=True)
    await page.locator('[data-block="runtime"] .table-wrap').evaluate("el => { el.scrollLeft = el.scrollWidth; }")
    await page.locator('[data-block="runtime"]').screenshot(path=str(out / "agent-config-390-runtime.png"))


async def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "acceptance-out")
    out.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with async_playwright() as play:
            browser = await play.chromium.launch(channel="chrome")
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda err: print("PAGEERROR", err))
            await open_agents(page)
            wide = await checklist(page)
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.wait_for_timeout(200)
            narrow = await checklist(page)
            await shoot(page, out)
            await browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)

    nav = wide["nav"]
    rows = [
        ("模型字段在运行配置，不在职责或边界", wide["runtimeModel"] > 0 and wide["dutyModel"] == 0 and wide["boundaryModel"] == 0 and wide["runtimeHasModel"] and not wide["dutyTextHasModel"]),
        ("推理、Tier、额度只在运行配置", wide["runtimeThinking"] > 0 and wide["runtimeTier"] > 0 and wide["runtimeQuota"] > 0 and wide["runtimeHasQuota"]),
        ("职责块有指令、Skills，边界块单独存在", wide["dutyInstructions"] > 0 and wide["dutySkills"] > 0 and wide["boundaryField"] > 0),
        ("三块只读，没有编辑控件", wide["runtimeControls"] == 0 and wide["dutyControls"] == 0 and wide["boundaryControls"] == 0 and wide["runtimeWrites"] == "0" and wide["edit"] == []),
        ("页面不出现密钥字段名", not wide["secrets"]),
        ("标题仍是 Agent 配置，二级导航未改", wide["title"] == "Agent 配置" and nav[:5] == ["Agent 配置", "skills", "MCP", "hooks", "运行配置"]),
        ("1440 与 390 都没有整页横向溢出", wide["scroll"] <= wide["viewport"] and narrow["scroll"] <= narrow["viewport"]),
    ]
    failed = False
    for name, ok in rows:
        print(("PASS" if ok else "FAIL") + "\t" + name)
        failed = failed or not ok
    if failed:
        print("WIDE", wide)
        print("NARROW", narrow)
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
