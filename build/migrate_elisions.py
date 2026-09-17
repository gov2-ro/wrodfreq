#!/usr/bin/env python3
"""One-time migration: split elision-hyphenated tokens in `source_counts` in
place, without re-ingesting any corpus.

Why in-place rather than a re-ingest (spec finding, docs/activity-history.md
2026-09-08 and 2026-09-17, wrodfreq/tokenizer.py's module docstring): a
token's occurrence count is exact regardless of *when* it's computed, so
splitting "într-o"'s existing count into "într"+"o" produces exactly the
result a corrected tokenizer would have produced from scratch — zipf values
come out exactly right, with none of the multi-day cost of re-reading 27.8B
tokens. The one approximation: `documents` becomes an upper bound for split
words (if a document has both "într-o" and "într-un", "într" gets credited
twice instead of once, since source_counts doesn't retain which document
each occurrence came from). Accepted: `documents` is a secondary field here,
and zipf — the thing check 2 actually measures — is unaffected.

Also cleans up a real gap this exposed in compute_zipf.py: it only INSERTs
or UPDATEs `source_zipf` rows for words *currently* in `source_counts`, never
deletes one for a word that disappeared — so a deleted compound's stale
`source_zipf` row would otherwise survive a bare re-run. Wiped explicitly
here per source; compute_zipf.py repopulates it fresh right after.

Run once. After this, wrodfreq/tokenizer.py's tokenize() and this script's
one-time transform agree on every word, so no future ingestion needs
anything special — a newly ingested source just tokenizes correctly from
the start.

Usage:
    python build/migrate_elisions.py --dry-run   # report only, no writes
    python build/migrate_elisions.py             # apply

Then, to bring every downstream table back in sync:
    python build/compute_zipf.py
    python build/merge.py
    python build/build_lemma_layer.py
    python build/build_package.py
    python build/validate.py

Back up data/wrodfreq.db before running for real — this mutates
source_counts, source_zipf, and sources.total_tokens directly, not via an
atomic table-swap (unlike merge.py/build_package.py), since this is a
one-time transform rather than a script meant to be safely re-run.
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


def migrate_source(conn: sqlite3.Connection, source_id: str, dry_run: bool) -> tuple[int, int]:
    """Returns (words_split, token_count_delta) for one source."""
    rows = conn.execute(
        "SELECT word, occurrences, documents FROM source_counts "
        "WHERE source_id = ? AND word LIKE '%-%'",
        (source_id,),
    ).fetchall()

    to_delete: list[str] = []
    deltas: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    token_delta = 0

    for word, occ, docs in rows:
        parts = _split_elisions(word)
        if len(parts) == 1:
            continue
        to_delete.append(word)
        token_delta += (len(parts) - 1) * occ
        for part in parts:
            entry = deltas[part]
            entry[0] += occ
            entry[1] += docs

    print(f"  [{source_id}] {len(rows):,} hyphenated words scanned, "
          f"{len(to_delete):,} split, +{token_delta:,} tokens", flush=True)

    if dry_run or not to_delete:
        return len(to_delete), token_delta

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
    # compute_zipf.py never deletes a stale row for a word that vanished from
    # source_counts (see module docstring) — wipe this source's slice now so
    # its next run starts clean instead of upserting alongside 118-ish (or
    # however many) leftover rows for words like "într-o" that no longer exist.
    conn.execute("DELETE FROM source_zipf WHERE source_id = ?", (source_id,))
    conn.commit()
    return len(to_delete), token_delta


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    args = parser.parse_args()

    conn = connect(args.db)
    sources = [r[0] for r in conn.execute("SELECT source_id FROM sources").fetchall()]

    total_split = 0
    total_delta = 0
    for source_id in sources:
        n_split, delta = migrate_source(conn, source_id, args.dry_run)
        total_split += n_split
        total_delta += delta

    verb = "Would split" if args.dry_run else "Split"
    print(f"\n{verb}: {total_split:,} words, +{total_delta:,} tokens total", flush=True)
    if args.dry_run:
        print("(dry run — no changes made)")
    else:
        print("Next: compute_zipf.py, merge.py, build_lemma_layer.py, "
              "build_package.py, validate.py")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
