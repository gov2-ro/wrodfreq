# ADR-005 — `family_ratio` compares loose and disambiguated totals over one source panel

**Status:** accepted, 2026-11-09 (brief B7, Phase 3, finding A06). **Amends:** spec
§7.1 (the `family_ratio` comment) and §9.

## Context

`family_ratio` is defined as the undivided ("loose") family total divided by the
disambiguated total. Per source, loose ≥ disambiguated, so the ratio should be at least 1.

The 0.2.0 build merged the two totals over **different** source panels. Each total used
its own reliable sources. A loose total often clears the reliability floor in a large
source where the split total does not. That source then enters only the loose mean, with
a low Zipf value, and pulls the loose mean down. In 0.2.0, 2,382 lemmas had a ratio
below 1. For example, `detoxica` shipped as 0.03.

Example (brief B7): 10 shared occurrences in a source of 1,000 tokens and 6 in a source of
10⁹ tokens, split equally between two lemmas. The disambiguated counts are 5 and 3, so
only the small source is reliable. The loose counts are 10 and 6, so both sources are
reliable. The old ratio was about 0.0015; the true ratio is 2.

## Decision

For lemma L, let R be the eligible (completed, contemporary) sources where the
**disambiguated** count of L clears `MIN_OCC_PER_SOURCE`. Both totals use R:

```text
z_disambig   = merge_zipf([zipf(disambig[L, s], tokens[s]) for s in R])
z_loose      = merge_zipf([zipf(loose[L, s],    tokens[s]) for s in R])
family_ratio = 10 ** (z_loose - z_disambig)
```

The lemma Zipf (`z_disambig`) and the lemma eligibility rule do not change. The merge
is monotone and loose ≥ disambiguated in every source of R, so the ratio is ≥ 1. A
value below 1 by no more than 1e-9 is floating-point noise and is set to 1. Any value
further below 1 stops the stage. The fix does not clamp real values, does not sum raw
counts across corpora, and does not change which lemmas get a row.

## Consequences

- No shipped `family_ratio` is below 1. The integrity check (`build/integrity.py`)
  fails a database that has one.
- The ratio now answers one question: in the sources that measure this lemma, how much
  bigger is the family that looks like it. Ratios for lemmas where only some sources
  are reliable change; the candidate report lists the largest changes.
- The published definition changes in meaning. It does not get a new estimator. The
  spec §9 and `docs/method.md` now state the matched panel.
