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

## 5. The 0.2.1 build (brief B7) — promoted 2026-11-09

The candidate was first built as `eb23d6fe219530ceb9bc65caef6475dc` from the uncommitted
tree, then rebuilt from commit `7d4d612` (branch `b7-pipeline-repair`, `dirty: false`)
as the promoted build. The data values are identical; the build id differs because the
code identity is part of it.

| What | Value |
|---|---|
| Staging directory | `data/staging/2026-11-09-b7/` (gitignored) |
| Build id | `9550cd80441597586fa1a74eaf9069b2` (0.2.1, data format 2, build date 2026-11-09) |
| Manifest | `inputs-final.json`, code revision `7d4d6128759efc621ca122509d44661fa91c7dbf` |
| Database (now `data/wrodfreq.db`) | SHA-256 `ef468da19885c9f45b973639d0418a2245aa93075bd952da6ce0ef2f9cdc93b8` |
| `ro_surface.msgpack.xz` | `b33b55d1f7c8993ea9b1974e1376bbade187ea9e6b914bc6c5aede3a0b1ce5cd` (28,556,304 B) |
| `ro_by_source.msgpack.xz` | `a278d9fb3a572685ab93d70592c548c179272dcd499ac03a0371090bddc1d9b1` (9,999,328 B) |
| `ro_lemma.msgpack.xz` | `7b446842bdef5380b7e2636316221fc76d29eadc14737275333eb98b83138e6d` (1,107,152 B) |
| `ro_surface.tsv.gz` (now `release/`) | `8bb1592e74a93afa9a909a48a44a26482fc7cbb07464894dc31ed1ceca54ca62` (35,763,094 B) |
| Bundle tarball | `bundle-final.tar.gz`, SHA-256 `5223adf922d3220c2eed865003d531a17e067ecf704b04b9edad39df0a1838d2` |
| Strict validation | `validation-final.json`: 8/8, `release_eligible: true` |
| Independent rebuild | `rebuild-final.json`: same build id, files and tables identical |
| Release preflight | `check_release.py --install-test`: passed |
| Rollback copies | `data/rollback/2026-11-09-pre-9550cd80441597586fa1a74eaf9069b2/` (0.2.0 db `f990c483…`, blobs, TSV `06f85c94…`, `hashes.txt`) |

Still to do by hand: upload the 0.2.1 TSV to Drive and update the README link; host
`bundle-final.tar.gz` and run the release workflow when you want to publish.
