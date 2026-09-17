"""THE tokenizer. One copy, imported by every ingester — never duplicated.

Ported from oțios's `process_culturax.py:81-84` (via `dump_parser.py:31-38` for
`normalize()`), with one deliberate change: the `len(t) > 2` filter is dropped.
Romanian's highest-frequency words are 1-2 characters (`de`, `la`, `cu`, `o`, `a`,
`nu`, `se`) — keeping that filter understates the denominator and inflates every
Zipf value derived from it. See docs/wrodfreq-spec.md §3.2.

The character class matches only Romanian letters plus internal hyphen/apostrophe,
so digit runs never match in the first place — numerals are excluded from both the
numerator and the denominator by construction, not by a separate filter.

**Elision splitting** (added 2026-09-17, see docs/BACKLOG.md's check-2 finding,
2026-09-08): a bare hyphen-keeping regex conflates two different things Romanian
writes with an internal hyphen —

    elision       într-o, dintr-un, n-am, s-a, mi-a, avut-o, du-te, spune-mi,
                  referindu-se — a reduced preposition or clitic pronoun on one
                  side of the hyphen. `într-o` should count as two occurrences,
                  one of `într` and one of `o` — not one occurrence of a rare
                  compound token that fragments both words' true frequency.
    not elision   mass-media, prim-ministru, on-line, cluj-napoca (compounds,
                  proper nouns), site-ul, show-ului (a Romanian noun suffix on
                  an undeclinable foreign stem) — one lexical unit, must stay
                  joined; splitting `site-ul` into `site`+`ul` would manufacture
                  a nonsense token, since the suffix alone isn't a word.

The rule below distinguishes these with a closed set of Romanian grammatical
particles (reduced prepositions, clitic pronouns, the auxiliary "avea"/"a fi"),
built empirically from wROdfreq's own top-200 hyphenated words by frequency —
not guessed from grammar rules alone. One residual ambiguity, accepted rather
than solved: `v` and `l` (and `i`, `m`, `c`) are simultaneously common Romanian
clitics (`v-a`, `l-a`) *and* valid Roman-numeral letters, so `v-lea` (Roman
ordinal "the 5th") splits like an elision would — rare enough in practice that
the dominant elision reading is worth keeping for the other ~99% of `v-`/`l-`
words. Multi-letter Roman numerals (`ii-a`, `xii-lea`) and the bare numeral `x`
don't share this ambiguity and are excluded explicitly — see `_is_roman_ordinal`.
"""

from __future__ import annotations

import re
import unicodedata

_TOKEN_RE = re.compile(r"[a-zăâîșț](?:[a-zăâîșț\-']*[a-zăâîșț])?")

# Reduced prepositions and clitics that indicate elision when they're the
# LEFT side of a hyphen — matched regardless of what follows.
_ELISION_LEFT = frozenset({
    "într", "intr", "dintr", "printr",  # prepositions (+ no-diacritic spelling)
    "s", "l", "i", "m", "n", "v",       # single-letter reduced clitics
    "ne", "le", "mi", "ți", "ti", "și", "si", "nu", "de", "a", "să", "sa", "te", "ce",
})

# Reduced forms of "avea"/"a fi" and clitic pronouns that indicate elision
# when they're the RIGHT side of a hyphen — matched regardless of what
# precedes, except for the Roman-numeral-ordinal guard below.
_ELISION_RIGHT = frozenset({
    "a", "am", "ai", "ar", "aș", "as", "avem", "ați", "ati", "au", "are", "e",
    "o", "l", "i", "le", "mi", "ți", "ti", "și", "si", "se", "ne", "vă", "mă", "te", "n",
})

_ROMAN_NUMERAL_RE = re.compile(r"^[ivxlcdm]+$")
# Single-letter clitics that happen to also be valid Roman numerals. The
# elision reading dominates in real text for these (v-a, l-a, i-a, m-a, c-a
# are all common), so they're exempted from the ordinal guard — only longer
# Roman numerals (ii, xii, ...) and the bare numeral x don't share that
# ambiguity in practice.
_ROMAN_LOOKALIKE_CLITICS = frozenset({"i", "v", "l", "m", "c"})


def _is_roman_ordinal(prev: str, cur: str) -> bool:
    """True for a Roman-numeral ordinal like `ii-a` ("the 2nd") or `xii-lea`
    ("the 12th") — not elision, even though `cur` matches `_ELISION_RIGHT`."""
    return (
        cur in ("a", "lea")
        and _ROMAN_NUMERAL_RE.match(prev) is not None
        and prev not in _ROMAN_LOOKALIKE_CLITICS
    )


def _split_elisions(word: str) -> list[str]:
    """Split a hyphenated word at every elision boundary it has.

    Compounds, proper nouns, and loanword+suffix constructions never match
    `_ELISION_LEFT` or `_ELISION_RIGHT` on either side of their hyphen(s) and
    pass through unchanged, hyphen intact.
    """
    if "-" not in word:
        return [word]
    parts = word.split("-")
    result = [parts[0]]
    for i in range(1, len(parts)):
        prev, cur = parts[i - 1], parts[i]
        splits_here = prev in _ELISION_LEFT or (
            cur in _ELISION_RIGHT and not _is_roman_ordinal(prev, cur)
        )
        if splits_here:
            result.append(cur)
        else:
            result[-1] = f"{result[-1]}-{cur}"
    return result


def normalize(text: str) -> str:
    """Canonical Romanian normalization: lower -> cedilla-to-comma (ş->ș, ţ->ț) -> NFC."""
    return unicodedata.normalize(
        "NFC", text.lower().replace("ş", "ș").replace("ţ", "ț")
    )


def tokenize(text: str) -> list[str]:
    """Split normalized text into Romanian word tokens, short words included."""
    tokens = _TOKEN_RE.findall(normalize(text))
    if not any("-" in t for t in tokens):
        return tokens
    out: list[str] = []
    for t in tokens:
        out.extend(_split_elisions(t))
    return out
