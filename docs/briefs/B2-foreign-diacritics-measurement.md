# Brief B2 — do non-Romanian diacritics need to be in the tokenizer's character class?

**Owner:** Sonnet. **Status:** DONE 2026-10-02 (9244e61) — decided in ADR-002. **Written:** 2026-10-02 by Opus.
**This is a measurement, not a change. Deliver numbers and a recommendation; Opus decides.**

## The question

The tokenizer's character class is Romanian-only:

```
[a-zăâîșț](?:[a-zăâîșț\-']*[a-zăâîșț])?
```

So a foreign word containing a non-Romanian diacritic splits mid-token: `Düsseldorf`
becomes `d` + `sseldorf`. Should the class be widened, and if so to what?

**Do not change the tokenizer.** Widening it is a design decision with a large blast
radius (see "Why this is expensive" below). Your job is to size the problem precisely
enough that the decision can be made on evidence.

## Background — so you do not need the specs

The project builds a Romanian word-frequency table on the Zipf scale (log10 of
occurrences per billion words, so 6.0–7.5 is a function word like `de` or `și`, ~3.0 is a
rare-but-real word, and lower is noise). One tokenizer module,
`wrodfreq/tokenizer.py`, is used by every ingester — that is deliberate, because two
sources tokenized differently cannot be merged and nothing would tell you.

Counts live in `data/wrodfreq.db` (SQLite, 3.7 GiB):
- `source_counts` — 38.4M rows, per-source `(source_id, word, occurrences, documents)`
  across five sources: `web` (23.8B tokens), `news` (2.2B), `subs` (2.0B), `wiki` (110M),
  `eu` (85M).
- `merged` — 6,050,327 rows, the merged per-word result.
- `sources` — per-source totals and derived Zipf floors.

Read the schema off the DB rather than trusting this summary.

## What is already known, so you do not redo it

Logged previously: single-letter values track `wordfreq`'s own Romanian within ~0.05, so
the damage is **spurious rare entries rather than corrupted common ones** — the opposite
of an earlier elision bug in severity. That is why this has stayed open rather than being
fixed in a hurry. Confirm or overturn that finding; do not assume it.

## Why this is expensive — the context for your recommendation

Changing the character class invalidates every number in `source_counts`, because the
token stream changes. There is no way to patch the table forward from a tokenizer change
without either re-ingesting (the `web` source alone took ~days) or writing a migration
that can prove it touched only affected rows. Two such migrations exist as precedent —
`build/migrate_elisions.py` and `build/migrate_dashes.py` — read them to see what a
defensible migration looks like and what it cost.

So a recommendation to widen the class must be justified by more than tidiness.

## What to measure

1. **How much is actually there.** Count the fragment-shaped entries in `merged` and in
   each source: tokens that look like the tail or head of a split foreign word. Report
   volume, and their Zipf distribution — specifically how much sits above the per-source
   floor (so it is being treated as a real word) versus below.
2. **What it displaces.** The splitting inflates short fragments. Quantify the effect on
   the *affected* single letters and short strings: compare their current Zipf against a
   recount with foreign-diacritic words excluded. Does any value move by more than 0.01
   at 2 decimal places (the shipped rounding)?
3. **Which characters matter.** Rank the non-Romanian characters by how many distinct
   words and how many occurrences they affect. `ü ö ä ë é è ç ñ å ø` and friends — find
   out empirically rather than from a guessed list. Distinguish genuinely foreign proper
   nouns (`Düsseldorf`, `Nürnberg`) from Romanian text written with wrong-keyboard
   substitutions, if the data lets you tell them apart.
4. **The false-positive risk of widening.** If the class were widened to include Latin-1
   letters, what *else* starts tokenizing as a word? Estimate the new junk admitted —
   this is the cost side, and it is the part a naive fix ignores.
5. **Whether `wordfreq` itself solves this.** Check what `wordfreq`'s Romanian does with
   the same inputs. If it also fragments them, widening would *reduce* comparability with
   the reference the project validates against. State which way it cuts.

## Deliver

- A written findings section appended to `docs/BACKLOG.md`, replacing the existing
  `- [ ]` entry on this topic (search for the `Düsseldorf` example) with the measured
  numbers, keeping it a `- [ ]` item since the decision is still open.
- An entry in `docs/activity-history.md` under `## 2026-10-02 — <short title>`, in the
  prose style of the existing entries (read two first).
- Any measurement scripts in `build/` only if they are worth keeping and are idempotent;
  otherwise keep them in your scratch dir and do not commit them.
- A report back: the five answers above, and **one of three recommendations** — widen
  (with the exact proposed class), do not widen, or widen only for a named subset — each
  with the number that drives it.

## Out of scope

- Editing `wrodfreq/tokenizer.py` or any ingester. No behaviour changes at all.
- Re-ingesting anything.
- `build/fetch_social.py` and the crawl now running — do not touch either.
- The separate open question about `zipf_frequency` not tokenizing its argument (Brief B3).

## Stop and ask

- If measuring requires a schema change or a write to `data/wrodfreq.db`. Reads only;
  the crawl is using the disk and the DB is the project's only copy.
- If the answer turns out to depend on a spec decision (what counts as a Romanian word at
  all) rather than on data.
