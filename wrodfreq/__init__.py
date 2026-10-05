"""wROdfreq — Romanian word frequencies on the Zipf scale, drop-in compatible
with `wordfreq` (spec §10.1): change the import, keep the code.

    from wrodfreq import zipf_frequency, word_frequency, top_n_list

    zipf_frequency('cuvânt', 'ro')   # 'ro' accepted and ignored, for compatibility
    zipf_frequency('cuvânt')         # the natural spelling — lang defaults to 'ro'

Extensions — clearly marked as such, not part of the wordfreq contract:

    from wrodfreq import frequency_detail, lemma_frequency, by_source, build_info

`zipf_frequency`/`word_frequency` tokenize their argument and return 0.0 (or
`minimum`) for an unknown word, matching wordfreq's own contract;
`frequency_detail`/`by_source` do an exact single-row lookup and return None
instead — two different signals for two different questions (spec §10.1,
ADR-001). `zipf_frequency('spune-')` is a number; `frequency_detail('spune-')`
is None. Divergences from wordfreq: numerals and words with non-Romanian
letters return `minimum` (see `zipf_frequency`).

`lemma_frequency(word)` rolls the word's whole DEX paradigm up into one Zipf
value. Pass the dictionary form (`înmărmuri`, not `înmărmurit`). A word that is
not a DEX lemma, or a build without the lemma file, answers with the plain
`zipf_frequency` value instead — never a crash.
"""

from __future__ import annotations

from wrodfreq import _surface
from wrodfreq._surface import FrequencyDetail, LemmaDetail
import math

from wrodfreq.tokenizer import _TOKEN_RE, normalize, tokenize

__version__ = "0.2.0"

__all__ = [
    "zipf_frequency",
    "word_frequency",
    "top_n_list",
    "frequency_detail",
    "lemma_frequency",
    "by_source",
    "build_info",
    "lemma_detail",
    "FrequencyDetail",
    "LemmaDetail",
]


def _unconsumed(text: str) -> bool:
    """True if `text` has a letter or digit the tokenizer did not consume
    (`café`, `a1b`, `de 123`, a bare `123`). ADR-001 decisions 3 and 4."""
    return any(c.isalnum() for c in _TOKEN_RE.sub(" ", normalize(text)))


def _combined_zipfs(text: str) -> list[float] | None:
    """The per-token Zipf values of `text`, or None if the whole string must
    answer `minimum`: any unconsumed letter/digit, any token this table never
    saw. An empty list means zero tokens (also `minimum`, but the caller can
    tell them apart if it needs to — it does not).
    """
    if _unconsumed(text):
        return None
    data = _surface.load()
    out: list[float] = []
    for token in tokenize(text):
        i = data.row(token)
        if i is None:
            return None
        out.append(data.zipf(i))
    return out


def zipf_frequency(
    word: str, lang: str = "ro", wordlist: str = "best", minimum: float = 0.0
) -> float:
    """Zipf-scale frequency of `word`, as wordfreq would answer it (ADR-001).

    The argument is tokenized with the same tokenizer that built the table, so
    `'Spune-'`, `'(de)'` and `'  DE '` all answer for the one token they hold.
    A multi-token string combines harmonically, `1/f = sum(1/f_i)`, so a phrase
    is always rarer than its rarest token. The result is `minimum` (default
    0.0) for: no tokens, any token this table never saw, and any string with a
    letter or digit the tokenizer did not consume — never a frequency
    fabricated from surviving fragments of a word we did not count.

    Two documented divergences from wordfreq, both deliberate:

    * **Numerals** (`'123'`) return `minimum`. wordfreq answers 3.92 from a
      digit-frequency model; this table excludes numerals from numerator and
      denominator by design (spec §3.2), and a word-frequency table is the
      wrong place for a digit model.
    * **Words with foreign diacritics** (`'café'`, `'Düsseldorf'`) return
      `minimum`, because the tokenizer only knows Romanian letters.

    **0.0 here does not mean `frequency_detail` is None, or vice versa.** This
    function answers "how common is this, as wordfreq would say"; the
    extensions answer "what does the table hold for this exact entry".
    `zipf_frequency('spune-')` is a real number while
    `frequency_detail('spune-')` is None, since no row is keyed `spune-`. Two
    different questions, two different signals (spec §10.1).

    `lang` and `wordlist` are accepted and ignored — wROdfreq has exactly one
    language and one wordlist. `minimum` is a floor on the *returned* value.
    """
    zipfs = _combined_zipfs(word)
    if not zipfs:
        return minimum
    if len(zipfs) == 1:
        return max(zipfs[0], minimum)
    # Harmonic combination in the linear domain: 1/f = sum(1/f_i) with
    # f_i = 10**(z_i - 9), so zipf = -log10(sum(10**-z_i)).
    combined = round(-math.log10(sum(10.0 ** -z for z in zipfs)), 2)
    return max(combined, minimum)


