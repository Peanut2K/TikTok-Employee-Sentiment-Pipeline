"""Step 3: pull video metadata + comments for the seed clips via Apify.

Scope is one clip per account — the video_url the sheet already names. By
default nothing is scraped to find clips: they are known, and only the
comments actor runs.

    export APIFY_TOKEN=...
    .venv/bin/python -m sltiktok.enrich --estimate   # cost/volume guess, no run
    .venv/bin/python -m sltiktok.enrich --limit 5    # smoke test on 5 accounts
    .venv/bin/python -m sltiktok.enrich              # the real thing

--scrape-profiles opts into the other shape: walk each creator's recent feed
(clockworks/tiktok-profile-scraper, apidojo as fallback), keep the work clips
with relevant(), then comment on those. Several clips per account and a much
larger bill, so it is never the default.

Output lands in out/ as JSON. Every run_id is recorded in out/runs.json so a
bad run can be traced or re-fetched from Apify without re-scraping.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from apify_client import ApifyClient

from sltiktok.discover import SEVEN, EMPLOYEE

# Project root: data and credentials live there, not next to this module.
HERE = Path(__file__).resolve().parents[2]
OUT = HERE / "out"
SEED = HERE / "filtered_100.json"

PROFILE_ACTOR = "clockworks/tiktok-profile-scraper"
COMMENTS_ACTOR = "clockworks/tiktok-comments-scraper"

# A second vendor for the same job. clockworks went down entirely on
# 2026-08-14 (SUCCEEDED, 0 profiles, for every input including a control),
# so the pipeline falls back rather than waiting for someone else's fix.
# Verified identical view/like/comment counts on 5 overlapping videos.
FALLBACK_PROFILE_ACTOR = "apidojo/tiktok-profile-scraper"

# Budget levers. Every scraped item is a billed event, so these are the only
# things standing between a smoke test and a surprise invoice.
VIDEOS_PER_PROFILE = 30
COMMENTS_PER_VIDEO = 100          # the story says cap at 100; actor may return fewer
MAX_VIDEOS_TO_COMMENT = 500       # worst case ~$28, under the account's $29 cap
PROFILE_BATCH = 25                # usernames per profile run


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def apify_token():
    """Environment first, then .env. Kept dependency-free on purpose.

    Never logged or echoed — only handed to the client.
    """
    if os.environ.get("APIFY_TOKEN"):
        return os.environ["APIFY_TOKEN"].strip()

    env = HERE / ".env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip() == "APIFY_TOKEN":
            return val.strip().strip("\"'")
    return ""


def account_budget():
    """(remaining, monthly_cap, used_this_cycle) in USD, straight from Apify.

    The account cap is the real ceiling — Apify halts a run that crosses it,
    which is worse than not starting, because a half-finished comments stage
    still costs money.
    """
    user = ApifyClient(apify_token()).user("me")
    cap = float(user.limits().limits.max_monthly_usage_usd)
    used = float(user.monthly_usage().total_usage_credits_usd_after_volume_discount or 0)
    return cap - used, cap, used


def seed_accounts(limit=None):
    """Usernames from the curated sheet export, deduped, order preserved."""
    rows = json.loads(SEED.read_text(encoding="utf-8"))
    seen, names = set(), []
    for r in rows:
        u = str(r.get("username", "")).strip().lstrip("@")
        if u and u not in seen:
            seen.add(u)
            names.append(u)
    return names[:limit] if limit else names


def normalize(v):
    """Put an apidojo item into the clockworks shape.

    Everything downstream (filter, coverage, OCR) reads clockworks field names,
    so translate once here instead of teaching every function both vendors.
    """
    if "playCount" in v or "webVideoUrl" in v:
        return v                                    # already clockworks

    ch = v.get("channel") or {}
    vid = v.get("video") or {}
    return {
        "id": str(v.get("id") or ""),
        "text": v.get("title") or "",
        "webVideoUrl": v.get("postPage") or "",
        "playCount": v.get("views") or 0,
        "diggCount": v.get("likes") or 0,
        "commentCount": v.get("comments") or 0,
        "shareCount": v.get("shares") or 0,
        "collectCount": v.get("bookmarks") or 0,
        "createTimeISO": v.get("uploadedAtFormatted") or "",
        "authorMeta": {"name": ch.get("username") or "",
                       "nickName": ch.get("name") or "",
                       "id": ch.get("id") or ""},
        "videoMeta": {"coverUrl": vid.get("cover") or vid.get("thumbnail") or "",
                      "duration": vid.get("duration") or 0},
        "hashtags": v.get("hashtags") or [],
        "subtitleInformation": v.get("subtitleInformation") or [],
        "_source": "apidojo",
    }


def relevant(video):
    """Keep a clip if it reads like workplace content from a 7-11 employee.

    Step 2 required BOTH a 7-11 mention and staff language, because it was
    searching the open platform and had to prove the account belonged here.
    That's already established: every account in the seed list was screened.
    So a clip about a terrible boss counts even when it never says "เซเว่น" —
    these creators don't re-introduce their employer in every caption.

    The profile scraper returns a creator's whole recent feed, so the job here
    is separating work content from their gym clips and horoscopes.
    """
    text = " ".join(str(video.get(k) or "") for k in ("text", "description"))
    for h in video.get("hashtags") or []:
        text += " " + str(h.get("name") if isinstance(h, dict) else h)

    if not text.strip():
        return True, "no-caption"
    if SEVEN.search(text):
        return True, "seven"
    if EMPLOYEE.search(text):
        return True, "workplace"
    return False, "off-topic"


def profile_input(actor, usernames, per_profile):
    """Each vendor names its fields differently."""
    if actor == FALLBACK_PROFILE_ACTOR:
        return {"usernames": usernames, "maxItems": per_profile * len(usernames)}
    return {
        "profiles": usernames,
        "resultsPerPage": per_profile,
        "profileScrapeSections": ["videos"],
        "profileSorting": "latest",
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadAvatars": False,
    }


def fetch_videos(client, usernames, videos_per_profile, runs, actor=PROFILE_ACTOR):
    """Profile scraper, in batches. One failed batch doesn't sink the rest."""
    videos, empty = [], 0
    for i in range(0, len(usernames), PROFILE_BATCH):
        batch = usernames[i:i + PROFILE_BATCH]
        log(f"profiles {i + 1}-{i + len(batch)} of {len(usernames)}")
        run_input = profile_input(actor, batch, videos_per_profile)
        try:
            run = client.actor(actor).call(run_input=run_input)
        except Exception as e:
            log(f"  batch FAILED: {e}")
            runs.append({"actor": actor, "batch": batch, "error": str(e)})
            continue

        info = run_info(run)
        runs.append({"actor": actor, "batch": batch, **info})
        got = [normalize(v) for v in client.dataset(info["dataset_id"]).iterate_items()]
        videos.extend(got)
        log(f"  run {info['run_id']} {info['status']} -> {len(got)} videos")
        # The actor reports SUCCEEDED even when TikTok blocks every profile,
        # so trust its own count, not the status. Seen live on 2026-08-14:
        # "SUCCEEDED | Scraped 0/3 profiles" for six runs straight.
        if info.get("status_message"):
            log(f"  actor says: {info['status_message']}")

        if not got:
            empty += 1
            if empty >= 2:
                log("")
                log("STOPPING: two batches in a row returned nothing.")
                log("The actor reports SUCCEEDED while scraping 0 profiles - that is")
                log("a block or a broken build on their side, not a bad input. Retrying")
                log("just burns budget. Check https://apify.com/" + PROFILE_ACTOR
                    + " and rerun later; already-collected videos are saved.")
                break
        else:
            empty = 0
        save(OUT / "videos_raw.json", videos)
        save(OUT / "runs.json", runs)
    return videos


