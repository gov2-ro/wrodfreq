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

- [x] `build/ingest_subs.py` scaffolded and smoke-tested 2026-09-08 —
  `--test` downloaded the real OPUS OpenSubtitles RO v2024 export (3.56 GB)
  and processed the first 200k lines cleanly: 53,070 unique words, top-20
  already shows a real conversational signature vs `wiki`/`web`/`news`
  (`să` #2, `nu` #4, colloquial `e`/`am` present) — the tokenizer/pipeline
  generalizes correctly to a non-parquet, single-big-file source. `documents`
  here counts *subtitle lines*, not the 427,889 distinct films OPUS's own
  metadata reports — no per-file boundary data ships with this export, see
  the ingester's docstring. Estimated full run ≈35 min (6 min already-cached
  download + ~32 min processing at the observed 200k lines/s) — dramatically
  shorter than `web`/`news`, safe to just run in one sitting rather than
  needing the user's-own-terminal handoff those needed.

- [ ] M3 still needs `eu` (Europarl/DGT) before the panel reaches ≥5 sources
  and `merge.py`'s trimmed-mean branch (spec §13 M3 "done when") is
  reachable. Also a direct OPUS download — can likely reuse most of
  `ingest_subs.py`'s download-resume/single-file-processing shape.