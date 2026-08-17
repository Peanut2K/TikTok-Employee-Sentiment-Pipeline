"""Checks the caption-sorting logic. Run: .venv/bin/python test_scrape.py"""

from scrape import SEVEN, COMPETITORS, EMPLOYEE, CUSTOMER, categorize


def keep(caption):
    """Mirror of the harvest filter, so the rules can be checked without a browser."""
    if not SEVEN.search(caption) or not EMPLOYEE.search(caption):
        return False
    return not CUSTOMER.search(caption)


def test_keeps_real_employee_captions():
    # Captions taken verbatim from a live search run.
    for s in [
        "จบกัน ลาก่อยยยยยยย #พนักงานเซเว่น #ออกจากเซเว่น",
        "#พนักงานเซเว่น #ลาออกแล้ว #แชร์ประสบการณ์",
        "ทำงานอีก 1 เดือน ก็จะเหลือแค่ความทรงจำสำหรับเราแล้วนะ 7-11 ครอบครัว CP All 8 ปี",
        "ทำงานเซเว่น=ทำได้ทุกงาน 💪 #เซเว่น #ประสบการณ์ชีวิต",
        "ชายด่าพนักงาน7 11 คำชี้แจง",
    ]:
        assert keep(s), s


def test_drops_shoppers_and_ads():
    # Same run: mentions 7-11, but not from someone who works there.
    for s in [
        "อัปเดต 11 เมนูใหม่เซเว่นเดือนสิงหาคม Ep.105",
        "เซเว่นมีชาบู หมูกระทะแล้วทุกคน🍲🥓",
        "สกุชชี่มาลง เอ เซเว่น แล้วน้าา✨ #aseven",
        "รีวิวบุกในเซเว่นนน 🐙🤤 #รีวิวเซเว่น",
        "หอมทะลุถุง เลย์รสใหม่ ผักชีทรงเครื่อง",
    ]:
        assert not keep(s), s


def test_captions_that_used_to_fall_through():
    # Real captions from a run that came back uncategorized.
    for caption in [
        "จบกัน ลาก่อยยยยยยย #พนักงานเซเว่น #ออกจากเซเว่น",
        "ทำงานอีก 1 เดือน ก็จะเหลือแค่ความทรงจำสำหรับเราแล้วนะ 7-11 ครอบครัว CP All 8 ปี",
        "ขอบาย>< #ลาออก #อดีตพนักงานเซเว่น",
        "ประสบการณ์เกือบ 10 ปีที่ถึงจุดอิ่มตัว #ลาก่อนเซเว่น",
    ]:
        assert categorize(caption)[0] == "ลาออกแล้ว", caption

    for caption in [
        "31🗓️👋🏻#สาวอวบอ้วน #สาวเซเว่น #เซเว่น #เด็กเซเว่น #ผู้จัดการ",
        "#ประสบการณ์ชีวิต #ประสบการณ์ทํางานเซเว่น 2เดือนออก",
        "#พนักงานเซเว่นเท่านั้นที่จะเข้าใจ กล้าเดินออกมาชีวิตดีขึ้น",
    ]:
        assert categorize(caption)[0] == "รีวิวชีวิตพนักงาน", caption


def test_review_of_the_job_still_counts():
    # "รีวิว" alone means a product review, but reviewing the job is on-topic.
    assert keep("รีวิวชีวิตพนักงานเซเว่น กะดึก")


def test_seven_spellings():
    for s in ["ลาออกเซเว่นแล้ว", "ทำงานเซเวน", "พนักงาน 7-11", "กะดึก 7/11", "seven eleven ชีวิตดี", "7 - 11 กะดึก"]:
        assert SEVEN.search(s), s
    for s in ["ลาออกจากโลตัส", "ชีวิตพนักงานร้านกาแฟ", ""]:
        assert not SEVEN.search(s), s


def test_competitors():
    for s in ["พนักงานโลตัส", "ทำงาน Big C", "แฟมิลี่มาร์ท", "CJ More", "ที่ makro"]:
        assert COMPETITORS.search(s), s
    assert not COMPETITORS.search("ลาออกเซเว่นแล้ว")


def test_categorize():
    assert categorize("ลาออกเซเว่นแล้วจ้า")[0] == "ลาออกแล้ว"
    assert categorize("บ่นหน่อย ลูกค้าโหดมาก")[0] == "บ่นงาน"
    assert categorize("รีวิวเงินเดือนพนักงาน")[0] == "รีวิวชีวิตพนักงาน"

    # "ลาออก" outranks "บ่น" when a caption carries both.
    assert categorize("บ่นๆ วันนี้ลาออกแล้ว")[0] == "ลาออกแล้ว"

    # No match and competitor mentions both go to a human, with a reason.
    unknown, note = categorize("วันนี้อากาศดี")
    assert unknown == "" and "ตรวจสอบ" in note

    other, note = categorize("ลาออกจากโลตัสแล้ว")
    assert other == "" and "เจ้าอื่น" in note


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nall passed")
