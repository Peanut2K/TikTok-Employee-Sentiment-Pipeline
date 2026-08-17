"""Look at what the search page actually renders. Diagnostic only.

    .venv/bin/python debug_page.py
"""

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(
        storage_state=str(HERE / "tiktok_state.json"),
        viewport={"width": 1280, "height": 900},
        locale="th-TH",
    )
    page = ctx.new_page()
    page.goto("https://www.tiktok.com/search/video?q=ลาออกเซเว่น", wait_until="domcontentloaded")
    time.sleep(8)

    print("url:", page.url)
    print("title:", page.title())

    # Is this even the search page, or a wall?
    body = page.locator("body").inner_text()[:600]
    print("\n--- body text (first 600 chars) ---")
    print(body)

    print("\n--- link counts by selector ---")
    for sel in [
        'a[href*="/video/"]',
        'a[href*="/@"]',
        '[data-e2e="search_top-item"]',
        '[data-e2e="search-card-video-caption"]',
        '[data-e2e="search_video-item"]',
        "div[class*='DivItemContainer']",
        "video",
    ]:
        print(f"  {sel:<45} {page.locator(sel).count()}")

    print("\n--- first 10 hrefs containing @ ---")
    for a in page.locator('a[href*="/@"]').all()[:10]:
        print("  ", a.get_attribute("href"))

    page.screenshot(path=str(HERE / "debug.png"), full_page=False)
    print(f"\nscreenshot -> debug.png")
    (HERE / "debug.html").write_text(page.content(), encoding="utf-8")
    print("html -> debug.html")

    input("\nPress Enter to close browser... ")
    browser.close()
