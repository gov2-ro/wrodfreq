# Release runbook — candidate, promotion, rollback

How to turn a database into a released build, and back (ADR-004, ADR-006; brief B7).
Every command is copy-paste ready. Run them from the repository root, in a shell with
`.venv` active, and stop at the first failure.

## 1. Make a candidate (never on the canonical database)

```bash
ST=data/staging/<date>-<name>          # gitignored; one directory per candidate
DEX=~/devbox/otios/data/processed/inflected_forms.db
WF=~/devbox/otios/.venv/bin/python3    # interpreter with the pinned wordfreq (3.1.1)

python build/inventory.py --out $ST/inventory-before.json          # hashes of everything
python - $ST/candidate.db <<'EOF'              # consistent copy (SQLite backup API)
import sqlite3, sys
src = sqlite3.connect('file:data/wrodfreq.db?mode=ro', uri=True)
dst = sqlite3.connect(sys.argv[1])
src.backup(dst)
EOF
python build/adopt_legacy.py --db $ST/candidate.db --all-completed  # seal; refuses bad sources
# rebuild a source into an EMPTY staged db, then swap it in:
python build/ingest_<source>.py --db $ST/<source>.db
python build/replace_source.py --target $ST/candidate.db --donor $ST/<source>.db --source <source>
python build/compute_zipf.py --db $ST/candidate.db
python build/merge.py --db $ST/candidate.db --dex-db $DEX
python build/build_lemma_layer.py --db $ST/candidate.db --dex-db $DEX
python build/build_manifest.py --db $ST/candidate.db --build-date YYYY-MM-DD --out $ST/inputs.json
python build/build_package.py --db $ST/candidate.db --manifest $ST/inputs.json --out-root $ST/builds
GEN=$ST/builds/<build_id>
python build/validate.py --db $ST/candidate.db --generation $GEN --strict \
    --dex-db $DEX --wordfreq-python $WF --report $ST/validation.json
python build/check_rebuild.py --db $ST/candidate.db --generation $GEN --work-dir $ST/rebuild-work \
    --dex-db $DEX --wordfreq-python $WF --strict --report $ST/rebuild.json
python build/compare_builds.py --baseline data/wrodfreq.db --candidate $ST/candidate.db \
    --out $ST/compare.json
python build/make_distribution.py --generation $GEN --validation $ST/validation.json \
    --rebuild-report $ST/rebuild.json --out $ST/bundle --tar
python build/check_release.py --bundle $ST/bundle.tar.gz --expected-sha256 <sha256> --install-test
```

Do not freeze the manifest while you are still changing `build/`, `wrodfreq/` or
`pyproject.toml`: the manifest names their content hashes, and `build_package.py` and
`check_rebuild.py` refuse a different code tree.

## 2. Promote (owner decision; nothing below is done by the brief)

Keep the baseline until the owner has accepted the candidate. Promotion replaces four
things **together**, in this order, and keeps the old files for rollback:

```bash
ST=data/staging/2026-11-09-b7
BID=<build_id>
KEEP=data/rollback/$(date +%F)-pre-$BID
mkdir -p $KEEP/wrodfreq-data $KEEP/release
# 1. the canonical database
mv data/wrodfreq.db $KEEP/wrodfreq.db
cp $ST/candidate.db data/wrodfreq.db
shasum -a 256 data/wrodfreq.db                      # must equal the candidate's hash below
# 2. the package data of the working checkout (all files of one generation, together)
cp wrodfreq/data/* $KEEP/wrodfreq-data/ 2>/dev/null || true
python build/build_package.py --install $ST/builds/$BID
# 3. the public review TSV (regenerate the published checksum from THIS file)
cp release/ro_surface.tsv.gz $KEEP/release/
cp $ST/builds/$BID/ro_surface.tsv.gz release/ro_surface.tsv.gz
shasum -a 256 release/ro_surface.tsv.gz
# 4. check the result, read-only
python build/validate.py --db data/wrodfreq.db --generation $ST/builds/$BID --strict \
    --dex-db ~/devbox/otios/data/processed/inflected_forms.db \
    --wordfreq-python ~/devbox/otios/.venv/bin/python3 --report $KEEP/validation-after-promotion.json
```

The Google Drive review copy and the README link are **not** changed by these
commands. When the owner adopts the build, upload the new TSV under a new name, update
the README link and its checksum, and say in the README that the 0.2.0 file was
superseded. Never present the old checksum as belonging to the corrected build.

## 3. Roll back

```bash
KEEP=data/rollback/<date>-pre-<build_id>
cp $KEEP/wrodfreq.db data/wrodfreq.db
cp $KEEP/wrodfreq-data/* wrodfreq/data/ && rm -f wrodfreq/data/build.json
cp $KEEP/release/ro_surface.tsv.gz release/ro_surface.tsv.gz
shasum -a 256 data/wrodfreq.db release/ro_surface.tsv.gz   # equal to the baseline hashes below
```

