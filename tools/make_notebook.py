"""Generate pipeline.ipynb. Kept as a script so the notebook is reproducible."""
import nbformat as nbf
from pathlib import Path

nb = nbf.v4.new_notebook()
C = []          # cells
md = lambda s: C.append(nbf.v4.new_markdown_cell(s.strip()))
code = lambda s: C.append(nbf.v4.new_code_cell(s.strip()))

md("""
# 7-Eleven staff voices — pipeline

Five stages. Each cell runs on its own and reads the previous stage's output
from `out/`, so you can re-run one step without repeating the others.

**Two stages spend real money** (Enrich = Apify, Analyze = Gemini). Both are
guarded by a `RUN = False` flag: running every cell top to bottom costs
nothing until you flip one deliberately.

Setup, once: `.venv/bin/pip install -e .`
""")

# ---------------------------------------------------------------- config
md("""
## 0 · Configuration

Everything tunable lives here. Change a value, re-run this cell, then run the
stage you care about. Nothing else in the notebook hardcodes these.
""")

code('''
from sltiktok.config import Config

cfg = Config(
    # --- discover --------------------------------------------------
    # keywords=["ลาออกเซเว่น", "พนักงานเซเว่น"],   # uncomment to narrow
    target_accounts=200,
    scrolls_per_keyword=25,
    headless=True,

    # --- enrich (Apify) --------------------------------------------
    # videos_per_profile applies only under --scrape-profiles. The default
    # path takes the one clip per account that the sheet already names.
    videos_per_profile=30,
    comments_per_video=100,
    max_videos_to_comment=500,

    # --- analyze (Gemini) ------------------------------------------
    model="gemini-3.7-flash",
    comment_batch=50,
    clip_workers=2,
    comment_workers=4,
    keep_media=False,

    # --- dashboard --------------------------------------------------
    highlight_count=12,
    phrase_count=40,
)

print(cfg.summary())
''')

code('''
# Where each stage reads and writes. Run this to see what already exists.
from pathlib import Path
from sltiktok import analyze, dashboard, discover, enrich, ocr

# out/ is grouped by what it costs to lose: 01_raw and 02_analyzed were
# paid for, 03_export rebuilds for free, cache/ is disposable.
for label, p in [
    ("01 seed metadata ", analyze.VIDEOS),
    ("01 raw comments  ", analyze.COMMENTS),
    ("02 clips analyzed", analyze.CLIPS_RESULT),
    ("02 comments class", analyze.COMMENTS_RESULT),
    ("03 dashboard.json", dashboard.SRC),
    ("   web/data.js   ", dashboard.DEST),
]:
    size = f"{p.stat().st_size / 1e6:6.2f} MB" if p.exists() else "   missing"
    print(f"{label}  {size}  {p}")
''')

# ---------------------------------------------------------------- 1 discover
md("""
## 1 · Discover — find the accounts

Playwright walks TikTok search for every keyword and collects the accounts
behind the results. Free, but needs a logged-in session and a visible browser
the first time (TikTok shows a slider captcha).

Produces: `accounts.json` + the Google Sheet. Search caps at ~30-40 results per
query however far you scroll, so coverage comes from more keywords, not deeper
scrolling.
""")

code('''
# One-off: opens a browser, log in by hand, saves the session.
# discover.login()
print("session:", "saved" if discover.STATE.exists() else "MISSING — run discover.login()")
''')

code('''
RUN = False          # flip to actually scrape

if RUN:
    rows = discover.scrape(
        target=cfg.target_accounts,
        per_keyword_scrolls=cfg.scrolls_per_keyword,
        headless=cfg.headless,
    )
    print(f"{len(rows)} accounts")
    for r in rows[:5]:
        print(f"  @{r['username']:<22} {r['category'] or '?':<14} {r['caption'][:45]}")
else:
    print("RUN is False — nothing scraped.")
''')

