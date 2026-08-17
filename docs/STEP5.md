# Step 5 — Executive dashboard

One page that answers "why do 7-Eleven staff want to quit" in five minutes,
built from step 4's export. No backend, no build step.

## Run it

```bash
.venv/bin/python -m sltiktok.dashboard    # out/dashboard.json -> web/data.js
xdg-open web/index.html                     # or: python3 -m http.server -d web
```

The page opens straight from `file://`. Data lives in `web/data.js` as
`window.DATA`, loaded by `<script src>` — a plain `<script>` tag is exempt
from the CORS rule that would block `fetch()` on a local file.

Tests:

```bash
.venv/bin/python tests/test_dashboard.py     # 27 checks, no pytest needed
```

## Files

| File | What |
|---|---|
| `src/sltiktok/dashboard.py` | Reads `out/dashboard.json`, precomputes every aggregate |
| `web/data.js` | Generated, 14KB. `window.DATA = {...}` |
| `web/index.html` | The page. React + Recharts + Tailwind from CDN |
| `tests/test_dashboard.py` | Reconciliation and PDPA-leak guards |
| `docs/superpowers/specs/2026-08-16-cp-dashboard-design.md` | Design decisions |

2.3MB of raw export becomes 14KB of dashboard data. Nothing is aggregated in
the browser — in particular Thai word segmentation runs in the pipeline, so
no segmenter and no comment bodies are shipped to the client.

## Numbers on the page

Three clip counts exist and they are not interchangeable:

| Number | Meaning |
|---|---|
| 99 | clips collected (in the sheet, with metadata) |
| 98 | analyzed successfully (`@ratti_21` went unreachable mid-step-4) |
| **90** | **talk about the job directly — every breakdown uses this** |

The other 8 are `ไม่เกี่ยว`: clips from the seed that turned out not to be
about work. They are excluded from the funnel, themes, and highlights, and
the hero leads with 90 so the page never shows two totals for one bar chart.

## Intent mapping

The story asks for three levels; the pipeline emits four labels, mapping
one-to-one:

| Story level | Pipeline label | Count | Share of 90 |
|---|---|---|---|
| Low — venting | `บ่น` | 61 | 68% |
| Medium — wavering | `คิดจะลาออก` | 4 | 4% |
| High — decided | `ลาออกแล้ว` | 25 | 28% |
| excluded | `ไม่เกี่ยว` | 8 | — |

28% already gone is the headline. The medium bucket being tiny (4) is the
uncomfortable finding: people do not appear to spend long wavering in public.
They vent for a while and then post that they have left.

## Sections

1. **Hero** — reach, and the one sentence an executive should leave with
2. **Intent funnel** — 61 / 4 / 25 with the "only medium is still savable" note
3. **Themes** — clip-side and comment-side side by side; they agree
4. **Trend** — clips per quarter with views overlaid, 2019Q4–2026Q3
5. **Voice highlights** — top 12 by views, filterable by intent, link out
6. **Comment insights** — sentiment donut (69% agree), repeated Thai phrases,
   agreement broken down per theme, and the most-liked comment on each side

## What is deliberately excluded

- **`อื่นๆ` in both theme charts.** It is the model's catch-all, ranked third
  on the clip side and second on the comment side. It names no problem
  anyone can act on and would have pushed real themes down the chart.
- **`ระบุไม่ได้` in comment themes** — 1,869 comments of "จริง" and "555".
- **Transcripts.** 3–5K characters each and only `notable_quote` is shown.
- **Single-occurrence words** in the cloud — a typo cannot describe 5,000 people.
- **Brand-name and platform noise** (`เซเว่น`, `ค่ะ`, `555`) — see `NOISE` in
  the pipeline. `เซเว่น` appears in nearly every comment and would have
  dominated the cloud while carrying no information.

## What the agreement is about

"69% agree" alone is not actionable, so agreement is also broken down per
theme. The denominator is people who took a side (agree + disagree) —
folding `อื่นๆ` in would drown every disagreement.

| Theme | Agree | Disagree | Pushback |
|---|---|---|---|
| ค่าแรง | 228 | 22 | **8.8%** |
| workload | 1,086 | 79 | 6.8% |
| ลูกค้า | 120 | 7 | 5.5% |
| ตารางกะ | 377 | 14 | 3.6% |
| เพื่อนร่วมงาน | 307 | 9 | 2.8% |
| หัวหน้างาน | 106 | 3 | **2.8%** |

