#!/usr/bin/env python3
"""ACM-389 cross-page consistency checks. Screenshots: 1440/390, light/dark.

Writes a checklist next to the screenshots. Does not change the dev server.
"""

import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

PORT = 8876
ROOT = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parents[3] / "acm-389-shots"


def serve():
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT))
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


CSS_CHECKS = """
() => {
  const find = (selector) => {
    for (const sheet of document.styleSheets) {
      let rules;
      try { rules = sheet.cssRules; } catch { continue; }
      for (const rule of rules) {
        if (rule.selectorText === selector) return rule;
      }
    }
    return null;
  };
  const value = (selector, prop) => find(selector)?.style.getPropertyValue(prop).trim() || "";
  const top = document.querySelector("header.topbar")?.innerText || "";
  return {
    quiet: value(".skill-row.quiet", "opacity"),
    stale: value(".is-stale, .plan-expired, .is-dim", "opacity"),
    matrixHead: value(".matrix thead th", "background-color") || value(".matrix thead th", "background"),
    group: value(".matrix .group-row th", "background"),
    usage: value(".usage-col", "border-top-color") || value(".usage-col", "border-top"),
    anomaly: value(".skills-anomaly", "color"),
    badge: value(".tab-badge", "color"),
    layout: !!find(".board-layout"),
    oldLayout: !!find(".skills-layout"),
    oldQueue: !!find(".skills-queue"),
    topHasInbox: top.includes("收件箱"),
    topHasRunning: top.includes("运行监控"),
    topHasConfigure: top.includes("配置管理"),
    topHasMap: top.includes("地图"),
  };
}
"""


