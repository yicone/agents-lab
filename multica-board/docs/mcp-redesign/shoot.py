"""Regenerate the ACM-378 comparison images from fixtures and the static mock only.

Usage (from multica-board/): python3 -m http.server 8799 --bind 127.0.0.1 &
                             python3 docs/mcp-redesign/shoot.py
Needs Python Playwright + local Chrome, and Pillow. Never points at a real backend.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8799"
OUT = Path(__file__).resolve().parent
SKILLS = f"{BASE}/web/?mock=1#/skills"
V1_MCP = f"{BASE}/web/?mock=mcp#/mcp"
MOCK = f"{BASE}/docs/mcp-redesign/mock.html"

PAIRS = [
    ("compare-1440", 1440, 900, "light", (SKILLS, False), (MOCK, False), "Skills（?mock=1）", "MCP 重做设计稿"),
    ("compare-1440-drawer", 1440, 900, "light", (SKILLS, True), (MOCK + "?drawer=1", False), "Skills 抽屉", "MCP 抽屉"),
    ("compare-1100", 1100, 900, "light", (SKILLS, False), (MOCK, False), "Skills 1100px", "MCP 1100px"),
    ("compare-390", 390, 844, "light", (SKILLS, False), (MOCK, False), "Skills 390px", "MCP 390px"),
    ("compare-dark-1440", 1440, 900, "dark", (SKILLS, False), (MOCK, False), "Skills 深色", "MCP 深色"),
    ("v1-vs-redesign-1440", 1440, 900, "light", (V1_MCP, False), (MOCK, False), "v1 MCP（?mock=mcp）", "MCP 重做设计稿"),
    ("copy-fail-1440", 1440, 900, "light", (MOCK + "?drawer=1&scroll=1", False), (MOCK + "?drawer=1&copyfail=1", False), "复制前", "复制失败 + 选中回退"),
]


async def shot(browser, url: str, width: int, height: int, scheme: str, open_drawer: bool) -> Image.Image:
    page = await browser.new_page(viewport={"width": width, "height": height}, color_scheme=scheme)
    await page.goto(url)
    await page.wait_for_timeout(1200)
    if open_drawer:
        await page.click(".skill-row")
        await page.wait_for_timeout(400)
    data = await page.screenshot()
    await page.close()
    return Image.open(BytesIO(data)).convert("RGB")


def font(size: int):
    for path in ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def side_by_side(left: Image.Image, right: Image.Image, left_label: str, right_label: str) -> Image.Image:
    bar, gap = 36, 16
    width = left.width + right.width + gap
    height = max(left.height, right.height) + bar
    canvas = Image.new("RGB", (width, height), (200, 204, 210))
    canvas.paste(left, (0, bar))
    canvas.paste(right, (left.width + gap, bar))
    draw = ImageDraw.Draw(canvas)
    label = font(18)
    draw.text((10, 8), left_label, fill=(20, 24, 30), font=label)
    draw.text((left.width + gap + 10, 8), right_label, fill=(20, 24, 30), font=label)
    return canvas


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome")
        for name, width, height, scheme, (left_url, left_drawer), (right_url, right_drawer), left_label, right_label in PAIRS:
            left = await shot(browser, left_url, width, height, scheme, left_drawer)
            right = await shot(browser, right_url, width, height, scheme, right_drawer)
            side_by_side(left, right, left_label, right_label).save(OUT / f"{name}.png", optimize=True)
            print(name)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
