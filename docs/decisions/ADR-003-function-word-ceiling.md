# ADR-003 — The function-word check uses the band Zipf 6.0–8.0

**Status:** accepted, 2026-11-06. **Decided by:** the owner, on Opus's recommendation.
**Amends:** spec §11.1 and the M1 "done when" line in spec §13. The earlier band was 6.0–7.5.

## Context

Validation check 1 requires `de`, `și`, `la`, `un` and `cu` to fall in a fixed Zipf band.
The spec and the documents gave the band as 6.0–7.5. `build/validate.py` uses a ceiling of
8.0 (`ZIPF_HIGH_ADJUSTED`). The documents and the code disagreed.

The correct value of `de` is above 7.5:

- `merged` in build 0.2.0 has `de` at 7.68.
- `wordfreq`'s own Romanian list has `de` at 7.72.

With a ceiling of 7.5, check 1 fails on a correct table.

## Decision

The band is 6.0–8.0, for `merged` and for each single source. The documents now state the
band that the code applies. The check itself does not change.

## Consequences

- The check still catches the denominator bug (spec §3.2). That bug makes a function word
  land low or go missing.
- The check is less sensitive to an inflated value between 7.5 and 8.0. Check 2, the
  concordance with `wordfreq`, stays as a separate control. It compares word order, so it
  does not measure this shift directly.
- Dated records keep the old band: `docs/BACKLOG.md`, `docs/activity-history.md` and
  `docs/briefs/`. They describe what was true at that time.
