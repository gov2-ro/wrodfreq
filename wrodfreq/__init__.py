"""wROdfreq — Romanian word frequencies on the Zipf scale, drop-in compatible
with `wordfreq` (spec §10.1): change the import, keep the code.

    from wrodfreq import zipf_frequency, word_frequency, top_n_list

    zipf_frequency('cuvânt', 'ro')   # 'ro' accepted and ignored, for compatibility
    zipf_frequency('cuvânt')         # the natural spelling — lang defaults to 'ro'

Extensions — clearly marked as such, not part of the wordfreq contract:

    from wrodfreq import frequency_detail, lemma_frequency, by_source, build_info

`zipf_frequency`/`word_frequency` return 0.0 for an unknown word, matching
wordfreq's own contract; `frequency_detail`/`by_source` return None instead —
two different signals for two different questions (spec §10.1).

`lemma_frequency` currently always returns 0.0: the DEX-derived paradigm
rollup isn't shipped in the data file pending an unresolved licensing
question (see build/build_package.py's docstring) — degrading gracefully
here, not crashing, is the one part of that decision that *is* made in code.
"""

from __future__ import annotations

from wrodfreq import _surface
from wrodfreq._surface import FrequencyDetail
from wrodfreq.tokenizer import normalize

__version__ = "0.1.0"

__all__ = [
    "zipf_frequency",
    "word_frequency",
    "top_n_list",
    "frequency_detail",
    "lemma_frequency",
    "by_source",
    "build_info",
    "FrequencyDetail",
]


def zipf_frequency(
    word: str, lang: str = "ro", wordlist: str = "best", minimum: float = 0.0
) -> float:
    """Zipf-scale frequency of `word`. 0.0 for a word this table never saw.

    `lang` and `wordlist` are accepted and ignored — wROdfreq has exactly one
    language and one wordlist. `minimum` is a floor on the *returned* value,
    same as wordfreq's: the result is never less than `minimum`, even for an
    unknown word.
    """
    data = _surface.load()
    i = data.row(normalize(word))
    zipf = data.zipf(i) if i is not None else 0.0
    return max(zipf, minimum)


def word_frequency(
    word: str, lang: str = "ro", wordlist: str = "best", minimum: float = 0.0
) -> float:
    """Linear-scale frequency of `word` (fraction of all tokens). 0.0 if unknown.

    `minimum` floors the *linear* result, matching wordfreq's own semantics
    (its `zipf_frequency` floors the zipf value instead — these are not the
    same floor restated in two scales).
    """
    z = zipf_frequency(word, lang, wordlist)
    freq = 0.0 if z == 0.0 else 10**z / 1e9
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


def lemma_frequency(word: str, lang: str = "ro") -> float:
    """Zipf-scale frequency of `word`'s whole inflectional paradigm.

    Always 0.0 right now — see this module's docstring. Once the DEX
    licensing question is resolved and a lemma data file ships, this
    degrades to `zipf_frequency`'s own behavior for a word outside the
    paradigm map, not a crash — that's the contract this stub already
    honors.
    """
    return 0.0


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
