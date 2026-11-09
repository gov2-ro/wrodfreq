"""Deterministic builds and coherent bundles (brief B7, finding A09; ADR-006).

A build is named by its input manifest. The same manifest gives the same bytes on
any day; a generation directory is complete or invisible; and the runtime refuses
any mix of files from different builds — including equal-sized ones.
"""

from __future__ import annotations

import json
import lzma
import shutil
import sys
from pathlib import Path

import msgpack
import pytest

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))

import build_package  # noqa: E402
import ingest_state  # noqa: E402
import make_synthetic  # noqa: E402
from build_manifest import build_id_of, load_manifest, make_manifest  # noqa: E402
from wrodfreq import _surface  # noqa: E402
from wrodfreq.db import connect  # noqa: E402


@pytest.fixture(scope="module")
def gen(tmp_path_factory) -> Path:
    return make_synthetic.make_generation(tmp_path_factory.mktemp("syn"), log=lambda *a: None)


def files_of(gen_dir: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(gen_dir.iterdir())}


def decode(path: Path) -> dict:
    return msgpack.unpackb(lzma.decompress(path.read_bytes()), raw=False)


def encode_to(path: Path, payload: dict) -> None:
    path.write_bytes(lzma.compress(msgpack.packb(payload, use_bin_type=True)))


@pytest.fixture
def use_files(monkeypatch, tmp_path):
    """Point the runtime at a copy of some data files."""
    def point(src_dir: Path, overrides: dict[str, dict] | None = None, drop=()):
        d = tmp_path / "pkg"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir()
        for name in build_package.PACKAGE_FILES:
            if (src_dir / name).exists() and name not in drop:
                shutil.copy(src_dir / name, d / name)
        for name, payload in (overrides or {}).items():
            encode_to(d / name, payload)
        monkeypatch.setattr(_surface, "SURFACE_PATH", d / build_package.SURFACE_FILENAME)
        monkeypatch.setattr(_surface, "BY_SOURCE_PATH", d / build_package.BY_SOURCE_FILENAME)
        monkeypatch.setattr(_surface, "LEMMA_PATH", d / build_package.LEMMA_FILENAME)
        monkeypatch.setattr(_surface, "_surface", None)
        monkeypatch.setattr(_surface, "_lemmas", None)
        return d
    yield point
    monkeypatch.setattr(_surface, "_surface", None)
    monkeypatch.setattr(_surface, "_lemmas", None)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_same_manifest_on_different_days_gives_identical_bytes(gen, tmp_path, monkeypatch):
    """Every clock the pipeline could read differs between the two runs; the files do not."""
    import time
    conn = connect(gen.parent.parent / "synthetic.db")
    manifest = load_manifest(gen / "inputs.json")
    outputs = []
    for day, epoch in (("2026-11-08T23:59:00+00:00", 1_762_646_340),
                       ("2027-03-01T12:00:00+00:00", 1_803_988_800)):
        monkeypatch.setattr(ingest_state, "now", lambda d=day: d)
        monkeypatch.setattr(time, "time", lambda e=epoch: float(e))
        out = build_package.write_generation(conn, manifest, tmp_path / day[:10],
                                             log=lambda *a: None)
        outputs.append(files_of(out))
    conn.close()
    assert outputs[0] == outputs[1] == files_of(gen)
    assert set(outputs[0]) >= {"ro_surface.msgpack.xz", "ro_by_source.msgpack.xz",
                               "ro_lemma.msgpack.xz", "ro_surface.tsv.gz", "result.json"}


def test_payload_code_never_reads_the_clock():
    for name in ("build_package.py", "export_tsv.py"):
        text = (BUILD / name).read_text()
        assert "datetime" not in text and "time.time" not in text, name


def test_rebuild_into_the_same_root_keeps_identical_files(gen):
    before = files_of(gen)
    conn = connect(gen.parent.parent / "synthetic.db")
    manifest = load_manifest(gen / "inputs.json")
    again = build_package.write_generation(conn, manifest, gen.parent, log=lambda *a: None)
    conn.close()
    assert again == gen and files_of(gen) == before
    assert not list(gen.parent.glob(".*partial*"))


def test_build_id_ignores_the_db_path_and_tracks_inputs(gen, tmp_path):
    copy = tmp_path / "copy.db"
    shutil.copy(gen.parent.parent / "synthetic.db", copy)
    conn = connect(copy)
    m = make_manifest(conn, "2000-01-01", lemma=True, synthetic=True, progress=False)
    assert m["build_id"] == gen.name
    other_day = make_manifest(conn, "2000-01-02", lemma=True, synthetic=True, progress=False)
    assert other_day["build_id"] != gen.name
    conn.close()


def test_edited_manifest_is_rejected(gen, tmp_path):
    m = json.loads((gen / "inputs.json").read_text())
    m["panel"] = m["panel"][:-1]
    p = tmp_path / "edited.json"
    p.write_text(json.dumps(m))
    with pytest.raises(ValueError, match="build_id"):
        load_manifest(p)
    assert build_id_of(m) != m["build_id"]


def test_stale_db_is_refused_by_the_package_stage(gen, tmp_path):
    copy = tmp_path / "stale.db"
    shutil.copy(gen.parent.parent / "synthetic.db", copy)
    conn = connect(copy)
    ingest_state.record_count_change(conn, "web", "test")
    conn.commit()
    with pytest.raises(build_package.PackageRefused, match="stale|stage"):
        build_package.write_generation(conn, load_manifest(gen / "inputs.json"),
                                       tmp_path / "out", log=lambda *a: None)
    conn.close()


