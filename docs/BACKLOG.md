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

- [ ] M3 still needs `subs` (OpenSubtitles RO) and `eu` (Europarl/DGT) before
  the panel reaches ≥5 sources and `merge.py`'s trimmed-mean branch (spec
  §13 M3 "done when") is reachable. Both are direct OPUS downloads, not HF
  parquet — will need their own fetch/decompress logic, not a copy of
  ingest_web.py/ingest_news.py's HfFileSystem pattern.