def word_frequency(
    word: str, lang: str = "ro", wordlist: str = "best", minimum: float = 0.0
) -> float:
    """Linear-scale frequency of `word` (fraction of all tokens). Tokenizes and
    combines exactly as `zipf_frequency` does (ADR-001, same divergences), but
    entirely in the linear domain — never through a rounded Zipf value.

    `minimum` floors the *linear* result, matching wordfreq's own semantics
    (its `zipf_frequency` floors the zipf value instead — these are not the
    same floor restated in two scales).
    """
    zipfs = _combined_zipfs(word)
    if not zipfs:
        return minimum
    freq = 1.0 / sum(1.0 / (10.0 ** z / 1e9) for z in zipfs)
    return max(freq, minimum)


def top_n_list(
    n: int, lang: str = "ro", wordlist: str = "best", ascii_only: bool = False
) -> list[str]:
    """The top `n` words by zipf, descending. `n` first — unlike wordfreq's
    own `top_n_list(lang, n, ...)`, since this package has only one language.
    """
    return _surface.load().top_n(n, ascii_only=ascii_only)


def frequency_detail(word: str) -> FrequencyDetail | None:
    """The corroboration fields behind a word's zipf (spec §8.3): how many
    sources measured it reliably, how many saw it at all, and how much they
    disagreed. None for a word not in the table — a different signal from
    `zipf_frequency`'s 0.0, which also means "never measured".
    """
    data = _surface.load()
    i = data.row(normalize(word))
    return data.detail(i) if i is not None else None


def lemma_detail(word: str) -> LemmaDetail | None:
    """Lemma-layer row for the dictionary form `word`: paradigm Zipf, number of
    forms, the citation form's own Zipf, and `family_ratio`. None if `word` is
    not a lemma in the table (or this build ships no lemma layer).
    """
    lemmas = _surface.load_lemmas()
    return lemmas.detail(normalize(word)) if lemmas is not None else None


def lemma_frequency(word: str, lang: str = "ro") -> float:
    """Zipf-scale frequency of the whole inflectional paradigm of `word`.

    Falls back to `zipf_frequency(word)` when `word` is not a lemma in the
    table, or when the build ships no lemma layer.
    """
    detail = lemma_detail(word)
    return detail.zipf if detail is not None else zipf_frequency(word)


def by_source(word: str) -> dict[str, float | None] | None:
    """Per-source zipf for `word`, e.g. {'web': 1.8, 'news': None, ...} —
    None for a source that abstained (spec §8.1), not a claim of zero. None
    for the whole word if it's not in the table at all.
    """
    data = _surface.load()
    i = data.row(normalize(word))
    return data.by_source_row(i) if i is not None else None


def build_info() -> dict:
    """Which corpus panel this build came from, and when (spec §10.3) — the
    field that lets anyone citing this table in a paper name the exact build.
    """
    data = _surface.load()
    return {
        "version": __version__,
        "sources": data.sources,
        "built": data.built,
    }
