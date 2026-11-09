"""Exact derived stages and the matched-panel family_ratio (brief B7, findings A06, A07).

Stage 2 must replace a source's derived rows with exactly what the current counts
give; stages 3-4 must refuse stale input; and `family_ratio` must compare the loose
and disambiguated lemma totals over one and the same source panel.
"""

from __future__ import annotations

import importlib.util
import math
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_{name}_ds", BUILD / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


compute_zipf = _load("compute_zipf")
merge = _load("merge")
lemma_layer = _load("build_lemma_layer")
import ingest_state  # noqa: E402
import integrity  # noqa: E402
from wrodfreq.db import connect, eligible_sources  # noqa: E402


def make_db(tmp_path: Path, counts: dict[str, dict[str, int]], status=None) -> sqlite3.Connection:
    """Sources whose totals are exactly the sum of their counts."""
    conn = connect(tmp_path / "d.db")
    for sid, words in counts.items():
        conn.execute("INSERT INTO sources (source_id, display_name, register, period, "
                     "total_tokens, total_docs, status) VALUES (?, ?, 'web', 'contemporary', "
                     "?, 10, ?)", (sid, sid, sum(words.values()),
                                   (status or {}).get(sid, "completed")))
        conn.executemany("INSERT INTO source_counts VALUES (?, ?, ?, 1)",
                         [(w, sid, n) for w, n in words.items()])
    conn.commit()
    return conn


def build(conn) -> None:
    compute_zipf.compute_all(conn)
    merge.run_merge(conn, eligible_sources(conn), set())


def rows(conn, sql):
    return conn.execute(sql).fetchall()


# ---------------------------------------------------------------------------
# Stage 2 exact replacement (A07)
# ---------------------------------------------------------------------------

def test_removed_word_disappears_from_both_derived_tables(tmp_path):
    conn = make_db(tmp_path, {"a": {"ghost": 10, "de": 990}, "b": {"de": 500, "ghost": 7}})
    build(conn)
    assert rows(conn, "SELECT word FROM merged WHERE word='ghost'") == [("ghost",)]
    # A count change done properly: counts, total and provenance together.
    conn.execute("DELETE FROM source_counts WHERE word='ghost'")
    conn.execute("UPDATE sources SET total_tokens = total_tokens - 10 WHERE source_id='a'")
    conn.execute("UPDATE sources SET total_tokens = total_tokens - 7 WHERE source_id='b'")
    ingest_state.record_count_change(conn, "a", "test")
    conn.commit()
    build(conn)
    assert rows(conn, "SELECT * FROM source_zipf WHERE word='ghost'") == []
    assert rows(conn, "SELECT * FROM merged WHERE word='ghost'") == []
    assert integrity.check_database(conn).ok


def test_single_source_recompute_preserves_other_sources(tmp_path):
    conn = make_db(tmp_path, {"a": {"de": 100, "la": 50}, "b": {"de": 70}})
    build(conn)
    b_rows = rows(conn, "SELECT * FROM source_zipf WHERE source_id='b'")
    conn.execute("DELETE FROM source_counts WHERE word='la'")
    conn.execute("UPDATE sources SET total_tokens = 100 WHERE source_id='a'")
    conn.commit()
    compute_zipf.compute_all(conn, "a")
    assert rows(conn, "SELECT word FROM source_zipf WHERE source_id='a'") == [("de",)]
    assert rows(conn, "SELECT * FROM source_zipf WHERE source_id='b'") == b_rows


def test_emptied_source_clears_its_rows_and_floor(tmp_path):
    conn = make_db(tmp_path, {"a": {"de": 100}, "b": {"de": 70}})
    build(conn)
    conn.execute("DELETE FROM source_counts WHERE source_id='b'")
    conn.execute("UPDATE sources SET total_tokens = 0 WHERE source_id='b'")
    conn.commit()
    compute_zipf.compute_all(conn)
    assert rows(conn, "SELECT * FROM source_zipf WHERE source_id='b'") == []
    assert rows(conn, "SELECT zipf_floor FROM sources WHERE source_id='b'") == [(None,)]