The pattern matters more than the numbers: **nobody defends a bad manager or
a toxic team, but plenty of people will argue the job is not that hard.**
Themes with near-zero pushback are the ones the audience treats as settled
fact.

Reading the disagreements themselves, most are not denials that the job is
hard. They are comparisons (`เซเว่นไม่หนัก เท่าโลตัส`, `งานโรงงานหนักกว่า`)
or the "then quit" reply (`ลาออกสิครับ ถ้าไม่อยากเหนื่อยก็กลับไปนอนอยู่บ้าน`,
845 likes). A handful object only to the swearing in a clip, not its content.

`top_comments` carries the six most-liked comments on each side so the split
has a voice rather than only a percentage.

## Phrases, not a word cloud

The first version was a single-word cloud and it said nothing: the largest
words were `ทำ`, `งาน`, `คน`, `ดี`. Worse, the segmenter splits `กะดึก` —
the single most repeated complaint in the corpus, 107 times — into `กะ` and
`ดึก`, scattering the loudest signal across two meaningless tokens.

Two- and three-word phrases carry their own meaning. The filters that make
the list readable, in order:

1. drop n-grams that are entirely stopwords, or under 5 characters
2. drop any that open or close on a connective (`DANGLING`) — `งานไม่`,
   `เคยทำ`, `กับคน` are half-sentences, not thoughts
3. drop drawn-out typing (`PHRASE_OK` rejects any triple-repeated character,
   so `มากกกกก` and its mis-split pieces never surface)
4. drop a phrase that is only ever the fragment of a more complete one:
   `เหนื่อยกับ` (50) always fronts `เหนื่อยกับคน` (31), and only the second
   means anything
5. drop the handful that survive all of the above but are still scaffolding
   or a bare restatement of the subject (`PHRASE_NOISE`: `พนักงานเซเว่น`,
   `งานนี้`, `เพลงนี้`, `เข้าทุ่มเลิก`)
6. separate encouragement (`SUPPORT_PHRASES`) into its own count — 168
   comments of `สู้ๆนะคะ` / `เก่งมาก` are real, but they are the audience
   being kind, not a problem to act on, and they would otherwise take four
   of the top slots

What this surfaced that single words had hidden: `เสียสุขภาพจิต` (22) and
`สุขภาพจิตดีขึ้น` (20) — people describe the job in mental-health terms both
while in it and after leaving; `โอฟรี` (23) — unpaid overtime; `โดนด่า` (23);
`งานรองรับ` (23) — people staying because they have nowhere to go.

## Quote widening

20 of the 88 quotes the model returned are under 30 characters, and several
were fragments: `ผลัดเช้าเข้างานกี่โมงคะ` without the `7 โมงปลายแล้ว` that
makes it a complaint. `widen_quote()` finds the fragment in the transcript or
on-screen text and extends forward to the next shot change, dropping a
trailing `@handle`.

It invents nothing. If the quote is not present in the source text, or the
only gain is a trailing emoji, the original stands. 5 of the 20 widened; the
other 15 are clips whose entire text really is that short (`22:00-11:00 💪`).

## Links out

All 12 highlight links point at live TikTok URLs, but **this machine cannot
open them** — TikTok has blocked this IP since step 4 and returns a 1462-byte
stub instead of the ~400KB page. Verified on all 12: identical byte count,
no embedded JSON.

The links are correct; the network is the problem. Test them from a phone on
mobile data, or from any machine that has not been scraping TikTok.

## PDPA

Names are replaced by `ผู้ใช้ #NN`, numbered by sheet row and stable across
rebuilds. `data.js` carries no `username`, `media_file`, or `transcript` key —
`test_no_username_reaches_the_browser` fails the build if one reappears.

`video_url` is kept, because the story requires a link to the real clip and a
TikTok URL necessarily contains the handle. Nothing on the page renders it as
text; it is reachable only by following the link off-site. This was the user's
explicit call.

## Verification

Rendered headlessly at 1440×900, 1024×1366, and 390×844: 5 sections, 4 charts,
12 quote cards, zero console errors, zero horizontal overflow at every size.

A JSX error in a Babel-in-browser page produces a blank white screen and no
visible message — the first render attempt did exactly that from one unclosed
brace. Screenshot the page after any edit; do not trust that it still works.

## Not done

- **Deploy to Cloudflare Pages** — deferred, local first. When wanted: the
  whole `web/` directory is the artifact, drop it into Pages as-is.
- **Access control** — the page is unlisted-by-nature but has no auth.
  `noindex` is set. Anyone with the URL can read it.
