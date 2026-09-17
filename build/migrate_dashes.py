#!/usr/bin/env python3
"""One-time migration: repair the malformed tokens the pre-2026-09-18
tokenizer wrote into `source_counts`, without re-ingesting any corpus.

Companion to `migrate_elisions.py`, and mostly cleaning up after it. That
script split stored compounds with `_split_elisions` under a rule that used a
naive `word.split("-")`, so any word containing a *doubled* hyphen produced
empty parts, and those became rows: an empty-string word from `într--o`, a
leading-hyphen `-adevăr` from `într--adevăr`, a trailing-hyphen `spune-` from
`spune--mi`. Words like `eu--eu` it left alone entirely, since the old rule
folded them back into a single part.

The empty-string row reached the shipped data file, where `zipf_frequency('')`
answered 2.34 instead of wordfreq's 0.0 — a live break of the drop-in API
contract (spec §10.1), which is what makes this worth a migration at all. The
*numbers* barely move: 43,137 rows carrying 75,442 occurrences out of
28,217,964,718 is 0.00027% of the panel, and every affected entry sits below
Zipf 2.1 except the empty string itself.

Why in-place rather than a re-ingest: exactly `migrate_elisions.py`'s
reasoning — a token's occurrence count is exact regardless of when it is
computed, so re-splitting these stored words under the corrected rule gives
precisely what a fixed tokenizer would have counted from scratch, without
re-reading 27.8B tokens. It inherits that script's one approximation too
(`documents` becomes a slight upper bound for split words).

**Only malformed words are scanned, and that is not a shortcut.** The new
`_split_elisions` differs from the old one only where `word.split("-")` yields
an empty part — i.e. a leading, trailing or doubled hyphen. For every other
word the two produce byte-identical output (the loop's `prev` is the same
value and the branches are unchanged), so `mass-media` and `într-o` are
provably untouched and do not need rescanning.

Token accounting, which is the part worth checking:

    ''            -> []                 delta -occ   (a token that never existed)
    '-adevăr'     -> ['adevăr']         delta  0     (renamed, not recounted)
    'spune-'      -> ['spune']          delta  0
    'eu--eu'      -> ['eu', 'eu']       delta +occ   (one token becomes two)

Note `_split_elisions('')` returns `['']` via its no-hyphen early return, so
empty parts are filtered explicitly here rather than relying on it.

Usage:
    python build/migrate_dashes.py --dry-run   # report only, no writes
    python build/migrate_dashes.py             # apply

Then, to bring every downstream table back in sync:
    python build/compute_zipf.py
    python build/merge.py
    python build/build_lemma_layer.py
    python build/build_package.py
    python build/validate.py

Back up data/wrodfreq.db before running for real — like migrate_elisions.py
this mutates source_counts, source_zipf and sources.total_tokens directly
rather than via an atomic table swap, because it is a one-time transform and
not a script meant to be re-run.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrodfreq.db import DEFAULT_DB_PATH, connect
from wrodfreq.tokenizer import _split_elisions

DELETE_BATCH = 20_000

# The three shapes the old rule could produce that the regex never can, plus
# the empty string it also produced. A word matching none of these is
# provably unchanged by the fix — see the module docstring.
MALFORMED_PREDICATE = (
    "(word = '' OR word LIKE '-%' OR word LIKE '%-' OR word LIKE '%--%')"
)


def repair(word: str) -> list[str]:
    """The corrected token(s) for a stored word, empties dropped."""
    return [p for p in _split_elisions(word) if p]


def migrate_source(conn: sqlite3.Connection, source_id: str, dry_run: bool) -> tuple[int, int, int]:
    """Returns (rows_repaired, rows_dropped_entirely, token_count_delta)."""
    rows = conn.execute(
        f"SELECT word, occurrences, documents FROM source_counts "
        f"WHERE source_id = ? AND {MALFORMED_PREDICATE}",
        (source_id,),
    ).fetchall()

    to_delete: list[str] = []
    deltas: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    token_delta = 0
    dropped = 0

    for word, occ, docs in rows:
        parts = repair(word)
        if parts == [word]:
            continue                      # already well-formed, nothing to do
        to_delete.append(word)
        token_delta += (len(parts) - 1) * occ
        if not parts:
            dropped += 1
        for part in parts:
            entry = deltas[part]
            entry[0] += occ
            entry[1] += docs

    print(f"  [{source_id}] {len(rows):,} malformed words scanned, "
          f"{len(to_delete):,} repaired ({dropped:,} dropped outright), "
          f"{token_delta:+,} tokens", flush=True)

    if dry_run or not to_delete:
        return len(to_delete), dropped, token_delta

    if deltas:
        conn.executemany(
            """
            INSERT INTO source_counts (word, source_id, occurrences, documents)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(word, source_id) DO UPDATE SET
                occurrences = occurrences + excluded.occurrences,
                documents   = documents   + excluded.documents
            """,
            [(w, source_id, occ, docs) for w, (occ, docs) in deltas.items()],
        )

    for i in range(0, len(to_delete), DELETE_BATCH):
        batch = to_delete[i:i + DELETE_BATCH]
        placeholders = ",".join("?" * len(batch))
        conn.execute(
            f"DELETE FROM source_counts WHERE source_id = ? AND word IN ({placeholders})",
            [source_id, *batch],
        )

    conn.execute(
        "UPDATE sources SET total_tokens = total_tokens + ? WHERE source_id = ?",
        (token_delta, source_id),
    )
    # compute_zipf.py only ever INSERTs or UPDATEs; it never deletes the row of
    # a word that vanished from source_counts. Wipe this source's slice so its
    # next run starts clean instead of leaving stale rows for the words that
    # just disappeared (migrate_elisions.py hit the same trap — see its
    # docstring).
    conn.execute("DELETE FROM source_zipf WHERE source_id = ?", (source_id,))
    conn.commit()
    return len(to_delete), dropped, token_delta


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    args = parser.parse_args()

    conn = connect(args.db)
    sources = [r[0] for r in conn.execute("SELECT source_id FROM sources").fetchall()]

    total_repaired = total_dropped = total_delta = 0
    for source_id in sources:
        repaired, dropped, delta = migrate_source(conn, source_id, args.dry_run)
        total_repaired += repaired
        total_dropped += dropped
        total_delta += delta

    verb = "Would repair" if args.dry_run else "Repaired"
    print(f"\n{verb}: {total_repaired:,} rows "
          f"({total_dropped:,} dropped outright), {total_delta:+,} tokens total",
          flush=True)
    if args.dry_run:
        print("(dry run — no changes made)")
    else:
        print("Next: compute_zipf.py, merge.py, build_lemma_layer.py, "
              "build_package.py, validate.py")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
