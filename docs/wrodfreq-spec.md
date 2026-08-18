# wROdfreq — build spec for a Romanian word-frequency table

> **Status:** design spec, 2026-08-18. Written in `otios` because that is where the
> lessons were learned; **the implementation belongs in its own repo**. Nothing here
> modifies oțios. Companion reading: `docs/wordfreq-recipe.md` §1–8 (why oțios does *not*
> use this method), `docs/corpus-options.md` (corpus catalog), `docs/corpus-expansion-plan.md`
> (measurements on the two rejected corpora).

## 0. Read this first — the one-paragraph brief

Build an open Romanian word-frequency table that is better than `wordfreq`'s Romanian
(three sources, "small" list only, reliability floor at Zipf 3.0). Ship it as a
pip-installable package whose API is **drop-in compatible with `wordfreq`**, plus the raw
SQLite for researchers. Reuse oțios's tokenizer, normalizer, checkpointing and — the real
differentiator — its **DEX-derived inflection map**, which lets the table offer per-lemma
frequencies that `wordfreq` cannot compute for any language.

**This is a different question from oțios's.** Oțios asks *did this word's usage change*
and lives on the disagreement between corpora. wROdfreq asks *how common is this word*
and wants the typical value across corpora. That is why the figure-skating trimmed mean
is **wrong for oțios and correct here.** Do not carry oțios's reasoning across
unexamined; §3 lists exactly where the two designs must diverge.

---

## 1. Name

**Brand it `wROdfreq`; ship it as `wrodfreq`.** PyPI normalises names to lowercase, so
`pip install wrodfreq` and the `wROdfreq` styling in the README are the same package. It
is unique, searchable and states the scope in one glance.

Rejected: `wordfreq-ro` (implies affiliation with a project that is in sunset, and reads
as a fork rather than a new build), `rofreq` / `freqro` (unsearchable — collides with
unrelated packages).

```python
import wrodfreq                       # module
from wrodfreq import zipf_frequency   # wordfreq-compatible entry point
```

---

## 2. What this is, and what it is not

**It is:** a per-source and merged frequency table for Romanian surface forms, on the
Zipf scale, with an optional per-lemma layer, built from open corpora, reproducible from
scripts in the repo.

**It is not:** a lemmatizer, a tagger, a corpus distribution, or an oțios feature. It must
run standalone with no dependency on the oțios pipeline at runtime. Build-time it consumes
**one** artifact from oțios (§5).

**Non-goals, state them in the README:** no language other than Romanian; no embeddings;
no attempt to model word senses; no "large"/"small" split unless the data forces one.

---

## 3. The four structural departures from oțios — get these right or the table is wrong

Oțios's corpus processors are correct for oțios and would produce a **broken frequency
table** if lifted unchanged. Four changes, in descending order of how badly they bite.

### 3.1 Open vocabulary — do not restrict counting to a word list

`process_culturax.py:294-297` does this:

```python
for tok in tokens:
    if tok in dex_words:          # ← ~315k DEX forms
        word_counts[tok] += 1
```

Correct for oțios: it only ever asks about DEX headwords, and the restriction keeps the
counter dict small over 40M documents. **Fatal for a frequency table** — it caps the
vocabulary at what a dictionary already knows, so every neologism, borrowing, brand,
proper noun and inflected-but-unlisted form is invisible. `laptop`, `selfie`, `covid`,
`clujean` would all be absent or wrong.

**Do:** count every token. Control memory with a two-pass or a periodic prune (§7.2), not
with a vocabulary filter.

### 3.2 The denominator must include short words

`tokenize()` (identical in `process_culturax.py:81-84`, `process_wikisource.py:39-42`,
`process_lumro.py:82-88`) ends with:

```python
return [t for t in tokens if len(t) > 2 and not t.isdigit()]
```

`tokens_processed` in `processing_stats` is therefore a count of **≥3-character tokens
only**. Romanian's highest-frequency words are largely 1–2 characters — `de`, `la`, `cu`,
`o`, `a`, `un`, `nu`, `se`, `pe`, `ce`, `ca`, `să`, `mi`, `îi`, `și` is 3 — so the
denominator is missing a large share of running text and **every per-million figure
derived from it is inflated by an unknown factor.**

