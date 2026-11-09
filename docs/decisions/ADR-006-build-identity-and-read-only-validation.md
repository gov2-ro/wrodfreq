# ADR-006 — One build, one identity: deterministic manifests, complete generations, read-only validation

**Status:** accepted, 2026-11-09 (brief B7, Phases 3–5, findings A02, A07, A08, A09).
**Amends:** spec §7.3–§7.7, §10.2, §10.3, §11 (check 6), §12.

## Context

The audit found these defects:

- **A07.** Stage 2 upserted rows and never deleted them. A word that left
  `source_counts` stayed in `source_zipf` and in `merged`.
- **A09.** The payloads embedded the wall-clock date, so the same inputs gave different
  bytes on different days. The runtime accepted a by-source file from another build
  when only the row count matched. The audit reproduced old news values shown under
  the name `social`.
- **A02.** `validate.py` rebuilt stages 2–5 in place as its check 6. It validated tables
  that it had just rewritten, and it never checked the files that ship. On a stale
  fixture it reported "3/3 passed" and lost `de`.
- **A08.** CI built the wheel from a checkout where the data files are ignored by git.
  The wheel had no data, and CI still passed it.

## Decision

1. **Stages record their inputs.** `stage_state` holds one row per derived stage:
   `zipf:<source>` (generation, sequence, total), `merge` (panel, stage-2 runs,
   `is_dex` map), `lemma` (merge run, DEX map). Any count change deletes all rows.
   Stage 2 replaces a source's rows exactly, in one transaction. It refuses when
   `SUM(occurrences) != total_tokens`. Stages 3–5 refuse missing or stale input.
2. **A manifest names the build.** `build/build_manifest.py` writes the canonical
   inputs: versions, code revision and the content hashes of the source files (so a
   build from a dirty tree is attributed correctly), the tokenizer hash, the fixed
   build date, the panel with generations, totals and logical count hashes, the stage
   runs, the constants, the DEX map identities, and the synthetic and lemma flags.
   `build_id` is the hash of that JSON. A path, the page layout or a run time does not
   enter it.
3. **Format 2 payloads carry identity.** Every file carries the `build_id`, the panel
   and the scale. The surface and by-source files also carry `words_sha256`, the hash
   of the ordered word list. The runtime refuses any mismatch, truncated array or
   format-1 file. The lemma fallback applies only when the surface header says the
   build has no lemma layer.
4. **Generations are complete or invisible.** `build_package.py` writes the files to a
   hidden temporary directory, writes `result.json` (the SHA-256 of every file) last,
   and renames the directory to `<out-root>/<build_id>/`. A rebuild into an existing
   generation must give identical bytes. `--install` copies one verified generation
   into the package and removes the files the build does not include.
5. **Validation is read-only.** `validate.py` opens the database with `mode=ro` and
   checks the integrity, the provenance, the quality gates and the exact artifacts that
   `result.json` names. It writes only its report. `--strict` turns a skipped check
   (for example, a missing reference) into a failure, and it hashes the inputs before
   and after the run. Only a strict, all-pass, non-synthetic report is
   `release_eligible`.
6. **The rebuild check is a separate command.** `check_rebuild.py` copies only the raw
   tables to a disposable database, reruns every stage, and compares the manifest, each
   file byte by byte, and the logical content of each derived table.
7. **The release gate checks the real distribution.** `make_distribution.py` builds the
   sdist from a staging tree that holds one generation, then builds the wheel from that
   sdist. `check_release.py` checks the bundle. CI builds a synthetic bundle the same
   way and installs it outside the checkout. The release workflow downloads the real
   candidate with its digest. A synthetic, data-less or unbound candidate fails.

## Consequences

- The data format version is now 2, and the package version is 0.2.1. A wheel that is
  already installed keeps its own files and is not affected. A working checkout needs
  freshly built format-2 data (`build_package.py --install`).
- Strict validation of the real database takes several minutes. It streams the
  tables; it does not load them into memory.
- A report that is bound to the artifact hashes says what the local real-data run
  found. The CI runner cannot repeat the corpus checks, so that local run stays a
  documented release step.
