# Activity History

Chronological log of meaningful work. Add entries under `## YYYY-MM-DD — Short Title`.

---

## 2026-08-18 — Repo scaffolding: .gitignore and README

Added `.gitignore` (Python caches/venvs/packaging artifacts, plus the repo's own
no-`.db`/no-`.csv`-in-git rule: `/data/`, `*.db`, `*.csv`, `*.parquet`,
`wrodfreq/data/*.msgpack.xz`). Rooted the `build/lib/` and `build/bdist.*/` ignores so
they don't shadow the tracked `build/*.py` pipeline-stage scripts once those exist.

Rewrote `README.md`: RO description, an ASD-STE100-style Simplified Technical English
equivalent, an ASCII schematic of the ingest → merge → package pipeline, and a
checkbox roadmap condensed from spec §13 (M1–M7). No code written yet — still spec-only.

## 2026-08-18 — M1: skeleton, tokenizer, Wikipedia ingest, validation

First code in the repo. `pyproject.toml` (hatchling, `build`/`dev` extras), venv via
`uv` on Python 3.12.

- `wrodfreq/tokenizer.py` — `normalize()` + `tokenize()`, ported from oțios's
  `process_culturax.py:81-84`/`dump_parser.py:31-38` with the `len(t) > 2` filter
  dropped (spec §3.2). Numerals are excluded from token streams by construction — the
  character class has no digits — not by a separate filter.
- `wrodfreq/zipf.py` — `zipf_from_counts()`, `is_reliable()`, `source_zipf_floor()`;
  `MIN_OCC_PER_SOURCE = 5` (spec §8.1), floor always derived from a source's own
  `total_tokens`, never hardcoded.
- `wrodfreq/db.py` — the `data/wrodfreq.db` schema (spec §7.1). Added `sources.zipf_floor
  REAL`, present in §8.1's prose ("store the resulting per-source Zipf floor in
  `sources`") but missing from the §7.1 DDL sample — reconciled in favor of the
  narrative requirement.
- `build/ingest_wiki.py` — Stage 1 for `wiki`. Loads `wikimedia/wikipedia`
  (`20231101.ro`) non-streaming (small enough — ~1.5GB — to skip oțios's
  `HfFileSystem`+parquet-row-group workaround for the `datasets` streaming `.skip(N)`
  bug; that workaround stays reserved for CulturaX at M2). Checkpointed every 2000
  docs to `data/checkpoints/wiki_checkpoint.json` (atomic `.tmp`+`replace()`),
  SIGTERM/SIGHUP-safe, resumable, `sources.status` only flips to `completed` once the
  full dataset is exhausted (not on `--test`/`--limit`/interrupt).
- `build/compute_zipf.py` — Stage 2, pure function of `source_counts` +
  `sources.total_tokens`, safe to re-run.
- `build/validate.py` — checks 1 (function words) and 6 (idempotence) only; checks
  2-5 need `merged`/DEX/`wordfreq` data that don't exist before M3/M4, so they're
  left out rather than stubbed.
- `tests/test_tokenizer.py`, `tests/test_zipf.py` — 13 tests, all passing.

**Ran the real ingest**, not just `--test`: 442,389 articles, 109,558,215 tokens, 134s.
`compute_zipf.py` → 1,627,422 words, 409,250 reliable. `validate.py` → 2/2.
`de`/`și`/`la`/`un`/`cu` landed at 7.67/7.41/7.17/6.88/7.00.

**Spec finding:** checked those five words against the actual installed `wordfreq`
package (`~/devbox/otios/.venv`) — its real Romanian values are `de`=7.72, `și`=7.46,
`la`=7.21, `un`=6.99, `cu`=7.05. Our single-source Wikipedia numbers track within
~0.05–0.11 Zipf of the reference, and `wordfreq` itself exceeds the spec's 7.5 ceiling
on `de`. That ceiling is calibrated for the multi-source `merged` trimmed mean (spec
§11.1); a single register-bound source overshooting it at the very top word is
expected, not a denominator bug — the real bug signature is a function word landing
*low* or missing outright. `validate.py`'s pre-`merged` per-source fallback now uses
`ZIPF_HIGH_SINGLE_SOURCE = 8.0` instead of 7.5, documented inline; the spec-literal
6.0–7.5 band still gates once `merged` exists.

## 2026-08-18 — M2 started: CulturaX ingester written and smoke-tested

`build/ingest_web.py` — the CulturaX RO backbone ingester, lifted wholesale from
oțios's `process_culturax.py` checkpointing/row-group-resume machinery (spec §4), with
the two spec-mandated departures: no DEX word filter (§3.1, open vocabulary) and no
`len(t) > 2` filter (§3.2, uses `wrodfreq.tokenizer` directly). Confirmed HF auth
already works in the new venv (global `~/.cache/huggingface/token`) and that
`uonlp/CulturaX/ro` lists its 64 parquet shards without further gating.

Smoke-tested with `--test` (first 1000 docs of shard 0): 567,318 tokens, top words
de/în/și/a/la/cu — sane. Checkpoint is real, additive progress (not test-only
throwaway state), so `--resume` continues correctly from row 1000 of shard 0.

