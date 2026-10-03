#!/usr/bin/env python3
"""Measure the `social` source's independence and composition, for
`sources.period_note`.

Spec rule: `documents` is an independence claim. Where a corpus has few authors,
count them and say so -- the cautionary case is LUMRO (175 novels, 111 authors,
638 of 1,425 rare words from one person). A 15-subreddit source needs the same
treatment, because "15 sources" is worthless if it is one community under 15
names, and because 9 of the 15 are city subreddits, which skews toponyms.

Read-only. Safe against a live crawl: it reads the `.zst` files and the
in-progress `.ndjson`, tolerating a torn final line.

    python build/measure_social_independence.py              # full, slow
    python build/measure_social_independence.py --sample 150000

`--sample` takes the newest N comments per subreddit, which is the same recent
window for every one of them and so is the comparable cheap measurement. Run it
without `--sample` before writing `period_note`: the sampled figures are
indicative only, and the marginal-vocabulary curve in particular depends on the
occurrence floor, which is per-source and derived from the whole corpus.
"""

from __future__ import annotations

import argparse
import io
import json
import pathlib
from collections import Counter

import zstandard

from wrodfreq.tokenizer import tokenize

RAW = pathlib.Path(__file__).resolve().parent.parent / "data/raw/social"

# 9 of these 15 are city subreddits; that is the composition fact period_note
# has to carry, not just the author counts.
REGIONAL = {"Bucuresti", "cluj", "Iasi", "Sibiu", "Timisoara", "Oradea",
            "Craiova", "Constanta", "Brasov"}
MIN_OCC = 5     # the abstention floor, per spec


def lines(sub: str, kind: str = "comments"):
    z, n = RAW / f"{sub}_{kind}.zst", RAW / f"{sub}_{kind}.ndjson"
    if z.exists():
        with z.open("rb") as f:
            yield from io.TextIOWrapper(
                zstandard.ZstdDecompressor().stream_reader(f), encoding="utf-8")
    elif n.exists():
        with n.open("r", encoding="utf-8", errors="replace") as f:
            yield from f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, help="newest N comments per subreddit")
    args = ap.parse_args()

    subs = sorted({p.name.split("_")[0] for p in RAW.glob("*_comments.*")},
                  key=lambda s: -(RAW / f"{s}_comments.zst").stat().st_size
                  if (RAW / f"{s}_comments.zst").exists() else 0)
    if not subs:
        print(f"no comment files in {RAW}")
        return 1

    authors: dict[str, set[str]] = {}
    types: dict[str, Counter] = {}
    docs: dict[str, int] = {}

    for sub in subs:
        A: set[str] = set()
        T: Counter = Counter()
        i = -1
        for i, line in enumerate(lines(sub)):
            if args.sample and i >= args.sample:
                break
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue            # torn final line of a live append-log
            a = rec.get("author")
            if a and a not in ("[deleted]", "AutoModerator"):
                A.add(a)
            T.update(tokenize(rec.get("body") or ""))
        authors[sub], types[sub], docs[sub] = A, T, i + 1
        tag = "regional" if sub in REGIONAL else ""
        print(f"{sub:<14} {docs[sub]:>10,} docs {len(A):>8,} authors "
              f"{len(T):>8,} types  {tag}")

    all_authors = set().union(*authors.values())
    print(f"\n{sum(docs.values()):,} documents, {len(all_authors):,} distinct authors")

    # Concentration: the LUMRO failure mode is a few authors carrying the corpus.
    per_author: Counter = Counter()
    for A in authors.values():
        per_author.update(A)
    top100 = sum(c for _, c in per_author.most_common(100))
    print(f"top 100 authors appear in {top100/sum(per_author.values()):.1%} "
          f"of (subreddit, author) pairs")

    biggest = subs[0]
    print(f"\n--- author overlap with r/{biggest} ---")
    B = authors[biggest]
    for sub in subs[1:]:
        S = authors[sub]
        shared = len(B & S)
        print(f"  {sub:<14} shared {shared:>7,}  Jaccard {shared/len(B | S):.3f}  "
              f"{shared/len(S):6.1%} of r/{sub} authors also post in r/{biggest}")

    print(f"\n--- marginal new vocabulary (types at >= {MIN_OCC} occurrences) ---")
    seen: Counter = Counter()
    for sub in subs:
        before = sum(1 for c in seen.values() if c >= MIN_OCC)
        seen.update(types[sub])
        after = sum(1 for c in seen.values() if c >= MIN_OCC)
        print(f"  +{sub:<14} {before:>8,} -> {after:>8,}  new {after-before:>7,}"
              f"  (+{(after-before)/max(before,1)*100:5.1f}%)")

    reg = [s for s in subs if s in REGIONAL]
    print(f"\nregional subreddits present: {len(reg)}/{len(subs)} "
          f"({', '.join(reg) or 'none'})")
    for sub in reg:
        other: Counter = Counter()
        for s in subs:
            if s != sub:
                other.update(types[s])
        uniq = [w for w, c in types[sub].items() if c >= MIN_OCC and other[w] == 0]
        print(f"  r/{sub:<12} {len(uniq):>6,} reliable types appear in no other subreddit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
