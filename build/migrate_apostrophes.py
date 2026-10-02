#!/usr/bin/env python3
"""One-time migration: re-split the apostrophe-edged tokens that an older
tokenizer wrote into `source_counts`, without re-ingesting any corpus
(ADR-001 decision 2, Brief B5).

Companion to `migrate_dashes.py`, and the same defect class one character over:
that script cleaned `-`-edged tokens and missed `'`-edged ones. The current
token regex requires a token to start and end with a letter, so `'aci`, `'aswad`
and `acu'` cannot be produced by it — they are residue. They are mostly
Arabic transliterations carrying a leading hamza (`'aalamin`, `'alamiin`) plus
clipped speech (`lasă-n'`, `da'`) from subtitles and web text. 225 of them
reached `merged`, and ADR-001 decided to migrate them out rather than keep an
exact-key lookup path alive to answer them.

Method, exactly `migrate_dashes.py`'s: a stored word's occurrence count is
exact whenever it was computed, so re-splitting each residue word with today's
`tokenize()` gives precisely what a fixed tokenizer would have counted from
scratch. `documents` is added across the folded rows, so it becomes a slight
upper bound where a word folds into an existing one — the same approximation
`migrate_elisions.py` and `migrate_dashes.py` accept.

Token accounting. Fixing `source_counts` is the whole migration, but the totals
are *not* all unchanged, and the difference is the part worth reading:

    "'aci"      -> ['aci']              delta  0   (renamed, folded: the 2,111 common case)
    "'a-dracu"  -> ['a', 'dracu']       delta +occ (one token becomes two)
    "'"         -> []                   delta -occ (a lone apostrophe: never a token)

Conservation therefore holds as an exact identity, which the script asserts:

    sum(occurrences) after  ==  sum(occurrences) before + sum((len(parts) - 1) * occ)

and, for the dominant rename class, as literal equality. `sources.total_tokens`
is moved by the same delta, so `total_tokens == SUM(occurrences)` still holds
(asserted before and after) and the denominator stays honest.

Safety. The 4 GB db is the project's only copy of the counted data, so this runs
in ONE transaction (all sources or none) and writes a rollback snapshot first
to `data/checkpoints/pre_apostrophe_migration.db`: the rows it deletes, the
pre-migration values of every row it folds into (the existing-row case needs the
old value back, which a subtraction would otherwise have to reconstruct), and
the old `sources.total_tokens`. Everything else in the db is derived from
`source_counts`. Rollback = delete the target rows, re-insert `*_before`, restore
totals. The script refuses to overwrite an existing snapshot.

Idempotent: with no apostrophe-edged rows left, it says so and writes nothing.

Usage:
    python build/migrate_apostrophes.py --dry-run
    python build/migrate_apostrophes.py

Then:
    python build/compute_zipf.py && python build/merge.py \\
      && python build/build_lemma_layer.py && python build/build_package.py
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrodfreq.db import DEFAULT_DB_PATH, connect
from wrodfreq.tokenizer import tokenize

SNAPSHOT_PATH = Path("data/checkpoints/pre_apostrophe_migration.db")
EDGED = "(word LIKE '''%' OR word LIKE '%''')"


def repair(word: str) -> list[str]:
    """The token(s) today's tokenizer emits for this stored word, taken to a
    fixed point so that every output satisfies `tokenize(t) == [t]`.

    One pass is not always enough: `da'-a'-a'` tokenizes to `["da'-a'", "a"]`,
    and `da'-a'` is itself not a fixed point (`tokenize` gives `da'-a`) because
    an elision split can leave an apostrophe at a token's edge. Re-tokenizing
    until stable is what keeps ADR-001's idempotence claim true of the table.
    """
    out: list[str] = []
    for t in tokenize(word):
        parts = [t] if t == word else repair(t)
        out.extend(parts)
    return out


def totals(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """{source_id: (SUM(occurrences), sources.total_tokens)}."""
    return {
        s: (conn.execute("SELECT COALESCE(SUM(occurrences),0) FROM source_counts "
                         "WHERE source_id=?", (s,)).fetchone()[0], t)
        for s, t in conn.execute("SELECT source_id, total_tokens FROM sources").fetchall()
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    ap.add_argument("--snapshot", type=Path, default=SNAPSHOT_PATH)
    ap.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    args = ap.parse_args()

    conn = connect(args.db)
    conn.isolation_level = None  # explicit BEGIN/COMMIT: one transaction, all or nothing

    rows = conn.execute(
        f"SELECT word, source_id, occurrences, documents FROM source_counts WHERE {EDGED}"
    ).fetchall()
    work = [(w, s, o, d) for w, s, o, d in rows if repair(w) != [w]]
    if not work:
        print("Nothing to do: no apostrophe-edged rows in source_counts. "
              "Already migrated (no-op).")
        conn.close()
        return 0

    before = totals(conn)
    for s, (occ, tot) in before.items():
        assert occ == tot, f"{s}: SUM(occurrences) {occ:,} != total_tokens {tot:,} before"

    # Plan: per source, fold deltas by destination word.
    deltas: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    token_delta: dict[str, int] = defaultdict(int)
    renamed_occ: dict[str, int] = defaultdict(int)
    n_rows: dict[str, int] = defaultdict(int)
    n_vanish: dict[str, int] = defaultdict(int)
    n_split: dict[str, int] = defaultdict(int)
    for w, s, occ, docs in work:
        parts = repair(w)
        n_rows[s] += 1
        token_delta[s] += (len(parts) - 1) * occ
        if not parts:
            n_vanish[s] += 1
        elif len(parts) > 1:
            n_split[s] += 1
        else:
            renamed_occ[s] += occ
        for p in parts:
            e = deltas[s][p]
            e[0] += occ
            e[1] += docs

    print(f"{len(work):,} apostrophe-edged rows across {len(n_rows)} sources")
    for s in sorted(n_rows):
        print(f"  [{s}] {n_rows[s]:,} rows: {n_rows[s]-n_vanish[s]-n_split[s]:,} renamed/folded, "
              f"{n_split[s]:,} split in two, {n_vanish[s]:,} vanish; "
              f"token delta {token_delta[s]:+,}")
    if args.dry_run:
        print("(dry run — no changes made)")
        conn.close()
        return 0

    if args.snapshot.exists():
        sys.exit(f"Refusing to overwrite existing snapshot {args.snapshot}; "
                 f"move it aside deliberately first.")
    args.snapshot.parent.mkdir(parents=True, exist_ok=True)
    snap = sqlite3.connect(args.snapshot)
    snap.executescript("""
        CREATE TABLE source_counts_before (word TEXT, source_id TEXT, occurrences INTEGER, documents INTEGER);
        CREATE TABLE target_rows_before   (word TEXT, source_id TEXT, occurrences INTEGER, documents INTEGER);
        CREATE TABLE sources_before       (source_id TEXT, total_tokens INTEGER);
    """)
    snap.executemany("INSERT INTO source_counts_before VALUES (?,?,?,?)", work)
    targets = []
    for s, d in deltas.items():
        for p in d:
            r = conn.execute("SELECT word, source_id, occurrences, documents FROM source_counts "
                             "WHERE word=? AND source_id=?", (p, s)).fetchone()
            if r:
                targets.append(r)
    snap.executemany("INSERT INTO target_rows_before VALUES (?,?,?,?)", targets)
    snap.executemany("INSERT INTO sources_before VALUES (?,?)",
                     [(s, tot) for s, (_, tot) in before.items()])
    snap.commit()
    snap.close()
    print(f"Snapshot: {args.snapshot} ({len(work):,} rows, {len(targets):,} fold targets)")

    conn.execute("BEGIN")
    try:
        for s, d in deltas.items():
            conn.executemany(
                """INSERT INTO source_counts (word, source_id, occurrences, documents)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(word, source_id) DO UPDATE SET
                       occurrences = occurrences + excluded.occurrences,
                       documents   = documents   + excluded.documents""",
                [(p, s, o, dc) for p, (o, dc) in d.items()],
            )
        conn.executemany("DELETE FROM source_counts WHERE word=? AND source_id=?",
                         [(w, s) for w, s, _, _ in work])
        for s, delta in token_delta.items():
            conn.execute("UPDATE sources SET total_tokens = total_tokens + ? WHERE source_id=?",
                         (delta, s))
            # compute_zipf.py never deletes the row of a word that vanished; wipe
            # the slice so its next run starts clean (same trap as migrate_dashes).
            conn.execute("DELETE FROM source_zipf WHERE source_id=?", (s,))

        after = totals(conn)
        for s, (occ_b, _) in before.items():
            occ_a, tot_a = after[s]
            assert occ_a == occ_b + token_delta.get(s, 0), \
                f"{s}: conservation identity violated ({occ_b:,} -> {occ_a:,}, delta {token_delta.get(s,0):+,})"
            assert occ_a == tot_a, f"{s}: SUM(occurrences) != total_tokens after"
        # Not "no edged rows at all": `da'-a'` style tokens stay edged-looking
        # only if they are fixed points. Assert no row remains that would change.
        left = [w for (w,) in conn.execute(f"SELECT word FROM source_counts WHERE {EDGED}")
                if repair(w) != [w]]
        assert not left, f"{len(left)} unreproducible apostrophe rows remain: {left[:5]}"
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    print("\nConservation (occurrences per source):")
    for s in sorted(before):
        print(f"  {s:5} {before[s][0]:>16,} -> {after[s][0]:>16,}  "
              f"(identity delta {token_delta.get(s,0):+,}; "
              f"renamed rows carried {renamed_occ.get(s,0):,} occurrences unchanged)")
    print("Next: compute_zipf.py, merge.py, build_lemma_layer.py, build_package.py")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
