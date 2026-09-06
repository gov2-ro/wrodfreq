# Backlog

Open bugs, debt, and enhancements. Add new entries with `- [ ]` and enough context to act on later.

---


- [ ] maybe implement with another model and check results?

- [ ] when done, integrate with the synonims project

- [ ] `build/ingest_news.py` real run: the restart-loop's blind 15s-retry-on-
  any-nonzero-exit won't help if the process is wedged rather than crashing
  outright, and this has now happened twice with two different causes (see
  activity-history 2026-09-06 both entries) — a stalled fetch, and a
  huggingface_hub retry-storm that spun up a background thread the first
  Ctrl+C fix (`raise SystemExit`) couldn't kill because CPython won't exit
  while a non-daemon thread is alive. Second Ctrl+C now calls `os._exit()`
  instead (fixed), which should be unconditional, but this still needs a
  human watching — it isn't self-healing. Worth revisiting if a real
  overnight run wedges a third way.