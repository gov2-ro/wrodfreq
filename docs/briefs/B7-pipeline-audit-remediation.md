# Brief B7 — repair pipeline recovery, validation, and the contaminated social build

**Status:** ready for implementation; no implementation or data repair performed by
this brief. **Written:** 2026-11-09. **Owner:** the implementing agent assigned this
brief. **Audit baseline:** commit `81635a640cc4d6a72e0f2f234ab93b10b4bc65ca`.

Read this file first. It is a complete handoff: preserve the evidence, fix the code,
then produce a repaired candidate database and distribution for review. Finishing code
changes alone does not finish the assignment.

## 1. Objective and delivery boundary

Produce a pipeline that can be interrupted, resumed, rebuilt, packaged, and checked
without losing or duplicating counts, mixing generations, or reporting a successful
release check for a broken distribution. Rebuild the contaminated social source from
the retained local dumps and regenerate all derived candidate outputs.

Work on code and **staged copies of data**. Preserve `data/wrodfreq.db`, the existing
`wrodfreq/data/` blobs, `release/ro_surface.tsv.gz`, legacy checkpoints, and raw dumps as
the baseline until the candidate is reviewed. Deliver exact promotion and rollback
commands, but do not replace those baseline outputs, tag, publish, upload, send the DEX
request, or change the public review copy as part of this brief. This is a staging
boundary, not a reason to stop before building and verifying the candidate.

The brief covers changes to `build/`, `wrodfreq/db.py`, `wrodfreq/_surface.py`, additive
`build_info()` metadata, tests, packaging configuration, workflows, and the relevant
docs/ADRs. These are in scope even where older briefs restricted work to one file.
Use small reviewable changes in the order below. Do not spawn other agents unless the
owner explicitly asks for delegation.

### Preserve these scientific and API decisions

- Keep the tokenizer, normalization, language filter, bot filter, and markdown cleaning
  unchanged. In particular, do not add the deferred legacy-letter fixes to social alone.
- Keep open vocabulary, all short alphabetic words, and numeral exclusion.
- Keep `MIN_OCC_PER_SOURCE = 5`, the contemporary/completed source filter, unweighted
  cross-source means, trimming only at five or more reliable sources, and `MAX_HYPHENS=2`.
- Keep raw surface counts and derived lemma data separate. Keep ambiguity shares and
  the optional lemma fallback. Only the panel used to compare lemma totals changes.
- Keep public lookup signatures, harmonic combination, rounding, and the `0.0`/`None`
  distinction. `build_info()` may gain metadata; existing keys must remain.
- No imports from oțios at runtime, and no new runtime dependency. Build-only tools may
  continue to read the local reference interpreter and DEX map.
- Keep `.db`, `.csv`, raw corpora, blobs, and the review TSV out of Git. Do not change
  the DEX distribution policy, choose a public data host, or publish derived artifacts.

## 2. Evidence to reproduce before editing

The audit's 231 tests passed. The four non-mutating quality checks also passed against
the real database: function words, all 59 monotone pairs, concordance **0.948**, and DEX
coverage **97.7%**. None detected the two current-data defects below.

| Source | `sources.total_tokens` | `SUM(source_counts.occurrences)` | Legacy checkpoint tokens |
|---|---:|---:|---:|
| eu | 84,722,606 | 84,722,606 | 84,415,957 |
| news | 2,213,978,077 | 2,213,978,077 | 2,186,239,880 |
| social | 536,523,553 | **566,678,860** | 536,523,553 |
| subs | 2,013,153,404 | 2,013,153,404 | 1,936,897,280 |
| web | 23,795,634,236 | 23,795,634,236 | 23,516,444,566 |
| wiki | 110,528,828 | 110,528,828 | 109,558,215 |

The social excess is **30,155,307 tokens**, exactly
`data/checkpoints/social_checkpoint.test.json["tokens_done"]` (686,807 documents).
The full checkpoint records 13,717,928 documents. Additive test/full writes reproduce
the contamination mechanism; the exact excess strongly supports that historical cause.
Do not claim a word-by-word historical attribution was proved by the audit.

