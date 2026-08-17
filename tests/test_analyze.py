"""Checks the parts of analyze.py that fail quietly. Run:

    .venv/bin/python test_analyze.py

No network, no API key — the Gemini calls are the expensive part and the
export/schema logic is what silently produces a wrong dashboard.
"""

import sys
from pathlib import Path
# Run standalone (no pytest on this machine): src/ must be importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import json

from sltiktok.analyze import (COMMENT_SCHEMA, CLIP_SCHEMA, NO_THEME, THEMES, batch_key,
                     comment_prompt, clip_prompt, export, tally)


def test_no_empty_enum_values():
    """The API rejects "" as an enum member with a 400, and the whole comment
    pass fails at once. This exact bug cost a full smoke run."""
    def enums(node):
        if isinstance(node, dict):
            if "enum" in node:
                yield node["enum"]
            for v in node.values():
                yield from enums(v)
        elif isinstance(node, list):
            for v in node:
                yield from enums(v)

    for schema in (CLIP_SCHEMA, COMMENT_SCHEMA):
        for values in enums(schema):
            assert all(v.strip() for v in values), f"empty enum member in {values}"


def test_prompt_offers_every_schema_theme():
    """A theme in the schema but not in the prompt never gets chosen, and one
    in the prompt but not the schema makes the response invalid."""
    prompt = comment_prompt("ctx", ["a"])
    for t in THEMES:
        assert t in prompt, t
    assert NO_THEME in prompt

    cp = clip_prompt({"caption": "x"})
    for t in THEMES:
        assert t in cp, t


def test_clip_prompt_falls_back_when_caption_is_empty():
    # Half these clips carry no caption; the prompt must not send a bare
    # "caption:" line that reads as a missing field.
    p = clip_prompt({"caption": "", "scraped_caption": "#เซเว่น"})
    assert "#เซเว่น" in p
    assert "(ไม่มี)" in clip_prompt({})


def test_comment_prompt_numbers_every_comment():
    """Results come back keyed by index, so the numbering has to be stable."""
    p = comment_prompt("ctx", ["aaa", "bbb", "ccc"])
    assert "0. aaa" in p and "1. bbb" in p and "2. ccc" in p


def test_batch_key_is_unique_per_slice():
    url = "https://www.tiktok.com/@a/video/123"
    assert batch_key(url, 0) == "123#0"
    assert batch_key(url, 50) != batch_key(url, 0)


def test_export_joins_clips_comments_and_counts(tmp_path=None):
    videos = [
        {"video_url": "https://www.tiktok.com/@a/video/1", "username": "a",
         "caption": "c1", "views": 10, "likes": 2, "comments": 3, "shares": 0,
         "uploaded_at": "2026-01-01", "duration": 15, "category": "บ่นงาน"},
        {"video_url": "https://www.tiktok.com/@b/video/2", "username": "b",
         "caption": "c2", "views": 5, "likes": 1, "comments": 0, "shares": 0,
         "uploaded_at": "2026-01-02", "duration": 20, "category": ""},
    ]
    clips = {
        "1": {"transcript": "t", "on_screen_text": "", "themes": ["ค่าแรง"],
              "intent_level": "บ่น", "notable_quote": "q", "relevant": True,
              "confidence": "high", "status": "ok"},
        "2": {"transcript": "", "on_screen_text": "", "themes": [],
              "intent_level": "", "notable_quote": "", "relevant": None,
              "confidence": "", "status": "failed"},
    }
    batches = {"1#0": [
        {"cid": "x", "video_url": "https://www.tiktok.com/@a/video/1", "text": "ใช่",
         "likes": 5, "sentiment": "เห็นด้วย", "theme": "ค่าแรง", "status": "ok"},
        {"cid": "y", "video_url": "https://www.tiktok.com/@a/video/1", "text": "ไม่",
         "likes": 1, "sentiment": "ไม่เห็นด้วย", "theme": NO_THEME, "status": "ok"},
    ]}

    import sltiktok.analyze as analyze
    from pathlib import Path
    import tempfile
    original = analyze.OUT
    analyze.OUT = Path(tempfile.mkdtemp())
    analyze.REPORT = analyze.OUT / "analysis_report.json"
    try:
        rep = export(videos, clips, batches)
        rows = json.loads((analyze.OUT / "dashboard.json").read_text())["clips"]
    finally:
        analyze.OUT, analyze.REPORT = original, original / "analysis_report.json"

    assert rep["clips_analyzed"] == 1 and rep["clips_failed"] == 1
    assert rep["comments_classified"] == 2
    assert rep["comment_sentiment"]["เห็นด้วย"] == 1

    row = next(r for r in rows if r["video_id"] == "1")
    assert row["comments_agree"] == 1 and row["comments_disagree"] == 1
    # A failed clip must still appear, flagged, not silently dropped.
    failed = next(r for r in rows if r["video_id"] == "2")
    assert failed["analysis_status"] == "failed" and failed["intent_level"] == ""
    # The human's own category survives next to the model's, for spot-checking.
    assert row["human_category"] == "บ่นงาน"


