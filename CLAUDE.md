# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state: spec only, no code yet

The repo holds four documents and nothing else. Everything below the "Commands" heading
describes work that is **planned, not present** — `pyproject.toml`, `wrodfreq/`, `build/`
and `tests/` all still have to be created. Before writing any code, read
`docs/wrodfreq-spec.md` end to end; it is the authoritative design and this file only
summarises the parts that are easy to get wrong.

- `docs/wrodfreq-spec.md` — the build spec (schema §7.1, merge §8, lemma layer §9, API
  §10, validation §11, layout §12, milestones §13, traps §14).
- `docs/wordfreq-recipe.md` — why the parent project (oțios) rejected this method for
  *its* question, and the measurements behind that. Read §§1–8 before arguing with any
  design decision here; most objections are already answered there.
- `docs/BACKLOG.md` — open bugs/debt/enhancements, `- [ ]` entries with enough context to act on.
- `docs/activity-history.md` — chronological log, entries under `## YYYY-MM-DD — Short Title`.

Keep both of those files current as work lands.

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
Vendor `extract_inflected_forms.py` for provenance, and resolve the DEX Online licence
question before redistributing it. **Do not** reuse oțios's `corpus_frequencies.db` counts
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
- **Never weight sources by size.** CulturaX is ~200× the next source, so size-weighting
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

## Commands (to be created — none of this exists yet)

Follow oțios's "one script per pipeline stage" convention; every stage resumable and
idempotent. Planned pipeline, in order:

```bash
python build/ingest_wiki.py       # then news, subs, eu, web — writes source_counts + sources
python build/compute_zipf.py      # per-source zipf + reliable flag
python build/merge.py             # trimmed mean → merged
python build/build_lemma_layer.py # needs inflected_forms.db; optional
python build/build_package.py     # → wrodfreq/data/ro_surface.msgpack.xz
python build/validate.py          # must run in CI and fail the build
```

Ingesters are multi-hour to multi-day jobs that *will* be killed — checkpoint from the
first commit, and run them under the status/health-check/audit triad.

## Validation is the gate

`validate.py` must fail the build. The one check that catches the worst bug in the spec:
**function words land in Zipf 6.0–7.5** (`de`, `și`, `la`, `un`, `cu`) — if they do not,
the denominator is wrong. Then: Spearman ρ > 0.9 against `wordfreq`'s Romanian over its
covered range (a lower value is a tokenizer/normalisation divergence, not a discovery);
~50 hand-written monotone pairs; ≥95% coverage of DEX lemmas with `frequency > 0.5` (a
gap means the vocabulary filter crept back); a printed top-100-by-`spread` report to read
by eye; and **idempotence** — re-running stages 2–5 on unchanged `source_counts` must
produce a byte-identical data file.

## Build order

M1 skeleton + tokenizer + Wikipedia only (proves the denominator in hours, before any
long job) → M2 CulturaX backbone → M3 rest of panel (≥5 sources, so the trim branch is
reachable) → M4 merge + first wheel → M5 lemma layer → M6 publish → M7 expose
`n_reliable` back to oțios (a change in *that* repo — the only coupling between the two).

## Repo conventions

- No `.db` or `.csv` in git, ever. `data/wrodfreq.db` and `data/checkpoints/` are
  gitignored; large artifacts ship as GitHub release assets, not in the wheel.
- Package name is `wrodfreq` on PyPI, styled `wROdfreq` in prose.
- `MINOR` version changes when the corpus panel changes; `build_info()` exposes the panel
  so a paper can cite an exact build.
- Round shipped Zipf values to 2 decimals — the third decimal is noise and costs real
  bytes across ~2M entries.
