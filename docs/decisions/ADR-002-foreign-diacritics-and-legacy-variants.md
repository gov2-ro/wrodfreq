# ADR-002 — Do not widen the token class. Legacy Romanian variants are the real defect, and both are blocked on the same re-ingest

**Status:** accepted, 2026-10-02. **Decided by:** Opus, on Brief B2's measurements.
**Supersedes:** the earlier "single letters track `wordfreq` within ~0.05" assessment,
which B2 overturned for rare letters.

## Context

The tokenizer's class is Romanian-only, so a foreign word containing a non-Romanian
diacritic splits mid-token: `Düsseldorf` → `d` + `sseldorf`. Brief B2 measured whether to
widen it, reading raw text next to the production tokenizer across all of `wiki` (442,389
docs) and ~187M tokens of `web`; `news`, `subs` and `eu` have no raw text on disk, so
their figures are extrapolated and labelled as such.

**B2's first finding was methodological and it reframes the question:** `source_counts`
cannot answer it at all. It stores only already-tokenized words, so the foreign characters
are gone before anything is counted. That is not a measurement inconvenience — it is the
whole cost structure of this decision, and §4 below turns on it.

## Decision 1 — do not widen the token class

The benefit is small and lands where it matters least. At least 16,433 of 6,050,327
`merged` rows are fragments (0.27%); 15,490 sit below Zipf 2.0 and only **52 reach ≥3.0**.
After the merge's trimmed mean, 22 of 26 single letters move by ≥0.005 and the largest
mover is `k` at **~0.07**. Function words are untouched, and every other word moves
+0.006 (wiki) or +0.001 (web). Against that: a multi-day re-ingest.

Two supporting findings, one of which corrects my own brief:

- **Latin-1 would not even fix it.** It covers 75% of wiki's and 83% of web's foreign-Latin
  occurrences. Reaching 98.7% needs Latin Extended-A/B as well (≤U+024F). So the cheap
  version of this change does not exist.
- **My brief's worry was backwards.** I warned that widening might *reduce* comparability
  with `wordfreq`. In fact `wordfreq` keeps `Düsseldorf`, `Köln`, `Zürich` and `Škoda`
  whole and returns 0.0 for `nchen`, `sseldorf` and `rnberg`, where we ship 3.55, 2.94 and
  2.84. Widening would *raise* comparability. B2 was right to then discount it: the
  concordance gate only covers `wordfreq`'s own range, where the fragments are absent. A
  correct argument that does not bear on the decision.

## Decision 2 — the real defect is legacy Romanian diacritic variants, and it is bigger

This is B2's most valuable finding and it was not what the brief asked for. Half of `web`'s
foreign-Latin occurrences are **accent-stripped or wrong-codepoint Romanian**, against 9%
in wiki. `ã` is web's top "foreign" character (87,914 occurrences), and **89% of those
become an established Romanian word** when read as `ă` — `sã`, `cã`, `dupã`. `ǎ`, `ȋ` and
`ȃ` behave the same at 90–95%.

So the fragments are not mostly foreign names. In the largest source they are mostly
Romanian, mis-encoded, and currently being destroyed: `sã` tokenizes to `s`, and those
occurrences are pooled with every genuine `s`. B2 measures 3,214 real Romanian words in
`web` gaining ≥0.005 Zipf under normalization, the largest **+0.58**. A factor of ~3.8 on a
real Romanian word is exactly the class of error this project exists not to make, and it
is an order of magnitude more serious than the 0.07 that widening buys.

**The precedent is already in the code.** `normalize()` is
`lower → ş→ș, ţ→ț → NFC`: it already maps legacy cedilla forms to the comma-below
Romanian ones. Mapping `ȋ→î` and `ȃ→â` is the same operation on the same kind of defect —
old Romanian typography at a different codepoint — not a new category.

### Split the class; `ã` is not like the others

- **`ȋ ȃ ǎ` → `î â ă` — unambiguous.** 90–95% resolve to established Romanian words, and
  no other language puts these codepoints in a Romanian-language corpus in quantity. These
  extend the existing `ş→ș` rule.
- **`ã` — contested, and a blanket map would corrupt.** 89% Romanian in `web` but only
  **17% in `wiki`**, where it is Portuguese and Spanish proper nouns (`São`, `João`). One
  global rule gets the majority right in one source and damages the minority in another.
  Resolving it needs more than a character table, and it is not resolved here.

## Decision 3 — both are deferred, and they are deferred *together*

Neither lands now. The trigger is a full reprocess of the existing five sources for an
independent reason; at that point both ride along at near-zero marginal cost, and widening
becomes worth doing precisely because it is no longer paying for its own re-ingest.

## Decision 4 — why no migration can do this, unlike the dash and apostrophe fixes

This is the constraint that decides decision 3, and it is worth stating plainly because the
project has two recent counter-examples that look similar and are not.

`build/migrate_dashes.py` and the apostrophe migration in ADR-001 work because the
malformed token is **still present in `source_counts` as its own distinct key** — `acu'` is
a row, so it can be re-split and folded into `acu` with occurrences conserved.

Diacritic damage is not recoverable that way. `sã` never became a row; it became `s`, and
merged indistinguishably into the millions of genuine `s` occurrences at ingest time. The
information is gone from the table, so only re-reading raw text can recover it. And raw text
for `news`, `subs` and `eu` is no longer on disk, while `web` is 23.8B tokens — so "re-ingest"
means re-download and re-process, not a local pass.

## Decision 5 — do not apply either change to the incoming `social` source alone

`social` is being acquired now and has not been tokenized, so it is tempting to ingest it
under better rules. **No.** One tokenizer serves every source by design: two sources
tokenized differently cannot be merged and nothing would tell you. A fresh source is not a
licence to fork the tokenizer, and the inconsistency would be invisible in every number
downstream. `social` is ingested with exactly the rules the other five used.

## Consequences

- `merged` keeps ~16,433 fragment rows; 52 of them above Zipf 3.0 are wrong enough to see
  (`nchen` 3.55, `rich` 3.48, `sseldorf` 2.94) and stay wrong for now.
- Rare single letters stay inflated against `wordfreq` (`k` +0.37, `w` +0.35, `g` +0.34).
  B2 attributes only a minority of that gap to fragmentation and the rest to corpus
  composition, so widening would not have closed it anyway.
- The `+0.58` legacy-variant error on real Romanian words stands until a re-ingest.
  **This is the known-wrong number to carry forward**, and the one to quote when a
  re-ingest is next being costed.

## Follow-up required before any re-ingest acts on this

A brief must first measure, on raw text rather than `source_counts`: the `ã`
disambiguation (per-source, since wiki and web disagree 17% vs 89%) and whether a
per-source or context-sensitive rule is defensible at all given decision 5's
one-tokenizer constraint; the same legacy-variant rate in `subs`, `news`, `eu` and the new
`social`, which are currently extrapolations; and what re-acquiring each source now costs.
Not written yet — nothing is blocked on it.
