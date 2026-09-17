"""Tests for wrodfreq.tokenizer — THE tokenizer, spec §3.2 and §4.

There is exactly one tokenizer module, imported by every ingester (spec §4:
"the tokenizer is copy-pasted into four files in oțios ... in the new repo it
is one module"). That makes a cross-ingester byte-identical-token-stream test
structurally guaranteed rather than something to assert at runtime — this file
pins the one implementation's behavior instead.
"""

import random
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------------------
# Doubled hyphens are a dash, not an internal hyphen (fixed 2026-09-18)
# ---------------------------------------------------------------------------
#
# Found by reading validate.py's check-5 spread report by eye, which is what
# that check is for: `baden-w` in the top-100 by spread was the thread. The
# token regex's inner class permits consecutive hyphens, so dash typography
# (overwhelmingly subtitles) reached _split_elisions as a single token and
# the naive `.split("-")` turned the resulting empty parts into malformed
# tokens — including an empty-string token that shipped in the data file,
# where zipf_frequency('') answered 2.34 instead of wordfreq's 0.0.

DASH_CASES = [
    ("într--o", ["într", "o"]),          # elision + dash: was ["într", "", "o"]
    ("n--am", ["n", "am"]),
    ("într--adevăr", ["într", "adevăr"]),  # was ["într", "-adevăr"]
    ("spune--mi", ["spune", "mi"]),        # was ["spune-", "mi"]
    ("eu--eu", ["eu", "eu"]),              # subtitle repetition, not a compound
    ("sunt--sunt", ["sunt", "sunt"]),
    ("spider--man", ["spider", "man"]),
    ("a---b", ["a", "b"]),                 # any run of hyphens, not just two
    ("xn--urlaub-in-rumnien", ["xn", "urlaub-in-rumnien"]),
]


@pytest.mark.parametrize("text,expected", DASH_CASES)
def test_doubled_hyphen_is_a_token_boundary(text, expected):
    assert tokenize(text) == expected


def test_dash_handling_does_not_disturb_single_hyphens():
    # The cases the 2026-09-17 elision rule exists to get right must be
    # untouched by the dash fix: compounds stay joined, elisions still split.
    assert tokenize("mass-media") == ["mass-media"]
    assert tokenize("site-ul") == ["site-ul"]
    assert tokenize("cluj-napoca") == ["cluj-napoca"]
    assert tokenize("într-o") == ["într", "o"]
    assert tokenize("avut-o") == ["avut", "o"]
    assert tokenize("al ii-a") == ["al", "ii-a"]


# The invariants the malformed rows in `merged` violated. Asserted over every
# fixture in this module plus adversarial hyphen/apostrophe soup, because a
# token that is empty or hyphen-edged is not a word in any corpus and must
# never reach `source_counts` — one did, and shipped.
_INVARIANT_INPUTS = [text for text, _ in DASH_CASES] + [
    "", "-", "--", "---", "-a", "a-", "-a-", "a-b", "a--b-", "--a--",
    "într-o casă -- și n--am spus", "s--a dus", "-- -- --", "a'-'b",
    "mass--media", "prim---ministru", "d--na", "l--a", "i--a", "x--lea",
    "Într--O CASĂ", "ăîâșț--ăîâșț", "'-'", "a-'-b", "spune-mi -- acum",
]


@pytest.mark.parametrize("text", _INVARIANT_INPUTS)
def test_tokenizer_output_invariants(text):
    for token in tokenize(text):
        assert token, f"empty token from {text!r}"
        assert not token.startswith("-"), f"leading hyphen in {token!r} from {text!r}"
        assert not token.endswith("-"), f"trailing hyphen in {token!r} from {text!r}"
        assert "--" not in token, f"doubled hyphen in {token!r} from {text!r}"


def test_tokenizer_output_invariants_on_random_hyphen_soup():
    rng = random.Random(0)
    alphabet = "abcăîșț-'"
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 14)))
        for token in tokenize(text):
            assert token and not token.startswith("-") and not token.endswith("-")
            assert "--" not in token, (text, token)
