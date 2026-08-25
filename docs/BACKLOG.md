# Backlog

Open bugs, debt, and enhancements. Add new entries with `- [ ]` and enough context to act on later.

---


- [ ] maybe implement with another model and check results?

- [ ] when done, integrate with the synonims project

- [ ] `build/ingest_news.py` (M3, `news` source) is scaffolded but its `--test`
  smoke run was never confirmed to finish — session ended with it still stuck
  fetching the `language`+`plain_text` columns of the very first shard
  (`2016_0000.parquet`, 1M rows) after 20+ minutes on one HTTPS connection, no
  progress signal available mid-fetch. Source is `stanford-oval/ccnews` (spec's
  `news`/CC-News RO row has no ready-made RO-only mirror, so this reads the
  full multilingual crawl and filters `language == 'ro'` client-side — see the
  ingester's docstring for why `rows_scanned` and `docs_matched` are tracked
  separately). Before trusting this ingester: (1) let `--test` actually finish
  and confirm it finds real `ro` rows with sane tokenized text, not zero
  matches from a language-code mismatch; (2) if a single 1M-row shard takes
  20+ min just to download two columns, 479 shards could make `news` far
  slower than `web` (CulturaX) was for a fraction of the tokens — time a
  handful of real shards before committing to an unattended multi-day run.