def test_counts_without_tokens_are_refused(tmp_path):
    conn = make_db(tmp_path, {"a": {"de": 100}})
    conn.execute("UPDATE sources SET total_tokens = 0")
    conn.commit()
    with pytest.raises(compute_zipf.StageRefused):
        compute_zipf.compute_all(conn)


def test_non_conserving_counts_are_refused(tmp_path):
    """The contaminated social: SUM(occurrences) > total_tokens."""
    conn = make_db(tmp_path, {"social": {"de": 100}})
    conn.execute("UPDATE source_counts SET occurrences = 112")
    conn.commit()
    with pytest.raises(compute_zipf.StageRefused, match="social"):
        compute_zipf.compute_all(conn)


def test_failure_mid_rebuild_keeps_the_old_complete_set(tmp_path, monkeypatch):
    conn = make_db(tmp_path, {"a": {f"w{i}": 5 + i for i in range(50)}})
    compute_zipf.compute_all(conn)
    before = rows(conn, "SELECT * FROM source_zipf ORDER BY word")
    stages = rows(conn, "SELECT * FROM stage_state")
    calls = {"n": 0}
    real = compute_zipf._row

    def flaky(*a):
        calls["n"] += 1
        if calls["n"] == 20:
            raise RuntimeError("disk full")
        return real(*a)
    monkeypatch.setattr(compute_zipf, "_row", flaky)
    monkeypatch.setattr(compute_zipf, "BATCH", 7)
    for only in (None, "a"):
        calls["n"] = 0
        with pytest.raises(RuntimeError):
            compute_zipf.compute_all(conn, only)
        assert rows(conn, "SELECT * FROM source_zipf ORDER BY word") == before
        assert rows(conn, "SELECT * FROM stage_state") == stages


def test_unknown_source_is_an_error(tmp_path):
    conn = make_db(tmp_path, {"a": {"de": 100}})
    with pytest.raises(compute_zipf.StageRefused, match="unknown"):
        compute_zipf.compute_all(conn, "nope")
    conn.close()
    out = subprocess.run([sys.executable, str(BUILD / "compute_zipf.py"), "--db",
                          str(tmp_path / "d.db"), "--source", "nope"], capture_output=True)
    assert out.returncode == 2


def test_missing_db_is_not_created(tmp_path):
    absent = tmp_path / "absent.db"
    dex = tmp_path / "dex.db"
    dex.write_bytes(b"")
    for script, extra in (("compute_zipf", []), ("merge", ["--no-dex"]),
                          ("build_lemma_layer", ["--dex-db", str(dex)])):
        out = subprocess.run([sys.executable, str(BUILD / f"{script}.py"), "--db", str(absent),
                              *extra], capture_output=True)
        assert out.returncode == 2, script
        assert not absent.exists(), script


def test_incomplete_source_never_reaches_merged(tmp_path):
    conn = make_db(tmp_path, {"a": {"de": 100}, "b": {"de": 50, "numai": 40}},
                   status={"b": "in_progress"})
    build(conn)
    assert rows(conn, "SELECT word FROM merged WHERE word='numai'") == []
    assert rows(conn, "SELECT n_sources FROM merged") == [(1,)]


def test_merge_refuses_stale_stage_two(tmp_path):
    conn = make_db(tmp_path, {"a": {"de": 100}})
    compute_zipf.compute_all(conn)
    ingest_state.record_count_change(conn, "a", "test")    # wipes stage records
    conn.commit()
    with pytest.raises(merge.StageRefused):
        merge.run_merge(conn, eligible_sources(conn), set())


def test_merge_refuses_a_changed_total(tmp_path):
    conn = make_db(tmp_path, {"a": {"de": 100}})
    compute_zipf.compute_all(conn)
    conn.execute("UPDATE sources SET total_tokens = 101")
    conn.commit()
    with pytest.raises(merge.StageRefused):
        merge.run_merge(conn, eligible_sources(conn), set())


def test_lemma_layer_refuses_without_current_merge(tmp_path):
    conn = make_db(tmp_path, {"a": {"de": 100}})
    compute_zipf.compute_all(conn)
    with pytest.raises(lemma_layer.StageRefused):
        lemma_layer.run(conn, eligible_sources(conn), {"de": ["de"]})