def fetch_comments(client, urls, per_video, runs):
    """Comments scraper. Chunked so a mid-run failure keeps what came before."""
    comments, chunk = [], 100
    for i in range(0, len(urls), chunk):
        part = urls[i:i + chunk]
        log(f"comments {i + 1}-{i + len(part)} of {len(urls)}")
        run_input = {
            "postURLs": part,
            "commentsPerPost": per_video,
            "maxRepliesPerComment": 0,   # top-level only; replies are billed too
        }
        try:
            run = client.actor(COMMENTS_ACTOR).call(run_input=run_input)
        except Exception as e:
            log(f"  chunk FAILED: {e}")
            runs.append({"actor": COMMENTS_ACTOR, "urls": len(part), "error": str(e)})
            continue

        info = run_info(run)
        runs.append({"actor": COMMENTS_ACTOR, "urls": len(part), **info})
        got = list(client.dataset(info["dataset_id"]).iterate_items())
        # The actor returns TikTok's own ordering, not by likes. The brief asks
        # for top comments by like count, so sort here.
        got.sort(key=lambda c: int(c.get("diggCount") or 0), reverse=True)
        comments.extend(got)
        log(f"  run {info['run_id']} {info['status']} -> {len(got)} comments")
        save(OUT / "comments_raw.json", comments)
        save(OUT / "runs.json", runs)
    return comments


