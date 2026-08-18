#!/usr/bin/env python3
"""Stage 6 — the CI validation gate (spec §11). Must fail the build on a regression.

Six checks total in the spec; only two are checkable at M1, before `merge.py`,
the DEX lexeme set and a `wordfreq` comparison exist:

  1. Function words land in Zipf 6.0-7.5 — the check that catches a broken
     denominator (spec §3.2), run here per-source against `source_zipf`.
  6. Idempotence — re-running compute_zipf.py on unchanged `source_counts`
     must produce a byte-identical `source_zipf`.

Checks 2 (rank correlation vs `wordfreq`), 3 (monotone sanity pairs), 4 (DEX
coverage) and 5 (per-source spread report) need `merged`, DEX and `wordfreq`
data that only exist from M3/M4 onward — add them there rather than stubbing
them out here.

Usage:
    python build/validate.py [--db PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrodfreq.db import DEFAULT_DB_PATH, connect

from compute_zipf import compute_all

FUNCTION_WORDS = ["de", "și", "la", "un", "cu"]
ZIPF_LOW, ZIPF_HIGH = 6.0, 7.5

# The 6.0-7.5 band is the spec's check against `merged` (spec §11.1), a
# multi-source trimmed mean. A single register-bound source legitimately
# overshoots it at the very top word: `wordfreq`'s own published Romanian
# 'de' is 7.72 (checked directly against wordfreq.zipf_frequency('de', 'ro')
# on 2026-08-18), and Wikipedia-only measured 7.67 here — both past 7.5, both
# correct. Before `merged` exists (pre-M3/M4), widen the ceiling for the
# per-source fallback rather than false-fail on this kind of overshoot; the
# floor stays put, since a function word landing *low* (or missing outright)
# is the actual signature of the denominator bug this check exists to catch.
ZIPF_HIGH_SINGLE_SOURCE = 8.0


def check_function_words(conn: sqlite3.Connection) -> bool:
    """Check 1: de, și, la, un, cu must fall in Zipf 6.0-7.5."""
    print("[1] function words in Zipf 6.0-7.5")

    (merged_count,) = conn.execute("SELECT COUNT(*) FROM merged").fetchone()
    if merged_count:
        placeholders = ",".join("?" * len(FUNCTION_WORDS))
        rows = conn.execute(
            f"SELECT word, zipf FROM merged WHERE word IN ({placeholders})",
            FUNCTION_WORDS,
        ).fetchall()
        return _report(rows, "merged", ZIPF_HIGH)

    source_ids = [r[0] for r in conn.execute(
        "SELECT source_id FROM sources WHERE status != 'rejected'"
    ).fetchall()]
    if not source_ids:
        print("  FAIL — no sources ingested yet")
        return False

    ok = True
    for source_id in source_ids:
        placeholders = ",".join("?" * len(FUNCTION_WORDS))
        rows = conn.execute(
            f"""SELECT word, zipf FROM source_zipf
                WHERE source_id = ? AND reliable = 1 AND word IN ({placeholders})""",
            [source_id, *FUNCTION_WORDS],
        ).fetchall()
        ok = _report(rows, source_id, ZIPF_HIGH_SINGLE_SOURCE) and ok
    return ok


def _report(rows: list[tuple[str, float]], label: str, zipf_high: float) -> bool:
    found = dict(rows)
    ok = True
    for word in FUNCTION_WORDS:
        zipf = found.get(word)
        if zipf is None:
            print(f"  [{label}] {word:6s} MISSING (not reliable or not ingested)")
            ok = False
        elif not (ZIPF_LOW <= zipf <= zipf_high):
            print(f"  [{label}] {word:6s} {zipf:.2f}  OUT OF RANGE "
                  f"({ZIPF_LOW}-{zipf_high}) — denominator is probably wrong")
            ok = False
        else:
            print(f"  [{label}] {word:6s} {zipf:.2f}  ok")
    return ok


def _source_zipf_hash(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT word, source_id, zipf, reliable FROM source_zipf ORDER BY source_id, word"
    ).fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def check_idempotence(conn: sqlite3.Connection) -> bool:
    """Check 6: re-running compute_zipf.py on unchanged source_counts must be
    byte-identical. At M1 that means stage 2 alone; stages 3-5 join this check
    as they're built."""
    print("[6] idempotence (compute_zipf.py re-run)")
    compute_all(conn)
    hash_a = _source_zipf_hash(conn)
    compute_all(conn)
    hash_b = _source_zipf_hash(conn)
    ok = hash_a == hash_b
    print(f"  {'ok' if ok else 'FAIL — source_zipf changed on re-run'} "
          f"({hash_a[:12]}... vs {hash_b[:12]}...)")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    conn = connect(args.db)
    results = {
        "function_words": check_function_words(conn),
        "idempotence": check_idempotence(conn),
    }
    conn.close()

    passed = sum(results.values())
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