# ---------------------------------------------------------------- 2 enrich
md("""
## 2 · Enrich — engagement counts and comments  💸

Apify runs from its own IPs and returns what the search page will not: views,
likes, shares, `uploaded_at`, and the comment threads.

Scope is **one clip per account** — the `video_url` the sheet already names.
That is the default, and it scrapes no feeds at all, so the videos half of the
bill is $0.

`--scrape-profiles` opts into the other shape: walk each creator's recent feed
(`videos_per_profile` clips each, then filter). Several clips per account and a
much bigger bill — roughly 15× on the same account list.

**This spends money.** Estimate first; the cell below only prints.
""")

code('''
# Costs nothing — prints volume and dollars before you commit.
usernames = enrich.seed_accounts()
enrich.estimate(
    usernames,
    cfg.videos_per_profile,
    cfg.comments_per_video,
    cfg.max_videos_to_comment,
)
''')

code('''
RUN = False          # flip only after reading the estimate above

if RUN:
    import subprocess, sys
    # Runs the stage's own CLI: it handles resume, run bookkeeping and the
    # budget guard, none of which is worth reimplementing in a cell.
    subprocess.run([sys.executable, "-m", "sltiktok.enrich"], check=True)
else:
    print("RUN is False — no Apify calls, nothing spent.")
''')

# ---------------------------------------------------------------- 3 analyze
md("""
## 3 · Analyze — Gemini reads the clips  💸

One call per clip does transcription and classification together. The model
receives the **whole mp4**, not just audio, so it reads on-screen text too —
many of these clips have no speech at all, just captions over music.

**This spends money.** Estimate first.
""")

code('''
import json

videos = json.loads(analyze.VIDEOS.read_text(encoding="utf-8"))
comments = json.loads(analyze.COMMENTS.read_text(encoding="utf-8")) \\
    if analyze.COMMENTS.exists() else []

analyze.estimate(videos, comments)
''')

code('''
RUN = False          # flip only after reading the estimate above

if RUN:
    import subprocess, sys
    cmd = [sys.executable, "-m", "sltiktok.analyze"]
    if cfg.keep_media:
        cmd.append("--keep-media")
    subprocess.run(cmd, check=True)
else:
    print("RUN is False — no Gemini calls, nothing spent.")
''')

code('''
# Re-export from cached results. No API calls, no cost — safe to re-run.
# Use after editing export logic, or to rebuild out/dashboard.json.
import subprocess, sys
subprocess.run([sys.executable, "-m", "sltiktok.analyze", "--export"], check=True)
''')

# ---------------------------------------------------------------- 4 ocr
md("""
## 4 · OCR — cover-frame fallback

Only for clips Gemini could not process (TikTok refused the mp4). Reads one
frame, so the text is worse — Thai tone marks drop — but it beats an empty row.

Currently every one of the 98 analysed clips came back from Gemini, so this
stage has nothing to do. It stays for the run where that is not true.
""")

code('''
import json
videos = json.loads(analyze.VIDEOS.read_text(encoding="utf-8"))
todo = [v for v in videos if ocr.needs_ocr(v)]
print(f"{len(todo)} clips would be OCR'd (of {len(videos)})")

RUN = False          # local easyocr, costs nothing but is slow
if RUN:
    import subprocess, sys
    subprocess.run([sys.executable, "-m", "sltiktok.ocr"], check=True)
''')

# ---------------------------------------------------------------- 5 dashboard
md("""
## 5 · Dashboard — build the page data

Aggregates everything into `web/data.js`. Free and fast — re-run it as often
as you like. Thai segmentation (pythainlp) happens **here**, never in the
browser: the page ships precomputed phrase counts only.
""")