Oțios never notices because it compares occurrence counts, never ppm across corpora (see
its own "never compare the two corpora in ppm" gotcha). A Zipf value is *defined* as a
rate, so the denominator has to be honest.

**Do:** drop the `len(t) > 2` filter entirely. Count all alphabetic tokens in both
numerator and denominator. Exclude numerals from both, consistently, and say so in the
docs.

### 3.3 Store per-source counts; merge as a derived view, never in place

`wordfreq` publishes only the merged value. Keep both. Per-source retention is what makes
three things possible that `wordfreq` cannot do:

- consumers merging on their own terms,
- the **corroboration count** (§8.3), which is the output oțios actually wants,
- diagnosing a bad source after the fact instead of rebuilding.

Cost is trivial — a few hundred MB of SQLite before compression.

### 3.4 Raw counts are surface forms; the lemma layer is derived and separate

`wordfreq` counts surface forms and stops there. Oțios rolls counts up through DEX
paradigms. **Do both, in that order, in separate tables.** You can always roll up; you
cannot un-roll. Baking the rollup into the base counts would make the table incomparable
with every other frequency resource.

---

## 4. What transfers from oțios, verbatim or nearly

Copy these into the new repo. Do **not** import from oțios at runtime.

| From | What | Change needed |
|---|---|---|
| `dump_parser.py:31-38` | `normalize()` — lower → `ş→ș`, `ţ→ț` → NFC. The canonical Romanian normalisation. | none |
| `process_culturax.py:81-84` | the tokenizer regex `[a-zăâîșț](?:[a-zăâîșț\-']*[a-zăâîșț])?` | **remove the `len > 2` filter** (§3.2) |
| `process_culturax.py:163-189, 240-320` | per-parquet-file checkpointing with row-group resume; atomic `save_checkpoint()` via `.tmp` + `replace()`; SIGTERM/SIGHUP flush | none — lift it wholesale |
| `process_culturax.py` docstring | the **HuggingFace `ds.skip(N)` cycling bug** workaround: read parquet shards directly via `HfFileSystem` + `pyarrow` instead of `datasets` streaming | none — this is hard-won |
| `corpus_frequencies.db` schema | `corpus_word_frequency(word, corpus_name, occurrence_count, document_count)` with `UNIQUE(word, corpus_name)`; `processing_stats(corpus_name, documents_processed, tokens_processed, status)` | rename, extend (§7.1) |
| `validate_diachronic.py:386-440` | `aggregate_by_family()` — share-weighted paradigm rollup with ambiguity splitting | adapt (§9) |
| `scrape_*.py` | `acquire_host_lock()` — `flock` on a per-**host** file so two scrapers cannot double the request rate | only if you scrape |
| `status.py`, `health_check.py`, `audit.py` | the monitoring triad for multi-hour jobs: read-only status, alert-once-per-problem, daily history snapshot | keep the shape, retarget |

**The tokenizer is copy-pasted into four files in oțios.** That is a known smell there.
In the new repo it is **one module, `wrodfreq/tokenizer.py`, imported everywhere** — and
pinned by a test asserting every processor produces byte-identical token streams for a
fixture paragraph. Two panels counted differently cannot be merged, and that failure is
invisible.

---

## 5. The one data asset worth taking, and it is a big one

**`data/processed/inflected_forms.db` — do not rebuild this.** Built by oțios's
`extract_inflected_forms.py` from the 1.65 GB DEX Online MySQL dump. Verified sizes:

```
lexeme(lexeme_id, lemma, frequency)          317,721 rows
inflected(form, lexeme_id)                 2,269,003 rows
form_lemma(form, lemma, lexeme_id, n_lemmas) 1,633,231 rows   200,601 of them ambiguous
```

This is a **complete, hand-curated Romanian morphological paradigm map** — not a
statistical lemmatizer's guesses. Nothing equivalent is available as a drop-in resource,
and it is what makes §9 possible. Ship it as a release artifact (SQLite, ~a few hundred
MB, compresses well), with `extract_inflected_forms.py` vendored so the provenance is
reproducible.

**Licensing is a real question, resolve it before publishing.** DEX Online's data is
community-contributed under its own terms. Check `dexonline.ro` licence terms and
attribute prominently. If redistribution is not permitted, ship the *extractor* and have
users build the file from the public dump — one extra step, no legal exposure.

