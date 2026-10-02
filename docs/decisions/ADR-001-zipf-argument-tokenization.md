# ADR-001 — `zipf_frequency` tokenizes its argument; the extensions do not

**Status:** accepted, 2026-10-02. **Decided by:** Opus, on Brief B3's measurements.
**Implements:** Brief B5. **Supersedes:** nothing.

## Context

`wordfreq`'s `zipf_frequency` tokenizes its argument; ours looked the string up as given.
The whole adoption claim for this package is that changing one import line is enough, so a
divergence in the most-used function matters more than its frequency of occurrence.

Brief B3 measured ~45 input shapes against wordfreq 3.1.1 and established the mechanism,
which was not what anyone assumed: for a multi-token argument wordfreq combines
**harmonically**, `1/f = Σ 1/f_i`, so a phrase always comes out *rarer than its rarest
token*. It is not the first token, the minimum, or a mean. Zero tokens, or any single
unknown token, returns the `minimum` argument. Digit runs go through a digit-frequency
model.

Nothing in this project depended on the old behaviour: `tests/test_api.py` only ever passed
single tokens, and `validate.py` never calls our `zipf_frequency` at all.

## Decisions

### 1. `zipf_frequency` and `word_frequency` tokenize; match wordfreq's mechanism exactly

Tokenize with `wrodfreq.tokenizer.tokenize`. Zero tokens → `minimum`. One token → that
token's value. Two or more → the harmonic combination, with any unknown token giving
`minimum`. `word_frequency` combines in the **linear domain**, never by round-tripping
through a rounded Zipf.

### 2. Do *not* try the exact key before tokenizing — and delete the rows that would need it

B3 proposed an exact-key-first lookup, partly to keep 225 apostrophe-edged entries
(`'a`, `'aci`, `'aswad`) answerable. That rationale is inverted, and the measurement
shows why: **the tokenizer is idempotent on its own output.** Over 200,000 sampled
`merged` keys, `tokenize(w) == [w]` without exception. So for every key the tokenizer
could have produced, tokenize-then-lookup and exact lookup are the same operation, and an
exact-key-first path can only change the answer for keys the tokenizer *cannot* produce.

Those are exactly the 225 apostrophe-edged rows, and all 225 are unreproducible by the
current tokenizer — the same class as the hyphen-edged rows, of which `merged` now holds
**zero** because `build/migrate_dashes.py` cleaned them. It fixed `-`-edged tokens and
missed `'`-edged ones; these are that bug, one character over. Their values sit at or
below the per-source floors, and the content (`'aalamin`, `'alamiin`, `'aswad` — Arabic
transliterations carrying a leading hamza) confirms it.

So an exact-key-first path's only real function would be keeping residue answerable.
Drop the path; migrate the residue out. The lookup stays single-path and a defect class
closes instead of being enshrined in the API.

### 3. A string with letters or digits the tokenizer did not consume returns `minimum`

`café`, `a1b`, `de 123`. Never combine surviving fragments into an answer — that fabricates
a frequency for a word we never counted. This holds regardless of how Brief B2's
foreign-diacritics question is decided; B2 can only change *which* strings reach this rule,
not whether the rule is right.

### 4. Numerals return `minimum`. We are not building a digit model

wordfreq answers `123` with 3.92. We exclude numerals from both numerator and denominator
by design — that is the honest-denominator departure in §3, and it is load-bearing for
every ppm in the table. Matching wordfreq here would mean a digit-frequency model, which
is out of scope for a *word*-frequency table. Accept the divergence and document it.

### 5. The `0.0` / `None` asymmetry is correct, not a conflict

B3 flagged that tokenizing breaks today's identity — `zipf_frequency(w) == 0.0` exactly
when `frequency_detail(w) is None`. After the change, `zipf_frequency('spune-')` is 5.78
while `frequency_detail('spune-')` stays `None`.

That identity was never the contract. The contract is that these are **two different
signals for two different questions, not to be unified**. The questions are "how common is
this, as wordfreq would answer it" and "what does our table hold for this exact entry". A
multi-token string genuinely has an answer to the first and none to the second, so the
coincidence holding until now was incidental. Breaking it is *more* faithful to the spec,
not less.

`frequency_detail`, `by_source` and `lemma_frequency` keep exact single-row lookup and
keep returning `None`. The asymmetry is documented in the API reference as the intended
behaviour.

## Consequences

- One changed import line becomes true for every input shape except numerals (4) and
  foreign-diacritic words pending B2 (3). Both are documented divergences with reasons.
- `merged` loses 225 junk rows; 6,050,327 → 6,050,102. The shipped file shrinks slightly
  and `MINOR` does not change, since the corpus panel has not.
- Callers who relied on `zipf_frequency` as an exact-row probe must move to
  `frequency_detail`. That is what it is for, and no caller in this repo did.

## Rejected alternatives

- **Don't tokenize.** Weakens the adoption claim for no gain now that the mechanism is known.
- **Exact key first, then tokenize.** Redundant by idempotence; its only effect is to
  preserve residue. See decision 2.
- **Make `frequency_detail` strip to a single token.** Unifies two signals the spec
  separates on purpose.
