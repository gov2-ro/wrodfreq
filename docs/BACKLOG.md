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

- [ ] `validate.py` doesn't check `merged` or `lemma_zipf` at all yet
  (checks 2-5 — rank correlation vs `wordfreq`, ~50 hand-written monotone
  pairs, DEX lemma coverage, the spread report). Both tables now exist,
  `wordfreq` is reachable in oțios's venv (M1 precedent) — nothing blocking
  this. Up next.