LAYER_COLORS = """
() => {
  const paint = (name) => {
    const probe = document.createElement("span");
    probe.style.backgroundColor = `var(${name})`;
    document.body.append(probe);
    const color = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return color;
  };
  const bgOf = (selector) => {
    const node = document.querySelector(selector);
    return node ? getComputedStyle(node).backgroundColor : "";
  };
  return {
    th: bgOf(".layer-table th"),
    groupsTh: bgOf(".layer-groups th"),
    h3: bgOf(".layer-table h3"),
    groupsH3: bgOf(".layer-groups > section > h3"),
    panel2: paint("--panel-2"),
    bg: paint("--bg"),
  };
}
"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    httpd = serve()
    rows = []

    def check(name, ok, detail=""):
        rows.append({"item": name, "result": "pass" if ok else "fail", "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme="light")
            page.on("pageerror", lambda err: check(f"pageerror {err}", False))
            origin = f"http://127.0.0.1:{PORT}/index.html?mock"
            page.goto(f"{origin}#/configure/skills", wait_until="networkidle")
            page.wait_for_selector("main h2")
            css = page.evaluate(CSS_CHECKS)
            check("G11 quiet opacity 0.62", css["quiet"] == "0.62", css["quiet"])
            check("G10 stale opacity 0.62", css["stale"] == "0.62", css["stale"])
            check("G16 board-layout exists", css["layout"] is True)
            check("G16 old skills-layout gone", css["oldLayout"] is False)
            check("G16 old skills-queue gone", css["oldQueue"] is False)
            check("G14 badge text uses on-warn token", "1c1408" in css["badge"].lower() or css["badge"].startswith("var("), css["badge"])
            check("顶栏四项仍在", css["topHasInbox"] and css["topHasRunning"] and css["topHasConfigure"] and css["topHasMap"])
            check("Skills 页有 h2", page.locator("main h2").first.inner_text() == "skills")
            check("G16 queue class", page.locator("aside.attention-queue").count() == 1)
            check("G16 bucket class", page.locator("section.queue-bucket").count() > 0)
            check("G5 collapse control after expand", _collapse(page))
            check("G9 readonly sentence", "页面只读，不代为执行。复制命令后在自己的终端运行。" in _open_skill(page))
            check("G7 copy failure text", _copy_fail(page))

            page.goto(f"{origin}#/running", wait_until="networkidle")
            page.wait_for_selector(".provider")
            page.locator(".provider").first.click()
            active = page.evaluate("""() => {
              const node = document.querySelector(".provider.active");
              if (!node) return "";
              const style = getComputedStyle(node);
              return style.backgroundColor + " " + style.borderTopColor;
            }""")
            check("G2 provider selected uses info background", "232, 240, 253" in active or "info" in active, active)
            check("Agents 页有 h2", page.locator("main h2").first.inner_text() == "Agent 状态")

            page.goto(f"{origin}#/decide", wait_until="networkidle")
            page.wait_for_selector("main h2")
            check("待批准页有 h2", "待批准" in page.locator("main h2").first.inner_text())

            page.goto(f"{origin}#/map", wait_until="networkidle")
            layer = page.evaluate("""() => {
              const probe = document.createElement("span");
              probe.className = "layer-badge";
              probe.style.setProperty("--cell", "var(--layer-l5)");
              document.body.append(probe);
              const color = getComputedStyle(probe).backgroundColor;
              probe.remove();
              return color;
            }""")
            check("G14 layer badge painted from --layer-l5", layer not in ("", "rgba(0, 0, 0, 0)"), layer)

            for theme, label in (("light", "亮色"), ("dark", "暗色")):
                page.emulate_media(color_scheme=theme)
                page.set_viewport_size({"width": 1440, "height": 900})
                page.goto(f"http://127.0.0.1:{PORT}/index.html?mock=atlas#/map/all?seg=all", wait_until="networkidle")
                page.wait_for_selector(".layer-table th")
                page.wait_for_selector(".layer-groups > section > h3")
                page.keyboard.press("Escape")
                page.wait_for_function("() => document.querySelector('#drawer')?.hidden === true")
                colors = page.evaluate(LAYER_COLORS)
                check(
                    f"G1 层清单表头 {label} 为 panel-2",
                    colors["th"] == colors["panel2"] and colors["groupsTh"] == colors["panel2"] and colors["th"] not in ("", "rgba(0, 0, 0, 0)"),
                    f"{colors['th']} / {colors['groupsTh']} vs {colors['panel2']}",
                )
                check(
                    f"G1 层清单分组 {label} 为 bg",
                    colors["h3"] == colors["bg"] and colors["groupsH3"] == colors["bg"] and colors["h3"] != colors["th"],
                    f"{colors['h3']} / {colors['groupsH3']} vs {colors['bg']}",
                )
                page.evaluate("() => document.querySelector('.layer-groups')?.scrollIntoView({block:'start'})")
                file = OUT / f"layers-1440-{theme}.png"
                page.screenshot(path=str(file), full_page=False)
                check(f"shot {file.name}", file.stat().st_size > 1000, str(file.stat().st_size))

            for width, height in ((1440, 900), (390, 844)):
                for theme in ("light", "dark"):
                    page.emulate_media(color_scheme=theme)
                    page.set_viewport_size({"width": width, "height": height})
                    for name, route, selector in (
                        ("skills", "#/configure/skills", "main h2"),
                        ("agents", "#/running", ".provider"),
                        ("decide", "#/decide", "main h2"),
                    ):
                        page.goto(f"{origin}{route}", wait_until="networkidle")
                        page.wait_for_selector(selector)
                        page.keyboard.press("Escape")
                        page.wait_for_function("() => document.querySelector('#drawer')?.hidden === true")
                        page.evaluate("() => window.scrollTo(0, 0)")
                        file = OUT / f"{name}-{width}-{theme}.png"
                        page.screenshot(path=str(file), full_page=False)
                        check(f"shot {file.name}", file.stat().st_size > 1000, str(file.stat().st_size))
            browser.close()
    finally:
        httpd.shutdown()

    (OUT / "checklist.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [row for row in rows if row["result"] != "pass"]
    print(f"{len(rows) - len(failed)} pass, {len(failed)} fail")
    return 1 if failed else 0


def _open_skill(page):
    page.goto(f"http://127.0.0.1:{PORT}/index.html?mock#/configure/skills", wait_until="networkidle")
    page.locator(".skill-row").first.click()
    page.wait_for_selector("#drawer .command")
    return page.locator("#drawer").inner_text()


def _collapse(page):
    page.goto(f"http://127.0.0.1:{PORT}/index.html?mock#/configure/skills", wait_until="networkidle")
    more = page.get_by_role("button", name="显示其余")
    if more.count() == 0:
        return True
    more.first.click()
    page.wait_for_timeout(50)
    return page.get_by_role("button", name="收起到 8 条").count() > 0


def _copy_fail(page):
    _open_skill(page)
    page.evaluate("""() => {
      const deny = () => Promise.reject(new Error("denied"));
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: deny } });
      document.execCommand = () => false;
    }""")
    page.locator("#drawer .command button").first.click()
    page.wait_for_timeout(80)
    return page.locator("#drawer .command button").first.inner_text() == "复制失败，请手抄"


if __name__ == "__main__":
    sys.exit(main())
