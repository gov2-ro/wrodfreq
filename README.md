# wROdfreq

A Romanian word-frequency table on the Zipf scale, built from 5 open corpora (27.8B
tokens total), designed to ship as `pip install wrodfreq` with an API that is drop-in
compatible with [`wordfreq`](https://github.com/rspeer/wordfreq) — plus the raw SQLite
for researchers.

> **Status: built and working.** All 7 build milestones (`docs/wrodfreq-spec.md` §13)
> are done — 5-source panel ingested, merged, packaged, and validated
> (`build/validate.py`: 4/5 checks pass, one open issue tracked in
> [`docs/NEXT-SESSION.md`](docs/NEXT-SESSION.md)). Verified against a real, isolated
> `pip install` of the built wheel. **Not yet published to PyPI** — build the wheel
> yourself for now (see below).

```python
from wrodfreq import zipf_frequency, word_frequency, top_n_list

zipf_frequency('cuvânt')            # 4.86 — 'ro' accepted and ignored, for compatibility
word_frequency('cuvânt')            # 7.24e-05
top_n_list(5)                       # ['de', 'în', 'a', 'și', 'la']
```

Plus extensions no other Romanian frequency resource publishes — per-source
corroboration, not just an average:

```python
from wrodfreq import frequency_detail, by_source

frequency_detail('birjă')
# FrequencyDetail(zipf=1.78, n_reliable=4, n_attesting=4, n_sources=5, spread=0.89)
by_source('birjă')
# {'eu': None, 'news': 1.22, 'subs': 2.1, 'web': 1.77, 'wiki': 2.04}
```

`by_source`'s `None` for `eu` means that source never saw the word reliably — not that
the word is rare there. A source that abstains is a different claim from a source that
reports zero, and this table keeps the two apart (see `docs/wrodfreq-spec.md` §8.1).

`lemma_frequency()` also exists but currently always returns `0.0` — the DEX-derived
paradigm rollup is built and validated (180,820 lemmas) but not shipped in the package
yet, pending confirmation of DEX Online's redistribution terms. See
[`docs/NEXT-SESSION.md`](docs/NEXT-SESSION.md).

### Building it yourself

```bash
git clone git@github.com:gov2-ro/wrodfreq.git && cd wrodfreq
uv pip install -e ".[dev]"       # msgpack is the only runtime dependency; dev adds pytest
python -m pytest tests/ -q       # 43 tests — pass with no data files at all
# Full corpus re-ingestion is a multi-day job (see docs/wrodfreq-spec.md §13) —
# most people will want data/wrodfreq.db as a release asset once one exists, and
# just run:
python build/build_package.py    # compiles wrodfreq/data/*.msgpack.xz from it
```

---

## Română

wROdfreq este un tabel de frecvență a cuvintelor pentru limba română, calculat pe scara
Zipf din cinci corpusuri deschise (web, presă, subtitrări, Wikipedia, texte UE — 27,8
miliarde de cuvinte în total). Se instalează din sursă deocamdată (nu este încă publicat
pe PyPI — vezi mai sus) și oferă o interfață compatibilă 1:1 cu biblioteca `wordfreq` —
se schimbă o singură linie de import.

Este mai bun decât suportul `wordfreq` pentru română, care folosește doar trei surse,
doar lista "small" și un prag minim de Zipf 3.0. wROdfreq adaugă și un lucru pe care
nicio altă resursă de frecvență nu îl publică: gradul de confirmare per sursă
(`n_reliable`, `n_attesting`, `spread`) — câte din cele cinci surse confirmă fiecare
cuvânt, nu doar o medie. Un strat de frecvențe per lemă, derivat din paradigmele
flexionare din DEX, este deja calculat (180.820 leme) dar nu este încă inclus în pachet,
în așteptarea confirmării termenilor de redistribuire ai DEX Online.

Nu este un lematizator, un etichetator gramatical (POS tagger) sau o distribuție de
corpus — vezi [`docs/wrodfreq-spec.md`](docs/wrodfreq-spec.md) §2.

## English — Simplified Technical English (ASD-STE100 style)

wROdfreq gives word-frequency data for the Romanian language. The data comes from five
open text collections (27.8 billion words in total). wROdfreq uses the Zipf scale to
show how common each word is.

wROdfreq is not yet on PyPI. You must install it from source for now — see the build
instructions above.

wROdfreq has the same interface as the `wordfreq` tool. You can change one import line
to use wROdfreq instead of `wordfreq`.

wROdfreq is better than the Romanian data in `wordfreq`. The `wordfreq` tool uses only
three text collections. It shows only the "small" word list. It does not show words with
a Zipf value below 3.0.

wROdfreq adds one new type of data: it shows how many of the five sources confirm each
word, not only an average. A frequency value for each word lemma is also computed (from
DEX dictionary word-form data), but it is not in the package yet. The dictionary's terms
for this use are not yet confirmed.

wROdfreq is not a lemmatizer. wROdfreq is not a part-of-speech tagger. wROdfreq is not a
text-collection product.

---

## Schematic overview

```
5 open corpora (web, news, subs, wiki, eu — 27.8B tokens)
        │
        ▼
 ingest_<source>.py    per-source token counts — checkpointed, resumable
        │
        ▼
 compute_zipf.py       per-source Zipf + `reliable` flag (abstains below MIN_OCC_PER_SOURCE)
        │
        ▼
 merge.py              trimmed mean across sources — a derived view, never merged in place
        │
        ├──────────────► build_lemma_layer.py   per-lemma rollup via DEX paradigm map
        ▼
 build_package.py      wrodfreq/data/ro_surface.msgpack.xz  (the pip package payload)
        │
        ▼
 validate.py           CI gate — function words must land in Zipf 6.0–7.5, ρ > 0.9 vs wordfreq, ...
        │
        ▼
 zipf_frequency('cuvânt')     ← drop-in wordfreq API, plus frequency_detail / lemma_frequency / by_source
```

Two SQLite tables underpin all of this: per-source surface-form counts (raw) and a
separate lemma rollup (derived) — you can roll counts up, you cannot un-roll them. The
merge is always computed from the raw counts, never stored in place over them.

## Roadmap

Sequenced so something is usable early and each milestone is independently verifiable.
Full detail in [`docs/wrodfreq-spec.md`](docs/wrodfreq-spec.md) §13 and
[`docs/activity-history.md`](docs/activity-history.md)'s dated entries.

- [x] **M1 — Skeleton & tokenizer.** Schema, `tokenizer.py`, `compute_zipf.py`,
      `validate.py` checks 1 & 6. Ingested Wikipedia RO (442,389 articles, 109.6M tokens).
      `de`/`și`/`la`/`un`/`cu` land at 7.67/7.41/7.17/6.88/7.00 — within a few
      hundredths of `wordfreq`'s own published Romanian values, confirming the denominator
      is honest before any long job runs.
- [x] **M2 — Backbone.** `ingest_web.py` over CulturaX — 23.5B tokens, 40.3M docs, all
      64 shards.
- [x] **M3 — Panel.** `ingest_news` (CC-News RO, 2.19B tokens), `ingest_subs`
      (OpenSubtitles RO, 1.94B tokens), `ingest_eu` (Europarl+DGT, 84.4M tokens) — 5/5
      sources, the trimmed-mean branch in `merge.py` is reachable.
- [x] **M4 — Merge.** `merge.py`: 6,193,962 words in `merged`, atomic table-swap rebuild,
      verified idempotent.
- [x] **M5 — Lemma layer.** `build_lemma_layer.py`: 180,820 lemmas, ported oțios's
      disambiguation math but merges across sources via the same trimmed mean as surface
      forms (not oțios's raw-sum, which would let CulturaX dominate). Built and validated
      — **not shipped in the package** pending the DEX Online licensing question.
- [x] **M6 — Package.** `build_package.py` + the real API (`wrodfreq/__init__.py`).
      Verified against a real, isolated `pip install` of the built wheel. Not yet
      published to PyPI or cut as a GitHub release.
- [x] **M7 — Feed back.** `~/devbox/otios/validate_with_wrodfreq.py` exposes
      `n_reliable`/`n_attesting`/`spread` as a corroboration signal there — staged as a
      standalone CSV, not yet wired into oțios's own scoring (an editorial decision for
      that project, not this one).

All seven milestones are done. What's left is `build/validate.py`'s one remaining
failing check (a tokenizer/elision issue that needs a full 5-source re-ingest to fix)
and the DEX licensing question — both tracked in
[`docs/NEXT-SESSION.md`](docs/NEXT-SESSION.md).

## Docs

- [`docs/wrodfreq-spec.md`](docs/wrodfreq-spec.md) — the build spec: schema, merge rules,
  lemma layer, API contract, validation, repo layout, build order, traps.
- [`docs/wordfreq-recipe.md`](docs/wordfreq-recipe.md) — why the parent project (oțios)
  rejected this method for *its* question, and the measurements behind that.
- [`docs/BACKLOG.md`](docs/BACKLOG.md) — open bugs, debt, enhancements.
- [`docs/activity-history.md`](docs/activity-history.md) — chronological work log.
- [`docs/NEXT-SESSION.md`](docs/NEXT-SESSION.md) — where things stand and what's still
  an open decision, for picking this back up cold.

## Relationship to oțios

Spin-off of [voroave neglijate](https://github.com/gov2-ro/voroave)
(local path: `~/devbox/otios/`). Code is copied in, never imported at runtime. oțios asks
*did this word's usage change*; wROdfreq asks *how common is this word* — a different
question with a different correct merge. See `CLAUDE.md` before porting any reasoning
across.

The one real coupling (spec §13's M7) runs the other way: `validate_with_wrodfreq.py` in
oțios installs this project as an editable dependency and uses its corroboration signal
(`n_reliable`/`n_attesting`/`spread`) to screen oțios's own "forgotten word" candidates —
resolving 25.7% zero-signal on that project's real candidate list, against the ~99.6%
zero-signal `wordfreq` gave it before.