def test_ocr_fills_in_only_where_the_video_pass_failed():
    """A clip TikTok refused to serve still has step 3's cover OCR. It must
    never overwrite a real Gemini reading, and its origin must be visible."""
    import sltiktok.analyze as analyze
    videos = [
        {"video_url": "https://www.tiktok.com/@a/video/1", "username": "a"},
        {"video_url": "https://www.tiktok.com/@b/video/2", "username": "b"},
        {"video_url": "https://www.tiktok.com/@c/video/3", "username": "c"},
    ]
    clips = {
        "1": {"transcript": "", "on_screen_text": "จาก gemini", "themes": [],
              "intent_level": "บ่น", "notable_quote": "", "relevant": True,
              "confidence": "high", "status": "ok"},
        "2": {"transcript": "", "on_screen_text": "", "themes": [],
              "intent_level": "", "notable_quote": "", "relevant": None,
              "confidence": "", "status": "failed"},
        "3": {"transcript": "", "on_screen_text": "", "themes": [],
              "intent_level": "", "notable_quote": "", "relevant": None,
              "confidence": "", "status": "failed"},
    }

    from pathlib import Path
    import tempfile
    original_out, original_load = analyze.OUT, analyze.load_ocr
    analyze.OUT = Path(tempfile.mkdtemp())
    analyze.REPORT = analyze.OUT / "analysis_report.json"
    analyze.load_ocr = lambda: {"1": "ocr ที่ไม่ควรใช้", "2": "ocr หน้าปก"}
    try:
        rep = analyze.export(videos, clips, {})
        rows = json.loads((analyze.OUT / "dashboard.json").read_text())["clips"]
    finally:
        analyze.OUT, analyze.load_ocr = original_out, original_load
        analyze.REPORT = original_out / "analysis_report.json"

    by_id = {r["video_id"]: r for r in rows}
    assert by_id["1"]["on_screen_text"] == "จาก gemini"
    assert by_id["1"]["text_source"] == "gemini"
    assert by_id["2"]["on_screen_text"] == "ocr หน้าปก"
    assert by_id["2"]["text_source"] == "ocr_cover"
    # No OCR either — stays empty rather than borrowing from anywhere.
    assert by_id["3"]["on_screen_text"] == "" and by_id["3"]["text_source"] == "none"
    assert rep["clips_text_from_ocr_fallback"] == 1
    assert rep["clips_with_no_text_at_all"] == 1


def test_media_name_follows_sheet_order():
    """Files are named by sheet row so they line up with what a person reads.
    A clip outside the seed must not borrow a neighbour's number or name."""
    from sltiktok.analyze import media_name
    order = {"111": (3, "noonanduanglada"), "222": (17, "a.b/c d")}
    assert media_name("111", order) == "003_noonanduanglada.mp4"
    # Anything a filesystem would choke on is flattened, nothing is dropped.
    assert media_name("222", order) == "017_a.b_c_d.mp4"
    # Unknown id keeps the raw video id rather than being misfiled.
    assert media_name("999", order) == "999.mp4"


def test_tally_sorts_by_count():
    assert list(tally(["a", "b", "b", "c", "b", "a"])) == ["b", "a", "c"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nall passed")
