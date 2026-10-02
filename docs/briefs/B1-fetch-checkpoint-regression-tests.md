# Brief B1 — regression tests for `fetch_social.py` checkpoint recovery

**Owner:** Sonnet. **Status:** ready. **Written:** 2026-10-02 by Opus.

## The question

A near-miss happened today and nothing in the test suite would have caught it. Write the
tests that would have.

## Background — what you need, so you do not need the specs

`build/fetch_social.py` fetches Reddit history over HTTP and appends one JSON record per
line to `data/raw/social/<sub>_<kind>.ndjson`. It is acquisition only: it computes no
frequencies, so the only thing that can go wrong is the *set of records on disk*.

It checkpoints to `data/checkpoints/social_fetch.json`, one entry per `<sub>/<kind>`:

```json
"Romania/posts": { "before": 1641741116, "count": 260088, "done": false, "bytes": 82098907 }
```

- `before` — a unix timestamp; the next page fetches records *strictly older* than this.
- `count` — records written so far.
- `bytes` — size of the ndjson at the moment the checkpoint was written.
- `done` — history exhausted; the entry is skipped on resume and the ndjson has been
  compacted to `.zst`.

The checkpoint is saved every ~5,000 records, but the file is appended continuously. So a
process killed between two saves leaves records on disk that the checkpoint does not know
about, and because `before` never advanced past them, a naive resume refetches and
re-appends them. **Duplicates silently inflate occurrence counts, which is the one failure
mode a frequency table cannot tolerate.** `bytes` exists to close that window: on resume,
the file is truncated back to the checkpointed size so file and checkpoint agree exactly.

## The bug these tests must cover

`bytes` was added after some checkpoints had already been written. Today's on-disk
checkpoint had **no `bytes` key**, and the code read it as `state.setdefault("bytes", 0)`.
Zero means "nothing accounted for", so the resume check saw the entire 82 MB file as
unaccounted and would have called `truncate(0)` — destroying all 259,688 records already
fetched — while leaving `before` at its stale value. The 2022-02 → 2026-09 span would have
been silently lost and never refetched. A missing byte count is *unknown*, not zero.

The fix (already landed, read it before writing tests) adds `rebuild_state_from_file()`:
when `bytes` is absent, it scans the ndjson for complete lines only, and rebuilds
`count`, `before` (the oldest `created_utc` actually on disk) and `bytes` from the file,
trimming any torn final line. The file is the one thing that cannot disagree with itself.

## What to build

Tests in `tests/`, following whatever conventions the existing files there already use
(match their style; do not introduce a new framework or fixture pattern). Cover:

1. **Missing `bytes` with `count > 0` does not truncate.** A checkpoint entry with no
   `bytes` key plus a populated ndjson must end with every record still present.
2. **Recovery values are exact.** `rebuild_state_from_file()` returns the true line count,
   the true minimum `created_utc`, and a byte length equal to the offset of the end of the
   last complete line.
3. **`before` is corrected backwards, never forwards.** Build the real-world shape: a
   stale `before` that is *newer* than the oldest record on disk. After recovery, `before`
   must equal the file's oldest timestamp, so the next fetch cannot refetch records the
   file already holds.
4. **A torn final line is trimmed, not parsed.** Append a truncated fragment with no
   newline; recovery must exclude it from `count` and leave the file ending at the last
   complete line.
5. **The normal (post-fix) path still truncates.** `bytes` present and smaller than the
   file: the excess must be removed, because those are the unaccounted records that would
   otherwise duplicate.
6. **`done: true` is skipped** and is never truncated or recovered.

Use small synthetic ndjson files in a tmp dir. **Do not** hit the network — no test may
call `get_page()` or touch `arctic-shift.photon-reddit.com`.

## Constraints

- **`build/fetch_social.py` is running right now** (a multi-hour crawl, under
  `build/run_social_fetch.sh`, which re-executes the script on every loop iteration). **Do
  not edit `build/fetch_social.py`.** An edit would be picked up by the next restart and
  could corrupt a live 12M-record acquisition. Tests only, under `tests/`.
- Do not touch `data/` at all — not the real checkpoint, not the real ndjson, not the
  `.db`. Everything synthetic, in a tmp dir.

## Deliver

- The new test file(s) under `tests/`.
- `python -m pytest tests/ -q` passing, with the new count reported.
- An entry appended to `docs/activity-history.md` under `## 2026-10-02 — <short title>`,
  matching the prose style of the existing entries there (read two before writing), saying
  what the near-miss was and what is now covered.
- A one-paragraph report back: what you tested, anything you found that the fix does *not*
  cover.

## Out of scope — do not do these

- Changing `fetch_social.py` in any way.
- The `compress()` overwrite issue (compacting an unfinished subreddit loses data on the
  next pass) — known, separately logged, not yours.
- `COMPRESS_EVERY` dead constant — known, separately logged.
- Running or restarting the crawl.

## Stop and ask

If a test can only be made to pass by changing `fetch_social.py`, stop and report it —
that is a finding about the fix, and the fix is not yours to change while it is running.
