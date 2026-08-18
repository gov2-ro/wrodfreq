"""Tests for wrodfreq.tokenizer — THE tokenizer, spec §3.2 and §4.

There is exactly one tokenizer module, imported by every ingester (spec §4:
"the tokenizer is copy-pasted into four files in oțios ... in the new repo it
is one module"). That makes a cross-ingester byte-identical-token-stream test
structurally guaranteed rather than something to assert at runtime — this file
pins the one implementation's behavior instead.
"""

from pathlib import Path

from wrodfreq.tokenizer import normalize, tokenize

FIXTURE = (Path(__file__).parent / "fixtures" / "tokenizer_fixture.txt").read_text()

EXPECTED_TOKENS = [
    "țara", "aceasta", "are", "de", "milioane", "de", "oameni", "și", "un",
    "laptop", "pe", "cap", "de", "locuitor", "dintr-un", "motiv", "sau",
    "altul", "m-a", "întrebat", "ce", "e", "cu", "covid", "și", "cu",
    "selfie-urile", "de", "pe", "internet",
]


def test_normalize_lowercases():
    assert normalize("ABC") == "abc"


def test_normalize_maps_legacy_cedilla_diacritics():
    assert normalize("ş ţ") == "ș ț"


def test_normalize_is_nfc():
    # combining-form ș (s + combining comma below) must compose to the
    # single-codepoint precomposed form used everywhere else in the pipeline.
    decomposed = "ș"
    assert normalize(decomposed) == "ș"


def test_tokenize_keeps_short_function_words():
    # spec §3.2: de, la, cu, o, a, nu, se must survive — this is the
    # denominator-honesty requirement the len(t) > 2 filter used to break.
    assert tokenize("de la cu o a nu se") == ["de", "la", "cu", "o", "a", "nu", "se"]


def test_tokenize_excludes_digits_from_both_sides():
    assert tokenize("anul 2023 a fost bun") == ["anul", "a", "fost", "bun"]


def test_tokenize_open_vocabulary_neologisms():
    # spec §3.1: no DEX filter lives in the tokenizer — laptop/selfie/covid/
    # clujean must come through untouched, same as any dictionary word.
    assert tokenize("laptop selfie covid clujean") == [
        "laptop", "selfie", "covid", "clujean",
    ]


def test_tokenize_keeps_internal_hyphen_and_apostrophe():
    assert tokenize("dintr-un s-a m-a") == ["dintr-un", "s-a", "m-a"]


def test_tokenize_drops_bare_punctuation():
    assert tokenize("cuvânt, cuvânt. cuvânt!") == ["cuvânt", "cuvânt", "cuvânt"]


def test_tokenize_fixture_paragraph():
    assert tokenize(FIXTURE) == EXPECTED_TOKENS
