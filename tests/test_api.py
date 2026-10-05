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


LEMMAS = {
    # lemma -> (paradigm zipf, n_forms, headword zipf or None, family_ratio or None)
    "birjă": (2.40, 4, 1.78, 3.5),
    "tinereță": (1.74, 6, None, None),
}


def _lemma_payload() -> dict:
    names = sorted(LEMMAS)
    q = lambda v: -1 if v is None else round(v * SCALE)
    return {
        "format_version": 1,
        "zipf_scale": SCALE,
        "lemma_count": len(names),
        "lemmas": names,
        "zipf": [q(LEMMAS[n][0]) for n in names],
        "n_forms": [LEMMAS[n][1] for n in names],
        "zipf_headword": [q(LEMMAS[n][2]) for n in names],
        "family_ratio": [q(LEMMAS[n][3]) for n in names],
    }


@pytest.fixture(autouse=True)
def fake_data_files(tmp_path, monkeypatch):
    surface, by_src = _make_payloads()
    surface_path = tmp_path / "ro_surface.msgpack.xz"
    by_source_path = tmp_path / "ro_by_source.msgpack.xz"
    surface_path.write_bytes(lzma.compress(msgpack.packb(surface, use_bin_type=True)))
    by_source_path.write_bytes(lzma.compress(msgpack.packb(by_src, use_bin_type=True)))

    lemma_path = tmp_path / "ro_lemma.msgpack.xz"
    lemma_path.write_bytes(lzma.compress(msgpack.packb(_lemma_payload(), use_bin_type=True)))
    monkeypatch.setattr(_surface, "LEMMA_PATH", lemma_path)
    monkeypatch.setattr(_surface, "_lemmas", None)
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


def test_lemma_frequency_reads_the_paradigm_rollup():
    assert wrodfreq.lemma_frequency("birjă") == 2.40
    assert wrodfreq.lemma_frequency("Birjă") == 2.40  # normalized like every other lookup


def test_lemma_detail_fields_and_missing_values():
    d = wrodfreq.lemma_detail("birjă")
    assert (d.zipf, d.n_forms, d.zipf_headword, d.family_ratio) == (2.40, 4, 1.78, 3.5)
    d = wrodfreq.lemma_detail("tinereță")
    assert d.zipf_headword is None and d.family_ratio is None
    assert wrodfreq.lemma_detail("de") is None


def test_lemma_frequency_falls_back_to_the_surface_value_for_a_non_lemma():
    assert wrodfreq.lemma_frequency("de") == wrodfreq.zipf_frequency("de") == 7.71
    assert wrodfreq.lemma_frequency("cuvântcarenuexista") == 0.0


def test_lemma_frequency_degrades_gracefully_without_a_lemma_data_file(monkeypatch, tmp_path):
    monkeypatch.setattr(_surface, "LEMMA_PATH", tmp_path / "absent.msgpack.xz")
    monkeypatch.setattr(_surface, "_lemmas", None)
    assert wrodfreq.lemma_detail("birjă") is None
    assert wrodfreq.lemma_frequency("birjă") == wrodfreq.zipf_frequency("birjă") == 1.78


def test_build_info_reports_the_panel():
    info = wrodfreq.build_info()
    assert info["sources"] == SOURCES
    assert info["built"] == "2026-01-01"
    assert info["version"] == wrodfreq.__version__


# ---------------------------------------------------------------------------
# ADR-001 / Brief B5 — zipf_frequency and word_frequency tokenize their argument.
# Mechanism (wordfreq 3.1.1 `_word_frequency`): tokenize, any missing token ->
# `minimum`, otherwise combine 1/f = sum(1/f_i) in the LINEAR domain, round to
# 2 decimals. These began as Brief B3's skipped proposal; two cases were changed
# against it (see the notes on `_harmonic_zipf` and the edge-punctuation test).
# ---------------------------------------------------------------------------
import math  # noqa: E402


def _harmonic_zipf(*zipfs: float) -> float:
    # B3's original had a stray `+ 9`: log10 of the harmonic combination of the
    # 10**z values is already a Zipf value, so it never matched wordfreq's.
    return round(math.log10(1 / sum(1 / 10**z for z in zipfs)), 2)


@pytest.mark.parametrize("text", ["-de", "(de)", "'de'", "  de  ", "DE", "de-"])
def test_b3_edge_punctuation_and_case_resolve_to_the_single_token(text):
    assert wrodfreq.zipf_frequency(text) == wrodfreq.zipf_frequency("de")


def test_edge_hyphen_resolves_to_the_token_it_wraps():
    # B3's parametrization compared "spune-" with "de"; it meant "the same word
    # without the hyphen", which needs a word the fixture actually holds.
    assert wrodfreq.zipf_frequency("birjă-") == wrodfreq.zipf_frequency("birjă") == 1.78


def test_b3_multi_token_is_harmonic_combination_not_first_or_min():
    assert wrodfreq.zipf_frequency("de un") == _harmonic_zipf(7.71, 6.89)
    assert wrodfreq.zipf_frequency("de,un") == wrodfreq.zipf_frequency("de un")


def test_b3_any_unknown_token_makes_the_whole_phrase_unknown():
    assert wrodfreq.zipf_frequency("de cuvântcarenuexista") == 0.0
    assert wrodfreq.zipf_frequency("de cuvântcarenuexista", minimum=2.0) == 2.0


@pytest.mark.parametrize("text", ["", "   ", "...", "!?"])
def test_b3_zero_tokens_is_zero_not_none(text):
    # Matches wordfreq. zipf_frequency stays 0.0; frequency_detail stays None.
    assert wrodfreq.zipf_frequency(text) == 0.0
    assert wrodfreq.zipf_frequency(text, minimum=2.0) == 2.0
    assert wrodfreq.frequency_detail(text) is None


def test_b3_word_frequency_combines_in_linear_domain():
    expected = 1 / (1 / (10**7.71 / 1e9) + 1 / (10**6.89 / 1e9))
    assert wrodfreq.word_frequency("de un") == pytest.approx(expected, rel=0.02)
    assert wrodfreq.word_frequency("de un", minimum=1.0) == 1.0


@pytest.mark.parametrize("text", ["café", "a1b", "de 123", "123", "de1"])
def test_unconsumed_letters_or_digits_return_minimum_not_a_fragment_answer(text):
    # ADR-001 decisions 3 and 4: never combine surviving fragments, and
    # numerals are not modelled (wordfreq answers 3.92 for "123"; we do not).
    assert wrodfreq.zipf_frequency(text) == 0.0
    assert wrodfreq.zipf_frequency(text, minimum=2.0) == 2.0
    assert wrodfreq.word_frequency(text) == 0.0


def test_zipf_and_detail_are_two_signals_not_one():
    # ADR-001 decision 5: zipf_frequency answers as wordfreq would; the
    # extensions stay exact single-row lookup and keep returning None.
    assert wrodfreq.zipf_frequency("birjă-") == 1.78
    assert wrodfreq.frequency_detail("birjă-") is None
    assert wrodfreq.by_source("birjă-") is None
    assert wrodfreq.frequency_detail("de un") is None
