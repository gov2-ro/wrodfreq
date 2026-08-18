#!/usr/bin/env python3
"""Stage 2 — per-source Zipf + reliability flag.

Pure function of `source_counts` + `sources.total_tokens` (spec §7.3): safe to
re-run on unchanged input, which is exactly what validate.py's idempotence
check (§11.6) exercises. Also derives and stores each source's Zipf floor in
`sources.zipf_floor` (spec §8.1).

Usage:
    python build/compute_zipf.py [--db PATH] [--source SOURCE_ID]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrodfreq.db import DEFAULT_DB_PATH, connect
from wrodfreq.zipf import is_reliable, source_zipf_floor, zipf_from_counts


def compute_source(conn: sqlite3.Connection, source_id: str, total_tokens: int) -> int:
    """Recompute source_zipf for one source. Returns the number of words written."""
    rows = conn.execute(
        "SELECT word, occurrences FROM source_counts WHERE source_id = ?",
        (source_id,),
    ).fetchall()

    values = []
    for word, occurrences in rows:
        reliable = is_reliable(occurrences)
        zipf = zipf_from_counts(occurrences, total_tokens) if reliable else None
        values.append((word, source_id, zipf, int(reliable)))

    conn.executemany(
        """
        INSERT INTO source_zipf (word, source_id, zipf, reliable)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(word, source_id) DO UPDATE SET
            zipf = excluded.zipf, reliable = excluded.reliable
        """,
        values,
    )
    conn.execute(
        "UPDATE sources SET zipf_floor = ? WHERE source_id = ?",
        (source_zipf_floor(total_tokens), source_id),
    )
    conn.commit()
    return len(values)


def compute_all(conn: sqlite3.Connection, only_source: str | None = None) -> None:
    query = "SELECT source_id, total_tokens FROM sources"
    params: tuple = ()
    if only_source:
        query += " WHERE source_id = ?"
        params = (only_source,)
    sources = conn.execute(query, params).fetchall()

    for source_id, total_tokens in sources:
        if not total_tokens:
            print(f"  {source_id}: skipped (total_tokens=0, not yet ingested)")
            continue
        n = compute_source(conn, source_id, total_tokens)
        print(f"  {source_id}: {n:,} words, floor={source_zipf_floor(total_tokens):.2f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--source", help="Only recompute this source_id")
    args = parser.parse_args()

    conn = connect(args.db)
    compute_all(conn, args.source)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
