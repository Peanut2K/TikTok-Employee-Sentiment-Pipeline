"""Checks the filter and coverage logic. Run: .venv/bin/python test_apify.py

No network, no token — these are the parts that fail silently and cost money.
"""

import sys
from pathlib import Path
# Run standalone (no pytest on this machine): src/ must be importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sltiktok.enrich import relevant, video_url, author_of, coverage_report, seed_accounts, run_info


def test_normalize_apidojo_to_clockworks_shape():
    """Verbatim apidojo item. Counts were confirmed identical to clockworks
    on 5 overlapping videos, so the mapping must not lose or rename any."""
    from sltiktok.enrich import normalize
    item = {
        "id": "7650464146546265365",
        "title": "รอบนี้ เจอหัวหน้าดีแตกแทน #หัวหน้าเฮงซวย",
        "views": 4820, "likes": 138, "comments": 8, "shares": 0, "bookmarks": 0,
        "uploadedAtFormatted": "2026-06-12T11:08:17.000Z",
        "postPage": "https://www.tiktok.com/@n_kacha629/video/7650464146546265365",
        "channel": {"username": "n_kacha629", "name": "NCK mart", "id": "701037"},
        "video": {"cover": "http://x/c.jpg", "duration": 30},
    }
    v = normalize(item)
    assert v["playCount"] == 4820 and v["diggCount"] == 138
    assert v["commentCount"] == 8 and v["shareCount"] == 0
    assert author_of(v) == "n_kacha629"
    assert video_url(v).endswith("/7650464146546265365")
    assert relevant(v)[0], "workplace clip must survive the filter after mapping"
    assert v["createTimeISO"] == "2026-06-12T11:08:17.000Z"


def test_normalize_leaves_clockworks_items_alone():
    from sltiktok.enrich import normalize
    original = {"id": "1", "playCount": 5, "text": "x",
                "webVideoUrl": "https://www.tiktok.com/@a/video/1"}
    assert normalize(original) is original


def test_run_info_handles_both_client_shapes():
    """A dict-vs-pydantic mixup crashed a paid run and threw away its data."""
    as_dict = {"id": "abc", "defaultDatasetId": "ds1", "status": "SUCCEEDED"}
    got = run_info(as_dict)
    assert got["run_id"] == "abc" and got["dataset_id"] == "ds1"

    class FakeRun:                       # what apify-client 3.x returns
        id = "xyz"
        default_dataset_id = "ds2"
        status = "SUCCEEDED"
        status_message = "Scraped 1/5 profiles"
        usage_total_usd = 0.06

    got = run_info(FakeRun())
    assert got["run_id"] == "xyz" and got["dataset_id"] == "ds2"
    assert got["status_message"] == "Scraped 1/5 profiles"


def test_keeps_employee_clips():
    for text in [
        "จบกัน ลาก่อยยยยยยย #พนักงานเซเว่น #ออกจากเซเว่น",
        "ทำงานอีก 1 เดือน ก็จะเหลือแค่ความทรงจำสำหรับเราแล้วนะ 7-11 ครอบครัว CP All 8 ปี",
    ]:
        ok, why = relevant({"text": text})
        assert ok and why == "seven", text


def test_keeps_workplace_clips_without_a_seven_mention():
    # Verbatim from the first real Apify run. These creators are already known
    # to be 7-11 staff, so they don't name their employer every time.
    for text in [
        "รอบนี้ เจอหัวหน้าดีแตกแทน #หัวหน้าเฮงซวย #เพื่อนร่วมงาน #แบ่งปันกันเล่า",
        "ลูกค้าโหดมากวันนี้ #กะดึก",
    ]:
        ok, why = relevant({"text": text})
        assert ok and why == "workplace", text


def test_drops_off_topic():
    # Also verbatim: the profile scraper returns the creator's whole feed,
    # and most of it has nothing to do with the job.
    for text in [
        "ฟังคลิปเบาหวาน ความดัน ไขมัน โรคไต ตอนออกกำลังกายซะละ 💪 #ความดัน",
        "อย่าหาวางลัคนาผิดเด้ ราศี20% ลัคนา80เด้อ #ดูดวงลัคนา",
        "โอ้ยยย ทำไงดีเนี่ยย #หาเพลง #เพลง10ปีก่อน",
        "รีวิวลิปใหม่ สวยมาก #makeup",
    ]:
        ok, why = relevant({"text": text})
        assert not ok and why == "off-topic", text


