"""Distribution and release gate (brief B7, finding A08).

The wheel is built from the sdist of a staging tree holding one generation; the
release preflight checks that exact bundle. A data-less wheel, a synthetic build,
a report for another build, or a tampered bundle can never pass the release gate.
These tests build real archives with `python -m build` (needs the `build` package
and network access for the build backend).
"""

from __future__ import annotations

import importlib.metadata
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))

import build_package  # noqa: E402
import make_synthetic  # noqa: E402
import validate  # noqa: E402
from build_manifest import make_manifest  # noqa: E402
from wrodfreq.db import connect  # noqa: E402

try:
    importlib.metadata.version("build")
except importlib.metadata.PackageNotFoundError:
    pytest.skip("the PyPA `build` frontend is not installed", allow_module_level=True)


def sh(*args, ok=True):
    res = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True)
    if ok:
        assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-3000:]
    return res


@pytest.fixture(scope="module")
def real_bundle(tmp_path_factory):
    """A fixture build marked non-synthetic, validated strictly with fake references."""
    out = tmp_path_factory.mktemp("rel")
    make_synthetic.build_synthetic_db(out / "db.db")
    make_synthetic.write_dex_map(out / "dex.db")
    make_synthetic.build_stages(out / "db.db", out / "dex.db", lemma=True)
    conn = connect(out / "db.db")
    manifest = make_manifest(conn, "2000-01-01", lemma=True, synthetic=False, progress=False)
    gen = build_package.write_generation(conn, manifest, out / "builds", log=lambda *a: None)
    words = {w: round(z, 2) for w, z in conn.execute("SELECT word, zipf FROM merged")}
    conn.close()
    (out / "wf.json").write_text(json.dumps({"version": "0-fake", "words": words}))
    fake = out / "fake-python"
    fake.write_text(f"#!/bin/sh\ncat '{out / 'wf.json'}'\n")
    fake.chmod(0o755)
    saved = validate.MONOTONE_PAIRS
    validate.MONOTONE_PAIRS = [("de", "casă"), ("casă", "locuință")]
    try:
        report = validate.run_validation(out / "db.db", gen, strict=True, dex_db=out / "dex.db",
                                         wordfreq_python=fake)
    finally:
        validate.MONOTONE_PAIRS = saved
    assert report["release_eligible"], report["statuses"]
    (out / "validation.json").write_text(json.dumps(report))
    sh(BUILD / "make_distribution.py", "--generation", gen, "--validation",
       out / "validation.json", "--out", out / "bundle", "--tar")
    return out, gen


def test_real_bundle_passes_the_release_gate_and_installs(real_bundle):
    out, _ = real_bundle
    res = sh(BUILD / "check_release.py", "--bundle", out / "bundle", "--install-test")
    assert "RELEASE CHECK PASSED" in res.stdout


def test_tarball_needs_and_checks_its_digest(real_bundle):
    out, _ = real_bundle
    tar = out / "bundle.tar.gz"
    assert sh(BUILD / "check_release.py", "--bundle", tar, ok=False).returncode == 1
    assert sh(BUILD / "check_release.py", "--bundle", tar, "--expected-sha256", "0" * 64,
              ok=False).returncode == 1
    digest = build_package.sha256_file(tar)
    assert sh(BUILD / "check_release.py", "--bundle", tar, "--expected-sha256",
              digest).returncode == 0


def test_tarball_is_deterministic(real_bundle, tmp_path):
    out, _ = real_bundle
    sys.path.insert(0, str(BUILD))
    import make_distribution
    a = make_distribution.deterministic_tar(out / "bundle", tmp_path / "a.tar.gz")
    b = make_distribution.deterministic_tar(out / "bundle", tmp_path / "b.tar.gz")
    assert a == b


def _copy(real_bundle, tmp_path) -> Path:
    out, _ = real_bundle
    copy = tmp_path / "bundle"
    shutil.copytree(out / "bundle", copy)
    return copy


def _refresh_candidate(bundle: Path) -> None:
    cand = json.loads((bundle / "candidate.json").read_text())
    cand["files"] = {str(p.relative_to(bundle)): build_package.sha256_file(p)
                     for p in sorted(bundle.rglob("*"))
                     if p.is_file() and p.name != "candidate.json"}
    (bundle / "candidate.json").write_text(json.dumps(cand))


