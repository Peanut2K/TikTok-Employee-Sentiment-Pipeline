"""Guards for the parts of the dashboard pipeline that fail quietly.

A wrong number on an executive dashboard looks exactly like a right one, so
these check reconciliation and leakage rather than shape.

    .venv/bin/python test_dashboard_data.py
"""

import sys
from pathlib import Path
# Run standalone (no pytest on this machine): src/ must be importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import json

import sltiktok.dashboard as b

# Built once - the real run segments 5,181 comments and is the slow part.
DATA = b.build()


def clip(row, intent="บ่น", **kw):
    base = {"sheet_row": row, "video_id": f"v{row}", "username": f"u{row}",
            "video_url": f"https://www.tiktok.com/@u{row}/video/v{row}",
            "uploaded_at": "2025-03-23T14:03:09.000Z", "views": 100,
            "likes": 10, "shares": 1, "comments": 5, "themes": ["workload"],
            "intent_level": intent, "notable_quote": "q"}
    base.update(kw)
    return base


def test_every_pipeline_intent_has_a_bucket():
    """A new label added upstream must not silently vanish from the funnel."""
    seen = {c["intent_level"] for c in b.load()["clips"] if c.get("intent_level")}
    assert seen <= set(b.INTENT_BUCKETS), \
        f"unmapped intent labels: {seen - set(b.INTENT_BUCKETS)}"


def test_clip_counts_reconcile():
    o = DATA["overview"]
    # What the model read, plus what only a human screener labelled, is every
    # clip the breakdowns are built from.
    assert o["clips_analyzed"] + o["clips_from_human_label"] \
        == o["clips_with_intent"]
    assert o["clips_analyzed"] <= o["clips_collected"]
    assert o["clips_with_intent"] <= o["clips_collected"]
    assert sum(i["value"] for i in o["intents"]) == o["clips_with_intent"]


def test_survey_total_is_every_account_looked_at():
    """100 accounts were surveyed; the page says 100 and must mean it."""
    o = DATA["overview"]
    seed = json.loads(b.paths.SEED.read_text(encoding="utf-8"))
    assert o["clips_collected"] == len(seed)
    assert o["accounts"] == len(seed)


def test_engagement_totals_ignore_clips_with_no_metadata():
    """A row added back from the seed has no views - it must stay that way.

    @ai_pon08 counts in the survey but never returned metadata. If it ever
    carried engagement numbers they would be invented, and every per-clip
    average on the page would be wrong.
    """
    clips = [clip(1, views=100, likes=10, shares=1),
             {"video_id": "seed-2", "sheet_row": 2, "username": "u2",
              "human_category": "บ่นงาน", "intent_level": "", "themes": [],
              "notable_quote": "", "analysis_status": "no_metadata"}]
    o = b.overview(clips, [])
    assert o["views"] == 100 and o["likes"] == 10 and o["shares"] == 1
    assert o["clips_collected"] == 2
    assert o["clips_with_intent"] == 2
    assert o["clips_analyzed"] == 1


def test_no_username_reaches_the_browser():
    """Pseudonymization is the whole PDPA story - one leaked key breaks it."""
    blob = json.dumps(DATA, ensure_ascii=False)
    assert '"username"' not in blob
    assert '"media_file"' not in blob
    assert '"transcript"' not in blob
    for h in DATA["highlights"]:
        assert h["person"].startswith("ผู้ใช้")


def test_highlight_links_are_the_only_place_a_handle_appears():
    """The link must point at the real clip; the card text must not name it."""
    for h in DATA["highlights"]:
        assert h["url"].startswith("https://www.tiktok.com/@")
        handle = h["url"].split("/@")[1].split("/")[0]
        assert handle not in h["person"]
        assert handle not in h["quote"]


def test_pseudonym_is_stable_and_unique():
    assert b.pseudonym(7) == "ผู้ใช้ #07"
    rows = [1, 2, 3, 99]
    assert len({b.pseudonym(r) for r in rows}) == len(rows)
    assert b.pseudonym(7) == b.pseudonym(7)


