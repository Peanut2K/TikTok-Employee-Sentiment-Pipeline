# CP Executive Dashboard — Design

**Date:** 2026-08-16
**Step:** 5 (follows step 4's `out/dashboard.json`)

## Goal

A CP executive opens one page and understands in five minutes why 7-Eleven
staff want to quit, without reading raw data.

## Source data

`out/dashboard.json`, produced by `analyze.py --export`:

- `clips` — 99 rows, 98 analyzed (`@ratti_21` has metadata but no transcript)
- `comments` — 5,181 rows, all classified

Aggregate facts: 99 distinct accounts (one clip each), 25.2M views,
1.7M likes, posts spanning 2019-12 to 2026-08.

## Intent mapping

The user story names three levels; the pipeline emits four labels. They map
one-to-one, with the fourth excluded from the funnel:

| Story level | Pipeline label | Count |
|---|---|---|
| Low — venting | `บ่น` | 61 |
| Medium — wavering | `คิดจะลาออก` | 4 |
| High — decided | `ลาออกแล้ว` | 25 |
| — (excluded) | `ไม่เกี่ยว` | 8 |

90 clips carry a real intent. High is 28% of them — the headline number.

`ไม่เกี่ยว` clips are excluded from the funnel, the theme chart, and
highlights, but still counted in the "clips collected" total so the number
reconciles with step 4's report.

## Architecture

Three files. No build step — the page opens from `file://`.

```
build_dashboard_data.py   pipeline: out/dashboard.json -> web/data.js
web/data.js               window.DATA = {...}   (~250KB, generated)
web/index.html            React + Recharts + Tailwind via CDN
```

Data lives in a separate `data.js` assigning `window.DATA`, not inlined in
the HTML. A `<script src>` is not subject to the CORS rule that blocks
`fetch()` on `file://`, so the page still opens directly while the HTML
stays small enough to edit by hand.

### Why no bundler

The user chose the CDN approach over Vite. The cost is Babel standalone
compiling JSX in the browser (~1s on first paint) and a hard dependency on
CDN reachability. The benefit is that `web/` is copyable anywhere and needs
no toolchain. Accepted as specified.

## Pipeline: `build_dashboard_data.py`

Reads `out/dashboard.json`, precomputes every aggregate, writes `web/data.js`.
No aggregation happens in the browser — in particular Thai word segmentation
runs here, never client-side.

Output shape:

- **`overview`** — clips, comments, accounts, views, likes; theme and intent
  breakdowns as `[{name, value}]` ready for Recharts.
- **`trend`** — one row per quarter from 2019Q4 to 2026Q3: clip count, total
  views, average engagement. Quarters with no clips are present with zeros so
  the x-axis stays evenly spaced.
- **`highlights`** — top 12 clips by views: pseudonym, notable quote, themes,
  intent, engagement counts, `video_url`.
- **`phrases`** — top 40 repeated two- and three-word Thai phrases across all
  5,181 comment texts, segmented with pythainlp `newmm`. Single words were
  tried first and rejected: they surfaced `ทำ`/`คน`/`ดี`, and segmentation
  split `กะดึก` — the most repeated complaint, 107 times — into two
  meaningless halves. Fragments, filler-only grams, drawn-out typing, and
  phrases contained in a fuller phrase are all filtered out. Each entry
  carries its count plus a per-sentiment split.
- **`comment_sentiment`** — the three-way split for the donut.
- **`sentiment_by_theme`** — agreement per theme, with the disagreement rate
  taken over people who took a side. Answers what the 69% is agreeing about.
- **`top_comments`** — the six most-liked comments on each side, so the split
  has a voice and not only a percentage.

### Pseudonymization

Every clip gets `ผู้ใช้ #NN` from its `sheet_row`, stable across rebuilds.
`data.js` carries no `username` field and no `media_file` field. It does
carry `video_url`, because the story requires a link to the real clip and a
TikTok URL necessarily contains the handle. The dashboard surface shows only
the pseudonym; the handle is reachable only by following the link off-site.
This is the tradeoff the user chose explicitly.

Transcripts are not shipped. They run 3–5K characters per clip and only
`notable_quote` is displayed, so shipping them would multiply the payload for
nothing.

## Page

One scrolling page. An executive should not have to click to find anything.

1. **Hero** — 25.2M views · 98 clips · 5,181 comments, and one sentence:
   1 in 4 clips is someone who already left.
2. **Intent funnel** — Low 61 / Medium 4 / High 25, green → amber → red.
3. **Themes** — horizontal bars, workload (61) leading.
4. **Trend** — clips per quarter with engagement overlaid.
5. **Voice highlights** — 12 cards: quote, pseudonym, engagement, link out.
6. **Comment insights** — sentiment donut plus the repeated Thai phrases.

Phrases render as a ranked two-column list with a frequency bar, not as a
cloud. A cloud sizes by frequency and so makes most entries unreadable on a
projector; a ranked list keeps every phrase at the same legible size and puts
the count in figures.

## Visual design

Palette taken from cpbrandsite.com's own CSS: orange `#EE843C`, gold
`#FFBF3C`, near-black `#2B2B2B`. Light theme on off-white — meeting-room
projectors wash out dark themes. Intent keeps its own green/amber/red scale,
which is the only place color carries meaning rather than brand.

Typography: IBM Plex Sans Thai from Google Fonts. CP's own DB Heavent is
licensed and cannot be redistributed.

Responsive: three columns on desktop, two on tablet, one on phone. Charts use
Recharts' `ResponsiveContainer`.

## Testing

`test_dashboard_data.py` guards what fails silently:

- every pipeline intent label maps to a bucket — a new label must not vanish
- clip counts reconcile: funnel + excluded == analyzed
- no `username` key anywhere in the emitted data
- pseudonyms are unique and stable for a given `sheet_row`
- word cloud excludes stopwords and single-occurrence tokens
- trend covers every quarter in range with no gaps

## Out of scope

Backend, auth, interactive drill-down, realtime refresh. Deploy to Cloudflare
Pages is deferred — the user asked for local first.
