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

  2. ~~Check 4 (DEX coverage) fails: 85.1% (103,702/121,895), need 95%.~~
     **Fixed 2026-09-14** — see the dated entry below. Root cause confirmed
     2026-09-09 (oțios's own `CLAUDE.md`: `Lexeme.frequency` is a
     literary-prominence score, not usage frequency), recalibrated the
     threshold by measurement rather than guessing.

- [x] `build/build_package.py` + the real API (`wrodfreq/__init__.py`,
  `wrodfreq/_surface.py`) — completed 2026-09-09 (M6, spec §7.6/§10). This
  was the actual missing piece: `wrodfreq/__init__.py` had nothing but
  `__version__` before this — none of `zipf_frequency`/`word_frequency`/
  `top_n_list`/`frequency_detail`/`lemma_frequency`/`by_source`/
  `build_info` existed. Verified against a real, isolated `pip install` of
  the built wheel in a throwaway venv (not just "works when run from the
  repo") — this caught a real packaging bug: Hatchling's default file
  selection is git-tracked files only, and the data files are deliberately
  gitignored, so the first wheel build silently shipped with *no* data
  files at all. Fixed via `[tool.hatch.build] artifacts = [...]` — has to
  be at the top level, not just under `targets.wheel`, since `uv build`/
  `pip wheel` build the wheel from the sdist and the sdist needs the same
  override.

  Ships two files, not one: `ro_surface.msgpack.xz` (27.8 MB — words, zipf,
  n_reliable, n_attesting, spread) and `ro_by_source.msgpack.xz` (9.1 MB —
  the per-source breakdown, lazy-loaded only if `by_source()` is actually
  called). A single combined file measured 38.7 MB, over spec's 30 MB
  target for "the surface table"; splitting off `by_source` — the one
  extension spec explicitly calls out as "clearly marked as such" — both
  hit the target and matched that framing rather than being an arbitrary
  size-driven cut. Also quantized zipf/spread to centizipf ints instead of
  Python floats (msgpack packs a float as 8 bytes regardless of precision;
  spec's own "round to 2 decimals, it costs real bytes" reasoning taken to
  its actual conclusion) — this alone took the core file from 31.1 MB to
  29.2 MB, the only thing that got it under target. One real bug caught
  before shipping: the by-source file originally duplicated the full
  6.19M-word list a second time (for self-containment) — measured 27.6 MB
  instead of the 9.5 MB it should've been; fixed by making it positional,
  aligned to the surface file's word order, with a `word_count` field as a
  cheap cross-file integrity check instead of a second copy of the words.

  **`is_dex` and the lemma layer are deliberately NOT shipped.** CLAUDE.md
  flags the DEX Online licence question as unresolved before
  *redistributing* anything derived from it, and is_dex (an aggregate of
  ~587k booleans over merged's own words) would let anyone reconstruct a
  large fraction of DEX's own headword list by cross-referencing which
  shipped words have it set — a real redistribution question, not a
  hypothetical one. This wasn't decided here (not an engineering call to
  make unilaterally); `lemma_frequency()` just degrades gracefully (0.0,
  matching `zipf_frequency`'s own unknown-word contract) until it is.

  `build/validate.py`'s check 6 extended once more: stage 5
  (`build_package.py`) now joins stages 2-4, verified idempotent by
  rebuilding its payload twice and hashing. 16 new tests in
  `tests/test_api.py` against a small synthetic fixture (not the real
  ~37 MB build artifact) — 43/43 total across the repo now.

- [x] M7 — completed 2026-09-09, spec §13: "expose `n_reliable` to oțios as
  a corroboration signal... a change in the oțios repo, not this one." Done
  there: `~/devbox/otios/validate_with_wrodfreq.py` (commit `10b9883`),
  installed wRodfreq into oțios's venv as an editable dependency
  (`-e ../gov2/wrodfreq` in its `requirements.txt`). Measured before
  building anything, not assumed: a random 2,000-word sample of oțios's
  real 18,271-word shortlist against `wrodfreq.zipf_frequency()` came back
  27.8% zero-resolution, vs. the old `wordfreq`-based screen's documented
  99.6% (oțios's own `CLAUDE.md`, 2026-08-11) — confirmed the whole
  exercise was worth doing before writing the integration. Full run on the
  real 145,358-candidate list: 25.7% zero. Staged as a standalone CSV
  output (same status `dcr_definitions.csv` had before anyone decided how
  to use it) — deliberately **not** wired into `make_shortlist.py`'s
  scoring or `ui.db`; that's a real decision about how much weight a
  5-corpus corroboration count should carry against oțios's existing
  historical-attestation-driven score, not made unilaterally in this
  session. Found unrelated pre-existing uncommitted state while there
  (`docs/wordfreq-recipe.md` deleted from oțios's working tree, last
  touched 2026-08-11 — predates this session, nothing to do with this
  work) — left untouched, committed only the files this task actually
  changed rather than `git add -A`.

  This also **confirmed** (not just hedged) the check-4 finding above:
  oțios's `CLAUDE.md` states outright that `Lexeme.frequency` is a
  literary-prominence score, not usage frequency.

- [x] `validate.py` check 4 recalibrated — completed 2026-09-14. Given
  `frequency`'s confirmed meaning (literary prominence, not usage — see
  entry above), no target near 95% was reachable at the spec-literal
  `frequency > 0.5` cutoff (~125,000 lemmas, spanning from genuinely
  common words down into exactly the archaic-but-canonical territory
  oțios's own project exists to find). Measured coverage across a range of
  thresholds rather than guessing one: stayed >=99% from `frequency>=0.99`
  down through `>=0.85`, then degraded roughly linearly (96.1% at >=0.75,
  93.9% at >=0.70). Picked `>=0.80` (97.7% measured, 48,648
  tokenizer-reachable lemmas) for a comfortable margin above 95% rather
  than the threshold nearest the exact crossover, so routine future panel
  changes don't turn this into a flaky check. A `LIMIT N`-by-rank approach
  was tried first and rejected — DEX's frequency values are heavily tied
  at round numbers like 0.99, so a rank cutoff sliced arbitrarily through
  a tied group and measured misleadingly low (94.8% for "top 4,500" vs.
  99.9% for the equivalent `frequency>=0.99` threshold covering the same
  words). `validate.py` now reports 4/5 checks passing; only check 2
  (rank correlation, tokenizer/elision issue, needs a full re-ingest)
  remains open.

- [x] Elision-splitting tokenizer fix — completed 2026-09-17, but **did not
  fix check 2 alone** (see the new finding logged right after this one).
  `wrodfreq/tokenizer.py` now splits Romanian clitic/preposition elisions
  (`într-o`→`într`+`o`, `n-am`→`n`+`am`, `avut-o`→`avut`+`o`) while keeping
  genuine compounds/proper-nouns/loanword-suffixes joined (`mass-media`,
  `cluj-napoca`, `site-ul`) — rule built empirically from the top-33,859
  hyphenated words in `merged`, not guessed from grammar alone (see the
  module docstring for the full reasoning, including the accepted `v-lea`
  edge case and the Roman-numeral-ordinal guard `ii-a`/`xii-lea` needed).
  10 new tokenizer tests, 52/52 passing repo-wide.

  **Avoided a full re-ingest.** Since occurrence counts are exact
  per-token, `build/migrate_elisions.py` split each affected compound's
  *existing* count in `source_counts` directly — e.g. `într-o`'s count
  redistributes into `într` and `o` exactly as a corrected tokenizer would
  have produced from scratch. 2,095,059 words split across the 5-source
  panel, +384,408,820 tokens recovered (elision rate scaled with
  register as expected: 1.2% of `web`'s tokens vs. 3.9% of `subs`'s,
  confirming conversational text really does contract more). One
  accepted approximation: `documents` becomes a slight upper bound for
  split words (if a document has both `într-o` and `într-un`, `într` gets
  credited twice instead of once) — zipf itself, the thing check 2
  measures, is unaffected. Backed up `data/wrodfreq.db` first (removed
  after verifying correctness, given tight disk space), re-ran
  `compute_zipf.py`/`merge.py`/`build_lemma_layer.py`/`build_package.py`,
  confirmed still idempotent (check 6 unaffected) and `a` jumped to #3 by
  zipf (was outside the top 20 before) with `o` also entering the top 20
  — exactly the words the fix targeted. Directly verified `într` moved
  from 3.45 (wildly wrong) to 6.05, matching `wordfreq`'s own 6.08 almost
  exactly; `dintr`, `printr`, `a`, `o`, `am`, `au`, `ai` all showed
  similarly dramatic, correct improvement.

  **But check 2's overall Spearman rho barely moved (0.860 → 0.863)** —
  see the next entry for why, and what's needed to actually clear 0.9.

- [ ] **New finding: a second, separate hyphenation phenomenon is now the
  dominant driver of check 2's gap** — Romanian combining-form compound
  adjectives (`austro-ungar`, `socio-economic`, `daco-roman`,
  `anti-terorist`), not clitic elision. `wordfreq` apparently splits these
  too, giving the combining prefix (`austro`, `socio`, `daco`, `anti`,
  `pseudo`, `cvasi`, `traco`, `geto`, `indo`, `anglo`, `ruso`, `moldo`,
  `germano`, `româno`, `fizico`, `științifico`, `carpato`, `istorico`)
  real standalone frequency aggregated across every compound it appears
  in, while ours currently keeps each compound joined, fragmenting the
  prefix's true (often much higher) combined frequency the same way
  elision used to fragment `într`'s.

  Measured before deciding whether to chase this: 7,395 distinct prefixes
  appear in >=15 distinct hyphenated compounds each in `merged` — a
  comparably-sized problem to the one just fixed, **not a quick
  extension of the same rule**. Unlike clitics (a small closed
  grammatical set), this candidate list is much larger and much noisier:
  alongside genuine, well-established combining forms (`anti`, `non`,
  `auto`, `pre`, `super`, `pseudo`, `micro`, `multi`, `neo`, `bio`, `eco`,
  the historical/ethnic ones above) sit clearly spurious single-letter or
  coincidental "prefixes" (`d`, `t`, `b`, `p`, `c`, `x`, `al`) that are
  almost certainly noise from a huge web corpus, not real productive
  compounding — a blanket rule here risks manufacturing nonsense splits
  at a much larger scale than the elision fix's one accepted edge case.

  Separately, **not everything driving check 2's remaining gap is fixable
  on our side**: `ul`/`ului`/`uri`/`urile` show up as wordfreq's biggest
  divergences in the *opposite* direction (wordfreq scores them
  implausibly high, 4.6–5.6 zipf, for what are Romanian noun-inflection
  suffixes that never stand alone in real text) — this looks like a
  subword-segmentation artifact on wordfreq's own side (from splitting
  `site-ul`-shaped words), not something correcting our own tokenizer
  could or should chase.

  Stopped here rather than expanding scope unilaterally into a
  meaningfully riskier fix — logged for a deliberate decision on whether
  and how to pursue the combining-form category next.