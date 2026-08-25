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
