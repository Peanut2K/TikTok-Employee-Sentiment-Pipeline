"""Ask TikTok why each unfetchable clip is unfetchable.

Every earlier attempt guessed from yt-dlp's error text, which is unreliable -
it reported "Your IP address is blocked" for a clip that was simply set to
friends-only. The page itself carries the real verdict in
__UNIVERSAL_DATA_FOR_REHYDRATION__, so read that instead.

    .venv/bin/python why_failed.py

Writes out/failed_reasons.json.
"""

import json
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "out"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0")

# TikTok's own codes. Anything not listed here is printed raw rather than
# guessed at.
MEANING = {
    0: "public — reachable, tooling problem",
    10101: "removed by author",
    10102: "account private",
    10202: "account not found / banned",
    10204: "friends-only (creator setting)",
    10216: "region locked",
    10217: "under review",
    10222: "age restricted",
}


def page_status(url, tries=3):
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                html = r.read().decode("utf-8", "replace")
        except Exception as e:
            return None, f"fetch failed: {e}", None
        m = re.search(r'id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                      html, re.S)
        if m:
            detail = (json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {})
                      .get("webapp.video-detail", {}))
            item = (detail.get("itemInfo") or {}).get("itemStruct")
            return detail.get("statusCode"), detail.get("statusMsg", ""), item
        # A stripped page means throttling, not a verdict.
        if attempt < tries - 1:
            time.sleep(10 * (attempt + 1) + random.uniform(0, 5))
    return None, "throttled (no embedded data after retries)", None


def main():
    clips = json.loads((OUT / "clips_analyzed.json").read_text(encoding="utf-8"))
    failed = [(k, r) for k, r in clips.items() if r["status"] == "failed"]
    if not failed:
        return print("nothing failed")

    print(f"checking {len(failed)} clips — slow on purpose, one at a time\n")
    results = {}
    for i, (vid, r) in enumerate(sorted(failed, key=lambda kv: kv[1]["username"]), 1):
        code, msg, item = page_status(r["video_url"])
        why = MEANING.get(code, f"code {code}" + (f" ({msg})" if msg else ""))
        downloadable = ""
        if item:
            video = item.get("video") or {}
            downloadable = " playAddr=yes" if video.get("playAddr") else " playAddr=no"
        print(f"[{i:2}/{len(failed)}] @{r['username'][:20]:<20} {why}{downloadable}")
        results[vid] = {"username": r["username"], "video_url": r["video_url"],
                        "status_code": code, "status_msg": msg, "meaning": why}
        (OUT / "failed_reasons.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(6 + random.uniform(0, 4))

    print("\n--- summary ---")
    tally = {}
    for v in results.values():
        tally[v["meaning"]] = tally.get(v["meaning"], 0) + 1
    for k, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"{n:>3}  {k}")
    print(f"\nwrote {OUT}/failed_reasons.json")


if __name__ == "__main__":
    main()
