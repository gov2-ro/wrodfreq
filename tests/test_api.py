"""Tests for the public API (spec §10.1) against a small synthetic data file —
not the real ~37 MB build artifact, which is gitignored and may not exist.
"""

from __future__ import annotations

import lzma

import msgpack
import pytest

import wrodfreq
from wrodfreq import _surface

SOURCES = ["eu", "news", "subs", "web", "wiki"]
SCALE = 100

# word -> (zipf, n_reliable, n_attesting, spread, {source: zipf_or_None})
WORDS = {
    "de": (7.71, 5, 5, 0.33, {"eu": 7.79, "news": 7.74, "subs": 7.46, "web": 7.71, "wiki": 7.67}),
    "birjă": (1.78, 4, 4, 0.89, {"eu": None, "news": 1.22, "subs": 2.10, "web": 1.77, "wiki": 2.04}),
    "și": (7.38, 5, 5, 0.30, {"eu": 7.47, "news": 7.39, "subs": 7.17, "web": 7.35, "wiki": 7.41}),
    # ASCII-only, ranks between "de" and "și" — needed so ascii_only tests can
    # tell "skipped a diacritic word" apart from "ran out of ASCII words".
    "un": (6.89, 5, 5, 0.29, {"eu": 6.67, "news": 6.90, "subs": 6.96, "web": 6.90, "wiki": 6.88}),
}


def _make_payloads() -> tuple[dict, dict]:
    words = sorted(WORDS)
    zipf = [round(WORDS[w][0] * SCALE) for w in words]
    n_reliable = [WORDS[w][1] for w in words]
    n_attesting = [WORDS[w][2] for w in words]
    spread = [round(WORDS[w][3] * SCALE) for w in words]
    by_source = [
        [None if WORDS[w][4][s] is None else round(WORDS[w][4][s] * SCALE) for s in SOURCES]
        for w in words
    ]
    surface = {
        "format_version": 1,
        "built": "2026-01-01",
        "sources": SOURCES,
        "n_sources": len(SOURCES),
        "zipf_scale": SCALE,
        "word_count": len(words),
        "words": words,
        "zipf": zipf,
        "n_reliable": n_reliable,
        "n_attesting": n_attesting,
        "spread": spread,
    }
    by_src = {
        "format_version": 1,
        "built": "2026-01-01",
        "sources": SOURCES,
        "zipf_scale": SCALE,
        "word_count": len(words),
        "by_source": by_source,
    }
    return surface, by_src


@pytest.fixture(autouse=True)
def fake_data_files(tmp_path, monkeypatch):
    surface, by_src = _make_payloads()
    surface_path = tmp_path / "ro_surface.msgpack.xz"
    by_source_path = tmp_path / "ro_by_source.msgpack.xz"
    surface_path.write_bytes(lzma.compress(msgpack.packb(surface, use_bin_type=True)))
    by_source_path.write_bytes(lzma.compress(msgpack.packb(by_src, use_bin_type=True)))

    monkeypatch.setattr(_surface, "SURFACE_PATH", surface_path)
    monkeypatch.setattr(_surface, "BY_SOURCE_PATH", by_source_path)
    monkeypatch.setattr(_surface, "_surface", None)
    yield
    monkeypatch.setattr(_surface, "_surface", None)


def test_zipf_frequency_known_word():
    assert wrodfreq.zipf_frequency("de") == 7.71


def test_zipf_frequency_lang_argument_accepted_and_ignored():
    assert wrodfreq.zipf_frequency("de", "ro") == 7.71
    assert wrodfreq.zipf_frequency("de", "en") == 7.71  # spec §10.1: ignored, not validated


def test_zipf_frequency_unknown_word_is_zero():
    assert wrodfreq.zipf_frequency("cuvântcarenuexista") == 0.0


def test_zipf_frequency_respects_minimum_floor():
    assert wrodfreq.zipf_frequency("cuvântcarenuexista", minimum=2.0) == 2.0
    assert wrodfreq.zipf_frequency("de", minimum=10.0) == 10.0  # floor, not a cap


def test_word_frequency_matches_the_inverse_of_zipf():
    # zipf = log10(freq * 1e9)  =>  freq = 10**zipf / 1e9
    assert wrodfreq.word_frequency("de") == pytest.approx(10**7.71 / 1e9)


def test_word_frequency_unknown_word_is_zero():
    assert wrodfreq.word_frequency("cuvântcarenuexista") == 0.0


def test_top_n_list_descending_by_zipf():
    assert wrodfreq.top_n_list(4) == ["de", "și", "un", "birjă"]


def test_top_n_list_n_is_the_first_argument():
    # spec §10.1's own example: top_n_list(1000) — n first, unlike wordfreq's
    # own top_n_list(lang, n, ...), since this package has one language.
    assert wrodfreq.top_n_list(1) == ["de"]


def test_top_n_list_ascii_only_excludes_diacritics_and_still_fills_n():
    # "și" outranks "un" (7.38 vs 6.89) but has a diacritic — ascii_only must
    # skip past it to "un" rather than under-return, which a naive
    # over-fetch-by-a-fixed-multiple approach could do on a word list this
    # heavy in diacritics (see _surface.py's top_n).
    assert wrodfreq.top_n_list(2, ascii_only=True) == ["de", "un"]


def test_frequency_detail_known_word():
    detail = wrodfreq.frequency_detail("birjă")
    assert detail.zipf == 1.78
    assert detail.n_reliable == 4
    assert detail.n_attesting == 4
    assert detail.n_sources == 5
    assert detail.spread == 0.89


def test_frequency_detail_unknown_word_is_none():
    assert wrodfreq.frequency_detail("cuvântcarenuexista") is None


def test_by_source_reports_none_for_abstaining_source_not_zero():
    # spec §8.1: a source that never saw a word abstains — None is not 0.0.
    result = wrodfreq.by_source("birjă")
    assert result["eu"] is None
    assert result["news"] == 1.22
    assert result["web"] == 1.77


def test_by_source_unknown_word_is_none():
    assert wrodfreq.by_source("cuvântcarenuexista") is None


def test_by_source_lazily_loads_the_second_file_only_when_called():
    data = _surface.load()
    assert data._by_source is None  # not loaded yet — only zipf_frequency called so far
    wrodfreq.zipf_frequency("de")
    assert data._by_source is None
    wrodfreq.by_source("de")
    assert data._by_source is not None


def test_lemma_frequency_degrades_gracefully_without_a_lemma_data_file():
    # No lemma data file shipped yet (DEX licence question unresolved) —
    # must not crash, matches zipf_frequency's own "0.0 for unknown" shape.
    assert wrodfreq.lemma_frequency("înmărmuri") == 0.0


def test_build_info_reports_the_panel():
    info = wrodfreq.build_info()
    assert info["sources"] == SOURCES
    assert info["built"] == "2026-01-01"
    assert info["version"] == wrodfreq.__version__
