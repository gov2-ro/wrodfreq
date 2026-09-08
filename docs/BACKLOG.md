# Backlog

Open bugs, debt, and enhancements. Add new entries with `- [ ]` and enough context to act on later.

---


- [ ] maybe implement with another model and check results?

- [ ] when done, integrate with the synonims project

- [x] `build/ingest_news.py` — completed 2026-09-08: 479/479 shards,
  2,186,239,880 tokens, 6,636,815 ro docs, 2,943,510 unique words, 27.9h.
  `compute_zipf.py --source news` → floor=0.36. Lesson carried forward for
  `subs`/`eu`: a wedged (not crashed) ingester defeats the restart-loop's
  blind retry-on-nonzero-exit, and this hit two different causes here (a
  stalled fetch, then a huggingface_hub retry-storm background thread) before
  `os._exit()` on the second Ctrl+C proved reliable — reuse that signal
  handler pattern rather than the plain flag-only one if `subs`/`eu` also do
  large blocking remote reads.

- [x] `build/ingest_subs.py` — completed 2026-09-08: 371,104,866 lines,
  1,936,897,280 tokens, 2,165,628 unique words, 75.4 min (slower than the
  ~200k lines/s smoke-test rate suggested — real sustained rate was
  ~82-87k lines/s; the `--test` "in 1s" timing was too coarse to trust for
  extrapolation). `compute_zipf.py --source subs` → floor=0.41.
  `validate.py` 2/2 across all four sources. Top-20 has a strong, genuine
  conversational signature (`nu` #2, colloquial `asta`/`te`/`sunt` present)
  clearly distinct from the three written-register sources.

- [x] `build/ingest_eu.py` — completed 2026-09-08: combines OPUS Europarl v8
  (404,131 lines, 9,570,511 tokens) + DGT v2021 (4,534,513 lines, 74,845,446
  tokens) into `source_id='eu'` per spec's panel table listing them as one
  row. Both tiny compared to the other four sources — full run took well
  under a minute since both files were already cached from `--test`.
  `compute_zipf.py --source eu` → floor=1.77. Top-20 shows a clean
  legal/formal signature (`pentru`, `care`, `sau`, `al`, `articolul` all
  rank — `articolul` at #18 is about as unambiguous a legal-register tell as
  this table will ever produce) distinct from all four other sources.
  **M3 is done: panel is 5/5, `validate.py` 2/2 across all five sources,
  `merge.py`'s trimmed-mean branch (spec §13's M3 "done when") is now
  reachable.** Two real bugs caught and fixed during scaffolding, before any
  real run: (1) `ensure_source_row()` was originally called only after all
  parts finished, but each part's `flush()` does an `UPDATE ... WHERE
  source_id=?` that silently no-ops against a nonexistent row in SQLite —
  moved the row-creation earlier so in-progress totals aren't lost if
  interrupted mid-part; (2) the exit code returned 1 whenever not every part
  was fully exhausted, which would make a deliberate `--test`/`--limit` run
  look like a crash to the restart-loop's `[ $? -eq 0 ] && break` and retry
  it forever — changed to match `ingest_subs.py`'s convention (1 only for a
  real signal-interrupted stop).

- [x] `build/merge.py` — completed 2026-09-08 (M4, spec §7.4/§8): rebuilds
  `merged` from scratch every run (atomic table-swap via `merged_new`, never
  upserts) from the 5-source panel. 6,193,962 words written, 27,070,331
  attested-but-below-floor words correctly omitted (not zeroed). `is_dex`
  wired to oțios's vendored `inflected_forms.db` (587,535 of 6.19M merged
  words matched), gracefully degrades to 0 if that db isn't reachable. Added
  `wrodfreq.zipf.merge_zipf()` (the trim/mean itself, spec §8.2) with 6 new
  unit tests, all passing (16/16 total in `tests/`).

  **Two findings worth remembering, not bugs, both confirmed by direct query:**
  1. `merged`'s own zipf for `de` is **7.71** — above the spec's literal 6.0–7.5
     function-word band, same as the single-source finding from M1
     (`docs/activity-history.md` 2026-08-18), but this time it's the *proper
     5-source trimmed mean*, not a single skewed source. It lines up almost
     exactly with `wordfreq`'s own real Romanian value (7.72, checked M1).
     `validate.py`'s check 1 doesn't test `merged` yet (only per-source
     `source_zipf`) — when it's extended to also check `merged`, the ceiling
     needs to be ~8.0 there too, not the spec-literal 7.5, or this specific,
     correctly-computed word will fail CI forever.
  2. Spec §11 check 5's named example — "`dumneavoastră` high in `eu`, low in
     `subs`" — **does not hold** in the real data: `eu`=4.71, `subs`=5.04
     (subs is *higher*), `web`=5.15 is actually the high end, `wiki`=3.63 the
     low end. Not a tokenizer artifact (checked the exact diacritic-correct
     token directly) — a real finding that contradicts the spec's stated
     prior. The rest of the top-by-spread list (checked `n_reliable=5` only,
     to exclude single-source noise) *does* look like genuine register/topic
     signal, not noise: `vrei` (informal "you want"), `alineatul`/`alineatele`
     (legal "paragraph/subsection"), `isbn`, place names — so the mechanism is
     sound, just this one named example was wrong.

- [x] `build/build_lemma_layer.py` — completed 2026-09-08 (M5, spec §7.5/§9,
  optional layer). Ports `aggregate_by_family()`/`aggregate_loose()` from
  oțios into new `wrodfreq/lemma.py` (pure functions, 8 unit tests — 27/27
  total across the repo now). Deliberately does **not** port oțios's
  cross-corpus `merge_panels()` (raw-occurrence summing across corpora) —
  that would let CulturaX dominate lemma-level results the same way it would
  have dominated surface forms without the trim. Instead: disambiguate per
  source, convert to that source's own zipf, then cross-source merge via the
  same `merge_zipf()` `merge.py` already uses for surface forms — a lemma is
  just a word whose count came from a paradigm roll-up. Query performance
  fix found while building this: a plain `JOIN` against a ~1.5M-row temp
  table of DEX forms made SQLite scan all of `source_counts` (tens of
  millions of rows) and probe the small table per row, since it had no
  selectivity estimate for `source_id`; `CROSS JOIN` forces the small table
  to drive instead, turning each lookup into a direct primary-key hit —
  13x faster measured against the real db (26s → 2s per source). Ran
  against the real 5-source panel: 180,820 lemmas written in 16s, verified
  idempotent (hashed two independent runs, identical). The spec's own
  motivating example checks out: `înmărmuri` (the verb) rolls up from a
  bare 0.82 zipf (citation form alone) to 1.67 once its whole paradigm is
  counted — correctly *less* than `înmărmurit`'s own merged zipf (2.23)
  because that participle is genuinely ambiguous (also a separate DEX
  adjective lexeme) and the split correctly divides credit rather than
  crediting the verb sense in full.

  **Finding, not a bug:** the "top 20 by family_ratio" printout is
  dominated by values in the hundreds-of-thousands (vs. spec's own stated
  examples topping out at 938×) and several implausible-looking "lemmas"
  (`voame`, `îmulți`). Traced concretely: `voame`'s DEX paradigm has 40
  forms including malformed entries (`vomeți-` with a trailing hyphen) and
  shares `vom` with the auxiliary `vrea` (3-way ambiguous) — this looks like
  a genuine extraction artifact in oțios's vendored `inflected_forms.db`,
  not a bug in the merge math (confirmed the math is doing exactly what
  it's supposed to on this input). Worth a closer look before ever
  redistributing the lemma layer, separate from the licence question
  CLAUDE.md already flags.

- [x] `validate.py` extended to all 6 spec checks — completed 2026-09-08.
  Check 1 fixed to use the adjusted ceiling (`ZIPF_HIGH_ADJUSTED = 8.0`) for
  the `merged` branch too, not just the pre-M4 per-source fallback — it was
  already checking `merged` when populated but with the spec-literal 7.5,
  which failed on `de`=7.71 (see M4's finding above). Check 3: 59
  hand-written monotone pairs, cross-validated against `wordfreq`'s
  independent data before committing (all 59 agree on direction) — every
  pair is a single token, several first drafts using multi-word phrases
  (`"cavitate bucală"` etc.) had to be replaced since the tokenizer can
  never produce a multi-word token. Check 6 (idempotence) extended from
  stage 2 alone to stages 2-4 (compute_zipf, merge, build_lemma_layer),
  each rebuilt and hashed twice — all three ok. Checks 2 and 4 both skip
  (not fail) if their external reference (`wordfreq`, DEX db) isn't
  reachable, rather than reporting a misleading pass.

  **Two checks fail, both understood, neither a bug in validate.py itself:**

  1. **Check 2 (rank correlation) fails: Spearman rho=0.86, need >0.9.**
     Investigated rather than accepted at face value — correlation gets
     *worse* at higher wordfreq-zipf thresholds (0.86 at >=3.0, down to 0.79
     at >=4.5), ruling out simple low-frequency tail noise; something
     systematic is concentrated among well-attested words. The biggest
     divergences are Romanian elision prefixes — `într`, `dintr`, `printr`
     (normally written `într-o`, `dintr-un`, always before a vowel) — off by
     +1.85 to +2.63 zipf. Root cause: `wrodfreq/tokenizer.py` keeps internal
     hyphens (a deliberate, documented choice, for genuine compounds like
     `bine-cunoscut`), so `într-o` tokenizes as *one* token, fragmenting what
     should be one common preposition's count across dozens of separate
     `într-X` compound tokens, each individually much rarer than `într`'s
     true combined frequency — same mechanism likely explains the `n-o`/
     `n-am`/`l-ai`/`s-a`-shaped words seen high in the spread report (M4
     entry above) too. Not symmetric, though: the same hyphen-keeping
     behavior is *more* correct for genuine compounds/proper nouns —
     `cluj-napoca` staying one token seems right, and wordfreq's own data
     has a separate-looking artifact (bare `ul`/`ului`/`uri`/`urile` —
     enclitic definite-article suffixes that never appear as free-standing
     words in real Romanian — scoring implausibly high, zipf 4.6-5.6,
     hinting at a subword-segmentation artifact on wordfreq's side, not
     ours). **Not fixed** — a real fix means deciding which hyphens are
     elision (split) vs. genuine compounds (keep), then re-ingesting all
     five sources from scratch (hours-to-days). Documented here for when
     that's worth doing; not attempted in this session.

  2. **Check 4 (DEX coverage) fails: 85.1% (103,702/121,895), need 95%.**
     Excluded 118 structurally-unreachable DEX lemmas from the denominator
     first (abbreviations like `acad.`, Latin binomials like `acanthus
     longifolius`, foreign-diacritic loanwords like `müsli` — none of these
     can ever match the tokenizer's letters-only output, checked via the
     real `tokenize()` function, not a duplicated regex). That barely moves
     the number (~85.2%) — the real gap is ~18,000 lemmas DEX's own
     `frequency` field calls common (>0.5) that our contemporary 5-source
     panel never attests 5+ times anywhere. Spot-checked the missing sample:
     dominated by genuinely obscure/archaic/technical words (`abcede`,
     `abdomenoscop`, `abstenționist`). Checked oțios's own
     `extract_inflected_forms.py`: `lexeme.frequency` is read verbatim from
     DEX Online's own database column, not derived from any corpus — and
     `acanthus longifolius` (a Latin botanical binomial) scoring 0.99 on
     that same scale is hard to square with "frequency" meaning real-world
     usage frequency. Best guess, not confirmed: DEX's own `frequency`
     measures something like lexicographic completeness/dictionary-edition
     coverage, not corpus usage — which would mean this check's 95% target
     may be unreachable by any realistic *contemporary* corpus panel,
     regardless of vocabulary-filter correctness (spec's stated failure
     mode). Left failing rather than silently loosening the threshold —
     worth resolving what `frequency` actually means before deciding
     whether to recalibrate the check or add an explicitly non-contemporary
     source to close the gap.