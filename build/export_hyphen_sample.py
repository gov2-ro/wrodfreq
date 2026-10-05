#!/usr/bin/env python3
"""Export a stratified, shuffled sample of one-hyphen `merged` rows for a human to label.

Context: docs/BACKLOG.md, "URL slugs" — 819,273 one-hyphen rows (13.5% of the table), only
6,130 in DEX. Whether a rule can drop the junk without dropping real compounds
(`cluj-napoca`, `e-mail`) is a judgement a person has to make on examples first.

Read-only on the database. Writes data/review/hyphen_sample.csv (gitignored: /data/).
Rows are shuffled so the order carries no information; `stratum` is the last column and
can be ignored while labelling.

Label column, one of:   K = keep (a real Romanian word or compound, or a real name)
                        J = junk (slug, page title, chunk of a list, boilerplate)
                        ? = cannot tell
Then run build/summarize_hyphen_labels.py to see which strata are safe to filter.

Usage:
    python build/export_hyphen_sample.py [--db PATH] [--per-stratum N] [--seed S]
"""

from __future__ import annotations

import argparse
import csv
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wrodfreq.db import DEFAULT_DB_PATH

OUT = Path("data/review/hyphen_sample.csv")

# The one huge stratum (700k rows) gets a larger sample than the rest.
WEIGHT = {"one_src_rare": 2}

# name -> WHERE clause over merged (alias m). Each is a population we might filter as a unit.
STRATA = {
    "dex":            "m.is_dex = 1",
    "corroborated":   "m.is_dex = 0 AND m.n_reliable >= 3",
    "two_sources":    "m.is_dex = 0 AND m.n_reliable = 2",
    "one_src_common": "m.is_dex = 0 AND m.n_reliable = 1 AND m.zipf >= 1.0",
    "one_src_rare":   "m.is_dex = 0 AND m.n_reliable = 1 AND m.zipf < 1.0",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    ap.add_argument("--per-stratum", type=int, default=60)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rng = random.Random(args.seed)
    rows = []
    for name, where in STRATA.items():
        pool = conn.execute(
            f"""SELECT m.word, m.zipf, m.n_reliable, m.is_dex,
                       COALESCE((SELECT documents FROM source_counts c
                                 WHERE c.word = m.word AND c.source_id = 'web'), 0)
                FROM merged m
                WHERE m.word LIKE '%-%'
                  AND length(m.word) - length(replace(m.word, '-', '')) = 1
                  AND {where}
                ORDER BY m.word"""      # ordered, so the seeded sample is reproducible
        ).fetchall()
        picked = rng.sample(pool, min(args.per_stratum * WEIGHT.get(name, 1), len(pool)))
        print(f"  {name:<15} {len(pool):>8,} rows in the stratum, {len(picked)} sampled")
        rows += [(w, round(z, 2), nr, dex, docs, "", "", name) for w, z, nr, dex, docs in picked]
    conn.close()

    rng.shuffle(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:   # BOM: Excel reads ă/ș right
        w = csv.writer(f)
        w.writerow(["word", "zipf", "n_reliable", "is_dex", "web_docs", "label", "note", "stratum"])
        w.writerows(rows)
    print(f"Wrote {OUT} — {len(rows)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
