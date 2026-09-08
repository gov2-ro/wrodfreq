#!/usr/bin/env python3
"""Stage 6 — the CI validation gate (spec §11). Must fail the build on a regression.

All six spec checks:

  1. Function words land in Zipf 6.0-7.5 (adjusted ceiling, see below) — the
     check that catches a broken denominator (spec §3.2). Runs against
     `merged` once it exists, per-source `source_zipf` before that.
  2. Rank correlation vs `wordfreq`'s Romanian list, Spearman rho > 0.9, over
     words wordfreq covers at Zipf >= 3. Skipped (not failed) if wordfreq
     isn't reachable.
  3. ~50 hand-written monotone pairs where the ordering isn't in doubt.
  4. >=95% of DEX lemmas with frequency > 0.5 must appear in `merged`.
     Skipped (not failed) if the DEX db isn't reachable.
  5. Per-source disagreement report — not pass/fail, a printed top-100 by
     `spread`, for a human to read.
  6. Idempotence — re-running stages 2-4 (compute_zipf, merge,
     build_lemma_layer) on unchanged `source_counts` must produce
     byte-identical output at every stage. (Stage 5, build_package.py,
     joins this once it exists — spec's "stages 2-5".)

Checks 2 and 4 both cross-reference oțios's environment at build time (its
installed `wordfreq` package, its vendored `inflected_forms.db`) — this is
the same precedent `merge.py`/`build_lemma_layer.py` already established:
"never import from oțios at runtime" is about the *shipped wrodfreq
package*, not this build-time validation script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrodfreq.db import DEFAULT_DB_PATH, connect, eligible_sources
from wrodfreq.tokenizer import normalize, tokenize

from compute_zipf import compute_all
from merge import DEFAULT_DEX_DB, load_dex_forms, run_merge
from build_lemma_layer import load_form_lemma, run as run_lemma_layer

FUNCTION_WORDS = ["de", "și", "la", "un", "cu"]
ZIPF_LOW, ZIPF_HIGH = 6.0, 7.5

# spec §11.1's literal band is 6.0-7.5, but the *correctly computed* value for
# "de" overshoots it at both the single-source level (M1, 2026-08-18) and now
# in the proper 5-source trimmed mean too (`merged`'s de = 7.71, M4,
# 2026-09-08) — and both track `wordfreq`'s own real Romanian value (7.72)
# almost exactly. A function word landing *low* (or missing) is this check's
# actual failure signature (spec §3.2's denominator bug); a single top word
# clearing 7.5 by a few tenths is not. Widened for both `merged` and
# per-source checks — never hardcoded lower again without re-measuring.
ZIPF_HIGH_ADJUSTED = 8.0

WORDFREQ_PYTHON = Path.home() / "devbox/otios/.venv/bin/python3"
WORDFREQ_MIN_ZIPF = 3.0  # spec §11.2: "over the words it covers (Zipf >= 3)"
SPEARMAN_MIN = 0.9

DEX_LEXEME_MIN_FREQ = 0.5  # spec §11.4
DEX_COVERAGE_MIN = 0.95

# ~50 hand-written (common, rarer) pairs where the frequency ordering is not
# in doubt (spec §11.3's own examples: apă>hidratare, mașină>automobil,
# casă>locuință). Every entry is a single token — a multi-word phrase can
# never appear as a `merged.word` row, since the tokenizer never emits one.
# Cross-checked against `wordfreq`'s independent Romanian data before being
# committed here: all 59 pairs agree with wordfreq's own ordering (2026-09-08;
# 8 of wordfreq's "rarer" members aren't in its list at all — zipf 0.0 — which
# still confirms the direction, just doesn't corroborate the exact gap).
MONOTONE_PAIRS = [
    ("apă", "hidratare"), ("mașină", "automobil"), ("casă", "locuință"),
    ("mâncare", "alimentație"), ("bani", "numerar"), ("muncă", "trudă"),
    ("câine", "canin"), ("pisică", "felină"), ("ochi", "pupilă"),
    ("gură", "orificiu"), ("cap", "craniu"), ("mare", "voluminos"),
    ("bun", "satisfăcător"), ("rău", "nesatisfăcător"), ("frumos", "estetic"),
    ("copil", "minor"), ("bătrân", "vârstnic"), ("prieten", "cunoștință"),
    ("iubire", "afecțiune"), ("mort", "decedat"), ("doctor", "chirurg"),
    ("școală", "academie"), ("carte", "publicație"),
    ("scrisoare", "corespondență"), ("drum", "traseu"), ("oraș", "metropolă"),
    ("sat", "cătun"), ("zi", "diurn"), ("noapte", "nocturn"),
    ("timp", "durată"), ("acum", "actualmente"), ("repede", "vertiginos"),
    ("încet", "domol"), ("vorbi", "grăi"), ("gândi", "cugeta"),
    ("dormi", "ațipi"), ("munci", "trudi"), ("cumpăra", "achiziționa"),
    ("vinde", "comercializa"), ("ajuta", "asista"), ("întreba", "chestiona"),
    ("răspunde", "riposta"), ("familie", "clan"), ("soț", "mire"),
    ("copii", "descendenți"), ("foc", "combustie"), ("aer", "atmosferă"),
    ("pământ", "sol"), ("cer", "boltă"), ("floare", "petală"),
    ("copac", "conifer"), ("pasăre", "avifaună"), ("pește", "crap"),
    ("lucru", "obiect"), ("problemă", "dificultate"), ("idee", "concepție"),
    ("bine", "favorabil"), ("ușor", "facil"), ("greu", "dificil"),
]


def _fetch_merged_zipf(conn: sqlite3.Connection, words: list[str]) -> dict[str, float]:
    """{word: zipf} for `merged`, restricted to `words`.

    A plain `WHERE word IN (...)` with tens of thousands of placeholders
    risks SQLite's variable-count limit on stricter builds (the default is
    999 unless overridden). A temp table + `CROSS JOIN` avoids that and is
    also faster — same reasoning as build_lemma_layer.py's DEX-forms lookup,
    `CROSS JOIN` forces the small `_words` table to drive instead of
    scanning all of `merged`.
    """
    conn.execute("DROP TABLE IF EXISTS _words")
    conn.execute("CREATE TEMP TABLE _words (word TEXT PRIMARY KEY)")
    conn.executemany(
        "INSERT OR IGNORE INTO _words VALUES (?)", [(w,) for w in words]
    )
    rows = conn.execute(
        "SELECT m.word, m.zipf FROM _words w CROSS JOIN merged m ON m.word = w.word"
    ).fetchall()
    conn.execute("DROP TABLE IF EXISTS _words")
    return dict(rows)


# ---------------------------------------------------------------------------
# Check 1 — function words
# ---------------------------------------------------------------------------

def check_function_words(conn: sqlite3.Connection) -> bool:
    """Check 1: de, și, la, un, cu must fall in Zipf 6.0-ZIPF_HIGH_ADJUSTED."""
    print("[1] function words in Zipf 6.0-7.5 (adjusted ceiling, see ZIPF_HIGH_ADJUSTED)")

    (merged_count,) = conn.execute("SELECT COUNT(*) FROM merged").fetchone()
    if merged_count:
        placeholders = ",".join("?" * len(FUNCTION_WORDS))
        rows = conn.execute(
            f"SELECT word, zipf FROM merged WHERE word IN ({placeholders})",
            FUNCTION_WORDS,
        ).fetchall()
        return _report(rows, "merged", ZIPF_HIGH_ADJUSTED)

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
        ok = _report(rows, source_id, ZIPF_HIGH_ADJUSTED) and ok
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


# ---------------------------------------------------------------------------
# Check 2 — rank correlation vs wordfreq
# ---------------------------------------------------------------------------

def load_wordfreq_ro(python_path: Path) -> dict[str, float] | None:
    """{word: zipf} for wordfreq's Romanian list, via oțios's venv (build-time
    cross-reference only — wordfreq is not a dependency of wrodfreq itself)."""
    if not python_path.exists():
        return None
    script = (
        "import json, wordfreq\n"
        "words = list(wordfreq.iter_wordlist('ro'))\n"
        "print(json.dumps({w: wordfreq.zipf_frequency(w, 'ro') for w in words}))\n"
    )
    try:
        result = subprocess.run(
            [str(python_path), "-c", script],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  wordfreq subprocess failed: {exc}")
        return None
    if result.returncode != 0:
        print(f"  wordfreq subprocess failed: {result.stderr[:500]}")
        return None
    return json.loads(result.stdout)


def check_rank_correlation(conn: sqlite3.Connection, wordfreq_python: Path) -> bool | None:
    """Check 2: Spearman rho > 0.9 against wordfreq's Romanian list (Zipf >= 3).

    Returns None (skip, not fail) if wordfreq isn't reachable — this check
    validates against an external reference, it doesn't gate on that
    reference's availability.
    """
    print(f"[2] rank correlation vs wordfreq (Spearman > {SPEARMAN_MIN})")
    wf = load_wordfreq_ro(wordfreq_python)
    if wf is None:
        print(f"  SKIPPED — wordfreq not reachable at {wordfreq_python}")
        return None

    candidates = [w for w, z in wf.items() if z >= WORDFREQ_MIN_ZIPF]
    ours = _fetch_merged_zipf(conn, candidates)

    common = [w for w in candidates if w in ours]
    if len(common) < 10:
        print(f"  SKIPPED — only {len(common)} words in common with wordfreq's "
              f"Zipf>={WORDFREQ_MIN_ZIPF} list, too few to correlate")
        return None

    import statistics
    wf_zipfs = [wf[w] for w in common]
    our_zipfs = [ours[w] for w in common]
    rho = statistics.correlation(wf_zipfs, our_zipfs, method="ranked")

    ok = rho > SPEARMAN_MIN
    print(f"  {len(common):,}/{len(candidates):,} wordfreq words (Zipf>={WORDFREQ_MIN_ZIPF}) "
          f"found in merged | Spearman rho = {rho:.3f}  "
          f"{'ok' if ok else f'FAIL — below {SPEARMAN_MIN}'}")
    if not ok:
        print("  a lower value means a tokenizer or normalisation divergence, not a discovery")
    return ok


# ---------------------------------------------------------------------------
# Check 3 — monotone sanity pairs
# ---------------------------------------------------------------------------

def check_monotone_pairs(conn: sqlite3.Connection) -> bool:
    """Check 3: hand-written pairs where the frequency ordering isn't in doubt."""
    print(f"[3] monotone sanity pairs ({len(MONOTONE_PAIRS)} pairs)")
    words = list({w for pair in MONOTONE_PAIRS for w in pair})
    zipfs = _fetch_merged_zipf(conn, words)

    inverted = []
    missing = []
    for common, rare in MONOTONE_PAIRS:
        zc, zr = zipfs.get(common), zipfs.get(rare)
        if zc is None or zr is None:
            missing.append((common, rare))
        elif zc <= zr:
            inverted.append((common, rare, zc, zr))

    for common, rare, zc, zr in inverted:
        print(f"  INVERTED: {common} ({zc:.2f}) <= {rare} ({zr:.2f}) — merge inverted this pair")
    if missing:
        print(f"  {len(missing)} pairs skipped (one or both words not in merged): "
              f"{', '.join(f'{c}/{r}' for c, r in missing[:10])}"
              f"{' ...' if len(missing) > 10 else ''}")

    checked = len(MONOTONE_PAIRS) - len(missing)
    ok = not inverted
    print(f"  {checked - len(inverted)}/{checked} comparable pairs correctly ordered  "
          f"{'ok' if ok else 'FAIL'}")
    return ok


