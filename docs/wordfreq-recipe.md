# wordfreq recipe — the pragmatic path

> **Status (2026-08-11): this screen feeds nothing.** The `rare_in_use` tab it filled was
> removed. wordfreq's Romanian list has no resolution at the low end — measured over 60,000
> candidates, **99.6% score exactly 0.00** because the library has never heard of them, so
> its lowest real scores are ordinary words (`haz` 3.31, `bețiv` 3.22) while `zapciu`,
> `vornic` and `logofăt` are all 0.00 and indistinguishable. A tier defined on that band
> could only ever hold common words, at any threshold. The idea it was reaching for is now
> the `urme azi` filter, measured on CulturaX. The script still runs standalone; do not wire
> it back into `ui.db`. See docs/BACKLOG.md and CLAUDE.md.
>
> **The body below is 2021-era advice — *adopt wordfreq instead of a corpus pipeline*.**
> The pipeline now exists, so that recommendation is superseded. For the current
> question — *should we copy wordfreq's method, and what corpora would that need* —
> skip to **[Should we build a Romanian wordfreq?](#should-we-build-a-romanian-wordfreq--measured-2026-08-18)**
> at the end of this file.

For this project's actual goal — *produce a defensible list of Romanian
dictionary words that aren't in common modern use* — `wordfreq` plus
lemmatization probably collapses Phase 2 into a one-line lookup. This doc
explains why and how.

Companion to `docs/corpus-options.md` (which is mostly about going beyond
this) and `docs/methodology-v2.md` (which is mostly aspirational).

## What `wordfreq` is

[`wordfreq`](https://github.com/rspeer/wordfreq) by Robyn Speer is a
Python library for word frequencies in 40+ languages. It is not
itself a corpus — it is the **aggregated frequency tables** built from a
mix of corpora.

### How it's built

For each supported language, wordfreq combines up to **eight** sources:

- **Wikipedia** — encyclopedic
- **OpenSubtitles 2018 + SUBTLEX** — conversational / spoken
- **NewsCrawl 2014 + GlobalVoices** — news
- **Google Books Ngrams 2012** — books, where available
- **OSCAR** — web text
- **Twitter** — social
- **Reddit** — social

Romanian has **three** of these (Wikipedia, Subtitles, Web; plus some
Twitter), and only the **small** wordlist — no "large" tier.

### The figure-skating metric

For each word, wordfreq drops the **highest** and **lowest** per-source
frequency estimates, then averages the rest. The effect: any single
corpus's bias (Wikipedia's encyclopedic skew, Twitter's hashtag noise) is
trimmed. Robust by construction.

This is the **single most important methodological lesson** to learn from
wordfreq: don't trust any individual corpus, and don't try to weight them
manually — drop the outliers and average.

### Zipf scale

Output is on the **Zipf scale** = `log10(frequency_per_billion)`. So:

| Zipf | Frequency | Intuition |
|------|-----------|-----------|
| 7    | 1 per 100  | extremely common (the, and, …) |
| 6    | 1 per 1000 | common content word |
| 5    | 1 per 10K  | reasonably frequent |
| 4    | 1 per 100K | uncommon but known |
| 3    | 1 per million | rare; **wordfreq's reliability floor for small lists** |
| < 3  | below floor | returned as default; not real estimates |

For Romanian's small list, **anything with Zipf < 3 is "below the floor"**
— wordfreq returns 0 (or its default) rather than a real frequency, because
the underlying counts are too noisy at that level.

### Maintenance status

The project is in **sunset since around 2021** (Twitter killing its
academic API broke the rebuild). Data is frozen but stable. **For our
purposes that's a feature**: we want a stable reference baseline, not a
moving target.

## Why this nearly solves the project's problem

The project's framing is "find dictionary words not in common modern use."
The current Phase 2 builds a custom corpus to count occurrences. wordfreq
already aggregated 8 corpora across many registers and made the answer a
function call.

Reframe:

> Instead of building a corpus to find rare words, use `wordfreq` to
> filter out the words that *aren't* rare.

For each DEX low-frequency candidate:

- **Zipf ≥ 3** → modern signal across multiple corpora; **not forgotten**;
  filter out.
- **Zipf < 3 / 0 / below floor** → no modern signal in 8 aggregated
  corpora; **high-confidence forgotten**; keep.

## The recipe

```python
from wordfreq import zipf_frequency
import simplemma

THRESHOLD = 3.0  # wordfreq's small-list reliability floor for Romanian

def is_forgotten(word: str) -> bool:
    """True if a DEX candidate has no detectable modern usage signal."""
    lemma = simplemma.lemmatize(word, lang='ro')
    return zipf_frequency(lemma, 'ro') < THRESHOLD
```

That's it. Apply over `forgotten_words_curated.csv` (or directly over the
DEX low-frequency band from `lexemes.db`), keep the rows where
`is_forgotten` is true.

For ranking within the kept set, you can additionally:

```python
zipf = zipf_frequency(lemma, 'ro')
# zipf == 0  → strongest "forgotten" signal
# 0 < zipf < THRESHOLD → marginal; some signal but below reliability floor
```

Sort ascending by `zipf` for a "most forgotten first" ordering.

## What you give up

Honest accounting:

- **No resolution below Zipf 3.0.** wordfreq can't tell you whether a
  below-floor word has Zipf 2.5 or Zipf 0. They're all "rare." If you
  need to *rank* within the forgotten set with high resolution, you'd
  still need a custom corpus pass — but only over the below-floor subset
  (a few thousand words at most), which is much cheaper than a full pass.
- **Frozen at ~2021.** Words that became prominent or died in 2022–2026
  are mis-measured. For "forgotten Romanian dictionary words" this almost
  never matters — the dynamics are decadal at best.
- **No inflection awareness.** Romanian is morphologically rich. Always
  lemmatize before lookup. `simplemma` is one line.
- **Romanian is the small list.** No "large" wordlist exists, so the
  reliability floor is at Zipf 3.0 rather than the more permissive Zipf 1.0
  available for English / French / etc.

## When to add corpora on top

Consider supplementary corpora **only if** wordfreq's Zipf-3 floor proves
too coarse for your use case — i.e., if filtering at Zipf < 3 still leaves
many candidates that are actually common in modern Romanian.

If that happens, the smallest useful addition is:

- **OpenSubtitles RO** (via OPUS, free, no auth) — captures conversational
  register that even wordfreq's small Romanian list under-represents.
  Single-corpus pass, then filter words that appear ≥ N times in
  subtitles. This catches casually-used words that wordfreq missed.

Skip the rest of `docs/corpus-options.md` for the pragmatic goal. CulturaX,
mC4, Wikisource, Project Gutenberg, scraping — all aspirational unless
you're chasing the broader methodology in `docs/methodology-v2.md`.

## What this means for the existing code

If you adopt this recipe, the current Phase 2 pipeline (`process_corpus.py`
+ `validate_forgotten_words.py`) is largely redundant. A reasonable
migration:

1. **New script: `validate_with_wordfreq.py`** — ~30 lines, the recipe
   above, output `forgotten_words_validated.csv` with `zipf_frequency`,
   `is_forgotten`, and a confidence proxy (e.g. `1 - zipf/THRESHOLD`
   clamped).
2. **Demote `process_corpus.py`** to a one-shot reranker: run it only
   over the below-floor subset to get higher-resolution ranking among
   the genuinely forgotten words. This is a small fraction of the
   original workload.
3. **`validate_forgotten_words.py`** becomes redundant unless you want to
   keep the multi-signal confidence score (DEX × wordfreq × custom corpus).

This sidesteps the candidate-set mismatch bug (CLAUDE.md "Known issues"
#1) entirely — the bug only matters because the custom corpus pipeline
exists.

## TL;DR

```
pip install wordfreq simplemma
```

Three lines of Python replace ~600 lines of corpus-streaming code for the
common case. Use the rest of the documented infrastructure only if you
want to go beyond what wordfreq can express.

---

# Should we build a Romanian wordfreq? — measured 2026-08-18

Everything above was written in 2021 terms: *use wordfreq instead of a corpus
pipeline*. The corpus pipeline now exists and works. So the question changed. It is
no longer "should we use wordfreq's table" — the status banner at the top answers
that, and the answer is no. It is **"should we copy wordfreq's method"**.

This section answers that. It is written in short sentences on purpose.

## 1. What our index is

We rank words with `quality_score`. `make_shortlist.score()` computes it. It is a sum
of six bands and one penalty:

```
quality_score = verdict(12–30)              # what the corpora say happened
              + modern_rarity(4–25)          # how rare the word is today
              + hist_attestation(0–25)       # how well old text attests it
              + dex_frequency(0–20)          # DEX literary prominence
              + dict_count(0–12)             # how many dictionaries carry it
              + 13 · in_current_dict
              +  5 · has_definition
              −  8 · (4 ≤ family_ratio < 25) # count propped up by relatives
```

The result is clamped at 0. The UI sorts on it. User votes are added later, in SQL,
and they only reorder — see `VOTE_BOOST_SQL` in `public/api/_lib.php`.

## 2. What wordfreq does differently

wordfreq answers one question: **how common is this word?**

It reads up to eight corpora per language. For each word it takes the frequency from
each corpus. It drops the highest value. It drops the lowest value. It averages the
rest. This is the "figure-skating" metric. Then it writes the result on the Zipf
scale, which is `log10(occurrences per billion)`.

The trimming makes one bad corpus harmless. That is the whole point.

## 3. Copying the trimmed mean is the wrong move

I want to state this clearly, because the multi-corpus idea sounds obviously good and
it is not.

**wordfreq trims away disagreement between corpora. Disagreement between corpora is
our finding.**

A forgotten word is a word that old text uses often and new text does not use. That is
a large difference between two corpora. wordfreq treats a large difference as noise
and removes it. We treat it as the signal. `validate_diachronic.py` measures it
directly as `log_ratio`.

So the two methods want opposite treatment of the same fact. wordfreq wants the
typical value. We want the gap.

Four more reasons, each on its own:

1. **Three sources is not enough to trim.** You drop the highest and the lowest. With
   three sources, one source remains. That is not an average. That is "pick the middle
   corpus". wordfreq's own Romanian list has only three sources. This is part of why
   Romanian gets no "large" list.

2. **Our corpora differ too much in size.** CulturaX has 16.97 billion tokens. The
   subtitles have 13.2 million. That is a ratio of 1,283. A per-source average gives
   both the same weight. A rare word occurs 0–5 times in the small corpus, so its
   per-million estimate jumps wildly. Averaging that against a stable 17-billion-token
   estimate makes the good number worse.

3. **Averaging does not create resolution.** Below a few occurrences per corpus, each
   estimate is noise. The mean of noise is noise. wordfreq's Zipf-3 floor exists for
   this reason. We would inherit the same floor. And the floor is where our entire
   subject lives — 99.6% of our candidates sit below it.

4. **Each corpus costs a full re-run and a rescale.** A new corpus means a
   `process_*.py` pass over the DEX lookup set. It also means rescaling
   `MODERN_RARE_OCC` and `MODERN_ALIVE_OCC` through `scaled_modern_thresholds()`. Skip
   the rescale and every word looks more alive. CoRoLa was added and removed in one
   day. That is the cost of one attempt.

## 4. What multiple corpora actually buy us

Not an average. **Corroboration.**

"This word is absent from four independent modern corpora" is a stronger claim than
"this word is absent from one". It is a count of corpora that agree. It is not a mean
of their values.

Corroboration is better than the trimmed mean here for one reason: **absence is
binary, so corpus size does not distort it.** A 13-million-token corpus can honestly
report "I never saw this word". It cannot honestly report "this word occurs at 0.3 per
million". So we use the small corpora for the claim they can support.

This is the shape to build if we widen the modern panel. It is not the shape wordfreq
uses, and that is correct — we are asking a different question.

## 5. The panel that is thin is the historical one

This is the most useful number in this section. Measured from
`corpus_frequencies.db`:

| panel | corpora | tokens |
|---|---|---|
| modern | `culturax_ro` | **16,969,999,321** |
| historical | `wikisource_ro` + `lumro_ro` | **19,369,272** |

The modern panel is **876× larger** than the historical panel.

Now look at the score again. `hist_attestation` is worth up to 25 points. It ties with
`modern_rarity` for the largest band. `make_shortlist.py` says why in its own comment:
without it the score rewards obscurity itself, and the top of the list fills with words
that were never really in circulation.

So the single most load-bearing signal in our ranking is measured on 19.4 million
tokens. The signal we do not need more of is measured on 17 billion.

**Adding a fifth modern web corpus improves nothing.** CulturaX at 17 billion tokens
already resolves far below wordfreq's floor. That is why `modern_band` can separate
`zapciu` at 1,322 occurrences from `celșag` at 0. More web text does not make that
sharper.

**Adding historical text changes the ranking directly.** Every word now scoring 0 on
`SCORE_HIST` because old text shows it fewer than 3 times is a word we cannot yet tell
apart from a dictionary ghost.

## 6. Which corpora, then

### For the historical panel — do this first

| corpus | period | size | auth | note |
|---|---|---|---|---|
| **DigiBuc** (`digibuc.ro`) | 19th–20th c. | large | none | National Library. Historical newspapers and books. Needs scraping. OCR quality varies. **The biggest single unlock.** |
| **BCU Cluj / DigiTeca** | 19th–20th c. | large | none | Same shape, more academic. |
| **Project Gutenberg RO** | 19th–early 20th c. | ~50 books | none | Small, clean, direct download. Cheap to add. |
| **Transcriptorium / arcanum-type press archives** | 19th–20th c. | varies | varies | Worth a survey pass. |

Newspapers matter more than novels here. A novel is one writer's vocabulary. A
newspaper is the vocabulary of ordinary public life, which is what a word falls out
of. LUMRO already taught us this — its `document_count` is authors, not novels,
because three novels by one writer are one writer.

### For the modern panel — only if we want corroboration

| corpus | register | size | auth | note |
|---|---|---|---|---|
| **CC-News RO** | news | hundreds of M | none | The register CulturaX under-represents. |
| **OpenSubtitles RO** (OPUS) | conversational | large | none | Not the same as our `subtitle_ro`, which is Digi24 TV. |
| **Wikipedia RO** | encyclopedic | ~80M | none | We have the code in `archive/`. |
| **Reddit r/Romania** (pre-2023 dumps) | colloquial online | moderate | none | Missing from nearly every Romanian corpus. DIY work. |
| **Europarl / DGT** (OPUS) | bureaucratic | large | none | Good only as "does this word appear anywhere". |
| **`subtitle_ro`, folk-clips removed** | broadcast news | ~11M | have it | Cheapest of all. `clipId` is already the document unit. |

### Not available for Romanian

- **Google Books Ngrams.** Romanian is not one of its languages. That axis is closed.
- **Twitter.** The academic API is gone. Closed for everyone, including wordfreq.

## 7. So: could we build a Romanian wordfreq?

Yes. And it would be better than wordfreq's own Romanian list.

wordfreq's Romanian uses three sources and publishes only the "small" list. We could
reach six modern registers: web, news, subtitles, encyclopedic, colloquial-online,
bureaucratic. We already have the tokenizer, the normalizer, the schema
(`corpus_word_frequency` keys on `(word, corpus_name)`), and the paradigm rollup that
wordfreq does not have at all.

That last one is a real advantage. wordfreq counts surface forms. Romanian is heavily
inflected. Our `aggregate_by_family` rolls counts up through DEX's own paradigms.
Measured on CoRoLa: someone else's lemmatizer put 12,176 on `strugur` and 724 on
`strugure`; ours puts it back as 749 and 12,034.

## 8. Should we?

**Not as part of this project.** Three reasons.

1. It does not improve the list. Our bottleneck is the historical panel, and a
   modern-frequency table does nothing for it.
2. It is a different deliverable with a different audience. A frequency table is a
   public good for Romanian NLP. Oțios is a reading site.
3. It would compete for the same corpus-ingestion effort that the historical panel
   needs.

**But the infrastructure overlaps by about 80%.** If the historical panel work happens,
a Romanian frequency table is a small extra step from the same code. That makes it a
good *second* output, not a good *first* one.

The order that follows from all of the above:

1. Filter the folk clips out of `subtitle_ro` and re-run. Cheapest. We already hold the
   data.
2. Ingest one historical newspaper archive. Largest effect on the ranking.
3. Add modern corpora for **corroboration counts**, never for a trimmed mean.
4. Publish a Romanian frequency table only after 1–3, as a by-product.

## 9. The spin-off has its own spec

§7 says a Romanian frequency table is worth building and is a **separate project**.
The build spec for it is `docs/wrodfreq-spec.md` — corpus panel, schema, merge
algorithm, package API, validation plan, and the list of what transfers out of oțios.
It lives here only because the lessons were learned here; **the implementation belongs
in its own repo** and must not import from this one at runtime.

The one coupling worth building, in that order: wROdfreq publishes `n_reliable` (how
many independent corpora measured the word above their own floor), and oțios consumes
it as the corroboration signal §4 argues for.