**What is *not* worth taking:** the existing `corpus_frequencies.db` counts. They are
DEX-restricted (§3.1) and their denominator is wrong (§3.2). The corpora must be
re-processed. The *code* transfers; the numbers do not.

---

## 6. Sources

Target **≥5 sources** so the trimmed mean averages ≥3 (§8.2). Six is better.

### Core panel — build to this

| id | corpus | register | access | rough size | notes |
|---|---|---|---|---|---|
| `web` | **CulturaX RO** (`uonlp/CulturaX`) | web mixed | HF, no auth | ~40B tokens raw | oțios counted 16.97B post-tokenizer. The statistical backbone. |
| `news` | **CC-News RO** | news | HF mirrors / CC | hundreds of M | the register CulturaX under-represents |
| `subs` | **OpenSubtitles RO** (OPUS) | conversational | direct, no auth | large | closest open thing to spoken Romanian |
| `wiki` | **Wikipedia RO** (`rowiki` dumps) | encyclopedic | dumps.wikimedia.org | ~80M | oțios has working dump-streaming code in `archive/` |
| `books` | **Wikisource RO + Gutenberg RO** | literary (older) | dumps / direct | ~14M + ~50 books | **date-skewed — see §6.2** |
| `eu` | **Europarl / DGT** (OPUS) | bureaucratic-formal | direct | large | a strong "does this word exist in formal use at all" signal |

### Optional seventh

| `social` | **Reddit r/Romania**, pre-2023 Pushshift dumps via academictorrents | colloquial-online | DIY | moderate | the register missing from every Romanian corpus. Worth it if the effort is available. |

### 6.1 Closed axes — do not spend time looking

- **Google Books Ngrams:** Romanian is not one of its languages. That axis does not exist.
- **Twitter:** the academic API is gone. Closed for everyone, `wordfreq` included — it is
  why that project is in sunset.

### 6.2 Two traps oțios measured the hard way — they apply here too

- **A subtitle corpus is not automatically conversational.** Oțios's `subtitle_ro` was
  described as Digi24 news and is **~1/6th folk-music television**; clips with ≥3 genre
  markers are 15.6% of tokens but 27.5% of shortlist-word occurrences, and 444 of 2,446
  attested rare words appear *only* there. Sung traditional lyrics are not modern speech.
  **Do:** if you ingest a broadcast corpus, sample it by document and check genre before
  trusting it. OpenSubtitles (film dialogue) is a different and safer animal.
- **A "contemporary" reference corpus may not be contemporary.** CoRoLa spans **1945–
  present** and its frequency lists carry **no dates**, so no modern slice can be taken.
  Against CulturaX it over-represents pre-1953-reform spellings by 50–110× (`condițiune`
  112.8×, `comisiune` 49.6×). **Do:** treat it as its own `ref` source with its span
  documented, never silently folded into a "modern" merge. Same for `books`, which is
  19th–early-20th century and will pull archaic forms up.

**Consequence for the merge:** tag every source with a `period` (`contemporary` /
`mixed` / `historical`) and make the default merge use `contemporary` sources only. Expose
the others as an explicit opt-in. A frequency table that quietly averages 1890 and 2023 is
lying about the present.

---

## 7. Pipeline

Six stages, one script each, oțios's "one script per pipeline stage" convention. Every
stage is resumable and idempotent.

### 7.1 Schema

One SQLite file, `data/wrodfreq.db`.

