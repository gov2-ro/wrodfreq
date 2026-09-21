"""End-to-end test of pipeline stages 2-5 over a synthetic database.

This exists because `build/validate.py` cannot run in CI. Its checks 1, 3 and 5
read `data/wrodfreq.db` — a 3.7 GB artifact of multi-day ingest runs, gitignored
by the repo's "no .db in git, ever" rule — and checks 2 and 4 cross-reference a
local oțios checkout. So the *logic* of the pipeline is exercised here instead,
on a database small enough to build in memory, and this file runs everywhere
the test suite does.

The headline assertion is spec §11.6's, which §14 calls "a testable property and
the cheapest bug detector you have": re-running a stage on unchanged input must
be byte-identical. That caught nothing today, which is the point — it is the
check that notices when someone swaps a `set` for a `dict` iteration order or a
`statistics.mean` for a naive float sum.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_{name}", BUILD / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


compute_zipf = _load("compute_zipf")
merge = _load("merge")
build_package = _load("build_package")

from wrodfreq.db import connect, eligible_sources
from wrodfreq.zipf import MIN_OCC_PER_SOURCE, merge_zipf

SOURCES = ["eu", "news", "subs", "web", "wiki"]

# Per-source token totals that differ by orders of magnitude, so the derived
# per-source floors genuinely differ — the property CLAUDE.md insists on
# ("five occurrences in 80M tokens is a different claim from five in 17B").
TOTALS = {"eu": 84_000_000, "news": 2_200_000_000, "subs": 2_000_000_000,
          "web": 23_000_000_000, "wiki": 110_000_000}

# word -> {source: occurrences}. Chosen so the fixture exercises every merge
# branch: 5 reliable (trimmed), 3-4 reliable (plain mean), 1 reliable, and a
# word that abstains everywhere and must therefore be absent from `merged`.
COUNTS = {
    # a function word: frequent everywhere, must land in the 6-8 Zipf band
    "de":      {"eu": 5_000_000, "news": 120_000_000, "subs": 90_000_000,
                "web": 1_200_000_000, "wiki": 6_000_000},
    "și":      {"eu": 3_000_000, "news": 80_000_000, "subs": 50_000_000,
                "web": 800_000_000, "wiki": 4_000_000},
    # ordinary content word, all five reliable -> trimmed mean
    "casă":    {"eu": 8_000, "news": 300_000, "subs": 400_000,
                "web": 3_000_000, "wiki": 12_000},
    # rarer synonym, must stay below `casă` (the monotone property check 3 tests)
    "locuință": {"eu": 3_000, "news": 20_000, "subs": 6_000,
                 "web": 150_000, "wiki": 2_000},
    # reliable in only three sources: abstains (not zeroes) in the other two
    "birjă":   {"eu": 1, "news": 400, "subs": 900, "web": 4_000, "wiki": 2},
    # below MIN_OCC_PER_SOURCE everywhere -> omitted from `merged` entirely
    "hapaxul": {"eu": 1, "news": 2, "subs": 1, "web": 4, "wiki": 1},
}


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "fixture.db")
    for src in SOURCES:
        conn.execute(
            """INSERT INTO sources (source_id, display_name, register, period,
                                    total_tokens, total_docs, status)
               VALUES (?, ?, 'web', 'contemporary', ?, 1000, 'completed')""",
            (src, src.title(), TOTALS[src]),
        )
    conn.executemany(
        "INSERT INTO source_counts (word, source_id, occurrences, documents) "
        "VALUES (?, ?, ?, ?)",
        [(w, s, occ, max(1, occ // 10))
         for w, per_src in COUNTS.items() for s, occ in per_src.items()],
    )
    conn.commit()
    return conn


def _hash_query(conn: sqlite3.Connection, sql: str) -> str:
    h = hashlib.sha256()
    for row in conn.execute(sql):
        h.update(repr(row).encode())
    return h.hexdigest()


def _build_all(conn: sqlite3.Connection) -> None:
    compute_zipf.compute_all(conn)
    merge.run_merge(conn, eligible_sources(conn), set())


# ---------------------------------------------------------------------------
# Idempotence — spec §11.6, the assertion check 6 makes against the real corpus
# ---------------------------------------------------------------------------

def test_compute_zipf_is_idempotent(db):
    compute_zipf.compute_all(db)
    first = _hash_query(db, "SELECT * FROM source_zipf ORDER BY word, source_id")
    compute_zipf.compute_all(db)
    assert _hash_query(db, "SELECT * FROM source_zipf ORDER BY word, source_id") == first


def test_merge_is_idempotent(db):
    compute_zipf.compute_all(db)
    merge.run_merge(db, eligible_sources(db), set())
    first = _hash_query(db, "SELECT * FROM merged ORDER BY word")
    merge.run_merge(db, eligible_sources(db), set())
    assert _hash_query(db, "SELECT * FROM merged ORDER BY word") == first


def test_package_payload_is_idempotent(db):
    _build_all(db)
    a = build_package.build_payloads(db)
    b = build_package.build_payloads(db)
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# The merge rules CLAUDE.md calls "the rules that decide whether the table is
# right" — asserted on data where the expected answer is computable by hand.
# ---------------------------------------------------------------------------

def test_a_source_abstains_it_never_reports_zero(db):
    """Below MIN_OCC_PER_SOURCE a source contributes NULL/0, not a zipf of 0."""
    compute_zipf.compute_all(db)
    row = db.execute(
        "SELECT zipf, reliable FROM source_zipf WHERE word='birjă' AND source_id='eu'"
    ).fetchone()
    assert row == (None, 0), "an abstention must be NULL, never a zero zipf"


def test_word_below_the_floor_everywhere_is_omitted_not_zeroed(db):
    _build_all(db)
    assert db.execute("SELECT 1 FROM merged WHERE word='hapaxul'").fetchone() is None


def test_floors_are_derived_per_source_never_hardcoded(db):
    """CLAUDE.md: "Never hardcode a Zipf number — a pinned constant breaks the
    day a source lands." The floor must be a pure function of that source's own
    total_tokens, which is checked here exactly rather than by proxy. (Two
    sources of equal size sharing a floor is correct, not a bug — an earlier
    version of this test asserted otherwise and was wrong.)"""
    from wrodfreq.zipf import source_zipf_floor
    compute_zipf.compute_all(db)
    floors = dict(db.execute("SELECT source_id, zipf_floor FROM sources"))
    for src, floor in floors.items():
        assert floor == pytest.approx(source_zipf_floor(TOTALS[src]))
    assert floors["web"] < floors["eu"], (
        "a larger corpus must have a lower floor — five occurrences in 23B "
        "tokens is a weaker claim than five in 84M")


def test_trimmed_mean_engages_at_five_and_drops_the_extremes(db):
    """>=5 reliable sources: drop max and min, mean the rest (spec §8.2)."""
    compute_zipf.compute_all(db)
    merge.run_merge(db, eligible_sources(db), set())
    per_source = [z for (z,) in db.execute(
        "SELECT zipf FROM source_zipf WHERE word='casă' AND reliable=1")]
    assert len(per_source) == 5
    merged = db.execute("SELECT zipf FROM merged WHERE word='casă'").fetchone()[0]
    assert merged == pytest.approx(merge_zipf(per_source))
    # and it is genuinely the trimmed value, not the plain mean
    assert merged != pytest.approx(sum(per_source) / 5)


def test_fewer_than_five_reliable_uses_a_plain_mean(db):
    compute_zipf.compute_all(db)
    merge.run_merge(db, eligible_sources(db), set())
    row = db.execute(
        "SELECT zipf, n_reliable, n_attesting FROM merged WHERE word='birjă'").fetchone()
    zipf, n_reliable, n_attesting = row
    assert n_reliable < 5 and n_attesting == 5, "attests in all five, reliable in fewer"
    reliable = [z for (z,) in db.execute(
        "SELECT zipf FROM source_zipf WHERE word='birjă' AND reliable=1")]
    assert zipf == pytest.approx(sum(reliable) / len(reliable))


def test_function_words_land_in_the_zipf_band_that_proves_the_denominator(db):
    """Spec §11.1 / §3.2 — the check CLAUDE.md calls the one that catches the
    worst bug in the spec. A broken denominator shows up here first."""
    _build_all(db)
    for word in ("de", "și"):
        zipf = db.execute("SELECT zipf FROM merged WHERE word=?", (word,)).fetchone()[0]
        assert 6.0 <= zipf <= 8.0, f"{word} at {zipf:.2f} — denominator is wrong"


def test_monotone_ordering_survives_the_merge(db):
    _build_all(db)
    z = dict(db.execute("SELECT word, zipf FROM merged"))
    assert z["casă"] > z["locuință"]
    assert z["de"] > z["casă"]


def test_merge_never_weights_by_source_size(db):
    """CulturaX is ~200x the next source; size-weighting would reduce the
    merge to 'the web source with extra steps' (CLAUDE.md)."""
    _build_all(db)
    merged = db.execute("SELECT zipf FROM merged WHERE word='casă'").fetchone()[0]
    web_only = db.execute(
        "SELECT zipf FROM source_zipf WHERE word='casă' AND source_id='web'").fetchone()[0]
    assert merged != pytest.approx(web_only, abs=0.01), (
        "merged tracking the largest source exactly suggests size-weighting")
