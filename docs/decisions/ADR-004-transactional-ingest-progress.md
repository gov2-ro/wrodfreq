# ADR-004 — Ingest progress commits with the counts; legacy counts are sealed, not replayed

**Status:** accepted, 2026-11-09 (brief B7, Phases 1–2). **Amends:** spec §4 (the
checkpointing row), §7.1 (schema), §7.2 (stage 1), §14 (long jobs).

## Context

The 2026-11-09 audit found that the six ingesters kept their cursor in a JSON file
next to the database and committed the counts separately:

- **A01.** A `--test` run of `ingest_social.py` wrote to the real database through a
  separate JSON file. The following full run added its counts to the test counts.
  Social sums to 566,678,860 tokens against a stored total of 536,523,553. The
  difference, 30,155,307, equals the test checkpoint's `tokens_done`.
- **A03.** A kill between the database commit and the JSON save replays a committed
  batch. Then the counts grow and the total does not.
- **A04.** The token migrations changed the database totals after the JSON files were
  last written. Resuming a completed `eu` reset 84,722,606 to 84,415,957.
- **A05.** The social `--limit N` cursor recorded N+1, so a capped and resumed run
  silently skipped one record per file.

## Decision

1. **One transaction per batch.** Each batch checks the writer's generation and
   sequence, applies the count and document deltas, moves `sources.total_tokens` and
   `total_docs` by the same deltas, stores the cursor in `ingest_state`, and commits.
   The ingester clears its buffers only after the commit. A batch that does not commit
   leaves no trace and is read again. All six ingesters use `build/ingest_state.py`.
   They keep their own reading and tokenizing loops.
2. **`ingest_state` belongs to its database.** The table holds the generation id, a
   sequence number, the state (`in_progress`, `completed` or `sealed_legacy`), the
   cursor and its format version, a tokenizer and source-policy hash, the input
   manifest and its hash, the known upstream snapshot, and the `db_meta.db_id` of the
   owning database. A state from another database is refused. A tool that imports a
   source must rebind the id on purpose (`build/replace_source.py`).
3. **A fresh run never adds to an existing generation.** It refuses a source that
   already has counts or state. `--resume` continues the stored generation only if
   the policy hash and the input manifest still match. If they differ, the run stops
   and says why; it never resets.
4. **`--test` writes to an experiment database.** Without `--db` the run creates a new
   file under `data/experiments/`. The canonical `data/wrodfreq.db` is always refused.
   A separate JSON file name is not isolation.
5. **JSON checkpoints are not read.** The old files stay as evidence.
   `build/adopt_legacy.py` seals a completed source whose counts are conserved
   (`SUM(occurrences) == total_tokens`). It keeps the **database** totals and records
   the logical hash of the counts and the hashes of the old JSON files. It records the
   unknown raw inputs as `unknown`. A sealed source resumes as a no-op. The step
   refuses an unfinished source or a source whose counts are not conserved.
6. **Count changes outside an ingest** (the migrations, a source replacement) move the
   sequence on and invalidate every derived stage in the same transaction. A stale
   writer is then refused.
7. **Limits.** The social `--limit N` is an absolute position in each file: the run
   consumes N records and records N. `--limit 1`, then `--resume --limit 2`, then
   `--resume` gives the same counts as one unbounded run. The subs and eu limits stay
   per-session, and their help text says so.
8. **Fault points for tests.** `WRODFREQ_FAULT=<point>:<n>` makes the process exit at
   `before_commit`, `during_apply` or `after_commit`. The kill tests use this point
   instead of a timed `kill -9`.

## Consequences

- Counts, totals and cursor cannot disagree. The reproduced A01/A03/A04/A05 cases are
  regression tests across all six real ingesters (`tests/test_ingest_state.py`).
- The five unaffected sources are sealed without a multi-day re-ingest. Their raw-input
  provenance stays `unknown`. The strict report says that this provenance verifies the
  retained counts and their derivations, not a fresh reconstruction from raw corpora.
- A legacy source that was not completed cannot be resumed. It must be rebuilt into an
  empty staged database.
- The social fetcher (`fetch_social.py`) keeps its own JSON state, because it writes
  raw files, not counts. It gets a separate finalization protocol (brief B7 Phase 2;
  it closes brief B4).