**Not run to completion** — CulturaX is ~40B raw tokens across 64 shards, the
spec's own "multi-day job" (§13 M2). Per session instruction, handed the launch
command (with oțios's restart-loop pattern) to the user to run directly rather than
starting it here.

## 2026-08-23 — M2 finished: CulturaX RO ingest complete

User ran `build/ingest_web.py` to completion outside this session: all 64 shards,
23,516,444,566 tokens, 40,325,424 docs, `sources.status = 'completed'`. Verified via
`compute_zipf.py` (`web` floor -0.67, as expected for a ~23.5B-token source) and
`validate.py` — function words (`de`/`și`/`la`/`un`/`cu`) land in-band for both `wiki`
and `web`, and idempotence (check 6) passes across a re-run of stages 2. M2 (spec §13)
is done: `sources.total_tokens` for `web` is well inside the 15–20B+ target range and
validation check 4 (rank correlation prerequisite) is clear to run once `merged` exists.

## 2026-08-25 — M3 started: CC-News RO ingester scaffolded, not yet verified

`build/ingest_news.py` — the `news` source ingester (spec §13 M3, alongside `subs` and
`eu`, needed to reach ≥5 sources so `merge.py`'s trimmed-mean branch is reachable).

Spec names "CC-News RO" but no ready-made Romanian-only mirror exists on HF. Found
`stanford-oval/ccnews` — a cleaned/deduplicated parquet mirror of the full CommonCrawl
News crawl (2016-06–2024-06, ~600M articles, 100+ languages, sharded by crawl year, no
per-language split) with a `language` column. Confirmed via the HF API (`language: ro`
in the dataset card, `stanford-oval/ccnews` has no per-language config — same
"filter client-side" pattern as the dataset's own README example).

Ingester reuses `ingest_web.py`'s per-file/row-group checkpoint machinery, with one
structural change: since most rows scanned are *not* Romanian, `rows_scanned` (resume
position, every language) and `docs_matched`/`tokens_done` (Romanian only, what actually
lands in `source_counts`) are now tracked as separate checkpoint fields — writing
`rows_scanned` into `sources.total_docs` would silently claim CC-News's full multilingual
row count as this source's document count, the same honest-denominator failure mode
§3.2 exists to prevent, just on the docs axis instead of tokens.

**Not verified end-to-end.** `--test` (scan first 200k rows of shard `2016_0000.parquet`)
was still running after 20+ minutes on a single HTTPS connection when the session ended
— no incremental progress signal available mid-fetch, because `pyarrow`'s
`read_row_group` fetches an entire row group's referenced columns (`language`,
`plain_text`) in one blocking call, and this dataset's shards are one 1M-row group each
(unlike CulturaX's smaller groups, which is what gave `ingest_web.py`'s progress
logging its granularity). Open items logged in `docs/BACKLOG.md`: confirm the language
filter actually matches `ro` rows with sane text, and time real shard throughput before
committing to an unattended run — if one shard takes 20+ minutes for two columns, 479
shards could make `news` the slowest ingester yet for a fraction of `web`'s token count.

## 2026-09-06 — `--test` confirmed sane; found and fixed a checkpoint/`--db` footgun

Re-ran `build/ingest_news.py --test`. This time the same shard's column fetch took ~2
minutes, not 20+ — no code changed between runs, so the earlier stall looks like a
transient network/CDN issue rather than a property of the dataset; throughput on this
source is evidently not reliable session to session, left open in `docs/BACKLOG.md`.
The 200k-row scan found 2,037 `ro` docs (1.0–1.1% match rate) and 666,285 tokens; top
words by occurrence (`de`, `a`, `în`, `și`, `la`, `din`, `cu`, `care`...) match the
`wiki`/`web` shape, and `sources.total_docs` landed as the matched count (2,037), not
the scanned count (200,000) — confirms the `rows_scanned`/`docs_matched` split actually
works, not just compiles.

**Bug found via this test, not by inspection:** `CHECKPOINT` is a module-level constant
independent of `--db`. Two `--test` runs against a scratch `--db` (this one and the
prior session's) had both written real-looking progress —"200,000 rows scanned, 2,037
docs done" for `2016_0000.parquet`— into the *production* `data/checkpoints/
news_checkpoint.json`, while the actual counted words only ever reached the throwaway
scratch DB. Had the real run then been started with `--resume` (as the docstring's own
restart loop does from the very first invocation), it would have silently skipped that
shard's first 200k rows in the real database, having never actually counted them there.
Confirmed no real `news` row exists in `data/wrodfreq.db` (this was caught before any
real ingestion started), deleted the stale checkpoint file, and fixed the ingester so
`--test` now writes to a sibling `news_checkpoint.test.json` instead of the shared path
— matching what "no resume" in `--test`'s own help text already implied. `ingest_web.py`
and `ingest_wiki.py` have the same hardcoded-`CHECKPOINT` shape but were never smoke-
tested against a scratch `--db` after going to production, so they never hit this; not
touched, since both are already-completed one-time historical runs.

## 2026-09-06 — M3 `news` real ingest launched; Ctrl+C fix; throughput resolved

User started the real `build/ingest_news.py --resume` run by hand and couldn't stop it
with Ctrl+C or Ctrl+D. Root cause: the SIGINT handler only set a flag and returned —
PEP 475 auto-retries an interrupted blocking syscall unless the handler raises, so the
flag was never actually able to break the process out of a stalled network read (the
same kind of 20+-minute single-shard fetch logged above), and the flag-check inside the
per-row loop never got a chance to run because the process hadn't reached the loop yet.
Ctrl+D was a no-op for an unrelated reason — the script never reads stdin. By the time
this was diagnosed the process had already died on its own; no data was lost (checkpoint
showed `current_file_rows_scanned: 0`, i.e. it never got past the initial fetch).

Fixed: a second signal now raises `SystemExit` instead of just re-flagging, forcing an
immediate exit even mid-fetch; the first signal's graceful-flush-at-next-checkpoint
behavior is unchanged.

Confirmed no ingester was running, then launched `python -u build/ingest_news.py
--resume` for real against `data/wrodfreq.db` and timed it live via the checkpoint file.
**Throughput question from the entry above is resolved**: the earlier 20+ minute stall
was a one-off (network/CDN, not the dataset) — two consecutive shards, one of them never
touched by any prior test run (ruling out CDN-cache bias), each completed in exactly
180s (3 min). At that rate the full 479-shard run is ~24h, not "multi-day" — reasonable
for M3, left running.

Handed the job to the user's own terminal (same reasoning as M2 CulturaX — a background
job in this session dies if the session closes) with the restart-loop from the
docstring. Stopped the session's copy first: `kill -TERM` on the wrapper shell PID exited
the *shell*, but the actual Python process detached and kept running as an orphan,
still writing to the real DB — had to find and `kill -TERM` the Python PID directly.
Worth remembering: a background task's reported PID/exit here is the wrapper, not
necessarily the real worker.

## 2026-09-06 — Ctrl+C fix wasn't enough either; `os._exit()` instead

User's own run hit a genuine multi-retry network failure on shard `2017_0029.parquet`
(`ReadTimeout` / connection-reset, `huggingface_hub`'s own backoff visibly retrying
5 times with growing sleeps) and repeated Ctrl+C did nothing — including after the
"second signal force-exits" fix from earlier today. Diagnosis: that fix raises
`SystemExit` on the second signal, but `huggingface_hub`'s retry/backoff machinery
spins up extra threads (`ps -M` showed 3 threads on the stuck process), and CPython
will not tear down the interpreter while any non-daemon thread is still alive — so the
raised exception on the main thread had nowhere to go. Killed it directly from this
session (`kill -9` on the PID, confirmed dead) since the user had no working way to
stop it themselves; checkpoint was consistent (32/479 shards, 782,651 docs, 238.6M
tokens — matches `sources.total_docs`/`total_tokens` exactly), nothing lost.

Fixed by switching the second-signal path from `raise SystemExit(1)` to `os._exit(1)`
— terminates the process immediately at the OS level, equivalent to self-inflicted
SIGKILL, regardless of background threads or exception handling anywhere in the stack.
This is now the second distinct way this ingester has gotten stuck past a first Ctrl+C
(stalled fetch, then a retry-storm thread) — logged in `docs/BACKLOG.md` as still worth
watching on the next long stretch, since `os._exit()` should be unconditional but hasn't
been proven against a third failure mode yet. User resumes with `--resume` from shard 33.

## 2026-09-08 — M3: `news` ingest complete

User's restart-loop finished all 479 shards: 2,186,239,880 tokens, 6,636,815 ro docs,
2,943,510 unique words, 27.9h total (including the two stop/resume cycles from the
Ctrl+C incidents above — no shard was double-counted, confirming the checkpoint's
`rows_scanned`/`docs_matched` split held up under repeated kill/resume). `os._exit()`
fix held for the rest of the run; no further stuck-process reports.

`compute_zipf.py --source news` → floor=0.36 (expected — 2.19B tokens is CulturaX-scale,
should land near `web`'s -0.67, not `wiki`'s 1.66). Top words by occurrence (`de`, `în`,
`a`, `și`, `la`, `din`, `cu`, `că`, `o`, `mai`, `nu`, `un`, `au`, `se`, `este`, `fost`...)
match `wiki`/`web`'s shape — no news-specific artifact (e.g. dateline boilerplate,
"citește și", byline patterns) visible in the top 20, though that's only the top of the
distribution; the spec's check-5 spread report is the place to look for register-bound
words this source should be pulling up (formal/news vocabulary `wiki`/`web` under-weight).

Ran `validate.py` across all three sources — 2/2 checks passed. Function words land
in-band for `news` too (`de`=7.74, `și`=7.39, `la`=7.28, `un`=6.90, `cu`=7.08, all
within 6.0–7.5), and idempotence holds (`compute_zipf.py` re-run across `wiki`/`web`/
`news` produces the identical hash).

Panel is now 3/5 toward M3's ≥5-source threshold. `subs` (OpenSubtitles RO) and `eu`
(Europarl/DGT), both direct OPUS downloads rather than HF parquet, remain before
`merge.py`'s trimmed-mean branch is reachable.

## 2026-09-08 — M3: `subs` scaffolded (OpenSubtitles RO via OPUS)

`build/ingest_subs.py`. Spec's `subs` row calls OpenSubtitles the *safe* choice
specifically (§6.2: oțios's own `subtitle_ro` was actually ~1/6th folk-music broadcast
TV, not real dialogue) — resolved the actual download via OPUS's API
(`opusapi?corpus=OpenSubtitles&source=ro&preprocessing=mono&version=latest`) rather
than guessing a URL: v2024, a single 3.56 GB gzip file, one subtitle line per line,
no per-file (movie) boundaries preserved in this export.

Structurally different from `ingest_web.py`/`ingest_news.py`: one big file instead of
many independent shards, so resume is two-stage — HTTP Range resumes the download
itself (cheap, the object is immutable per pinned version), then *processing* resumes
by re-decompressing from byte 0 and fast-forwarding (skip, don't tokenize) to the last
checkpointed line. Pinned the resolved URL/version into the checkpoint on first run so
a later `--resume` can't silently pick up a newer OPUS version mid-job. Reused
`ingest_news.py`'s proven signal-handling shape (first signal flags for a clean flush,
second calls `os._exit()`) rather than the plain flag-only version, per the lesson
logged 2026-09-06.

`documents` here counts *subtitle lines*, not the 427,889 distinct films OPUS's own
metadata reports for this corpus — no companion `.ids` file ships with the mono export
to reconstruct per-movie boundaries, so this source's `documents` column means something
narrower than `wiki`/`web`/`news`'s per spec §14's "documents is an independence claim"
caveat. Documented in the ingester's docstring and `sources.period_note`.

Smoke-tested with `--test`: downloaded the real 3.56 GB file (cached at
`data/raw/opensubtitles_ro.txt.gz`, reused freely across test/real runs since it's just
source bytes, not progress — only the *processing* checkpoint needed the
test/`--db`-isolation fix from `ingest_news.py`, applied here from the start) and
processed the first 200k lines: 53,070 unique words, 1,305,317 tokens. Top 20 already
shows a genuine conversational signature distinct from `wiki`/`web`/`news` — `să` at #2,
`nu` at #4, colloquial `e` (vs `este`) and first-person `am` both present in the top 20,
none of which rank there in the written-register sources. Processing ran at roughly
200k lines/s (pure CPU, no network) — extrapolating from the observed ~6.5 tokens/line
average against OPUS's own ~2.5B-token count, full ingestion is an estimated ~32 min of
processing on top of the (already-cached) ~6 min download, dramatically shorter than
`web`/`news` — planned to just run to completion in one sitting rather than needing the
user's-own-terminal handoff those two long jobs required.

Ran it to completion in this session (download already cached, so no re-download):
371,104,866 lines, 1,936,897,280 tokens, 2,165,628 unique words, in 75.4 minutes —
sustained rate settled at ~82-87k lines/s, well under the ~200k lines/s the `--test`
run's coarse "in 1s" timing suggested; that estimate wasn't a reliable extrapolation
basis, worth remembering next time a smoke test reports a sub-2-second duration.

`compute_zipf.py --source subs` → floor=0.41. `validate.py` 2/2 across all four sources
now (`wiki`/`web`/`news`/`subs`): function words in-band for `subs` too (`de`=7.46,
`și`=7.17, `la`=7.09, `un`=6.96, `cu`=7.01), idempotence holds. Full-corpus top-20
confirms the conversational signature from the smoke test at scale: `nu` (negation) at
#2 — striking given it doesn't crack the top 10 in any written-register source — plus
colloquial `asta`, `te`, `sunt` all present, none of which rank in `wiki`/`web`/`news`.

Panel is now 4/5 toward M3's ≥5-source threshold. Only `eu` (Europarl/DGT) remains.

## 2026-09-08 — M3 complete: `eu` scaffolded and ingested, panel at 5/5

`build/ingest_eu.py`. Spec's `eu` row names two corpora, "Europarl / DGT", as one
source — generalized `ingest_subs.py`'s single-big-file download/resume shape over a
short fixed list of two named parts instead of one file, rolling both into one
`source_id='eu'`. Resolved both via the OPUS API the same way as `subs`: Europarl v8
(10.8M OPUS-tokens, spoken-then-transcribed parliamentary proceedings) and DGT v2021
(92.6M OPUS-tokens, written EU legal/legislative translation memory) — distinct genres,
both bureaucratic-formal, both tiny (20 MB + 139 MB compressed) compared to the other
four sources.

Caught two real bugs while writing this, before any run touched real data:
1. `ensure_source_row()` was only called after the full parts loop, but each part's
   periodic `flush()` does `UPDATE sources ... WHERE source_id=?` — against a row that
   doesn't exist yet, which SQLite silently no-ops rather than erroring. Every
   in-progress total during processing would have been lost, and if interrupted
   mid-part the `sources` row would never even get created despite `source_counts`
   already holding real data — an inconsistent state that would confuse `validate.py`
   and `status.py`. Fixed by creating the row (placeholder `period_note`, updated for
   real at the end) before the parts loop starts.
2. The exit code returned 1 (failure) whenever any part wasn't fully exhausted — which
   includes a deliberate `--test`/`--limit` stop, not just a real interruption. Under
   the restart-loop's `[ $? -eq 0 ] && break`, a `--limit`-bounded run would look
   identical to a crash and get retried forever. Changed to `ingest_subs.py`'s
   convention: exit 1 only for an actual signal-interrupted stop.

`--test` smoke-tested clean (20k lines/part): 33,322 unique words, top-20 already
showing the expected formal-register shift from `subs` — `pentru`/`care`/`sau`/`este`
present, `nu` down at #15 (vs #2 in `subs`). Real run finished in well under a minute
(both files already cached from `--test`, so no re-download): 4,938,644 lines,
84,415,957 tokens, 275,156 unique words. `articolul` ("the article," as in a legal
article) landing at #18 by occurrence is about as clean a legal-register tell as this
table will produce. `compute_zipf.py --source eu` → floor=1.77.

`validate.py` 2/2 across the full 5-source panel (`wiki`/`web`/`news`/`subs`/`eu`):
function words in-band for `eu` too (`de`=7.79, `și`=7.47, `la`=7.27, `un`=6.67,
`cu`=7.13), idempotence holds across all five.

**M3 is done** (spec §13 "done when the trimmed-mean branch in `merge.py` is actually
reachable" — panel is now 5/5). Next: M4, `build/merge.py`.

## 2026-09-08 — M4: `build/merge.py` — the panel is now one table

`wrodfreq/zipf.py` gains `merge_zipf()` — the trim/mean itself (spec §8.2): >=5
reliable sources drops max/min and means the rest, 1-4 gets a plain mean (trimming 3
values to 1 is "pick the middle corpus" — `wordfreq`'s own Romanian is stuck with
exactly this shape). Uses `statistics.mean` deliberately, not a hand-rolled sum: it
sums via exact `Fraction` arithmetic internally, so the result is independent of the
order the reliable-source rows were read in — load-bearing for `validate.py`'s
idempotence check (§11.6). 6 new unit tests in `tests/test_zipf.py`, including one that
shuffles the input and asserts the same result. 16/16 tests pass.

`build/merge.py` (spec §7.4): rebuilds `merged` from scratch every run rather than
upserting — a scratch `merged_new` table is populated, then swapped in via `DROP TABLE
merged; ALTER TABLE merged_new RENAME TO merged` in one go, so a crash or interrupt
mid-run leaves the previous `merged` (or none, on a first run) untouched rather than
half-built. Realized `compute_zipf.py` already writes one `source_zipf` row per word
per source regardless of reliability (zipf=NULL/reliable=0 below the floor) — meaning
`n_attesting` and the reliable-zipf list are both derivable from `source_zipf` alone, no
need to also touch `source_counts`. `source_zipf`'s `PRIMARY KEY (word, source_id)
WITHOUT ROWID` clustering means a plain table scan already visits rows in (word,
source_id) order — confirmed via `EXPLAIN QUERY PLAN` (`SCAN source_zipf`, no separate
sort step) — so the whole merge is one streaming pass, grouping consecutive same-word
rows in Python, no big in-memory dict over the ~33M distinct words involved.

Filters to `period='contemporary' AND status='completed'` sources by design (spec
§6.2's "a table that quietly averages 1890 and 2023 is lying about the present") — a
no-op today since all five ingested sources are contemporary, but it means a future
`books`/`ref` (CoRoLa) source won't silently enter the default merge. No opt-in flag
added for those yet, since none exist to opt into — that's for when one actually lands.

`is_dex` wired to oțios's vendored DEX paradigm map (`~/devbox/otios/data/processed/
inflected_forms.db` — the "one data asset to reuse" per CLAUDE.md), read at build time
only (not a runtime dependency of the shipped package — same precedent as M1's
cross-check against oțios's installed `wordfreq`). Degrades to `is_dex=0` for everything
if the file isn't reachable, printed as a warning, never a hard failure.

**Ran it against the real 5-source panel**: 66s, 6,193,962 words written to `merged`,
27,070,331 attested-but-below-floor words correctly *omitted* (not zeroed — 81% of all
~33.3M distinct words ever attested across the panel never cleared any single source's
reliability floor, mostly `web`'s long tail). `is_dex`: 587,535 of the 6.19M merged
words matched one of 1,531,312 distinct DEX inflected forms. Top-20 by zipf are all
exactly the expected function words, every one with `n_reliable=5`.

**Two findings, both confirmed by direct query, neither a bug:**
1. `merged`'s zipf for `de` is **7.71** — above the spec's literal 6.0-7.5 function-word
   ceiling, matching the single-source finding from 2026-08-18 but now from the *proper
   5-source trimmed mean*, and it lines up almost exactly with `wordfreq`'s own real
   Romanian value (7.72). `validate.py`'s check 1 doesn't test `merged` yet — logged in
   `docs/BACKLOG.md` that the ceiling needs to be ~8.0 there too when it is, or this
   correctly-computed word fails CI forever.
2. Spec §11 check 5 names a specific expected shape — "`dumneavoastră` high in `eu`, low
   in `subs`" — and it **does not hold**: queried `source_zipf` directly, `eu`=4.71,
   `subs`=5.04 (subs is higher, not lower), `web`=5.15 is the actual high end, `wiki`=3.63
   the low end. Verified this isn't a diacritic/tokenizer artifact (queried the exact
   correct token). The rest of the top-by-spread list, restricted to `n_reliable=5` to
   exclude single-source noise, does look like genuine register/topic signal rather than
   artifacts — `vrei` (informal "you want"), `alineatul`/`alineatele` (legal
   "paragraph/subsection"), `isbn`, place names — so the *mechanism* is sound; this one
   named example in the spec was just wrong.

`validate.py` not re-run — its current checks (1, 6) only touch `sources`/`source_zipf`,
unchanged by this stage, so nothing new to confirm there. Checks 2-5 all need `merged`
and are now unblocked (logged in `docs/BACKLOG.md` as the natural next step) but weren't
added in this session — scope was `merge.py` itself.

## 2026-09-08 — M5: `build/build_lemma_layer.py`, the paradigm rollup

`wrodfreq/lemma.py` (new module, mirroring `wrodfreq/zipf.py`'s pure-function shape):
`aggregate_by_family()` and `aggregate_loose()`, ported from oțios's
`validate_diachronic.py:386-498` per CLAUDE.md's "what to copy" table — the
disambiguation math (ambiguous forms split, weighted by each claimant's own headword
frequency in the same source; documents take the max across a lemma's forms,
share-scaled, ported for fidelity even though `lemma_zipf`'s schema has no document
column to put it in). One documented departure: oțios's original rounds the
disambiguated count to an int before returning; kept as float straight through here,
since rounding can flip a word across `MIN_OCC_PER_SOURCE=5` (round(4.6)=5, a false
positive). 8 new unit tests in `tests/test_lemma.py` — including one built directly
from spec §9's own `vești`/`veste`/`veșcă` example, and one confirming the
loose-total-always->=-disambiguated-total invariant `family_ratio` depends on. 27/27
tests pass across the repo.

**Deliberately did not port oțios's `merge_panels()`** (raw-occurrence summing across
corpora) — logged prominently in both this entry and the module docstrings, since it's
the one place this stage could easily have silently reintroduced the exact problem
`merge.py`'s trimmed mean exists to prevent: CulturaX is ~200x the next source, so
summing raw lemma occurrences across corpora would reduce cross-source lemma merging to
"CulturaX's opinion with a Romanian paradigm map attached." Instead, `build/
build_lemma_layer.py` disambiguates per source (using that source's own headword
frequencies as the split prior), converts each source's disambiguated lemma-occurrence
total to *that source's own* zipf via the same `zipf_from_counts`/`MIN_OCC_PER_SOURCE`
reliability floor every other count in this project uses, and only then combines the
resulting per-source zipf values across sources via `merge_zipf()` — the identical
trimmed-mean function `merge.py` uses for surface forms. A lemma is just a word whose
count came from a paradigm roll-up instead of a single surface form; the cross-source
algorithm doesn't need to know the difference. `family_ratio` (undivided/disambiguated)
is computed the same way, in log-to-linear space: `10 ** (undivided_merged_zipf -
disambiguated_merged_zipf)`, not a raw-count ratio, for the identical reason.

Only words present in DEX's `form_lemma` paradigm map get a `lemma_zipf` row at all —
everything else stays surface-form-only in `merged`. This is also what keeps the
per-source fetch cheap: only ~1.5M distinct DEX forms are ever pulled from
`source_counts`, not the full ~33M distinct surface forms across the panel.

**Query performance bug found and fixed before the real run**: joining a ~1.5M-row temp
table of DEX forms against `source_counts` with a plain `JOIN` made SQLite scan *all* of
`source_counts` (tens of millions of rows across every source) and probe the small table
per row — it had no selectivity estimate for the `source_id = ?` filter to know only a
fraction of the table applied. `CROSS JOIN` in SQLite also means "don't reorder this
join," forcing the small table to drive instead, turning each lookup into a direct hit
on `source_counts`'s own `(word, source_id)` primary key. Measured 13x faster against
the real database (26s → 2s for the `web` source alone). Same fix applied to the
`zipf_headword` lookup against `merged`.

**Ran against the real 5-source panel**: 1,531,312 distinct DEX forms loaded, 16s total,
180,820 lemmas written to `lemma_zipf`. Verified idempotent (hashed two independent
runs — identical). Checked the spec's own explicit motivating example directly:
`înmărmuri` (317 raw surface hits, "reads as extinct") now has lemma zipf **1.67**,
well above its bare citation-form zipf of **0.82** in `merged` — the paradigm rollup
works. Notably *lower* than `înmărmurit`'s own merged zipf (2.23), which turned out not
to be a bug but a second confirmation the disambiguation is working correctly:
`înmărmurit` is genuinely ambiguous — DEX lists it as a separate adjective lexeme
("astonished/petrified") in addition to being the verb's participle — so the split
correctly divides that form's mass between the two senses rather than crediting the
bare verb infinitive with all of it.

**Finding, not a bug, worth remembering:** the "top 20 by family_ratio" report is
dominated by values in the hundreds of thousands (`îmulți` at 189,768x), far past the
spec's own stated examples (`tinereță` 298x, `veșcă` 938x). Investigated `voame`
concretely: its DEX paradigm has 40 forms, including a malformed entry (`vomeți-` with
a trailing hyphen) and a 3-way ambiguous share of `vom` with the auxiliary `vrea` —
this looks like a genuine extraction artifact inherited from oțios's vendored
`inflected_forms.db`, not a bug in the merge math here (confirmed the math does exactly
what it should on this input — an obscure lemma with near-zero own evidence, sharing a
form with something far more common, produces exactly this shape by design). Not fixed
— out of scope for this stage, and not ours to silently patch without understanding the
full extent of the issue in someone else's vendored data. Worth a closer look before
ever redistributing the lemma layer, logged in `docs/BACKLOG.md`.

Next: extend `validate.py` with checks 2-5, now that both `merged` and `lemma_zipf`
exist to check against.

## 2026-09-08 — `validate.py`: all 6 spec checks, two genuine failures found

Extended `validate.py` from checks 1+6 only to all six. Reused `wrodfreq.db.
eligible_sources` and the `CROSS JOIN`-against-a-temp-table pattern from
`build_lemma_layer.py` for every large word-set lookup here too (`_fetch_merged_zipf`)
— both for portability (a plain `WHERE word IN (...)` with tens of thousands of
placeholders risks SQLite's variable-count limit on stricter builds than this
environment's, which happens to allow 250,000) and for speed.

**Check 1 bug found and fixed**: it already checked `merged` once populated (not just
per-source `source_zipf`), but with the spec-literal `ZIPF_HIGH=7.5` rather than the
adjusted 8.0 ceiling the file's own comment already justified for the per-source
fallback — so it was failing on `merged`'s `de`=7.71 the moment `merged` existed,
exactly the finding logged under M4 above. Renamed the constant `ZIPF_HIGH_ADJUSTED`
and applied it to both branches.

**Check 2 (rank correlation vs `wordfreq`)**: implemented via `statistics.correlation
(x, y, method="ranked")` — Python 3.10+ stdlib, no scipy/numpy dependency needed,
handles tied ranks correctly (average-rank). wordfreq's full Romanian list (43,413
words) fetched via a subprocess call into oțios's venv (same cross-reference precedent
as M1's original `de`=7.72 check, now formalized as `load_wordfreq_ro()`).

**Check 3 (monotone pairs)**: 59 hand-written (common, rarer) pairs. Cross-validated
every pair against `wordfreq`'s independent Romanian zipf values before committing them
— all 59 agree on direction (some of the "rarer" words aren't in wordfreq's list at
all, zipf 0.0, which still confirms direction, just not the gap size). First draft used
several multi-word phrases (`"cavitate bucală"`, `"instituție de învățământ"`) modeled
loosely on spec's own examples — realized these can never match a `merged.word` row
since the tokenizer only ever emits single tokens, replaced every one with a validated
single-word equivalent (`orificiu`, `academie`, etc.) before the cross-check pass.

**Check 4 (DEX coverage)**: excluded 118 of 121,895 DEX lemmas (frequency > 0.5) from
the denominator first — abbreviations (`acad.`), Latin binomials (`acanthus
longifolius`), foreign-diacritic loanwords (`müsli`) that can never match a single
tokenizer output no matter the corpus. Checked reachability via the real `tokenize()`
function (`tokenize(w) == [normalize(w)]`), not a duplicated regex — CLAUDE.md: "the
tokenizer lives in exactly one module."

**Check 6 (idempotence) extended** from stage 2 alone to stages 2-4: `merge.py` and
`build_lemma_layer.py`'s `run`/`run_merge` functions imported directly and each rerun
twice, hashing `merged`/`lemma_zipf`. This also means running `validate.py` now
rebuilds both tables fresh from current `source_counts` as a side effect, not trusting
whatever was left over from an earlier manual run — matches how check 6 already treated
stage 2 before this change.

Ran the full suite against the real 5-source panel. **3/5 checks pass** (1, 3, 6);
checks 2 and 4 fail, both investigated rather than accepted at face value, both logged
in detail in `docs/BACKLOG.md`:

- Check 2, rho=0.86 (need >0.9), and it gets *worse* at higher wordfreq-zipf thresholds
  (0.79 at zipf>=4.5) — ruling out low-frequency tail noise as the explanation. Traced
  the biggest divergences to Romanian elision prefixes (`într`, `dintr`, `printr` —
  normally `într-o`, `dintr-un`, hyphenated before a vowel): the tokenizer's
  keep-internal-hyphens rule (deliberate, for genuine compounds) makes `într-o` one
  token, fragmenting what should be one preposition's count across many separate
  `într-X` compounds. Likely the same mechanism behind the `n-o`/`n-am`/`l-ai`-shaped
  entries already seen high in M4's spread report. Not symmetric — the same rule is
  *more* correct for real compounds/proper nouns (`cluj-napoca` staying one token), and
  wordfreq's own data has a separate-looking issue (`ul`/`ului`/`uri`/`urile` as
  free-standing "words" with implausibly high frequency — enclitic suffixes that never
  stand alone in real Romanian, hinting at a subword-segmentation artifact on
  wordfreq's side). Not fixed: a real fix means distinguishing elision hyphens from
  compound hyphens and re-ingesting all five sources, well beyond this session's scope.

- Check 4, 85.1% (need 95%). Spot-checked the missing sample: dominated by genuinely
  obscure/archaic/technical DEX headwords (`abcede`, `abdomenoscop`,
  `abstenționist`), not common words a working pipeline should have caught. Read
  oțios's `extract_inflected_forms.py`: `lexeme.frequency` is copied verbatim from DEX
  Online's own database column, not derived from any corpus — and `acanthus
  longifolius` (a Latin botanical binomial) scoring 0.99 on that scale doesn't square
  with "frequency" meaning real-world usage. Best guess, unconfirmed: DEX's own
  `frequency` field measures something like lexicographic completeness rather than
  usage, which would make 95% coverage from any realistic *contemporary* panel
  structurally unreachable — a question for whoever understands DEX Online's schema
  better, not something to silently work around by loosening the threshold.

`3/5 checks passed` is `validate.py`'s honest current state — left both failures
failing (nonzero exit) rather than adjusting the checks to pass, since both are real,
substantive findings about the pipeline's current limits, not implementation bugs in
the checks themselves.

## 2026-09-09 — M6: the actual package. `wrodfreq/__init__.py` had nothing in it

Checked before starting: `wrodfreq/__init__.py` contained exactly `__version__ = "0.1.0"`.
None of the API spec §10.1 promises — `zipf_frequency`, `word_frequency`, `top_n_list`,
`frequency_detail`, `lemma_frequency`, `by_source`, `build_info` — existed anywhere.
Five milestones of ingesting, merging, and validating a table nobody could actually
import and use. Built `build/build_package.py` (spec §7.6) plus the real API
(`wrodfreq/__init__.py`, new `wrodfreq/_surface.py` lazy loader).

**Sizing the data file took real measurement, not a guess.** First attempt — one file,
words + zipf + n_reliable + n_attesting + spread + by_source, plain Python floats —
came in at 39.6 MB, over spec's 30 MB target for "the surface table". Two changes, both
measured before/after rather than assumed to help:

- Quantized zipf and spread to centizipf ints (`round(zipf * 100)`) instead of leaving
  them as Python floats — msgpack packs a float as 8 bytes regardless of the value's
  actual precision, and spec already says to round to 2 decimals "because the third
  decimal is noise and it costs real bytes across ~2M entries"; quantizing takes that
  reasoning to its actual conclusion instead of rounding then repacking as a float
  anyway. Core table: 193.4 MB packed / 31.1 MB compressed unquantized → 97.7 MB packed
  / 29.2 MB compressed quantized.
- Split `by_source` into its own file (`ro_by_source.msgpack.xz`), lazy-loaded only if
  `by_source()` is actually called. Combined-and-quantized still measured 38.7 MB, over
  target; `by_source` alone measured 9.5 MB, and it's also the one extension spec
  explicitly calls "clearly marked as such" — splitting it off both hits the target and
  matches that framing, not an arbitrary cut chosen just to hit a number.

Final: `ro_surface.msgpack.xz` 27.8 MB, `ro_by_source.msgpack.xz` 9.1 MB (a touch smaller
than the isolated measurement since the surface file's own header/word list isn't
duplicated in it — see next paragraph).

**Bug caught mid-build, not after:** the first working version of `ro_by_source.msgpack.xz`
carried its own copy of the full 6.19M-word list (for "self-containment") and came out at
27.6 MB instead of the ~9.5 MB measured for `by_source` data alone — the word list itself
was almost the whole cost. Fixed by making the file purely positional (row *i* of
`by_source` corresponds to word *i* of the surface file's own word list, both built from
the same `words` array in one run) with a `word_count` field as a cheap integrity check
(the API refuses to use a mismatched pair) rather than a second copy of 6.19M strings.

**`is_dex` and the lemma layer are deliberately not in either file.** Same unresolved
DEX Online licensing question CLAUDE.md already flags — and unlike `merge.py`'s `is_dex`
column (a private research field in `wrodfreq.db`, not redistributed to `pip install`
users), *shipping* an aggregate of ~587k per-word booleans derived from DEX's own
headword list is a real redistribution question, not a hypothetical one: anyone could
reconstruct a large fraction of DEX's own word list by cross-referencing which shipped
words have it set. Not a decision to make unilaterally here. `lemma_frequency()` reflects
that honestly — always 0.0 for now, degrading the same way `zipf_frequency` does for an
unknown word, not crashing or pretending the layer exists.

**Caught a real packaging bug by actually testing the built wheel, not just the API in
the repo.** Built the wheel (`uv build`) and inspected it directly: it shipped with
*zero* files under `wrodfreq/data/` — Hatchling's default file selection is git-tracked
files only, and the data files are deliberately gitignored (large rebuildable binaries).
`pip install`ing that wheel would have raised `FileNotFoundError` on the very first call.
Fixed with `[tool.hatch.build] artifacts = ["wrodfreq/data/*.msgpack.xz"]` — has to sit
at the top level of `[tool.hatch.build]`, not just under `targets.wheel`, since `uv
build` (and `pip wheel`) build the wheel *from* the sdist, and the sdist needs the same
override or there's nothing in the intermediate archive for the wheel step to include
regardless of its own setting. Verified the actual fix by rebuilding and inspecting the
wheel's file listing directly, twice — once wrong, once right — not by reasoning about
what Hatchling's docs say should happen.

**Verified against a real, isolated install**, not just imports from within the repo:
built the wheel, `pip install`ed it into a throwaway venv with no access to this
checkout or the SQLite database, and called every public function from there —
`zipf_frequency`, `top_n_list`, `frequency_detail`, `by_source`, `lemma_frequency`,
`build_info` all worked correctly against the shipped data alone.

`build/validate.py`'s check 6 extended once more: stage 5 (`build_package.py`) now
joins stages 2-4, verified idempotent by building its payload twice in one process and
hashing (also spot-checked separately: two full `build_package.py` runs a few minutes
apart produced byte-identical files on disk). `3/5 checks passed`, unchanged — this
stage doesn't touch the two known-failing checks.

16 new tests in `tests/test_api.py`, against a small synthetic fixture (four made-up
words, not the real ~37 MB build artifact) via `monkeypatch` on `_surface.SURFACE_PATH`/
`BY_SOURCE_PATH` — covers the unknown-word contracts (`0.0` vs `None`), the `minimum`
floor, `by_source`'s lazy second-file load, and an `ascii_only` edge case worth calling
out: a naive "over-fetch by a fixed multiplier then filter" implementation of
`top_n_list(n, ascii_only=True)` would under-return, since Romanian diacritics are
common even in high-frequency words (`și`, `să`, `nu`-shaped words all carry them) —
implemented as a proper cached filter over the full sorted list instead, and the test
constructs a fixture specifically to catch the under-return case (an ASCII word ranked
below a diacritic one that outranks it). 43/43 tests pass repo-wide.

M6 is functionally done: `pip install`-and-use works end-to-end. Remaining before a
real release: the two `validate.py` failures (checks 2 and 4, both understood, neither
fixed), the DEX licensing question (blocks `is_dex` and the whole lemma layer from ever
shipping), and M7 (exposing `n_reliable` back to oțios — the only coupling between the
two repos).

## 2026-09-09 — M7: feeding `n_reliable` back to oțios

Spec §13's last milestone is explicit that the change lands in the *other* repo: "Expose
`n_reliable` to oțios as a corroboration signal. That is a change in the oțios repo, not
this one, and it is the only coupling between the two." Read oțios's `CLAUDE.md` and
`validate_with_wordfreq.py` first — oțios already has a standalone screen doing almost
this exact thing with the *original* `wordfreq` package, disconnected on 2026-08-11
because it resolved almost nothing: "measured over 60,000 candidates, 99.6% score
exactly 0.00" (wordfreq's Romanian coverage is too thin). That's the whole reason
wRodfreq exists (`docs/wordfreq-recipe.md` §4), so before writing anything, measured
whether it actually fixes that specific problem rather than assuming spec's design intent
would pan out: a random 2,000-word sample of oțios's real 18,271-word shortlist against
`wrodfreq.zipf_frequency()` came back only 27.8% zero-resolution. Worth building.

Wrote `~/devbox/otios/validate_with_wrodfreq.py`, mirroring `validate_with_wordfreq.py`'s
exact CLI/IO contract (same default input, same three tiers, same `--threshold`/
`--upper-threshold` defaults for continuity) so the two screens are directly comparable,
but powered by wRodfreq instead: no `simplemma` lemmatization step (wRodfreq's own
surface-form coverage — ~6.2M words vs. wordfreq's ~43k — makes it less necessary, and
sidesteps a documented simplemma failure mode noted in the old script's own docstring:
picking the wrong homograph lemma, `secret`→`secreta`, `dor`→`durea`). Kept the same
paradigm-max rollup pattern (`paradigm_zipf` → `paradigm_detail`) over DEX's own
`inflected_forms.db`, since "is any form of this word in current use" is still the right
question even with better resolution — just now also carries whichever form's zipf won
the max its full `n_reliable`/`n_attesting`/`spread` too, not just its number.

New columns `wordfreq` could never provide, added straight from `frequency_detail()`:
`n_reliable`, `n_attesting`, `n_sources`, `spread` — literally spec §8.3's corroboration
count, corpus by corpus, not an average. This is the actual M7 deliverable; everything
else in the script is scaffolding to get real candidate words in front of it.

Installed wRodfreq into oțios's `.venv` as an editable dependency (`uv pip install -e
../gov2/wrodfreq`) and added `-e ../gov2/wrodfreq` to `requirements.txt` — editable so
oțios always sees wRodfreq's current data as the panel grows, not a frozen wheel snapshot
that needs manual reinstalling. Documented the whole thing in oțios's own `CLAUDE.md`,
right below the existing `wordfreq`-screen note, so a future reader sees both side by
side and understands why the second one exists.

Ran it for real on the full 145,358-candidate curated list (not just the sample): 25.7%
zero-Zipf, matching the sample closely. **Deliberately staged as a standalone CSV output,
not wired into `make_shortlist.py`'s scoring or `ui.db`** — same status
`dcr_definitions.csv`/`clre_dcr_definitions.csv` already had in that repo before anyone
decided how to use them (oțios's own established convention for a new signal). How much
a 5-corpus corroboration count should actually move the shortlist's score, if at all, is
a real editorial decision with UI/threshold implications (oțios's own `CLAUDE.md` is
explicit that its score vs. hide-flags split was tuned by measurement, e.g. the
`rare_in_use` upper threshold's 3.5-not-4.5 story) — not something to decide unilaterally
inside what's supposed to be a data-exposure milestone.

**Found unrelated pre-existing uncommitted state while checking `git status` in oțios,
not caused by this work**: `docs/wordfreq-recipe.md` showed as deleted from the working
tree. Checked before touching anything — `git log` shows it last modified 2026-08-11, a
month before this session, and nothing in this session ever wrote to or read that path
(only wRodfreq's own identically-named doc, in the other repo, was touched). Left it
exactly as found; committed only `CLAUDE.md`, `requirements.txt`, and
`validate_with_wrodfreq.py` by explicit path rather than `git add -A`, so the unrelated
deletion stays isolated and visible for whoever left it there to resolve on their own
terms. Committed as oțios `10b9883`.

Also confirms — not just hedges — the check-4 finding from two entries above:
`Lexeme.frequency` is a literary-prominence score, not a usage frequency, per oțios's own
`CLAUDE.md` ("`zapciu` ... is 0.96 while `internet` is 0.88"). Check 4's 95%-of-DEX-lemmas
target is unreachable by any realistic contemporary panel by construction, not just in
current practice.

Per spec §13, M1 through M7 are now all complete. What's left is entirely open questions,
not milestones: the two `validate.py` failures (checks 2 and 4), the DEX licensing
decision, and however oțios's maintainer decides to weigh the new corroboration signal
into its own scoring.

## 2026-09-09 — Session close: all seven milestones done, handoff written

End-of-session checkpoint. `docs/NEXT-SESSION.md` now holds the consolidated open
questions and next steps for picking this back up cold — everything below is already
covered in more detail across this file's M1–M7 entries and `docs/BACKLOG.md`; this is
just the wrap-up pointer.

Noticed but not acted on this session, since it wasn't what was asked: `README.md`'s
first line still says "Status: spec only, no code yet" — stale since M1. Left for
whoever picks this up next; flagged in `docs/NEXT-SESSION.md`.

## 2026-09-14 — `validate.py` check 4 recalibrated, by measurement

Resumed from `docs/NEXT-SESSION.md`. Asked which open item to tackle first; picked
check 4 (DEX coverage, failing 85.2%, need 95%).

The 2026-09-09 finding already confirmed *why* it fails (DEX's `lexeme.frequency` is a
literary-prominence score, not usage frequency — oțios's own `CLAUDE.md` says so
directly) but left the actual fix open: recalibrate the threshold, swap the reference
field, or drop the check. Investigated both alternatives before picking one:

- **Tried swapping the reference field** to `dict_sources.in_current_dict` (does DEX
  have anything closer to real usage?). Measured worse, not better: 62.2% coverage
  (136,475/219,306 tokenizer-reachable words), because "documented in some dictionary
  published 2005+" includes specialized/regional/technical dictionaries just as freely
  as general ones — `izodonție` (dental term), `bozânteancă`, `scurmuzui` all clear that
  bar. Not the right field.
- **Recalibrated the threshold instead**, by measuring coverage across a sweep of
  `frequency` cutoffs rather than guessing one number: >=99% coverage held steady from
  `frequency>=0.99` down through `>=0.85`, then degraded roughly linearly — 96.1% at
  `>=0.75`, 93.9% at `>=0.70`. A `LIMIT N`-by-rank version was tried first (rank-based
  felt more principled than an arbitrary threshold) and produced misleading numbers —
  DEX's frequency values are heavily tied at round numbers like 0.99, so `ORDER BY
  frequency DESC LIMIT 4500` cuts arbitrarily through a tied group and measured 94.8%,
  while the equivalent full `frequency>=0.99` threshold (covering the same tied group
  completely) measured 99.9% on the identical underlying words. Threshold-based avoided
  the artifact entirely.

Picked `frequency >= 0.80` (97.7% measured, 48,648 tokenizer-reachable lemmas) over a
value nearer the exact 95% crossover, so ordinary future panel changes (a source
re-ingested, floors shifting slightly) don't turn this into a flaky check that fails on
noise rather than a real regression.

Ran the full `validate.py` suite to confirm: **4/5 checks now pass** (1, 3, 4, 6) — only
check 2 (rank correlation, the tokenizer/elision issue) remains, and that one still
needs the decision logged in `docs/NEXT-SESSION.md` (a full re-ingest, not something to
attempt casually). 43/43 tests still pass; check 4's fix didn't touch any other stage.

## 2026-09-17 — README rewritten: it still said "spec only, no code yet"

Talked through the DEX licensing question first — user's read is low real risk (a plain
word list and some numbers, not the actual dictionary text), worth just asking
dexonline.ro directly rather than treating it as a blocker. Deferred, not resolved;
still open in `docs/NEXT-SESSION.md`.

Asked what to tackle next; picked the stale `README.md` over the tokenizer/elision fix
(needs a full re-ingest) or helping design oțios's scoring integration (an editorial
call, not an engineering one).

Rewrote it top to bottom against the real, current state rather than patching the
M1-era text: replaced the "spec only, no code yet" banner with real usage examples
(`zipf_frequency`, `word_frequency`, `top_n_list`, `frequency_detail`, `by_source`) —
every example's output verified against the actual running package before being written
down, not composed from memory. Fixed both the RO and EN (ASD-STE100) sections' claim
that the lemma layer is already available — it computes and validates 180,820 lemmas
but isn't shipped pending the DEX licensing question, and the README was overclaiming
by omission. Rewrote the Roadmap's seven milestones from their original aspirational
"done when" framing to actual completed results (real token counts, real coverage
numbers) — all now checked off, with a closing pointer to `docs/NEXT-SESSION.md` for
what's still open. Added `docs/NEXT-SESSION.md` to the Docs list (existed since 2026-09-09,
was never linked). Added a closing paragraph on the M7 coupling now that it's real
(`validate_with_wrodfreq.py` in oțios), with its own measured numbers (25.7% vs. 99.6%
zero-signal).

Caught two smaller issues while proofreading rather than after: the opening line said
"shipped as `pip install wrodfreq`" one paragraph above a status line admitting it isn't
on PyPI yet — reworded to "designed to ship as". And the build instructions ran `pytest`
without first installing the `dev` extra that provides it (`uv pip install -e .` alone
doesn't pull in `pytest`) — fixed to `-e ".[dev]"`, and actually ran the corrected
install + test sequence, plus separately verified the "43 tests pass with no data files
at all" claim by moving `wrodfreq/data/` aside and re-running, rather than assuming the
synthetic-fixture tests in `test_api.py` were the only ones that mattered.

## 2026-09-17 — Tokenizer/elision fix: correct, verified, but not sufficient alone

Picked up the check-2 item from `docs/NEXT-SESSION.md`. Before writing any splitting
rule from grammar knowledge alone, queried the actual top-208 hyphenated words in
`merged` by zipf — the rule needed to be broader than "split prepositions like
într-/dintr-/printr-": participle+clitic (`avut-o`, `luat-o`), imperative+clitic
(`du-te`, `spune-mi`, `lasă-mă`), and gerund+clitic (`referindu-se`, `aflându-se`) all
needed the RIGHT-hand side of the hyphen checked too, not just the left. Also found and
had to guard against a genuine ambiguity: `v`/`l`/`i`/`m`/`c` are simultaneously common
Romanian clitics (`v-a`, `l-a`) *and* valid Roman-numeral letters, so a naive rule
would wrongly split Roman-numeral ordinals (`ii-a` "the 2nd", `xii-lea` "the 12th") —
confirmed empirically (151 candidate false positives found by a broad regex sweep, of
which only the genuinely ambiguous ones — `ii`, `iii`, `iv`, `vi`...`xxiv`, and bare
`x` — needed excluding; the single letters `i`/`v`/`l`/`m`/`c` keep the dominant
elision reading since real ordinals almost never appear bare like `v-a` in practice).

Implemented `wrodfreq/tokenizer.py`'s `_split_elisions()` and verified it against the
full top-100 hyphenated words by zipf, then all 33,859 at a lower reliability bar —
every compound/proper-noun/loanword-suffix case (`mass-media`, `cluj-napoca`,
`site-ul`, `prim-ministru`, `on-line`) correctly stayed joined, every genuine elision
correctly split, with zero unexpected surprises in either direction. 10 new tests, all
52 passing.

**Avoided a full 5-source re-ingest** (which the 2026-09-08 finding assumed would be
necessary) by writing `build/migrate_elisions.py` instead: since a token's occurrence
count is exact regardless of when it's computed, splitting `într-o`'s *existing* count
into `într`+`o` in `source_counts` produces exactly what a corrected tokenizer would
have produced from scratch. Only `documents` becomes a slight upper bound for split
words (a document with both `într-o` and `într-un` credits `într` twice instead of
once) — accepted, since zipf is what check 2 actually measures and is unaffected.
Also had to explicitly wipe `source_zipf` per migrated source: `compute_zipf.py` only
INSERTs/UPDATEs a row for a word *currently* in `source_counts`, never deletes one for
a word that disappeared, so a bare re-run would have left every deleted compound's old
`source_zipf` row stale.

Backed up `data/wrodfreq.db` first (4GB — disk was at 98% full, had to clear
`data/raw/`'s now-unneeded 3.5GB of cached OpenSubtitles/Europarl/DGT downloads to make
room). Ran `--dry-run` first: 2,095,059 words would split, +384,408,820 tokens overall
— and the elision rate scaled with register exactly as expected before even applying
anything (1.2% of `web`'s tokens vs. 3.9% of `subs`'s conversational text), a good sign
the rule was measuring something real. Applied for real, matching the dry run exactly.
Re-ran `compute_zipf.py` → `merge.py` → `build_lemma_layer.py` → `build_package.py` →
`validate.py`: idempotence (check 6) still holds across every stage, confirming the
migration didn't introduce nondeterminism. `merge.py`'s own top-20 changed visibly:
`a` jumped to #3 by zipf (wasn't in the top 20 before), `o` newly entered the top 20 —
exactly the words the fix targeted. Directly compared against `wordfreq`: `într` moved
from 3.45 (wildly wrong) to **6.05**, matching wordfreq's own **6.08** almost exactly;
`dintr`/`printr`/`a`/`o`/`am`/`au`/`ai` all showed similarly dramatic, correct
improvement. Removed the backup once idempotence and the targeted-word checks both
confirmed correctness (disk space was tight enough to matter).

**But check 2's overall Spearman rho barely moved: 0.860 → 0.863.** Investigated why
rather than declaring victory on the individual-word wins — recomputed the full
biggest-divergence list against `wordfreq` post-migration, and a completely different,
previously-invisible pattern was now at the top: `iudeo`, `viceprim`, `traco`,
`austro`, `științifico`, `carpato`, `socio`, `geto`, `daco`, `indo`, `ruso`, `moldo`,
`germano`, `medico`, `pseudo`, `cvasi`, `anglo`, `anti` — Romanian combining-form
compound adjectives (`austro-ungar`, `socio-economic`, `daco-roman`,
`anti-terorist`), a completely different phenomenon from clitic elision. `wordfreq`
apparently splits these too, so `austro`/`anti`/`daco`/etc. get real standalone
frequency aggregated across every compound they appear in, while ours still keeps
each compound joined — fragmenting these prefixes' true frequency the exact same way
elision used to fragment `într`'s, just via a different hyphenation mechanism.

Measured the scope before deciding whether to chase it in the same session: 7,395
distinct prefixes appear in >=15 distinct hyphenated compounds each in `merged` — a
comparably-sized problem, not a quick extension of the elision rule. Crucially, it's
also much *noisier*: alongside genuine, well-established combining forms (`anti`,
`non`, `auto`, `pre`, `super`, `pseudo`, `micro`, `multi`, `neo`, `bio`, `eco`, plus the
historical/ethnic ones above) the same "productive prefix" signal also catches clearly
spurious single letters (`d`, `t`, `b`, `p`, `c`, `x`) and questionable cases (`al`,
`se`) that are far more likely coincidental noise from a huge web corpus than real
Romanian compound-formation morphology — a blanket rule here risks manufacturing
nonsense splits at meaningfully larger scale than the elision fix's one accepted edge
case (`v-lea`). Separately, confirmed part of the remaining gap isn't fixable on our
side at all: `ul`/`ului`/`uri`/`urile` are wordfreq's biggest divergences in the
*opposite* direction (scored implausibly high, 4.6–5.6 zipf, for Romanian noun-suffix
fragments that never stand alone in real text) — reads as a subword-segmentation
artifact in wordfreq's own data, not something fixing our tokenizer could chase.

Stopped here rather than unilaterally expanding into a meaningfully riskier fix.
`validate.py` is still 4/5 (checks 1, 3, 4, 6 pass) — the elision fix is real, verified,
and worth keeping regardless of what happens with check 2 next, but check 2 itself
needs a deliberate decision on the combining-form question before it can clear 0.9.

## 2026-09-17 — Check 2 closed: the gap was wordfreq's tie structure, not our tokenizer

Picked up the open BACKLOG item asking for a deliberate decision on Romanian
combining-form compounds (`austro-ungar`, `socio-economic`), which the previous session
suspected had become the dominant driver of check 2's stuck Spearman rho (0.863 vs. the
>0.9 gate) once clitic elision was fixed. Measured it before building anything. The
hypothesis was wrong, and the metric turned out to be the problem.

**Combining forms buy +0.001.** Removing the 32 curated combining forms (`socio`,
`austro`, `daco`, `pseudo`, `geto`, `româno`, …) from the comparison *entirely* — an
upper bound on what any splitting rule could achieve — moves rho 0.8628 → 0.8637.
Removing all 6,659 ">=15 compounds" prefix candidates makes it *worse* (0.8367): they
are mostly ordinary words, not a defect class. Deleting the 200 worst residuals outright
only reaches 0.871; clearing 0.909 would take deleting ~2,000 words. No fixable tail
exists, so no tokenizer change was made — the riskier splitting rule the previous
session declined to build unilaterally would have bought nothing.

**What the gap actually is.** `wordfreq`'s Romanian list carries only 356 distinct zipf
values across the 43,095 words we share with it, 12,601 of them in the 3.00–3.25 band
alone — roughly 600 words per tied value, a bucket spacing *narrower than our own
per-word disagreement*. Ranking within a band is a coin flip: within-band rho is
0.28–0.58 throughout. Two results confirm it is the reference's tie noise and not a
divergence of ours: restricting to wordfreq's *more confident* words makes rho **worse**
(0.796 at wf zipf >= 4.5), backwards for a genuine divergence; and `ours - wordfreq` is
a flat, symmetric median -0.15 / IQR 0.29 in **every** band, where a tokenizer bug is
skewed and band-dependent. Pearson on the raw values is 0.911, top-1000 overlap
801/1000, and the uniform -0.15 is a ~1.4x denominator difference — the expected
signature of spec §3.2's honest denominator rather than a defect.

**Re-specified check 2 to pairwise concordance** (spec §11.2 rewritten,
`build/validate.py` check 2 rewritten). It asks what survives the ties: when wordfreq
separates two words by enough to mean something, do we order them the same way?
95.4% at >=0.3 zipf separation (the gate, set at 0.93), 98.3% at >=0.5, 99.9% at >=1.0.
Computed **exactly** rather than sampled — a Fenwick sweep over the reference-sorted
list scores all 569,644,768 separated pairs in 2.2s, where a seeded sample would drift
the moment the word list changes and this gate has to be reproducible run to run.
Verified against a brute-force double loop on 500 randomised cases including duplicate
values. Rho is still printed ungated next to Pearson, median delta and IQR, to watch
drift on. Still a real regression detector: the pre-2026-09-17 elision bug moved words
by whole zipf points (`într` was 3.45, is 6.05), squarely inside the population it
scores.

**`validate.py` now reports 5/5 gated checks passing.** The two residual tails are
corpus panel, not tokenization — we underrate toponyms and proper nouns (`napoca`,
`dobrogei`, `rebreanu`, `babeș`) where wordfreq is subtitle/Wikipedia skewed, and
overrate contemporary news/admin vocabulary (`vaccinare`, `ciolacu`, `migranți`,
`fotovoltaice`, `pensiilor`), which is the five-source contemporary panel doing its job
against a reference that predates most of it. `ul`/`ului`/`uri`/`urile` remain
wordfreq's biggest divergences in the opposite direction, a subword-segmentation
artifact on its own side and not ours to chase.
