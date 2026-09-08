#!/usr/bin/env python3
"""Stage 3 — trimmed mean across the panel into `merged` (spec §7.4, §8).

Pure function of `sources` + `source_zipf` (which already carries every
attesting word for a source, reliable or not — see compute_zipf.py: it writes
one source_zipf row for *every* word in that source's source_counts, with
zipf=NULL/reliable=0 below the floor). Safe to re-run: this script always
rebuilds `merged` from scratch rather than upserting into it, so a change to
any source's counts (or a source being re-ingested, or dropped) can never
leave a stale row behind. Also makes idempotence (spec §11.6) trivial — a
full rebuild from the same inputs is byte-identical by construction, no
incremental-drift risk to reason about.

Rebuild is atomic: results land in a scratch `merged_new` table, and only the
final `DROP TABLE merged; ALTER TABLE merged_new RENAME TO merged;` swap is
allowed to touch the real `merged` name — a crash or Ctrl+C mid-run leaves the
previous `merged` (or none, on a first run) untouched rather than half-built.

**Contemporary sources only, by default** (spec §6.2's "a frequency table that
quietly averages 1890 and 2023 is lying about the present" — CLAUDE.md
repeats this as a hard rule). All five sources ingested so far (wiki, web,
news, subs, eu) are period='contemporary', so this is a no-op today; it
exists so a future historical/mixed source (`books`, a `ref` CoRoLa source)
doesn't silently pull into the default merge. No opt-in flag for those yet —
add one when a non-contemporary source actually exists to opt into, not
before.

The merge itself (spec §8.2), per word, over that word's *reliable* zipf
values among eligible sources only:
  - 0 reliable sources: word is below the table's floor — omitted from
    `merged` entirely (see wrodfreq.zipf.merge_zipf's docstring for why this
    is never a fabricated zipf of 0).
  - 1-4: plain mean, no trim (trimming 3 values to 1 is "pick the middle
    corpus" — wordfreq's own Romanian list is stuck with exactly this bug).
  - >=5: drop the max and min, mean the rest.

`is_dex` is populated from oțios's vendored DEX inflected-forms map
(~/devbox/otios/data/processed/inflected_forms.db, see CLAUDE.md's "the one
data asset to reuse") if it's reachable — this is a build-time lookup for one
boolean column, not a runtime dependency of the shipped wrodfreq package (the
"never import from oțios at runtime" rule is about the package, not the build
pipeline; validate.py's very first run already cross-referenced oțios's
installed `wordfreq` package the same way). Degrades gracefully to is_dex=0
for every word if the file isn't found, printed as a warning, never a hard
failure — matching how the lemma layer (M5, optional) is meant to degrade.

Usage:
    python build/merge.py
    python build/merge.py --dex-db PATH   # override the DEX forms db location
    python build/merge.py --no-dex        # skip the DEX lookup entirely

Output: data/wrodfreq.db, table `merged`.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrodfreq.db import DEFAULT_DB_PATH, connect
from wrodfreq.zipf import merge_zipf

DEFAULT_DEX_DB = Path.home() / "devbox/otios/data/processed/inflected_forms.db"
BATCH_SIZE = 200_000


def eligible_sources(conn: sqlite3.Connection) -> list[str]:
    """Contemporary, fully-ingested sources — the default merge's inputs (spec §6.2)."""
    rows = conn.execute(
        "SELECT source_id FROM sources WHERE period = 'contemporary' "
        "AND status = 'completed' ORDER BY source_id"
    ).fetchall()
    return [r[0] for r in rows]


def load_dex_forms(dex_db_path: Path | None) -> set[str]:
    if dex_db_path is None:
        print("  --no-dex: is_dex will be 0 for every word", flush=True)
        return set()
    if not dex_db_path.exists():
        print(f"  DEX forms db not found at {dex_db_path} — is_dex will be 0 "
              f"for every word (pass --dex-db to point elsewhere)", flush=True)
        return set()
    conn = sqlite3.connect(dex_db_path)
    forms = {row[0] for row in conn.execute("SELECT DISTINCT form FROM inflected")}
    conn.close()
    print(f"  loaded {len(forms):,} DEX inflected forms from {dex_db_path}", flush=True)
    return forms


MERGED_DDL = """
    word            TEXT PRIMARY KEY,
    zipf            REAL NOT NULL,
    n_reliable      INTEGER NOT NULL,
    n_attesting     INTEGER NOT NULL,
    n_sources       INTEGER NOT NULL,
    zipf_min        REAL,
    zipf_max        REAL,
    spread          REAL,
    is_dex          INTEGER DEFAULT 0
"""


