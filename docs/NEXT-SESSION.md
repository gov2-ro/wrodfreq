# Next session — where things stand, what needs a decision

Written 2026-09-09; **rewritten 2026-10-05**, when the six-source 0.2.0 release candidate landed.
Resolved items move to "Resolved since this file was written" below rather than being
deleted, so the history of what's already been decided doesn't get lost. This file is a
consolidated pointer, not a new source of truth — everything here is covered in more detail
in `docs/activity-history.md`'s dated entries and `docs/BACKLOG.md`'s checklist. Read this
first to reorient, then follow the links.

## Where things stand (2026-10-05)

- **Version 0.2.0, a release candidate. Not published.** Six sources (`web`, `news`,
  `subs`, `social`, `wiki`, `eu` — 28.75B tokens), 6,064,995 words in `merged`, of which
  **6,041,542 ship** (rows with 3+ hyphens stay in the database only), 180,569 lemmas.
  `validate.py` 5/5 (concordance 0.948), ~230 tests, CI green on GitHub.
- **The lemma layer now ships** as `ro_lemma.msgpack.xz` (1.1 MB): `lemma_frequency()`
  and `lemma_detail()` read it, and fall back to the surface value for a non-lemma. This
  replaces the old "ship nothing until DEX answers" stance — it assumes DEX Online will say
  yes. If it says no, the file comes out (see open question 1).