def run_info(run):
    """Pull run fields out of whatever the client hands back.

    apify-client 3.x returns pydantic models; older versions and some calls
    return plain dicts. Reading it the wrong way threw away a completed run
    once already, so handle both.
    """
    def field(*names):
        for n in names:
            if isinstance(run, dict):
                if run.get(n) is not None:
                    return run[n]
            elif getattr(run, n, None) is not None:
                return getattr(run, n)
        return None

    return {
        "run_id": field("id"),
        "dataset_id": field("default_dataset_id", "defaultDatasetId"),
        "status": field("status"),
        "status_message": field("status_message", "statusMessage"),
        "usage_usd": field("usage_total_usd", "usageTotalUsd"),
    }


def save(path, data):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def video_url(v):
    for k in ("webVideoUrl", "videoUrl", "url"):
        if v.get(k):
            return v[k]
    author = (v.get("authorMeta") or {}).get("name") or v.get("authorName") or ""
    return f"https://www.tiktok.com/@{author}/video/{v.get('id')}" if v.get("id") else ""


def author_of(v):
    return ((v.get("authorMeta") or {}).get("name")
            or v.get("authorName") or v.get("authorUniqueId") or "")


def coverage_report(usernames, videos, kept, comments):
    """Which accounts came back empty, and did the comments land where expected.

    This is the 'actual vs expected' log — without it a half-failed run looks
    identical to a thin one.
    """
    by_author = {}
    for v in videos:
        by_author.setdefault(author_of(v).lower(), []).append(v)

    missing = [u for u in usernames if not by_author.get(u.lower())]

    commented = {}
    for c in comments:
        url = c.get("videoWebUrl") or c.get("postUrl") or c.get("videoUrl") or ""
        commented[url] = commented.get(url, 0) + 1

    kept_urls = [video_url(v) for v in kept]
    no_comments = [u for u in kept_urls if u and u not in commented]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "accounts_requested": len(usernames),
        "accounts_with_videos": len(usernames) - len(missing),
        "accounts_empty": missing,
        "videos_total": len(videos),
        "videos_kept_after_filter": len(kept),
        "videos_dropped": len(videos) - len(kept),
        "videos_sent_for_comments": len(kept_urls),
        "videos_that_returned_comments": len(commented),
        "videos_with_zero_comments": no_comments,
        "comments_total": len(comments),
        "comments_expected_ceiling": len(kept_urls) * COMMENTS_PER_VIDEO,
        "videos_per_account": {u: len(by_author.get(u.lower(), [])) for u in usernames},
    }
    save(OUT / "coverage_report.json", report)

    log("")
    log(f"accounts:  {report['accounts_with_videos']}/{len(usernames)} returned videos")
    if missing:
        log(f"  EMPTY ({len(missing)}): {', '.join(missing[:12])}"
            + (" ..." if len(missing) > 12 else ""))
    log(f"videos:    {len(videos)} scraped, {len(kept)} kept, {len(videos) - len(kept)} filtered out")
    log(f"comments:  {len(comments)} from {len(commented)}/{len(kept_urls)} videos")
    if no_comments:
        log(f"  {len(no_comments)} videos returned no comments (may genuinely have none)")
    return report