```sql
-- one row per source
CREATE TABLE sources (
  source_id     TEXT PRIMARY KEY,      -- 'web', 'news', ...
  display_name  TEXT NOT NULL,
  url           TEXT,
  licence       TEXT,
  register      TEXT NOT NULL,         -- web | news | conversational | encyclopedic | literary | formal | social
  period        TEXT NOT NULL,         -- contemporary | mixed | historical
  period_note   TEXT,                  -- e.g. '1945–present, undated'
  total_tokens  INTEGER NOT NULL,      -- ALL alphabetic tokens (§3.2)
  total_docs    INTEGER NOT NULL,
  ingested_at   TIMESTAMP,
  status        TEXT DEFAULT 'in_progress'   -- in_progress | completed | rejected
);

-- raw surface-form counts, per source
CREATE TABLE source_counts (
  word          TEXT NOT NULL,
  source_id     TEXT NOT NULL REFERENCES sources(source_id),
  occurrences   INTEGER NOT NULL,
  documents     INTEGER NOT NULL,
  PRIMARY KEY (word, source_id)
) WITHOUT ROWID;
CREATE INDEX idx_sc_word ON source_counts(word);

-- derived: per-source Zipf, with the abstention flag (§8.1)
CREATE TABLE source_zipf (
  word          TEXT NOT NULL,
  source_id     TEXT NOT NULL,
  zipf          REAL,                  -- NULL when below that source's floor
  reliable      INTEGER NOT NULL,      -- 1 = clears the floor, 0 = abstains
  PRIMARY KEY (word, source_id)
) WITHOUT ROWID;

-- the published table
CREATE TABLE merged (
  word            TEXT PRIMARY KEY,
  zipf            REAL NOT NULL,       -- trimmed mean over reliable contemporary sources
  n_reliable      INTEGER NOT NULL,    -- how many sources cleared the floor  ← corroboration
  n_attesting     INTEGER NOT NULL,    -- how many saw it at all (≥1 occurrence)
  n_sources       INTEGER NOT NULL,    -- how many were eligible to see it
  zipf_min        REAL,
  zipf_max        REAL,
  spread          REAL,                -- zipf_max - zipf_min  ← register/diachrony signal
  is_dex          INTEGER DEFAULT 0    -- present in the DEX lexeme set
) WITHOUT ROWID;

-- optional layer (§9)
CREATE TABLE lemma_zipf (
  lemma           TEXT PRIMARY KEY,
  zipf            REAL NOT NULL,
  n_forms         INTEGER NOT NULL,
  zipf_headword   REAL,                -- the citation form alone, for comparison
  family_ratio    REAL                 -- undivided family total / disambiguated total
) WITHOUT ROWID;
```

### 7.2 Stage 1 — `ingest_<source>.py`

One per source. Streams the corpus, tokenizes, writes `source_counts` and the
`sources` row. Requirements:

- **Checkpointed**, per the `process_culturax.py` pattern. These are multi-hour jobs and
  they will be killed.
- **Memory-bounded.** With an open vocabulary the counter dict grows unboundedly on a 40B
  corpus. Use a `Counter` flushed to SQLite every N documents with `INSERT ... ON CONFLICT
  DO UPDATE SET occurrences = occurrences + excluded.occurrences`, and prune the in-memory
  dict after each flush. Do **not** try to hold the full vocabulary in RAM.
- **`documents` is a distinct-document count**, incremented once per document per word
  (oțios uses a `set` per doc — `process_culturax.py:294-300`).
- **Records `total_tokens` over every alphabetic token**, not just stored ones.

**One caveat inherited from oțios (`process_lumro.py`):** if a corpus has few authors,
`documents` overstates independence — LUMRO's 175 novels are 111 authors, and 638 of the
1,425 rare words it attests came from one person. Where author metadata exists, count
**authors** as the document unit and say so in `sources.period_note`.

### 7.3 Stage 2 — `compute_zipf.py`

Per source: `zipf = log10(occurrences / total_tokens * 1e9)`. Sets `reliable` per §8.1.
Pure function of `source_counts` + `sources`; safe to re-run.

### 7.4 Stage 3 — `merge.py`

The trimmed mean, §8.2. Writes `merged`.

### 7.5 Stage 4 — `build_lemma_layer.py`

§9. Requires `inflected_forms.db`. Optional — the package must work without it.

### 7.6 Stage 5 — `build_package.py`

Compiles `merged` into the shipped data file (§10.2).

### 7.7 Stage 6 — `validate.py`

§11. Must run in CI and must fail the build on a regression.

---

## 8. The merge — precise

### 8.1 A source abstains rather than reporting zero

This is the single most important algorithmic decision in the project.

A source's estimate for a word is **usable** only if the word occurs at least
`MIN_OCC_PER_SOURCE = 5` times in it. Below that, the source contributes **nothing** —
`zipf = NULL`, `reliable = 0`. It does **not** contribute a zero.

Why: a zero is a claim ("this word is rare"), but a small corpus that never saw a word is
usually just small. Feeding zeros into a mean lets an 80M-token Wikipedia drag down a
word that a 17B-token web corpus measured precisely. Abstention keeps the small sources
useful for what they *can* say — see `n_attesting` in §8.3.

