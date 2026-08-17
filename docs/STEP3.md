# Step 3 — Apify: video metadata + comments

**Done.** 99 of the 100 curated clips have metadata and comments; the missing
one is gone from TikTok (`Post not found or private`), not a scrape failure.
Spend was $6.36 of the $29 account cap. (Step 4 analyses 98 of those 99 — one
clip's mp4 could not be downloaded. Different number, different stage.)

Comments were pulled with `--comments-from-seed`: the profile actor was down,
and the sheet already carries one `video_url` per account, so the feed walk
was skipped entirely.

Scope is the **100 clips listed in the sheet**, not the accounts' full feeds.

> During the run the clockworks profile actor went down platform-wide,
> returning `SUCCEEDED | Scraped 0/N profiles | 0 items` for every input
> including `@tiktok` as a control. `apify_scrape.py` gained an
> `apidojo/tiktok-profile-scraper` fallback (cross-checked: identical
> view/like/comment counts on 5 overlapping videos) plus a preflight that
> aborts before spending if an actor returns nothing.

Seed: `filtered_100.json` (exported from the `filtered` tab of
[Seed] TikTok — Employee Sentiment).

## Run it

Token is read from `APIFY_TOKEN` in the environment, or from `.env`
(gitignored).

```bash
.venv/bin/python -m sltiktok.enrich --estimate     # cost + volume, spends nothing
.venv/bin/python -m sltiktok.enrich --limit 5      # smoke test, ~$0.30 real
.venv/bin/python -m sltiktok.enrich                # full run
```

Every run preflights one profile first and aborts if the actor returns
nothing, so an outage costs ~$0.01 instead of the batch.

**Account cap is $29/month** (STARTER, `praneat`) — read live by `--estimate`,
which warns and suggests a smaller `--max-comment-videos` if the worst case
won't fit. Defaults are sized to land at ~$28.

A Console limit (Settings → Limits) is still worth setting: the in-script
guard can be bypassed by a wrong flag, the Console one can't.

## Output — `out/`

| File | What |
|---|---|
| `seed_100_metadata.json` | the 98 sheet clips + views/likes/comments/shares/uploaded_at |
| `comments_raw.json` | 5,201 comments, capped at 100/clip, sorted by likes |
| `coverage_report.json` | actual vs expected — which clips came back empty |
| `runs.json` | every `run_id` + `dataset_id`, for tracing or re-fetching |
| `ocr_text.json` | superseded by step 4; see STEP4.md |

Written incrementally, so a crash or a failed batch keeps what came before.

## Budget

Rates as of 2026-08-14: **$1.00/1k videos**, **$0.50/1k comments**.
Comments are ~95% of the cost, so `--max-comment-videos` is the real lever.

| Setting | Worst case |
|---|---|
| defaults (30 videos, 100 comments, 600 clips) | ~$33 |
| `--max-comment-videos 1200` | ~$63 — **over budget** |
| `--limit 5` smoke test | ~$4 |

Worst case assumes every account is full and every clip is comment-heavy.
Real spend is normally well under.

## Filtering

The profile scraper returns each creator's **whole recent feed** — gym clips,
horoscopes, songs — so the filter's job is separating work content from the
rest.

A clip is kept if it mentions 7-11 **or** reads like workplace content.
That's deliberately looser than step 2, which needed *both*: step 2 was
searching the open platform and had to prove the account belonged. That's
already settled here, and these creators don't re-introduce their employer in
every caption. On the first real run this recovered
`"รอบนี้ เจอหัวหน้าดีแตกแทน #หัวหน้าเฮงซวย"` — clearly on-topic, never says
"เซเว่น".

Clips with **no caption at all are kept**, not dropped. They're usually
text-on-image, which is the content most worth reading — see below.

Measured on 30 real clips from one account: 2 kept, 28 dropped. A low keep
rate is normal and not a bug — most of what these accounts post isn't about
work.

---

# The text-on-image problem

Half the curated clips (**50/100**) have captions too thin to analyse — just
hashtags and emoji. The story is burned onto the video frame.

Worked example, from the sheet:

```
caption:  31🗓️👋🏻#สาวอวบอ้วน #สาวเซเว่น #เซเว่น #เด็กเซเว่น #ผู้จัดการ
frame:    ทำมั้ยถึงตัดสินใจลาออกนะหรอ...
```

Caption alone → `รีวิวชีวิตพนักงาน` (wrong).
Caption + frame → `ลาออกแล้ว` (right — and what the human screener chose).

## Three ways to get that text

| | Gets on-image text | Cost |
|---|---|---|
| TikTok subtitles (`downloadSubtitlesOptions`) | **no** — spoken audio only | free |
| Apify transcription add-on | **no** — spoken audio only | paid/minute |
| OCR on the cover image | **yes** | free, local |

The first two are worth turning on for talking-to-camera clips, but they do
**not** solve this — they transcribe speech, not pixels.

## OCR

```bash
.venv/bin/pip install easyocr
.venv/bin/python -m sltiktok.ocr --check      # what it would target
.venv/bin/python -m sltiktok.ocr --limit 5    # try a few
.venv/bin/python -m sltiktok.ocr              # all of them
```

> **Superseded by step 4.** `analyze.py` sends the whole video to Gemini, which
> reads on-screen text across the clip and spells Thai correctly. Nothing
> downstream reads `ocr_text.json` any more. Kept for the record.

Reads `out/seed_100_metadata.json` → writes `out/ocr_text.json`. Resumable, and
falls back to TikTok's oembed thumbnail when the dataset has no cover.

Final result: **42/49** caption-thin clips yielded text, after discovering that
TikTok serves covers as HEIF while naming them `.jpg` — easyocr failed on 35 of
them with an error that looked exactly like "image has no text."

**Measured on 6 real clips:** 6/6 returned text, 1 got a corrected category.
Roughly 2.4s/clip on CPU after a one-off 13s model load.

Accuracy is good but not perfect — Thai tone marks drop occasionally
(`ทำมั้ย` → `ทำมัย`) and one clip returned near-garbage. That's tolerable
here: the category keywords (`ลาออก`, `พนักงาน`) survive, and bad OCR adds
noise rather than a wrong label. Treat `ocr_text` as a hint for the analysis
step, not as ground truth.

**Limitation:** this reads the *cover frame only* — one image per clip. Text
that appears later in the video is missed. Sampling more frames means
downloading the videos (a paid Apify add-on) and is a bigger job; worth doing
only if cover-only OCR proves too thin.
