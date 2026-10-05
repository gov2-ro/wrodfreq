# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state: built — 0.2.0 release candidate (2026-10-05)

All seven build milestones are done. Six sources ingested (28.75B tokens), 6,064,995
words in `merged` (6,041,542 shipped), lemma layer of 180,569 lemmas, `validate.py` 5/5,
~230 tests, CI green. **Not yet published** — see `docs/BACKLOG.md` for what stands
between this and a release (DEX licence answer, data-licence wording, `LICENSE` file, the
4 GB database vs GitHub's 2 GiB asset cap). Read `docs/wrodfreq-spec.md` for the design;
it is still authoritative, with ADRs in `docs/decisions/` amending it.

- `docs/wrodfreq-spec.md` — the build spec (schema §7.1, merge §8, lemma layer §9, API
  §10, validation §11, layout §12, milestones §13, traps §14).
- `docs/method.md`, `docs/sources.md` — the public methodology and the six corpora, **in
  Romanian**, for readers of the table. Keep their numbers true when the table changes.
- `docs/wordfreq-recipe.md` — why the parent project (oțios) rejected this method for
  *its* question, and the measurements behind that. Read §§1–8 before arguing with any
  design decision here; most objections are already answered there.
- `docs/BACKLOG.md` — open bugs/debt/enhancements, `- [ ]` entries with enough context to act on.
- `docs/activity-history.md` — chronological log, entries under `## YYYY-MM-DD — Short Title`.
- `docs/NEXT-SESSION.md` — where things stand and what needs a human decision.
- `docs/decisions/` (ADRs), `docs/briefs/` (self-contained task briefs),
  `docs/hyphen-labels.md` (the labelled hyphen-row sample and its policy),
  `docs/dex-online-cerere.md` (draft request to DEX Online, **not sent**).
- `tools/dex_extractor/` — unchanged copy of oțios's DEX extractor, for provenance only;
  not part of the pipeline.

Keep `BACKLOG.md` and `activity-history.md` current as work lands.

## What this project is

A Romanian word-frequency table (Zipf scale) built from ≥5 open corpora, shipped as
`pip install wrodfreq` with an API that is drop-in compatible with `wordfreq`, plus the
raw SQLite for researchers. It is deliberately better than `wordfreq`'s Romanian (three
sources, "small" list only, floor at Zipf 3.0), and adds two things no frequency resource
publishes: per-source retention (`n_reliable`, `n_attesting`, `spread`) and a DEX-derived
per-lemma layer.

It is **not** a lemmatizer, tagger, corpus distribution, or an oțios feature.

## Relationship to oțios — read this before copying anything

The parent project is at `~/devbox/otios` (spin-off of
https://github.com/gov2-ro/voroave). Its `CLAUDE.md` is worth reading for pipeline
conventions. Two hard rules:

1. **Never import from oțios at runtime.** Code is copied in; nothing is depended on.
2. **Oțios asks a different question** — *did this word's usage change* (it lives on
   disagreement between corpora). wROdfreq asks *how common is this word* (it wants the
   typical value). The figure-skating trimmed mean is wrong there and correct here. Do
   not carry oțios's reasoning across unexamined.

Consequently oțios's corpus processors would produce a **broken** frequency table if
lifted unchanged. Spec §3 lists the four departures; the two that silently corrupt every
number:

- **Open vocabulary.** `process_culturax.py:294-297` counts a token only if it is in the
  ~315k DEX form set. Count *every* token instead; bound memory by flushing/pruning
  (§7.2), never by a vocabulary filter. Otherwise `laptop`, `selfie`, `covid`, `clujean`
  are invisible.
- **Honest denominator.** The shared `tokenize()` ends with
  `[t for t in tokens if len(t) > 2 and not t.isdigit()]`. Romanian's most frequent words
  are 1–2 characters (`de`, `la`, `cu`, `o`, `a`, `nu`, `se`), so `tokens_processed` is
  not a token count and every ppm derived from it is inflated. **Drop the `len(t) > 2`
  filter**; count all alphabetic tokens in numerator and denominator, exclude numerals
  from both.

