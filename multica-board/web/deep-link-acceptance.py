#!/usr/bin/env python3
"""Browser checks for Agents ↔ Skills ↔ MCP focus links. ACM-390."""

import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

PORT = 8808
ROOT = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/acm-390-shots")
HMAC = "hmac-salted-id"


def serve():
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT))
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def shot(page, name):
    page.screenshot(path=str(OUT / name))


def wait_focus(page, needle):
    page.wait_for_function("(needle) => location.hash.includes(needle)", arg=needle)


def open_agent(page):
    page.goto(f"http://127.0.0.1:{PORT}/index.html?mock=deep-link#/running", wait_until="networkidle")
    page.get_by_role("row", name="打开 Repo Analyst").click()
    expect(page.locator("#drawer")).to_be_visible()
    page.get_by_role("button", name="Skills 绑定").click()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    httpd = serve()
    failed = []

    def check(name, ok, detail=""):
        print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))
        if not ok:
            failed.append(name)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            light = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
            page = light.new_page()
            page.on("pageerror", lambda err: failed.append(f"pageerror {err}"))

            open_agent(page)
            page.get_by_role("button", name="打开 skill colgrep-search").click()
            wait_focus(page, "focus=colgrep-search")
            wait_focus(page, "from=agent")
            page.wait_for_timeout(50)
            skill_hash = page.evaluate("() => location.hash")
            title = page.locator("#drawer .drawer-title")
            expect(title).to_be_visible()
            check("skill url", "focus=colgrep-search" in skill_hash and "from=agent" in skill_hash, skill_hash)
            check("skill origin", "来自 Agent Repo Analyst" in page.locator("#drawer").inner_text())
            check("skill title focused", page.evaluate("() => document.activeElement?.classList.contains('drawer-title')"))
            check("mcp binding copy", "不是依赖关系" in page.locator("#drawer").inner_text())
            shot(page, "skill-1440-light.png")

            page.get_by_role("button", name="打开 MCP github").click()
            wait_focus(page, "focus=ws%2F")
            page.wait_for_timeout(50)
            mcp_hash = page.evaluate("() => location.hash")
            check("mcp stable ref", "srv-github" in mcp_hash and HMAC not in mcp_hash, mcp_hash)
            drawer = page.locator("#drawer").inner_text()
            check("mcp origin", "来自 Skill colgrep-search" in drawer, drawer[:240])
            check("mcp reverse skill", "打开 skill colgrep-search" in page.locator("#drawer").inner_html() or "colgrep-search" in drawer)
            shot(page, "mcp-1440-light.png")

            page.get_by_role("button", name="返回上一层").click()
            page.wait_for_function("() => location.hash.includes('configure/skills') && location.hash.includes('colgrep-search')")
            page.wait_for_timeout(50)
            check("back one layer", "configure/mcp" not in page.evaluate("() => location.hash"))
            expect(page.locator("#drawer")).to_contain_text("colgrep-search")
            shot(page, "back-skill-1440-light.png")

            fresh = light.new_page()
            fresh.goto(f"http://127.0.0.1:{PORT}/index.html?mock=deep-link{skill_hash}", wait_until="networkidle")
            fresh.wait_for_timeout(100)
            check("fresh url restores skill", "colgrep-search" in fresh.locator("#drawer").inner_text())
            fresh.close()

            missing = light.new_page()
            missing.goto(f"http://127.0.0.1:{PORT}/index.html?mock=deep-link#/configure/skills?focus=missing-skill&from=agent%2Fe8659de2-7504-4ee2-9f0a-fd608a9a8bc2%2Fagent-1", wait_until="networkidle")
            missing.wait_for_timeout(100)
            panel = missing.locator("[data-focus-state]")
            check("missing state", panel.get_attribute("data-focus-state") == "missing")
            check("missing copy", "找不到目标" in panel.inner_text() and "不会改去打开别的" in panel.inner_text())
            check("missing hides drawer", missing.locator("#drawer").is_hidden())
            shot(missing, "missing-1440-light.png")
            missing.close()

            archived = light.new_page()
            archived.goto(f"http://127.0.0.1:{PORT}/index.html?mock=deep-link#/configure/skills?focus=old-skill", wait_until="networkidle")
            archived.wait_for_timeout(100)
            check("archived state", archived.locator("[data-focus-state='archived']").inner_text().find("已下线") >= 0)
            check("archived hides drawer", archived.locator("#drawer").is_hidden())
            shot(archived, "archived-1440-light.png")
            archived.close()

            down = light.new_page()
            down_url = f"http://127.0.0.1:{PORT}/index.html?mock=deep-link-down#/configure/skills?focus=colgrep-search&from=agent%2Fe8659de2-7504-4ee2-9f0a-fd608a9a8bc2%2Fagent-1"
            down.goto(down_url, wait_until="networkidle")
            down.wait_for_timeout(100)
            before = down.evaluate("() => location.hash")
            check("network state", down.locator("[data-focus-state='network']").is_visible())
            down.get_by_role("button", name="重试").click()
            down.wait_for_timeout(200)
            check("retry keeps hash", down.evaluate("() => location.hash") == before)
            check("retry keeps failure", down.locator("[data-focus-state='network']").is_visible())
            shot(down, "network-1440-light.png")
            down.close()

            denied = light.new_page()
            denied.goto(f"http://127.0.0.1:{PORT}/index.html?mock=deep-link-denied#/configure/skills?focus=colgrep-search", wait_until="networkidle")
            denied.wait_for_timeout(100)
            check("business forbidden", denied.locator("[data-focus-state='forbidden']").is_visible())
            http403 = light.new_page()
            http403.route(
                "**/deep-link-denied/skills.json",
                lambda route: route.fulfill(status=403, content_type="application/json", body='{"error":{"code":"permission_denied","message":"没有权限"}}'),
            )
            http403.goto(f"http://127.0.0.1:{PORT}/index.html?mock=deep-link-denied#/configure/skills?focus=colgrep-search&from=agent%2Fe8659de2-7504-4ee2-9f0a-fd608a9a8bc2%2Fagent-1", wait_until="networkidle")
            http403.wait_for_timeout(100)
            forbidden = http403.locator("[data-focus-state='forbidden']")
            check("http 403 is forbidden", forbidden.is_visible() and "没有权限" in forbidden.inner_text() and "网络失败" not in forbidden.inner_text())
            shot(http403, "forbidden-1440-light.png")
            denied.close()
            http403.close()

            keys = light.new_page()
            keys.goto(f"http://127.0.0.1:{PORT}/index.html?mock=deep-link#/running", wait_until="networkidle")
            row = keys.get_by_role("row", name="打开 Repo Analyst")
            row.focus()
            row.press("Enter")
            keys.get_by_role("button", name="Skills 绑定").click()
            button = keys.get_by_role("button", name="打开 skill colgrep-search")
            button.focus()
            button.press("Enter")
            keys.wait_for_timeout(80)
            check("keyboard lands on title", keys.evaluate("() => document.activeElement?.classList.contains('drawer-title')"))
            back = keys.get_by_role("button", name="返回上一层")
            check("back is focusable", back.evaluate("el => el.tabIndex >= 0"))
            keys.keyboard.press("Escape")
            keys.wait_for_timeout(50)
            landed_on = keys.evaluate("() => document.activeElement?.id || document.activeElement?.tagName")
            check("escape returns to skill row", landed_on == "skill-colgrep-search", landed_on)
            keys.close()

            scroll = light.new_page()
            scroll.goto(f"http://127.0.0.1:{PORT}/index.html?mock=deep-link#/configure/skills", wait_until="networkidle")
            more = scroll.get_by_role("button", name="显示其余")
            if more.count():
                more.first.click()
            scroll.evaluate("() => window.scrollTo(0, 280)")
            scroll.wait_for_timeout(30)
            y = scroll.evaluate("() => window.scrollY")
            scroll.locator("#skill-filler-11").click()
            scroll.wait_for_timeout(50)
            scroll.go_back()
            scroll.wait_for_timeout(80)
            y2 = scroll.evaluate("() => window.scrollY")
            check("scroll restored", abs(y2 - y) <= 40, f"{y} -> {y2}")
            scroll.close()

            direct = (
                "http://127.0.0.1:%s/index.html?mock=deep-link#/configure/mcp?focus=ws%%2Fe8659de2-7504-4ee2-9f0a-fd608a9a8bc2%%2Fsrv%%2Fsrv-github&from=skill%%2Fcolgrep-search"
                % PORT
            )
            cycle = light.new_page()
            cycle.goto(direct, wait_until="networkidle")
            cycle.wait_for_timeout(100)
            check("second library entry opens", cycle.locator("#drawer").is_visible() and "找不到目标" not in cycle.locator("#main").inner_text())
            cycle.get_by_role("button", name="返回上一层").click()
            cycle.wait_for_function("() => location.hash.includes('configure/skills') && location.hash.includes('colgrep-search')")
            cycle.get_by_role("button", name="返回上一层").click()
            cycle.wait_for_timeout(80)
            landed = cycle.evaluate("() => location.hash")
            check("direct return reaches agents", "running" in landed and "configure/mcp" not in landed and "focus=" not in landed, landed)
            cycle.close()

            space = light.new_page()
            space.goto(f"http://127.0.0.1:{PORT}/index.html?mock=deep-link#/configure/skills", wait_until="networkidle")
            space.locator("#skill-colgrep-search").focus()
            before_y = space.evaluate("() => window.scrollY")
            space.keyboard.press("Space")
            space.wait_for_function("() => location.hash.includes('focus=colgrep-search')")
            after_y = space.evaluate("() => window.scrollY")
            check("space opens skill", "focus=colgrep-search" in space.evaluate("() => location.hash"))
            check("space does not scroll", abs(after_y - before_y) <= 5, f"{before_y}->{after_y}")
            space.keyboard.press("Escape")
            space.wait_for_timeout(50)
            space.goto(f"http://127.0.0.1:{PORT}/index.html?mock=deep-link#/configure/mcp", wait_until="networkidle")
            space.locator("#mcp-row-hmac-salted-id").focus()
            before_y = space.evaluate("() => window.scrollY")
            space.keyboard.press("Space")
            space.wait_for_function("() => location.hash.includes('focus=')")
            after_y = space.evaluate("() => window.scrollY")
            mcp_space = space.evaluate("() => location.hash")
            check("space opens mcp", "focus=" in mcp_space and "srv-" in mcp_space, mcp_space)
            check("mcp space does not scroll", abs(after_y - before_y) <= 5, f"{before_y}->{after_y}")
            space.keyboard.press("Enter")
            space.wait_for_timeout(40)
            space.keyboard.press("Escape")
            space.wait_for_timeout(50)
            mcp_focus = space.evaluate("() => document.activeElement?.id || document.activeElement?.tagName")
            check("escape returns to mcp row", mcp_focus == "mcp-row-hmac-salted-id", mcp_focus)
            space.close()

            held = []

            def hold_slow(route):
                url = route.request.url
                if url.endswith("/fixtures/meta.json") or url.endswith("/fixtures/deep-link/skills.json"):
                    held.append(route)
                    return
                route.continue_()

            slow = light.new_page()
            slow.route("**/*", hold_slow)
            slow.goto(
                f"http://127.0.0.1:{PORT}/index.html?mock=deep-link#/configure/skills?focus=colgrep-search&from=agent%2Fe8659de2-7504-4ee2-9f0a-fd608a9a8bc2%2Fagent-1",
                wait_until="commit",
            )
            slow.wait_for_selector("[data-focus-state='loading']", timeout=5000)
            loading = slow.locator("[data-focus-state='loading']").inner_text()
            check("slow load names target", "colgrep-search" in loading and "正在打开" in loading, loading[:180])
            check("slow load names origin", "来自" in loading)
            check("slow load return", slow.get_by_role("button", name="返回上一层").is_visible())
            check("slow load is not only the page placeholder", "加载中…" not in slow.locator("#main").inner_text())
            slow.get_by_role("button", name="返回上一层").click()
            slow.wait_for_timeout(50)
            check("slow return leaves the target", "focus=colgrep-search" not in slow.evaluate("() => location.hash"))
            for route in held:
                route.continue_()
            slow.close()
            light.close()

            dark = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark")
            dpage = dark.new_page()
            dpage.goto(f"http://127.0.0.1:{PORT}/index.html?mock=deep-link{skill_hash}", wait_until="networkidle")
            dpage.wait_for_timeout(100)
            shot(dpage, "skill-1440-dark.png")
            dark.close()

            phone = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="light")
            pp = phone.new_page()
            pp.goto(f"http://127.0.0.1:{PORT}/index.html?mock=deep-link{skill_hash}", wait_until="networkidle")
            pp.wait_for_timeout(100)
            shot(pp, "skill-390-light.png")
            phone.close()

            phone_dark = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
            pd = phone_dark.new_page()
            pd.goto(f"http://127.0.0.1:{PORT}/index.html?mock=deep-link{mcp_hash}", wait_until="networkidle")
            pd.wait_for_timeout(150)
            check("phone mcp", "github" in pd.locator("body").inner_text())
            shot(pd, "mcp-390-dark.png")
            phone_dark.close()
            browser.close()
    finally:
        httpd.shutdown()

    print(f"\n{len(failed)} failed")
    if failed:
        for item in failed:
            print(" -", item)
        sys.exit(1)


if __name__ == "__main__":
    main()
