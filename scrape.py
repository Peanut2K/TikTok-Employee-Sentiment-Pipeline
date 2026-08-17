"""TikTok seed-account scraper for the 7-11 employee content project.

Opens a real headful browser, lets you log in by hand once, then walks the
search pages for each keyword and collects the accounts behind the results.
Writes straight to the team Google Sheet.

    .venv/bin/python scrape.py --login     # once, to save the session
    .venv/bin/python scrape.py             # collect
    .venv/bin/python scrape.py --dry-run   # collect, print, don't touch the Sheet

ponytail: no CLI framework, no config file, no class hierarchy. argparse and
two functions. Add structure when a second scraper shows up.
"""

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
STATE = HERE / "tiktok_state.json"
SERVICE_ACCOUNT = HERE / "aiwriteupload-a58b02f9f539.json"
SHEET_KEY = "1ipeGci4ONpxeU4vc5QlaXAnqFAViB6xn7_DKHs790os"

# TikTok's search caps out around 30-40 results per query no matter how far you
# scroll, so coverage comes from many distinct queries, not deeper scrolling.
# Hashtags and phrasings below are lifted from captions the earlier runs found.
KEYWORDS = [
    "ลาออกเซเว่น",
    "พนักงานเซเว่น",
    "พนักงาน 7-11",
    "กะดึกเซเว่น",
    "ชีวิตพนักงานเซเว่น",
    "ลาออกจากเซเว่น",
    "เด็กเซเว่น",
    "ทํางานเซเว่น",
    "อดีตพนักงานเซเว่น",
    "พนักงาน711",
    "สาวเซเว่น",
    "ลาก่อนเซเว่น",
    "ผู้จัดการเซเว่น",
    "ผู้ช่วยผู้จัดการเซเว่น",
    "พนักงานเซเว่นเท่านั้นที่จะเข้าใจ",
    "ประสบการณ์ทํางานเซเว่น",
    "วันสุดท้ายเซเว่น",
    "เซเว่นอีเลฟเว่นพนักงาน",
    "ทํางานเซเว่นเหนื่อยมาก",
    "บ่นงานเซเว่น",
    "ลูกค้าเซเว่น",
    "เข้ากะเซเว่น",
    "สมัครงานเซเว่น",
    "เงินเดือนพนักงานเซเว่น",
    "cpall พนักงาน",
    "หนุ่มเซเว่น",
    "ชีวิตเด็กเซเว่น",
    "ลาออกงานประจําเซเว่น",
    # Round 2: the queries above ran dry, so these come at the same people from
    # different angles — shift slang, store-floor tasks, and how staff actually
    # caption their own clips rather than how an outsider would phrase a search.
    "เด็กเซเว่นเท่านั้นที่จะเข้าใจ",
    "พนักงานเซเว่นกะดึก",
    "ชีวิตในเซเว่น",
    "เพื่อนร่วมงานเซเว่น",
    "ร้านเซเว่นสาขา",
    "เติมของเซเว่น",
    "นับสต๊อกเซเว่น",
    "ปิดร้านเซเว่น",
    "ลูกค้าร้านเซเว่น",
    "โดนด่าเซเว่น",
    "เหนื่อยเซเว่น",
    "ท้อเซเว่น",
    "เซเว่นอีเลฟเว่น พนักงาน",
    "แคชเชียร์เซเว่น",
    "ยูนิฟอร์มเซเว่น",
    "ชุดพนักงานเซเว่น",
    "สาขาเซเว่น พนักงาน",
    "งานเซเว่น",
    "ทำงานร้านสะดวกซื้อ",
    "พนักงานร้านสะดวกซื้อ",
    "ออกจากเซเว่น",
    "ไม่ทำเซเว่นแล้ว",
    "เซเว่นลาออก",
    "ประสบการณ์เซเว่น",
    "เล่าเรื่องเซเว่น",
    "ชีวิตกะดึก",
    "cpall",
    "เด็กปั๊มเด็กเซเว่น",
]

TARGET = 200

# One search result. TikTok ships obfuscated class names, so anchor on the
# data-e2e hooks it keeps stable for its own tooling.
CARD = 'div[class*="DivItemContainer"], [data-e2e="search_top-item"]'
CAPTION = '[data-e2e="search-card-video-caption"]'

