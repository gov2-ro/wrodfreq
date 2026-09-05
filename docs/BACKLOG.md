# Backlog

Open bugs, debt, and enhancements. Add new entries with `- [ ]` and enough context to act on later.

---


- [ ] maybe implement with another model and check results?

- [ ] when done, integrate with the synonims project

- [ ] `build/ingest_news.py` real run: the restart-loop's blind 15s-retry-on-
  any-nonzero-exit won't help if the process is wedged on a stalled network
  read rather than crashing outright — the one time that happened (20+ min
  stuck, see activity-history 2026-09-06) Ctrl+C/Ctrl+D did nothing until the
  process died on its own; a second Ctrl+C now force-exits (fixed), but this
  still needs a human watching, it isn't self-healing. Worth revisiting if a
  real overnight run wedges again.