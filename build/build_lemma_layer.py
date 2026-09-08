#!/usr/bin/env python3
"""Stage 4 — the lemma layer: rolls surface-form counts up through DEX
paradigms into `lemma_zipf` (spec §7.5, §9). Optional — the shipped package
must work without it, and this script exits 0 (not an error) if the DEX
paradigm map isn't reachable, printing a warning instead of failing the build.

`wordfreq` counts surface forms, so a heavily-inflected verb like `înmărmuri`
reads as nearly extinct (317 hits) while its participle `înmărmurit` alone has
5,846 — the citation form is only one slice of the paradigm. This stage rolls
a lemma's whole paradigm together instead.

Disambiguation math (ambiguous forms split, weighted by each claimant's own
headword frequency) lives in `wrodfreq/lemma.py`, ported from oțios's
`validate_diachronic.py:386-498` (see CLAUDE.md's "what to copy" table and
that module's docstring for the full reasoning). `lemma_zipf`'s schema (spec
§7.1) has no document-count column, so `aggregate_by_family`'s document half
is computed for fidelity to the ported function and then simply unused here.

One deliberate, documented departure from the ported function: oțios's
version rounds each lemma's disambiguated count to the nearest int before
returning (`validate_diachronic.py:458`) — reasonable for its coarser
present/absent/forgotten verdict system, but for wROdfreq that rounding can
flip a word across the MIN_OCC_PER_SOURCE=5 reliability floor (round(4.6) = 5,
a false positive; the true evidence was 4.6). `wrodfreq/lemma.py` keeps the
split as a float straight through into the reliability check and the zipf
calculation, never rounding until display.

**Structural departure from oțios's own cross-corpus step, and the point of
this whole docstring** (CLAUDE.md: "oțios asks a different question... do not
carry oțios's reasoning across unexamined"): oțios's `merge_panels()` sums
raw disambiguated occurrences across corpora, which is exactly the
CulturaX-dominates problem spec §8.2's trimmed mean exists to prevent — one
corpus being 200x the next would just reduce lemma-level merging to
"CulturaX's opinion with a Romanian paradigm map attached". Instead: each
eligible source's disambiguated (and separately, undivided/loose) lemma
occurrence total is converted to *that source's own* zipf first
(`zipf_from_counts`, same per-source reliability floor as every other
occurrence count in this project), and only *those* per-source zipf values
are combined across sources — via `wrodfreq.zipf.merge_zipf()`, the exact
same trimmed-mean function `merge.py` uses for surface forms. A lemma is
just a word whose count came from a paradigm roll-up instead of a single
surface form; the cross-source merge algorithm doesn't need to know the
difference.

`family_ratio` (undivided family total / disambiguated total, spec §9) is
computed the same way, in the same log-to-linear conversion: as
`10 ** (undivided_merged_zipf - disambiguated_merged_zipf)`. A `merged`-scale
raw-count ratio was deliberately not used, for the same CulturaX-dominance
reason as above.

Only words present in the DEX paradigm map (`form_lemma`) get a lemma_zipf
row *at all* — a surface form absent from the map is simply not part of any
known Romanian lexeme's paradigm as far as this table is concerned, and stays
reachable only through `merged`'s surface-form lookup. This is also what
keeps the per-source JOIN against `source_counts` cheap: only ~1.5M distinct
DEX forms are ever fetched, not the full ~33M distinct surface forms across
the panel.

Usage:
    python build/build_lemma_layer.py
    python build/build_lemma_layer.py --dex-db PATH

Output: data/wrodfreq.db, table `lemma_zipf`.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrodfreq.db import DEFAULT_DB_PATH, connect, eligible_sources
from wrodfreq.lemma import aggregate_by_family, aggregate_loose
from wrodfreq.zipf import MIN_OCC_PER_SOURCE, is_reliable, merge_zipf, zipf_from_counts

DEFAULT_DEX_DB = Path.home() / "devbox/otios/data/processed/inflected_forms.db"


def load_form_lemma(dex_db_path: Path) -> dict[str, list[str]]:
    conn = sqlite3.connect(dex_db_path)
    rows = conn.execute("SELECT form, lemma FROM form_lemma").fetchall()
    conn.close()
    out: dict[str, list[str]] = {}
    for form, lemma in rows:
        out.setdefault(form, []).append(lemma)
    return out


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def fetch_dex_freqs(conn: sqlite3.Connection, source_id: str) -> dict[str, tuple[int, int]]:
    """{word: (occurrences, documents)} for one source, restricted to DEX-paradigm
    words via a JOIN against the `_dex_forms` temp table (see main()).

    `CROSS JOIN` here isn't a real cross product — in SQLite it also means
    "don't reorder this join", which matters: a plain `JOIN` let the planner
    scan all of `source_counts` (tens of millions of rows across every
    source) and probe the small `_dex_forms` per row, since it had no
    selectivity estimate for `source_id`. Forcing `_dex_forms` (~1.5M rows)
    as the driving table instead turns each lookup into a direct hit on
    `source_counts`'s own `(word, source_id)` primary key — ~13x faster
    measured against the real database (26s to 2s for the `web` source).
    """
    rows = conn.execute(
        """
        SELECT sc.word, sc.occurrences, sc.documents
        FROM _dex_forms d CROSS JOIN source_counts sc
            ON sc.word = d.form AND sc.source_id = ?
        """,
        (source_id,),
    ).fetchall()
    return {word: (occ, doc) for word, occ, doc in rows}


LEMMA_ZIPF_DDL = """
    lemma           TEXT PRIMARY KEY,
    zipf            REAL NOT NULL,
    n_forms         INTEGER NOT NULL,
    zipf_headword   REAL,
    family_ratio    REAL
