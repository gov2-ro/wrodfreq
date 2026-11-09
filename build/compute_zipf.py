#!/usr/bin/env python3
"""Stage 2 — per-source Zipf + reliability flag.

Pure function of `source_counts` + `sources.total_tokens` (spec §7.3). Also derives
and stores each source's Zipf floor in `sources.zipf_floor` (spec §8.1).

**Exact replacement** (brief B7, finding A07). Each run replaces the derived rows
with exactly the rows the current counts give, in one transaction: a word that left
`source_counts` leaves `source_zipf` too, and a failure keeps the previous complete
set. The earlier upsert kept rows for deleted words, so stage 3 kept merging them.

**Conservation first.** Before deriving anything the stage checks
`SUM(occurrences) == total_tokens` for every source it touches and refuses
otherwise: current counts are the truth only when they are consistent. A source
with no tokens and no counts has its derived rows cleared and its floor unset; a
source with counts and no tokens is an error.

Every run records what it was computed from (`stage_state`, `zipf:<source>`), so
stages 3-5 can refuse stale input (ADR-006).

Usage:
    python build/compute_zipf.py [--db PATH] [--source SOURCE_ID]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from provenance import write_stage, zipf_inputs
from wrodfreq.db import DEFAULT_DB_PATH, connect
from wrodfreq.zipf import is_reliable, source_zipf_floor, zipf_from_counts

BATCH = 100_000

SOURCE_ZIPF_DDL = """
    word          TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    zipf          REAL,
    reliable      INTEGER NOT NULL,
    PRIMARY KEY (word, source_id)
"""


class StageRefused(RuntimeError):
    """The stage's input is inconsistent; deriving from it would publish a wrong number."""


def check_conservation(conn: sqlite3.Connection, source_ids: list[str]) -> dict[str, int]:
    """{source: total_tokens} after checking each source's raw sum; raises on a mismatch."""
    sums = dict(conn.execute(
        "SELECT source_id, SUM(occurrences) FROM source_counts GROUP BY source_id").fetchall())
    totals = dict(conn.execute("SELECT source_id, total_tokens FROM sources").fetchall())
    bad = []
    for sid in source_ids:
        total, occ = totals[sid], sums.get(sid, 0)
        if occ != total:
            bad.append(f"{sid}: SUM(occurrences) {occ:,} != total_tokens {total:,}")
        elif total == 0 and occ:
            bad.append(f"{sid}: counts without tokens")
    orphans = sorted(set(sums) - set(totals))
    if orphans:
        bad.append(f"counts for unknown sources: {', '.join(orphans)}")
    if bad:
        raise StageRefused("raw counts are not conserved — stage 2 refuses to derive from "
                           "them:\n  " + "\n  ".join(bad))
    return {sid: totals[sid] for sid in source_ids}


def _row(word: str, sid: str, occ: int, total: int) -> tuple:
    reliable = is_reliable(occ)
    return (word, sid, zipf_from_counts(occ, total) if reliable else None, int(reliable))


def _begin(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")


def compute_source(conn: sqlite3.Connection, source_id: str, total_tokens: int | None = None) -> int:
    """Replace one source's `source_zipf` rows in one transaction. Returns rows written.

    `total_tokens` is accepted for compatibility and must match the stored value.
    """
    totals = check_conservation(conn, [source_id])
    total = totals[source_id]
    if total_tokens is not None and total_tokens != total:
        raise StageRefused(f"{source_id}: caller's total {total_tokens:,} != stored {total:,}")
    _begin(conn)
    try:
        conn.execute("DELETE FROM source_zipf WHERE source_id = ?", (source_id,))
        n = 0
        if total:
            cur = conn.cursor().execute(
                "SELECT word, occurrences FROM source_counts WHERE source_id = ? ORDER BY word",
                (source_id,))
            while rows := cur.fetchmany(BATCH):
                conn.executemany("INSERT INTO source_zipf VALUES (?, ?, ?, ?)",
                                 [_row(w, source_id, o, total) for w, o in rows])
                n += len(rows)
        conn.execute("UPDATE sources SET zipf_floor = ? WHERE source_id = ?",
                     (source_zipf_floor(total) if total else None, source_id))
        write_stage(conn, f"zipf:{source_id}", zipf_inputs(conn, source_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return n


def compute_everything(conn: sqlite3.Connection) -> dict[str, int]:
    """Rebuild the whole `source_zipf` table in one transaction (all sources)."""
    sources = [r[0] for r in conn.execute("SELECT source_id FROM sources ORDER BY source_id")]
    totals = check_conservation(conn, sources)
    written = {sid: 0 for sid in sources}
    _begin(conn)
    try:
        conn.execute("DROP TABLE IF EXISTS source_zipf_new")
        conn.execute(f"CREATE TABLE source_zipf_new ({SOURCE_ZIPF_DDL}) WITHOUT ROWID")
        cur = conn.cursor().execute(
            "SELECT word, source_id, occurrences FROM source_counts ORDER BY word, source_id")
        while rows := cur.fetchmany(BATCH):
            conn.executemany("INSERT INTO source_zipf_new VALUES (?, ?, ?, ?)",
                             [_row(w, s, o, totals[s]) for w, s, o in rows])
            for _, s, _ in rows:
                written[s] += 1
        conn.execute("DROP TABLE source_zipf")
        conn.execute("ALTER TABLE source_zipf_new RENAME TO source_zipf")
        for sid in sources:
            total = totals[sid]
            conn.execute("UPDATE sources SET zipf_floor = ? WHERE source_id = ?",
                         (source_zipf_floor(total) if total else None, sid))
            write_stage(conn, f"zipf:{sid}", zipf_inputs(conn, sid))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return written


def compute_all(conn: sqlite3.Connection, only_source: str | None = None) -> None:
    if only_source is not None:
        if conn.execute("SELECT 1 FROM sources WHERE source_id = ?",
                        (only_source,)).fetchone() is None:
            raise StageRefused(f"unknown source {only_source!r}")
        n = compute_source(conn, only_source)
        written = {only_source: n}
    else:
        written = compute_everything(conn)
    floors = dict(conn.execute("SELECT source_id, zipf_floor FROM sources"))
    for sid, n in written.items():
        floor = floors.get(sid)
        print(f"  {sid}: {n:,} words, "
              f"{'floor=' + format(floor, '.2f') if floor is not None else 'no tokens — cleared'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 2: per-source Zipf")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--source", help="Only recompute this source_id")
    args = parser.parse_args()
    if not args.db.is_file():
        print(f"database not found: {args.db}", file=sys.stderr)
        return 2
    conn = connect(args.db)
    try:
        compute_all(conn, args.source)
    except StageRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
