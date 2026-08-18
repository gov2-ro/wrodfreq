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

MIN_OCC_PER_SOURCE = 5


def zipf_from_counts(occurrences: int, total_tokens: int) -> float:
    """Zipf value for `occurrences` hits out of `total_tokens` alphabetic tokens."""
    return math.log10(occurrences / total_tokens * 1e9)


def is_reliable(occurrences: int, min_occ: int = MIN_OCC_PER_SOURCE) -> bool:
    """Whether a source's count for a word clears its reliability floor."""
    return occurrences >= min_occ


def source_zipf_floor(total_tokens: int, min_occ: int = MIN_OCC_PER_SOURCE) -> float:
    """The Zipf value MIN_OCC_PER_SOURCE occurrences corresponds to in a source
    of this size — store this in `sources.zipf_floor` so the abstention line is
    inspectable per source, and re-derive it whenever a corpus is (re)ingested."""
    return zipf_from_counts(min_occ, total_tokens)
