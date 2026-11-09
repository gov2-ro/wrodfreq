"""The read-only validation gate (brief B7, finding A02; ADR-006).

`validate.py` must never change what it inspects, must fail strict validation for
stale or contaminated data even when external references are unavailable, and must
reject artifacts that do not match the database or the result manifest.
"""

from __future__ import annotations

import hashlib
import json
import lzma
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import msgpack
import pytest

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))

import build_package  # noqa: E402
import make_synthetic  # noqa: E402
import validate  # noqa: E402
from wrodfreq.db import connect  # noqa: E402

MISSING = Path("/nonexistent/reference")


@pytest.fixture(scope="module")
def syn(tmp_path_factory):
    out = tmp_path_factory.mktemp("synv")
    gen = make_synthetic.make_generation(out, log=lambda *a: None)
    return out, gen


def tree_hashes(*paths: Path) -> dict:
    out = {}
    for p in paths:
        for f in ([p] if p.is_file() else sorted(p.rglob("*"))):
            if f.is_file():
                out[str(f)] = (hashlib.sha256(f.read_bytes()).hexdigest(), f.stat().st_mtime_ns)
    return out


@pytest.fixture
def fake_wordfreq(tmp_path, syn):
    """An executable that prints a wordfreq-shaped reference built from our own table."""
    out, _ = syn
    conn = sqlite3.connect(out / "synthetic.db")
    words = {w: round(z, 2) for w, z in conn.execute("SELECT word, zipf FROM merged")}
    conn.close()
    data = tmp_path / "wf.json"
    data.write_text(json.dumps({"version": "0.0-fake", "words": words}))
    exe = tmp_path / "fake-python"
    exe.write_text(f"#!/bin/sh\ncat '{data}'\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def run(db, gen, *, strict, dex=MISSING, wf=MISSING, **kw):
    return validate.run_validation(db, gen, strict=strict, dex_db=dex, wordfreq_python=wf, **kw)


def test_validation_never_changes_its_inputs(syn):
    out, gen = syn
    db = out / "synthetic.db"
    before = tree_hashes(db, gen)
    report = run(db, gen, strict=True)
    assert tree_hashes(db, gen) == before
    assert report["inputs_unchanged"] is True
    assert report["inspected"]["db_sha256_before"] == report["inspected"]["db_sha256_after"]


def test_missing_database_fails_and_creates_nothing(tmp_path):
    absent = tmp_path / "absent.db"
    out = subprocess.run([sys.executable, str(BUILD / "validate.py"), "--db", str(absent)],
                         capture_output=True)
    assert out.returncode == 2 and not absent.exists()


def test_absent_references_fail_strict_and_skip_otherwise(syn):
    out, gen = syn
    strict = run(out / "synthetic.db", gen, strict=True)
    assert strict["statuses"]["wordfreq"] == "fail"
    assert strict["statuses"]["dex_coverage"] == "fail"
    assert strict["release_eligible"] is False
    loose = run(out / "synthetic.db", gen, strict=False)
    assert loose["statuses"]["wordfreq"] == "skip"
    assert loose["release_eligible"] is False


def test_missing_comparison_words_fail_strict(syn):
    out, gen = syn
    report = run(out / "synthetic.db", gen, strict=True)
    assert report["statuses"]["monotone_pairs"] == "fail"
    assert report["checks"]["monotone_pairs"]["detail"]["missing"]


def test_all_checks_pass_but_a_synthetic_build_is_never_eligible(syn, fake_wordfreq,
                                                                  monkeypatch):
    out, gen = syn
    monkeypatch.setattr(validate, "MONOTONE_PAIRS", [("de", "casă"), ("casă", "locuință")])
    report = run(out / "synthetic.db", gen, strict=True, dex=out / "dex.db", wf=fake_wordfreq)
    assert set(report["statuses"].values()) == {"pass"}, report["statuses"]
    assert report["synthetic"] is True and report["release_eligible"] is False
    assert report["references"]["wordfreq"]["version"] == "0.0-fake"


def test_developer_skip_is_visible_and_never_eligible(syn, fake_wordfreq, monkeypatch):
    out, gen = syn
    monkeypatch.setattr(validate, "MONOTONE_PAIRS", [("de", "casă")])
    report = run(out / "synthetic.db", gen, strict=True, dex=out / "dex.db", wf=fake_wordfreq,
                 skip=["dex_coverage"])
    assert report["statuses"]["dex_coverage"] == "skipped_by_developer"
    assert report["release_eligible"] is False


def test_stale_merged_fixture_fails_strict_without_references(tmp_path):
    """The audit's VALIDATION_GATE: the old validator rebuilt `merged`, lost `de`, and
    still reported 3/3. Now the check is read-only and the inconsistency is a failure."""
    db = tmp_path / "stale.db"
    conn = connect(db)
    conn.execute("INSERT INTO sources (source_id, display_name, register, period, total_tokens, "
                 "total_docs, status) VALUES ('wiki', 'w', 'web', 'contemporary', 129, 1, "
                 "'completed')")
    conn.executemany("INSERT INTO source_counts VALUES (?, 'wiki', ?, 1)",
                     list(zip(validate.FUNCTION_WORDS, [50, 30, 20, 15, 12])) + [("x", 2)])
    conn.commit()
    sys.path.insert(0, str(BUILD))
    import compute_zipf
    import merge
    compute_zipf.compute_all(conn)
    merge.run_merge(conn, ["wiki"], set())
    conn.execute("UPDATE source_counts SET occurrences = 1 WHERE word = 'de'")
    conn.commit()
    conn.close()
    before = tree_hashes(db)
    out = subprocess.run([sys.executable, str(BUILD / "validate.py"), "--db", str(db),
                          "--strict", "--dex-db", str(MISSING), "--wordfreq-python",
                          str(MISSING), "--report", str(tmp_path / "r.json")],
                         capture_output=True, text=True)
    assert out.returncode == 1
    report = json.loads((tmp_path / "r.json").read_text())
    codes = {i["code"] for i in report["checks"]["integrity"]["detail"]["issues"]}
    assert {"token_sum_mismatch", "reliable_flag_wrong"} & codes
    assert tree_hashes(db) == before
    assert sqlite3.connect(db).execute("SELECT 1 FROM merged WHERE word='de'").fetchone()


def test_contaminated_counts_fail_integrity_and_name_the_source(syn, tmp_path):
    out, gen = syn
    db = tmp_path / "contaminated.db"
    shutil.copy(out / "synthetic.db", db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE source_counts SET occurrences = occurrences + 7 "
                 "WHERE source_id = 'social' AND word = 'de'")
    conn.commit()
    conn.close()
    report = run(db, gen, strict=False)
    assert report["statuses"]["integrity"] == "fail"
    assert report["statuses"]["provenance"] == "fail"
    issues = report["checks"]["integrity"]["detail"]["issues"]
    assert any(i["code"] == "token_sum_mismatch" and i["source"] == "social" for i in issues)


def _repack(gen_copy: Path, name: str, mutate, update_result: bool) -> None:
    path = gen_copy / name
    payload = msgpack.unpackb(lzma.decompress(path.read_bytes()), raw=False)
    mutate(payload)
    path.write_bytes(build_package.encode(payload))
    if update_result:
        result = json.loads((gen_copy / "result.json").read_text())
        result["files"][name] = {"sha256": build_package.sha256_file(path),
                                 "size": path.stat().st_size}
        (gen_copy / "result.json").write_text(json.dumps(result))


@pytest.mark.parametrize("update_result", [False, True])
def test_tampered_by_source_values_fail_artifacts(syn, tmp_path, update_result):
    out, gen = syn
    copy = tmp_path / gen.name
    shutil.copytree(gen, copy)

    def bump(p):
        row = next(r for r in p["by_source"] if any(v is not None for v in r))
        k = next(i for i, v in enumerate(row) if v is not None)
        row[k] += 1
    _repack(copy, build_package.BY_SOURCE_FILENAME, bump, update_result)
    report = run(out / "synthetic.db", copy, strict=False)
    assert report["statuses"]["artifacts"] == "fail"


def test_tampered_tsv_fails_artifacts(syn, tmp_path):
    out, gen = syn
    copy = tmp_path / gen.name
    shutil.copytree(gen, copy)
    import gzip
    lines = gzip.decompress((copy / "ro_surface.tsv.gz").read_bytes()).decode().splitlines(True)
    lines[3] = lines[3].replace("\t", "\t9", 1)
    (copy / "ro_surface.tsv.gz").write_bytes(gzip.compress("".join(lines).encode(), mtime=0))
    result = json.loads((copy / "result.json").read_text())
    result["files"]["ro_surface.tsv.gz"]["sha256"] = build_package.sha256_file(
        copy / "ro_surface.tsv.gz")
    (copy / "result.json").write_text(json.dumps(result))
    assert run(out / "synthetic.db", copy, strict=False)["statuses"]["artifacts"] == "fail"


def test_report_is_never_written_into_the_inputs(syn, tmp_path):
    out, gen = syn
    for target in (out / "synthetic.db", gen / "report.json"):
        res = subprocess.run([sys.executable, str(BUILD / "validate.py"), "--db",
                              str(out / "synthetic.db"), "--generation", str(gen),
                              "--report", str(target)], capture_output=True)
        assert res.returncode == 2
    assert not (gen / "report.json").exists()


def test_independent_rebuild_matches(syn, tmp_path):
    out, gen = syn
    res = subprocess.run([sys.executable, str(BUILD / "check_rebuild.py"), "--db",
                          str(out / "synthetic.db"), "--generation", str(gen), "--work-dir",
                          str(tmp_path / "work"), "--dex-db", str(out / "dex.db"),
                          "--wordfreq-python", str(MISSING), "--report",
                          str(tmp_path / "rebuild.json")],
                         capture_output=True, text=True, env={k: v for k, v in os.environ.items()
                                                              if k != "PYTHONPATH"})
    assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-2000:]
    report = json.loads((tmp_path / "rebuild.json").read_text())
    assert report["same_manifest"] and all(report["files_identical"].values())
    assert all(report["derived_tables_identical"].values())
