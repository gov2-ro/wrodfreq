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