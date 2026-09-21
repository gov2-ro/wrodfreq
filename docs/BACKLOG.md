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
  remains open. *(Superseded 2026-09-17 — check 2 now passes; the
  remaining gap turned out to be wordfreq's own tie structure, not a
  tokenizer issue, and the check was re-specified. See the last two
  entries in this file.)*

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

- [x] **Combining-form compounds investigated and closed — measured worth
  +0.001 rho, not the dominant driver.** Resolved 2026-09-17. The previous
  session's hypothesis (that Romanian combining-form compound adjectives —
  `austro-ungar`, `socio-economic`, `daco-roman` — were the main remaining
  cause of check 2's gap, the way clitic elision had been) does not survive
  measurement, so no tokenizer change was made:

  | test | rho |
  |---|---|
  | baseline | 0.8628 |
  | the 32 curated combining forms (`socio`, `austro`, `daco`, `pseudo`, `geto`, `româno`, …) removed from the comparison entirely — an **upper bound** on what any fix could buy | **0.8637** |
  | all 6,659 ">=15 compounds" prefix candidates removed | 0.8367 — *worse*, i.e. they are mostly ordinary words, not a defect class |

  Removing the 200 worst residuals outright only moves 0.863 → 0.871;
  reaching 0.909 takes deleting ~2,000 words. There is no fixable tail, and
  the riskier splitting rule the entry worried about would have bought
  essentially nothing. Good instinct to stop and measure first.

- [x] **Check 2 re-specified from Spearman rho to pairwise concordance.**
  Done 2026-09-17 — spec §11.2 rewritten, `validate.py` check 2 rewritten,
  now **passing at 0.954** (gate 0.93). Root cause of the stuck 0.863 is the
  *reference*, not our table: `wordfreq`'s Romanian list has only 356
  distinct zipf values across the 43,095 words we share with it, 12,601 of
  them in the 3.00–3.25 band alone — ~600 words per tied value, a bucket
  spacing narrower than our own per-word disagreement, so within-band
  ranking is a coin flip (within-band rho 0.28–0.58 throughout).

  Two findings settle that it is tie noise and not a divergence of ours:
  restricting to wordfreq's *more confident* words makes rho **worse**
  (0.796 at wf zipf>=4.5), which is backwards for a real divergence; and
  `ours - wordfreq` is a flat, symmetric median -0.15 / IQR 0.29 in **every**
  band, where a tokenizer bug is skewed and band-dependent. Pearson on the
  raw values is 0.911, top-1000 overlap 801/1000. The uniform -0.15 is a
  ~1.4x denominator difference — the expected signature of spec §3.2's
  honest denominator, not a defect.

  The replacement metric asks what survives the ties — *when wordfreq
  separates two words by enough to mean something, do we order them the
  same way?*

  | wordfreq separation | we agree |
  |---|---|
  | >=0.3 zipf (the gate) | 95.4% |
  | >=0.5 zipf | 98.3% |
  | >=1.0 zipf | 99.9% |

  Computed **exactly**, not sampled (Fenwick sweep over the
  reference-sorted list, 569,644,768 pairs in 2.2s) — a seeded sample would
  still drift the moment the word list changes, and this gate has to be
  reproducible run to run. Verified against a brute-force double loop on 500
  randomised cases including duplicate values. Rho is still printed ungated
  alongside Pearson, median delta and IQR, to watch drift on. **validate.py
  now reports 5/5 gated checks passing.**

  Still-open observation, unchanged and not ours to fix: `ul`/`ului`/`uri`/
  `urile` remain wordfreq's biggest divergences in the *opposite* direction
  (it scores Romanian noun-inflection suffixes at 4.6–5.6 zipf), a
  subword-segmentation artifact on its side from splitting `site-ul`-shaped
  words. The two residual tails are otherwise corpus panel, not
  tokenization: we underrate toponyms and proper nouns (`napoca`,
  `dobrogei`, `rebreanu`, `babeș`) where wordfreq is subtitle/Wikipedia
  skewed, and overrate contemporary news/admin vocabulary (`vaccinare`,
  `ciolacu`, `migranți`, `fotovoltaice`, `pensiilor`) — which is the
  five-source contemporary panel doing exactly its job, given wordfreq's
  Romanian predates most of it.

