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

- [x] **Non-Romanian diacritics split foreign words mid-token.** Mechanism
  confirmed; **measured 2026-10-02 (brief B2). DECIDED 2026-10-05: do not widen the
  class; accept the limitation in 0.2.0 (documented in `docs/method.md` §7); the
  legacy-diacritic normalisation is tracked separately below and rides the next
  re-ingest.** `_TOKEN_RE`'s class is `[a-zăâîșț]`, so any other
  letter terminates the match and restarts it:

      Düsseldorf -> ['d', 'sseldorf']      Köln   -> ['k', 'ln']
      Zürich     -> ['z', 'rich']          François -> ['fran', 'ois']
      Baden-Württemberg -> ['baden-w', 'rttemberg']

  **Method and its limit.** `source_counts` holds only already-tokenized words,
  so the foreign characters are gone from the db and the problem cannot be
  measured there directly. Measured instead by re-reading raw text with a
  Unicode-letter regex beside the production tokenizer: all of Wikipedia
  (442,389 docs, 110.5M tokens, matches `sources`) and ~187M tokens of CulturaX
  `web` (row groups from `ro_part_00000` and `ro_part_00040` — a 0.8% sample of
  `web`, from two shards). News, subs and eu have no raw text on disk: their
  figures below are extrapolated from the density of wiki-derived fragment
  types in their `source_counts`, and are estimates. The db was opened
  read-only. Wordfreq lives in oțios's venv, not this one.

  **1. Volume.** Words containing a non-Romanian Latin letter: wiki 0.73% of
  tokens (159,881 types), web sample 0.125% (62,179 types). They shatter into
  1.86 fragments each, so fragment tokens are **1.36% of wiki and 0.20% of web**
  (93,570 and 36,335 types). Estimated for news/subs ≈0.08%, eu ≈0.2%. In the
  shipped db, the 19,203 "signature" fragment types (multi-letter, ≥90% of
  their wiki occurrences foreign-derived — a lower bound, since it is wiki-only)
  appear in 16,433 `merged` rows (0.27% of 6,050,327; 16,001 not DEX), 5,142 of
  them with ≥3 reliable sources and 509 with all five. Their `merged` Zipf is
  almost all in the long tail: 15,490 of 16,433 sit below 2.0, 232 at 2.5 or
  above, **52 at ≥3.0** (33 of them non-DEX; `nchen` 3.55, `rich` 3.48, `sseldorf` 2.94 ...). By
  per-source floor they are overwhelmingly "reliable" (11,132 of 19,203 clear
  wiki's floor, 15,202 clear web's) — but web's floor is -0.68, so clearing it
  means little. Pollution is real and cheap: ~0.3% of rows, almost none loud.
  Non-Latin foreign scripts (Cyrillic, Greek, CJK) are not fragmented, they are
  dropped whole: wiki 0.14% of tokens, web 0.04%, absent from numerator and
  denominator alike.

  **2. Displacement.** The earlier "single letters track wordfreq within 0.05"
  was true only of the common letters and is **overturned for the rest**:
  merged vs wordfreq is k +0.37, w +0.35, g +0.34, j +0.33, h +0.29, r +0.27,
  t +0.22 (and a, o, s, v, y, l, m within 0.02). The fragments explain only a
  minority of that. Recounting with foreign-Latin words excluded (numerator and
  denominator), per-source single-letter Zipf shifts: wiki k −0.46, z −0.32,
  r −0.31, g −0.25, t −0.22, b −0.17 (a, o, x, i ≈ 0); web k −0.13, z −0.10,
  t −0.09, r −0.09, j −0.07. Every non-fragment word shifts by only +0.006
  (wiki) / +0.001 (web), the denominator effect. Pushed through the merge's
  trimmed mean (wiki/web measured, others extrapolated): **22 of 26 letters move
  by ≥0.005 and so change at 2dp, 6 by ≥0.05, max k ≈0.07** — most of the
  wordfreq gap on k/w/j/g/h is corpus composition, not fragments. Function
  words are untouched (`a` −0.004, `o` −0.001, `x` 0.000 in wiki).

  **3. Which characters.** By occurrences (wiki / web sample): é 175k / 25k,
  á 104k / 16k, ü 67k / 8k, ö 55k / 6k, ó 47k / 9k, è 37k / 5k, í 31k / 5k,
  š 29k / 2k, ä 29k / 5k, ć, ç, č, ı, ł, ô, ž ... Latin-1 (≤U+00FF) covers only
  **75% of wiki's and 83% of web's** foreign-Latin occurrences; Latin-1 +
  Extended-A/B (≤U+024F) covers 98.7% / 98.8%, so a "Latin-1 widening" is not
  actually sufficient (Polish/Czech/Turkish/Hungarian proper nouns). Wiki's are
  genuine foreign names (münchen, josé, françois, köln, zürich, lászló). **Web's
  are largely Romanian typed wrongly**: `ã` is the top foreign character in
  web (13,690 types, 87,914 occurrences = 0.047% of tokens, 89% of which become
  an established Romanian word on substituting `ă` — `sã`, `cã`, `dupã`,
  `aceastã`); `ǎ` (94% ok), `ȋ`→`î` (95%), `ȃ`→`â` (90%) and `þ`→`ț` (72%)
  likewise. Half (50.6%) of web's foreign-Latin occurrences have a common
  Romanian word as their accent-stripped form, vs 9.2% in wiki. In wiki `ã` is
  Portuguese (`são`, 17% map to a RO word), so a blanket `ã→ă` is not free.

  **4. Cost of widening.** Admitted as new tokens in place of fragments: in
  wiki 96,902 types / 583k occurrences of Latin-1 letters (85% of the types
  under 5 occurrences) and 55,639 types / 201k beyond Latin-1 (88% under 5);
  in web 26,840 / 89k and 15,360 / 41k (89–91% under 5). A further
  mojibake-prone slice (`å ÿ ð ý þ æ`: wiki 3,641 types, web 6,701, 88–89%
  under 5 occurrences; `åÿi` is in web's top foreign words) and ~9k–32k words
  with ≥2 distinct foreign characters. Mostly hapax, which the
  `MIN_OCC_PER_SOURCE` abstention keeps out of `merged` — but the wrong-keyboard
  words (`sã`) would become **their own reliable, high-count entries**
  rather than fixing the fragment `s`: widening trades one kind of junk for
  another there. Normalizing the legacy variants in `normalize()` would do the
  job properly, but that is a different change.

  **5. wordfreq.** It does not fragment: `tokenize('Düsseldorf','ro')` →
  `['düsseldorf']`, `Köln`, `Zürich`, `François`, `München`, `Škoda`, `Łódź`
  all whole; `Baden-Württemberg` → `['baden', 'württemberg']`. And
  `zipf_frequency('nchen','ro')`, `sseldorf`, `rnberg`, `rttemberg` are all 0.0
  there (we ship 3.55, 2.94, 2.84, 2.93). So widening would *increase*
  comparability, not reduce it — the opposite of the worry in the brief. It is
  not a strong argument: the Spearman gate covers only wordfreq's range, where
  the fragments are absent.

  **Recommendation: do not widen.** Re-ingest cost is days (`web` alone), for
  a measured benefit of ≈0.02–0.07 on 22 rare letters' Zipf, 52 `merged` rows
  at ≥3.0 (16,433 rows overall, 0.27%), and nothing on any function word or
  common letter. The harder question the numbers raise is the *legacy-diacritic
  normalization* (`ã ǎ ȋ ȃ` → `ă î â`, perhaps `þ`→`ț`): it moves real words
  (web: 3,214 Romanian words gain ≥0.005 Zipf, up to +0.58 for `învătământ`;
  `ã`-fragments are 0.047% of web tokens, ~11M occurrences at scale), and
  belongs in the same decision — see `normalize()`. If a re-ingest is ever
  forced for another reason, fold that in. Scripts kept in the scratchpad, not
  committed. Timing: wiki 129 s, web ~255 s of streaming, db queries ~2 s.

- [x] **`zipf_frequency` does not tokenize its argument; wordfreq's does.** Noted
  2026-09-18; measured 2026-10-02 (Brief B3) against wordfreq 3.1.1 (the copy in
  `~/devbox/otios/.venv` — the one `validate.py` uses; this repo's `.venv` has no
  wordfreq). **Resolved 2026-10-02: ADR-001 decided, Brief B5 implemented.**
  `zipf_frequency`/`word_frequency` now tokenize and combine harmonically; numerals
  and unconsumed letters return `minimum` (documented divergences); the extensions
  keep exact lookup. Two departures from the proposal below: **no exact-key-first
  path** (ADR-001 decision 2 — the tokenizer is idempotent on its own output, so it
  was redundant, and the 225 apostrophe rows it existed to protect were migrated out
  by `build/migrate_apostrophes.py`), and conflict A resolved as option (i). The
  text below is the original B3 record, kept for the measurements.

  *What wordfreq does* (`_word_frequency`, `wordfreq/__init__.py:~237`): tokenize;
  **zero tokens -> `minimum`**; look each token up (digit runs are smashed to `0`s
  and scored by a digit-frequency model); **any token missing -> `minimum`** for
  the whole string; otherwise combine **harmonically**, `1/f = 1/f1 + 1/f2 + ...`
  (not first, not min, not mean — always below the rarest token), round to 3
  significant digits, then `zipf_frequency` rounds to 2 decimals. Ours: normalize,
  one exact key lookup, no tokenization.

  Divergence table (value differences on well-formed words, e.g. `de` 7.72 vs
  7.70, are the data, not this issue; shown as wordfreq / ours):

  | Input | Our tokenizer yields | wordfreq | ours now |
  |---|---|---|---|
  | `de`, `De`, `DE`, `ţară` | 1 token | answers | answers (agree on shape) |
  | `spune-`, `-adevăr`, `pământ-` | 1 (edge hyphen dropped) | answers for the stem (5.78, 5.03, 5.12) | 0.0 |
  | `(de)`, `'de'`, `  de  ` | 1 | 7.72 | 0.0 |
  | `de la`, `de,la`, `de\nla` | 2 | 7.09 (harmonic) | 0.0 |
  | `de la cu` | 3 | 6.77 | 0.0 |
  | `spune--mi`, `într-o`, `n-am` | 2 (elision split) | 5.58, 6.04, 6.00 | 0.0 |
  | `de cuvântcarenuexista` | 2, one unknown | 0.0 (`minimum`) | 0.0 (agree) |
  | `mass-media`, `site-ul`, `e-mail` | 1 (kept joined, in table) | splits and combines: 4.41, 5.18, 4.45 | exact row: 4.36, 4.90, 4.32 |
  | `d'ale` | 1 (kept joined) | splits: 5.71 | exact row: 2.18 |
  | `''`, `'   '`, `'...'`, `'!?'` | 0 | 0.0 | 0.0 (agree) |
  | `123`, `1990`, `3.14`, `50%`, `$5` | 0 (numerals excluded by construction) | **3.92, 4.63, 2.26, 4.85, 5.81** | 0.0 |
  | `de 123` | 1 (`de`) | 3.92 | 0.0 |
  | `covid-19`, `a1b` | 1 / 2 (`a`,`b`) | 0.0 | 0.0 (agree; ours would combine fragments `a`+`b` if tokenized — wrong) |
  | `café`, `naïve` (foreign diacritics) | `caf` / `na`,`ve` (letters silently dropped) | 3.44 / 0.0 | 0.0 |
  | `ro.wikipedia.org` | 3 | 0.0 (one token, not in list) | 0.0 |
  | `minimum=2.0` with any unknown/zero-token input | | returns 2.0 | returns 2.0 (agree) |

  **Proposed behaviour** (API layer only, using `wrodfreq.tokenizer.tokenize`;
  `word_frequency` has the same issue and must combine in the *linear* domain,
  not by round-tripping through the 2-decimal zipf):
  1. Normalize, try the **exact key first**. A hit returns as today. Reason:
     keeps compounds (`mass-media`) and the 225 legacy rows that re-tokenizing
     cannot reach (190 trailing-`'` such as `acu'`, 35 leading-`'`) answerable.
  2. Otherwise tokenize. **Zero tokens -> 0.0 (`minimum`)**, as wordfreq.
  3. **One token -> its row** (`spune-`, `(de)`, `'de'`, `DE`): the edge
     punctuation is not part of the word.
  4. **Two or more tokens -> harmonic combination, linear domain, any unknown
     token -> 0.0 (`minimum`)**, round to 2 decimals: matches wordfreq's
     mechanism exactly; spreads the elision splits (`într-o`) the same way.
  5. **Do not silently drop content.** If the argument contains letters or digits
     our tokenizer did not consume (`café`, `naïve`, `a1b`, `de 123`) answer
     0.0 rather than combining the surviving fragments (`caf`, `na`+`ve`) —
     tokenize-first with *our* tokenizer would otherwise fabricate answers.
  Extensions (`frequency_detail`, `by_source`, `lemma_frequency`) keep the
  exact-lookup, single-row behaviour (see conflict A).

  **CONFLICTS WITH THE PROJECT'S OWN CONTRACT — this is the decision, not
  resolved above:**
  - **A. `0.0` <=> `None` stops being the same fact.** Today
    `zipf_frequency(w) == 0.0` exactly when `frequency_detail(w) is None`. After
    tokenize-first, `zipf_frequency('spune-')` is 5.78 while
    `frequency_detail('spune-')` is `None` (a multi-token string has no single
    row, so no `n_reliable`/`spread` exist for it). Options: (i) leave the
    extensions exact-lookup and document the asymmetry; (ii) make
    `frequency_detail`/`by_source` also strip to a single token (`spune-` ->
    `spune`'s detail) but stay `None` for 2+ tokens; (iii) don't tokenize at
    all and document `zipf_frequency` as exact-key (breaks the "change one
    import line" claim for non-single-token input).
  - **B. Numerals.** wordfreq answers `123` with 3.92; our table excludes
    numerals from numerator and denominator *by design*. Matching requires
    either a numeral model (a spec §3 decision, borrows wordfreq's `digit_freq`)
    or accepting a permanent divergence where we say 0.0 — which this project
    otherwise reads as "never seen", not "not a word". `None` would be more
    honest than `0.0` here but wordfreq's contract has no `None`.
  - **C. Foreign diacritics (`café` 3.44 in wordfreq).** Matching needs a
    wider token class — that is Brief B2's tokenizer decision, out of scope.
    Rule 5 above is a stopgap that gives 0.0, not a match.
  - Not a conflict, for the record: zero tokens (`''`, `'...'`) is 0.0 in both,
    matching the existing expectation (the `''` -> 2.34 bug fix; the suite pins
    only the tokenizer invariant "no empty token", not an API assertion).

  Blast radius: no existing test or `validate.py` check depends on
  non-tokenizing behaviour. `tests/test_api.py` calls the API only with
  single well-formed tokens (`de`, `birjă`, `cuvântcarenuexista`); `validate.py`
  never calls `wrodfreq.zipf_frequency` (it reads the DB, and calls
  *wordfreq's* `zipf_frequency` on wordfreq's own list, all single tokens).
  The `test_b3_*` cases in `tests/test_api.py` are now live (un-skipped by B5).

- [x] **The web corpus tokenizes URL slugs and punycode into `merged`.** (3+ hyphens: filtered at packaging, 2026-10-05; the 1–2-hyphen junk is still unmeasured.)
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

  **MEASURED 2026-10-05 (read-only, 0.2.0 `merged`, 6,064,995 rows). `documents` does NOT
  discriminate; hyphen count does.**
  - 902,490 rows (14.9%) contain a hyphen. By hyphen count: 1 → 819,273 rows (avg Zipf
    −0.13, 6,130 in DEX, 268 at Zipf ≥3); 2 → 59,764 (223 DEX, 4 at ≥3); **≥3 → 23,453
    rows (9 in DEX, 18 at Zipf ≥2, none above 3.2).**
  - Of the 22,450 `web`-attested rows with ≥3 hyphens, `documents` is spread everywhere:
    1 doc 1,394 · 2 docs 789 · 3–5 docs 8,523 · 6–20 docs 10,348 · >20 docs 1,396. A slug
    repeats across the pages of one site, so a documents floor would keep most of it and
    drop only the least harmful tail. Rejected as a discriminator.
  - 97.7% (21,931) of those are single-source (`n_reliable=1`); only 40 have `n_reliable≥3`.
    Those 40 are English or French compounds and interjections, not Romanian vocabulary:
    `state-of-the-art`, `out-of-the-box`, `saint-germain-en-laye`, `ha-ha-ha-ha`,
    `la-la-la-la`, `pnl-usr-plus-udmr`. All below Zipf 1.8.
  - What the ≥3 population is: URL slugs, English page titles, chemical names
    (`o-beta-d-galactopiranosil`), place lists (`tisa-iza-vișeu`), boilerplate
    (`cookielawinfo-checkbox-necessary`), stuttered interjections.
  - **Recommendation: drop rows with ≥3 hyphens when building the package (`build_package.py`),
    keep them in `wrodfreq.db`.** Costs 23,453 shipped rows (0.39%) and no function word,
    DEX lemma (9 rows) or row above Zipf 3.2; needs no re-ingest; reversible. Lost: ~40
    interjection/foreign compounds. **CORRECTION (same day):** the first draft of this
    said `zipf_frequency` would still answer them by combining the parts. It does not —
    the tokenizer keeps hyphenated words whole (`tokenize('state-of-the-art')` is one
    token), so a dropped row answers `0.0` / `None` (it was 1.66). `wordfreq` splits such
    words and combines them harmonically. **DECIDED and DONE 2026-10-05 (owner agreed):**
    `build_package.py` `MAX_HYPHENS = 2`; the shipped file has 6,041,542 words; the 23,453
    rows stay in `wrodfreq.db`. Open follow-up: an API fallback that splits an unknown
    hyphenated token on `-` and combines the parts as `wordfreq` does would restore a value
    for the ~40 and for any compound we never saw; it touches ADR-001's exact-token rule,
    so it needs its own decision.
  - Larger, separate finding: the 819,273 one-hyphen rows are 13.5% of the table and only
    6,130 are in DEX (268 at Zipf ≥3: real compounds like `cluj-napoca`, `e-mail`, mixed with
    junk). A one-hyphen rule would need its own measurement; a hyphen cap does not touch it.

- [x] **`social` source (Romanian subreddits) — ingester written and tested,
  waiting on the data download.** Started 2026-09-21. `build/ingest_social.py`
  is complete, tested end to end against a synthetic dump, and blocked only on
  a manual torrent fetch (see below). Chosen over `books` because `books` is
  `period='historical'` and would be excluded from the default contemporary
  merge — it would not move a single shipped number. `social` targets the one
  band where the panel is measurably thin: trim engagement is 99.7% at zipf>=5
  but only 24.9% at zipf 2-3, and this takes the trim from averaging 3 values
  to 4.

  **CORRECTED 2026-09-22 — the per-subreddit torrent does not exist.** An
  earlier version of this entry (and of the module docstring) said to fetch
  `Romania_comments.zst` from Academic Torrents hash
  `1614740ac8c94505e4ecb9d88be8bed7b6afddd4`, taken from a search result
  rather than a verified page. Checked properly:

  - that hash returns **404**, as do the three other per-subreddit hashes
    search results offer — *including the one in Watchful1's own
    `PushshiftDumps` README*;
  - the URL form `academictorrents.com/download/<hash>.torrent` is correct
    (200 + a 3.7 MB torrent for a hash that is real), so the syntax was fine
    and the hashes are simply dead;
  - Academic Torrents' own `database.xml` lists 34 Reddit entries and **not
    one is per-subreddit**. Parsing the real archive torrent
    (`ba051999301b109eab37d16f027b3f49ade2de13`) confirms it: **464 files,
    all whole-month** (`RC_2005-12.zst`, `RS_…`), **2.84 TiB**.

  Extracting r/Romania that way means pulling ~50 GiB per month to recover a
  few MB of Romanian. Not viable — the machine has ~11.5 GiB free. **The
  torrent route is dead, not merely inconvenient.**

  **Working route: the Arctic Shift HTTP archive**, no auth, verified live.
  `https://arctic-shift.photon-reddit.com/api/comments/search?subreddit=Romania&limit=100`
  returns real r/Romania comments carrying `author`, `body`, `subreddit` and
  `created_utc` — exactly the fields `record_text()` already parses, including
  the literal `[deleted]`/`[removed]` bodies it already skips. Page size caps
  at 100; measured throughput **~335k comments/hour** (12,000 in 129s), well
  above the ~120k/hour the docs suggest. The 12k sample spanned only
  2026-09-15..22, i.e. roughly one week, which puts full r/Romania history at
  an estimated 4-6M comments ≈ **15 hours**, not the 40-80 first guessed.

  Still to decide: whether to run that full crawl (days of traffic against a
  community service — worth asking before starting), and whether to widen
  beyond r/Romania.

  **Calibration done 2026-09-22, and it changed the rule** — which is the
  whole reason `--calibrate` exists. Against 12,000 live r/Romania comments
  the original rule kept 80.5% of judgeable documents and dropped 7.3% of the
  corpus *wrongly*: `Ma bucur ca a supravietuit!`, `De ce nu are sabie de
  dac?`, `Hai sa vedem pe cine mai ataca Rusia in afara NATO` — all
  unambiguously Romanian, all scoring `ro=1, en=0`, all discarded because the
  rule demanded two Romanian signals regardless of length.

  The missed insight: **absence of English is itself the strong signal**, and
  length barely matters. Romanian written without diacritics in a short
  comment does not trip two markers, and short comments are most of Reddit.
  The rule now keeps anything with one Romanian marker and no English at all,
  applying the stricter both-and test only when English is actually present.

  Measured effect: keep rate **80.5% → 88.5%** of judgeable documents, +877
  documents. Verified the relaxation is safe rather than assumed — of the 877,
  a hand-checked sample was uniformly Romanian, **zero** contained 3+ common
  English words, and **zero** URL-slug tokens appeared (a `digi24.ro/stiri/
  guvernul-aloca-peste-135-milioane-…` link produced no slug tokens at all,
  confirming the markdown/URL stripping works on real data). The ten real
  comments that exposed the bug are now regression tests.

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

  **DONE 2026-10-04.** Superseded: data fetched via `fetch_social.py` (not the torrent) and ingested 2026-10-04.

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

- [x] **`social` fetch running — `build/fetch_social.py`, 15 subreddits over
  HTTP.** Started 2026-09-22. Acquisition is now a separate stage from
  ingestion, deliberately: the tokenizer has already changed twice (elision
  2026-09-17, doubled hyphens 2026-09-18) and each change would otherwise have
  meant re-downloading rather than re-running a local stage.

  **Panel chosen by measurement, not guesswork** (`--probe`, 2026-09-22,
  comments/day):

  | tier | subreddits |
  |---|---|
  | high | `Romania` 2,729 · `CasualRO` 1,366 · `programare` 889 · `Bucuresti` 828 · `AskRomania` 709 |
  | regional | `moldova` 133 · `cluj` 124 · `Iasi` 94 · `Sibiu` 59 · `Timisoara` 53 · `Oradea` 49 · `Craiova` 47 · `Constanta` 33 · `Brasov` 24 · `romani` 12 |
  | probed and dead | `RoGaming`, `RomaniaMuiePSD`, `RomaniaTravel`, `RepublicaMoldova`, `RoStocks`, `financiarRO`, `RomaniaCorporate`, `universitate`, `RomaniaGaming`, `antiromania` (last post 2022), `baniRO`, `transilvania`, `ITjobsRomania`, `StiriDinRomania`, `RomanianFood` — recorded so nobody re-probes them hopefully |

  ~7,000 comments/day across the live panel today; full history is roughly
  11M records, ~33h at the measured 335k/hour.

  **Only four fields are stored** (`author`, `body`, `subreddit`,
  `created_utc`, plus `title`/`selftext` for submissions). A raw Arctic Shift
  record carries ~90 fields and runs 3-5 KB, which would be tens of GB of
  JSON to recover a few hundred MB of Romanian — against ~11.5 GiB free. The
  trade is explicit: re-deriving anything from score, flair or thread
  structure later means re-fetching.

  `moldova` is included on purpose — Moldovan Romanian is the same language,
  and its heavy code-switching is a real test of the language filter rather
  than a reason to exclude it. Worth checking its keep rate separately once
  the data lands.

  **DONE 2026-10-04.** Superseded: fetch complete, see "Finish the `social` acquisition crawl".

- [x] **Bot/moderator accounts were leaking boilerplate into the corpus —
  fixed 2026-09-22.** Caught by running the real fetcher rather than the
  synthetic fixture: `brasov-ModTeam` posts removal notices constantly ("Hi
  u/X! To reduce spam, accounts with less than 200 comment karma require
  moderator approval"), and the original filter caught `AutoModerator` and
  `*bot` but not `<subreddit>-ModTeam`, which every subreddit has. Now
  excluded, with regression tests.

  Deliberately **not** deduplicating repeated text: the unit of trust is the
  *author*, not the string. Real people repeat themselves ("da", "mersi",
  "asa e") and that is genuine Romanian usage — only mechanical repetition
  from bot/mod accounts is mechanical. Guard tests also assert ordinary
  accounts (`MakavelliRo`, `SfantulAsteapta`) are not swallowed by the
  deliberately broad `*bot` suffix.

  Two smaller fixes from the same run: `sources.period_note` still described
  the Watchful1 torrent route that does not exist, and the subreddit list in
  it double-counted every subreddit (once for `_comments`, once for `_posts`).

  Early independence reading on a 10k-document test: 2,997 distinct authors,
  top 100 accounting for 28.5% of documents — far healthier than the LUMRO
  case spec §7.2 warns about (111 authors, 638 of 1,425 rare words from one
  person).

- [x] **Finish the `social` acquisition crawl.** `build/fetch_social.py` has r/Romania
  comments complete (12,050,513 records back to 2010-03-30, 844.2 MiB `.zst`). Remaining:
  r/Romania posts, interrupted at 255,000 records back to 2022-02-06, plus all 14 other
  subreddits. Resume with `nohup build/run_social_fetch.sh > /dev/null 2>&1 &` — the
  checkpoint skips what is `done`. Measured cost basis: 73.4 bytes/record compressed,
  3.87× zstd-10 ratio, so sum-of-rates scaling off r/Romania puts the remaining 14 at
  ~19.5M comments and ~1.4 GiB compressed; ~2.4 GiB for the whole panel once compacted.
  **Free space is governed by the transient peak, not that total**: `compress()` runs once
  per subreddit at the end, so the uncompressed append-log and the finished `.zst` coexist
  — ~2.1 GiB for CasualRO, the largest remaining. Budget ~5 GiB free, ~8 GiB comfortable.
  After acquisition: `ingest_social.py`, then `compute_zipf.py --source social`, `merge.py`,
  `build_lemma_layer.py`, `build_package.py`, `validate.py`. The 6th source makes the panel ≥6.
  The §8 trim branch is **already the active path** (69,939 rows have `n_reliable=5`), so this
  does not newly reach it; it means the trim drops max and min from 6 values leaving 4 to mean
  rather than 3, and more words clear the ≥5 threshold. Check 1's function-word band and
  check 2's concordance still need re-reading after it.

  **DONE 2026-10-04.** All 15 subreddits fetched; ingested 2026-10-04 (13,717,928 documents, 536,523,553 tokens, 35.1m). Downstream stages rebuilt, `validate.py` 5/5, panel now six sources (`n_reliable=6` for common words). Check 1: `de` 7.68, `și` 7.34, `la` 7.23, `un` 6.94, `cu` 7.08. Check 2: concordance 0.948, ρ 0.851 (ungated).

- [x] **`COMPRESS_EVERY = 250_000` in `fetch_social.py` is dead code.** Defined, never
  read; `compress()` is called once per subreddit at the end of `fetch()`. Either wire it
  up to compact incrementally (which would cap the transient peak above, and matters if a
  subreddit ever dwarfs r/Romania) or delete the constant so it stops implying a buffering
  behaviour that does not exist.

  **DONE 2026-10-04.** Constant deleted from `fetch_social.py`; `compress()` still runs once per subreddit.

- [x] **The tokenizer is not idempotent on one corner: an apostrophe at an internal
  hyphen boundary.** Found 2026-10-02 by `migrate_apostrophes.py`'s own
  postcondition; fixed the same day (brief B6). `tokenize("da'-a'-a'")` gave
  `["da'-a'", "a"]` and `las'-o` gave `las'` — the elision split handed back pieces
  without re-applying the edge invariant `_TOKEN_RE` enforces. `_settle()` now re-runs
  each changed piece through the regex and the split to a fixed point, so every emitted
  token `t` satisfies `tokenize(t) == [t]`. Asserted by a property test over adversarial
  soup. No data migration was needed: all 6,050,118 `merged` keys reproduce exactly.

- [x] **`sources.period_note` for `social` must carry the independence and composition
  claim — write it from a full run of `build/measure_social_independence.py`, not from
  the sampled numbers below.** Spec rule: `documents` is an independence claim, and where
  a corpus has few authors you count them and say so. The cautionary case is LUMRO (175
  novels, 111 authors, 638 of 1,425 rare words from one person). A 15-subreddit source
  needs the same treatment for two reasons, and the second one is easy to miss.

  **1. Is it one community under 15 names?** Measured 2026-10-03 on the newest 150,000
  comments of each of the first four subreddits — the same recent window for each, so the
  comparison is fair:

  | vs r/Romania | shared authors | Jaccard | share of smaller sub |
  |---|---|---|---|
  | CasualRO | 6,425 | 0.235 | 33.5% |
  | programare | 4,390 | 0.173 | 29.0% |
  | Bucuresti | 5,477 | 0.206 | 31.2% |

  **No — roughly 70% of each subreddit's authors never post in r/Romania**, and author
  concentration is healthy (top 100 authors appear in ~2% of (subreddit, author) pairs,
  nothing like LUMRO). Marginal reliable vocabulary is still climbing at the fourth
  subreddit: 38,844 types from r/Romania alone, then +46.7%, +23.5%, +15.8%. So the panel
  is genuinely multi-community and more subreddits were worth the hours.

  **2. Nine of the fifteen are city subreddits**, which skews toponyms relative to
  national usage. r/Bucuresti alone contributes 847 reliable types that appear in no other
  subreddit, and they are overwhelmingly Bucharest street and neighbourhood names
  (`străulești`, `giurgiului`, `oltenitei`, `dămăroaia`, `băzilescu`, `tpbi`), mixed with
  genuine common nouns that only surface in urban-planning talk (`patinoar`, `suprateran`,
  `riveran`). This is real vocabulary and should **not** be pruned — a Romanian frequency
  table that cannot price `giurgiului` has a gap. But a reader has to be told, because
  `social` is one source of six, so a Bucharest street name lands at `n_reliable = 1` with
  a high `spread`, and check 5's top-by-spread report is where it will surface.

  `period_note` therefore states: the subreddit list; that 9 of 15 are city subreddits and
  toponyms are consequently over-represented; the distinct-author count and the top-100
  concentration; and the measured cross-subreddit author overlap. Follow the existing
  `subs` and `eu` notes for tone — they already disclose what `documents` does and does
  not count.

  Incidental, and independent confirmation of
  `docs/decisions/ADR-002-foreign-diacritics-and-legacy-variants.md`: `straulesti` and
  `străulești` both appear as separate reliable types in the same sample. The social source
  will carry the legacy-diacritic split too, so the +0.58 Zipf figure in that ADR is not
  confined to `web`.

  **DONE 2026-10-04.** `ingest_social.py` writes the note from the full run: 264,357 distinct authors across 13,717,928 documents, top 100 authors = 9.5% of documents; says `documents` counts comments, not authors.

- [ ] **LexicRo — evaluate what we can use, and what we can offer.**
  Full evaluation: **[`docs/lexicro-evaluation.md`](lexicro-evaluation.md)**.
  <https://lexicro.com/> — a hosted Romanian morphological-analysis API (lemma, POS,
  features, conjugation) on a fine-tuned `bert-base-romanian-cased-v1`; code MIT. The
  owner has had a brief exchange with the author and intends to offer wROdfreq's results
  for integration.

  The fit is disjoint: it does the morphology this project explicitly refuses to do, and
  has no frequency data, which is all this project does. Three things worth taking, in the
  doc with their caveats — the headline one being **MULTEXT-East (428k word forms,
  CC BY-SA 4.0)**, a clearly licensed morphological lexicon where the lemma layer's blocker
  is that `inflected_forms.db` is DEX-derived with unresolved terms. Not a free fix:
  coverage drops from 2,269,003 forms to 428k, the resource is weakest on modern vocabulary
  where open vocabulary is the point, and share-alike may bind the shipped data file —
  which cannot even be assessed yet, because **this project has no `LICENSE` file and no
  `license` field in `pyproject.toml`**. That is a prerequisite.

  One thing to raise with the author: LexicRo is built on DEXonline and RoLEX but states
  licensing only for MULTEXT-East and UD RRT, so its author has already met this project's
  deferred DEX blocker from the same direction. How they resolved it may unblock
  `lemma_frequency()` outright.

- [ ] **DEX Online licence — send the request, then write the answer into the methodology.**
  The lemma layer (`ro_lemma.msgpack.xz`, 180,569 lemmas, numbers only) now ships in the
  package and the project treats permission as granted, but no answer exists yet. Draft
  Romanian request: `docs/dex-online-cerere.md` (not sent). It asks three things:
  (1) the derived per-lemma numbers, (2) redistributing the form→lemma map
  (`inflected_forms.db`) as a release asset, (3) attribution wording. **Before publishing
  the GitHub release:** either get the answer, or ship only `extract_inflected_forms.py`
  and drop `inflected_forms.db` from the release assets. **When the answer arrives, add the
  licence terms and attribution to `docs/method.md` (section 5, the "Licență" note) and to
  `docs/sources.md`** — both currently say the terms are still being clarified.

- [ ] **GitHub Actions at each major release.** `.github/workflows/release-check.yml`
  (added 2026-10-05) runs on a `v*` tag: tag = pyproject = `__init__` version, tests on
  Python 3.10–3.13, wheel built and installed into a clean venv, `docs/method.md` and
  `docs/sources.md` present. It does not publish, and it cannot run `build/validate.py`
  (the 3.7 GB database is not in git): run it locally before tagging and paste the
  "N/N checks passed" line into the release notes. Decide whether to add a publish job
  (PyPI trusted publishing) once the DEX answer is in.

- [ ] **Normalise legacy/wrong-keyboard Romanian letters in `normalize()` — fold into the
  next re-ingest, do not re-ingest for this alone.** Decided 2026-10-05 (owner), from
  ADR-002 / brief B2. Map `ã ǎ → ă`, `ȋ → î`, `ȃ → â`, and decide separately on `þ → ț`
  (72% of its forms become a real word) and on Portuguese `ã` in Wikipedia names (`são`;
  only 17% map to a Romanian word, so a blanket `ã → ă` is not free — consider applying it
  per source, or only when the result is in a known-word set). Measured payoff on `web`:
  3,214 Romanian words gain ≥0.005 Zipf, up to +0.58 (`învătământ`); `ã` fragments are
  0.047% of web tokens. Needs the measure-first treatment once more before landing: re-read
  raw text with the new `normalize()` beside the old one, and check that `tokenize(t) == [t]`
  still holds for every `merged` key. Trigger: any change that forces a full re-ingest.

- [ ] add Mermaid charts to Methodology or README?

- [ ] **API: hyphen-splitting fallback in `zipf_frequency` / `word_frequency` — to be
  discussed again later, eventually (owner, 2026-10-05).** If a tokenized token contains
  `-` and is not in the table, split it on the hyphens and combine the parts harmonically,
  as `wordfreq` does (`state-of-the-art` → `state`+`of`+`the`+`art`). Restores a value for
  the ~40 corroborated 3+-hyphen compounds dropped from the package on 2026-10-05
  (`state-of-the-art` 1.66 → now 0.0, `ha-ha-ha-ha`, `saint-germain-en-laye`) and for any
  compound the table never saw. Costs: about ten lines plus tests; it amends ADR-001's
  "one token → its row, exact" rule, so it needs a short ADR-003 and a look at
  `validate.py` check 2 (wordfreq comparison) to make sure it only moves words we lack.
  Extensions (`frequency_detail`, `by_source`) would stay exact-lookup. Not started.

- [ ] **Label the one-hyphen rows (human review in progress).** 819,273 one-hyphen rows,
  13.5% of `merged`, 6,130 in DEX. `python build/export_hyphen_sample.py` writes
  `data/review/hyphen_sample.csv` (gitignored): 360 shuffled rows from five strata (`dex`,
  `corroborated` n_reliable≥3, `two_sources`, `one_src_common` zipf≥1, `one_src_rare`
  zipf<1 — 701,782 rows, sampled double). Owner fills the `label` column with K (keep) /
  J (junk) / ? and an optional `note`; then `python build/summarize_hyphen_labels.py`
  prints junk share per stratum. A stratum is a candidate for dropping at packaging only if
  nearly all of it is J (and no K that matters); otherwise the rule must be finer and needs
  another measurement. Outcome decides whether `MAX_HYPHENS` goes down or a per-stratum
  rule is added to `build_package.py`.

  **Findings from the first labelling pass (owner's notes on ~50 rows, 2026-10-05):**
  - *Hyphen + article ending* (`site-ul`, `wp-ul`, `pnț-ului`, `idn-urilor`, `sud-coreencelor`)
    is correct Romanian orthography for loans, abbreviations and letters. They are real
    inflected forms of a root word and should be **K**, not junk, and exempt from any
    junk rule. "Belonging to the root" is the lemma layer's job, not the surface table's
    (`casei` is also its own row). The lemma layer only knows DEX roots; a hyphen-ending
    rule (`-ul/-ului/-lui/-urile/-urilor/-lor/-uri…`) could extend it to `burrito-ul`,
    `reload-ul`. Not started; mind roots that are also English words.
  - *Line-break artifacts* (`lo-gica`, `do-rește`, `poli-morfe`, `pan-demie`) are junk, and
    there is a cheap detector: the joined twin (`logica`) exists in `merged` far above the
    hyphenated row. Measured: of 819,273 one-hyphen rows, 237,194 have a joined twin,
    71,501 with the twin ≥1.0 Zipf higher and **35,462 with it ≥2.0 higher**. The ≥2.0 group
    is mostly artifacts (`cele-brează`, `aca-demie`) but also native-word-plus-misplaced-
    hyphen (`ghid-ului`, `videoclip-ului`), and a few real compounds can slip in. Candidate
    treatment: fold these rows' counts into the twin (like `migrate_dashes.py`) rather than
    drop them. Needs the labelled sample to check precision; not done.
  - Other classes seen: name pairs / route pairs (`iohannis-dăncilă`, `aiud-turda`: a hyphen
    standing for a dash), full names, sentence fragments (`banca-de`, `copacii-s`), slugs
    without diacritics, English phrases, prefix compounds (`anti-poluare`, `ex-șefa`: keep),
    elisions (`se-ntâlnește`: keep), verb + clitic (`trimițându-li`: keep).
  - Proposals for 22 varied rows, for the owner to confirm or overrule:
    `data/review/hyphen_proposals.csv` (gitignored).