# Playwright's own UA advertises HeadlessChrome and gets a stripped-down page.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

HEADERS = [
    "username",
    "profile_url",
    "category",
    "note",
    "source_keyword",
    "video_url",
    "caption",
]

# Caption keywords that sort an account into a content bucket. First match wins,
# so the most specific bucket is listed first.
# ponytail: crude keyword vote, not a classifier. The brief says manual
# cleansing happens downstream and misses are acceptable. Upgrade to an LLM
# pass over `caption` if the buckets turn out too noisy to clean by hand.
CATEGORIES = [
    ("ลาออกแล้ว", [
        "ลาออก", "ออกจากงาน", "ออกจากเซเว่น", "วันสุดท้าย", "ไม่ทำแล้ว", "ยื่นใบลาออก",
        "ลาออกละ", "ลาก่อน", "อดีตพนักงาน", "จบกัน", "บ๊ายบาย", "บ้ายบาย", "ไว้เจอกันใหม่",
        "lastworkingday", "last working day", "ขอตัวก่อน", "ขอบาย", "เหลือแค่ความทรงจำ",
        "นับถอยหลัง", "ขอบคุณประสบการณ์", "ถึงจุดอิ่มตัว", "เรียนต่อ", "งานใหม่",
    ]),
    ("บ่นงาน", [
        "บ่น", "เหนื่อย", "ท้อ", "ลูกค้า", "กะดึก", "ด่า", "โดนด่า", "งานหนัก", "หัวหน้า",
        "โหดมาก", "ดราม่า", "เครียด", "ทนไม่ไหว", "ระบาย", "เซ็ง", "หมดไฟ",
    ]),
    ("รีวิวชีวิตพนักงาน", [
        "รีวิว", "ชีวิตพนักงาน", "วันนึงของ", "เงินเดือน", "สวัสดิการ", "สมัครงาน",
        "เล่าให้ฟัง", "ประสบการณ์", "แชร์ประสบการณ์", "เด็กเซเว่น", "สาวเซเว่น",
        "ผู้จัดการ", "ผู้ช่วยผู้จัดการ", "มนุษย์เงินเดือน", "เท่านั้นที่จะเข้าใจ",
        "ชีวิตการทำงาน", "งานประจํา", "งานประจำ",
    ]),
]

# 7-11 in the wild: เซเว่น, 7-11, 7/11, เซเวน, ...
SEVEN = re.compile(r"เซเว่?น|เซเวน|7\s*[-/]?\s*11|seven\s*eleven", re.IGNORECASE)

# Mentioning 7-11 isn't enough — the search also returns snack reviews, product
# ads and customers filming a store. Keep a card only if it also reads like it
# comes from someone who works or worked there.
EMPLOYEE = re.compile(
    r"พนักงาน|ลาออก|กะดึก|กะเช้า|เข้ากะ|ออกกะ|เด็กเซเว่?น|ทํางาน|ทำงาน|"
    r"ลูกค้า|เงินเดือน|สมัครงาน|หัวหน้า|ผจก|ผู้จัดการ|สาขา|ชีวิตพนักงาน|"
    r"เพื่อนร่วมงาน|โดนด่า|ยูนิฟอร์ม|ชุดพนักงาน",
    re.IGNORECASE,
)

# A customer filming staff is not a source account, even with employee words.
CUSTOMER = re.compile(r"รีวิว(?!ชีวิต|งาน)|เมนูใหม่|ของใหม่|มาลง|ลดราคา|โปรโมชั่น|ชวนกิน|กินอะไรดี|อร่อย", re.IGNORECASE)

# Competitors are explicitly out of scope for this list.
COMPETITORS = re.compile(r"โลตัส|lotus|บิ๊กซี|big\s*c|แม็คโคร|makro|ท็อปส์|tops|แฟมิลี่มาร์ท|family\s*mart|cj\s*more|ซีเจ", re.IGNORECASE)


def categorize(caption):
    """Bucket a caption. Returns (category, note) — note flags what needs a human."""
    if COMPETITORS.search(caption):
        return "", "อาจเป็นร้านเจ้าอื่น - ตรวจสอบ"
    for name, words in CATEGORIES:
        if any(w in caption for w in words):
            return name, ""
    return "", "ยังไม่จัดหมวด - ตรวจสอบ"


