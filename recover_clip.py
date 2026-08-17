"""Pull one seed clip that was unavailable during the step-3 run.

@arraksin15's clip returned "Post not found or private" in step 3 and is live
again as of 2026-08-14. Rather than rerun the whole pipeline for one video,
this fetches its metadata and comments and merges them into the existing
files, so the dataset stops being short by a clip nobody deleted.

    .venv/bin/python recover_clip.py --check          # is it live? spends nothing
    .venv/bin/python recover_clip.py <video_url>      # fetch and merge

Costs about $0.05 — one video plus up to 100 comments.
"""

import argparse
import json
import sys
from pathlib import Path

from apify_scrape import (COMMENTS_ACTOR, OUT, apify_token, normalize,
                          run_info, save, video_url)

SEED = Path(__file__).parent / "filtered_100.json"
METADATA = OUT / "seed_100_metadata.json"
COMMENTS = OUT / "comments_raw.json"
RUNS = OUT / "runs.json"
DETAIL_ACTOR = "clockworks/tiktok-scraper"   # takes postURLs directly


def missing_from_dataset():
    """Seed rows with no matching row in the metadata export."""
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    have = {v["video_url"] for v in json.loads(METADATA.read_text(encoding="utf-8"))}
    return [s for s in seed if s["video_url"] not in have]


BROWSER_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}


def page_status(url):
    """TikTok's own verdict on a clip, read from the page it serves a browser.

    Worth the extra request because every other signal lies. oembed 200s for a
    clip the actor can't touch; the localized error strings ("Video currently
    unavailable", "This account is private") ship in every page regardless of
    state; and yt-dlp reported "Your IP address is blocked" for a clip that was
    simply set to friends-only. The embedded JSON gives the real code:

        0      public, fetchable
        10204  status_friend_see - visible only to mutual follows

    Returns (status_code, message, itemStruct or None).
    """
    import random
    import re
    import time
    import urllib.request

    # A stripped page with no embedded JSON is throttling, not a verdict - the
    # same URL returns the full payload a minute later. Retry before believing
    # it, or a rate limit reads as "this clip is gone".
    m = None
    for attempt in range(4):
        req = urllib.request.Request(url, headers=BROWSER_UA)
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                html = r.read().decode("utf-8", "replace")
        except Exception as e:
            return None, str(e)[:120], None
        m = re.search(r'id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                      html, re.S)
        if m:
            break
        if attempt < 3:
            time.sleep(8 * (attempt + 1) + random.uniform(0, 4))
    if not m:
        return None, "no embedded data after 4 tries (throttled)", None
    detail = (json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {})
              .get("webapp.video-detail", {}))
    item = (detail.get("itemInfo") or {}).get("itemStruct")
    return detail.get("statusCode"), detail.get("statusMsg", ""), item


def row_from_item(item, seed):
    """Map TikTok's itemStruct onto the same columns the pipeline writes."""
    stats = item.get("stats") or {}
    video = item.get("video") or {}
    created = item.get("createTime")
    iso = ""
    if created:
        from datetime import datetime, timezone
        iso = datetime.fromtimestamp(int(created), timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "username": seed.get("username") or (item.get("author") or {}).get("uniqueId", ""),
        "profile_url": seed.get("profile_url", ""),
        "category": seed.get("category", ""),
        "note": seed.get("note", ""),
        "source_keyword": seed.get("source_keyword", ""),
        "video_url": seed.get("video_url", ""),
        "caption": seed.get("caption", ""),
        "scraped_caption": item.get("desc", ""),
        "views": stats.get("playCount"),
        "likes": stats.get("diggCount"),
        "comments": stats.get("commentCount"),
        "shares": stats.get("shareCount"),
        "uploaded_at": iso,
        "duration": video.get("duration"),
        "cover_url": video.get("cover", ""),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", help="video URL; defaults to every missing seed clip")
    ap.add_argument("--check", action="store_true", help="report status only, spends nothing")
    args = ap.parse_args()

    targets = [args.url] if args.url else [s["video_url"] for s in missing_from_dataset()]
    if not targets:
        return print("nothing missing")

    seed_by_url = {s["video_url"]: s for s in json.loads(SEED.read_text(encoding="utf-8"))}
    live = {}
    for url in targets:
        code, msg, item = page_status(url)
        label = {0: "PUBLIC", 10204: "FRIENDS-ONLY"}.get(code, f"code {code}")
        print(f"{label:<14} {url}")
        if msg:
            print(f"               {msg}")
        if code == 0 and item:
            print(f"               {(item.get('desc') or '')[:80]}")
            live[url] = item

    if args.check or not live:
        if not live:
            print("\nnothing fetchable — a friends-only clip needs the creator to "
                  "change it, no scraper can reach it")
        return

    # The page JSON already carries everything the metadata columns need, and
    # it works where the actor returns an empty item, so skip Apify for this
    # part. Comments still need the actor.
    rows = json.loads(METADATA.read_text(encoding="utf-8"))
    for url, item in live.items():
        rows.append(row_from_item(item, seed_by_url.get(url, {"video_url": url})))
    save(METADATA, rows)
    print(f"\nmetadata now {len(rows)} clips")

    from apify_client import ApifyClient
    client = ApifyClient(apify_token())
    runs = json.loads(RUNS.read_text(encoding="utf-8")) if RUNS.exists() else []
    live = list(live)

    print("fetching comments")
    run = client.actor(COMMENTS_ACTOR).call(run_input={
        "postURLs": live, "commentsPerPost": 100, "maxRepliesPerComment": 0})
    info = run_info(run)
    runs.append({"actor": COMMENTS_ACTOR, "urls": len(live), **info})
    got = list(client.dataset(info["dataset_id"]).iterate_items())
    got.sort(key=lambda c: int(c.get("diggCount") or 0), reverse=True)
    print(f"  run {info['run_id']} {info['status']} -> {len(got)} comments")

    comments = json.loads(COMMENTS.read_text(encoding="utf-8"))
    comments.extend(got)
    save(COMMENTS, comments)
    save(RUNS, runs)
    print(f"  comments now {len(comments)}")
    print("\nnext: .venv/bin/python analyze.py --clips-only && analyze.py --comments-only")


if __name__ == "__main__":
    main()
