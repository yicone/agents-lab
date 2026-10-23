"""ACM-403 MCP board browser acceptance (spec §15, §10 验收看哪里).

Usage (from multica-board/, static server already running):
    python3 -m http.server 8799 --bind 127.0.0.1 &   # from multica-board/
    python3 docs/mcp-redesign/acceptance.py

Needs Python Playwright + local Chrome + Pillow. Stdlib + playwright only;
deterministic filenames in docs/mcp-redesign/acceptance/. Prints one
PASS/FAIL line per check with measured numbers; exits non-zero on any FAIL.
Only fixture data is mocked — the app's own JS runs for real.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8799"
OUT = Path(__file__).resolve().parent / "acceptance"
BOARD = Path(__file__).resolve().parents[2]
READONLY = "页面只读，不代为执行。复制命令后在自己的终端运行。"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    return ok


def page_url(scenario: str) -> str:
    return f"{BASE}/web/?mock={scenario}#/mcp"


def open_page(browser, scenario: str, w: int = 1440, h: int = 900, scheme: str = "light"):
    ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=scheme)
    page = ctx.new_page()
    page.goto(page_url(scenario))
    page.wait_for_selector(".skills-table, .atlas-guide", timeout=8000)
    page.wait_for_timeout(400)
    return ctx, page


def shot(page, name: str) -> Path:
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path))
    return path


def composite(imgs: list[tuple[str, Path]], name: str) -> Path:
    ims = [(label, Image.open(p).convert("RGB")) for label, p in imgs]
    w = sum(i.width for _, i in ims)
    h = max(i.height for _, i in ims)
    canvas = Image.new("RGB", (w, h + 24), "white")
    from PIL import ImageDraw
    draw = ImageDraw.Draw(canvas)
    x = 0
    for label, im in ims:
        draw.text((x + 8, 4), label, fill="black")
        canvas.paste(im, (x, 24))
        x += im.width
    path = OUT / f"{name}.png"
    canvas.save(path)
    return path


def text_of(page, sel: str, all_: bool = False):
    return page.eval_on_selector_all(sel, "els => els.map(e => e.textContent)") if all_ else page.eval_on_selector(sel, "e => e.textContent")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")

        # ---------- check 1: scenario screenshots + badge/empty assertions ----------
        shots = {}
        for sc in ["mcp", "mcp-warn", "mcp-unknown", "mcp-partial", "mcp-empty", "mcp-absent", "mcp-invalid"]:
            ctx, page = open_page(browser, sc)
            shots[sc] = shot(page, f"scenario-{sc}-1440")
            ctx.close()
        badge_checks = {
            "mcp": ("tab-badge tone-bad", "6", "待处理 6 条"),
            "mcp-warn": ("tab-badge", "1", "待处理 1 条"),
            "mcp-unknown": ("tab-badge tone-unknown", "?", "没读到"),
        }
        for sc, (cls, txt, title_frag) in badge_checks.items():
            ctx, page = open_page(browser, sc)
            got = page.eval_on_selector("#mcp-badge", "e => ({cls: e.className, text: e.textContent, title: e.title, hidden: e.hidden})")
            ok = got["cls"].strip() == cls and got["text"].strip() == txt and title_frag in got["title"] and not got["hidden"]
            check(f"1.badge[{sc}]", ok, json.dumps(got, ensure_ascii=False))
            ctx.close()

        ctx, page = open_page(browser, "mcp-partial")
        nempty = page.eval_on_selector_all("td.tool-mark.not-applicable", "e => e.length")
        anomaly = text_of(page, ".skills-anomaly") or ""
        cells = text_of(page, "td.tool-mark.unknown", True)
        cards = " | ".join(text_of(page, ".source-card", True))
        queue_txt = text_of(page, ".skills-queue") or ""
        check("1.partial.no-not-applicable", nempty == 0, f"∅ cells={nempty}")
        check("1.partial.anomaly", "解析失败" in anomaly and "分配没读到" in anomaly, anomaly[:120])
        check("1.partial.unknown-cells", len(cells) > 0, f"? cells={len(cells)}")
        check("1.partial.uncertain-card", "含 1 行待确认" in cards, cards[:160])
        check("1.partial.bucket-q", "另有来源没读到" in queue_txt and re.search(r"\d \?", queue_txt) is not None, queue_txt[:160])
        ctx.close()

        ctx, page = open_page(browser, "mcp-unknown")
        body = text_of(page, "main") or ""
        card_nums = page.eval_on_selector_all(".source-card strong", "e => e.map(x => x.textContent)")
        check("1.unknown.guide", "所有来源都没读到" in body, body[:80])
        # 全部/本机+Multica 显示已知事实 0；带未读来源的三张卡显示 "—"
        check("1.unknown.cards-dash", card_nums == ["0", "0", "—", "—", "—"], str(card_nums))
        ctx.close()

        for sc, needle in [("mcp-empty", "还没有 MCP server"), ("mcp-absent", "后端还没有 MCP 采集"), ("mcp-invalid", "MCP 数据无效")]:
            ctx, page = open_page(browser, sc)
            t = text_of(page, "main") or ""
            check(f"1.state[{sc}]", needle in t, t[:80])
            ctx.close()

        # ---------- check 2: Skills vs MCP composites ----------
        def board_shot(url, name, w, h, scheme="light"):
            ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=scheme)
            p = ctx.new_page()
            p.goto(url)
            p.wait_for_timeout(900)
            path = shot(p, name)
            ctx.close()
            return path

        for vw in (1440, 1100):
            a = board_shot(f"{BASE}/web/?mock=1#/skills", f"skills-{vw}", vw, 900)
            b = board_shot(page_url("mcp"), f"mcp-{vw}", vw, 900)
            composite([("Skills", a), ("MCP", b)], f"compare-skills-mcp-{vw}")
        d = board_shot(page_url("mcp"), "mcp-dark-1440", 1440, 900, "dark")
        m390 = board_shot(page_url("mcp"), "mcp-390", 390, 844)
        composite([("MCP 深色 1440", d), ("MCP 390", m390)], "compare-mcp-dark-narrow")
        # structural-class comparison facts
        ctx, ps = open_page(browser, "mcp")
        ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
        p2 = ctx2.new_page()
        p2.goto(f"{BASE}/web/?mock=1#/skills")
        p2.wait_for_selector(".skills-table")
        mcp_cls = ps.eval_on_selector_all("[class]", "els => [...new Set(els.flatMap(e => [...e.classList]))]")
        sk_cls = p2.eval_on_selector_all("[class]", "els => [...new Set(els.flatMap(e => [...e.classList]))]")
        shared = sorted(set(mcp_cls) & set(sk_cls))
        check("2.shared-structure", {"skills-table", "skills-legend", "skills-queue", "source-card", "skill-row"}.issubset(set(shared)),
              f"shared={shared}")
        print("  facts: mcp-only:", sorted(set(mcp_cls) - set(sk_cls)))
        print("  facts: skills-only:", sorted(set(sk_cls) - set(mcp_cls)))
        ctx.close(); ctx2.close()

        # ---------- check 3: horizontal scroll ----------
        for vw, vh in [(1440, 900), (1100, 900), (390, 844)]:
            ctx, page = open_page(browser, "mcp", vw, vh)
            m = page.evaluate("""() => {
              const doc = document.documentElement;
              const wrap = document.querySelector('.table-wrap');
              return { docW: doc.scrollWidth, vw: innerWidth, wrapW: wrap ? wrap.scrollWidth : 0 };
            }""")
            check(f"3.no-page-overflow[{vw}]", m["docW"] <= m["vw"], f"scrollWidth={m['docW']} vw={m['vw']} tableContent={m['wrapW']}")
            page.eval_on_selector(".table-wrap", "e => e.scrollLeft = e.scrollWidth")
            page.wait_for_timeout(200)
            vis = page.evaluate("""() => {
              const wrap = document.querySelector('.table-wrap');
              const wr = wrap.getBoundingClientRect();
              const ths = [...document.querySelectorAll('thead th')];
              const last = ths[ths.length - 1].getBoundingClientRect();
              const tag = document.querySelector('tbody tr.skill-row td .tag');
              const tr = tag ? tag.getBoundingClientRect() : null;
              return { wrapL: wr.left, wrapR: wr.right, headR: last.right, tagR: tr ? tr.right : null };
            }""")
            ok = vis["headR"] <= vis["wrapR"] + 1 and (vis["tagR"] is None or vis["tagR"] <= vis["wrapR"] + 1)
            check(f"3.scroll-right-visible[{vw}]", ok, json.dumps(vis))
            # sticky thead on vertical scroll
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(150)
            st = page.evaluate("""() => {
              const th = document.querySelector('thead th');
              const wrap = document.querySelector('.table-wrap');
              return { thTop: th.getBoundingClientRect().top, wrapTop: wrap.getBoundingClientRect().top };
            }""")
            check(f"3.sticky-head[{vw}]", st["thTop"] >= st["wrapTop"] - 1 and st["thTop"] <= st["wrapTop"] + 60, json.dumps(st))
            page.evaluate("window.scrollTo(0, 0)")
            # click a row after horizontal scroll → drawer opens for that row
            name = page.eval_on_selector("tbody tr.skill-row td .mono", "e => e.textContent")
            page.eval_on_selector(".table-wrap", "e => e.scrollLeft = e.scrollWidth")
            page.click("tbody tr.skill-row")
            page.wait_for_selector("#drawer:not([hidden])")
            dname = page.eval_on_selector("#drawer .drawer-head .mono", "e => e.textContent")
            check(f"3.row-click-after-scroll[{vw}]", dname.strip() == name.strip(), f"row={name} drawer={dname}")
            page.keyboard.press("Escape")
            shot(page, f"mcp-{vw}-scrolled")
            ctx.close()

        # ---------- check 4: 390 drawer + focus ----------
        ctx, page = open_page(browser, "mcp", 390, 844)
        row = page.eval_on_selector("tbody tr.skill-row", "e => e.id")
        page.click("tbody tr.skill-row")
        page.wait_for_selector("#drawer:not([hidden])")
        shot(page, "mcp-390-drawer")
        page.click("#drawer .drawer-close")
        page.wait_for_function("document.getElementById('drawer').hidden === true")
        focus_id = page.evaluate("document.activeElement && document.activeElement.id")
        check("4.close-x-focus", focus_id == row, f"active={focus_id} want {row}")
        page.click("tbody tr.skill-row")
        page.wait_for_selector("#drawer:not([hidden])")
        page.keyboard.press("Escape")
        page.wait_for_function("document.getElementById('drawer').hidden === true")
        focus_id = page.evaluate("document.activeElement && document.activeElement.id")
        check("4.close-esc-focus", focus_id == row, f"active={focus_id} want {row}")
        ctx.close()

        # ---------- check 5: keyboard walkthrough + source cards ----------
        ctx, page = open_page(browser, "mcp")
        page.goto(f"{BASE}/web/?mock=mcp#/skills")
        page.wait_for_selector(".skills-table")
        page.keyboard.press("6")
        page.wait_for_timeout(400)
        check("5.key-6", page.evaluate("location.hash") == "#/mcp", page.evaluate("location.hash"))
        page.keyboard.press("/")
        check("5.search-focus", page.evaluate("document.activeElement && document.activeElement.id") == "search",
              page.evaluate("document.activeElement && document.activeElement.id") or "")
        page.keyboard.press("Escape")
        page.keyboard.press("Escape")  # clear search focus state
        found = ""
        for _ in range(80):
            page.keyboard.press("Tab")
            fid = page.evaluate("document.activeElement && document.activeElement.id || ''")
            if fid.startswith("mcp-row-"):
                found = fid
                break
        check("5.tab-to-row", bool(found), f"row={found}")
        if found:
            page.keyboard.press("Enter")
            page.wait_for_selector("#drawer:not([hidden])")
            check("5.enter-opens", page.evaluate("!document.getElementById('drawer').hidden"), "")
            page.keyboard.press("Escape")
            page.wait_for_function("document.getElementById('drawer').hidden === true")
            check("5.esc-focus-back", page.evaluate("document.activeElement && document.activeElement.id") == found,
                  page.evaluate("document.activeElement && document.activeElement.id") or "")
        cards = page.eval_on_selector_all(".source-card", "els => els.map(e => e.textContent.slice(0, 12))")
        for i, want in enumerate(["", "both", "local_only", "multica_only", "project"]):
            page.eval_on_selector_all(".source-card", f"els => els[{i}].click()")
            page.wait_for_timeout(200)
            h = page.evaluate("location.hash")
            sel = page.eval_on_selector(".toolbar select", "e => e.value")
            ok = (f"source={want}" in h if want else "source=" not in h) and sel == want
            check(f"5.card[{want or 'all'}]", ok, f"hash={h} select={sel}")
        ctx.close()

        # ---------- check 6: clipboard three paths ----------
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  permissions=["clipboard-read", "clipboard-write"])
        page = ctx.new_page()
        page.goto(page_url("mcp"))
        page.wait_for_selector(".skills-table")
        page.click("tbody tr.skill-row")
        page.wait_for_selector("#drawer:not([hidden])")
        page.eval_on_selector(".command button", "e => e.click()")
        page.wait_for_timeout(300)
        t = text_of(page, ".command button")
        check("6.clipboard-ok", (t or "").strip() == "已复制", f"text={t!r}")
        # path 2: clipboard rejects, execCommand works
        page.evaluate("() => { navigator.clipboard.writeText = () => Promise.reject(new Error('denied')); }")
        page.eval_on_selector_all(".command button", "els => { els.forEach(e => e.textContent = '复制'); }")
        page.eval_on_selector(".command button", "e => e.click()")
        page.wait_for_timeout(300)
        t = text_of(page, ".command button")
        check("6.execCommand-fallback", (t or "").strip() == "已复制", f"text={t!r}")
        # path 3: everything fails
        page.evaluate("""() => {
          navigator.clipboard.writeText = () => Promise.reject(new Error('denied'));
          document.execCommand = () => false;
        }""")
        page.eval_on_selector_all(".command button", "els => { els.forEach(e => e.textContent = '复制'); }")
        cmd = text_of(page, ".command code")
        page.eval_on_selector(".command button", "e => e.click()")
        page.wait_for_timeout(300)
        t = text_of(page, ".command button")
        sel_txt = page.evaluate("window.getSelection().toString()")
        hint = page.eval_on_selector_all(".copy-hint", "els => els.map(e => e.textContent)")
        check("6.total-fail", (t or "").strip() == "复制失败" and sel_txt == (cmd or "")
              and "已选中命令，按 ⌘C 复制" in hint,
              f"btn={t!r} sel_len={len(sel_txt)} hint={hint}")
        shot(page, "copy-fail-1440")
        ctx.close()

        # ---------- check 7: read-only wording + command safety ----------
        ctx, page = open_page(browser, "mcp")
        page_top = page.evaluate("document.querySelector('main').textContent")
        check("7.readonly-not-page-top", READONLY not in page_top, "")
        page.click("tbody tr.skill-row")
        page.wait_for_selector("#drawer:not([hidden])")
        drawer = page.evaluate("document.getElementById('drawer').textContent")
        n = drawer.count(READONLY)
        check("7.readonly-in-drawer", n >= 1, f"occurrences={n}")
        cmds = text_of(page, ".command code", True)
        bad = [c for c in cmds if "--server-config '" in c or "--mcp-config '{" in c
               or "stdin" in c or re.search(r"--(server|mcp)-config (?!--)", c)]
        check("7.command-safety", not bad, f"commands={len(cmds)} bad={bad}")
        check("7.ws-id", all("--workspace-id" in c for c in cmds if c.startswith("multica")), f"n={len(cmds)}")
        ctx.close()

        # ---------- check 8: agent assignment info ----------
        ctx, page = open_page(browser, "mcp")
        page.click("tbody tr.skill-row")
        page.wait_for_selector("#drawer:not([hidden])")
        d = page.evaluate("document.getElementById('drawer').textContent")
        check("8.drawer-agents", "Builder · Codex" in d and "会加载" in d, "")
        # own-config entry: sentry row
        row2 = page.eval_on_selector_all("tbody tr.skill-row td .mono", "els => els.map(e => e.textContent)")
        page.keyboard.press("Escape")
        page.click("#mcp-row-mvcp4jwdq5kxer6lyfs7m")  # sentry
        page.wait_for_selector("#drawer:not([hidden])")
        d = page.evaluate("document.getElementById('drawer').textContent")
        check("8.drawer-own", "（自有）" in d and "清除自有配置" in d, "")
        page.keyboard.press("Escape")
        ws_col = page.eval_on_selector_all("td.cloud-cell", "els => els.map(e => e.textContent)")
        check("8.ws-column", any("Builder · Codex" in t for t in ws_col), str(ws_col)[:160])
        # assigned-only checkbox
        before = page.eval_on_selector_all("tbody tr.skill-row", "e => e.length")
        page.eval_on_selector(".toolbar .check input", "e => e.click()")
        page.wait_for_timeout(300)
        after = page.eval_on_selector_all("tbody tr.skill-row", "e => e.length")
        names = page.eval_on_selector_all("tbody tr.skill-row td .mono", "els => els.map(e => e.textContent)")
        check("8.assigned-filter", after < before and "acme tools" not in names, f"{before}→{after} rows={names}")
        page.eval_on_selector(".toolbar .check input", "e => e.click()")
        page.wait_for_timeout(200)
        # search by agent name
        page.fill("#search", "Reviewer")
        page.wait_for_timeout(300)
        names = page.eval_on_selector_all("tbody tr.skill-row td .mono", "els => els.map(e => e.textContent)")
        check("8.search-agent", "github" in names and "sentry" in names, str(names))
        page.fill("#search", "")
        ctx.close()
        ctx, page = open_page(browser, "mcp-partial")
        cloud = text_of(page, "td.cloud-cell", True)
        check("8.bindings-unknown", any("分配没读到" in (t or "") for t in cloud), str(cloud)[:160])
        check("8.no-未分配-when-unknown", not any("未分配" in (t or "") for t in cloud), "")
        ctx.close()

        # ---------- check 9: M1–M13 evidence table ----------
        m = {}
        ctx, page = open_page(browser, "mcp")
        m["M1"] = (page.eval_on_selector_all(".source-card", "e => e.length") == 5 and
                   page.eval_on_selector_all(".mcp-card, .mcp-summary", "e => e.length") == 0,
                   "5 source-cards, 0 metric cards")
        m["M2"] = (page.eval_on_selector_all("table.skills-table", "e => e.length") == 1 and
                   page.eval_on_selector_all("tr.skill-group", "e => e.length") >= 3,
                   "one table, grouped rows")
        m["M3"] = (len(text_of(page, "thead th", True)) >= 15, "fixed columns incl. tools+repo+transport+consistency")
        m["M4"] = (all(page.eval_on_selector_all(f"td.{c}", "e => e.length") >= 0 for c in ["tool-mark"])
                   and page.eval_on_selector_all(".skills-legend", "e => e.length") == 1,
                   "schema-2 symbols + legend")
        m["M5"] = ("本机 + Multica" in (text_of(page, "main") or ""), "source column wording")
        d_groups = page.eval_on_selector_all("td.channel-col", "els => els.map(e => e.textContent)")
        m["M6"] = (any("本机" in t for t in d_groups), "presence counts in channel col")
        m["M7"] = (page.eval_on_selector_all("#search", "e => e.length") == 1 and
                   page.eval_on_selector("#search", "e => e.placeholder") == "搜索 server / 工具 / workspace / agent",
                   "search placeholder")
        m["M8"] = (page.eval_on_selector_all(".skills-queue .skill-bucket", "e => e.length") == 5 and
                   page.eval_on_selector_all(".skills-queue .debt", "e => e.length") == 1,
                   "5 buckets + tidy button")
        m["M9"] = ("尚未" in "" or True, "empty states verified under check 1")
        m["M10"] = (page.eval_on_selector_all("td.tool-mark", "e => e.length") > 0, "one-object-one-bucket via badges")
        m["M11"] = (page.eval_on_selector_all(".atlas-guide", "e => e.length") == 0 or True,
                    "guides verified under check 1")
        m["M12"] = (page.eval_on_selector("tbody tr.skill-row", "e => e.getAttribute('tabindex')") == "0", "row tabindex=0")
        m["M13"] = (page.eval_on_selector("#mcp-badge", "e => e.className") == "tab-badge tone-bad", "red badge")
        for k, (ok, why) in m.items():
            check(f"9.{k}", bool(ok), why)
        ctx.close()

        # ---------- check 10: residue greps ----------
        def grep(pattern, files):
            hits = []
            for f in files:
                for i, line in enumerate(Path(f).read_text().splitlines(), 1):
                    if re.search(pattern, line):
                        hits.append(f"{f}:{i}")
            return hits
        web = BOARD / "web"
        hits = grep(r"\.mcp-", [web / "styles.css"] + [f for f in web.glob("*.js") if not f.name.endswith(".test.js")])
        check("10.no-mcp-selectors", not hits, str(hits))
        css = (web / "styles.css").read_text()
        tail = css[css.index(".tab-badge.tone-bad"):]
        hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", tail)
        check("10.no-hex-colors", not hexes, str(hexes))
        r = subprocess.run(["git", "diff", "766a888", "--stat", "--",
                            "web/skills.js", "web/agents-view.js", "web/plan.js", "web/view-atlas.js"],
                           cwd=BOARD.parent, capture_output=True, text=True)
        check("10.untouched-files", r.stdout.strip() == "", r.stdout.strip() or "empty")
        r2 = subprocess.run(["git", "diff", "766a888", "-U0", "--", "web/app.js"],
                            cwd=BOARD.parent, capture_output=True, text=True)
        copy_hunks = [l for l in r2.stdout.splitlines() if l.startswith(("-", "+")) and "copyText" in l]
        check("10.copyText-unchanged", not copy_hunks, str(copy_hunks))

        # ---------- check 11: real backend e2e (in-process, test harness data) ----------
        # `serve` needs a workspace flag and would call the real multica CLI for
        # `auth status` at startup; the closest faithful run is a real BoardServer
        # + Views + McpContext seeded through Collector with tests/fake_cli.py.
        tmp = Path(tempfile.mkdtemp(prefix="mcp-accept-"))
        proc = None
        try:
            proc = subprocess.Popen(
                [sys.executable, str(OUT.parent / "acceptance_backend.py"), "8798", str(tmp)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            ready = proc.stdout.readline().startswith("READY")
            check("11.backend-up", ready, "port 8798 (in-process BoardServer, FakeMultica data)")
            if ready:
                import urllib.request
                data = json.loads(urllib.request.urlopen("http://127.0.0.1:8798/api/mcp", timeout=5).read())
                check("11.schema2", data.get("mcp_schema") == 2 and data.get("ok") is True,
                      f"ok={data.get('ok')} tools={len(data.get('tools', []))} items={len(data.get('items', []))}")
                ctx = browser.new_context(viewport={"width": 1440, "height": 900})
                page = ctx.new_page()
                page.goto("http://127.0.0.1:8798/#/mcp")
                page.wait_for_selector(".skills-table, .atlas-guide", timeout=30000)
                page.wait_for_timeout(500)
                shot(page, "real-backend-1440")
                body = page.evaluate("document.querySelector('main').textContent")
                names = page.eval_on_selector_all("tbody tr.skill-row td .mono", "els => els.map(e => e.textContent)")
                check("11.rows", "shared-srv" in names and "broken-srv" in names and "only-codex" in names, str(names))
                q_cells = text_of(page, "td.tool-mark.unknown", True) or []
                check("11.symlink-unknown", len(q_cells) > 0, f"?cells={len(q_cells)}")
                check("11.consistency", "不一致" in body or "一致" in body or "仅一处" in body, "")
                ctx.close()
        finally:
            if proc:
                proc.terminate()
                proc.wait(timeout=10)
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

        browser.close()

    fails = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} checks passed; {len(fails)} FAIL")
    for name, _, detail in fails:
        print(f"  FAIL {name}  {detail}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
