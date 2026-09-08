"""Tests for wrodfreq.lemma — the paradigm-rollup disambiguation math (spec §9)."""

import pytest

from wrodfreq.lemma import aggregate_by_family, aggregate_loose


def test_unambiguous_form_passes_through_unsplit():
    # a form claimed by exactly one lemma keeps its full count
    freqs = {"pisica": (100, 80)}
    form_lemma = {"pisica": ["pisică"]}
    assert aggregate_by_family(freqs, form_lemma) == {"pisică": (100.0, 80.0)}


def test_unambiguous_forms_sum_occurrences_and_max_documents():
    # two forms of the same lemma: occurrences sum, documents take the max
    # (never the sum — a document holding both forms would be double-counted)
    freqs = {"merge": (50, 40), "mersul": (30, 45)}
    form_lemma = {"merge": ["a merge"], "mersul": ["a merge"]}
    assert aggregate_by_family(freqs, form_lemma) == {"a merge": (80.0, 45.0)}


def test_ambiguous_form_splits_by_claimant_headword_frequency():
    # spec §9's own example shape: a shared form should mostly go to whichever
    # claimant is far more prominent in its own right (own headword row).
    freqs = {
        "vești": (1000, 900),
        "veste": (576_766, 500_000),
        "veșcă": (264, 200),
    }
    form_lemma = {
        "vești": ["veste", "veșcă", "vești"],
        "veste": ["veste"],
        "veșcă": ["veșcă"],
    }
    result = aggregate_by_family(freqs, form_lemma)
    # each lemma's total is its own unambiguous row plus its share of the
    # 1000 disputed "vești" occurrences — isolate the share by subtracting
    # the known unambiguous baseline back out.
    veste_share = result["veste"][0] - 576_766
    vesca_share = result["veșcă"][0] - 264
    # veste's headword (576,766) dominates veșcă's (264) by far more than it
    # dominates the disputed form's raw 1000 — nearly the whole share goes
    # to veste, next to nothing to veșcă (spec's own ~99.9%/~0.1% example).
    assert veste_share / 1000 > 0.99
    assert vesca_share / 1000 < 0.001
    # the disputed occurrences must be fully accounted for (no leakage) —
    # split three ways since "vești" also claims itself as a third lemma
    vesti_share = result["vești"][0]
    assert veste_share + vesca_share + vesti_share == pytest.approx(1000.0)


def test_ambiguous_split_never_double_credits_documents():
    # documents are max-per-lemma, share-scaled — summing the shares across
    # claimants must never exceed the original document count, since that
    # would imply more distinct documents than the form actually appeared in.
    freqs = {"vești": (1000, 900)}
    form_lemma = {"vești": ["veste", "veșcă"]}
    result = aggregate_by_family(freqs, form_lemma)
    assert sum(v[1] for v in result.values()) <= 900.0 + 1e-9


def test_zero_evidence_claimant_still_gets_a_nonzero_smoothed_share():
    # spec §9 point 3's "văz" case: a claimant with no evidence of its own
    # must not be credited a hard zero (SHARE_ALPHA smoothing), or it would
    # accumulate zero documents forever despite genuinely sharing a form.
    freqs = {"shared": (100, 50)}
    form_lemma = {"shared": ["prominent", "obscure"]}
    # neither claimant has its own headword row in freqs
    result = aggregate_by_family(freqs, form_lemma)
    assert result["obscure"][0] > 0
    assert result["prominent"][0] > 0


def test_aggregate_loose_credits_every_claimant_in_full():
    # the undivided sibling: no split, every claimant gets the whole count
    freqs = {"vești": (1000, 900)}
    form_lemma = {"vești": ["veste", "veșcă", "vești"]}
    result = aggregate_loose(freqs, form_lemma)
    assert result == {"veste": 1000.0, "veșcă": 1000.0, "vești": 1000.0}


def test_aggregate_loose_sums_across_multiple_forms_of_one_lemma():
    freqs = {"merge": (50, 40), "mersul": (30, 45)}
    form_lemma = {"merge": ["a merge"], "mersul": ["a merge"]}
    assert aggregate_loose(freqs, form_lemma) == {"a merge": 80.0}


def test_loose_total_is_always_at_least_the_disambiguated_total():
    # family_ratio (spec §9) depends on this: undivided >= disambiguated,
    # always, for every claimant of an ambiguous form (share <= 1).
    freqs = {
        "vești": (1000, 900),
        "veste": (200, 150),
        "veșcă": (50, 40),
    }
    form_lemma = {
        "vești": ["veste", "veșcă"],
        "veste": ["veste"],
        "veșcă": ["veșcă"],
    }
    disambig = aggregate_by_family(freqs, form_lemma)
    loose = aggregate_loose(freqs, form_lemma)
    for lemma in disambig:
        assert loose[lemma] >= disambig[lemma][0] - 1e-9