code('''
import json
from sltiktok import dashboard

raw = dashboard.load()
clips, comments = raw["clips"], raw["comments"]

# Each piece is a plain function, so a cell can call one and look at it
# without rebuilding the whole file.
data = {
    "overview":           dashboard.overview(clips, comments),
    "trend":              dashboard.trend(clips),
    "highlights":         dashboard.highlights(clips, limit=cfg.highlight_count),
    "phrases":            dashboard.phrases(comments, size=cfg.phrase_count),
    "comment_sentiment":  dashboard.comment_sentiment(comments),
    "comment_themes":     dashboard.comment_themes(comments),
    "sentiment_by_theme": dashboard.sentiment_by_theme(comments),
    "top_comments":       dashboard.top_comments(comments),
}

o = data["overview"]
print(f"clips {o['clips_collected']} / analyzed {o['clips_analyzed']} "
      f"/ with intent {o['clips_with_intent']}")
print(f"comments {o['comments']}  accounts {o['accounts']}  views {o['views']:,}")
print(f"trend {len(data['trend'])} quarters  highlights {len(data['highlights'])}  "
      f"phrases {len(data['phrases'])}")
''')

code('''
# Write it out. This is the only cell that touches web/data.js.
body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
dashboard.DEST.write_text(f"window.DATA = {body};\\n", encoding="utf-8")
print(f"wrote {dashboard.DEST}  {dashboard.DEST.stat().st_size / 1024:.0f}KB")
''')

# ---------------------------------------------------------------- 6 inspect
md("""
## 6 · Inspect — check the numbers before showing anyone

The dashboard is a summary; these cells are how you tell whether the summary
is honest.
""")

code('''
# The three clip counts, which are NOT interchangeable.
o = data["overview"]
print(f"collected      {o['clips_collected']:>4}   in the sheet, with metadata")
print(f"analyzed       {o['clips_analyzed']:>4}   came back from the model")
print(f"with intent    {o['clips_with_intent']:>4}   talk about the job — every breakdown uses this")
print(f"excluded       {o['clips_excluded']:>4}   ไม่เกี่ยว — not about work\\n")
for i in o["intents"]:
    pct = i["value"] / max(o["clips_with_intent"], 1) * 100
    print(f"  {i['label']:<14} {i['value']:>3}  {pct:4.1f}%")
''')

code('''
# What people repeat, and what they push back on.
print("top phrases")
for p in [p for p in data["phrases"] if p["kind"] == "topic"][:15]:
    print(f"  {p['phrase']:<22} {p['count']:>4}   agree {p['agree']:>3} / disagree {p['disagree']:>3}")

print("\\npushback by theme (of those who took a side)")
for r in data["sentiment_by_theme"]:
    print(f"  {r['name']:<16} {r['disagree_pct']:>5.1f}%   "
          f"agree {r['agree']:>4} / disagree {r['disagree']:>3}")
''')

code('''
# PDPA guard: no real username may reach the browser.
blob = json.dumps(data, ensure_ascii=False)
for key in ('"username"', '"media_file"', '"transcript"'):
    assert key not in blob, f"LEAK: {key} reached the page data"
assert all(h["person"].startswith("ผู้ใช้") for h in data["highlights"])
print("no usernames, no transcripts, no media paths in the page data")
''')

md("""
## Tests

The suites are the real safety net — 59 checks, no pytest needed.

```bash
for t in tests/test_*.py; do .venv/bin/python "$t"; done
```

## Before committing this notebook

Outputs are stored inside the `.ipynb` as JSON and make diffs unreadable.
Clear them first: **Kernel → Restart & Clear Output**, or

```bash
.venv/bin/jupyter nbconvert --clear-output --inplace pipeline.ipynb
```
""")

nb["cells"] = C
nb.metadata = {
    "kernelspec": {"display_name": "Python 3", "language": "python",
                   "name": "python3"},
    "language_info": {"name": "python"},
}
out = Path("/home/sapon/Desktop/Projects/sl-tiktok/pipeline.ipynb")
nbf.write(nb, str(out))
print(f"wrote {out}  {len(C)} cells")
