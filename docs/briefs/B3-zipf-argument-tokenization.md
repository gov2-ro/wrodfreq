# Brief B3 — should `zipf_frequency` tokenize its argument?

**Owner:** Sonnet. **Status:** ready. **Written:** 2026-10-02 by Opus.
**This is a measurement plus a design proposal. Do not change shipped behaviour; Opus decides.**

## The question

`wordfreq`'s `zipf_frequency(word, lang)` tokenizes its argument before looking it up.
This project's does not — it looks up the string as given. They therefore agree on every
single-token input and diverge on everything else:

```
zipf_frequency('spune-')   # here: 0.0      wordfreq: 5.78  (it answers for `spune`)
```

The project's entire adoption story is that changing one import line is enough, so a
behavioural difference in the most-used function matters more than its size suggests.
Decide what a multi-token argument *should* return, before 1.0 pins it.

## Background — so you do not need the specs

The package ships a `wordfreq`-compatible API. Three functions must behave exactly like
`wordfreq`'s, with `'ro'` accepted and ignored: `zipf_frequency`, `word_frequency`,
`top_n_list`. Everything else (`frequency_detail`, `lemma_frequency`, `by_source`,
`build_info`) is explicitly marked an extension.

Two deliberate contract points you must not break:

- `zipf_frequency` returns **`0.0`** for an unknown word; `frequency_detail` returns
  **`None`**. Two different signals for two different questions — they are not to be
  unified.
- Shipped Zipf values are rounded to 2 decimals.

The public surface is `wrodfreq/__init__.py`; the single shared tokenizer is
`wrodfreq/tokenizer.py`. Read both. The installed `wordfreq` package is available in
`.venv` for direct comparison — use it as the oracle rather than reasoning about what it
probably does.

## What to measure

1. **The real divergence surface.** Enumerate the input shapes where the two disagree:
   multi-word strings, trailing/leading hyphens and apostrophes, mixed case, numerals,
   empty string, whitespace-only, punctuation-only, strings that tokenize to two or more
   tokens, strings that tokenize to zero tokens. For each, record both answers. Build this
   as a table, not prose.
2. **What `wordfreq` actually does** in each case, mechanically. In particular: when its
   tokenizer yields several tokens, does it return the first, the minimum, a combination,
   or something else? Read its source to confirm, and verify by calling it. Do not guess.
3. **Blast radius.** Does the project's own test suite, or `validate.py`, depend anywhere
   on the current non-tokenizing behaviour? Grep for call sites. A change that quietly
   flips a passing validation check is the risk here.
4. **The `0.0` versus `None` interaction.** If the argument tokenizes to zero tokens
   (`''`, `'...'`, `'123'`), what is the honest answer under the contract above? Note that
   a prior bug had `zipf_frequency('')` returning 2.34 where `wordfreq` gives 0.0; it was
   fixed, so there is an existing expectation in the suite — find it and respect it.

## Deliver

- A proposal: the exact behaviour `zipf_frequency` (and, if it shares the issue,
  `word_frequency`) should have for each input shape in your table, with the one-line
  reason each. Flag explicitly any case where matching `wordfreq` conflicts with the
  project's own `0.0`/`None` contract — that conflict is the actual decision and Opus
  needs it stated, not resolved quietly.
- The divergence table, written into `docs/BACKLOG.md` in place of the existing `- [ ]`
  entry on this topic (search for `spune-`), kept as `- [ ]`.
- An entry in `docs/activity-history.md` under `## 2026-10-02 — <short title>`, in the
  prose style of the existing entries (read two first).
- Optionally, the tests that *would* encode your proposal, marked skipped/xfail so they
  document the intent without changing behaviour or breaking CI.

## Out of scope

- Changing `zipf_frequency`, `word_frequency`, or the tokenizer. Proposal only.
- Rebuilding the data file or running the full `validate.py` (it takes 10–12 minutes and
  rebuilds artifacts; you do not need it for this).
- `build/fetch_social.py` and the crawl now running — do not touch either.
- Brief B2's foreign-diacritics question.

## Stop and ask

- If matching `wordfreq` would require changing the tokenizer rather than the API layer.
  That crosses into Brief B2's territory and is a spec decision.
- If you find that `wordfreq`'s own behaviour is inconsistent across versions — then the
  question becomes which version to target, which is Opus's call.
