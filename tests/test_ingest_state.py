"""Transactional ingest progress across all six real ingesters (brief B7 A01, A03, A04, A05).

Each test runs the real `build/ingest_<source>.py` with offline fixture readers
(tests/ingest_fakes.py). The property under test: an interrupted run, resumed,
produces exactly the counts, totals and cursor of one clean run — and nothing
(a test run, a stale writer, a legacy JSON file) can add counts to a generation
it does not own.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_fakes import BUILD, SOURCES, load, make_fixtures, run  # noqa: E402

import adopt_legacy  # noqa: E402
import ingest_state  # noqa: E402
from wrodfreq.db import connect  # noqa: E402

DRIVER = Path(__file__).resolve().parent / "ingest_driver.py"


class Killed(BaseException):
    """Stands in for the process dying; not an Exception, so nothing catches it."""


@pytest.fixture
def fx(tmp_path):
    make_fixtures(tmp_path / "fx")
    return tmp_path / "fx"


@pytest.fixture(autouse=True)
def _reset_fault_hook():
    saved = ingest_state.FAULT_HOOK
    yield
    ingest_state.FAULT_HOOK = saved


def snapshot(db: Path, source: str) -> dict:
    conn = sqlite3.connect(db)
    try:
        counts = conn.execute("SELECT word, occurrences, documents FROM source_counts "
                              "WHERE source_id = ? ORDER BY word", (source,)).fetchall()
        src = conn.execute("SELECT total_tokens, total_docs, status, period_note FROM sources "
                           "WHERE source_id = ?", (source,)).fetchone()
        state = conn.execute("SELECT state, cursor FROM ingest_state WHERE source_id = ?",
                             (source,)).fetchone()
    finally:
        conn.close()
    return {"counts": counts, "source": src,
            "state": state and (state[0], json.loads(state[1]))}


def clean(source: str, fx: Path, tmp_path: Path) -> dict:
    db = tmp_path / f"clean-{source}.db"
    assert run(source, fx, ["--db", str(db)]) == 0
    return snapshot(db, source)


def kill_at(point: str, n: int):
    hits = {"n": 0}

    def hook(p):
        if p == point:
            hits["n"] += 1
            if hits["n"] == n:
                raise Killed(point)
    return hook


# ---------------------------------------------------------------------------
# The shared protocol, on every real adapter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source", SOURCES)
def test_clean_run_conserves_counts_and_completes(source, fx, tmp_path):
    snap = clean(source, fx, tmp_path)
    total = sum(occ for _, occ, _ in snap["counts"])
    assert total == snap["source"][0] > 0, "SUM(occurrences) must equal total_tokens"
    assert snap["source"][2] == "completed"
    assert snap["state"][0] == "completed"


@pytest.mark.parametrize("source", SOURCES)
def test_adapter_commits_through_the_shared_helper(source, fx, tmp_path, monkeypatch):
    calls = []
    real = ingest_state.IngestSession.commit_batch

    def spy(self, *a, **kw):
        calls.append(self.spec.source_id)
        return real(self, *a, **kw)
    monkeypatch.setattr(ingest_state.IngestSession, "commit_batch", spy)
    assert run(source, fx, ["--db", str(tmp_path / "x.db")]) == 0
    assert len(calls) >= 2 and set(calls) == {source}


@pytest.mark.parametrize("point", ["before_commit", "during_apply", "after_commit"])
@pytest.mark.parametrize("source", SOURCES)
def test_kill_at_each_boundary_then_resume_equals_clean_run(source, point, fx, tmp_path):
    expected = clean(source, fx, tmp_path)
    db = tmp_path / "killed.db"
    ingest_state.FAULT_HOOK = kill_at(point, 2)
    with pytest.raises(Killed):
        run(source, fx, ["--db", str(db)])
    ingest_state.FAULT_HOOK = lambda p: None
    assert run(source, fx, ["--resume", "--db", str(db)]) == 0
    assert snapshot(db, source) == expected


@pytest.mark.parametrize("point", ["during_apply", "after_commit"])
@pytest.mark.parametrize("source", SOURCES)
def test_subprocess_kill_then_resume_equals_clean_run(source, point, fx, tmp_path):
    expected = clean(source, fx, tmp_path)
    db = tmp_path / "proc.db"
    env = {**os.environ, "WRODFREQ_FAULT": f"{point}:2"}
    env.pop("PYTHONPATH", None)
    cmd = [sys.executable, str(DRIVER), source, str(fx), "--", "--db", str(db)]
    first = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
    assert first.returncode == ingest_state.FAULT_EXIT, first.stderr
    env.pop("WRODFREQ_FAULT")
    second = subprocess.run(cmd[:-2] + ["--resume", "--db", str(db)], env=env,
                            capture_output=True, text=True, timeout=120)
    assert second.returncode == 0, second.stderr
    assert snapshot(db, source) == expected


@pytest.mark.parametrize("source", SOURCES)
def test_fresh_run_refuses_an_existing_generation(source, fx, tmp_path):
    db = tmp_path / "x.db"
    assert run(source, fx, ["--db", str(db)]) == 0
    before = snapshot(db, source)
    assert run(source, fx, ["--db", str(db)]) == 2
    assert snapshot(db, source) == before


@pytest.mark.parametrize("source", SOURCES)
def test_completed_generation_resumes_as_a_no_op(source, fx, tmp_path):
    db = tmp_path / "x.db"
    assert run(source, fx, ["--db", str(db)]) == 0
    before = snapshot(db, source)
    assert run(source, fx, ["--resume", "--db", str(db)]) == 0
    assert snapshot(db, source) == before


# ---------------------------------------------------------------------------
# Experiment isolation (A01)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source", SOURCES)
def test_test_run_never_touches_the_canonical_db(source, fx, tmp_path, monkeypatch):
    canonical = tmp_path / "data" / "wrodfreq.db"
    monkeypatch.setattr(ingest_state, "CANONICAL_DB", canonical.resolve())
    monkeypatch.setattr(ingest_state, "EXPERIMENT_DIR", tmp_path / "experiments")
    monkeypatch.setattr(ingest_state, "DEFAULT_DB_PATH", canonical)
    assert run(source, fx, ["--db", str(canonical)]) == 0
    full = snapshot(canonical, source)
    canonical_bytes = canonical.read_bytes()

    assert run(source, fx, ["--test"]) == 0
    experiments = list((tmp_path / "experiments").glob(f"{source}-*.db"))
    assert len(experiments) == 1
    assert canonical.read_bytes() == canonical_bytes
    assert snapshot(canonical, source) == full

    assert run(source, fx, ["--test", "--db", str(canonical)]) == 2
    assert canonical.read_bytes() == canonical_bytes


def test_social_test_then_full_equals_fresh_full(fx, tmp_path, monkeypatch):
    """The historical contamination: a --test run followed by a full run."""
    monkeypatch.setattr(ingest_state, "EXPERIMENT_DIR", tmp_path / "experiments")
    fresh = clean("social", fx, tmp_path)
    db = tmp_path / "real.db"
    assert run("social", fx, ["--test"]) == 0
    assert run("social", fx, ["--db", str(db)]) == 0
    assert snapshot(db, "social") == fresh


def test_test_run_into_an_occupied_db_is_refused(fx, tmp_path):
    db = tmp_path / "staged.db"
    assert run("social", fx, ["--db", str(db)]) == 0
    before = snapshot(db, "social")
    assert run("social", fx, ["--test", "--db", str(db)]) == 2
    assert snapshot(db, "social") == before


# ---------------------------------------------------------------------------
# Writers, databases, fingerprints
# ---------------------------------------------------------------------------

def _session(db: Path, resume: bool, policy=None):
    conn = connect(db)
    spec = ingest_state.SourceSpec("toy", "Toy", None, None, "web", "contemporary", None, 1,
                                   policy or {"v": 1})
    return ingest_state.begin(conn, spec, resume=resume, initial_cursor={"n": 0},
                              input_manifest={"files": ["a"]})


def test_stale_writer_is_rejected_before_applying(tmp_path):
    db = tmp_path / "x.db"
    a = _session(db, resume=False)
    b = _session(db, resume=True)
    a.commit_batch({"de": 3}, {"de": 1}, {"n": 1}, 3, 1)
    with pytest.raises(ingest_state.StaleWriter):
        b.commit_batch({"la": 5}, {"la": 1}, {"n": 1}, 5, 1)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT word, occurrences FROM source_counts").fetchall() == [("de", 3)]
    assert conn.execute("SELECT total_tokens FROM sources").fetchone() == (3,)


def test_a_count_change_outside_the_ingest_makes_the_writer_stale(tmp_path):
    db = tmp_path / "x.db"
    a = _session(db, resume=False)
    conn = connect(db)
    ingest_state.record_count_change(conn, "toy", "migration")
    conn.commit()
    with pytest.raises(ingest_state.StaleWriter):
        a.commit_batch({"de": 3}, {"de": 1}, {"n": 1}, 3, 1)


def test_failed_batch_rolls_back_and_stays_replayable(tmp_path):
    db = tmp_path / "x.db"
    s = _session(db, resume=False)
    ingest_state.FAULT_HOOK = kill_at("during_apply", 1)
    occ, docs = {"de": 3}, {"de": 1}
    with pytest.raises(Killed):
        s.commit_batch(occ, docs, {"n": 1}, 3, 1)
    assert occ == {"de": 3}, "buffers must survive a failed commit"
    ingest_state.FAULT_HOOK = lambda p: None
    s.commit_batch(occ, docs, {"n": 1}, 3, 1)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT occurrences FROM source_counts").fetchone() == (3,)
    assert conn.execute("SELECT total_tokens FROM sources").fetchone() == (3,)


def test_state_from_another_database_is_refused(tmp_path):
    db = tmp_path / "x.db"
    _session(db, resume=False).commit_batch({"de": 1}, {"de": 1}, {"n": 1}, 1, 1)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE db_meta SET value = 'someone-else' WHERE key = 'db_id'")
    conn.commit()
    with pytest.raises(ingest_state.IngestRefused, match="another database|written by"):
        _session(db, resume=True)


def test_changed_policy_is_refused(tmp_path):
    db = tmp_path / "x.db"
    _session(db, resume=False)
    with pytest.raises(ingest_state.IngestRefused, match="policy"):
        _session(db, resume=True, policy={"v": 2})


def test_changed_social_dump_is_refused_on_resume(fx, tmp_path):
    db = tmp_path / "x.db"
    assert run("social", fx, ["--limit", "1", "--db", str(db)]) == 0
    before = snapshot(db, "social")
    dump = fx / "social" / "cluj_posts.zst"
    dump.write_bytes(dump.read_bytes() + b"\0")
    assert run("social", fx, ["--resume", "--db", str(db)]) == 2
    assert snapshot(db, "social") == before


# ---------------------------------------------------------------------------
# Social limits (A05): the cursor is N after N records, never N+1
# ---------------------------------------------------------------------------

def test_social_limit_checkpoints_n_not_n_plus_one(fx, tmp_path):
    db = tmp_path / "x.db"
    assert run("social", fx, ["--limit", "1", "--db", str(db)]) == 0
    cursor = snapshot(db, "social")["state"][1]
    assert set(cursor["files_done"].values()) == {1}


@pytest.mark.parametrize("limits", [[1, 2], [3], [2, 3, 4], [1, 5]])
def test_social_capped_and_resumed_equals_unbounded(limits, fx, tmp_path):
    expected = clean("social", fx, tmp_path)
    db = tmp_path / "capped.db"
    assert run("social", fx, ["--limit", str(limits[0]), "--db", str(db)]) == 0
    for n in limits[1:]:
        assert run("social", fx, ["--resume", "--limit", str(n), "--db", str(db)]) == 0
    assert run("social", fx, ["--resume", "--db", str(db)]) == 0
    assert snapshot(db, "social") == expected


# ---------------------------------------------------------------------------
# Legacy JSON checkpoints and adoption (A04)
# ---------------------------------------------------------------------------

def _legacy_db(tmp_path: Path, source: str, total: int, status: str = "completed") -> Path:
    db = tmp_path / "legacy.db"
    conn = connect(db)
    conn.execute("INSERT INTO sources (source_id, display_name, register, period, total_tokens, "
                 "total_docs, status) VALUES (?, ?, 'formal', 'contemporary', ?, 7, ?)",
                 (source, source, total, status))
    conn.execute("INSERT INTO source_counts VALUES ('de', ?, ?, 7)", (source, total - 10))
    conn.execute("INSERT INTO source_counts VALUES ('la', ?, 10, 3)", (source,))
    conn.commit()
    conn.close()
    return db


def test_eu_legacy_checkpoint_cannot_lower_the_migrated_denominator(fx, tmp_path):
    """Audit: resuming a completed eu with its old checkpoint reset 84,722,606 to 84,415,957."""
    cp_dir = tmp_path / "checkpoints"
    cp_dir.mkdir()
    old = {"parts": {"europarl": {"completed": True, "tokens_done": 9_570_511, "lines_done": 1},
                     "dgt": {"completed": True, "tokens_done": 74_845_446, "lines_done": 1}}}
    (cp_dir / "eu_checkpoint.json").write_text(json.dumps(old))
    db = _legacy_db(tmp_path, "eu", 84_722_606)

    assert run("eu", fx, ["--resume", "--db", str(db)]) == 2      # unadopted: refused
    assert run("eu", fx, ["--db", str(db)]) == 2                  # fresh: refused
    assert snapshot(db, "eu")["source"][0] == 84_722_606

    conn = connect(db)
    conn.isolation_level = None
    out = adopt_legacy.adopt(conn, ["eu"], cp_dir)
    conn.close()
    assert out["eu"].startswith("sealed")
    assert run("eu", fx, ["--resume", "--db", str(db)]) == 0      # sealed: no-op
    snap = snapshot(db, "eu")
    assert snap["source"][:3] == (84_722_606, 7, "completed")
    assert snap["state"][0] == "sealed_legacy"
    prov = json.loads(sqlite3.connect(db).execute(
        "SELECT provenance FROM ingest_state").fetchone()[0])
    assert prov["legacy_checkpoints"][0]["parts_tokens_done"] == {
        "europarl": 9_570_511, "dgt": 74_845_446}


@pytest.mark.parametrize("source", ["web", "news", "subs", "wiki"])
def test_completed_legacy_sources_are_sealed_not_replayed(source, fx, tmp_path):
    db = _legacy_db(tmp_path, source, 1_000)
    assert run(source, fx, ["--resume", "--db", str(db)]) == 2
    conn = connect(db)
    conn.isolation_level = None
    assert adopt_legacy.adopt(conn, [source], tmp_path)[source].startswith("sealed")
    conn.close()
    before = snapshot(db, source)
    assert run(source, fx, ["--resume", "--db", str(db)]) == 0
    assert snapshot(db, source) == before
    assert before["source"][0] == 1_000


@pytest.mark.parametrize("corrupt", [None, "{not json", json.dumps({"tokens_done": 5})])
def test_unfinished_legacy_source_refuses_resume_whatever_its_json(corrupt, fx, tmp_path):
    db = _legacy_db(tmp_path, "social", 1_000, status="in_progress")
    if corrupt is not None:
        (tmp_path / "social_checkpoint.json").write_text(corrupt)
    before = snapshot(db, "social")
    assert run("social", fx, ["--resume", "--db", str(db)]) == 2
    conn = connect(db)
    conn.isolation_level = None
    assert adopt_legacy.adopt(conn, ["social"], tmp_path)["social"].startswith("refused")
    conn.close()
    assert snapshot(db, "social") == before


def test_adoption_refuses_non_conserving_counts(tmp_path):
    """The contaminated social: completed, but SUM(occurrences) != total_tokens."""
    db = _legacy_db(tmp_path, "social", 1_000)
    conn = connect(db)
    conn.execute("UPDATE source_counts SET occurrences = occurrences + 5 WHERE word = 'la'")
    conn.commit()
    conn.isolation_level = None
    assert "SUM(occurrences)" in adopt_legacy.adopt(conn, ["social"], tmp_path)["social"]
    assert conn.execute("SELECT COUNT(*) FROM ingest_state").fetchone() == (0,)


def test_new_build_scripts_answer_help():
    for name in ("adopt_legacy", "integrity", "inventory", "replace_source", "compare_builds",
                 "build_manifest", "check_rebuild", "check_release", "make_distribution",
                 "make_synthetic"):
        out = subprocess.run([sys.executable, str(BUILD / f"{name}.py"), "--help"],
                             capture_output=True, timeout=60)
        assert out.returncode == 0, name


# ---------------------------------------------------------------------------
# Source replacement in a candidate (brief B7 step 6)
# ---------------------------------------------------------------------------

def test_replace_source_swaps_counts_and_rebinds_state(fx, tmp_path):
    import replace_source
    target = tmp_path / "target.db"
    assert run("wiki", fx, ["--db", str(target)]) == 0
    # contaminated social in the target: completed but non-conserving, no state
    conn = connect(target)
    conn.execute("INSERT INTO sources (source_id, display_name, register, period, total_tokens, "
                 "total_docs, status) VALUES ('social', 's', 'social', 'contemporary', 5, 1, "
                 "'completed')")
    conn.execute("INSERT INTO source_counts VALUES ('ghost', 'social', 9, 1)")
    conn.commit()
    wiki_before = snapshot(target, "wiki")
    donor = tmp_path / "donor.db"
    assert run("social", fx, ["--db", str(donor)]) == 0
    expected = snapshot(donor, "social")

    replace_source.replace(target, donor, "social")
    got = snapshot(target, "social")
    assert got["counts"] == expected["counts"] and got["source"] == expected["source"]
    assert got["state"] == expected["state"]
    assert snapshot(target, "wiki") == wiki_before
    c = sqlite3.connect(target)
    owner, prov = c.execute("SELECT db_id, provenance FROM ingest_state "
                            "WHERE source_id='social'").fetchone()
    assert owner == c.execute("SELECT value FROM db_meta").fetchone()[0]
    assert json.loads(prov)["imported_from"]["replaced_sum_occurrences"] == 9
    # the imported generation now resumes as a no-op in its new home
    assert run("social", fx, ["--resume", "--db", str(target)]) == 0


def test_replace_source_refuses_an_unfinished_donor(fx, tmp_path):
    import replace_source
    target = tmp_path / "target.db"
    assert run("wiki", fx, ["--db", str(target)]) == 0
    donor = tmp_path / "donor.db"
    assert run("social", fx, ["--limit", "1", "--db", str(donor)]) == 0
    before = target.read_bytes()
    with pytest.raises(replace_source.ReplaceRefused):
        replace_source.replace(target, donor, "social")
    assert target.read_bytes() == before