Also: store per-source counts and derive the merge as a view (never merge in place), and
keep surface-form counts and the lemma rollup in **separate tables** — you can roll up,
you cannot un-roll.

### What to copy, and from where

| From (`~/devbox/otios`) | What | Change |
|---|---|---|
| `dump_parser.py:31-38` | `normalize()` — lower → `ş→ș`, `ţ→ț` → NFC | none |
| `process_culturax.py:81-84` | tokenizer regex `[a-zăâîșț](?:[a-zăâîșț\-']*[a-zăâîșț])?` | drop the `len > 2` filter |
| `process_culturax.py:163-189, 240-320` | per-file checkpointing with row-group resume, atomic `.tmp`+`replace()` saves, SIGTERM/SIGHUP flush | lift wholesale |
| `process_culturax.py` docstring | the HuggingFace `ds.skip(N)` cycling bug workaround (read parquet shards via `HfFileSystem` + `pyarrow`, not `datasets` streaming) | none — hard-won |
| `validate_diachronic.py:386-440` | `aggregate_by_family()` — share-weighted paradigm rollup | adapt per §9 |
| `status.py`, `health_check.py`, `audit.py` | monitoring triad for multi-hour jobs | keep the shape |

**The tokenizer lives in exactly one module, `wrodfreq/tokenizer.py`.** Oțios has four
copies, identical only by discipline; two sources tokenized differently cannot be merged
and nothing will tell you. A test must assert byte-identical token streams across every
ingester.

**The one data asset to reuse, not rebuild:** `~/devbox/otios/data/processed/inflected_forms.db`
(203 MB; 317,721 lexemes, 2,269,003 inflected forms, 1,633,231 form→lemma rows of which
200,601 are ambiguous). A hand-curated DEX paradigm map, not a lemmatizer's guesses.
The extractor is copied, unchanged, into `tools/dex_extractor/` for provenance. The DEX
Online licence question is **still open**: the lemma layer ships in the wheel as
`ro_lemma.msgpack.xz` on the assumption that permission will be granted, and if it is
refused that file must come out of the package (`build_package.py`) and the form→lemma map
must not be redistributed. **Do not** reuse oțios's `corpus_frequencies.db` counts
— they are DEX-restricted with a wrong denominator. The code transfers; the numbers do not.

## The merge rules that decide whether the table is right

- **A source abstains; it never reports zero.** Below `MIN_OCC_PER_SOURCE = 5` a source
  contributes `zipf = NULL, reliable = 0` — not a zero. A zero is a claim ("rare"); a
  small corpus that never saw a word is usually just small.
- **Floors are per source and derived, stored in `sources`.** Five occurrences in 80M
  tokens is a different claim from five in 17B. **Never hardcode a Zipf number** in
  consumer code or tests — a pinned constant breaks the day a source lands.
- **Trim only at ≥5 reliable sources** (drop max and min, mean the rest). At 3–4 use a
  plain mean; trimming three values leaves one, which is "pick the middle corpus".
- **Never weight sources by size.** CulturaX is >10× the next source (23.8B vs 2.2B tokens in 0.2.0; it was ~200× against Wikipedia alone), so size-weighting
  reduces to "CulturaX with extra steps" and defeats the trim.
- **Tag every source with a `period`** (`contemporary` / `mixed` / `historical`) and make
  the default merge contemporary-only. `books` is 19th–early-20th century; CoRoLa spans
  1945–present with undated frequency lists and over-represents pre-1953 spellings by
  50–110×. A table that quietly averages 1890 and 2023 lies about the present.
- **`documents` is an independence claim.** Where a corpus has few authors, count authors
  and say so in `sources.period_note` (LUMRO: 175 novels, 111 authors, 638 of 1,425 rare
  words from one person).

Lemma layer (§9), the three things a re-implementation gets wrong: ambiguous forms are
**split, not duplicated**; the split is weighted by each lemma's own headword frequency;
documents take the **max across a lemma's forms, share-scaled** — never the sum, never
all-or-nothing.

## API contract