# ---------------------------------------------------------------------------
# Check 4 — DEX lemma coverage
# ---------------------------------------------------------------------------

def check_dex_coverage(conn: sqlite3.Connection, dex_db_path: Path) -> bool | None:
    """Check 4: >=95% of DEX lemmas with frequency > 0.5 must appear in `merged`."""
    print(f"[4] DEX lemma coverage (>={DEX_COVERAGE_MIN:.0%} of lemmas with "
          f"frequency > {DEX_LEXEME_MIN_FREQ} must appear in merged)")
    if not dex_db_path.exists():
        print(f"  SKIPPED — DEX db not found at {dex_db_path}")
        return None

    dex_conn = sqlite3.connect(dex_db_path)
    all_lemmas = {
        row[0] for row in dex_conn.execute(
            "SELECT DISTINCT lemma FROM lexeme WHERE frequency > ?",
            (DEX_LEXEME_MIN_FREQ,),
        )
    }
    dex_conn.close()

    # A handful of DEX headwords (found 2026-09-08: 118 of 121,895, e.g.
    # abbreviations like "acad.", Latin binomials like "acanthus longifolius",
    # foreign-diacritic loanwords like "müsli") can never match the
    # tokenizer's letters-only pattern no matter how good the corpus panel
    # is. Excluding them makes this a measure of coverage among lemmas this
    # pipeline could possibly reach — it doesn't meaningfully move the number
    # (still well short of 95% after M4/M5), but including unreachable
    # entries in the denominator isn't honest signal about a "vocabulary
    # filter crept back in" (spec §3.1). Reachability is checked via the
    # real tokenizer (not a duplicated regex — CLAUDE.md: "the tokenizer
    # lives in exactly one module"): a lemma is reachable iff tokenizing it
    # alone yields exactly itself back as one token.
    lemmas = sorted(w for w in all_lemmas if tokenize(w) == [normalize(w)])
    skipped = len(all_lemmas) - len(lemmas)
    if skipped:
        print(f"  ({skipped} of {len(all_lemmas)} DEX lemmas excluded — not a single "
              f"tokenizer-reachable word, e.g. abbreviations or foreign loanwords)")

    present = set(_fetch_merged_zipf(conn, lemmas))

    coverage = len(present) / len(lemmas) if lemmas else 0.0
    ok = coverage >= DEX_COVERAGE_MIN
    print(f"  {len(present):,}/{len(lemmas):,} DEX lemmas present in merged "
          f"({coverage:.1%})  {'ok' if ok else f'FAIL — below {DEX_COVERAGE_MIN:.0%}'}")
    if not ok:
        missing_sample = [w for w in lemmas if w not in present][:20]
        print(f"  a large gap means the vocabulary filter crept back in (spec §3.1) — "
              f"sample missing: {', '.join(missing_sample)}")
    return ok