The 0.2.0 blobs are data format 1. The 0.2.1 code refuses them with a rebuild message.
A rollback of the data therefore also needs the 0.2.0 code (`git checkout 81635a6 --
wrodfreq/`), or a reinstall of a 0.2.0 wheel.

## 4. Release

1. Host the bundle tarball (`$ST/bundle.tar.gz`) at an HTTPS URL. This brief chooses no
   host.
2. Either commit `.github/release-candidate.json`:
   `{"bundle_url": "https://…", "bundle_sha256": "<sha256>"}` and push the tag `v<version>`,
   or run the **Release check** workflow by hand with those two inputs.
3. The workflow verifies the digest, checks the bundle, and installs the wheel. It does
   not publish. Publishing to PyPI stays manual.

## 5. The 0.2.1 candidate (brief B7)

Filled in from the staged run on 2026-11-09; see `docs/activity-history.md`.

| What | Value |
|---|---|
| Staging directory | `data/staging/2026-11-09-b7/` (gitignored) |
| Build id | `eb23d6fe219530ceb9bc65caef6475dc` (0.2.1, data format 2, build date 2026-11-09) |
| Candidate database | `candidate.db`, SHA-256 `ef468da19885c9f45b973639d0418a2245aa93075bd952da6ce0ef2f9cdc93b8` |
| Staged social source | `social.db`, SHA-256 `fa13638997f15040f664e341541675cf92c1c65432222f5978167a8a07eaf658` |
| Generation | `builds/eb23d6fe219530ceb9bc65caef6475dc/` |
| `ro_surface.msgpack.xz` | `6483c18328ebce8e665ce00f2294d5296ebd1d50d427fc853dbf6c1c1e163e28` (28,555,296 B) |
| `ro_by_source.msgpack.xz` | `4c532b47e8f4c284a30707e179698ed4f1ab92cff8415bd5d4ecc4639b043734` (10,000,232 B) |
| `ro_lemma.msgpack.xz` | `fef835f9776c69317b025b4b9fe6ef3d42ebab87dbe4d2ea630aef2aa2fc9936` (1,107,800 B) |
| `ro_surface.tsv.gz` | `8bb1592e74a93afa9a909a48a44a26482fc7cbb07464894dc31ed1ceca54ca62` (35,763,094 B) |
| Bundle tarball | `bundle.tar.gz`, SHA-256 `3af9fabdafcec58d65dbce773c74aa939e5f346d0aaab86d37b423ab53347a20` |
| Strict validation | `validation.json`: 8/8 pass, `release_eligible: true` |
| Independent rebuild | `rebuild.json`: same build id, files and tables identical |
| Comparison | `compare.json` (see `docs/activity-history.md`, 2026-11-09) |
| Baseline (rollback) | `data/wrodfreq.db` SHA-256 `f990c4837c424772f54474ba50fafbd62118721d6266436b3099ad41df44fb10`; review TSV `06f85c941e0ce8f3ef8cf9b5f04b6dc37cedb9b2c12faa75463eea66b8502fc3` |

**Before promoting**, commit the B7 code and rebuild the generation from the committed
tree, so the manifest names a clean revision (this one names `81635a6` with
`dirty: true`). Only the manifest, the package and the checks rerun — about 25 minutes;
the candidate database stays as it is:

```bash
ST=data/staging/2026-11-09-b7
DEX=~/devbox/otios/data/processed/inflected_forms.db; WF=~/devbox/otios/.venv/bin/python3
python build/build_manifest.py --db $ST/candidate.db --build-date 2026-11-09 --out $ST/inputs-final.json
python build/build_package.py --db $ST/candidate.db --manifest $ST/inputs-final.json --out-root $ST/builds
BID=$(python -c "import json;print(json.load(open('$ST/inputs-final.json'))['build_id'])")
python build/validate.py --db $ST/candidate.db --generation $ST/builds/$BID --strict \
    --dex-db $DEX --wordfreq-python $WF --report $ST/validation-final.json
python build/check_rebuild.py --db $ST/candidate.db --generation $ST/builds/$BID \
    --work-dir $ST/rebuild-final --dex-db $DEX --wordfreq-python $WF --strict --report $ST/rebuild-final.json
python build/make_distribution.py --generation $ST/builds/$BID --validation $ST/validation-final.json \
    --rebuild-report $ST/rebuild-final.json --out $ST/bundle-final --tar
```

The data values do not change; the build id does, because the code identity is part of
it. Update the build id in `README.md`, `docs/method.md` and `docs/sources.md` to the
new one when you promote.
