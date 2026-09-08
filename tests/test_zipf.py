"""Tests for wrodfreq.zipf — the abstention rule (spec §8.1) and the merge (§8.2)."""

import math
import random

import pytest

from wrodfreq.zipf import (
    MIN_OCC_PER_SOURCE,
    is_reliable,
    merge_zipf,
    source_zipf_floor,
    zipf_from_counts,
)


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


def test_merge_zipf_rejects_empty_input():
    # spec §8.2: 0 reliable sources means the word is below the table's floor
    # and is omitted from `merged` entirely — never given a fabricated zipf.
    with pytest.raises(ValueError):
        merge_zipf([])


def test_merge_zipf_single_source_is_that_value():
    assert merge_zipf([6.5]) == 6.5


def test_merge_zipf_two_to_four_sources_is_a_plain_mean():
    assert merge_zipf([6.0, 7.0]) == 6.5
    assert merge_zipf([6.0, 6.0, 9.0]) == pytest.approx(7.0)
    assert merge_zipf([5.0, 6.0, 7.0, 8.0]) == 6.5


def test_merge_zipf_five_or_more_drops_max_and_min():
    # spec §8.2: trim only at >=5 — dropping max/min from exactly 3 would leave
    # one value ("pick the middle corpus"), so this branch must not fire below 5.
    values = [1.0, 5.0, 6.0, 7.0, 100.0]  # drop 1.0 and 100.0
    assert merge_zipf(values) == pytest.approx(6.0)


def test_merge_zipf_trim_ignores_input_order():
    # the trim is by *value* (sorted), not by the order sources were read in.
    assert merge_zipf([1.0, 5.0, 6.0, 7.0, 100.0]) == merge_zipf([100.0, 7.0, 6.0, 5.0, 1.0])


def test_merge_zipf_is_order_independent_for_idempotence():
    # validate.py's idempotence check (spec §11.6) requires this: the same
    # multiset of reliable-source zipf values must merge to the same result
    # regardless of the order source_zipf rows were read in.
    values = [6.1, 6.9, 7.4, 6.3]
    shuffled = values[:]
    random.shuffle(shuffled)
    assert merge_zipf(values) == merge_zipf(shuffled)