`zipf_frequency`, `word_frequency`, `top_n_list` must behave exactly like `wordfreq`'s,
with `'ro'` accepted and ignored — one changed import line is the entire adoption story.
`zipf_frequency` returns `0.0` for an unknown word; `frequency_detail` returns `None`.
Two different signals for two different questions — do not unify them. Extensions
(`frequency_detail`, `lemma_frequency`, `by_source`, `build_info`) are clearly marked as
extensions, and the lemma layer must degrade gracefully when the paradigm map is absent.

## Commands

Follow oțios's "one script per pipeline stage" convention; every stage resumable and
idempotent. The pipeline, in order:

```bash
python build/ingest_wiki.py       # then news, subs, eu, web, social — writes source_counts + sources
python build/compute_zipf.py      # per-source zipf + reliable flag
python build/merge.py             # trimmed mean → merged
python build/build_lemma_layer.py # needs inflected_forms.db; optional
python build/build_package.py     # → wrodfreq/data/ro_{surface,by_source,lemma}.msgpack.xz
                                  #   (rows with 3+ hyphens are left out of the package, not the db)
python build/validate.py          # must run in CI and fail the build
```

Ingesters are multi-hour to multi-day jobs that *will* be killed — checkpoint from the
first commit, and run them under the status/health-check/audit triad.
Tests: `python -m pytest -q` (no data files needed). Release gate: `.github/workflows/release-check.yml`
on a `v*` tag; `validate.py` itself cannot run in CI (the 4 GB database is not in git) — run it
locally before tagging.

## Validation is the gate

`validate.py` must fail the build. The one check that catches the worst bug in the spec:
**function words land in Zipf 6.0–7.5** (`de`, `și`, `la`, `un`, `cu`) — if they do not,
the denominator is wrong. Then: **conditional pairwise concordance ≥ 0.93** against
`wordfreq`'s Romanian where it separates two words by ≥0.3 Zipf (Spearman ρ is printed but
no longer gated — it scores `wordfreq`'s own ties; spec §11.2); ~60 hand-written monotone
pairs; ≥95% coverage of DEX lemmas with `frequency ≥ 0.8` (a gap means the vocabulary
filter crept back); a printed top-100-by-`spread` report to read by eye; and
**idempotence** — re-running stages 2–5 on unchanged `source_counts` must produce a
byte-identical data file.

## Build order

M1 skeleton + tokenizer + Wikipedia only (proves the denominator in hours, before any
long job) → M2 CulturaX backbone → M3 rest of panel (≥5 sources, so the trim branch is
reachable) → M4 merge + first wheel → M5 lemma layer → M6 publish → M7 expose
`n_reliable` back to oțios (a change in *that* repo — the only coupling between the two).

## Opus designs, Sonnet builds

The work splits by model. **When you are Opus**, defer implementation and well-defined
future work to Sonnet by default:

- **Opus** writes specs, ADRs and briefs; makes and records decisions; and reviews Sonnet's
  results against the brief before they are accepted.
- **Sonnet** carries out a brief: code, spikes, measurements, fixture changes, and the
  activity and backlog entries for that work.
- **Opus does the work itself** when the task is a few lines, or when the design is still
  moving and the doing is part of the deciding.

**A brief** is one self-contained file next to the work it describes. It holds the question, 
the decisions already made, what to build, what to measure, what to deliver and where, 
what is out of scope, and when to stop and ask. 
Sonnet must not need to read the specs to follow it.

**Sonnet stops and asks** when a step would change a spec, an ADR, an invariant or a
dependency outside the brief's folder. It reports what it measured and what it assumed.

To hand over, Opus tells the owner that the brief is ready. The owner runs it in a Sonnet
session, or asks Opus to start a Sonnet agent on it.

## Repo conventions

- No `.db` or `.csv` in git, ever. `data/wrodfreq.db` and `data/checkpoints/` are
  gitignored; large artifacts ship as GitHub release assets, not in the wheel.
- Package name is `wrodfreq` on PyPI, styled `wROdfreq` in prose.
- `MINOR` version changes when the corpus panel changes; `build_info()` exposes the panel
  so a paper can cite an exact build.
- Round shipped Zipf values to 2 decimals — the third decimal is noise and costs real
  bytes across ~2M entries.
