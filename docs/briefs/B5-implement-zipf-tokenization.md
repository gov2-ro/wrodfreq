# Brief B5 — implement ADR-001: tokenize the argument, migrate the apostrophe residue

**Owner:** Sonnet. **Status:** DONE 2026-10-02 (66b5f7c). **Written:** 2026-10-02 by Opus.
**Read `docs/decisions/ADR-001-zipf-argument-tokenization.md` first — it is the decision
this brief implements, and its reasoning answers most "why not instead…" questions.**

## The task, in two independent halves

Either can land without the other. Do the migration first: it is the one that touches data.

### Half 1 — migrate the 225 apostrophe-edged rows out

`merged` holds 225 keys that begin or end with an apostrophe (`'a`, `'aci`, `'aswad`,
`acu'`). **All 225 are unreproducible by the current tokenizer**: its pattern requires a
token to start and end with a letter from `[a-zăâîșț]`, so it cannot emit them. They are
residue from before an earlier tokenizer fix — the same defect class as the hyphen-edged
rows, of which `merged` now holds zero because `build/migrate_dashes.py` cleaned them. It
handled `-`-edged tokens and missed `'`-edged ones.

**Read `build/migrate_dashes.py` before writing anything.** It is the precedent: what a
defensible migration looks like here, how it snapshots for rollback, and how it proves it
touched only the rows it meant to. Follow its shape as `build/migrate_apostrophes.py`.

Requirements:
- Fix `source_counts` (the stored truth), then re-derive `merged`. Never edit `merged`
  directly — it is a derived view of per-source counts, and the merge must stay
  re-derivable.
- Re-split each residue key the way the tokenizer would now: `tokenize("'aci")` gives
  `['aci']`, so its occurrences and documents fold into the existing `aci` row for that
  same source if one exists, or create it. Do not simply delete rows — that silently drops
  real occurrences. Prove conservation: total occurrences per source must be unchanged by
  the migration, and assert it.
- Snapshot to `data/checkpoints/` for rollback, as `migrate_dashes.py` does.
- Idempotent: running it twice must be a no-op the second time, and say so.
- Report before/after counts and confirm no function word moved at 2 decimal places.

### Half 2 — implement the API change

Per ADR-001 decisions 1, 3, 4, 5. In short: `zipf_frequency` and `word_frequency`
tokenize via `wrodfreq.tokenizer.tokenize`; zero tokens → `minimum`; one token → its
value; two or more → harmonic `1/f = Σ 1/f_i` with any unknown token giving `minimum`;
`word_frequency` combines in the linear domain, never through a rounded Zipf; a string
with letters or digits the tokenizer did not consume → `minimum`; numerals → `minimum`;
**no exact-key-first path** (ADR-001 decision 2 explains why it is redundant);
`frequency_detail`, `by_source` and `lemma_frequency` keep exact single-row lookup and keep
returning `None`.

`tests/test_api.py` already contains 13 `test_b3_*` cases encoding this, currently marked
skip. Un-skip them, make them pass, and fix any that encode the rejected exact-key-first
behaviour rather than the ADR — the ADR wins, and say which ones you changed and why.

Document the asymmetry (decision 5) and the two divergences (decisions 3 and 4) in the API
reference where the public functions are described, so a user hits the explanation before
the surprise.

## Background you may need

Zipf scale is log10 occurrences per billion words: function words like `de` and `și` land
6.0–7.5, ~3.0 is rare-but-real, below the per-source floor is noise. Shipped values are
rounded to **2 decimals**. The reference `wordfreq` is **not** in this repo's `.venv`; it
lives in `~/devbox/otios/.venv/bin/python3`, which is how `build/validate.py` reaches it
(it skips rather than fails when absent). Use it as the oracle, via subprocess, the way
`validate.py` does.

## Deliver

- `build/migrate_apostrophes.py`, run, with its before/after numbers in the report.
- The API change in `wrodfreq/`, with the 13 tests un-skipped and passing.
- `.venv/bin/python -m pytest tests/ -q` green, with the count.
- Entries in `docs/activity-history.md` and the matching `- [ ]` items updated in
  `docs/BACKLOG.md`, in the prose style already there.
- A report: the migration's conservation proof, which tests you had to change against B3's
  original proposal, and anything the ADR got wrong.

## Out of scope

- `build/fetch_social.py`, `build/run_social_fetch.sh`, `data/checkpoints/social_fetch*`,
  `data/raw/` — **a multi-hour crawl is running against those.** Check with
  `pgrep -f 'build/fetch_social.py'`; leave them alone whether or not it is still going.
- Widening the tokenizer's character class for foreign diacritics — that is Brief B2's
  open decision. ADR-001 decision 3 is deliberately written to hold either way.
- Re-running `build/validate.py` end to end (10–12 min, rebuilds artifacts). Run it only
  if your migration changes `merged`, and then say what it reported.
- Any change to `MINOR`: the corpus panel has not changed.

## Stop and ask

- If conservation of occurrences cannot be proved for the migration. That means the
  residue is not what the ADR says it is, and the decision needs revisiting.
- If making the 13 tests pass requires changing the tokenizer rather than the API layer.
- If `merged` turns out to hold other keys the tokenizer cannot reproduce, beyond the
  apostrophe class. Report the classes you find; do not expand the migration to cover them
  without asking.
