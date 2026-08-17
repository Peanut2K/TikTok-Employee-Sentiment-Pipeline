"""Step 4 — turn the scraped clips and comments into dashboard-ready rows.

One Gemini call per clip does transcription and classification together: the
model gets the actual video, so it reads speech, on-screen text, and what is
happening in frame in one pass. That replaces the step-3 cover OCR, which only
ever saw the first frame and mangled Thai tone marks.

    .venv/bin/python analyze.py --estimate        # cost + volume, spends nothing
    .venv/bin/python analyze.py --limit 5         # try a few first
    .venv/bin/python analyze.py                   # clips, then comments
    .venv/bin/python analyze.py --comments-only   # skip the video pass
    .venv/bin/python analyze.py --export          # build the dashboard files

Reads out/seed_100_metadata.json + out/comments_raw.json.
Writes out/clips_analyzed.json, out/comments_classified.json,
out/dashboard.json, out/clips.csv, out/comments.csv, out/analysis_report.json.

Resumable: every clip and every comment batch is saved as it lands, so a crash
or a rate-limit wall keeps what came before. Rerunning skips finished work.
"""

import argparse
import csv
import json
import random
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "out"
VIDEOS = OUT / "seed_100_metadata.json"
COMMENTS = OUT / "comments_raw.json"
MEDIA = OUT / "media"

CLIPS_RESULT = OUT / "clips_analyzed.json"
COMMENTS_RESULT = OUT / "comments_classified.json"
REPORT = OUT / "analysis_report.json"
RAW_LOG = OUT / "gemini_raw.jsonl"

MODEL = "gemini-3.7-flash"
COMMENT_BATCH = 50
# TikTok is the bottleneck, not Gemini: it starts 403ing above ~2 concurrent
# downloads. Comments have no download step, so they run wider.
CLIP_WORKERS = 2
COMMENT_WORKERS = 4

# The story names five themes plus อื่นๆ. เพื่อนร่วมงาน is added because the
# first real batch showed people quitting over colleagues, not over the job —
# without it those clips all collapsed into อื่นๆ and said nothing.
THEMES = ["ค่าแรง", "workload", "หัวหน้างาน", "เพื่อนร่วมงาน", "ลูกค้า", "ตารางกะ", "อื่นๆ"]
INTENTS = ["บ่น", "คิดจะลาออก", "ลาออกแล้ว", "ไม่เกี่ยว"]
SENTIMENTS = ["เห็นด้วย", "ไม่เห็นด้วย", "อื่นๆ"]
# The schema rejects "" as an enum member, so unknown needs a real label.
NO_THEME = "ระบุไม่ได้"


# --- credentials -------------------------------------------------------------

def google_key():
    """Environment first, then .env. Never logged."""
    import os
    if os.environ.get("GOOGLE_API_KEY"):
        return os.environ["GOOGLE_API_KEY"]
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                if k.strip() == "GOOGLE_API_KEY":
                    return v.strip().strip('"').strip("'")
    sys.exit("GOOGLE_API_KEY not found in environment or .env")


def cookie_file():
    """yt-dlp needs a logged-in session; TikTok 403s anonymous extraction.

    tiktok_state.json is the Playwright state the user created themselves with
    --login in step 2. Converted here rather than stored twice.
    """
    state = HERE / "tiktok_state.json"
    if not state.exists():
        return None
    dest = OUT / ".tt_cookies.txt"
    cookies = json.loads(state.read_text(encoding="utf-8")).get("cookies", [])
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        dom = c.get("domain", "")
        if "tiktok" not in dom:
            continue
        expires = int(c["expires"]) if c.get("expires", -1) > 0 else 0
        lines.append("\t".join([
            dom, "TRUE" if dom.startswith(".") else "FALSE", c.get("path", "/"),
            "TRUE" if c.get("secure") else "FALSE", str(expires),
            c["name"], c["value"],
        ]))
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(dest)


# --- schemas -----------------------------------------------------------------

CLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "transcript": {"type": "string"},
        "on_screen_text": {"type": "string"},
        "themes": {"type": "array", "items": {"type": "string", "enum": THEMES}},
        "intent_level": {"type": "string", "enum": INTENTS},
        "notable_quote": {"type": "string"},
        "relevant": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["transcript", "on_screen_text", "themes", "intent_level",
                 "notable_quote", "relevant", "confidence"],
}

COMMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "sentiment": {"type": "string", "enum": SENTIMENTS},
                    "theme": {"type": "string", "enum": THEMES + [NO_THEME]},
                },
                "required": ["i", "sentiment", "theme"],
            },
        }
    },
    "required": ["results"],
}


def clip_prompt(item):
    return f"""คลิป TikTok จากพนักงานร้านสะดวกซื้อ 7-Eleven ในไทย

caption: {item.get('caption') or item.get('scraped_caption') or '(ไม่มี)'}

งาน:
1. transcript — ถอดเสียงพูดในคลิปเป็นภาษาไทย ถ้าไม่มีเสียงพูดใส่ ""
2. on_screen_text — ข้อความที่ขึ้นบนจอทั้งหมด ถ้าไม่มีใส่ ""
3. themes — เลือกจากรายการ เลือกได้หลายอัน ดูจากเนื้อหาที่พูดหรือขึ้นจอจริง
   ค่าแรง = เงินเดือน โอที ค่าตอบแทน สวัสดิการ ประกัน
   workload = งานหนัก งานเยอะ คนไม่พอ ทำหลายหน้าที่
   หัวหน้างาน = ผู้จัดการ หัวหน้า เจ้าของร้าน สั่งงาน ถูกด่า
   เพื่อนร่วมงาน = เพื่อนร่วมกะ ทีม การเมืองในร้าน นินทา
   ลูกค้า = ลูกค้าหยาบคาย ปัญหาหน้าเคาน์เตอร์
   ตารางกะ = กะดึก เข้ากะ ตารางงาน วันหยุด เวลาพัก
   ใช้ "อื่นๆ" เฉพาะตอนที่ไม่เข้าหมวดไหนเลยจริงๆ ถ้าเข้าได้แม้บางส่วนให้เลือกหมวดนั้น
4. intent_level — บ่น / คิดจะลาออก / ลาออกแล้ว / ไม่เกี่ยว
5. notable_quote — ประโยคเด็ดที่ยกไปโชว์บน dashboard ได้
   ต้องเป็นคำพูดหรือข้อความที่ปรากฏในคลิปจริงเท่านั้น ห้ามแต่งขึ้นเอง
   ถ้าไม่มีประโยคที่เด่นพอ ใส่ ""
6. relevant — เกี่ยวกับชีวิตการทำงานร้านสะดวกซื้อไหม (false = คลิปหลุดหมวด เช่น ดูดวง ขายของ)
7. confidence — ความมั่นใจในการจัด intent_level และ themes"""


def comment_prompt(video_ctx, comments):
    listed = "\n".join(f"{i}. {c}" for i, c in enumerate(comments))
    return f"""คอมเมนต์ใต้คลิป TikTok ของพนักงานร้านสะดวกซื้อ 7-Eleven

บริบทคลิป: {video_ctx}

จัดหมวดคอมเมนต์แต่ละอัน ตอบครบทุกหมายเลข:
- sentiment: เห็นด้วย / ไม่เห็นด้วย / อื่นๆ
  เห็นด้วย = เห็นด้วยกับคนโพสต์ เจอเหมือนกัน ให้กำลังใจ ร่วมบ่น
  ไม่เห็นด้วย = เถียง ตำหนิคนโพสต์ บอกว่าคิดผิด
  อื่นๆ = ถามคำถาม พูดเล่น อีโมจิล้วน ไม่เกี่ยว
- theme: {' / '.join(THEMES)} หรือ "{NO_THEME}" ถ้าคอมเมนต์ไม่ได้พูดถึงเรื่องไหนชัดเจน

คอมเมนต์:
{listed}"""


# --- gemini ------------------------------------------------------------------

