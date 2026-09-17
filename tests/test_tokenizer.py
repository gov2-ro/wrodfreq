"""Tests for wrodfreq.tokenizer — THE tokenizer, spec §3.2 and §4.

There is exactly one tokenizer module, imported by every ingester (spec §4:
"the tokenizer is copy-pasted into four files in oțios ... in the new repo it
is one module"). That makes a cross-ingester byte-identical-token-stream test
structurally guaranteed rather than something to assert at runtime — this file
pins the one implementation's behavior instead.
"""

from pathlib import Path

from wrodfreq.tokenizer import _split_elisions, normalize, tokenize

FIXTURE = (Path(__file__).parent / "fixtures" / "tokenizer_fixture.txt").read_text()

EXPECTED_TOKENS = [
    "țara", "aceasta", "are", "de", "milioane", "de", "oameni", "și", "un",
    "laptop", "pe", "cap", "de", "locuitor", "dintr", "un", "motiv", "sau",
    "altul", "m", "a", "întrebat", "ce", "e", "cu", "covid", "și", "cu",
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


def test_tokenize_splits_elision_hyphens():
    # spec finding 2026-09-08 (docs/BACKLOG.md): a hyphen-preserving tokenizer
    # fragments într-o/s-a/m-a's true frequency across many rare compound
    # variants instead of crediting într/o/s/a/m — see wrodfreq/tokenizer.py's
    # module docstring for the full elision-vs-compound reasoning.
    assert tokenize("dintr-un s-a m-a") == ["dintr", "un", "s", "a", "m", "a"]


def test_tokenize_keeps_genuine_compound_hyphens():
    # compounds, proper nouns, and loanword+suffix constructions never match
    # either elision set and must stay joined — splitting "site-ul" would
    # manufacture a nonsense "ul" token.
    assert tokenize("mass-media site-ul cluj-napoca bine-crescut") == [
        "mass-media", "site-ul", "cluj-napoca", "bine-crescut",
    ]


def test_split_elisions_apostrophe_preserved():
    # apostrophes are a separate mechanism (stress marks, elided vowels like
    # "dintr-o", "s-a") from the hyphen-splitting logic — unaffected by it.
    assert _split_elisions("n-am") == ["n", "am"]


def test_split_elisions_left_side_match():
    assert _split_elisions("într-o") == ["într", "o"]
    assert _split_elisions("dintr-un") == ["dintr", "un"]


def test_split_elisions_right_side_match():
    # the left-hand side here is an open-class verb participle/imperative,
    # not a clitic — only the right-hand clitic pronoun triggers the split.
    assert _split_elisions("avut-o") == ["avut", "o"]
    assert _split_elisions("du-te") == ["du", "te"]
    assert _split_elisions("spune-mi") == ["spune", "mi"]


def test_split_elisions_recurses_through_chains():
    assert _split_elisions("s-a-ntâmplat") == ["s", "a", "ntâmplat"]


def test_split_elisions_leaves_compounds_alone():
    assert _split_elisions("mass-media") == ["mass-media"]
    assert _split_elisions("prim-ministru") == ["prim-ministru"]
    assert _split_elisions("on-line") == ["on-line"]


def test_split_elisions_leaves_loanword_suffix_alone():
    # "-ul"/"-ului"/"-uri" are noun-inflection suffixes, not clitic pronouns
    # — splitting "site-ul" would leave a bare "ul" that isn't a real word.
    assert _split_elisions("site-ul") == ["site-ul"]
    assert _split_elisions("show-ului") == ["show-ului"]
    assert _split_elisions("ong-uri") == ["ong-uri"]


def test_split_elisions_excludes_roman_numeral_ordinals():
    # "a II-a" ("the 2nd"), "al XII-lea" ("the 12th") are ordinals, not
    # elision, even though "-a"/"-lea" match the right-side elision set.
    assert _split_elisions("ii-a") == ["ii-a"]
    assert _split_elisions("xii-lea") == ["xii-lea"]
    assert _split_elisions("x-lea") == ["x-lea"]


def test_split_elisions_roman_lookalike_single_letters_still_split():
    # v/l/i/m/c are also valid Roman numerals, but the elision reading
    # dominates in real text for these (v-a, l-a are common; "the 5th"
    # written bare as "v-a" is not) — the ordinal guard doesn't apply to them.
    assert _split_elisions("v-a") == ["v", "a"]
    assert _split_elisions("l-a") == ["l", "a"]


def test_tokenize_drops_bare_punctuation():
    assert tokenize("cuvânt, cuvânt. cuvânt!") == ["cuvânt", "cuvânt", "cuvânt"]


def test_tokenize_fixture_paragraph():
    assert tokenize(FIXTURE) == EXPECTED_TOKENS
