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

- [ ] M4 next: `build/merge.py` — trimmed mean across the 5-source panel into
  `merged` (spec §8). This is the first stage that can actually validate
  checks 2-5 (rank correlation vs `wordfreq`, hand-written monotone pairs,
  DEX lemma coverage, the spread report) since they all need `merged` to
  exist.