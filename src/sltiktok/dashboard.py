"""Turn step 4's export into everything the dashboard page needs.

Every aggregate is computed here and nowhere else. The browser gets numbers
and strings only - in particular Thai word segmentation runs in this script,
because doing it client-side would mean shipping a segmenter and 5,181
comment bodies to render one word cloud.

    .venv/bin/python build_dashboard_data.py

Reads out/dashboard.json, writes web/data.js as `window.DATA = {...}`.
A separate script file, not inlined HTML: <script src> is exempt from the
CORS rule that blocks fetch() on file://, so the page still opens directly.
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from sltiktok import paths

HERE = paths.ROOT
SRC = paths.DASHBOARD
DEST = paths.WEB_DATA

# The story asks for three intent levels; the pipeline emits four labels.
#
# ไม่เกี่ยว is a label about resignation intent, not about topic: it means the
# clip never says quit / thinking of quitting. Reading all 8 of them, every one
# is still 7-Eleven workplace content - a day in the shift, a promotion, the
# health risks of nights - and several carry workload and ตารางกะ themes. The
# model had nowhere to put "talks about the job without wanting to leave", so
# they fall in with the ones voicing the job's strain rather than being dropped.
INTENT_BUCKETS = {
    "บ่น": "low",
    "ไม่เกี่ยว": "low",
    "คิดจะลาออก": "medium",
    "ลาออกแล้ว": "high",
}
# Nothing is excluded on topic now; a clip the model could not read at all is
# already kept out by analyzed(), which needs an intent_level to be present.

# One name per bucket, since a bucket can hold more than one model label. The
# low bucket now covers both venting and simply describing the job, so it is
# named for the whole group rather than for บ่น alone.
BUCKET_LABEL = {
    "low": "เล่าเรื่องงาน",
    "medium": "คิดจะลาออก",
    "high": "ลาออกแล้ว",
}

HIGHLIGHT_COUNT = 12
PHRASE_COUNT = 40

# Below this, a quote has usually been clipped mid-sentence and reads as a
# fragment on a card: "ผลัดเช้าเข้างานกี่โมงคะ" without the "7 โมงปลายแล้ว"
# that makes it a complaint. 20 of the 88 quotes land here.
QUOTE_MIN = 30
QUOTE_MAX = 140

# Words that survive stopword filtering but say nothing about work: platform
# chatter, interjections, and the brand name itself (present in nearly every
# comment, so it would dominate the cloud while carrying no information).
# Rejects the artefacts of drawn-out typing ("มากกกกก") and of the segmenter
# mis-splitting them, which produce high-frequency phrases that mean nothing.
PHRASE_OK = re.compile(r"^(?!.*(.)\1\1)[฀-๿]{5,}$")

# Words that cannot open or close a phrase without leaving it hanging.
# Kept separate from NOISE: these are fine mid-phrase ("เหนื่อยกับคน" needs
# กับ) and only signal a fragment at an edge.
DANGLING = {
    "ที่", "ซึ่ง", "และ", "แต่", "กับ", "ของ", "ให้", "ได้", "ไม่", "จะ",
    "ก็", "เป็น", "มี", "ทำ", "เคย", "ว่า", "คน", "เรา", "เขา", "มัน",
    "อยู่", "มา", "ไป", "ยัง", "ต้อง", "แล้ว", "นะ", "คะ", "ค่ะ", "ครับ",
    "เลย", "อีก", "ด้วย", "เพราะ", "ถ้า", "ตอน", "เมื่อ", "จาก", "ใน",
}

NOISE = {
    "เซเว่น", "เซ", "เว่น", "เซ่น", "เว็น", "เเ", "อ่ะ", "ค่ะ", "ครับ", "คับ",
    "จ้า", "จ้ะ", "นะ", "น่ะ", "ฮ่า", "555", "5555", "55555", "อ่า", "เออ",
    "อะ", "ๆ", "เนอะ", "งับ", "ค่า", "คะ", "ฮะ", "นะคะ", "ครับผม",
}

# Phrases that survive every structural filter but still say nothing - they
# are speech scaffolding, a bare restatement of the subject, or half a
# sentence the segmenter could not finish.
PHRASE_NOISE = {
    "บอกว่า", "งานนี้", "ร้านผม", "เหมือนกัน", "แบบนี้", "อย่างนี้",
    "เพลงนี้", "ขนาดนี้", "วงการนี้", "เซเว่นเหมือนกัน", "งานอื่น",
    "พนักงานเซเว่น", "ทำงานเซเว่น", "เข้าทุ่มเลิก", "เริ่มงาน", "กี่โมง",
    "แล้วจะ", "เข้าเลิก", "ทุ่มเลิกโมง", "พอทนเหนื่อย", "งานเซเว่น",
    "หน้าร้าน", "เข้าทุ่ม",
}

# Encouragement is real - 168 comments of it - but it is the audience being
# kind, not a problem to fix. Kept and counted separately so it cannot crowd
# out the topics an executive can act on.
SUPPORT_PHRASES = {"สู้ๆนะคะ", "สู้ๆๆ", "เก่งมาก", "เก่งที่สุด", "สู้ๆครับ",
                   "สู้ๆนะ", "เป็นกำลังใจ", "กำลังใจให้"}


def load():
    """Analysed clips, plus the surveyed accounts that never produced one.

    The survey covered 100 accounts; 99 returned a clip. @ai_pon08's metadata
    never came back, so it is absent from the analysed file entirely. It is
    still one of the hundred, and the screener filed it as บ่นงาน, so the seed
    is read here to put it back - carrying its human category and nothing else.
    A row with no views cannot inflate any engagement total.
    """
    data = json.loads(SRC.read_text(encoding="utf-8"))

    seen = {c.get("username") for c in data["clips"]}
    seed = json.loads(paths.SEED.read_text(encoding="utf-8"))
    for i, row in enumerate(seed, start=1):
        if row.get("username") in seen:
            continue
        data["clips"].append({
            "video_id": f"seed-{i}",
            "sheet_row": i,
            "username": row.get("username"),
            "caption": row.get("caption", ""),
            "video_url": row.get("video_url", ""),
            "human_category": row.get("category", ""),
            # No transcript, so no model intent, no themes and no quote: this
            # row counts in the survey and shows up nowhere that needs one.
            "intent_level": "",
            "themes": [],
            "notable_quote": "",
            "analysis_status": "no_metadata",
        })
    return data


def pseudonym(sheet_row):
    """ผู้ใช้ #07 - stable for a given row, so it survives a rebuild.

    Rows are 1-based and unique per clip (one clip per account in this
    dataset), so the number doubles as an account identifier without ever
    exposing the handle.
    """
    return f"ผู้ใช้ #{sheet_row:02d}" if sheet_row else "ผู้ใช้ (ไม่ทราบลำดับ)"


def quarter(iso):
    """2025-03-23T... -> (2025, 1). Posts are the only thing dated here."""
    y, m = int(iso[:4]), int(iso[5:7])
    return y, (m - 1) // 3 + 1


# The sheet's own vocabulary, for the clips the model never got to read. A
# human screener watched these and wrote a category down; that is weaker
# evidence than a transcript but it is not a guess.
HUMAN_TO_INTENT = {
    "บ่นงาน": "บ่น",
    "รีวิวชีวิตพนักงาน": "บ่น",
    "คิดจะลาออก": "คิดจะลาออก",
    "ลาออกแล้ว": "ลาออกแล้ว",
}


def intent_of(clip):
    """The clip's intent, from the model if it has one, else the screener's.

    Two of the 100 accounts never reached the model: @ai_pon08's metadata
    never came back, and @ratti_21's mp4 was unreachable in step 4. Both were
    watched and filed as บ่นงาน by hand, so dropping them understated the
    survey by two rather than leaving it undecided. Falling back to the human
    label counts them without inventing anything the sheet does not say.
    """
    return clip.get("intent_level") or HUMAN_TO_INTENT.get(
        (clip.get("human_category") or "").strip())


def analyzed(clips):
    """Clips carrying an intent - the model's, or the screener's as fallback."""
    return [c for c in clips if intent_of(c)]


def overview(clips, comments):
    real = analyzed(clips)
    # Every analysed clip is about the job, so every one of them counts.
    counted = real

    # อื่นๆ is the model's catch-all, not a problem anyone can act on. It
    # would sit third in the chart and tell an executive nothing.
    themes = Counter()
    for c in counted:
        for t in c.get("themes") or []:
            if t != "อื่นๆ":
                themes[t] += 1

    intents = Counter(intent_of(c) for c in real)

    # The window the sample actually covers. Stated on the page so a reader
    # is never left to assume the data runs up to the day they open it.
    dates = sorted(c["uploaded_at"][:10] for c in clips if c.get("uploaded_at"))

    return {
        "collected_from": dates[0] if dates else "",
        "collected_to": dates[-1] if dates else "",
        "clips_collected": len(clips),
        # What the model actually read. The 87% spot-check is measured against
        # this, not against the survey total, so the two must stay separate.
        "clips_analyzed": sum(1 for c in clips if c.get("intent_level")),
        # Everything with an intent, the model's or the screener's.
        "clips_with_intent": len(counted),
        "clips_from_human_label": sum(
            1 for c in real if not c.get("intent_level")),
        # Kept at zero rather than dropped: the page still reads the field, and
        # the honest answer now is that nothing was set aside as off-topic.
        "clips_excluded": 0,
        "comments": len(comments),
        # One clip per account in this dataset, but count distinctly anyway -
        # a future run with two clips from one person must not inflate this.
        "accounts": len({c["username"] for c in clips if c.get("username")}),
        "views": sum(c.get("views") or 0 for c in clips),
        "likes": sum(c.get("likes") or 0 for c in clips),
        "shares": sum(c.get("shares") or 0 for c in clips),
        "themes": [{"name": n, "value": v} for n, v in themes.most_common()],
        # Several labels can share a bucket, so sum into the bucket rather than
        # emitting a row per label - two labels map to low and the funnel must
        # show one bar, not two.
        "intents": [{"bucket": bucket, "label": BUCKET_LABEL[bucket],
                     "value": sum(intents.get(lbl, 0)
                                  for lbl, b in INTENT_BUCKETS.items()
                                  if b == bucket)}
                    for bucket in ("low", "medium", "high")],
    }


def trend(clips):
    """Clips per quarter, with the engagement they drew.

    Empty quarters are emitted with zeros rather than skipped, so the axis is
    evenly spaced and a gap reads as a gap instead of as compressed time.
    """
    dated = [c for c in clips if c.get("uploaded_at")]
    if not dated:
        return []

    by_q = defaultdict(lambda: {"clips": 0, "views": 0, "likes": 0})
    for c in dated:
        y, q = quarter(c["uploaded_at"])
        row = by_q[(y, q)]
        row["clips"] += 1
        row["views"] += c.get("views") or 0
        row["likes"] += c.get("likes") or 0

    # The newest clip is where collection stopped, not where posting stopped.
    # If that lands mid-quarter the final bar covers a shorter window than the
    # ones beside it, and a reader compares them as equals - a drop that only
    # means "we stopped looking" reads as "the problem went away". Flag it so
    # the page can draw it as incomplete.
    newest = max(c["uploaded_at"] for c in dated)[:10]
    cut_y, cut_q = quarter(newest)

    first, last = min(by_q), max(by_q)
    out = []
    y, q = first
    while (y, q) <= last:
        row = by_q.get((y, q), {"clips": 0, "views": 0, "likes": 0})
        # 28 quarters of "2025Q1" overlap on any projector, so the axis shows
        # the year once at Q1 and blanks the rest.
        out.append({"period": f"{y}Q{q}", "axis": str(y) if q == 1 else "",
                    "year": y, "quarter": q,
                    "partial": (y, q) == (cut_y, cut_q), **row})
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def widen_quote(quote, clip):
    """Give a clipped quote back the words around it.

    The model sometimes returns a fragment - a question without its answer,
    a label without its list - which reads as noise on a card. Where the
    fragment can be found in the source text, extend forward from it up to
    the next sentence-ish break. Nothing is invented: if the quote is not
    in the source, or the source has nothing more to give, it stands as is.
    """
    quote = (quote or "").strip()
    if len(quote) >= QUOTE_MIN:
        return quote

    for source in (clip.get("transcript"), clip.get("on_screen_text")):
        source = (source or "").strip()
        i = source.find(quote)
        if not quote or i < 0:
            continue

        tail = source[i:i + QUOTE_MAX]
        # A newline in on-screen text is a cut to a different shot, so stop
        # there rather than stitching two unrelated captions together.
        nl = tail.find("\n", len(quote))
        if nl > 0:
            tail = tail[:nl]
        # Handles are tagging, not speech.
        tail = re.sub(r"\s*@[\w.]+\s*$", "", tail).strip()

        # Widening only helps if it added words. Picking up a trailing emoji
        # or a stray quote mark leaves the same fragment, slightly longer.
        if len(tail) >= QUOTE_MIN and len(re.sub(r"\W", "", tail)) > \
                len(re.sub(r"\W", "", quote)):
            return tail
    return quote


def highlights(clips, limit=HIGHLIGHT_COUNT):
    """Loudest clips that actually say something, by views.

    A clip without a quote has nothing to show on a card, so it is skipped
    even if it out-performed the ones that made the list.
    """
    pool = [c for c in analyzed(clips) if c.get("notable_quote")]
    pool.sort(key=lambda c: c.get("views") or 0, reverse=True)
    return [{
        "id": c["video_id"],
        "person": pseudonym(c.get("sheet_row")),
        "quote": widen_quote(c["notable_quote"], c),
        "themes": c.get("themes") or [],
        "intent": intent_of(c),
        "bucket": INTENT_BUCKETS[intent_of(c)],
        "views": c.get("views") or 0,
        "likes": c.get("likes") or 0,
        "comments": c.get("comments") or 0,
        "uploaded_at": (c.get("uploaded_at") or "")[:10],
        # The handle is unavoidable inside a TikTok URL. It appears only as a
        # link target, never as text on the page.
        "url": c.get("video_url", ""),
    } for c in pool[:limit]]


def phrases(comments, size=PHRASE_COUNT):
    """Two- and three-word phrases people actually repeat.

    Single words were the first attempt and they said nothing: "ทำ", "คน",
    "ดี" top the list and leave the reader guessing. Segmentation also
    splits "กะดึก" into "กะ" + "ดึก", scattering the single loudest
    complaint in the corpus across two meaningless tokens. A phrase carries
    its own meaning: "เหนื่อยกับคน" is a finding, "คน" is not.
    """
    from pythainlp.corpus import thai_stopwords
    from pythainlp.tokenize import word_tokenize

    stop = set(thai_stopwords()) | NOISE
    thai = re.compile(r"^[฀-๿]+$")

    total = Counter()
    by_sentiment = defaultdict(Counter)
    for c in comments:
        text = (c.get("text") or "").strip()
        if not text:
            continue
        sentiment = c.get("sentiment") or "อื่นๆ"
        toks = [t.strip() for t in word_tokenize(text, engine="newmm")]
        toks = [t for t in toks if t and thai.match(t)]

        for n in (2, 3):
            for i in range(len(toks) - n + 1):
                gram = toks[i:i + n]
                # Filler-only n-grams ("ที่ จะ") and grams too short to read
                # are noise; a gram needs at least one word of substance.
                if all(w in stop for w in gram) or len("".join(gram)) < 5:
                    continue
                # A phrase that starts or ends on a connective is a fragment
                # of a sentence, not a thought: "งานไม่", "เคยทำ", "กับคน".
                if gram[0] in DANGLING or gram[-1] in DANGLING:
                    continue
                key = "".join(gram)
                if key in PHRASE_NOISE or not PHRASE_OK.match(key):
                    continue
                total[key] += 1
                by_sentiment[key][sentiment] += 1

    # A phrase that only ever appears inside a longer one is a fragment of
    # it: "เหนื่อยกับ" (50) is always the front of "เหนื่อยกับคน" (31) or
    # similar, and the longer form is the one that means something. Prefer
    # the longest phrase whose count is close to the fragment's.
    for phrase, n in list(total.items()):
        longer = [(o, m) for o, m in total.items()
                  if o != phrase and phrase in o and m >= n * 0.55]
        if longer:
            del total[phrase]

    kept = []
    for phrase, n in total.most_common():
        # Keep the ordering stable but never show two phrases where one
        # contains the other.
        if any(phrase in big or big in phrase for big, _ in kept):
            continue
        kept.append((phrase, n))
        if len(kept) == size:
            break

    return [{
        "phrase": p,
        "count": n,
        "agree": by_sentiment[p].get("เห็นด้วย", 0),
        "disagree": by_sentiment[p].get("ไม่เห็นด้วย", 0),
        "kind": "support" if p in SUPPORT_PHRASES else "topic",
    } for p, n in kept]


def comment_sentiment(comments):
    counts = Counter(c.get("sentiment") or "อื่นๆ" for c in comments)
    return [{"name": n, "value": v} for n, v in counts.most_common()]


def sentiment_by_theme(comments):
    """Which topics people push back on, and which nobody argues with.

    The headline "69% agree" says nothing about what the agreement is about.
    Split per theme and the picture sharpens: ค่าแรง and workload draw the
    most pushback, หัวหน้างาน and เพื่อนร่วมงาน draw almost none - nobody
    defends a bad manager, but plenty of people will argue the job is not
    that hard.
    """
    skip = {"ระบุไม่ได้", "อื่นๆ"}
    by_theme = defaultdict(Counter)
    for c in comments:
        theme = c.get("theme")
        if not theme or theme in skip:
            continue
        by_theme[theme][c.get("sentiment") or "อื่นๆ"] += 1

    rows = []
    for theme, counts in by_theme.items():
        agree = counts.get("เห็นด้วย", 0)
        disagree = counts.get("ไม่เห็นด้วย", 0)
        total = sum(counts.values())
        rows.append({
            "name": theme,
            "agree": agree,
            "disagree": disagree,
            "other": counts.get("อื่นๆ", 0),
            "total": total,
            # Of the people who took a side, how many pushed back.
            "disagree_pct": round(disagree / max(agree + disagree, 1) * 100, 1),
        })
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows


def top_comments(comments, per_sentiment=6):
    """The most-liked thing said on each side, so the split has a voice.

    A percentage does not tell an executive what the disagreement is; these
    do. Likes are the only ranking available and they are the audience's own.
    """
    out = {}
    for sentiment in ("เห็นด้วย", "ไม่เห็นด้วย"):
        pool = [c for c in comments
                if c.get("sentiment") == sentiment and (c.get("text") or "").strip()]
        pool.sort(key=lambda c: c.get("likes") or 0, reverse=True)
        out[sentiment] = [{
            "text": c["text"].strip(),
            "likes": c.get("likes") or 0,
            "theme": c.get("theme") or "",
        } for c in pool[:per_sentiment]]
    return out


def comment_themes(comments):
    """Themes people raise in comments, excluding the two non-answers.

    ระบุไม่ได้ is a third of all comments ("จริง", "555") and อื่นๆ is the
    model's catch-all. Neither names a problem anyone can act on, and both
    would outrank real themes in the chart while meaning nothing.
    """
    skip = {"ระบุไม่ได้", "อื่นๆ"}
    counts = Counter(c.get("theme") for c in comments
                     if c.get("theme") and c["theme"] not in skip)
    return [{"name": n, "value": v} for n, v in counts.most_common()]


def build():
    raw = load()
    clips, comments = raw["clips"], raw["comments"]
    return {
        "overview": overview(clips, comments),
        "trend": trend(clips),
        "highlights": highlights(clips),
        "phrases": phrases(comments),
        "comment_sentiment": comment_sentiment(comments),
        "comment_themes": comment_themes(comments),
        "sentiment_by_theme": sentiment_by_theme(comments),
        "top_comments": top_comments(comments),
    }


def main():
    data = build()
    DEST.parent.mkdir(exist_ok=True)
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    DEST.write_text(f"window.DATA = {body};\n", encoding="utf-8")

    o = data["overview"]
    print(f"wrote {DEST}  {DEST.stat().st_size / 1024:.0f}KB")
    print(f"  clips {o['clips_collected']} / analyzed {o['clips_analyzed']} "
          f"/ with intent {o['clips_with_intent']}")
    print(f"  comments {o['comments']}  accounts {o['accounts']}  "
          f"views {o['views']:,}")
    print(f"  trend {len(data['trend'])} quarters  "
          f"highlights {len(data['highlights'])}  "
          f"phrases {len(data['phrases'])}")


if __name__ == "__main__":
    main()