- **Docs for readers, in Romanian:** `docs/method.md`, `docs/sources.md`.
- **Hyphenated rows:** the 3+-hyphen rows are dropped from the package; the 819k one-hyphen
  rows are being reviewed by hand — `docs/hyphen-labels.md`, BACKLOG ("Label the one-hyphen
  rows", "Re-validate the hyphen decisions"). 46 sampled rows still need the owner's policy.
- **M7 exists** (`~/devbox/otios/validate_with_wrodfreq.py`, wROdfreq as an editable
  dependency there) but its text still says five sources; re-run it against 0.2.0.
- **What stands between here and a release:** the DEX answer; a `LICENSE` file and the
  data-licence wording (Reddit and OpenSubtitles have no formal redistribution licence —
  only counts are shipped); `data/wrodfreq.db` is 4.09 GB against GitHub's 2 GiB per-asset
  cap, so it needs compressing or splitting; choose PyPI trusted publishing or manual upload.

## How work is organised now

`CLAUDE.md` gained an "Opus designs, Sonnet builds" section. In practice:

- `docs/briefs/` — one self-contained brief per task, written before the work. Sonnet runs
  it without needing the specs. B1, B2, B3, B5, B6 are done; **B4 is the only queued item**
  and is blocked until the crawl finishes.
- `docs/decisions/` — ADRs for calls that outlive their brief. ADR-001 (`zipf_frequency`
  tokenizes; extensions stay exact lookup) and ADR-002 (do not widen the token class;
  legacy Romanian variants are the real defect).

## 2026-10-02 in one paragraph

The restart would have destroyed 259,688 already-fetched records: the checkpoint carried no
`bytes` key because the process that wrote it was running code loaded before the file was
edited, and `setdefault("bytes", 0)` read absent as zero. Fixed by recovering state from
the append-log, which is the only thing that cannot disagree with itself. Then five briefs
landed: regression tests for that recovery; the foreign-diacritics measurement (do not
widen — but half of `web`'s "foreign" characters turn out to be mis-encoded Romanian, worth
up to **+0.58 Zipf on real words**, which is the known-wrong number to quote when a
re-ingest is next costed); `zipf_frequency` now tokenizes its argument and matches
`wordfreq`'s harmonic combination; the apostrophe residue migrated out of `source_counts`
with conservation proved; and the tokenizer's hyphen-split path stopped regenerating that
residue, which had been blocking the social ingest.

## Open questions — need your decision, not more building

### 1. DEX Online licensing — the request is drafted, not sent

`docs/dex-online-cerere.md` is a Romanian draft (needs a name and address). It asks about
(1) the derived per-lemma numbers, (2) redistributing the form→lemma map as a release
asset, (3) attribution. Until DEX answers: do not publish to PyPI with `ro_lemma.msgpack.xz`
inside, or publish without it (a one-line change in `build_package.py`), and do not attach
`inflected_forms.db` to a release. The extractor that rebuilds that file from DEX's own dump
is in `tools/dex_extractor/`, so "no" is survivable. When the answer arrives, write the terms
into `docs/method.md` §5 and `docs/sources.md`.

### 2. How much should oțios's scoring weight the new corroboration signal?

`validate_with_wrodfreq.py` (in `~/devbox/otios`) computes `n_reliable`/`n_attesting`/
`spread` per candidate and writes them to a CSV, but deliberately doesn't touch
`make_shortlist.py`'s scoring or `ui.db` — see that repo's `CLAUDE.md` (2026-09-09 entry)
for the reasoning. A real editorial decision about oțios's product, not an engineering
task: does "attested by N independent modern corpora" belong in the shortlist score at
all, and if so at what weight relative to the existing historical-attestation score?

## Open, small, none blocking

All are `- [ ]` entries at the end of `docs/BACKLOG.md`: the API hyphen-splitting fallback
(to be discussed later), the legacy-diacritic normalisation (rides the next re-ingest),
labelling the one-hyphen rows, and re-validating those decisions. The foreign-diacritics and
`zipf_frequency` tokenization questions this file used to carry are decided (ADR-002, ADR-001).

## Also noticed, not acted on

- **`build_info()['built']` is a UTC date stamped at build time**, and check 6 rebuilds
  the payload twice in one run — so a `validate.py` run that straddles UTC midnight will
  compare two different `built` values and fail idempotence spuriously. Tiny window, real
  flake. Nothing has hit it; noting it so it isn't debugged from scratch if it ever does.
- **DEX Online request still unsent** — see open question 1.
- **Unrelated pre-existing issue in oțios, not caused by this work**:
  `~/devbox/otios/docs/wordfreq-recipe.md` shows as deleted in that repo's working tree,
  uncommitted, last touched 2026-08-11. Left exactly as found — worth a look next time
  you're in that repo, in case it wasn't intentional.
- **Two loose notes at the top of `docs/BACKLOG.md`** — "maybe implement with another
  model and check results?" and "when done, integrate with the synonims project" — have
  no context attached and nobody has expanded them. Either flesh them out or drop them.

## Resolved since this file was written

- **`validate.py` check 2 — resolved 2026-09-18, now passing.** The long-running rho=0.863
  problem was **the metric, not the table**. Measured first: the combining-form compound
  hypothesis this file previously carried as "the bigger driver" is worth **+0.001** —
  removing all 32 curated forms from the comparison (an upper bound on any fix) moves
  0.8628 → 0.8637, and removing the 6,659 broader prefix candidates makes rho *worse*
  (0.8367), since they are mostly ordinary words. No tokenizer change was made.

  The real cause: wordfreq's Romanian carries only 356 distinct zipf values across the
  43,095 words we share, ~600 words per tied value in the 3.00–3.25 band, so rho was
  scoring *its* tie structure. Confirmed by two backwards-for-a-real-divergence results —
  restricting to wordfreq's more confident words makes rho *worse* (0.796 at zipf≥4.5),
  and `ours − wordfreq` is a flat median −0.15 / IQR 0.29 in every band. Pearson is 0.911,
  top-1000 overlap 801/1000. Check 2 now scores conditional pairwise concordance:
  **0.954** at ≥0.3 zipf separation (gate 0.93), computed exactly over 569,644,768 pairs.
  Spec §11.2 rewritten with the full reasoning.
- **Tokenizer emitted empty and hyphen-edged tokens; one shipped** — fixed 2026-09-18,
  found by reading check 5's spread report by eye. `zipf_frequency('')` had been returning
  2.34 instead of wordfreq's 0.0. `build/migrate_dashes.py` repaired all 43,137 malformed
  rows; nothing real moved (every function word unchanged to 2dp, `într` still 6.05).
  Rollback snapshot at `data/checkpoints/pre_dash_migration.db` (1.5 MiB) — safe to delete
  once you're satisfied.
- **Elision-splitting tokenizer fix** — landed 2026-09-17, verified correct (`într`
  3.45 → 6.05 vs wordfreq's 6.08). Did not move rho on its own, for the reason above.
- **`validate.py` check 4 (DEX coverage)** — fixed 2026-09-14. Recalibrated
  `frequency > 0.5` to `frequency >= 0.80` by measuring a threshold sweep. Also tried
  swapping the reference field to `dict_sources.in_current_dict` — measured worse (62.2%).
- **`README.md` was stale** — rewritten 2026-09-17 against the real current state.

## If you want a quick orientation command

```bash
cd ~/devbox/gov2/wrodfreq
python build/status.py          # point-in-time pipeline progress
python build/validate.py        # 5 checks + idempotence (~12 min; rebuilds merged/lemma_zipf/package)
python -m pytest tests/ -q      # ~230 tests, well under a second
```
