"""Spot-check: model labels next to the human screener's, side by side.

The story asks for 30 clips eyeballed against the CEO's loose bar, not an
accuracy metric. This prints them in a form a person can actually read, plus
the one number worth having: how often the model's intent_level lines up with
the category a human already assigned in the sheet.

    .venv/bin/python spotcheck.py            # 30 clips
    .venv/bin/python spotcheck.py --all
    .venv/bin/python spotcheck.py --disagree # only where the two differ
"""

import argparse
import json
from pathlib import Path

OUT = Path(__file__).parent / "out"

# The sheet's categories and the model's intent_level are different
# vocabularies for the same idea. This is the honest mapping between them;
# everything else counts as a mismatch worth a human look.
HUMAN_TO_INTENT = {
    "ลาออกแล้ว": {"ลาออกแล้ว"},
    "คิดจะลาออก": {"คิดจะลาออก"},
    "บ่นงาน": {"บ่น"},
    "รีวิวชีวิตพนักงาน": {"บ่น", "ไม่เกี่ยว"},   # a review can read either way
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--disagree", action="store_true", help="only mismatches")
    ap.add_argument("-n", type=int, default=30)
    args = ap.parse_args()

    rows = json.loads((OUT / "dashboard.json").read_text(encoding="utf-8"))["clips"]
    rows = [r for r in rows if r["analysis_status"] == "ok"]

    agree = compared = 0
    shown = 0
    for r in rows:
        human = r["human_category"].strip()
        model = r["intent_level"]
        expected = HUMAN_TO_INTENT.get(human)
        match = expected is not None and model in expected
        if expected is not None:
            compared += 1
            agree += match

        if args.disagree and (match or expected is None):
            continue
        if not args.all and shown >= args.n:
            continue
        shown += 1

        flag = "  " if match else ("??" if expected else "--")
        print(f"{flag} @{r['username'][:20]:<20} human={human or '(blank)':<18} model={model}")
        print(f"   themes  {'/'.join(r['themes'])}   conf={r['confidence']}   relevant={r['relevant']}")
        src = r["transcript"] or r["on_screen_text"]
        print(f"   text    {src[:110].replace(chr(10), ' ')}")
        if r["notable_quote"]:
            print(f"   quote   {r['notable_quote'][:110]}")
        print()

    print(f"--- intent vs human category: {agree}/{compared} match "
          f"({agree / compared * 100:.0f}%) over clips with a human label ---")


if __name__ == "__main__":
    main()