def call_gemini(client, contents, schema, tag, tries=5):
    """One structured call with backoff. 429 and 503 are the ones worth retrying."""
    from google.genai import errors
    for attempt in range(tries):
        try:
            r = client.models.generate_content(
                model=MODEL, contents=contents,
                config={"response_mime_type": "application/json",
                        "response_schema": schema},
            )
            with RAW_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"tag": tag, "text": r.text}, ensure_ascii=False) + "\n")
            usage = r.usage_metadata
            return json.loads(r.text), {
                "in": usage.prompt_token_count or 0,
                "out": usage.candidates_token_count or 0,
            }
        except errors.APIError as e:
            if e.code not in (429, 500, 503) or attempt == tries - 1:
                raise
            wait = min(60, 4 * 2 ** attempt) + random.uniform(0, 2)
            print(f"    {tag}: {e.code}, retry in {wait:.0f}s")
            time.sleep(wait)
    raise RuntimeError("unreachable")


# --- clips -------------------------------------------------------------------

BROWSER = None      # set by --browser; yt-dlp reads its cookie store directly
SEED = HERE / "filtered_100.json"


def seed_order():
    """Row number in the sheet, keyed by video id. 1-based, as a person counts."""
    if not SEED.exists():
        return {}
    rows = json.loads(SEED.read_text(encoding="utf-8"))
    return {r["video_url"].rsplit("/", 1)[-1]: (i, r["username"])
            for i, r in enumerate(rows, 1)}


def media_name(vid, order=None):
    """003_noonanduanglada.mp4 - readable next to the sheet, sorts in sheet order.

    Falls back to the bare video id for anything not in the seed, so a file is
    never silently misfiled under someone else's name.
    """
    order = seed_order() if order is None else order
    if vid not in order:
        return f"{vid}.mp4"
    i, user = order[vid]
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", user)
    return f"{i:03d}_{safe}.mp4"


def download_clip(url, dest, cookies, tries=2):
    """Smallest stream that still carries audio.

    Kept deliberately short. TikTok 403s are throttling, and grinding through
    six long backoffs inside a worker stalls the whole pass behind one clip -
    the failed ones are cheaper to sweep up in a second run, since results are
    saved per clip and a rerun only retries what failed.
    """
    import yt_dlp
    # The video CDN rejects requests without a tiktok.com Referer and a
    # browser User-Agent - that, not the session, was behind a fifth of these
    # coming back 403. Verified on a clip that had failed every prior attempt.
    opts = {"quiet": True, "no_warnings": True, "outtmpl": str(dest),
            "format": "worst[acodec!=none]/worst", "noprogress": True,
            "http_headers": {
                "Referer": "https://www.tiktok.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
            }}
    # A logged-in session sees clips an anonymous one 403s on - tiktok_state.json
    # turned out to hold only visitor cookies, no sessionid, which is why a
    # fifth of these failed while the same URLs opened fine in a browser.
    if BROWSER:
        opts["cookiesfrombrowser"] = (BROWSER, None, None, None)
    elif cookies:
        opts["cookiefile"] = cookies
    last = None
    for attempt in range(tries):
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                y.download([url])
            return
        except Exception as e:
            last = e
            if attempt < tries - 1:
                time.sleep(3 * (attempt + 1) + random.uniform(0, 2))
    raise last


def analyze_clip(client, item, cookies, keep_media):
    url = item["video_url"]
    vid = url.rsplit("/", 1)[-1]
    dest = MEDIA / media_name(vid)
    from google.genai import types
    try:
        if not dest.exists():
            download_clip(url, dest, cookies)
        data, usage = call_gemini(
            client,
            [types.Part.from_bytes(data=dest.read_bytes(), mime_type="video/mp4"),
             clip_prompt(item)],
            CLIP_SCHEMA, vid,
        )
        if not keep_media:
            dest.unlink(missing_ok=True)
        return {**data, "status": "ok", "error": "", **{f"tokens_{k}": v for k, v in usage.items()}}
    except Exception as e:
        # A clip that won't process is recorded as a failure, not guessed at.
        # Step 3 established the rule: a blank is honest, a fabrication is not.
        return {"transcript": "", "on_screen_text": "", "themes": [],
                "intent_level": "", "notable_quote": "", "relevant": None,
                "confidence": "", "status": "failed", "error": str(e)[:200],
                "tokens_in": 0, "tokens_out": 0}