def test_trend_has_no_missing_quarters():
    """A skipped empty quarter would compress time and fake a spike."""
    clips = [clip(1, uploaded_at="2024-01-05T00:00:00.000Z"),
             clip(2, uploaded_at="2025-01-05T00:00:00.000Z")]
    periods = [r["period"] for r in b.trend(clips)]
    assert periods == ["2024Q1", "2024Q2", "2024Q3", "2024Q4", "2025Q1"]
    assert [r["clips"] for r in b.trend(clips)] == [1, 0, 0, 0, 1]


def test_only_the_last_quarter_is_marked_partial():
    """The final bar covers a shorter window than the ones beside it.

    Collection stops on the day it stops, mid-quarter. Drawn as an equal bar
    the shortfall reads as "the problem is going away" when it only means
    "we stopped looking", so the page needs to know which bar is incomplete.
    """
    clips = [clip(1, uploaded_at="2025-01-05T00:00:00.000Z"),
             clip(2, uploaded_at="2025-04-05T00:00:00.000Z"),
             clip(3, uploaded_at="2025-07-05T00:00:00.000Z")]
    rows = b.trend(clips)
    assert [r["partial"] for r in rows] == [False, False, True]


def test_gap_quarters_are_not_marked_partial():
    """Only the quarter collection stopped in is incomplete.

    An empty quarter in the middle is a real zero - nobody posted - and
    hollowing it out would excuse a gap that the data genuinely shows.
    """
    clips = [clip(1, uploaded_at="2024-01-05T00:00:00.000Z"),
             clip(2, uploaded_at="2024-07-05T00:00:00.000Z")]
    rows = b.trend(clips)
    assert [(r["period"], r["clips"], r["partial"]) for r in rows] == [
        ("2024Q1", 1, False),
        ("2024Q2", 0, False),   # a real gap, drawn as a real zero
        ("2024Q3", 1, True),    # where collection stopped
    ]


def test_highlights_skip_clips_with_nothing_to_show():
    """A quote is what a card shows; without one there is nothing to put up."""
    clips = [clip(1, views=999, notable_quote=""), clip(2, views=10)]
    got = b.highlights(clips)
    assert [h["id"] for h in got] == ["v2"]


def test_highlights_keep_clips_that_never_mention_quitting():
    """The loudest clip in the real set carries ไม่เกี่ยว.

    715K views on the health risks of shift work, tagged workload and
    ตารางกะ. Filtering the label out of highlights silently discarded the
    best-performing evidence for the report's main finding.
    """
    clips = [clip(1, intent="ไม่เกี่ยว", views=500), clip(2, views=10)]
    assert [h["id"] for h in b.highlights(clips)] == ["v1", "v2"]


def test_short_quote_gets_its_sentence_back():
    """A question without its answer reads as noise on a card."""
    c = clip(1, notable_quote="ผลัดเช้าเข้างานกี่โมงคะ",
             transcript="ค่ะ ผลัดเช้าเข้างานกี่โมงคะ 7 โมงปลายแล้ว")
    assert b.widen_quote(c["notable_quote"], c) == \
        "ผลัดเช้าเข้างานกี่โมงคะ 7 โมงปลายแล้ว"


def test_widening_never_invents_text():
    """A quote the source does not contain must survive untouched."""
    c = clip(1, notable_quote="ไม่มีในคลิป", transcript="คนละเรื่องกันเลย")
    assert b.widen_quote("ไม่มีในคลิป", c) == "ไม่มีในคลิป"


def test_widening_leaves_long_quotes_alone():
    long = "ก" * (b.QUOTE_MIN + 5)
    c = clip(1, notable_quote=long, transcript=long + " และยังมีต่ออีกยาว")
    assert b.widen_quote(long, c) == long


def test_widening_rejects_gains_that_are_only_punctuation():
    """Picking up a trailing emoji leaves the same fragment, slightly longer."""
    c = clip(1, notable_quote="22:00-11:00", on_screen_text="22:00-11:00 💪")
    assert b.widen_quote("22:00-11:00", c) == "22:00-11:00"


