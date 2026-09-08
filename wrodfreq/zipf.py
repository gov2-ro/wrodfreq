"""Zipf math and per-source reliability floors. Pure functions, no I/O.

See docs/wrodfreq-spec.md §8.1: a source's estimate for a word only counts once
the word clears MIN_OCC_PER_SOURCE occurrences *in that source*. Below the floor
the source abstains (zipf=None) rather than reporting a zero — a zero is a claim
("rare"), but a small corpus that never saw a word is usually just small.

The floor is derived from each source's own total_tokens, never hardcoded: five
occurrences in an 80M-token corpus is a different claim from five in a 17B-token
one.
"""

from __future__ import annotations

import math
import statistics

MIN_OCC_PER_SOURCE = 5
MIN_SOURCES_TO_TRIM = 5


def zipf_from_counts(occurrences: int | float, total_tokens: int) -> float:
    """Zipf value for `occurrences` hits out of `total_tokens` alphabetic tokens.

    `occurrences` is a float when called on a lemma's disambiguated,
    share-split occurrence total (build_lemma_layer.py) rather than a raw
    per-source surface-form count.
    """
    return math.log10(occurrences / total_tokens * 1e9)


def is_reliable(occurrences: int | float, min_occ: int = MIN_OCC_PER_SOURCE) -> bool:
    """Whether a source's count for a word clears its reliability floor."""
    return occurrences >= min_occ


def source_zipf_floor(total_tokens: int, min_occ: int = MIN_OCC_PER_SOURCE) -> float:
    """The Zipf value MIN_OCC_PER_SOURCE occurrences corresponds to in a source
    of this size — store this in `sources.zipf_floor` so the abstention line is
    inspectable per source, and re-derive it whenever a corpus is (re)ingested."""
    return zipf_from_counts(min_occ, total_tokens)


def merge_zipf(reliable_zipfs: list[float]) -> float:
    """The trimmed mean across a word's reliable per-source Zipf values (spec §8.2).

    >=5 reliable sources: drop the max and min, mean the rest — the "figure
    skating" trim that stops any single corpus (CulturaX is ~200x the next
    source) from dominating. Below 5, trimming would leave too few values to
    be a real average — 3 sources trimmed to 1 is "pick the middle corpus",
    strictly worse than the plain mean `wordfreq`'s own Romanian list is stuck
    with — so 1-4 reliable sources get a plain, untrimmed mean instead.

    Uses `statistics.mean`, not a naive float sum: it sums via exact `Fraction`
    arithmetic internally, so the result doesn't depend on the order the
    reliable-source rows were read in — required for validate.py's idempotence
    check (spec §11.6).

    Never call this with an empty list: spec §8.2 says zero reliable sources
    means the word is below the table's floor and is *omitted* from `merged`
    entirely, not given a zipf of 0 (a 0 would be a false "this word is rare"
    claim — see §8.1's abstention rule, the same reasoning one level up).
    """
    if not reliable_zipfs:
        raise ValueError("merge_zipf requires at least one reliable source's zipf value")
    if len(reliable_zipfs) >= MIN_SOURCES_TO_TRIM:
        trimmed = sorted(reliable_zipfs)[1:-1]
        return statistics.mean(trimmed)
    return statistics.mean(reliable_zipfs)