def login():
    """Open a browser so you can log into TikTok by hand, then save the session."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto("https://www.tiktok.com/login")
        print("\n  Log into TikTok in the browser window that just opened.")
        print("  Once you can see your feed, come back here and press Enter.\n")
        input("  Press Enter when logged in... ")
        ctx.storage_state(path=str(STATE))
        browser.close()
    print(f"Session saved to {STATE.name}. You can run the scraper now.")


def scrape(target, per_keyword_scrolls, headless):
    """Walk each keyword's search page and collect the accounts behind the results."""
    if not STATE.exists():
        sys.exit("No saved session. Run:  .venv/bin/python scrape.py --login")

    # Seed from previous runs so repeated passes accumulate toward the target
    # instead of re-collecting the same accounts from scratch.
    found = {}  # username -> row dict, so the same account can't land twice
    backup = HERE / "accounts.json"
    if backup.exists():
        found = {r["username"]: r for r in json.loads(backup.read_text(encoding="utf-8"))}
        print(f"resuming from {len(found)} accounts already collected")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            storage_state=str(STATE),
            viewport={"width": 1280, "height": 900},
            locale="th-TH",
            user_agent=USER_AGENT,
        )
        page = ctx.new_page()

        for keyword in KEYWORDS:
            if len(found) >= target:
                break
            print(f"\n[{keyword}] searching...", flush=True)
            page.goto(
                f"https://www.tiktok.com/search/video?q={keyword}",
                wait_until="domcontentloaded",
            )
            # Firing queries back to back is what trips the captcha; a pause
            # between keywords costs minutes and saves the whole session.
            time.sleep(random.uniform(8, 14))

            if blocked(page, interactive=not headless):
                time.sleep(2)  # let results settle after the wall clears

            before = len(found)
            stale = 0
            for i in range(per_keyword_scrolls):
                if len(found) >= target:
                    break
                seen = len(found)
                harvest(page, keyword, found)
                # Search results cap out; once scrolling stops yielding anyone
                # new, this query is spent and the next keyword is worth more.
                stale = 0 if len(found) > seen else stale + 1
                if stale >= 4:
                    # Distinguish a spent query from a wall thrown up mid-run.
                    if not page.locator(CARD).count() and blocked(page, interactive=not headless):
                        stale = 0
                        continue  # wall cleared — this keyword still has results
                    print(f"  exhausted after {i + 1} scrolls", flush=True)
                    break
                page.mouse.wheel(0, random.randint(2000, 3500))
                time.sleep(random.uniform(3.0, 6.0))

            harvest(page, keyword, found)
            print(f"[{keyword}] +{len(found) - before} new (total {len(found)})", flush=True)
            # Checkpoint per keyword so a captcha or crash never costs the run.
            backup.write_text(
                json.dumps(list(found.values()), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        browser.close()

    return list(found.values())


# A captcha renders an empty result list, which is indistinguishable from "no
# more results" unless you look for it. Silently treating it as the latter is
# how a blocked run reports itself as a complete one.
WALL = re.compile(r"ลากแถบเลื่อน|เลื่อนชิ้นส่วน|Drag the slider|Verify to continue|ยืนยันตัวตน|Log in to TikTok")


def blocked(page, interactive=False):
    """Handle a captcha or login wall. Returns True if it was cleared.

    With a visible browser there's a human who can drag the slider, so wait for
    them instead of throwing away the run.
    """
    try:
        body = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return False
    if not (WALL.search(body) or "/login" in page.url):
        return False

    page.screenshot(path=str(HERE / "blocked.png"))

    if not interactive:
        sys.exit(
            "\nTikTok is showing a captcha / login wall — it rate-limited this session.\n"
            "Nothing more can be collected right now. What works:\n"
            "  1. Re-run with --show and solve the slider by hand (results so far are kept).\n"
            "  2. Wait a few hours, then re-run — it resumes from accounts.json.\n"
            "  3. Move to the Apify path (step 3), which doesn't use your session.\n"
            "Screenshot of the wall saved to blocked.png\n"
        )

    print("\n" + "=" * 60)
    print("  CAPTCHA — solve the slider in the browser window.")
    print("  The run continues on its own once the results come back.")
    print("=" * 60, flush=True)

    # Poll rather than prompting: the wall often clears the moment the slider
    # lands, and an input() would sit there waiting for a keypress instead.
    for _ in range(60):  # ~5 minutes
        time.sleep(5)
        try:
            if page.locator(CARD).count():
                print("  cleared, resuming\n", flush=True)
                return True
        except Exception:
            pass
    sys.exit("Captcha not solved in time. Re-run to resume from accounts.json.")


def harvest(page, keyword, found):
    """Pull every /@user/video/ link currently rendered into `found`."""
    for card in page.locator(CARD).all():
        try:
            link = card.locator('a[href*="/video/"]').first
            href = link.get_attribute("href") or ""
            m = re.search(r"tiktok\.com/@([\w.\-]+)/video/(\d+)", href)
            if not m:
                continue
            username = m.group(1)
            if username in found:
                continue

            # The caption is its own element inside the card. Walking up the DOM
            # from the link instead lands on the like-count wrapper.
            caption = ""
            try:
                cap = card.locator(CAPTION).first
                if cap.count():
                    caption = (cap.inner_text(timeout=1500) or "").replace("\n", " ").strip()
            except Exception:
                pass

            # Keyword already targets 7-11, but search bleeds into unrelated
            # results. Require the place AND an employee angle, and drop the
            # product/menu reviews that ride the same hashtags.
            haystack = f"{caption} {username}"
            if not SEVEN.search(haystack) or not EMPLOYEE.search(haystack):
                continue
            if CUSTOMER.search(caption):
                continue

            category, note = categorize(caption)
            found[username] = {
                "username": username,
                "profile_url": f"https://www.tiktok.com/@{username}",
                "category": category,
                "note": note,
                "source_keyword": keyword,
                "video_url": f"https://www.tiktok.com/@{username}/video/{m.group(2)}",
                "caption": caption[:300],
            }
        except Exception:
            continue


def write_sheet(rows, gc=None):
    """Replace the sheet contents with `rows`, keeping the header and dropdown."""
    if gc is None:
        creds = Credentials.from_service_account_file(
            str(SERVICE_ACCOUNT),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_KEY)
    ws = sh.sheet1

    ws.clear()
    ws.update(
        values=[HEADERS] + [[r[h] for h in HEADERS] for r in rows],
        range_name="A1",
    )
    ws.format("A1:G1", {"textFormat": {"bold": True}})
    ws.freeze(rows=1)

    # Dropdown on `category` so the intern cleaning this can't invent buckets.
    sh.batch_update({
        "requests": [{
            "setDataValidation": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,
                    "startColumnIndex": 2,
                    "endColumnIndex": 3,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": "บ่นงาน"},
                            {"userEnteredValue": "ลาออกแล้ว"},
                            {"userEnteredValue": "รีวิวชีวิตพนักงาน"},
                        ],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        }]
    })
    return sh.url


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--login", action="store_true", help="save a TikTok session, then exit")
    ap.add_argument("--dry-run", action="store_true", help="print results instead of writing the Sheet")
    ap.add_argument("--target", type=int, default=TARGET, help=f"how many accounts to collect (default {TARGET})")
    ap.add_argument("--scrolls", type=int, default=25, help="scrolls per keyword (default 25)")
    ap.add_argument("--show", action="store_true", help="show the browser window (use when a captcha needs clicking)")
    args = ap.parse_args()

    if args.login:
        login()
        return

    rows = scrape(args.target, args.scrolls, headless=not args.show)
    print(f"\nCollected {len(rows)} accounts.")

    if not rows:
        sys.exit("Nothing collected. TikTok probably showed a captcha or the session expired.")

    out = HERE / "accounts.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Backup written to {out.name}")

    uncategorized = sum(1 for r in rows if not r["category"])
    print(f"Needs manual review: {uncategorized}/{len(rows)}")

    if args.dry_run:
        for r in rows[:20]:
            print(f"  @{r['username']:<24} {r['category'] or '?':<18} {r['caption'][:60]}")
        print("\n--dry-run: Sheet untouched.")
        return

    print(f"Sheet updated: {write_sheet(rows)}")


if __name__ == "__main__":
    main()