def estimate(usernames, videos_per_profile, comments_per_video, max_comment_videos,
             scrape_profiles=False):
    """Volume and cost, printed before spending anything.

    Rates read off the actor pages on 2026-08-14. Confirm them in Console before
    a full run — a price change here is a silent overspend.

    The two paths cost very different amounts, so this prices whichever one is
    actually about to run: the default takes one clip per account from the
    sheet and pays for comments only.
    """
    video_rate, comment_rate = 1.00 / 1000, 0.50 / 1000

    if scrape_profiles:
        v = len(usernames) * videos_per_profile
        kept = int(v * 0.5)   # half survive the filter, on the seed-list hit rate
    else:
        # One clip per account, already known - nothing is scraped to find them.
        v = 0
        kept = len(usernames)
    to_comment = min(kept, max_comment_videos)
    c = to_comment * comments_per_video

    log(f"path:                {'profile feeds' if scrape_profiles else 'seed clips (1 per account)'}")
    log(f"accounts:            {len(usernames)}")
    if scrape_profiles:
        log(f"videos (ceiling):    {v}  ({videos_per_profile}/account)  ~${v * video_rate:.2f}")
        log(f"expected kept:       ~{kept}  (depends on how on-topic each account is)")
    else:
        log(f"videos:              {kept}  (from the sheet, nothing to scrape)  $0.00")
    log(f"videos commented:    {to_comment}  (cap {max_comment_videos})")
    log(f"comments (ceiling):  ~{c}  ~${c * comment_rate:.2f}")
    log(f"WORST CASE TOTAL:    ~${v * video_rate + c * comment_rate:.2f}")
    log("")
    log(f"rates used: ${video_rate * 1000:.2f}/1k videos, ${comment_rate * 1000:.2f}/1k comments")

    # Compare against what the account will actually allow, not just the brief.
    try:
        remaining, cap, used = account_budget()
        log(f"account cap: ${cap:.2f}/month, ${used:.2f} used, ${remaining:.2f} left")
        if v * video_rate + c * comment_rate > remaining:
            log(f"WARNING: worst case exceeds the ${remaining:.2f} left on the account.")
            log("         Apify stops the run when the cap is hit, so a full-size run")
            log("         could die partway through the comments stage.")
            log(f"         Fits the budget: --max-comment-videos "
                f"{max(0, int((remaining - v * video_rate) / (comments_per_video * comment_rate)))}")
    except Exception as e:
        log(f"(could not read account limits: {e})")

    log("Ceilings assume every account is full and every clip is comment-heavy;")
    log("real spend is usually well under. Set a hard limit in Apify Console")
    log("(Settings > Limits) anyway — that is the only guard that cannot be")
    log("bypassed by a bad argument here.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only the first N accounts (smoke test)")
    ap.add_argument("--videos", type=int, default=VIDEOS_PER_PROFILE)
    ap.add_argument("--comments", type=int, default=COMMENTS_PER_VIDEO)
    ap.add_argument("--max-comment-videos", type=int, default=MAX_VIDEOS_TO_COMMENT)
    ap.add_argument("--estimate", action="store_true", help="print volume and exit")
    ap.add_argument("--skip-comments", action="store_true", help="videos only")
    # The scope is one clip per account - the one the sheet names. Walking a
    # creator's feed returns several clips each, which is a different dataset
    # and a much larger bill, so it is opt-in rather than the default.
    ap.add_argument("--scrape-profiles", action="store_true",
                    help="walk each account's recent feed instead of using the "
                         "sheet's video_url (several clips per account, costs more)")
    args = ap.parse_args()

    usernames = seed_accounts(args.limit)
    if args.estimate:
        return estimate(usernames, args.videos, args.comments,
                        args.max_comment_videos, args.scrape_profiles)

    token = apify_token()
    if not token:
        sys.exit("APIFY_TOKEN not found in environment or .env")

    client = ApifyClient(token)
    runs, started = [], time.time()

    # Default path: one clip per account, the one the sheet already names.
    # That is the scope of the study, and it needs no profile actor at all -
    # which also means an actor outage cannot stop the run.
    if not args.scrape_profiles:
        seed = json.loads(SEED.read_text(encoding="utf-8"))
        urls = [r["video_url"] for r in seed if str(r.get("video_url", "")).strip()]
        urls = urls[:args.max_comment_videos]
        log(f"seed clips: {len(urls)} — one per account, straight from the sheet")
        comments = fetch_comments(client, urls, args.comments, runs)
        save(OUT / "runs.json", runs)
        log(f"{len(comments)} comments -> {OUT}/comments_raw.json")
        return

    # One cheap profile per vendor before committing the batch. An actor can
    # report SUCCEEDED while returning nothing at all, and finding that out on
    # account 1 of 100 is much better than on account 60.
    actor = None
    for candidate in (PROFILE_ACTOR, FALLBACK_PROFILE_ACTOR):
        log(f"preflight: {candidate}")
        try:
            probe = client.actor(candidate).call(
                run_input=profile_input(candidate, usernames[:1], 3))
            pinfo = run_info(probe)
            pcount = client.dataset(pinfo["dataset_id"]).get().item_count
        except Exception as e:
            log(f"  error: {e}")
            continue
        log(f"  {pinfo['status']} | {pinfo['status_message']} | {pcount} items")
        if pcount:
            actor = candidate
            break
        log("  returned nothing - trying the next vendor")

    if not actor:
        sys.exit("Every profile actor returned nothing for a known-good profile. "
                 "They are blocked or broken right now - rerun later rather than "
                 "spending the budget.")

    log(f"using {actor}")
    log(f"--scrape-profiles: {len(usernames)} accounts, up to "
        f"{args.videos} videos each — several clips per account, "
        f"not the sheet's one-to-one scope")

    videos = fetch_videos(client, usernames, args.videos, runs, actor)

    kept, dropped = [], []
    for v in videos:
        ok, why = relevant(v)
        v["_filter_reason"] = why
        (kept if ok else dropped).append(v)
    save(OUT / "videos_filtered.json", kept)
    log(f"filter: kept {len(kept)} of {len(videos)}")

    comments = []
    if not args.skip_comments and kept:
        # When the budget cap bites, spend it on the clips with the most
        # discussion rather than whichever happened to be scraped first.
        ranked = sorted(kept, key=lambda v: int(v.get("commentCount") or 0), reverse=True)
        if len(ranked) > args.max_comment_videos:
            log(f"capping comments at {args.max_comment_videos} of {len(ranked)} videos "
                f"(budget guard, most-commented first)")
        urls = [u for u in (video_url(v) for v in ranked) if u][:args.max_comment_videos]
        comments = fetch_comments(client, urls, args.comments, runs)

    coverage_report(usernames, videos, kept, comments)
    save(OUT / "runs.json", runs)
    log(f"done in {(time.time() - started) / 60:.1f} min -> {OUT}/")


if __name__ == "__main__":
    main()
