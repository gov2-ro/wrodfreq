"""Tests for validate.py's `_concordance` — check 2's scoring primitive (spec §11.2).

The check gates the build on an exact count over ~570M word pairs, computed by a
Fenwick sweep rather than the naive double loop it stands in for. The optimisation is
the whole reason these tests exist: the sweep must agree with the definition on every
shape the real data can take, especially duplicate reference values (wordfreq's
Romanian list has only ~356 distinct Zipf values, so ties are the common case, not an
edge case) and duplicate values of our own.
"""

import importlib.util
import random
import sys
from pathlib import Path

import pytest

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))
_spec = importlib.util.spec_from_file_location("_validate", BUILD / "validate.py")
validate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate)

_concordance = validate._concordance


def brute_force(pairs, min_delta):
    """The definition check 2 is written against, stated as plainly as possible."""
    concordant = discordant = tied = 0
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            a, b = pairs[i], pairs[j]
            if abs(a[0] - b[0]) < min_delta:
                continue
            hi, lo = (a, b) if a[0] > b[0] else (b, a)
            if hi[1] > lo[1]:
                concordant += 1
            elif hi[1] < lo[1]:
                discordant += 1
            else:
                tied += 1
    return concordant, discordant, tied


def test_perfect_agreement():
    pairs = [(3.0, 3.0), (4.0, 4.5), (5.0, 5.2), (6.0, 7.1)]
    concordant, discordant, tied = _concordance(pairs, 0.3)
    assert (discordant, tied) == (0, 0)
    assert concordant == 6  # every pair separated, every one ordered our way


def test_perfect_disagreement():
    pairs = [(3.0, 7.1), (4.0, 5.2), (5.0, 4.5), (6.0, 3.0)]
    concordant, discordant, tied = _concordance(pairs, 0.3)
    assert (concordant, tied) == (0, 0)
    assert discordant == 6


def test_pairs_the_reference_does_not_separate_are_not_scored():
    # wordfreq rates these near-identically; how we order them is not evidence
    # either way, so they must not enter the denominator at all.
    pairs = [(3.01, 9.0), (3.05, 1.0), (3.09, 5.0)]
    assert _concordance(pairs, 0.3) == (0, 0, 0)


def test_separation_threshold_is_inclusive():
    # exactly min_delta apart counts — ">= min_delta", not "> min_delta".
    assert sum(_concordance([(3.0, 1.0), (3.5, 2.0)], 0.5)) == 1
    assert sum(_concordance([(3.0, 1.0), (3.49, 2.0)], 0.5)) == 0


def test_the_threshold_is_evaluated_in_plain_float_arithmetic():
    """A pair exactly on the boundary may fall either side of it, deterministically.

    `3.3 - 3.0` is 0.29999999999999982, so this pair is *not* >= 0.3 apart. That is
    fine and deliberately not papered over with a tolerance: the sweep evaluates
    `ref_hi - ref_lo >= min_delta`, the same expression the brute-force definition
    evaluates, so the two can never disagree about which pairs are scored. The result
    is identical run to run, which is what the gate needs; and across the ~570M pairs
    of a real build, a handful of boundary pairs cannot move the score.
    """
    assert sum(_concordance([(3.0, 1.0), (3.3, 2.0)], 0.3)) == 0
    assert brute_force([(3.0, 1.0), (3.3, 2.0)], 0.3) == _concordance(
        [(3.0, 1.0), (3.3, 2.0)], 0.3)


def test_our_own_ties_are_scored_as_neither():
    # we give both words the same Zipf; wordfreq separates them clearly. That is
    # not agreement, and it is not a reversal.
    assert _concordance([(3.0, 4.0), (5.0, 4.0)], 0.3) == (0, 0, 1)


def test_rejects_a_nonpositive_threshold():
    # "separated by at least 0" would score the reference's own ties, which is
    # precisely what this metric exists to exclude.
    with pytest.raises(ValueError):
        _concordance([(3.0, 3.0), (4.0, 4.0)], 0.0)


def test_empty_and_singleton():
    assert _concordance([], 0.3) == (0, 0, 0)
    assert _concordance([(3.0, 3.0)], 0.3) == (0, 0, 0)


@pytest.mark.parametrize("seed", range(25))
def test_matches_brute_force_on_random_input(seed):
    rng = random.Random(seed)
    n = rng.randint(2, 60)
    # 2-decimal values drawn from a deliberately narrow range, so duplicate
    # reference values and duplicate values of our own both occur often — the
    # shape that actually breaks a sweep implementation.
    pairs = [(round(rng.uniform(3.0, 4.0), 2), round(rng.uniform(3.0, 4.0), 2))
             for _ in range(n)]
    for min_delta in (0.01, 0.1, 0.3, 0.5, 1.0):
        assert _concordance(pairs, min_delta) == brute_force(pairs, min_delta), min_delta