**The floor is per source and derived, never a constant.** 5 occurrences in an 80M-token
corpus is a different claim from 5 in a 17B one. Generalise oțios's
`scaled_modern_thresholds()` lesson: *an absolute count only means something relative to
how much text was read.* Store the resulting per-source Zipf floor in `sources` so it is
inspectable, and re-derive it whenever a corpus is added — **never hardcode a Zipf number
in the consumer code.** A test pinned to a bare constant breaks the day a source lands.

### 8.2 Figure-skating trimmed mean, with a source-count guard

```
reliable = [zipf for each contemporary source where reliable = 1]

if len(reliable) >= 5:   zipf = mean(sorted(reliable)[1:-1])   # drop max and min
if len(reliable) == 3-4: zipf = mean(reliable)                 # plain mean, NO trim
if len(reliable) == 2:   zipf = mean(reliable)                 # flag low confidence
if len(reliable) == 1:   zipf = that value                     # flag low confidence
if len(reliable) == 0:   word is below the table's floor       # omit from `merged`
```

**Do not trim below 5 sources.** With 3, dropping max and min leaves one value — that is
not an average, it is "pick the middle corpus", and it is strictly worse than the plain
mean. `wordfreq`'s Romanian has exactly this problem and it is part of why Romanian gets
no "large" list.

Weighting by corpus size was considered and **rejected**: it defeats the purpose. The trim
exists to stop any single corpus dominating, and CulturaX is 200× the next source, so
size-weighting reduces to "CulturaX with extra steps".

### 8.3 The two counts that are wROdfreq's own contribution

`wordfreq` publishes one number. Publish three.

- **`n_reliable`** — how many sources measured the word above their floor. This is a
  **corroboration count**, and it is robust exactly where a mean is not: absence is
  binary, so a small corpus can honestly say "never saw it" even though it cannot honestly
  say "0.3 per million". *This is the field oțios should consume* (`docs/wordfreq-recipe.md` §4).
- **`n_attesting`** — saw it at least once. The difference between this and `n_reliable`
  is the "exists but is rare" band.
- **`spread`** (`zipf_max - zipf_min`) — how much the sources disagree. A high spread means
  the word is register-bound (`whereas` vs `innit`) or diachronically shifting. **This is
  the field that makes the table interesting rather than merely accurate**, and no other
  frequency resource publishes it. Oțios's entire thesis is a spread measurement.

---

## 9. The lemma layer — the real differentiator

`wordfreq` counts surface forms in every language, including heavily inflected ones. For
Romanian that means `înmărmuri` is credited 317 times while `înmărmurit` alone has 5,846 —
the verb reads as extinct.

Port `aggregate_by_family()` (`validate_diachronic.py:386-440`). Its design, and the three
things that will be got wrong on a re-implementation:

1. **Ambiguous forms are split, not duplicated.** ~12% of surface forms (200,601 of
   1,633,231 rows) are claimed by more than one lemma. `vești` is claimed by `veste`
   (news), `veșcă` (sieve rim) and `vești` itself. Crediting each claimant in full gives
   `veșcă` all 339,710 of `veste`'s occurrences.
2. **The split is weighted by each lemma's own headword frequency.** `vești` → `veste`
   576,766 vs `veșcă` 264, so `veste` takes ~99.9%. Weighting instead by "forms only one
   lemma claims" was tried and **fails**, because a noun's citation form is frequently
   shared too, leaving no evidence for exactly the words that need it.
3. **Documents take the max across a lemma's forms, share-scaled — never the sum, never
   all-or-nothing.** Summing double-counts a document holding two forms of one lemma.
   All-or-nothing (credit only a claimant winning ≥50%) gives zero documents to a lemma
   that never majority-claims any form, while it still accumulates occurrences — oțios had
   170 rows in that state, `văz` at 96 occurrences and 0 documents.

Publish `family_ratio` (undivided family total / disambiguated total) alongside. In oțios
it is a variant detector — `tinereță` sits at 298×, `veșcă` at 938×, an isolated word at
1×. As a public field it is a useful "this form shares a paradigm with something much more
common" signal.

