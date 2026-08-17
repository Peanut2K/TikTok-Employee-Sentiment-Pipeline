# Step 4 — transcribe and classify

One Gemini call per clip does transcription and classification together. The
model gets the actual video file, so speech, on-screen text, and what happens
in frame all land in the same pass.

That replaces step 3's cover OCR entirely — see [Why this replaces the
OCR](#why-this-replaces-the-ocr) below.

## Run it

`GOOGLE_API_KEY` is read from the environment or `.env` (gitignored).

```bash
.venv/bin/python -m sltiktok.analyze --estimate       # cost + volume, spends nothing
.venv/bin/python -m sltiktok.analyze --limit 5        # smoke test
.venv/bin/python -m sltiktok.analyze                  # clips, then comments
.venv/bin/python -m sltiktok.analyze --comments-only  # skip the video pass
.venv/bin/python -m sltiktok.analyze --export         # rebuild exports, no API calls
```

Resumable. Every clip and every comment batch is written as it lands, and a
rerun only picks up what is missing or previously failed. Killing the run
mid-way costs nothing already paid for.

Run the tests before a full pass: `.venv/bin/python tests/test_analyze.py`.

## Cost

| | |
|---|---|
| 98 clips (50 min of video) | ~0.5M input tokens |
| 5,201 comments (105 batches of 50) | ~0.2M input tokens |
| **total** | **~$0.53 (~19 THB)** |

Well under the 500–1,500 THB the story budgeted for. Video input is the bulk of
it; comments are cheap because they batch 50 to a call.

Downloads run ~78 MB total and each mp4 is deleted right after its clip is
analyzed, unless `--keep-media`.

## Output — `out/`

| File | What |
|---|---|
| `dashboard.json` | both tables in one file — what the dashboard reads |
| `clips.csv` | one row per clip, `themes` joined with `\|` |
| `comments.csv` | one row per comment |
| `clips_analyzed.json` | raw per-clip model output (resume state) |
| `comments_classified.json` | raw per-batch model output (resume state) |
| `gemini_raw.jsonl` | every response verbatim, for debugging a bad label |
| `analysis_report.json` | summary stats: processed, failed, dropped |

CSVs are written UTF-8 with BOM so Excel opens Thai correctly.

### Clip fields

`video_id`, `video_url`, `username`, `caption`, `uploaded_at`, `duration`,
`views`, `likes`, `comments`, `shares`, `human_category`, `transcript`,
`on_screen_text`, `text_source`, `themes[]`, `intent_level`, `notable_quote`,
`relevant`, `confidence`, `analysis_status`, `comments_classified`,
`comments_agree`, `comments_disagree`.

`text_source` is `gemini`, `ocr_cover`, or `none` — where `on_screen_text`
came from. `ocr_cover` is step 3's cover-frame reading, used only for clips the
video pass could not download, and it never overwrites a Gemini reading.

`human_category` is the screener's own label from the sheet, kept alongside the
model's so the two can be compared without a join.

### Comment fields

`cid`, `video_url`, `text`, `likes`, `sentiment`, `theme`, `status`.

## Labels

**themes** — `ค่าแรง`, `workload`, `หัวหน้างาน`, `เพื่อนร่วมงาน`, `ลูกค้า`,
`ตารางกะ`, `อื่นๆ`

`เพื่อนร่วมงาน` is not in the original story. It was added after the first real
batch: people quit over colleagues as often as over the job, and without the
theme those clips all collapsed into `อื่นๆ` and said nothing. The first clip
analyzed was exactly this — *"ไม่ใช่เป็นเพราะงานนะ ... แต่กับคน"*.

**intent_level** — `บ่น`, `คิดจะลาออก`, `ลาออกแล้ว`, `ไม่เกี่ยว`

**confidence** — the model answers `high` on essentially every clip, so treat
it as a field the schema requires rather than a quality signal. Rank by
`analysis_status` and by whether `transcript`/`on_screen_text` came back
non-empty instead.

**sentiment** (comments) — `เห็นด้วย`, `ไม่เห็นด้วย`, `อื่นๆ`

`อื่นๆ` covers questions, jokes, and emoji-only replies, which are common and
are not opinions either way.

**Unknown theme is `ระบุไม่ได้`, not an empty string.** The API rejects `""` as
an enum member with a 400 and the entire comment pass fails at once —
`test_no_empty_enum_values` guards this.

## Spot-check

```bash
.venv/bin/python tools/spotcheck.py             # 30 clips, model next to human label
.venv/bin/python tools/spotcheck.py --disagree  # only the mismatches
```

`intent_level` agreed with the sheet's human category on **67/77** clips
(87%). Most mismatches look like the model being right and the screener being
wrong — `@benz010942` says *"เรากำลังคิดว่าจะลาออก"* out loud and was filed as
`ลาออกแล้ว`; `@woranuch196` says *"คนที่**เคย**เป็นพนักงาน"* and was filed as
`บ่นงาน`. A handful are genuinely arguable.

The sheet was not corrected — spot-checking is for judging the model, and
which label wins is the screener's call.

## Failures are recorded, not guessed

A clip that won't download or won't process is written with
`analysis_status: "failed"`, empty fields, and the error text. It still appears
in the export. Same rule step 3 settled on: a blank is honest, a fabricated
label is indistinguishable from a real one.

`notable_quote` is likewise constrained to text that actually appears in the
clip; the prompt forbids composing one, and `""` is the correct answer when
nothing stands out.

## Results

| | |
|---|---|
| comments classified | **5,201 / 5,201** (100%) |
| clips analyzed | **78 / 98** |
| clips with text after OCR fallback | 87 / 98 |
| clips with no text at all | 11 |
| intent vs human category | 67/77 (87%) |

The 20 unanalyzed clips all failed at the *download* step, not the model step:
13 × HTTP 403, 6 × extractor error, 1 × `Your IP address is blocked`. Reruns
recovered them in waves (64 → 66 → 76 → 78) until TikTok stopped serving this
IP at all. Rerun `--clips-only` after a cooldown, or from another network.

Nine of the twenty still carry cover-frame text from step 3's OCR, flagged
`text_source: "ocr_cover"`. Eleven rows have no text at all — left empty.

## TikTok downloads

`yt-dlp` cannot extract TikTok anonymously — it 403s. The script converts
`tiktok_state.json` (the Playwright session the user created in step 2 with
`--login`) into a Netscape cookie file at `out/.tt_cookies.txt`.

TikTok also throttles by IP above roughly two concurrent downloads, which is
why `CLIP_WORKERS = 2`. Retries are deliberately short (2 attempts): a 403 is
throttling, and grinding through long backoffs inside a worker stalls the pass
behind one clip. Failed clips are cheaper to sweep up with a second run, since
a rerun retries exactly the failures.

## Why this replaces the OCR

Step 3's `ocr_captions.py` read only the cover frame and mangled Thai tone
marks. Same clip, both methods:

```
OCR (cover only):  ทำมัยถึงตัดสินใจลาออกนะหรอ๓ les
Gemini (video):    ทำมั้ยถึงตัดสินใจลาออกนะหรอ... Me: คนที่มีตำแหน่งผู้จัดการใน
                   สายนี้จะรู้ดีที่สุด อยู่ในจุดที่โดนมาเยอะพอสมควร แต่ยังมี
                   ความสดใสของตัวเองที่ยังอดทนได้ถึงวันนี้
```

Correct spelling, and the whole clip rather than one frame. `ocr_text.json`
stays on disk as a step-3 artifact but nothing downstream reads it.
