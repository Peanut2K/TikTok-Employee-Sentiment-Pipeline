"""Where each stage's files live, grouped by what it costs to lose them.

    01_raw/       bought from Apify. Deleting means paying again.
    02_analyzed/  Gemini output. Deleting means paying again.
    03_export/    derived from 02. Free to rebuild: analyze --export.
    cache/        mp4s and cover images. Safe to delete any time.

The numbers are the pipeline order, so the directory listing reads in the
order the stages ran. Anything under cache/ is disposable by definition -
that is the only group safe to clear when disk runs short.

One module owns these names so five stages cannot drift into disagreeing
about where a file lives.
"""
from pathlib import Path

# Project root: data and credentials live there, not next to this module.
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out"

RAW = OUT / "01_raw"
ANALYZED = OUT / "02_analyzed"
EXPORT = OUT / "03_export"
CACHE = OUT / "cache"

# --- 01_raw: scraped, and billed for -----------------------------------
VIDEOS = RAW / "seed_100_metadata.json"
COMMENTS_RAW = RAW / "comments_raw.json"
VIDEOS_RAW = RAW / "videos_raw.json"
VIDEOS_FILTERED = RAW / "videos_filtered.json"
RUNS = RAW / "runs.json"
COVERAGE = RAW / "coverage_report.json"

# --- 02_analyzed: model output, also billed for ------------------------
CLIPS_RESULT = ANALYZED / "clips_analyzed.json"
COMMENTS_RESULT = ANALYZED / "comments_classified.json"
RAW_LOG = ANALYZED / "gemini_raw.jsonl"
OCR_TEXT = ANALYZED / "ocr_text.json"

# --- 03_export: rebuilt for free from 02 -------------------------------
# dashboard.json is the last file before the web page; sltiktok.dashboard
# reads this one and nothing else.
DASHBOARD = EXPORT / "dashboard.json"
CLIPS_CSV = EXPORT / "clips.csv"
COMMENTS_CSV = EXPORT / "comments.csv"
REPORT = EXPORT / "analysis_report.json"
FAILED_REASONS = EXPORT / "failed_reasons.json"

# --- cache: disposable --------------------------------------------------
MEDIA = CACHE / "media"
COVERS = CACHE / "covers"
COOKIES = CACHE / ".tt_cookies.txt"

# --- outside out/ -------------------------------------------------------
SEED = ROOT / "filtered_100.json"
WEB_DATA = ROOT / "web" / "data.js"


def ensure():
    """Create the stage directories. Safe to call repeatedly."""
    for d in (RAW, ANALYZED, EXPORT, CACHE):
        d.mkdir(parents=True, exist_ok=True)