def test_data_less_wheel_is_rejected(real_bundle, tmp_path):
    bundle = _copy(real_bundle, tmp_path)
    wheel = next((bundle / "dist").glob("*.whl"))
    stripped = tmp_path / wheel.name
    with zipfile.ZipFile(wheel) as src, zipfile.ZipFile(stripped, "w") as dst:
        for item in src.infolist():
            if "/data/" not in item.filename:
                dst.writestr(item, src.read(item))
    shutil.move(stripped, wheel)
    _refresh_candidate(bundle)
    res = sh(BUILD / "check_release.py", "--bundle", bundle, ok=False)
    assert res.returncode == 1 and "lacks wrodfreq/data" in res.stderr


def test_report_for_another_build_is_rejected(real_bundle, tmp_path):
    bundle = _copy(real_bundle, tmp_path)
    rep = json.loads((bundle / "validation.json").read_text())
    rep["build_id"] = "0" * 32
    (bundle / "validation.json").write_text(json.dumps(rep))
    _refresh_candidate(bundle)
    res = sh(BUILD / "check_release.py", "--bundle", bundle, ok=False)
    assert res.returncode == 1 and "another build id" in res.stderr


def test_missing_report_is_rejected(real_bundle, tmp_path):
    bundle = _copy(real_bundle, tmp_path)
    (bundle / "validation.json").unlink()
    _refresh_candidate(bundle)
    assert sh(BUILD / "check_release.py", "--bundle", bundle, ok=False).returncode == 1


def test_tampered_bundle_file_is_rejected(real_bundle, tmp_path):
    bundle = _copy(real_bundle, tmp_path)
    (bundle / "generation" / "ro_surface.tsv.gz").write_bytes(b"x")
    res = sh(BUILD / "check_release.py", "--bundle", bundle, ok=False)
    assert res.returncode == 1 and "candidate.json" in res.stderr


def test_wrong_tag_is_rejected(real_bundle):
    out, _ = real_bundle
    res = sh(BUILD / "check_release.py", "--bundle", out / "bundle", "--tag", "v9.9.9", ok=False)
    assert res.returncode == 1 and "tag" in res.stderr


@pytest.fixture(scope="module")
def synthetic_bundle(tmp_path_factory):
    out = tmp_path_factory.mktemp("synrel")
    gen = make_synthetic.make_generation(out / "syn", lemma=False, log=lambda *a: None)
    sh(BUILD / "make_distribution.py", "--generation", gen, "--out", out / "bundle",
       "--allow-synthetic")
    return out / "bundle"


def test_synthetic_bundle_is_never_a_release(synthetic_bundle):
    res = sh(BUILD / "check_release.py", "--bundle", synthetic_bundle, ok=False)
    assert res.returncode == 1 and "synthetic" in res.stderr


def test_synthetic_bundle_passes_ci_mode_with_lemma_absent(synthetic_bundle):
    res = sh(BUILD / "check_release.py", "--bundle", synthetic_bundle, "--mode", "ci-synthetic",
             "--install-test")
    assert "CI SYNTHETIC CHECK PASSED" in res.stdout and "'lemma': False" in res.stdout


def test_real_generation_without_report_cannot_be_bundled(real_bundle, tmp_path):
    _, gen = real_bundle
    res = sh(BUILD / "make_distribution.py", "--generation", gen, "--out", tmp_path / "b",
             ok=False)
    assert res.returncode == 2


def test_relative_paths_work(real_bundle, tmp_path, monkeypatch):
    """The build frontend runs in another directory; relative paths must still resolve."""
    _, gen = real_bundle
    monkeypatch.chdir(tmp_path)
    shutil.copytree(gen, tmp_path / "gen")
    report = json.loads((real_bundle[0] / "validation.json").read_text())
    (tmp_path / "v.json").write_text(json.dumps(report))
    res = subprocess.run([sys.executable, str(BUILD / "make_distribution.py"), "--generation",
                          "gen", "--validation", "v.json", "--out", "rel-bundle"],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr[-2000:]
    assert len(list((tmp_path / "rel-bundle" / "dist").glob("*.whl"))) == 1