**Keep the layer optional and separate.** `zipf_frequency()` returns surface-form
frequency by default, matching `wordfreq`. `lemma_frequency()` is the new thing.

---

## 10. The package

### 10.1 API — be a drop-in for `wordfreq`

```python
from wrodfreq import zipf_frequency, word_frequency, top_n_list

zipf_frequency('cuvânt', 'ro')      # → 5.12   ; 'ro' accepted and ignored, for compatibility
zipf_frequency('cuvânt')            # → 5.12   ; the natural spelling
word_frequency('cuvânt')            # → 1.32e-05
top_n_list(1000)                    # → ['de', 'și', 'la', ...]
```

Anyone with `wordfreq` code changes one import line. State this in the README's first
screen — it is the entire adoption story.

Then the extensions, clearly marked as such:

```python
from wrodfreq import frequency_detail, lemma_frequency, by_source

frequency_detail('birjă')
# FrequencyDetail(zipf=1.9, n_reliable=2, n_attesting=5, n_sources=6, spread=2.4)

lemma_frequency('înmărmuri')        # rolled up through the paradigm
by_source('birjă')                  # {'web': 1.8, 'news': None, 'subs': 2.1, ...}
```

**`zipf_frequency` must return `0.0` for an unknown word**, matching `wordfreq`'s
contract, and `frequency_detail` must return `None`. Two different signals for two
different questions; do not unify them.

### 10.2 Data file

Follow `wordfreq`'s own approach: a compressed table shipped inside the wheel, loaded
lazily on first call, no SQLite dependency at runtime. `msgpack` + `xz`, or Parquet.
Target < 30 MB for the surface table so the wheel stays installable.

Round Zipf to 2 decimals in the shipped file. The third decimal is noise and it costs
real bytes across ~2M entries.

Ship the full `wrodfreq.db` as a **GitHub release asset**, not in the wheel.

### 10.3 Versioning

`MAJOR.MINOR.PATCH` where **MINOR changes when the corpus panel changes**. Pin the panel
composition in the version metadata and expose it:

```python
wrodfreq.build_info()
# {'version': '0.3.0', 'sources': ['web','news','subs','wiki','eu'], 'built': '2026-09-01'}
```

Anyone citing the table in a paper needs to name the exact build. This is the field that
makes that possible.

---

## 11. Validation — the stage that will be skipped, so make it CI

`validate.py` must fail the build. Six checks:

1. **Function words land where they should.** `de`, `și`, `la`, `un`, `cu` must fall in
   Zipf 6.0–7.5. If they do not, the denominator is wrong (§3.2). **This check alone
   catches the worst bug in this spec.**
2. **Rank correlation against `wordfreq`'s Romanian list**, over the words it covers
   (Zipf ≥ 3). Expect Spearman ρ > 0.9. A lower value means a tokenizer or normalisation
   divergence, not a discovery.
3. **Monotone sanity pairs.** A hand-written fixture of ~50 pairs where the ordering is
   not in doubt: `apă` > `hidratare`, `mașină` > `automobil`, `casă` > `locuință`. Cheap,
   and catches merges that invert.
4. **Coverage.** ≥95% of DEX lemmas with `frequency > 0.5` must appear in `merged`. A
   large gap means the vocabulary filter crept back in (§3.1).
5. **Per-source disagreement report.** Not a pass/fail — a printed top-100 by `spread`.
   Read it. If it is full of tokenizer artifacts rather than genuinely register-bound
   words, something upstream is broken. (Expect: `dumneavoastră` high in `eu`, low in
   `subs`; that shape is correct.)
6. **Idempotence.** Re-running stages 2–5 on unchanged `source_counts` must produce a
   byte-identical data file. Oțios's `word_ids.tsv` discipline, generalised — it is the
   cheapest way to detect accidental nondeterminism (dict ordering, float accumulation).

---

## 12. Repo layout