def test_empty_caption_is_kept_for_review():
    # Text-on-image clips carry no caption. Dropping them silently loses
    # exactly the content the user flagged as a concern.
    ok, why = relevant({"text": "", "description": ""})
    assert ok and why == "no-caption"


def test_hashtags_count_as_text():
    # Some clips put everything in the hashtag array and leave text empty.
    v = {"text": "", "hashtags": [{"name": "พนักงานเซเว่น"}, {"name": "ลาออก"}]}
    ok, why = relevant(v)
    assert ok and why == "seven"

    # Plain-string hashtags happen too.
    ok, _ = relevant({"text": "", "hashtags": ["พนักงานเซเว่น"]})
    assert ok


def test_video_url_shapes():
    assert video_url({"webVideoUrl": "https://www.tiktok.com/@a/video/1"}) == "https://www.tiktok.com/@a/video/1"
    # Falls back to rebuilding from author + id.
    built = video_url({"id": "123", "authorMeta": {"name": "bob"}})
    assert built == "https://www.tiktok.com/@bob/video/123"
    assert video_url({}) == ""


def test_author_shapes():
    assert author_of({"authorMeta": {"name": "bob"}}) == "bob"
    assert author_of({"authorName": "sue"}) == "sue"
    assert author_of({}) == ""


def test_coverage_flags_empty_accounts():
    videos = [{"id": "1", "text": "ลาออกเซเว่น", "authorMeta": {"name": "alice"}}]
    kept = videos
    comments = [{"videoWebUrl": "https://www.tiktok.com/@alice/video/1", "text": "สู้ๆ"}]

    rep = coverage_report(["alice", "ghost"], videos, kept, comments)
    assert rep["accounts_empty"] == ["ghost"]
    assert rep["accounts_with_videos"] == 1
    assert rep["comments_total"] == 1
    assert rep["videos_with_zero_comments"] == []


def test_coverage_flags_videos_that_returned_nothing():
    videos = [
        {"id": "1", "text": "ลาออกเซเว่น", "authorMeta": {"name": "alice"}},
        {"id": "2", "text": "กะดึกเซเว่น", "authorMeta": {"name": "alice"}},
    ]
    rep = coverage_report(["alice"], videos, videos, [])
    assert len(rep["videos_with_zero_comments"]) == 2
    assert rep["comments_expected_ceiling"] > 0


def test_seed_list_loads_and_is_clean():
    names = seed_accounts()
    assert len(names) == len(set(names)), "duplicate usernames in seed"
    assert not any(n.startswith("@") for n in names), "@ should be stripped"
    assert len(names) == 100, f"expected 100 seed accounts, got {len(names)}"


# --- ocr.py ---

from sltiktok.ocr import needs_ocr, cover_url


def test_ocr_targets_hashtag_only_captions():
    # These are the real captions that came back uncategorizable in step 2 —
    # all signal is on the frame, not in the text.
    for cap in [
        "#พนักงานเซเว่น",
        "👋🏻งานใหม่ใกล้ฉัน #พนักงานเซเว่น",
        "",
        "#เซเว่น #เด็กเซเว่น #ผู้จัดการ",
    ]:
        assert needs_ocr({"text": cap}), cap


def test_ocr_skips_captions_that_already_say_something():
    for cap in [
        "ทำงานอีก 1 เดือน ก็จะเหลือแค่ความทรงจำสำหรับเราแล้วนะ 7-11 ครอบครัว CP All 8 ปี",
        "ขอบคุณประสบการณ์จากที่นี้ #ลาออก #พนักงาน711",
    ]:
        assert not needs_ocr({"text": cap}), cap


def test_cover_url_shapes():
    assert cover_url({"coverUrl": "http://x/a.jpg"}) == "http://x/a.jpg"
    assert cover_url({"videoMeta": {"coverUrl": "http://x/b.jpg"}}) == "http://x/b.jpg"
    assert cover_url({}) == ""


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nall passed")