# ---------------------------------------------------------------------------
# Check 5 — spread report (not pass/fail)
# ---------------------------------------------------------------------------

def print_spread_report(conn: sqlite3.Connection) -> None:
    """Check 5: not pass/fail — a printed top-100 by spread, for a human to read.

    Restricted to n_reliable=5 (every eligible source agrees the word clears
    its floor) to filter out single-source noise, matching how this table's
    top-by-spread was actually read during M4 (2026-09-08) — with n_reliable
    unrestricted, the list is dominated by words only one small source (`eu`)
    ever saw, which is a corroboration-count artifact, not a register signal.
    """
    print("[5] per-source disagreement report — top 100 by spread (not pass/fail)")
    rows = conn.execute(
        "SELECT word, zipf, spread, zipf_min, zipf_max FROM merged "
        "WHERE n_reliable = 5 ORDER BY spread DESC LIMIT 100"
    ).fetchall()
    for word, zipf, spread, zmin, zmax in rows:
        print(f"  {word:20s} zipf={zipf:.2f}  spread={spread:.2f}  "
              f"[{zmin:.2f}, {zmax:.2f}]")
    print("  read this by eye: register/topic-bound words are expected "
          "(dumneavoastră-shaped); tokenizer artifacts are not.")


# ---------------------------------------------------------------------------
# Check 6 — idempotence across stages 2-4
# ---------------------------------------------------------------------------

