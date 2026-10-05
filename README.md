# wROdfreq

A Romanian word-frequency table on the Zipf scale, built from 6 open corpora (28.8B
tokens total), designed to ship as `pip install wrodfreq` with an API that is drop-in
compatible with [`wordfreq`](https://github.com/rspeer/wordfreq) — plus the raw SQLite
for researchers.

> **Status: built and working.** All 7 build milestones (`docs/wrodfreq-spec.md` §13)
> are done — 6-source panel ingested, merged, packaged, and validated
> (`build/validate.py`: **5/5 checks pass**, including byte-identical rebuilds).
> 6,064,995 words; 180,569 lemmas; 229 tests. Verified against a real, isolated
> `pip install` of the built wheel. **Not yet published to PyPI** — build the wheel
> yourself for now (see below).

```python
from wrodfreq import zipf_frequency, word_frequency, top_n_list

zipf_frequency('cuvânt')            # 4.74 — 'ro' accepted and ignored, for compatibility
word_frequency('cuvânt')            # 5.50e-05
top_n_list(5)                       # ['de', 'a', 'în', 'și', 'la']
```

Plus extensions no other Romanian frequency resource publishes — per-source
corroboration, not just an average:

```python
from wrodfreq import frequency_detail, by_source

frequency_detail('birjă')
# FrequencyDetail(zipf=1.77, n_reliable=4, n_attesting=5, n_sources=6, spread=0.87)
by_source('birjă')
# {'eu': None, 'news': 1.21, 'social': None, 'subs': 2.09, 'web': 1.77, 'wiki': 2.04}
```

`by_source`'s `None` for `eu` means that source never saw the word reliably — not that
the word is rare there. A source that abstains is a different claim from a source that
reports zero, and this table keeps the two apart (see `docs/wrodfreq-spec.md` §8.1).

`lemma_frequency()` rolls a word up through its whole DEX paradigm
(`lemma_frequency('înmărmuri')` is 1.66; the bare form `zipf_frequency('înmărmuri')` is
0.81), and `lemma_detail()` adds the number of forms and `family_ratio`. The lemma layer
(180,569 lemmas) ships in the package; DEX Online's redistribution terms are still being
confirmed (see `docs/BACKLOG.md`). Method: [`docs/method.md`](docs/method.md) (Romanian).

### Building it yourself

```bash
git clone git@github.com:gov2-ro/wrodfreq.git && cd wrodfreq
uv pip install -e ".[dev]"       # msgpack is the only runtime dependency; dev adds pytest
python -m pytest tests/ -q       # 229 tests — pass with no data files at all
# Full corpus re-ingestion is a multi-day job (see docs/wrodfreq-spec.md §13) —
# most people will want data/wrodfreq.db as a release asset once one exists, and
# just run:
python build/build_package.py    # compiles wrodfreq/data/*.msgpack.xz from it
```

---

## What it's for

The distinctive assets are the ones worth building on: **6,064,995 words** rather than
`wordfreq`'s "small" list, a floor far below Zipf 3.0, per-source retention
(`n_reliable` / `n_attesting` / `spread` / `by_source`), six registers with period tags,
and a lemma layer kept separate from surface forms.

### Where it does something nothing else can

**Lexicography.** The table computes DEX lemma coverage at 97.7%. Invert that both ways:
dictionary headwords with *no* corpus attestation are candidates for an
obsolete/literary marking, and corpus words absent from DEX are candidates for inclusion
(`clujean`, `selfie`, `covid`). Both directions are concrete worklists, not statistics.

**Psycholinguistics and experiment design.** Frequency is the strongest single predictor
of lexical decision latency, and Romanian researchers have thin options. Two things a
single frequency number cannot give you: matching stimuli on frequency *while
controlling for register-boundness* via `spread`, and `build_info()` exposing the exact
corpus panel so a paper can cite a reproducible build. That citability is a design
decision, not a side effect — `MINOR` changes whenever the panel does.

**Pedagogy, using `spread` properly.** Frequency-ordered vocabulary lists are the obvious
use. The non-obvious one: a learner should get high-frequency **low-spread** words first.
Words common *everywhere* are core vocabulary; high-spread words are common in one
register only, and teach a dialect rather than the language.

**Diacritic restoration and OCR correction.** Deciding whether `sa` should be `să` or
`sa` is a frequency-ranking problem with per-register priors. Note the caveat in
[`docs/decisions/ADR-002-foreign-diacritics-and-legacy-variants.md`](docs/decisions/ADR-002-foreign-diacritics-and-legacy-variants.md):
legacy-variant encoding affects exactly this use case and is a known-wrong area.

### Conventional, but well served

- **Readability scoring** — per-register frequencies say a text sits at subtitle level
  versus EU-legal level, instead of producing one opaque number.
- **NLP preprocessing** — vocabulary thresholds, OOV analysis, subword tokenizer
  training, spellcheck and autocomplete candidate ranking.
- **Terminology extraction** — high `spread` *is* the topic-bound-jargon signal, so it
  falls out for free.
- **Neologism and loanword tracking** — open vocabulary plus period tags is what makes
  `selfie` and `clujean` visible at all; a dictionary-filtered vocabulary makes them
  structurally invisible.
- **Morphological analysers** — frequency is the standard prior for ranking competing
  analyses of an ambiguous form. See
  [`docs/lexicro-evaluation.md`](docs/lexicro-evaluation.md).

### What it is not

Not a lemmatizer, tagger, or corpus distribution. It answers *how common is this word*,
and keeping the API small is why it answers it well. The lemma layer is in the
package (180,569 lemmas), but its redistribution terms are still being confirmed with DEX
Online.

In one sentence: **it tells you not just how common a Romanian word is, but how much the
corpora agree — and for a rare word, agreement is the more useful number.**

---

## Română

wROdfreq este un tabel de frecvență a cuvintelor pentru limba română, calculat pe scara
Zipf din șase corpusuri deschise (web, presă, subtitrări, conversații online, Wikipedia,
texte UE — 28,8
miliarde de cuvinte în total). Se instalează din sursă deocamdată (nu este încă publicat
pe PyPI — vezi mai sus) și oferă o interfață compatibilă 1:1 cu biblioteca `wordfreq` —
se schimbă o singură linie de import.

Este mai bun decât suportul `wordfreq` pentru română, care folosește doar trei surse,
doar lista "small" și un prag minim de Zipf 3.0. wROdfreq adaugă și un lucru pe care
nicio altă resursă de frecvență nu îl publică: gradul de confirmare per sursă
(`n_reliable`, `n_attesting`, `spread`) — câte din cele șase surse confirmă fiecare
cuvânt, nu doar o medie. Un strat de frecvențe per lemă, derivat din paradigmele
flexionare din DEX (180.569 leme), este inclus în pachet; termenii de redistribuire ai
DEX Online sunt încă în curs de confirmare. Metodologia completă: [`docs/method.md`](docs/method.md).

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

wROdfreq adds one new type of data: it shows how many of the six sources confirm each
word, not only an average. A frequency value for each word lemma is also in the package
(from DEX dictionary word-form data). The dictionary's terms for this use are still being
confirmed.

wROdfreq is not a lemmatizer. wROdfreq is not a part-of-speech tagger. wROdfreq is not a
text-collection product.

---

## Schematic overview

```
6 open corpora (web, news, subs, social, wiki, eu — 28.8B tokens)
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
- [x] **M5 — Lemma layer.** `build_lemma_layer.py`: 180,569 lemmas, ported oțios's
      disambiguation math but merges across sources via the same trimmed mean as surface
      forms (not oțios's raw-sum, which would let CulturaX dominate). Built, validated and
      shipped in the package as `ro_lemma.msgpack.xz` (DEX Online terms still being confirmed).
- [x] **M6 — Package.** `build_package.py` + the real API (`wrodfreq/__init__.py`).
      Verified against a real, isolated `pip install` of the built wheel. Not yet
      published to PyPI or cut as a GitHub release.
- [x] **M7 — Feed back.** `~/devbox/otios/validate_with_wrodfreq.py` exposes
      `n_reliable`/`n_attesting`/`spread` as a corroboration signal there — staged as a
      standalone CSV, not yet wired into oțios's own scoring (an editorial decision for
      that project, not this one).

All seven milestones are done and `build/validate.py` passes 5/5. What's left is
publishing and the DEX licensing question — see [`docs/BACKLOG.md`](docs/BACKLOG.md).

## Docs

- [`docs/wrodfreq-spec.md`](docs/wrodfreq-spec.md) — the build spec: schema, merge rules,
  lemma layer, API contract, validation, repo layout, build order, traps.
- [`docs/wordfreq-recipe.md`](docs/wordfreq-recipe.md) — why the parent project (oțios)
  rejected this method for *its* question, and the measurements behind that.
- [`docs/method.md`](docs/method.md) — how the numbers are made, in plain Romanian.
- [`docs/sources.md`](docs/sources.md) — the six corpora: size, period, licence, caveats (Romanian).
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
