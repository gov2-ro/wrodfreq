# DEX paradigm extractor — provenance copy

**Not part of the build pipeline.** `build/build_lemma_layer.py` reads a finished
`inflected_forms.db`; this folder holds the scripts that made it, so the map can be
reproduced from DEX Online's public database dump if it cannot be redistributed
(BACKLOG: "DEX Online licence").

| file | what |
|---|---|
| `extract_inflected_forms.py` | reads the DEX Online SQL dump, writes `inflected_forms.db` (`lexeme`, `inflected`, `form_lemma`) |
| `dump_parser.py` | the quote-aware SQL-dump scanner and `normalize()` the extractor imports |

**Copied unchanged** from the oțios repo (`~/devbox/otios`, root directory) on 2026-10-05, at
that repo's commit `e3dbcd4`; both files were last changed there in `8c6cd4d`. Any edit
here is a fork — record it below. The docstring inside the extractor still speaks of oțios's
`process_culturax.py` and `corpus_frequencies.db`; read those as history.

## Running it

```bash
cd tools/dex_extractor
python extract_inflected_forms.py --dump /path/to/dex-database.sql   # full dump
python extract_inflected_forms.py --dump ... --limit 100000          # smoke test
```

Default input is `data/dictionaries/dex-database.sql` and default output
`data/processed/inflected_forms.db`, both relative to the working directory. The dump is
the 1.65 GB DEX Online MySQL export and is not in this repo. Verified output sizes
(oțios, 2026-09): 317,721 lexemes, 2,269,003 inflected forms, 1,633,231 form→lemma rows,
200,601 of them ambiguous.

## Changes since the copy

None.