def fetch_media(videos, cookies, workers):
    """Download the mp4s and stop. No Gemini calls, no analysis touched.

    The clips are already analyzed; this is only for keeping the source video
    around. Anything already on disk is skipped, so it is safe to rerun after
    a batch of 403s.
    """
    MEDIA.mkdir(parents=True, exist_ok=True)
    order = seed_order()
    todo = [v for v in videos
            if not (MEDIA / media_name(v["video_url"].rsplit("/", 1)[-1], order)).exists()]
    if not todo:
        print(f"media: all {len(videos)} already on disk")
        return

    print(f"media: {len(todo)} to download ({len(videos) - len(todo)} on disk)")
    n, ok = [0], [0]

    def work(v):
        vid = v["video_url"].rsplit("/", 1)[-1]
        try:
            download_clip(v["video_url"], MEDIA / media_name(vid, order), cookies)
            ok[0] += 1
            mark = "ok "
            detail = ""
        except Exception as e:
            mark, detail = "FAIL", str(e)[:60]
        n[0] += 1
        print(f"  [{n[0]}/{len(todo)}] {mark} {v['username'][:20]:<20} {detail}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, todo))

    size = sum(f.stat().st_size for f in MEDIA.glob("*.mp4")) / 1e6
    print(f"\n{ok[0]}/{len(todo)} downloaded — {MEDIA} now holds "
          f"{len(list(MEDIA.glob('*.mp4')))} files, {size:.0f} MB")


def fetch_via_apify(videos, order):
    """Last resort for clips this machine cannot download.

    TikTok blocked this IP after a day of scraping, and yt-dlp can no longer
    fetch anything - even clips that worked earlier. Apify runs from its own
    IPs and stores the mp4 in its key-value store, which is served from
    api.apify.com and therefore reachable. Costs about $0.004 per clip.
    """
    from apify_client import ApifyClient
    from apify_scrape import apify_token, run_info

    todo = [v for v in videos
            if not (MEDIA / media_name(v["video_url"].rsplit("/", 1)[-1], order)).exists()]
    if not todo:
        return print("apify: nothing missing")

    MEDIA.mkdir(parents=True, exist_ok=True)
    client = ApifyClient(apify_token())
    print(f"apify: requesting {len(todo)} clips (~${len(todo) * 0.004:.2f})")

    got = 0
    # Chunked so one bad batch doesn't cost the whole set.
    for i in range(0, len(todo), 10):
        part = todo[i:i + 10]
        run = client.actor("clockworks/tiktok-scraper").call(
            run_input={"postURLs": [v["video_url"] for v in part],
                       "shouldDownloadVideos": True, "shouldDownloadCovers": False})
        info = run_info(run)
        print(f"  run {info['run_id']} {info['status']}")
        for item in client.dataset(info["dataset_id"]).iterate_items():
            urls = item.get("mediaUrls") or []
            vid = str(item.get("id") or "")
            if not urls or not vid:
                print(f"    {vid or '?'}: no media returned")
                continue
            dest = MEDIA / media_name(vid, order)
            try:
                req = urllib.request.Request(urls[0], headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = r.read()
                if data[4:8] != b"ftyp":
                    raise RuntimeError("not an mp4")
                dest.write_bytes(data)
                got += 1
                print(f"    {dest.name} {len(data) / 1e6:.2f}MB")
            except Exception as e:
                print(f"    {vid} FAILED: {str(e)[:70]}")

    print(f"\n{got}/{len(todo)} recovered via Apify")


def run_clips(client, videos, cookies, keep_media, workers):
    done = json.loads(CLIPS_RESULT.read_text(encoding="utf-8")) if CLIPS_RESULT.exists() else {}
    todo = [v for v in videos
            if v["video_url"].rsplit("/", 1)[-1] not in done
            or done[v["video_url"].rsplit("/", 1)[-1]].get("status") == "failed"]
    if not todo:
        print(f"clips: all {len(done)} already analyzed")
        return done

    MEDIA.mkdir(parents=True, exist_ok=True)
    print(f"clips: {len(todo)} to process ({len(done)} done)")
    n = [0]

    def work(item):
        vid = item["video_url"].rsplit("/", 1)[-1]
        res = analyze_clip(client, item, cookies, keep_media)
        n[0] += 1
        mark = "ok " if res["status"] == "ok" else "FAIL"
        detail = res["error"][:60] if res["status"] == "failed" else \
            f"{res['intent_level']:<12} {'/'.join(res['themes'])[:30]}"
        print(f"  [{n[0]}/{len(todo)}] {mark} {item['username'][:18]:<18} {detail}")
        return vid, {"video_url": item["video_url"], "username": item["username"], **res}

    # as_completed, not map: map yields in input order, so one slow clip holds
    # back every finished result behind it and a kill loses all of them.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(work, item) for item in todo]):
            vid, res = fut.result()
            done[vid] = res
            CLIPS_RESULT.write_text(json.dumps(done, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    return done


# --- comments ----------------------------------------------------------------

def batch_key(video_url, start):
    return f"{video_url.rsplit('/', 1)[-1]}#{start}"


def run_comments(client, comments, clips, workers):
    done = json.loads(COMMENTS_RESULT.read_text(encoding="utf-8")) if COMMENTS_RESULT.exists() else {}

    by_video = {}
    for c in comments:
        by_video.setdefault(c.get("videoWebUrl") or c.get("submittedVideoUrl", ""), []).append(c)

    batches = []
    for url, group in by_video.items():
        vid = url.rsplit("/", 1)[-1]
        clip = clips.get(vid, {})
        ctx = (clip.get("notable_quote") or clip.get("on_screen_text")
               or clip.get("transcript") or "(ไม่มีข้อมูลคลิป)")[:300]
        for start in range(0, len(group), COMMENT_BATCH):
            if batch_key(url, start) not in done:
                batches.append((url, ctx, start, group[start:start + COMMENT_BATCH]))

    if not batches:
        print(f"comments: all {sum(len(v) for v in done.values())} already classified")
        return done

    print(f"comments: {len(batches)} batches of <={COMMENT_BATCH}")
    n = [0]

    def work(job):
        url, ctx, start, group = job
        texts = [c.get("text", "") for c in group]
        key = batch_key(url, start)
        try:
            data, _ = call_gemini(client, comment_prompt(ctx, texts), COMMENT_SCHEMA, key)
            by_i = {r["i"]: r for r in data.get("results", [])}
            out = []
            for i, c in enumerate(group):
                r = by_i.get(i)
                out.append({
                    "cid": c.get("cid", ""),
                    "video_url": url,
                    "text": c.get("text", ""),
                    "likes": int(c.get("diggCount") or 0),
                    "sentiment": r["sentiment"] if r else "",
                    "theme": r["theme"] if r else "",
                    "status": "ok" if r else "missing",
                })
            got = sum(1 for o in out if o["status"] == "ok")
            n[0] += 1
            print(f"  [{n[0]}/{len(batches)}] {key:<28} {got}/{len(group)}")
            return key, out
        except Exception as e:
            n[0] += 1
            print(f"  [{n[0]}/{len(batches)}] {key:<28} FAIL {str(e)[:60]}")
            return key, [{"cid": c.get("cid", ""), "video_url": url,
                          "text": c.get("text", ""), "likes": int(c.get("diggCount") or 0),
                          "sentiment": "", "theme": "", "status": "failed"} for c in group]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(work, job) for job in batches]):
            key, out = fut.result()
            done[key] = out
            COMMENTS_RESULT.write_text(json.dumps(done, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    return done


# --- export ------------------------------------------------------------------

def load_ocr():
    """Step 3's cover-frame OCR, used only where the video pass failed.

    Worse text - one frame, and Thai tone marks drop - but for a clip TikTok
    refuses to serve it is the difference between a row with something in it
    and a row with nothing. text_source records which one a value came from.
    """
    path = OUT / "ocr_text.json"
    if not path.exists():
        return {}
    return {k: (v.get("ocr_text") or "").strip()
            for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def export(videos, clips, comment_batches):
    """One flat schema per table, so the dashboard reads it without a join step."""
    flat_comments = [c for batch in comment_batches.values() for c in batch]
    ocr = load_ocr()
    counts = {}
    for c in flat_comments:
        counts.setdefault(c["video_url"], []).append(c)

    order = seed_order()
    rows = []
    for v in videos:
        vid = v["video_url"].rsplit("/", 1)[-1]
        media = media_name(vid, order)
        a = clips.get(vid, {})
        cs = counts.get(v["video_url"], [])
        on_screen = a.get("on_screen_text", "")
        source = "gemini" if a.get("status") == "ok" else "none"
        if a.get("status") != "ok" and ocr.get(vid):
            on_screen, source = ocr[vid], "ocr_cover"
        rows.append({
            "sheet_row": order.get(vid, (None, ""))[0],
            "video_id": vid,
            "media_file": media if (MEDIA / media).exists() else "",
            "video_url": v["video_url"],
            "username": v["username"],
            "caption": v.get("caption", ""),
            "uploaded_at": v.get("uploaded_at", ""),
            "duration": v.get("duration"),
            "views": v.get("views"),
            "likes": v.get("likes"),
            "comments": v.get("comments"),
            "shares": v.get("shares"),
            "human_category": v.get("category", ""),
            "transcript": a.get("transcript", ""),
            "on_screen_text": on_screen,
            "text_source": source,
            "themes": a.get("themes", []),
            "intent_level": a.get("intent_level", ""),
            "notable_quote": a.get("notable_quote", ""),
            "relevant": a.get("relevant"),
            "confidence": a.get("confidence", ""),
            "analysis_status": a.get("status", "not_processed"),
            "comments_classified": sum(1 for c in cs if c["status"] == "ok"),
            "comments_agree": sum(1 for c in cs if c["sentiment"] == "เห็นด้วย"),
            "comments_disagree": sum(1 for c in cs if c["sentiment"] == "ไม่เห็นด้วย"),
        })

    # Sheet order, so a row in the export sits where the screener expects it.
    rows.sort(key=lambda r: (r["sheet_row"] is None, r["sheet_row"] or 0))

    (OUT / "dashboard.json").write_text(
        json.dumps({"clips": rows, "comments": flat_comments}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    with (OUT / "clips.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({**r, "themes": "|".join(r["themes"])})

    if flat_comments:
        with (OUT / "comments.csv").open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(flat_comments[0].keys()))
            w.writeheader()
            w.writerows(flat_comments)

    ok = [r for r in rows if r["analysis_status"] == "ok"]
    irrelevant = [r for r in ok if r["relevant"] is False]
    report = {
        "clips_total": len(rows),
        "clips_analyzed": len(ok),
        "clips_failed": sum(1 for r in rows if r["analysis_status"] == "failed"),
        "clips_not_processed": sum(1 for r in rows if r["analysis_status"] == "not_processed"),
        "clips_dropped_irrelevant": len(irrelevant),
        "clips_with_transcript": sum(1 for r in ok if r["transcript"].strip()),
        "clips_with_on_screen_text": sum(1 for r in ok if r["on_screen_text"].strip()),
        "clips_with_quote": sum(1 for r in ok if r["notable_quote"].strip()),
        "clips_text_from_ocr_fallback": sum(1 for r in rows if r["text_source"] == "ocr_cover"),
        "clips_with_no_text_at_all": sum(1 for r in rows if r["text_source"] == "none"),
        "intent_breakdown": tally(r["intent_level"] for r in ok),
        "theme_breakdown": tally(t for r in ok for t in r["themes"]),
        "confidence_breakdown": tally(r["confidence"] for r in ok),
        "comments_total": len(flat_comments),
        "comments_classified": sum(1 for c in flat_comments if c["status"] == "ok"),
        "comments_failed": sum(1 for c in flat_comments if c["status"] != "ok"),
        "comment_sentiment": tally(c["sentiment"] for c in flat_comments if c["status"] == "ok"),
        "comment_theme": tally(c["theme"] for c in flat_comments if c["status"] == "ok" and c["theme"]),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def tally(values):
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


# --- main --------------------------------------------------------------------

def estimate(videos, comments):
    """Gemini 3.x Flash: ~$0.30/M in, ~$2.50/M out. Video dominates the input."""
    secs = sum(v.get("duration") or 20 for v in videos)
    clip_in = secs * 70 + len(videos) * 400        # ~70 tok/s of video at low res
    clip_out = len(videos) * 250
    batches = sum(1 for _ in range(0, len(comments), COMMENT_BATCH))
    cm_in = len(comments) * 40 + batches * 300
    cm_out = len(comments) * 25
    usd = (clip_in + cm_in) / 1e6 * 0.30 + (clip_out + cm_out) / 1e6 * 2.50
    print(f"clips     {len(videos):>6}  {secs/60:.0f} min of video")
    print(f"comments  {len(comments):>6}  {batches} batches")
    print(f"tokens    in ~{(clip_in+cm_in)/1e6:.1f}M  out ~{(clip_out+cm_out)/1e6:.2f}M")
    print(f"cost      ~${usd:.2f}  (~{usd*36:.0f} THB)")
    print(f"download  ~{len(videos) * 0.8:.0f} MB, deleted after each clip unless --keep-media")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--estimate", action="store_true", help="cost and volume, spends nothing")
    ap.add_argument("--limit", type=int, help="only the first N clips")
    ap.add_argument("--clips-only", action="store_true")
    ap.add_argument("--comments-only", action="store_true")
    ap.add_argument("--export", action="store_true", help="rebuild exports from saved results")
    ap.add_argument("--keep-media", action="store_true", help="don't delete downloaded mp4s")
    ap.add_argument("--fetch-media", action="store_true",
                    help="download the mp4s only, no Gemini calls")
    ap.add_argument("--via-apify", action="store_true",
                    help="fetch missing mp4s through Apify (~$0.004/clip) — "
                         "works when TikTok has blocked this IP")
    ap.add_argument("--browser", help="read TikTok cookies from this browser "
                                      "(chrome/firefox/edge/brave) instead of "
                                      "tiktok_state.json — needed for clips only "
                                      "visible to a logged-in account")
    ap.add_argument("--workers", type=int, default=CLIP_WORKERS)
    args = ap.parse_args()

    if args.browser:
        global BROWSER
        BROWSER = args.browser

    if not VIDEOS.exists():
        sys.exit(f"{VIDEOS} not found. Run apify_scrape.py first.")
    videos = json.loads(VIDEOS.read_text(encoding="utf-8"))
    comments = json.loads(COMMENTS.read_text(encoding="utf-8")) if COMMENTS.exists() else []
    if args.limit:
        videos = videos[:args.limit]
        keep = {v["video_url"] for v in videos}
        comments = [c for c in comments if c.get("videoWebUrl") in keep]

    if args.estimate:
        return estimate(videos, comments)

    clips = json.loads(CLIPS_RESULT.read_text(encoding="utf-8")) if CLIPS_RESULT.exists() else {}
    batches = json.loads(COMMENTS_RESULT.read_text(encoding="utf-8")) if COMMENTS_RESULT.exists() else {}

    if args.via_apify:
        return fetch_via_apify(videos, seed_order())

    if args.fetch_media:
        return fetch_media(videos, cookie_file(), args.workers)

    if not args.export:
        from google import genai
        client = genai.Client(api_key=google_key())
        if not args.comments_only:
            cookies = cookie_file()
            if not cookies:
                print("warning: no tiktok_state.json — TikTok will 403 most downloads")
            clips = run_clips(client, videos, cookies, args.keep_media, args.workers)
        if not args.clips_only:
            batches = run_comments(client, comments, clips, COMMENT_WORKERS)

    report = export(videos, clips, batches)
    print("\n--- summary ---")
    for k, v in report.items():
        print(f"{k:<28} {v if not isinstance(v, dict) else ''}")
        if isinstance(v, dict):
            for kk, vv in v.items():
                print(f"    {kk or '(blank)':<22} {vv}")
    print(f"\nwrote {OUT}/dashboard.json, clips.csv, comments.csv, analysis_report.json")


if __name__ == "__main__":
    main()