- [x] **Tokenizer emitted empty and hyphen-edged tokens; one shipped.** Fixed
  in code 2026-09-18 (`wrodfreq/tokenizer.py`), **data cleanup still open —
  see the next entry.** Found by reading check 5's top-100-by-spread report
  by eye, which is exactly what that check exists for: `baden-w` sitting in
  the list was the thread to pull.

  `_TOKEN_RE`'s inner class `[a-zăâîșț\-']*` permits *consecutive* hyphens, so
  dash typography (`eu--eu`, `spune--mi`, `într--adevăr` — overwhelmingly
  subtitles) arrived at `_split_elisions` as a single token. Its naive
  `word.split("-")` turned the resulting empty parts into malformed output:

  | input | was | now |
  |---|---|---|
  | `într--o` | `['într', '', 'o']` | `['într', 'o']` |
  | `într--adevăr` | `['într', '-adevăr']` | `['într', 'adevăr']` |
  | `spune--mi` | `['spune-', 'mi']` | `['spune', 'mi']` |
  | `eu--eu` | `['eu--eu']` | `['eu', 'eu']` |

  **The empty-string token reached the shipped data file**, where
  `zipf_frequency('')` answered **2.34** — wordfreq returns `0.0`, so this
  was a live violation of the drop-in API contract, not just an untidy row.

  A run of 2+ hyphens is now a token boundary. Deliberately *not* collapsed
  to a single hyphen instead: the dominant real cases are subtitle
  repetitions (`eu--eu`, `sunt--sunt`, `este--este`, `noi--noi`, `tu--tu`,
  `doar--doar`), and collapsing would manufacture the nonsense compound
  `eu-eu`. The single-hyphen elision rules from 2026-09-17 are untouched and
  re-asserted (`mass-media`, `site-ul`, `cluj-napoca` stay joined; `într-o`,
  `avut-o` still split; `ii-a` still guarded).

  Tokenizer tests went 10 → 55. The new ones assert the *invariants* the bad
  rows violated — no empty token, no leading/trailing/doubled hyphen — over
  every fixture, adversarial hyphen/apostrophe soup, and 3,000 random
  hyphen-alphabet strings. 130/130 passing repo-wide.

- [x] **Data cleanup for the doubled-hyphen fix — done 2026-09-18.**
  `build/migrate_dashes.py` repaired all 43,137 malformed rows in
  `source_counts`; stages 2-5 re-run and `validate.py` back to **5/5, still
  byte-identical on re-run** (check 6). `zipf_frequency('')` now returns
  `0.0`, matching wordfreq; `merged` holds zero empty, leading-hyphen,
  trailing-hyphen or doubled-hyphen rows.

  Scope, as measured beforehand:

  | class | rows in `source_counts` | rows in `merged` |
  |---|---|---|
  | empty string | 3 | 1 |
  | leading hyphen (`-adevăr`) | 1,140 | 61 |
  | trailing hyphen (`spune-`) | 4,596 | 198 |
  | doubled hyphen (`eu--eu`) | 39,274 | 980 |

  **43,137 rows, 75,442 occurrences out of 28,217,964,718 — 0.00027% of the
  panel.** No Zipf value of any real word moves measurably; the whole point
  is removing ~1,240 nonsense entries from `merged` and fixing
  `zipf_frequency('')`. Every affected entry sits below Zipf 2.1 except the
  empty string itself.

  Took the `build/migrate_elisions.py` route rather than a re-ingest —
  occurrence counts are exact per token, so re-running the *new*
  `_split_elisions` over just these 43,137 stored words and redistributing
  their counts reproduces what a corrected tokenizer would have counted from
  scratch. Carries the same accepted approximation as that migration
  (`documents` becomes a slight upper bound for split words). Only malformed
  words needed scanning, and that is provable rather than a shortcut: the new
  rule differs from the old one *only* where `word.split("-")` yields an
  empty part. `_split_elisions('')` returns `['']` via its no-hyphen early
  return, so the migration filters empty parts explicitly instead of relying
  on it.

  **Net +52,404 tokens** across the panel (`eu` +26, `news` +269, `subs`
  -1,015, `web` +52,910, `wiki` +214 — `subs` goes negative because
  subtitles carry most of the empty-string rows and few `eu--eu`-shaped
  ones). `merged` went 6,050,911 → 6,050,327 words. Every function word is
  unchanged to 2dp (`de` 7.70, `și` 7.40, `la` 7.22, `cu` 7.06, `un` 6.93),
  as is `într` at 6.05 — the migration moved nothing real, which was the
  prediction.

  **29 rows were dropped outright rather than repaired, and all 29 deserved
  it**: the 3 empty-string rows (6,256 + 1,745 + 101 occurrences) plus 26
  rows that are nothing but a run of hyphens — `-`, `--`, up to one
  170-hyphen horizontal rule from web text. None could have come from
  `_TOKEN_RE`, which requires a leading letter; they were manufactured
  entirely by the old buggy split, e.g. `a-----------b` collapsing its empty
  parts into a `-----` token. 8,274 occurrences removed in total.

  Instead of a 3.74 GiB copy of the db with 7.8 GiB free, the rollback
  snapshot is `data/checkpoints/pre_dash_migration.db` — 1.5 MiB holding the
  43,137 affected `source_counts` rows and the 5 pre-migration
  `sources.total_tokens` values, which is exactly enough to reverse it
  (everything else in the db is derived and rebuildable from
  `source_counts`). Cheaper *and* safer than a full copy on a tight disk;
  worth reaching for first next time a migration touches a bounded row set.

- [ ] **Non-Romanian diacritics split foreign words mid-token.** Mechanism
  confirmed, magnitude bounded, deliberately not chased — logged so the next
  person doesn't rediscover it from scratch. `_TOKEN_RE`'s character class is
  `[a-zăâîșț]`, so any other diacritic terminates the match and restarts it:

      Düsseldorf -> ['d', 'sseldorf']      Köln   -> ['k', 'ln']
      Zürich     -> ['z', 'rich']          François -> ['fran', 'ois']
      Baden-Württemberg -> ['baden-w', 'rttemberg']

  The tail fragments are real, reliable rows in `merged` (`nchen` 3.55,
  `rich` 3.48, `sseldorf` 2.94, `rttemberg` 2.93, `rnberg` 2.84 — most
  attested by all 5 sources), so this is genuine pollution, not a one-corpus
  artifact. **But the leading fragments, which is where the damage would
  actually show, are not detectably inflated**: our single-letter Zipf values
  track wordfreq's own within ~0.05 for the common letters (`a` 7.43/7.45,
  `s` 6.50/6.49, `l` 6.17/6.18, `m` 5.82/5.84, `v` 5.51/5.50). Single letters
  are genuinely frequent in Romanian text and wordfreq agrees with us about
  how frequent.

  So the cost is spurious *low-frequency* entries, not corrupted
  high-frequency ones — the opposite of the elision bug, which wrecked `într`
  at 3.45 vs its true 6.05. Widening the class to cover Latin-1 diacritics
  would fix the fragments but changes what counts as a Romanian token, which
  is a spec §3 decision and needs the same measure-first treatment the
  combining-form question got.

- [ ] **`zipf_frequency` does not tokenize its argument; wordfreq's does.** Noted
  2026-09-18, no action taken — flagging a real difference in the drop-in
  contract (spec §10.1), not asserting it is wrong. Ours normalizes and looks
  up one key, so `zipf_frequency('spune-')` finds the stored row (or 0.0);
  wordfreq tokenizes first, so it answers 5.78 — the value for `spune`.
  Likewise `-adevăr` -> 5.03 (`adevăr`). For well-formed single words, which
  is what the API is for, the two agree exactly; they diverge only on input
  that isn't a single token. Worth a deliberate decision before 1.0: matching
  wordfreq here means deciding what a multi-token argument should return.

- [ ] **The web corpus tokenizes URL slugs and punycode into `merged`.**
  Measured 2026-09-21 while verifying the dash migration, which is how it
  surfaced — **pre-existing, not caused by that migration**, which only
  *renamed* some of these (`live---postaci-…` → `postaci-…`, correctly, as
  the fixed tokenizer would). Logged for a decision, not because anything is
  broken.

  **23,425 `merged` entries carry 3+ hyphens**, URL-slug shaped. Examples,
  with the count each happened to gain in the migration:

  | entry | zipf |
  |---|---|
  | `postaci-antivaccin-forumurile-adevarul` | 1.20 |
  | `are-cookies-how-do-they-work` | 0.71 |
  | `increasing-and-enhancing-yourinternet-surfing-experience` | 0.63 |
  | `buletinul-comisiunii-monumentelor-istorice` | 0.10 |
  | `woocommerce-product-attributes-item` | -0.17 |
  | `xn--mavapress-mfb` (punycode IDN, now `mavapress-mfb`) | below floor |

  The mechanism is `_TOKEN_RE` permitting internal hyphens — necessary for
  `mass-media` and `cluj-napoca`, and there is no way to tell a compound from
  a slug by shape alone. Scraped web text simply contains URLs with the
  scheme and dots stripped by the character class, leaving the hyphenated
  path.

  **Stakes are genuinely low**, which is why this is a decision and not a
  bug: every example sits near Zipf 0, no consumer queries a URL slug,
  `top_n_list` is untouched, and 15,289 of the 43,390 words the migration
  touched fell below the floor and cost nothing at all. The argument for
  acting is tidiness plus check 5 — a `spread` report is meant to be read by
  eye, and slugs are noise in it.

  If pursued, the honest options are a length cap, a hyphen-count cap, or a
  minimum-`documents` requirement — **each needs the measure-first treatment
  the combining-form question got** (docs/BACKLOG.md, 2026-09-17), because a
  blanket rule also deletes real hyphenated Romanian. Note `documents` is the
  most promising discriminator and the one this repo already stores: a real
  compound appears across many documents, a slug appears in one page's
  boilerplate many times. Nobody has measured that split yet.

- [ ] **`social` source (Romanian subreddits) — ingester written and tested,
  waiting on the data download.** Started 2026-09-21. `build/ingest_social.py`
  is complete, tested end to end against a synthetic dump, and blocked only on
  a manual torrent fetch (see below). Chosen over `books` because `books` is
  `period='historical'` and would be excluded from the default contemporary
  merge — it would not move a single shipped number. `social` targets the one
  band where the panel is measurably thin: trim engagement is 99.7% at zipf>=5
  but only 24.9% at zipf 2-3, and this takes the trim from averaging 3 values
  to 4.

  **The download is manual and cannot be automated.** No API, no usable HF
  mirror (the one published Romanian Reddit corpus, arXiv 2410.09907, is 23k
  samples — two orders of magnitude too small; our smallest source, `eu`, is
  84.7M tokens). Route is Watchful1's per-subreddit extract of the Pushshift
  dumps on Academic Torrents, `1614740ac8c94505e4ecb9d88be8bed7b6afddd4`
  ("Subreddit comments/submissions 2005-06 to 2024-12"). The top ~40,000
  subreddits are separate files, so a client can fetch only what is needed
  rather than the multi-TB whole. Get at minimum `Romania_comments.zst` and
  `Romania_submissions.zst` into `data/raw/social/`; more Romanian subreddits
  can be added later and re-ingested incrementally, since the checkpoint is
  per file. Note the dumps run to **2024-12**, not the "pre-2023" spec §6
  assumed.

  **Then calibrate before the real run** — `--calibrate` prints the keep rate,
  a breakdown of why documents were dropped, and near-miss examples. The
  language-filter thresholds were necessarily chosen without access to the
  corpus and should be tuned against what it reports.

  Three decisions this source forced, all argued in the module docstring:

  1. **Language filtering, a first for this repo.** Every other source is
     pre-tagged or monolingual by construction. English contamination here is
     worse than the URL-slug noise, because `the`/`and`/`is` are valid token
     shapes and would land in a Romanian table with real frequencies. The
     filter is an English-vs-Romanian discriminator specifically — that is
     what makes it tractable — with every RO/EN homograph (`care`, `face`,
     `are`, `in`, `la`, `a`, `o`, `e`) deliberately excluded from the marker
     set. A test asserts none creeps back in, because adding one is the
     obvious wrong fix when a keep rate looks low.
  2. **`documents` stays "one comment", not "one author"**, departing from
     spec §7.2. Per-word distinct-author counting needs a (word, author) set
     spanning the corpus — the unbounded memory §7.2 itself forbids — and
     changing the unit would make the column mean something different here
     than in the other five, destroying the cross-source comparability that
     makes it useful. §7.2's actual concern is answered by *measuring* it
     instead: distinct author count and the top-100 authors' share of
     documents both go into `sources.period_note`. Same precedent
     `ingest_subs.py` set counting lines rather than films.
  3. **Markdown and URLs stripped before tokenizing**, so this source does not
     repeat the 23,425-entry slug problem the web corpus already has.

  Open sub-question for when the data lands: **how many subreddits?** Started
  with r/Romania alone since it is much the largest, but the arXiv paper used
  100+. The ingester takes a directory, so widening is free — worth measuring
  the token gain against the language-filter noise before hauling more down.

- [x] **CI exists — added 2026-09-21.** Spec §11's heading is literally
  "Validation — the stage that will be skipped, so make it CI", §13's M6
  criterion was "validation checks 1-4 and 6 pass in CI", and CLAUDE.md says
  `validate.py` "must run in CI and fail the build". There was no
  `.github/` at all. Now `.github/workflows/ci.yml` runs on push to main, on
  PRs, and on demand, in three jobs:

  - **tests** on Python 3.10 (the `requires-python` floor) and 3.13, so a
    3.10-incompatible syntax slip fails here rather than for whoever pip
    installs on an older interpreter.
  - **build scripts load** — `compileall` plus `--help` on every `build/*.py`.
    These have no other coverage, and a bad import in an ingester would
    otherwise surface hours into a multi-day job.
  - **wheel builds and imports** — builds sdist+wheel, installs into a clean
    venv and imports it. The data file is gitignored, so the wheel built there
    has none, which makes this a real test of spec §10.1's "degrade rather
    than explode" requirement.

  **What deliberately does NOT run in CI, and why.** `validate.py`'s checks 1,
  3 and 5 read `data/wrodfreq.db` — 3.7 GB, the output of multi-day ingests,
  gitignored by "no .db in git, ever" — and checks 2 and 4 cross-reference a
  local oțios checkout. Committing a fixture database would break the repo's
  own rule, so the pipeline's *logic* is covered by `tests/test_pipeline.py`
  instead: it builds a synthetic 5-source database in `tmp_path` and runs
  stages 2-5 over it. Running the real `validate.py` against the real corpus
  stays a local pre-release step, and that is now written down rather than
  assumed.

  `tests/test_pipeline.py` (11 tests) asserts spec §11.6's byte-identical
  idempotence for compute_zipf, merge and the package payload — the property
  §14 calls "the cheapest bug detector you have" — plus the merge rules
  CLAUDE.md calls "the rules that decide whether the table is right", on data
  where the expected answer is computable by hand: a source abstains rather
  than reporting zero, a word below the floor everywhere is omitted rather
  than zeroed, floors are derived per source (asserted against
  `source_zipf_floor` exactly, not by proxy), the trim engages at 5 and drops
  the extremes, 1-4 reliable takes a plain mean, function words land in the
  band that proves the denominator, monotone ordering survives, and the merge
  does not track the largest source.

  166 tests, passing in a clean clone with no build artifacts present —
  verified by actually cloning and installing, not assumed.
