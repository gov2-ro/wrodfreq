# Where the data lives — hosting and a light database

Written 2026-10-05. **A proposal with measurements, not a decision.** The open choices are
at the end. Context: `data/wrodfreq.db` cannot go on a GitHub release as it is, and the
spec (§10.2) promised "the full `wrodfreq.db` as a GitHub release asset".

## What was measured (0.2.0 build, 2026-10-05)

| artifact | size |
|---|---|
| `data/wrodfreq.db` | 4,091,002,880 bytes (4.09 GB, 3.81 GiB) |
| the same, `zstd -6` | **1.25 GiB** — under GitHub's 2 GiB per-asset cap |
| prototype light database, after `VACUUM` | 549.6 MiB |
| the same, `zstd -19` | **137.2 MiB** |

Where the 4.09 GB goes (SQLite `dbstat`, MiB): `source_counts` 1,130 · `source_zipf` 1,097 ·
`idx_sc_word` 1,001 · `merged` 332 · `lemma_zipf` 6. So about 2.1 GiB is **derived or
recreatable**: `source_zipf` is rebuilt by `build/compute_zipf.py` from `source_counts` and
`sources`, and the index is recreated on demand.

Row counts: `source_counts` 40,062,633 rows, of which **22,129,277 are words seen exactly
once** and only **8,332,781 clear their source's floor** (31,729,852 are below it);
`merged` 6,064,995; `lemma_zipf` 180,569.

## The light database

Prototype built in scratch (not a build stage yet):

- **Keep:** `sources` (floors, periods, notes), `merged` (`zipf`, `n_reliable`,
  `n_attesting`, `n_sources`, `zipf_min/max`, `spread`), per-source Zipf **only for the 8.33M
  reliable rows**, `lemma_zipf`.
- **Drop:** every below-floor count, raw `occurrences` and `documents`, the derived
  unreliable `source_zipf` rows, the index, and (to match the wheel) the 3+-hyphen rows.
- **Lost by the light version, kept by the full one:** all of `source_counts` — the only part
  nobody can regenerate without a multi-day ingest.

Is it worth publishing when the wheel already carries the core data? Modestly: it is 137 MiB,
and it adds SQL access, `zipf_min/max` and no `pip install`. It is a convenience, not a new
asset.

## Where to host

- **Full counts — a dedicated home.** It is a research artifact people will cite.
  - *Zenodo*: a DOI per version, which matches "name the exact build" in a paper
    (`build_info()`). Recommended.
  - *Hugging Face Datasets*: reach in the NLP community and a table viewer; works best as
    Parquet. A mirror of the Zenodo release is fine.
  - Neither service's current size limits were re-checked; confirm before uploading.
- **GitHub release:** the light database, plus the compressed full file too, since 1.25 GiB
  fits. The heavy copy still belongs on Zenodo.

## Before any database is published

- **`is_dex` and the lemma table leak the DEX headword list.** `merged.is_dex` marks which
  words are in DEX, and `lemma_zipf` is roughly its headword list with numbers; the
  `build_package.py` docstring flagged this. Until DEX Online answers
  (`docs/dex-online-cerere.md`), leave `is_dex` out of any public database. The lemma table is
  the same gamble the wheel already takes.
- **The licence wording.** `social` (Reddit) and `subs` (OpenSubtitles) have no formal
  redistribution licence; only aggregate counts are shipped, never text or author names. A
  public, indexed host makes the wording in the dataset card matter more.
- **A `LICENSE` file** and the data-licence statement do not exist yet.

## How `wordfreq` did it

- It publishes **only the final lists**: its wheel is 54.2 MiB for all its languages (PyPI,
  `wordfreq` 3.1.1, checked 2026-10-05). Raw counts are never distributed.
- The pipeline lives in a **separate repo** (exquisite-corpus). Publishing only aggregated
  frequencies sidesteps the corpus licences.
- From memory (not re-verified): each language gets a "small" list (roughly one per million
  and up) and some a larger one; Romanian only has "small" — the gap wROdfreq fills. Its data
  files group words by rounded frequency bin instead of storing a number per word. Ours stores
  a quantized integer per word (surface file 27.3 MiB); a binned layout could shrink it, which
  is optional.
- We ship 6.04M words to its tens of thousands, so one language costs us a 40 MB wheel.

## Proposed build stage (not written)

`build/export_release.py` would produce: the light database; a clean copy of the full counts
without the derived tables and index; SHA-256 checksums; a short dataset card (sources, floors,
`build_info()`, licences). It would omit `is_dex` while the DEX question is open and record the
exact build in the file names.

## Decisions for the owner

1. Full counts on **Zenodo** (DOI) or **Hugging Face** (reach), or both?
2. Publish the light database on GitHub, yes or no?
3. Keep `documents` in the full counts (it is the independence signal) or drop it from
   everything public?
4. Include the lemma table in public databases before DEX answers, or hold it?
