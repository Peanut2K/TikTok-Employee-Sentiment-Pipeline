"""Recover text that lives on the video frame, not in the caption.

A chunk of these clips say nothing in the caption ("#พนักงานเซเว่น" and no more)
and put the whole story in text burned onto the image. Caption-only filtering
throws those away, which is exactly backwards — they're often the good ones.

Three sources of text, cheapest first:

  1. subtitles    — TikTok's own, free, but only covers SPOKEN audio
  2. transcript   — speech-to-text, paid add-on, still only spoken audio
  3. OCR          — reads the frame itself; the only one that gets on-image text

This script does (3) locally with easyocr, so it costs nothing per clip beyond
time. Downloading the videos is the paid part, and that is a separate decision.

    .venv/bin/pip install easyocr
    .venv/bin/python ocr_captions.py --check          # what would it target
    .venv/bin/python ocr_captions.py --limit 5        # try a few first

Reads out/videos_filtered.json, writes out/ocr_text.json.
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "out"
VIDEOS = OUT / "seed_100_metadata.json"
COVERS = OUT / "covers"
RESULT = OUT / "ocr_text.json"

# Frame text is usually large and centred. Thai + English covers this dataset.
LANGS = ["th", "en"]


def caption_of(v):
    return " ".join(str(v.get(k) or "")
                    for k in ("text", "description", "caption")).strip()


CJK_OR_THAI = "฀-๿"
WORDS = re.compile(rf"[{CJK_OR_THAI}a-zA-Z]+")

# A caption this short says a greeting, not a story. "👋🏻งานใหม่ใกล้ฉัน" clears
# a naive char count but carries no reason, mood, or outcome — the OCR is what
# turns it into something the analysis step can use.
MIN_MEANINGFUL_CHARS = 25


def needs_ocr(v):
    """Clips whose caption carries no real words - just tags, emoji, or nothing."""
    cap = caption_of(v)
    without_tags = " ".join(w for w in cap.split() if not w.startswith("#"))
    return sum(len(m) for m in WORDS.findall(without_tags)) < MIN_MEANINGFUL_CHARS


def cover_url(v):
    """The static thumbnail. Cheap to fetch and usually carries the title text."""
    for k in ("cover_url", "covorUrl", "coverUrl", "cover",
              "originalCoverUrl", "dynamicCover"):
        if v.get(k):
            return v[k]
    meta = v.get("videoMeta") or {}
    for k in ("coverUrl", "originalCoverUrl", "cover"):
        if meta.get(k):
            return meta[k]
    return ""


def clip_url(v):
    return v.get("video_url") or v.get("webVideoUrl") or ""


def cover_via_oembed(video_url):
    """Fallback when the dataset has no cover, or the CDN link has expired.

    TikTok's oembed endpoint serves a thumbnail without auth. Verified working
    against a real clip on 2026-08-14.
    """
    import urllib.parse
    import urllib.request
    api = "https://www.tiktok.com/oembed?url=" + urllib.parse.quote(video_url, safe="")
    req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get("thumbnail_url", "")


def download(url, dest):
    """Fetch the cover and hand back something easyocr can actually open.

    TikTok serves most covers as HEIF while still naming them .jpg, and
    easyocr can't read those - it fails with "could not find a backend",
    which looks exactly like an unreadable image rather than a format
    problem. Convert anything that isn't already JPEG.
    """
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    dest.write_bytes(raw)

    if raw[:3] == b"\xff\xd8\xff":          # already JPEG
        return
    import pillow_heif
    from PIL import Image
    pillow_heif.register_heif_opener()
    Image.open(dest).convert("RGB").save(dest, "JPEG", quality=92)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="count targets, do nothing")
    ap.add_argument("--limit", type=int, help="only the first N clips")
    args = ap.parse_args()

    if not VIDEOS.exists():
        sys.exit(f"{VIDEOS} not found. Run apify_scrape.py first.")

    videos = json.loads(VIDEOS.read_text(encoding="utf-8"))
    targets = [v for v in videos if needs_ocr(v)]

    print(f"{len(videos)} clips, {len(targets)} with no usable caption")
    print(f"{sum(1 for v in targets if cover_url(v))} have a cover in the dataset "
          f"(rest fall back to oembed)")
    if args.check:
        for v in targets[:10]:
            print(f"  {caption_of(v)[:60]!r:64} cover={'yes' if cover_url(v) else 'oembed'}")
        return

    targets = targets[:args.limit] if args.limit else targets
    if not targets:
        return print("nothing to do")

    try:
        import easyocr
    except ImportError:
        sys.exit("easyocr not installed.  .venv/bin/pip install easyocr")

    print(f"loading easyocr ({'+'.join(LANGS)}) - first run downloads models...")
    reader = easyocr.Reader(LANGS, gpu=False)

    COVERS.mkdir(parents=True, exist_ok=True)
    results = json.loads(RESULT.read_text(encoding="utf-8")) if RESULT.exists() else {}

    for i, v in enumerate(targets, 1):
        # The seed export keys on video_url, the Apify datasets on id.
        vid = str(v.get("id") or "") or clip_url(v).rsplit("/", 1)[-1]
        if not vid or vid in results:
            continue
        img = COVERS / f"{vid}.jpg"
        try:
            if not img.exists():
                # Dataset cover first; oembed when it's missing or expired.
                url = cover_url(v) or cover_via_oembed(clip_url(v))
                if not url:
                    raise RuntimeError("no cover available")
                download(url, img)
            text = " ".join(reader.readtext(str(img), detail=0, paragraph=True))
        except Exception as e:
            # A clip that won't OCR is left blank on purpose - a blank is
            # honest, a guess would be indistinguishable from a real reading.
            text = ""
            print(f"  [{i}/{len(targets)}] {vid} FAILED: {e}")
        results[vid] = {
            "video_url": clip_url(v),
            "username": v.get("username", ""),
            "caption": caption_of(v),
            "ocr_text": text,
        }
        if text:
            print(f"  [{i}/{len(targets)}] {vid}: {text[:70]}")
        RESULT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    got = sum(1 for r in results.values() if r["ocr_text"])
    print(f"\n{got}/{len(results)} clips yielded text -> {RESULT}")


if __name__ == "__main__":
    main()