There are **2,382** database lemmas with `family_ratio < 0.999999`.
`detoxica` has approximately `0.0329449722`, shipped as `0.03`; the field is defined as
an undivided/disambiguated comparison and should be at least one.

Run the portable diagnostic, which uses temporary files and no private data/network:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python docs/briefs/B7-audit-reproduce.py
```

**The diagnostic asserts the old broken behavior.** It should pass at the audit
baseline and cease to pass as repairs land. It is not a regression suite to preserve
unchanged. Convert its cases into tests of correct behavior in the normal test suite.

| ID | Finding | Main code to inspect | Required outcome |
|---|---|---|---|
| A01 | Social test/full accumulation | `ingest_social.main/flush`; all ingester CLIs | Experiments cannot pollute a real generation; social rebuilt |
| A02 | Quality checks precede mutating rebuild; files unverified | `validate.main/check_idempotence` | Read-only verification of one exact candidate |
| A03 | DB commit and JSON cursor are separate | All six ingesters' `flush/save_checkpoint` | Counts, totals, and authoritative cursor commit together |
| A04 | Old checkpoints undo migrated totals | `ingest_eu.main`, web/news final flushes | Completed migrated sources resume as no-ops or refuse safely |
| A05 | Bounded fetch overwrites earlier records; ingest skips one | `fetch_social.fetch/compress`, social record loop | Capped/resumed output equals uninterrupted output |
| A06 | Lemma ratio compares different source panels | `build_lemma_layer.run` | Matched-panel ratio at least one |
| A07 | Stage 2 retains deleted raw words | `compute_zipf.compute_source` | Exact transactional replacement from current counts |
| A08 | Clean release CI accepts data-less wheels | `ci.yml`, `release-check.yml` | Real release artifact must contain and exercise required data |
| A09 | Date-dependent bytes; mixed artifact generations accepted | `build_package`, `_surface` | Stable manifest identity and complete candidate bundles |

## 3. Execution order

1. Preserve evidence and add failing regressions; introduce read-only integrity checks.
2. Repair authoritative ingest state and isolate experimental runs across all six sources.
3. Repair social acquisition/finalization and bounded ingest continuation.
4. Repair exact derived rebuilds and the matched-panel lemma ratio.
5. Introduce deterministic manifests, artifact identity, and candidate staging.
6. Finish read-only validation, independent rebuild verification, and release checks.
7. Rebuild social and all derived outputs in staging; compare and document the candidate.

Implement and verify each step before moving to its dependent step. Unit fixtures need
no corpus access. Full data repair waits until the generating code and its tests pass.

## 4. Phase 0 — preserve the baseline and make failures visible

### Preflight

- Read current `AGENTS.md`/`CLAUDE.md`; check `git status`. At the audit baseline an
  untracked `AGENTS.md` existed; do not accidentally stage, overwrite, or remove it.
- Check for active ingest, fetch, restart-loop, migration, and build processes before
  changing scripts used by them. Do not kill a job or edit code beneath a live restart
  loop. Independent tests/docs can continue while an actual job is finishing.
- Record the current commit, Python/dependency versions, file sizes and hashes, source
  metadata, raw-file inventory, and legacy checkpoint hashes in an ignored audit folder.
  Detect changes since the table above; fresh measurements override this snapshot.
- Use `sqlite3.connect("file:...?mode=ro", uri=True)` for baseline inspection. Existing
  `wrodfreq.db.connect()` applies DDL and is not a read-only inspection helper.
- Plan disk use before copying. At audit time only about **20 GiB** was free; the DB is
  about **4.09 GB**. Budget candidate DB, one verification copy, journals/spill, optional
  social-only staging DB, and compressed distributions. Avoid multiple full copies per
  phase and uncompressed corpus exports. Use SQLite backup for a consistent copy.

### First code/test change

Add a read-only integrity checker, reused by the eventual validator, which streams
rows and reports at least:

- Per-source `SUM(occurrences) == total_tokens`; non-negative totals; positive stored
  occurrence counts; `0 <= documents <= occurrences`.
- Valid token keys and source references; reliable/Zipf/null consistency; no derived
  rows whose raw count row is absent; finite numeric values.
- `0 < n_reliable <= n_attesting <= n_sources`, correct source panel, and non-negative
  spread consistent with reliable source estimates.
- Lemma `family_ratio >= 1` within floating-point tolerance and valid optional fields.

Do not sum per-word `documents` and compare it with total documents: those units do not
add that way. Do not assert exact distinct-document semantics for historically migrated
rows; the documented migration approximation remains.

Add the test/full contamination and the stale-validation regressions first. The new
integrity command must fail on the current real DB, name social, and leave its bytes
unchanged. Do not run the current `validate.py` CLI against the baseline: it rebuilds
tables despite presenting itself as a checker.

## 5. Phase 1 — transactional ingest progress for every source

### Authoritative state

Store the authoritative cursor **inside the same SQLite DB as the counts**. Introduce
a small versioned `ingest_state` table and a shared build helper, rather than six
slightly different recovery protocols. Keep source-specific reading/tokenization loops.
The state must distinguish:

- Source and generation identity, monotonically increasing batch sequence, and state
  (`in_progress`, `completed`, or sealed legacy counts).
- Cursor schema/version and source-specific cursor JSON (row, shard, line, or file).
- Tokenizer and source-policy fingerprint; a raw input manifest/fingerprint; known
  upstream snapshot/revision information. Record unavailable legacy provenance as
  unknown, not as a newly verified historical fact.
- A DB identity for any exported/legacy sidecar association. JSON exports, if retained
  for status/debugging, are derived and never decide replay independently of SQLite.

One batch transaction must: check the expected prior generation/sequence, apply count
and document deltas, update source totals/status, update the authoritative cursor, and
commit. Clear in-memory buffers only after a successful commit. A failed or rolled-back
batch must remain replayable. A second writer with a stale sequence must be rejected
before applying its deltas; a short SQLite transaction must not become a job-long lock.

### CLI behavior and compatibility

- A normal fresh run refuses an existing nonempty source generation before downloading
  or writing. It must not silently add a fresh prefix to old counts. Use an empty staged
  DB for a rebuild; no automatic reset/delete flag is needed for this work.
- `--resume` reads SQLite state. Verify input/policy identity before accepting its cursor.
  A changed or missing input manifest fails with an actionable diagnostic, never a reset.
- `--test` without `--db` uses a clearly printed unique experiment DB under ignored
  `data/experiments/`, with independent state. An explicitly supplied test DB must be an
  isolated target; reject the canonical production path and an existing nonempty source
  generation. A separate JSON filename alone is insufficient isolation.
- A bounded run checkpoints the last consumed record only. Preserve documented limit
  meanings unless required by A05; explicitly document per-session versus absolute
  limits instead of silently standardizing every CLI in this repair.
- The social absolute per-file limit must checkpoint N after consuming N records, never
  N+1. `--limit 1`, then `--resume --limit 2`, then unbounded resume must equal a full run.
- A completed generation with matching inputs is an idempotent no-op, including source
  totals and metadata. Check before an `ensure_source_row()` call can overwrite notes.

### Legacy checkpoints and count migrations

Do not automatically trust/import a legacy JSON cursor for a nonempty database. Totals
matching by themselves cannot prove that the cursor belongs to those particular counts.

For the five existing completed, count-conserving sources, provide an explicit adoption
step that seals their **database** totals and records legacy provenance. Do not replay
their obsolete cursors, lower their migrated denominators, or require a multi-day ingest
just to adopt them. A sealed completed source can be inspected and resumed as a no-op;
adding/replacing inputs requires a deliberate new generation. Unfinished legacy data
must refuse unsafe resume and explain how to stage a supported adoption/rebuild.

When an existing count migration really changes counts, bump/invalidate source generation
and downstream provenance in the same transaction. An already-applied migration remains
a no-op; old migrations must not reapply their math or export stale progress on startup.
Preserve legacy JSON files as evidence. Completed legacy JSON cannot override DB totals.

### Required tests

- Parameterize the shared transaction tests across all six ingesters; assert actual
  adapters use the helper, not merely that the helper works in isolation.
- Interrupt before commit, during count application, immediately after commit, and before
  optional JSON export; reopen and resume. Counts, totals, and cursor must match a clean run.
- Include deterministic subprocess kill/reopen tests at transaction boundaries, using
  local fake readers and synchronization rather than timing sleeps or network access.
- Test two stale writers, corrupt/missing legacy JSON, wrong DB, wrong generation,
  changed input/policy fingerprint, fresh-run refusal, and experiment DB isolation.
- Reconstruct the EU old checkpoint total 84,415,957 against migrated total 84,722,606;
  completed resume must preserve the latter. Cover analogous finalization in web/news.

## 6. Phase 2 — social acquisition and finalization

This phase incorporates **all recovery gaps in B4**. B4's crawl-running precondition
still matters for an actual live process. Its old open compaction choice is resolved
for this brief: **do not compact an unfinished crawl**. Keep its durable append log
until history is exhausted. A capped exploratory fetch returns incomplete status and
can be continued without losing the earlier prefix. Do not invent incremental compaction.

Implement a recoverable finalization state machine:

1. Flush/fsync the append log and save a cursor consistent with its durable byte boundary.
2. Record history exhaustion separately from the existence of a finalized compressed file.
3. Stream compression into a temporary `.zst` (do not `read_bytes()` the entire log).
4. Close/fsync, verify record count/content identity, and atomically replace the final file.
5. Record finalized artifact identity. Only then may the redundant append log be removed.

On restart, a saved `done`/exhausted cursor with missing or incomplete compressed output
must finish or repair finalization, not skip the dataset forever. If only a valid final
artifact remains, verify and keep it. The existing completed archives should require no
refetch. Wrong/missing local material must produce an explicit recoverability error.

Preserve B4's rules: missing/incoherent bytes are unknown; recover/report count and cursor
from valid durable data; explicit zero bytes with nonzero count and bytes beyond EOF are
not safe truncation instructions. Distinguish a torn final record from internal corruption;
do not silently discard valid later records. Diagnose nonnumeric/missing timestamps and
mid-line boundaries. Remove genuinely dead compaction code if still present.

Tests must compare decoded **records/content**, not only counts. Cover cap/continue,
cap/continue a second time, shutdown, failed compression, and process death before/after
each finalization transition. Cover B4's malformed-state cases. Keep all tests offline.
Do not broaden this phase into a new acquisition service or change the chosen source panel.

## 7. Phase 3 — exact derived tables and a meaningful lemma ratio

### Stage 2 replacement

`compute_source()` must replace exactly the current source's derived rows within a
transaction. An explicit `DELETE ... WHERE source_id=?` plus streamed inserts in one
transaction is acceptable. On failure, the old complete set survives. Avoid `fetchall()`
plus another full list for the 30M-word web vocabulary; use bounded batches.

Handle empty sources intentionally: clear their old derived rows and unset the floor
when there is no denominator; inconsistent positive counts with zero tokens are an
error. Validate an unknown `--source` instead of silently succeeding. Incomplete/rejected
sources must not leak into merged output. Deleting the last occurrence of a word and
rebuilding stages 2–3 must remove it from both derived tables. Preserve other sources.

Record each derived stage's input generations/logical hashes so packaging can reject
stale or mixed stages. Rebuild only after checking raw conservation. Current counts are
the truth; a previous derived table is never evidence that a removed word still exists.

### Matched-panel `family_ratio`

For lemma L, let R be exactly the completed contemporary sources whose **disambiguated**
count for L clears the existing reliability threshold. Compute its unchanged lemma Zipf
from disambiguated estimates over R. Compute the loose/undivided comparison over **that
same R**, rather than letting extra loose-only reliable sources enter the numerator:

```text
z_disambig = merge_zipf([zipf(disambig[L,s], tokens[s]) for s in R])
z_loose   = merge_zipf([zipf(loose[L,s], tokens[s])    for s in R])
family_ratio = 10 ** (z_loose - z_disambig)
```

Keep per-source values keyed by source identity. Loose count is at least disambiguated
count in each paired source. The same monotone merge over matched panels therefore gives
ratio at least one. Do not fix this by clamping substantial bad ratios to one, summing raw
counts across corpora, or changing lemma Zipf eligibility. Only tiny floating-point error
within a documented tolerance may be normalized to one.

Add a full `build_lemma_layer.run()` fixture with 10 shared occurrences in a 1,000-token
source and 6 in a billion-token source, split equally between two claimants. Disambiguated
counts are 5 and 3; the old independent-panel ratio is approximately 0.001549. The fixed
matched-panel ratio is **2**, and lemma Zipf is unchanged. Include unambiguous ratio=1,
unequal priors, and five/six-source trimmed cases. Pure rollup tests alone missed this bug.

Update the definition in the spec/method and record a short ADR explaining the matched
panel. This corrects the published meaning; it does not introduce a new lemma estimator.

## 8. Phase 4 — deterministic build manifest and coherent output bundles

Introduce a small canonical build manifest. It must identify:

- Package/data-format version, code revision, fixed build date, tokenizer/policy hashes.
  Capture relevant working-tree content hashes as well when code is uncommitted; a HEAD
  hash alone must not falsely attribute a dirty candidate to committed source.
- Eligible source IDs, token/doc totals, generation identities, input snapshots/revisions
  where known, logical count/stage hashes, and honest unknown legacy provenance.
- Merge constants, quantization/filter choices, and the DEX map content hash when used.
- Whether the build is synthetic or real; lemma included/omitted explicitly.

Create the fixed date once as an explicit manifest input. Rebuilding with that manifest
on another day must give identical bytes; do not call the wall clock inside payload
generation. Derive a content build ID from canonical inputs. A copy's filesystem path,
SQLite page layout, status-command invocation, or changing informational execution times
must not change logical identity. Keep volatile logs outside deterministic files.

The result manifest records SHA-256 of each emitted file. Avoid circular hashing: the
input-derived ID is embedded in payload headers; output digests live in the final result
manifest and are not included in the hash that produced that ID.

Use a new data format version for the identity-bearing payloads. Every surface,
by-source, and optional lemma header must carry the same build ID and expected source
panel/scale. Validate format, array lengths, source ordering, counts, and word-order
identity. Store/hash the ordered word list identity so equal row counts cannot conceal
different vocabularies. Extend `build_info()` with the ID and relevant provenance.

Do not silently accept unverifiable old positional files with a new file. Version 1 may
be rejected with a clear rebuild message; preserve the baseline blobs and document that
the working checkout needs freshly built format-version data. Public API behavior stays
the same. An installed old wheel remains self-contained and unaffected.

Write artifacts into a **new generation directory**, then write the completed manifest
last. No consumer can select an unfinished generation. Individual writes use temporary
files plus atomic replacement. Do not claim that three sequential file renames constitute
an atomic bundle. Packaging must take an explicitly completed generation (or a single
atomic generation pointer), not whatever blobs happen to occupy default paths.

Keep core/by-source lazy loading. Optional lemma fallback applies to an intentionally
absent layer, not a present file with the wrong build ID. An empty new lemma layer must
not retain a stale lemma file from an older output directory.

Required tests: different wall-clock days with identical explicit inputs; equal-sized
different vocabularies; changed source ordering/panel; wrong lemma ID; array truncation;
crash between files; failed compression; and unchanged compressed output on rebuild.
Compare actual msgpack.xz and TSV.gz bytes, not `repr()` of in-memory payloads.

## 9. Phase 5 — validation and release checks

### Read-only validator

Make normal `validate.py --db ...` a read-only check. It must not apply schema, recalculate
tables, repair counters, or update baseline artifacts. SQLite TEMP tables for joins are
fine. Invalid/missing paths fail without creating a new DB. Reports go to an explicit
output path separate from the inspected input.

Provide `--strict` and machine-readable JSON output. Strict success requires:

- Count conservation and structural checks from Phase 0 for every included source.
- All existing quality checks, with the existing function-word/concordance/DEX thresholds.
  No silent pass when a required reference or comparison population is unavailable.
- All 59 monotone pairs actually comparable; report missing members as failures in strict
  mode. Require at least 99% coverage of the pinned wordfreq Zipf>=3 candidate vocabulary
  (audit baseline 43,095/43,413, about 99.27%); record the reference version/data identity.
- Current stage/input generation consistency, matched-panel lemma invariants, and the
  exact candidate artifacts named by the result manifest.
- Required surface/by-source files present, hashes/IDs/array structure valid, expected
  filtered word set and quantized values equal to DB output, and TSV parity. Optional
  lemma presence must agree with the manifest; validate its values if included.

Stream whole-table/artifact comparisons where possible. Do not materialize several
copies of the real vocabulary in memory just to compare it. A developer-mode skip must
be visible in JSON and must never yield a release-eligible report.

Sealed legacy source counts may pass strict checks against their measured logical hashes
even where historical raw files are unavailable. Report that limit explicitly: verifying
the retained counts and their derivations does not prove a fresh raw-corpus reconstruction.
Do not invent historical fingerprints or block this scoped repair on re-downloading the
five good sources.

### Independent rebuild verification

Move idempotence rebuilding to an explicit separate command/mode using a disposable
SQLite backup or a fixture. Build once and compare against an independently rebuilt
generation from the same raw snapshot and manifest. Validate the rebuilt candidate
after all stages. Compare actual files plus logical table content, not SQLite file bytes
(page layout differs legitimately). Include optional lemma and TSV, which current check 6
does not verify as emitted files. Never overwrite the inspected baseline during a check.

The old stale-merged fixture must fail strict validation, including when references are
unavailable. Capture before/after DB and artifact hashes around validation to prove it
does not mutate them. Missing/broken references must not shrink the denominator into a
misleading `3/3` release success.

### Packaging and CI

Keep normal CI offline with respect to corpora: generate a tiny synthetic complete
generation, build both sdist and wheel, build a wheel **from that sdist**, install into a
clean venv, and execute surface, source, top-list, and lemma-present/absent lookups. Run
outside the checkout with `PYTHONPATH` unset so imports cannot fall back to repository data.
Assert expected files/IDs inside archives. Mark fixtures synthetic in their manifests.

The release workflow must check the **actual prepared distribution**, never interpret a
synthetic wheel or import-only success as a release gate. Implement a local release
preflight accepting a candidate bundle and checking its wheel/sdist/data/report identity.
Reuse that preflight in Actions. A strict real-data validation report must bind the exact
artifact hashes, code revision, version, and build ID being released; synthetic reports
are rejected. Do not imply that a hash-bound report independently reruns the corpus checks
on a runner that lacks the DB; the local real-data validation remains a documented step.

For transport, support explicit HTTPS candidate-bundle URL plus expected SHA-256 through
workflow_dispatch; tag runs may read those values from a small prepared candidate record
at the tagged revision. Verify the digest before extraction. If no real candidate is
provided, the release gate fails with an actionable message. This does not require a
public full-database host or a publishing job. Do not invent a URL or commit dummy success
records. Confirm versions/tag agree and docs describe the actual panel/build.

Prepare the corrected candidate as a patch build (`0.2.1` if `0.2.0` is still the current
version), since the panel is unchanged. Align pyproject and API version when implementing
the candidate; do not tag it. Record format-version changes separately from package version.

## 10. Phase 6 — repair real data without rerunning the five good corpora

Only start after the preceding fixes and focused tests pass. The following is the runbook
the implementing agent must execute in staging, then fill with actual paths/hashes/results.

1. **Freeze and inventory inputs.** Verify no relevant writer runs. Inventory/hash the 30
   `data/raw/social/*.zst` files and the private DEX map. Capture canonical, ordered logical
   hashes of all five unaffected sources' raw counts and metadata. Do not treat matching
   sums alone as proof their rows were preserved.
2. **Create the candidate DB using SQLite backup.** Use an ignored dated/unique staging
   directory. Keep the original as rollback material. Hash/record the baseline before any
   operation and never reuse the production JSON checkpoint namespace.
3. **Seal/adopt the five valid completed sources in the candidate.** Preserve migrated
   denominators and document any unknown historical raw-input identity. Do not re-ingest
   web/news/subs/eu/wiki or reapply already-completed tokenizer migrations.
4. **Build social into a fresh isolated DB using the fixed ingester.** Read the existing
   compressed dumps, with unchanged tokenizer/filter and unbounded record consumption.
   Use a new generation with SQLite state. Log raw/parsed/rejected/kept records and tokens
   per file, malformed records, hashes, and elapsed time. Verify input hashes have not
   changed by completion. Compare decoded raw counts with the fetch checkpoint inventory
   during this same pass, so missing acquisition prefixes are visible rather than hidden.
5. **Verify rebuilt social.** Expect approximately, and if unchanged inputs/policy exactly,
   **536,523,553 tokens and 13,717,928 kept documents**. These are a diagnostic expectation,
   not values to force into metadata. A mismatch requires investigation and a written
   explanation before accepting the candidate. The rebuilt rows must sum to their stored
   denominator. Reconstructing/removing only the smoke sample is not the primary repair.
6. **Replace social only in the candidate DB transactionally.** Attach/import the fresh
   source's counts, metadata, and authoritative state using bounded SQL operations. Drop
   old social counts and derived rows as part of candidate replacement, not by additive
   ingest. Source/DB identity fields must be rebound intentionally when importing a staged
   source; do not leave state pointing at the donor DB. Invalidate downstream provenance.
7. **Rebuild all derived candidate stages.** Recompute source Zipf, merge, optional lemma,
   data bundle, and TSV in order using one fixed manifest. Recompute every source's Zipf
   because stage 2's replacement contract changed, while preserving their raw counts.
8. **Validate strictly and independently rebuild once.** Use the private DEX map and the
   pinned wordfreq reference. Record all scores, candidate/output digests, byte parity,
   installed-wheel checks, resource use, and independent-rebuild result. A failed quality
   threshold requires diagnosis; do not lower it just to finish this repair.
9. **Compare to baseline.** Prove the five unaffected raw source hashes and totals match.
   Report social raw row/count changes; merged/shipped word counts; per-source and merged
   frequency deltas (quantiles and largest movers); `n_reliable` changes; lemma Zipf/ratio
   changes; and number of remaining invalid ratios (must be zero). Expect social's changed
   evidence to affect some merged/lemma values; quantify rather than promising no change.
10. **Prepare promotion/rollback, leave baseline untouched.** Document how to select the
    completed bundle, replace the canonical DB using the verified candidate, install/copy
    matching package data together, regenerate the public review TSV/checksum after owner
    adoption, and revert to the retained baseline. Do not silently edit the existing Drive
    share or advertise the old checksum as belonging to the corrected build.

The historical full social ingest took about 35 minutes, plus derived builds/validation.
Use checkpoints and progress reporting; do not promise this duration on another machine.
Do not repeatedly restart a good long-running rebuild just to collect another metric.

Suggested final command contracts (implement and document exact names/options; these
commands are not runnable in the baseline checkout):

```text
python build/validate.py --db <candidate.db> --manifest <result.json> --strict --report <validation.json>
python build/check_release.py --bundle <candidate-bundle> --expected-sha256 <digest>
python build/check_rebuild.py --db <candidate.db> --manifest <inputs.json> --work-dir <scratch>
```

CLI names may differ if a smaller coherent implementation fits existing conventions;
the behaviors and gates above are required. Every new `build/*.py` must have a harmless
`--help`, because CI exercises all those entry points.

## 11. Required regression matrix

| Area | Correctness evidence required |
|---|---|
| All ingesters | Shared real commit/kill/resume protocol; no duplicate batches; wrong-generation/writer refusal |
| Experiment isolation | Test then full equals fresh full; canonical DB/state unchanged by test invocation |
| Legacy adoption | All five migrated denominators preserved; completed resume no-op; unsafe unfinished import refused |
| Social limits | Full stream equals capped/resumed stream at boundaries, including rejected/malformed records |
| Acquisition | Decoded capped/resumed/finalized stream equals uninterrupted stream through crashes; all B4 recovery cases |
| Stage 2 | Removed rows disappear, empty source clears state, failure rolls back, other sources preserved |
| Lemma layer | Matched-panel ratio=2 fixture, unambiguous=1, 5/6-source trim, no ratio below one |
| Manifest/runtime | Same-input bytes across days; mixed equal-count files/panels/vocabularies/lemma IDs rejected |
| Validation | Read-only, stale inputs/artifacts fail, absent references fail strict, missing comparison words fail strict |
| Distribution | sdist-to-wheel includes data, clean installed API works, synthetic data cannot satisfy real release gate |
| Real candidate | Social conservation, five source hashes unchanged, full strict report, output parity and independent rebuild |

Run focused tests for each phase, then the full suite and existing build-script CLI checks.
Keep normal CI data-free. Use meaningful fixtures and failure injection; do not create a
test that merely reimplements the production formula and compares it with itself.

## 12. Documentation and handoff deliverables

- Record the implementation contracts in new ADRs with unused numbers (ADR-003 already
  exists). Include transactional progress/legacy adoption, matched-panel ratio, and the
  deterministic validation/artifact lifecycle. Update the authoritative spec accordingly.
- Update `docs/method.md` and `docs/sources.md` in Romanian with measured repaired numbers
  and truthful explanations. Preserve dated historical entries. Update README examples,
  checksum/build metadata, figures and rendered copies if their embedded numbers change.
- Keep `docs/BACKLOG.md`, `docs/activity-history.md`, and `docs/NEXT-SESSION.md` current.
  Mark B4 incorporated/completed only when its tests pass. Replace the old advice to run
  the mutating validator as a quick inspection command.
- Document that `n_reliable` is an observed source count, not proven independence, and
  singleton `spread=0` does not demonstrate agreement. Do not change estimators based on
  these research concerns in this repair. Leave held-out tail calibration, source overlap,
  and median/mean comparisons as separate research work.
- Deliver: changed files; test commands/results; build/validation commands; exact staged
  paths and hashes; before/after tables and deltas; independent rebuild evidence; updated
  docs/ADRs; and copy/paste promotion/rollback commands. State any remaining failed gate.

### Conditions requiring a scoped follow-up decision

Continue independent work and report the concrete issue if raw social material is missing
or inconsistent and repair would require refetching; if disk space cannot support the
staged process; if a quality gate fails after the known defects are fixed; or if a change
would alter tokenization/source policy/merge eligibility or require a different corpus
panel. Do not silently solve these by changing the scientific question or discarding data.
Known DEX/publication/hosting questions remain outside this brief and do not block private
code work, private candidate generation, and validation with the existing local reference.

### Completion checklist

- [ ] A01–A09 have correct-behavior regression coverage and fixes across the real adapters.
- [ ] Strict validation is read-only and rejects the known contaminated/stale cases.
- [ ] All tests and sdist/wheel installation checks pass; real release gate cannot accept a data-less build.
- [ ] A staged real social rebuild conserves counts; the five other raw sources are unchanged.
- [ ] Every candidate stage/artifact belongs to one explicit build; independent rebuild files match.
- [ ] All real quality gates pass, invalid lemma ratios are zero, and measured changes are reported.
- [ ] Docs/ADRs and the final promotion/rollback runbook match the implemented behavior.
- [ ] Baseline data, raw dumps, legacy checkpoints, and existing public outputs remain preserved.
