# wROdfreq

A Romanian word-frequency table on the Zipf scale, built from ≥5 open corpora, shipped
as `pip install wrodfreq` with an API that is drop-in compatible with
[`wordfreq`](https://github.com/rspeer/wordfreq) — plus the raw SQLite for researchers.

> **Status: spec only, no code yet.** The build spec below is the design; nothing in
> `wrodfreq/`, `build/`, or `tests/` has been written. See
> [`docs/wrodfreq-spec.md`](docs/wrodfreq-spec.md) for the authoritative plan.

---

## Română

wROdfreq este un tabel de frecvență a cuvintelor pentru limba română, calculat pe scara
Zipf din cel puțin cinci corpusuri deschise (web, presă, subtitrări, Wikipedia, texte
UE). Se instalează cu `pip install wrodfreq` și oferă o interfață compatibilă 1:1 cu
biblioteca `wordfreq` — se schimbă o singură linie de import.

Este mai bun decât suportul `wordfreq` pentru română, care folosește doar trei surse,
doar lista "small" și un prag minim de Zipf 3.0. wROdfreq adaugă și două lucruri pe care
nicio altă resursă de frecvență nu le publică: gradul de confirmare per sursă
(`n_reliable`, `n_attesting`, `spread`) și un strat de frecvențe per lemă, derivat din
paradigmele flexionare din DEX.

Nu este un lematizator, un etichetator gramatical (POS tagger) sau o distribuție de
corpus — vezi [`docs/wrodfreq-spec.md`](docs/wrodfreq-spec.md) §2.

## English — Simplified Technical English (ASD-STE100 style)

wROdfreq gives word-frequency data for the Romanian language. The data comes from five
or more open text collections. wROdfreq uses the Zipf scale to show how common each word
is.

You can install wROdfreq with pip. Use this command: `pip install wrodfreq`.

wROdfreq has the same interface as the `wordfreq` tool. You can change one import line
to use wROdfreq instead of `wordfreq`.

wROdfreq is better than the Romanian data in `wordfreq`. The `wordfreq` tool uses only
three text collections. It shows only the "small" word list. It does not show words with
a Zipf value below 3.0.

wROdfreq adds two new types of data. First, wROdfreq shows how many sources confirm each
word. Second, wROdfreq shows a frequency value for each word lemma. This lemma data
comes from the DEX dictionary.

wROdfreq is not a lemmatizer. wROdfreq is not a part-of-speech tagger. wROdfreq is not a
text-collection product.

---

## Schematic overview

```
five+ open corpora (web, news, subs, wiki, eu, ...)
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
Full detail in [`docs/wrodfreq-spec.md`](docs/wrodfreq-spec.md) §13.

- [x] **M1 — Skeleton & tokenizer.** Schema, `tokenizer.py`, `compute_zipf.py`,
      `validate.py` checks 1 & 6. Ingested Wikipedia RO (442,389 articles, 109.6M tokens).
      *Done:* `de`/`și`/`la`/`un`/`cu` land at 7.67/7.41/7.17/6.88/7.00 — within a few
      hundredths of `wordfreq`'s own published Romanian values, confirming the denominator
      is honest before any long job runs.
- [ ] **M2 — Backbone.** `ingest_web.py` over CulturaX (the multi-day job).
      *Done when* `sources.total_tokens` for `web` is in the 15–20B range.
- [ ] **M3 — Panel.** `ingest_news`, `ingest_subs`, `ingest_eu` → ≥5 sources.
      *Done when* the trimmed-mean branch in `merge.py` is actually reachable.
- [ ] **M4 — Merge & first wheel.** *Done when* `pip install -e .` and
      `zipf_frequency('cuvânt')` work from a clean venv, validation checks 1–4 & 6 pass in CI.
- [ ] **M5 — Lemma layer.** Needs `inflected_forms.db` (vendored from oțios).
      *Done when* `lemma_frequency('înmărmuri')` substantially exceeds `zipf_frequency('înmărmuri')`.
- [ ] **M6 — Publish.** PyPI + GitHub release (`wrodfreq.db`, `inflected_forms.db`), `docs/method.md`.
- [ ] **M7 — Feed back.** Expose `n_reliable` to oțios as a corroboration signal — a
      change in the oțios repo, the only coupling between the two projects.

## Docs

- [`docs/wrodfreq-spec.md`](docs/wrodfreq-spec.md) — the build spec: schema, merge rules,
  lemma layer, API contract, validation, repo layout, build order, traps.
- [`docs/wordfreq-recipe.md`](docs/wordfreq-recipe.md) — why the parent project (oțios)
  rejected this method for *its* question, and the measurements behind that.
- [`docs/BACKLOG.md`](docs/BACKLOG.md) — open bugs, debt, enhancements.
- [`docs/activity-history.md`](docs/activity-history.md) — chronological work log.

## Relationship to oțios

Spin-off of [voroave neglijate](https://github.com/gov2-ro/voroave)
(local path: `~/devbox/otios/`). Code is copied in, never imported at runtime. oțios asks
*did this word's usage change*; wROdfreq asks *how common is this word* — a different
question with a different correct merge. See `CLAUDE.md` before porting any reasoning
across.
