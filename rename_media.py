"""Rename downloaded clips from bare video ids to sheet-order names.

    out/media/7485005226362080520.mp4  ->  002_chalita_mungkhot.mp4

One-off: files downloaded before analyze.py started naming them this way.
Prints the plan and does nothing unless --apply is given.
"""

import argparse
import sys
from pathlib import Path

from analyze import MEDIA, media_name, seed_order


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually rename")
    args = ap.parse_args()

    order = seed_order()
    if not order:
        sys.exit("filtered_100.json not found — cannot determine sheet order")

    moves, skipped, clashes = [], [], []
    for f in sorted(MEDIA.glob("*.mp4")):
        want = media_name(f.stem, order)
        if f.name == want:
            skipped.append(f.name)
            continue
        dest = MEDIA / want
        if dest.exists():
            clashes.append((f.name, want))
            continue
        moves.append((f, dest))

    for f, dest in moves:
        print(f"  {f.name:<26} -> {dest.name}")
    for old, want in clashes:
        print(f"  CLASH {old} -> {want} (target exists, left alone)")

    print(f"\n{len(moves)} to rename, {len(skipped)} already named, "
          f"{len(clashes)} clashes")
    if not args.apply:
        return print("dry run — pass --apply to rename")

    for f, dest in moves:
        f.rename(dest)
    print(f"renamed {len(moves)}")


if __name__ == "__main__":
    main()