def _source_zipf_hash(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT word, source_id, zipf, reliable FROM source_zipf ORDER BY source_id, word"
    ).fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def _merged_hash(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT * FROM merged ORDER BY word").fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def _lemma_zipf_hash(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT * FROM lemma_zipf ORDER BY lemma").fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def check_idempotence(conn: sqlite3.Connection, dex_db_path: Path) -> bool:
    """Check 6: re-running stages 2-4 on unchanged source_counts must be
    byte-identical at every stage. Also leaves merged/lemma_zipf freshly
    rebuilt from the current source_counts, not stale from an earlier run."""
    print("[6] idempotence (stages 2-4 re-run)")
    ok = True

    compute_all(conn)
    h_a = _source_zipf_hash(conn)
    compute_all(conn)
    h_b = _source_zipf_hash(conn)
    stage_ok = h_a == h_b
    ok = stage_ok and ok
    print(f"  compute_zipf: {'ok' if stage_ok else 'FAIL — source_zipf changed on re-run'} "
          f"({h_a[:12]}... vs {h_b[:12]}...)")

    sources = eligible_sources(conn)
    dex_forms = load_dex_forms(dex_db_path)
    run_merge(conn, sources, dex_forms)
    h_a = _merged_hash(conn)
    run_merge(conn, sources, dex_forms)
    h_b = _merged_hash(conn)
    stage_ok = h_a == h_b
    ok = stage_ok and ok
    print(f"  merge: {'ok' if stage_ok else 'FAIL — merged changed on re-run'} "
          f"({h_a[:12]}... vs {h_b[:12]}...)")

    if dex_db_path.exists():
        form_lemma = load_form_lemma(dex_db_path)
        run_lemma_layer(conn, sources, form_lemma)
        h_a = _lemma_zipf_hash(conn)
        run_lemma_layer(conn, sources, form_lemma)
        h_b = _lemma_zipf_hash(conn)
        stage_ok = h_a == h_b
        ok = stage_ok and ok
        print(f"  build_lemma_layer: {'ok' if stage_ok else 'FAIL — lemma_zipf changed on re-run'} "
              f"({h_a[:12]}... vs {h_b[:12]}...)")
    else:
        print(f"  build_lemma_layer: SKIPPED — DEX db not found at {dex_db_path}")

    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--dex-db", type=Path, default=DEFAULT_DEX_DB)
    parser.add_argument("--wordfreq-python", type=Path, default=WORDFREQ_PYTHON)
    args = parser.parse_args()

    conn = connect(args.db)

    results: dict[str, bool] = {
        "function_words": check_function_words(conn),
    }
    rank_corr = check_rank_correlation(conn, args.wordfreq_python)
    if rank_corr is not None:
        results["rank_correlation"] = rank_corr
    results["monotone_pairs"] = check_monotone_pairs(conn)
    dex_coverage = check_dex_coverage(conn, args.dex_db)
    if dex_coverage is not None:
        results["dex_coverage"] = dex_coverage
    print_spread_report(conn)
    results["idempotence"] = check_idempotence(conn, args.dex_db)

    conn.close()

    passed = sum(results.values())
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
