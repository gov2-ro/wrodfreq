"""The paradigm-rollup math for the lemma layer (spec §9). Pure functions, no I/O.

Ported from oțios's `validate_diachronic.py:386-498` (see CLAUDE.md's "what to
copy" table) — the disambiguation math, not the cross-corpus merge philosophy.
`wordfreq` counts surface forms, so a heavily-inflected verb like `înmărmuri`
reads as nearly extinct (317 hits) while its participle `înmărmurit` alone has
5,846 — the citation form is only one slice of the paradigm.

Two things a re-implementation gets wrong:

1. **Ambiguous forms are split, not duplicated.** A shared form like `vești`
   (claimed by `veste` "news", `veșcă` "sieve rim", and `vești` itself) would
   hand every claimant its full count if credited naively.
2. **The split is weighted by each claimant lemma's own headword frequency**
   in the same source — the only prior that actually separates a common
   lemma from a rare one sharing its form (weighting by "forms only one
   lemma claims" was tried first and fails: a noun's own citation form is
   frequently shared too, leaving no evidence for exactly the words that
   need it).

`build/build_lemma_layer.py` calls these per source, then merges the
resulting *per-source zipf values* across sources via `wrodfreq.zipf.
merge_zipf` — the same trimmed mean `merge.py` uses for surface forms — not
by summing raw occurrences across corpora the way oțios's own `merge_panels`
does. See that script's docstring for why: raw-summing across corpora would
let CulturaX (~200x the next source) dominate the lemma layer the same way
it would have dominated surface forms if the trim didn't exist.
"""

from __future__ import annotations

SHARE_ALPHA = 1.0  # smoothing when splitting an ambiguous form's count


def aggregate_by_family(
    freqs: dict[str, tuple[int, int]], form_lemma: dict[str, list[str]]
) -> dict[str, tuple[float, float]]:
    """Roll one source's surface-form counts up to whole paradigms.

    `freqs` must already be restricted to words present in `form_lemma` —
    every key is expected to resolve to at least one claimant lemma; this
    function does not fall back to treating an unmapped word as its own
    lemma (unlike oțios's original, which does — see build_lemma_layer.py's
    module docstring for why only DEX-paradigm words get a lemma at all).

    Returns {lemma: (occurrences, documents)}, both floats — an ambiguous
    form's count is split proportionally, so an integer occurrence count
    would be a fiction. Documents take the max across a lemma's forms,
    share-scaled the same way occurrences are: summing would double-count a
    document that holds two forms of the same lemma, and all-or-nothing
    credit (only the majority claimant gets any documents) can leave a lemma
    with real occurrence evidence and zero documents whenever it never
    majority-claims any single form.
    """
    occ: dict[str, float] = {}
    doc: dict[str, float] = {}
    for word, (o, d) in freqs.items():
        lemmas = form_lemma[word]
        if len(lemmas) == 1:
            lemma = lemmas[0]
            occ[lemma] = occ.get(lemma, 0.0) + o
            doc[lemma] = max(doc.get(lemma, 0.0), float(d))
            continue
        # Prior: how prominent is each claimant lemma in its own right, in
        # this same source? (freqs.get(lm) is that lemma's own headword row,
        # if it has one — 0 if not, still smoothed by SHARE_ALPHA so an
        # obscure claimant still gets a nonzero, if tiny, share.)
        weights = [freqs.get(lm, (0, 0))[0] + SHARE_ALPHA for lm in lemmas]
        total = sum(weights)
        for lemma, w in zip(lemmas, weights):
            share = w / total
            occ[lemma] = occ.get(lemma, 0.0) + o * share
            doc[lemma] = max(doc.get(lemma, 0.0), d * share)
    return {w: (occ[w], doc.get(w, 0.0)) for w in occ}


def aggregate_loose(
    freqs: dict[str, tuple[int, int]], form_lemma: dict[str, list[str]]
) -> dict[str, float]:
    """Undivided word-family totals: every claimant lemma credited in full.

    Deliberately the over-counting sibling of `aggregate_by_family` — this is
    the "how often does a reader meet something that *looks like* this
    lemma" number. Used only to compute `family_ratio` (spec §9): a large
    gap between this and the disambiguated total means the lemma survives
    mostly as a relative of something much more common, not on its own.
    """
    out: dict[str, float] = {}
    for word, (o, _d) in freqs.items():
        for lemma in form_lemma[word]:
            out[lemma] = out.get(lemma, 0.0) + o
    return out