def test_widening_stops_at_a_shot_change():
    """A newline in on-screen text is a cut to a different frame."""
    c = clip(1, notable_quote="สิ่งที่ต้องเจอ",
             on_screen_text="สิ่งที่ต้องเจอ ทุกวันไม่มีหยุดพัก\nเอาชาไทยแก้วนึงค่ะ")
    assert b.widen_quote("สิ่งที่ต้องเจอ", c) == "สิ่งที่ต้องเจอ ทุกวันไม่มีหยุดพัก"


def test_widening_drops_a_trailing_handle():
    """A tag at the end is credit, not something the person said."""
    full = "บางวันประสาทแดกกับงานแบบสุดๆ จนอยากลาออก"
    c = clip(1, notable_quote="บางวันประสาทแดก",
             on_screen_text=f"{full} @panpinyo")
    assert b.widen_quote("บางวันประสาทแดก", c) == full


def test_highlights_are_ordered_by_reach():
    clips = [clip(1, views=10), clip(2, views=900), clip(3, views=50)]
    assert [h["views"] for h in b.highlights(clips)] == [900, 50, 10]


def test_human_label_stands_in_when_the_model_never_read_the_clip():
    """Two of the 100 accounts never reached the model.

    @ai_pon08's metadata never came back and @ratti_21's mp4 was unreachable.
    A screener watched both and filed them บ่นงาน, so they count on that
    label rather than being dropped and understating the survey by two.
    """
    c = {"video_id": "x", "human_category": "บ่นงาน", "intent_level": ""}
    assert b.intent_of(c) == "บ่น"
    # The model's own label always wins where there is one.
    assert b.intent_of({**c, "intent_level": "ลาออกแล้ว"}) == "ลาออกแล้ว"
    # An unrecognised category is not forced into a bucket.
    assert b.intent_of({**c, "human_category": "อย่างอื่น"}) is None


def test_no_intent_label_is_dropped_on_topic():
    """ไม่เกี่ยว means "not about quitting", not "not about the job".

    All 8 clips carrying it are still 7-Eleven workplace content - a shift
    walkthrough, a promotion, the health risks of night work - and several
    carry workload themes. Dropping them lost the most-watched clip in the
    set, so they count with the rest.
    """
    clips = [clip(1, themes=["ค่าแรง"]),
             clip(2, intent="ไม่เกี่ยว", themes=["ค่าแรง"])]
    o = b.overview(clips, [])
    assert o["themes"] == [{"name": "ค่าแรง", "value": 2}]
    assert o["clips_with_intent"] == 2
    assert o["clips_excluded"] == 0


def test_labels_sharing_a_bucket_produce_one_funnel_row():
    """บ่น and ไม่เกี่ยว both sit in low; the funnel must show one bar."""
    clips = [clip(1, intent="บ่น"), clip(2, intent="ไม่เกี่ยว"),
             clip(3, intent="ลาออกแล้ว")]
    rows = b.overview(clips, [])["intents"]
    assert [r["bucket"] for r in rows] == ["low", "medium", "high"]
    assert [r["value"] for r in rows] == [2, 0, 1]


def test_phrases_are_multi_word_and_meaningful():
    """Single words said nothing; a phrase has to carry its own meaning."""
    got = {p["phrase"] for p in DATA["phrases"]}
    # "กะ" and "ดึก" separately are noise - together they are the single
    # loudest complaint in the corpus.
    assert "กะดึก" in got
    assert "กะ" not in got and "ดึก" not in got
    assert all(len(p) >= 5 for p in got)


def test_phrases_reject_fragments():
    """A phrase that opens or closes on a connective is half a sentence."""
    for p in DATA["phrases"]:
        assert p["phrase"] not in b.PHRASE_NOISE
        assert not p["phrase"].endswith("ไม่")


