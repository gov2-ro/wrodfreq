"""Tests for wrodfreq.zipf — the abstention rule, spec §8.1."""

import math

from wrodfreq.zipf import MIN_OCC_PER_SOURCE, is_reliable, source_zipf_floor, zipf_from_counts


def test_zipf_from_counts_matches_definition():
    # zipf = log10(occurrences / total_tokens * 1e9)
    assert zipf_from_counts(1_000_000, 1_000_000_000) == math.log10(1_000_000 / 1_000_000_000 * 1e9)


def test_is_reliable_floor():
    assert is_reliable(MIN_OCC_PER_SOURCE) is True
    assert is_reliable(MIN_OCC_PER_SOURCE - 1) is False


def test_source_zipf_floor_scales_with_corpus_size():
    # spec §8.1: five occurrences in an 80M-token corpus is a different claim
    # than five in a 17B-token one — the floor must be lower for the bigger corpus.
    small_floor = source_zipf_floor(80_000_000)
    large_floor = source_zipf_floor(17_000_000_000)
    assert large_floor < small_floor


def test_source_zipf_floor_is_the_zipf_at_exactly_min_occ():
    total_tokens = 80_000_000
    assert source_zipf_floor(total_tokens) == zipf_from_counts(MIN_OCC_PER_SOURCE, total_tokens)
