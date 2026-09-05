# Backlog

Open bugs, debt, and enhancements. Add new entries with `- [ ]` and enough context to act on later.

---


- [ ] maybe implement with another model and check results?

- [ ] when done, integrate with the synonims project

- [ ] `build/ingest_news.py` (M3, `news` source, `stanford-oval/ccnews` filtered
  to `language == 'ro'`) — the language filter and tokenization are now
  confirmed sane (`--test` found 2,037 ro docs / 666,285 tokens in the first
  200k rows of shard `2016_0000.parquet`, 1.0–1.1% match rate, top words
  `de`/`a`/`în`/`și`/`la`... matching `wiki`/`web`'s shape). Still open:
  **shard fetch time is wildly inconsistent** — the same shard's `language`+
  `plain_text` column fetch took 20+ minutes with no progress signal in one
  run, then ~2 minutes for the full 1M rows in a later run, same code and
  network. Time a handful of *different* real shards (not just repeats of
  shard 0 — could be CDN-cache-dependent) before committing to an unattended
  multi-day run across all 479 shards; if the slow case is typical rather
  than a fluke, `news` could dwarf `web`'s (CulturaX) runtime for a fraction
  of the tokens, and the restart-loop's blind 15s-retry-on-any-nonzero-exit
  won't help if the process is just wedged on a single stalled read rather
  than crashing.