"""


def run(conn: sqlite3.Connection, sources: list[str], form_lemma: dict[str, list[str]]) -> int:
    source_tokens = dict(
        conn.execute(
            "SELECT source_id, total_tokens FROM sources WHERE source_id IN "
            f"({','.join('?' for _ in sources)})", sources,
        ).fetchall()
    )

    conn.execute("DROP TABLE IF EXISTS _dex_forms")
    conn.execute("CREATE TEMP TABLE _dex_forms (form TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO _dex_forms VALUES (?)", [(f,) for f in form_lemma])
    print(f"  {len(form_lemma):,} distinct DEX forms loaded", flush=True)

    disambig_zipfs: dict[str, list[float]] = defaultdict(list)
    loose_zipfs: dict[str, list[float]] = defaultdict(list)
    contributing_forms: dict[str, set[str]] = defaultdict(set)

    for source_id in sources:
        start = time.time()
        freqs = fetch_dex_freqs(conn, source_id)
        total_tokens = source_tokens[source_id]

        for form in freqs:
            for lemma in form_lemma[form]:
                contributing_forms[lemma].add(form)

        disambig = aggregate_by_family(freqs, form_lemma)
        loose = aggregate_loose(freqs, form_lemma)

        for lemma, (occ, _doc) in disambig.items():
            if is_reliable(occ, MIN_OCC_PER_SOURCE):
                disambig_zipfs[lemma].append(zipf_from_counts(occ, total_tokens))
        for lemma, occ in loose.items():
            if is_reliable(occ, MIN_OCC_PER_SOURCE):
                loose_zipfs[lemma].append(zipf_from_counts(occ, total_tokens))

        print(f"  [{source_id}] {len(freqs):,} DEX-paradigm words found, "
              f"{len(disambig):,} lemmas touched | {time.time() - start:.0f}s", flush=True)

    conn.execute("DROP TABLE IF EXISTS lemma_zipf_new")
    conn.execute(f"CREATE TABLE lemma_zipf_new ({LEMMA_ZIPF_DDL}) WITHOUT ROWID")

    # zipf_headword: the lemma's own citation form, looked up in the already-
    # computed surface-form table — a JOIN, not per-lemma queries.
    conn.execute("DROP TABLE IF EXISTS _lemma_names")
    conn.execute("CREATE TEMP TABLE _lemma_names (lemma TEXT PRIMARY KEY)")
    conn.executemany(
        "INSERT OR IGNORE INTO _lemma_names VALUES (?)",
        [(lemma,) for lemma in disambig_zipfs],
    )
    # CROSS JOIN forces _lemma_names (small) as the driving table instead of
    # scanning all of `merged` — same reasoning as fetch_dex_freqs above.
    headwords = dict(
        conn.execute(
            "SELECT m.word, m.zipf FROM _lemma_names l "
            "CROSS JOIN merged m ON m.word = l.lemma"
        ).fetchall()
    )

    rows = []
    for lemma, zipfs in disambig_zipfs.items():
        zipf = merge_zipf(zipfs)
        loose = loose_zipfs.get(lemma)
        family_ratio = 10 ** (merge_zipf(loose) - zipf) if loose else None
        n_forms = len(contributing_forms.get(lemma, ()))
        rows.append((lemma, zipf, n_forms, headwords.get(lemma), family_ratio))

    conn.executemany("INSERT INTO lemma_zipf_new VALUES (?,?,?,?,?)", rows)
    conn.execute("DROP TABLE IF EXISTS lemma_zipf")
    conn.execute("ALTER TABLE lemma_zipf_new RENAME TO lemma_zipf")
    conn.execute("DROP TABLE IF EXISTS _dex_forms")
    conn.execute("DROP TABLE IF EXISTS _lemma_names")
    conn.commit()
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--dex-db", type=Path, default=DEFAULT_DEX_DB,
                         help="Path to oțios's inflected_forms.db")
    args = parser.parse_args()

    if not args.dex_db.exists():
        print(f"DEX paradigm map not found at {args.dex_db} — the lemma layer is "
              f"optional (spec §7.5), skipping rather than failing the build. "
              f"Pass --dex-db to point elsewhere.")
        return 0

    conn = connect(args.db)
    sources = eligible_sources(conn)
    if not sources:
        print("No eligible (contemporary + completed) sources — nothing to build.")
        return 1
    print(f"Building lemma layer from {len(sources)} eligible sources: "
          f"{', '.join(sources)}", flush=True)

    print("Loading DEX paradigm map...", flush=True)
    form_lemma = load_form_lemma(args.dex_db)

    start = time.time()
    n_lemmas = run(conn, sources, form_lemma)
    elapsed = time.time() - start
    print(f"\nDone: {n_lemmas:,} lemmas written to `lemma_zipf` in {elapsed:.0f}s",
          flush=True)

    rows = conn.execute(
        "SELECT lemma, zipf, n_forms, zipf_headword, family_ratio FROM lemma_zipf "
        "ORDER BY family_ratio DESC LIMIT 20"
    ).fetchall()
    print("\nTop 20 by family_ratio (biggest paradigm-vs-headword gap):")
    for lemma, zipf, n_forms, zipf_headword, family_ratio in rows:
        hw = f"{zipf_headword:.2f}" if zipf_headword is not None else "—"
        print(f"  {lemma:20s} zipf={zipf:.2f}  headword={hw}  "
              f"n_forms={n_forms}  family_ratio={family_ratio:.1f}x")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