# ---------------------------------------------------------------------------
# Generation lifecycle: complete or invisible
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fail_on", [1, 2, 3])
def test_crash_between_files_leaves_no_selectable_generation(gen, tmp_path, monkeypatch, fail_on):
    calls = {"n": 0}
    real = build_package.encode

    def flaky(payload):
        calls["n"] += 1
        if calls["n"] == fail_on:
            raise lzma.LZMAError("compression failed")
        return real(payload)
    monkeypatch.setattr(build_package, "encode", flaky)
    conn = connect(gen.parent.parent / "synthetic.db")
    out = tmp_path / "out"
    with pytest.raises(lzma.LZMAError):
        build_package.write_generation(conn, load_manifest(gen / "inputs.json"), out,
                                       log=lambda *a: None)
    conn.close()
    assert list(out.iterdir()) == []
    with pytest.raises(build_package.PackageRefused):
        build_package.install(out / gen.name, tmp_path / "pkg")


def test_tampered_generation_cannot_be_installed(gen, tmp_path):
    copy = tmp_path / gen.name
    shutil.copytree(gen, copy)
    (copy / "ro_surface.tsv.gz").write_bytes(b"x")
    with pytest.raises(build_package.PackageRefused, match="does not match"):
        build_package.install(copy, tmp_path / "pkg")


def test_install_replaces_all_files_and_drops_a_stale_lemma(gen, tmp_path):
    no_lemma = make_synthetic.make_generation(tmp_path / "nolemma", lemma=False,
                                              log=lambda *a: None)
    pkg = tmp_path / "pkg"
    build_package.install(gen, pkg)
    assert (pkg / "ro_lemma.msgpack.xz").exists()
    build_package.install(no_lemma, pkg)
    assert not (pkg / "ro_lemma.msgpack.xz").exists()
    info = json.loads((pkg / "build.json").read_text())
    assert info["build_id"] == no_lemma.name and info["lemma"] is False


# ---------------------------------------------------------------------------
# Runtime: mixed or damaged files are refused
# ---------------------------------------------------------------------------

def test_runtime_reads_a_coherent_generation(gen, use_files):
    import wrodfreq
    use_files(gen)
    assert wrodfreq.zipf_frequency("de") > 6
    assert wrodfreq.build_info()["build_id"] == gen.name
    assert wrodfreq.by_source("de")
    assert wrodfreq.lemma_detail("casă") is not None


def test_equal_sized_different_vocabulary_is_refused(gen, use_files):
    import wrodfreq
    by_source = decode(gen / "ro_by_source.msgpack.xz")
    surface = decode(gen / "ro_surface.msgpack.xz")
    words = list(surface["words"])
    words[0] = "altceva"                       # same length, different vocabulary
    import hashlib
    by_source["words_sha256"] = hashlib.sha256("\n".join(sorted(words)).encode()).hexdigest()
    use_files(gen, {build_package.BY_SOURCE_FILENAME: by_source})
    with pytest.raises(_surface.DataFileError, match="word list"):
        wrodfreq.by_source("de")


def test_by_source_from_another_build_is_refused(gen, use_files):
    import wrodfreq
    by_source = decode(gen / "ro_by_source.msgpack.xz")
    by_source["build_id"] = "another-build"
    use_files(gen, {build_package.BY_SOURCE_FILENAME: by_source})
    with pytest.raises(_surface.DataFileError, match="another-build"):
        wrodfreq.by_source("de")


def test_reordered_source_panel_is_refused(gen, use_files):
    """The audit's MIXED_BUILD_FILES: news values presented under social's name."""
    import wrodfreq
    by_source = decode(gen / "ro_by_source.msgpack.xz")
    by_source["sources"] = list(reversed(by_source["sources"]))
    use_files(gen, {build_package.BY_SOURCE_FILENAME: by_source})
    with pytest.raises(_surface.DataFileError, match="panel"):
        wrodfreq.by_source("de")


def test_wrong_lemma_build_is_an_error_not_a_fallback(gen, use_files):
    import wrodfreq
    lemma = decode(gen / "ro_lemma.msgpack.xz")
    lemma["build_id"] = "another-build"
    use_files(gen, {build_package.LEMMA_FILENAME: lemma})
    with pytest.raises(_surface.DataFileError):
        wrodfreq.lemma_detail("casă")


def test_missing_lemma_file_of_a_lemma_build_is_an_error(gen, use_files):
    import wrodfreq
    use_files(gen, drop=(build_package.LEMMA_FILENAME,))
    with pytest.raises(_surface.DataFileError, match="missing"):
        wrodfreq.lemma_detail("casă")


@pytest.mark.parametrize("field", ["zipf", "spread", "words"])
def test_truncated_arrays_are_refused(gen, use_files, field):
    import wrodfreq
    surface = decode(gen / "ro_surface.msgpack.xz")
    surface[field] = surface[field][:-1]
    use_files(gen, {build_package.SURFACE_FILENAME: surface})
    with pytest.raises(_surface.DataFileError):
        wrodfreq.zipf_frequency("de")


def test_truncated_by_source_is_refused(gen, use_files):
    import wrodfreq
    by_source = decode(gen / "ro_by_source.msgpack.xz")
    by_source["by_source"] = by_source["by_source"][:-1]
    use_files(gen, {build_package.BY_SOURCE_FILENAME: by_source})
    with pytest.raises(_surface.DataFileError):
        wrodfreq.by_source("de")


def test_format_one_files_are_refused_with_a_rebuild_message(gen, use_files):
    import wrodfreq
    surface = decode(gen / "ro_surface.msgpack.xz")
    surface["format_version"] = 1
    use_files(gen, {build_package.SURFACE_FILENAME: surface})
    with pytest.raises(_surface.DataFileError, match="build_package"):
        wrodfreq.zipf_frequency("de")
