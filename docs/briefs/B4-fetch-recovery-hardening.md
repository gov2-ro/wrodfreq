# Brief B4 — harden `fetch_social.py` recovery against incoherent checkpoints

**Owner:** Sonnet. **Status:** BLOCKED until the social crawl finishes. **Written:** 2026-10-02 by Opus.

## Do not start this while the crawl is running

`build/run_social_fetch.sh` re-executes `build/fetch_social.py` on every loop iteration,
so an edit to that file lands on the next restart, against a live multi-million-record
acquisition. **That is precisely how today's bug was created**: the file was edited at
13:19 on 09-25 while a process begun earlier kept running the pre-edit code, so the
checkpoint it wrote at 13:24 lacked a field the new code required. Check first:

```bash
pgrep -f 'build/fetch_social.py' || echo "clear to proceed"
```

If that prints a pid, stop and come back later.

## The question

`rebuild_state_from_file()` (landed in 4dd1fdf) recovers `count`, `before` and `bytes`
from the append-log when `bytes` is **absent**. Tests for it are in
`tests/test_fetch_social.py` (landed in 22e9205), and writing them surfaced five further
inputs where recovery is either absent or actively destructive. Close them.

## Background — so you do not need the specs

`fetch_social.py` appends one JSON record per line to
`data/raw/social/<sub>_<kind>.ndjson` and checkpoints to
`data/checkpoints/social_fetch.json`, one entry per `<sub>/<kind>`:

```json
"Romania/posts": { "before": 1641741116, "count": 260088, "done": false, "bytes": 82098907 }
```

`before` is a unix timestamp; the next page fetches records strictly *older* than it.
`bytes` is the ndjson's size when the checkpoint was written. The checkpoint is saved
every ~5,000 records while the file is appended continuously, so `bytes` exists to let a
resume truncate back to an exactly-known boundary. Without that, a kill leaves records
the checkpoint never saw, `before` never advanced past them, and the resume refetches and
re-appends them — **duplicates silently inflate occurrence counts, the one failure mode a
frequency table cannot tolerate.**

The governing principle, and the thing to apply throughout this brief: **an absent or
self-contradictory checkpoint value is *unknown*, not zero.** The file is the only thing
that cannot disagree with itself, so an incoherent checkpoint should be rebuilt from the
file — or refused loudly — never acted on as though it were true.

## The gaps to close

Reachability is noted per gap. **None is reachable by the current production path**, which
is why the fix was left for after the crawl rather than rushed in. Do not downgrade them
on that basis: each one's *consequence* is silent, total data loss, and the bug that
nearly fired today was also "unreachable" until a file edit made it reachable.

1. **Corrupt line mid-file destroys everything after it.** The recovery scan stops at the
   first unparseable line and reports the preceding offset; the caller then truncates
   there. One bad line early in a 260,000-record file discards the rest, silently.
   A torn write is only physically plausible as the *last* line, so distinguish the two:
   a bad final line is a torn write and should be trimmed, while a bad line with valid
   records after it means a damaged file and should **abort with a loud error**, not
   truncate. Reachability: filesystem damage only.
2. **An explicit `bytes: 0` alongside `count > 0` still truncates the file to nothing.**
   Only a *missing* key triggers recovery. But `bytes: 0` with a non-zero count is not a
   claim that the file is empty, it is an incoherent checkpoint, and it must take the same
   recovery path. Same for **`bytes` greater than the file size**, which says the file
   shrank — currently undetected and unflagged. Reachability: via `--max-records`, which
   calls `compress()` on an unfinished subreddit and unlinks its ndjson without re-saving
   the checkpoint (see gap 6).
3. **A `bytes` value landing mid-line truncates mid-line**, and the next run appends to
   the torn remainder, producing one malformed record. Snap back to the preceding line
   boundary instead of trusting the offset. Reachability: not currently, because `bytes`
   is always read from `stat()` immediately after `flush()`+`fsync()`; it becomes
   reachable the moment anything writes `bytes` from another path.
4. **`before` can silently keep a stale value.** Records lacking a numeric `created_utc`
   do not contribute to the recovered oldest timestamp, so a file of such records leaves
   `before` untouched — which is the stale-value case that caused today's 4,688-record
   duplicate window. If no timestamp can be recovered from a non-empty file, say so
   explicitly rather than proceeding.
5. **A recovered `count` that disagrees with the checkpoint's `count` is not reported.**
   The difference is the size of the unaccounted window and is genuinely diagnostic —
   today it was +4,688, just under the ~5,000 checkpoint interval, which is what
   confirmed the diagnosis. Log it.
6. **Also fix, because gap 2 depends on it: `compress()` overwrites rather than merges.**
   It writes the `.zst` from the current ndjson and unlinks the ndjson, without saving the
   checkpoint afterwards. So compacting a subreddit that is not `done` — reachable today
   via `--max-records` — loses every earlier record on the next pass, which starts a fresh
   empty ndjson and later overwrites the `.zst` with only the new tail. Either refuse to
   compact unless `done`, or make compaction append-correct. Decide which and say why.
7. **While you are here: `COMPRESS_EVERY = 250_000` is dead code** — defined, never read;
   `compress()` is called once per subreddit at the end. Delete the constant, or wire it
   up to compact incrementally. Note that incremental compaction is what would cap the
   transient disk peak (the uncompressed append-log and the finished `.zst` coexist —
   3.87 GiB at peak for r/Romania's 844 MiB result), so this interacts with gap 6. If you
   wire it up, gap 6 must be solved first and properly.

## Deliver

- The changes in `build/fetch_social.py`, each gap covered by a test in
  `tests/test_fetch_social.py` alongside the existing ones (match their conventions).
- `.venv/bin/python -m pytest tests/ -q` passing, with the new count.
- **A real resume rehearsal**, not just unit tests: build a synthetic ndjson plus
  checkpoint in a tmp dir for each incoherent shape above and run `fetch()` against a
  stubbed `get_page` (the existing tests show the pattern), asserting no record is ever
  lost and none is ever duplicated.
- Entries in `docs/activity-history.md` (`## <date> — <short title>`) and the matching
  `- [ ]` items closed in `docs/BACKLOG.md`, both in the prose style already there.
- A report: which gaps you closed, how, and whether any turned out not to be real.

## Out of scope

- Re-running or restarting the crawl, and any change to `build/run_social_fetch.sh`.
- `ingest_social.py` and anything downstream of acquisition.
- Changing the checkpoint's on-disk format in a way that cannot read today's file. Any
  checkpoint written by any earlier version must still resume correctly — that backward
  compatibility *is* the feature being built here.

## Stop and ask

- If closing gap 1 or 6 requires deciding what "correct" means for a partially-acquired
  subreddit (refuse to compact, or merge). State the trade-off; that call is Opus's.
- If any fix would require re-fetching data already on disk.
