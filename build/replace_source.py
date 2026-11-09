#!/usr/bin/env python3
"""Replace one source in a target database with a staged donor generation (brief B7 step 6).

The donor is a database that holds one completed generation of the source, built by
the fixed ingester in isolation. In **one transaction** on the target this tool:

  1. deletes the target's counts and derived rows for the source;
  2. copies the donor's `sources` row, counts and `ingest_state` (read-only attach);
  3. rebinds the state to the target's `db_id` on purpose, recording the donor's
     identity in `provenance.imported_from` — it never leaves state pointing at the
     donor database;
  4. invalidates every derived stage (`stage_state`);
  5. checks `SUM(occurrences) == total_tokens` for the source, and rolls back
     otherwise.

Nothing is added to existing counts; this is a replacement, not an ingest.

Usage:
    python build/replace_source.py --target CANDIDATE.db --donor STAGED.db --source social
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_state import canonical, now
from wrodfreq.db import connect, connect_readonly, db_id


class ReplaceRefused(RuntimeError):
    pass


def check_donor(donor: Path, source: str) -> dict:
    conn = connect_readonly(donor)
    try:
        st = conn.execute("SELECT state, generation, seq, provenance FROM ingest_state "
                          "WHERE source_id = ?", (source,)).fetchone()
        src = conn.execute("SELECT total_tokens, status FROM sources WHERE source_id = ?",
                           (source,)).fetchone()
        occ = conn.execute("SELECT COALESCE(SUM(occurrences), 0), COUNT(*) FROM source_counts "
                           "WHERE source_id = ?", (source,)).fetchone()
        others = conn.execute("SELECT COUNT(*) FROM source_counts WHERE source_id != ?",
                              (source,)).fetchone()[0]
        donor_id = db_id(conn)
    finally:
        conn.close()
    if st is None or st[0] != "completed":
        raise ReplaceRefused(f"donor has no completed {source} generation ({st and st[0]})")
    if src is None or src[1] != "completed":
        raise ReplaceRefused(f"donor's sources row for {source} is not completed")
    if occ[0] != src[0]:
        raise ReplaceRefused(f"donor {source}: SUM(occurrences) {occ[0]:,} != "
                             f"total_tokens {src[0]:,}")
    return {"db_id": donor_id, "generation": st[1], "seq": st[2], "rows": occ[1],
            "total_tokens": src[0], "other_source_rows": others}


def replace(target: Path, donor: Path, source: str) -> dict:
    info = check_donor(donor, source)
    conn = connect(target)
    conn.isolation_level = None
    target_id = db_id(conn)
    conn.execute(f"ATTACH DATABASE 'file:{donor.resolve()}?mode=ro' AS donor")
    conn.execute("BEGIN IMMEDIATE")
    try:
        before = conn.execute("SELECT COUNT(*), COALESCE(SUM(occurrences), 0) FROM "
                              "main.source_counts WHERE source_id = ?", (source,)).fetchone()
        conn.execute("DELETE FROM main.source_counts WHERE source_id = ?", (source,))
        conn.execute("DELETE FROM main.source_zipf WHERE source_id = ?", (source,))
        conn.execute("DELETE FROM main.ingest_state WHERE source_id = ?", (source,))
        conn.execute("DELETE FROM main.sources WHERE source_id = ?", (source,))
        conn.execute("INSERT INTO main.sources SELECT * FROM donor.sources WHERE source_id = ?",
                     (source,))
        conn.execute("UPDATE main.sources SET zipf_floor = NULL WHERE source_id = ?", (source,))
        conn.execute("INSERT INTO main.source_counts SELECT * FROM donor.source_counts "
                     "WHERE source_id = ? ORDER BY word", (source,))
        conn.execute("INSERT INTO main.ingest_state SELECT * FROM donor.ingest_state "
                     "WHERE source_id = ?", (source,))
        prov = json.loads(conn.execute("SELECT provenance FROM main.ingest_state "
                                       "WHERE source_id = ?", (source,)).fetchone()[0])
        prov["imported_from"] = {"db_id": info["db_id"], "generation": info["generation"],
                                 "seq": info["seq"], "at": now(),
                                 "replaced_rows": before[0],
                                 "replaced_sum_occurrences": before[1]}
        conn.execute("UPDATE main.ingest_state SET db_id = ?, provenance = ? "
                     "WHERE source_id = ?", (target_id, canonical(prov), source))
        conn.execute("DELETE FROM main.stage_state")
        got = conn.execute("SELECT COALESCE(SUM(occurrences), 0), COUNT(*) FROM "
                           "main.source_counts WHERE source_id = ?", (source,)).fetchone()
        total = conn.execute("SELECT total_tokens FROM main.sources WHERE source_id = ?",
                             (source,)).fetchone()[0]
        if got != (total, info["rows"]):
            raise ReplaceRefused(f"after import: {got} vs total {total:,}, {info['rows']:,} rows")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("DETACH DATABASE donor")
        conn.close()
    return {**info, "replaced_rows": before[0], "replaced_sum_occurrences": before[1],
            "target_db_id": target_id}


def main() -> int:
    parser = argparse.ArgumentParser(description="Replace one source from a staged donor db")
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--donor", type=Path, required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    for p in (args.target, args.donor):
        if not p.is_file():
            print(f"database not found: {p}", file=sys.stderr)
            return 2
    try:
        out = replace(args.target, args.donor, args.source)
    except ReplaceRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"{args.source}: replaced {out['replaced_rows']:,} rows "
          f"(sum {out['replaced_sum_occurrences']:,}) with {out['rows']:,} rows "
          f"(sum {out['total_tokens']:,}) from generation {out['generation'][:12]}; "
          f"state rebound from donor {out['db_id'][:12]} to target {out['target_db_id'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