def test_phrases_drop_drawn_out_typing():
    """"มากกกกก" and its mis-segmented pieces are typing, not a topic."""
    comments = [{"text": "เหนื่อยมากกกกก", "sentiment": "เห็นด้วย"}] * 5
    assert not [p for p in b.phrases(comments) if "กกก" in p["phrase"]]


def test_phrases_prefer_the_longer_form():
    """"เหนื่อยกับ" is always the front of something; the whole is the point."""
    got = [p["phrase"] for p in DATA["phrases"]]
    for i, a in enumerate(got):
        for j, c in enumerate(got):
            assert i == j or a not in c, f"{a!r} is contained in {c!r}"


def test_phrases_split_by_sentiment():
    comments = [{"text": "ค่าแรงน้อยมาก", "sentiment": "เห็นด้วย"},
                {"text": "ค่าแรงน้อยมาก", "sentiment": "ไม่เห็นด้วย"}]
    got = {p["phrase"]: p for p in b.phrases(comments)}
    top = next(iter(got.values()))
    assert top["count"] == 2
    assert top["agree"] == 1 and top["disagree"] == 1


def test_sentiment_by_theme_reconciles_with_the_totals():
    """Per-theme counts must add up to the same comments the donut counts."""
    rows = DATA["sentiment_by_theme"]
    for r in rows:
        assert r["agree"] + r["disagree"] + r["other"] == r["total"]
    themed = {t["name"]: t["value"] for t in DATA["comment_themes"]}
    assert {r["name"]: r["total"] for r in rows} == themed


def test_disagree_pct_counts_only_people_who_took_a_side():
    """Lumping อื่นๆ into the denominator would hide every disagreement."""
    comments = [{"theme": "ค่าแรง", "sentiment": "เห็นด้วย"},
                {"theme": "ค่าแรง", "sentiment": "ไม่เห็นด้วย"},
                {"theme": "ค่าแรง", "sentiment": "อื่นๆ"}] * 1
    row = b.sentiment_by_theme(comments)[0]
    assert row["total"] == 3
    assert row["disagree_pct"] == 50.0
    # Both sides are shown on the page, so they have to be halves of the same
    # denominator - อื่นๆ excluded from each.
    assert row["agree_pct"] == 50.0
    assert row["agree_pct"] + row["disagree_pct"] == 100.0


def test_top_comments_are_ranked_and_non_empty():
    for side in ("เห็นด้วย", "ไม่เห็นด้วย"):
        got = DATA["top_comments"][side]
        assert got, f"no comments on the {side} side"
        assert [c["likes"] for c in got] == sorted(
            (c["likes"] for c in got), reverse=True)
        assert all(c["text"].strip() for c in got)


def test_support_phrases_are_separated_from_topics():
    """Encouragement is real but it is not a problem to act on."""
    kinds = {p["phrase"]: p["kind"] for p in DATA["phrases"]}
    assert kinds.get("เก่งมาก") == "support"
    assert kinds.get("กะดึก") == "topic"


def test_comment_themes_drop_the_non_answers():
    """ระบุไม่ได้ and อื่นๆ would both outrank real themes while saying nothing."""
    comments = [{"theme": "ระบุไม่ได้"}, {"theme": "อื่นๆ"}, {"theme": "ค่าแรง"}]
    assert b.comment_themes(comments) == [{"name": "ค่าแรง", "value": 1}]


def test_clip_themes_drop_the_catch_all():
    clips = [clip(1, themes=["อื่นๆ", "ค่าแรง"])]
    assert b.overview(clips, [])["themes"] == [{"name": "ค่าแรง", "value": 1}]


def test_real_run_produces_every_section():
    assert DATA["overview"]["clips_analyzed"] == 98
    assert DATA["overview"]["comments"] == 5181
    assert len(DATA["highlights"]) == b.HIGHLIGHT_COUNT
    assert len(DATA["phrases"]) == b.PHRASE_COUNT
    assert DATA["trend"][0]["period"] == "2019Q4"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nall passed")