# ---------------------------------------------------------------------------
# Matched-panel family_ratio (A06) — full run() fixtures
# ---------------------------------------------------------------------------

def lemma_rows(conn) -> dict:
    return {r[0]: r[1:] for r in conn.execute("SELECT * FROM lemma_zipf")}


def run_lemma(conn, form_lemma):
    build(conn)
    lemma_layer.run(conn, eligible_sources(conn), form_lemma)
    return lemma_rows(conn)


def test_brief_fixture_ratio_is_two_and_lemma_zipf_unchanged(tmp_path):
    """10 shared occurrences in 1,000 tokens, 6 in 1e9, split equally between two
    claimants: disambiguated 5 and 3. The old independent-panel ratio was ~0.001549."""
    conn = make_db(tmp_path, {"small": {"shared": 10, "filler": 990},
                              "large": {"shared": 6, "filler": 1_000_000_000 - 6}})
    got = run_lemma(conn, {"shared": ["alpha", "beta"]})
    for lemma in ("alpha", "beta"):
        zipf, n_forms, headword, ratio = got[lemma]
        assert ratio == pytest.approx(2.0)
        # Lemma Zipf: only `small` clears the floor with 5 disambiguated occurrences.
        assert zipf == pytest.approx(math.log10(5 / 1000 * 1e9))
        assert n_forms == 1 and headword is None
    assert integrity.check_database(conn).ok


def test_unambiguous_lemma_has_ratio_one(tmp_path):
    conn = make_db(tmp_path, {"a": {"casa": 40, "casei": 10, "x": 950},
                              "b": {"casa": 300, "x": 9700}})
    got = run_lemma(conn, {"casa": ["casă"], "casei": ["casă"]})
    assert got["casă"][3] == pytest.approx(1.0)
    assert got["casă"][1] == 2


def test_unequal_priors_split_by_headword_frequency(tmp_path):
    """`mare` is claimed by `mare` (seen 80x as a headword) and `mar` (seen 0x)."""
    conn = make_db(tmp_path, {"a": {"mare": 80, "x": 920}})
    got = run_lemma(conn, {"mare": ["mare", "mar"]})
    # weights: mare 80+1, mar 0+1 -> mar gets 80/82 of nothing reliable? 80*1/82 < 5
    assert "mar" not in got
    zipf, _, headword, ratio = got["mare"]
    assert zipf == pytest.approx(math.log10(80 * 81 / 82 / 1000 * 1e9))
    assert ratio == pytest.approx(82 / 81)
    assert headword == pytest.approx(math.log10(80 / 1000 * 1e9))


@pytest.mark.parametrize("n_sources", [5, 6])
def test_trimmed_panels_keep_ratio_two_for_an_even_split(tmp_path, n_sources):
    counts = {f"s{i}": {"forma": 10 * (i + 1) ** 3, "x": 10 ** (i + 4)} for i in range(n_sources)}
    conn = make_db(tmp_path, counts)
    got = run_lemma(conn, {"forma": ["unu", "doi"]})
    for lemma in ("unu", "doi"):
        assert got[lemma][3] == pytest.approx(2.0)


def test_extra_loose_only_sources_cannot_pull_the_ratio_below_one(tmp_path):
    """The A06 mechanism: a large source reliable only for the loose total."""
    conn = make_db(tmp_path, {"small": {"f": 12, "x": 988},
                              "huge": {"f": 8, "x": 10 ** 9},
                              "mid": {"f": 30, "x": 10 ** 6}})
    got = run_lemma(conn, {"f": ["l1", "l2"]})
    for lemma, (_, _, _, ratio) in got.items():
        assert ratio >= 1.0
    assert integrity.check_database(conn).ok


def test_integrity_flags_a_ratio_below_one(tmp_path):
    conn = make_db(tmp_path, {"a": {"casa": 40, "x": 960}})
    run_lemma(conn, {"casa": ["casă"]})
    conn.execute("UPDATE lemma_zipf SET family_ratio = 0.0329")
    conn.commit()
    report = integrity.check_database(conn)
    assert "lemma_family_ratio_below_one" in {i["code"] for i in report.issues.values()}