def run_merge(conn: sqlite3.Connection, sources: list[str], dex_forms: set[str]) -> tuple[int, int]:
    """Rebuild `merged` from `source_zipf`. Returns (words_written, words_below_floor)."""
    n_sources = len(sources)
    placeholders = ",".join("?" for _ in sources)

    conn.execute("DROP TABLE IF EXISTS merged_new")
    conn.execute(f"CREATE TABLE merged_new ({MERGED_DDL}) WITHOUT ROWID")

    # source_zipf's PRIMARY KEY is (word, source_id) WITHOUT ROWID, so a table
    # scan already visits rows in (word, source_id) order — no separate sort
    # needed for the group-by-word streaming pass below.
    cur = conn.execute(
        f"SELECT word, zipf, reliable FROM source_zipf "
        f"WHERE source_id IN ({placeholders}) ORDER BY word",
        sources,
    )

    batch: list[tuple] = []
    words_written = 0
    words_below_floor = 0

    def flush_word(word: str, n_attesting: int, reliable_zipfs: list[float]) -> None:
        nonlocal words_written, words_below_floor
        if not reliable_zipfs:
            words_below_floor += 1
            return
        zmin, zmax = min(reliable_zipfs), max(reliable_zipfs)
        batch.append((
            word, merge_zipf(reliable_zipfs), len(reliable_zipfs), n_attesting,
            n_sources, zmin, zmax, zmax - zmin, int(word in dex_forms),
        ))
        words_written += 1

    current_word: str | None = None
    n_attesting = 0
    reliable_zipfs: list[float] = []
    start = time.time()

    for word, zipf, reliable in cur:
        if word != current_word:
            if current_word is not None:
                flush_word(current_word, n_attesting, reliable_zipfs)
                if len(batch) >= BATCH_SIZE:
                    conn.executemany(
                        "INSERT INTO merged_new VALUES (?,?,?,?,?,?,?,?,?)", batch)
                    batch.clear()
                    elapsed = time.time() - start
                    print(f"  {words_written:,} words merged | {elapsed:.0f}s elapsed",
                          flush=True)
            current_word = word
            n_attesting = 0
            reliable_zipfs = []
        n_attesting += 1
        if reliable:
            reliable_zipfs.append(zipf)

    if current_word is not None:
        flush_word(current_word, n_attesting, reliable_zipfs)
    if batch:
        conn.executemany("INSERT INTO merged_new VALUES (?,?,?,?,?,?,?,?,?)", batch)

    conn.execute("DROP TABLE IF EXISTS merged")
    conn.execute("ALTER TABLE merged_new RENAME TO merged")
    conn.commit()

    return words_written, words_below_floor


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--dex-db", type=Path, default=DEFAULT_DEX_DB,
                         help="Path to oțios's inflected_forms.db, for is_dex")
    parser.add_argument("--no-dex", action="store_true",
                         help="Skip the DEX lookup — is_dex = 0 for every word")
    args = parser.parse_args()

    conn = connect(args.db)

    sources = eligible_sources(conn)
    if not sources:
        print("No eligible (contemporary + completed) sources — nothing to merge.")
        return 1
    print(f"Merging {len(sources)} eligible sources: {', '.join(sources)}", flush=True)

    dex_forms = load_dex_forms(None if args.no_dex else args.dex_db)

    start = time.time()
    words_written, words_below_floor = run_merge(conn, sources, dex_forms)
    elapsed = time.time() - start

    print(f"\nDone: {words_written:,} words written to `merged`, "
          f"{words_below_floor:,} attested-but-below-floor words omitted, "
          f"in {elapsed:.0f}s", flush=True)

    n_dex_matched = conn.execute(
        "SELECT COUNT(*) FROM merged WHERE is_dex = 1"
    ).fetchone()[0]
    if dex_forms:
        print(f"is_dex: {n_dex_matched:,} words matched")

    rows = conn.execute(
        "SELECT word, zipf, n_reliable, n_attesting, spread FROM merged "
        "ORDER BY zipf DESC LIMIT 20"
    ).fetchall()
    print("\nTop 20 by zipf:")
    for word, zipf, n_reliable, n_attesting, spread in rows:
        print(f"  {word:20s} zipf={zipf:.2f}  n_reliable={n_reliable}  "
              f"n_attesting={n_attesting}  spread={spread if spread is not None else 0:.2f}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