```
wrodfreq/
├── README.md                  # drop-in story first, then the panel table
├── LICENCE                    # MIT for code; data licence stated separately and per-source
├── pyproject.toml
├── wrodfreq/
│   ├── __init__.py            # public API (§10.1)
│   ├── tokenizer.py           # THE tokenizer + normalize(). One copy. Ever.
│   ├── zipf.py                # zipf math, per-source floors
│   ├── merge.py               # §8
│   ├── lemma.py               # §9, degrades gracefully with no paradigm map
│   └── data/
│       └── ro_surface.msgpack.xz
├── build/
│   ├── ingest_web.py          # CulturaX      — start here, it is the backbone
│   ├── ingest_news.py
│   ├── ingest_subs.py
│   ├── ingest_wiki.py
│   ├── ingest_eu.py
│   ├── compute_zipf.py
│   ├── merge.py
│   ├── build_lemma_layer.py
│   ├── build_package.py
│   └── validate.py
├── vendor/
│   └── extract_inflected_forms.py   # from oțios, for provenance (§5)
├── tests/
│   ├── test_tokenizer.py      # identical token streams across every ingester
│   ├── test_merge.py          # the ≥5 guard, abstention, trim arithmetic
│   ├── test_api.py            # wordfreq compatibility contract
│   └── fixtures/
└── docs/
    ├── method.md              # §3, §8, §9 written for an outside reader
    └── sources.md             # per-source provenance, licence, period, size
```

`data/wrodfreq.db` and `data/checkpoints/` are gitignored. Follow oțios's convention: no
`.db` or `.csv` in git, ever, with a release asset instead.

---

## 13. Build order

Sequenced so something is usable early and each milestone is independently verifiable.

**M1 — skeleton and tokenizer.** `tokenizer.py`, schema, `compute_zipf.py`,
`validate.py` checks 1 and 6. Ingest **Wikipedia RO only** (~80M tokens, hours not days).
*Done when:* `zipf_frequency('de')` is in 6.0–7.5 from one source. This proves the
denominator (§3.2) before any long job runs.

**M2 — the backbone.** `ingest_web.py` over CulturaX, with checkpointing lifted from
oțios. This is the multi-day job; start it and build M3 while it runs.
*Done when:* `sources.total_tokens` for `web` is in the 15–20B range and validation
check 4 passes.

**M3 — panel.** `ingest_news`, `ingest_subs`, `ingest_eu`. Now ≥5 sources.
*Done when:* the trimmed-mean branch in `merge.py` is actually reachable.

**M4 — merge and package.** §8, §10. First installable wheel.
*Done when:* validation checks 1–4 and 6 pass in CI, and `pip install -e . && python -c
"import wrodfreq; print(wrodfreq.zipf_frequency('cuvânt'))"` works from a clean venv.

**M5 — lemma layer.** §9, using `inflected_forms.db`.
*Done when:* `lemma_frequency('înmărmuri')` substantially exceeds
`zipf_frequency('înmărmuri')`, and `family_ratio` for `tinereță` is ~300.

**M6 — publish.** PyPI, GitHub release with `wrodfreq.db` and `inflected_forms.db`,
`docs/method.md`.

**M7 — feed it back.** Expose `n_reliable` to oțios as a corroboration signal. That is a
change in the oțios repo, not this one, and it is the only coupling between the two.

---

## 14. Traps, collected

Ported from oțios's own hard-won list. Every one of these cost real time there.

- **Never compare corpora in ppm without checking the size ratio.** Oțios's two panels
  differ by 876× and a shared 0.1 ppm floor meant "<1,697 occurrences" on one side and
  "≥1.43" on the other. That one line classified `zapciu` (1,322 hits) as extinct.
- **An absolute occurrence threshold must scale with corpus size.** Add a source without
  rescaling and every word looks more common. Derive; never hardcode.
- **Do not weight sources by size in the merge.** It defeats the trim (§8.2).
- **A source's zero is not a measurement.** §8.1.
- **Check what is actually in a corpus before trusting its label.** "Digi24 news" was
  1/6th folk music; "contemporary reference corpus" started in 1945. §6.2.
- **`document_count` is an independence claim.** If a corpus has few authors, count
  authors. §7.2.
- **The tokenizer must be one module.** Four copies in oțios, currently identical by
  luck and discipline. Two panels tokenized differently cannot be merged and nothing
  will tell you.
- **Ambiguous surface forms must be split, not duplicated.** §9.1.
- **Long jobs need checkpoints, a restart loop, and a monitoring script.** Oțios's
  `status.py` / `health_check.py` / `audit.py` triad exists because a 40M-document job
  was silently cycling and nobody noticed for a day.
- **Idempotence is a testable property and the cheapest bug detector you have.** §11.6.
