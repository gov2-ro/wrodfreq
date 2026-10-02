# Brief B6 — the hyphen-split path can still emit apostrophe-edged tokens

**Owner:** Sonnet. **Status:** ready. **BLOCKS `ingest_social.py`.** **Written:** 2026-10-02 by Opus.

## Why this blocks the social ingest

The apostrophe residue was cleaned out of the table today (`build/migrate_apostrophes.py`,
2,136 rows in `source_counts`, 225 of them reaching `merged`). **The data is clean; the
generator is not.** The tokenizer still produces apostrophe-edged tokens, so the next
ingest reintroduces the class — and the source being acquired right now is Reddit, the
most colloquial register in the panel, where the triggering construction is everyday
Romanian:

```
tokenize("las'-o in pace")    -> ["las'", 'o', 'in', 'pace']     # las' is apostrophe-edged
tokenize("zi'-mi")            -> ["zi'", 'mi']
tokenize("hai, las'-o' baltă")-> ['hai', "las'", 'o', 'baltă']
tokenize("zi-i'-o'")          -> ["zi-i'", 'o']
tokenize("da'-a'-a'")         -> ["da'-a'", 'a']
```

Six of eleven realistic colloquial strings regenerate the class. `las'-o`, `zi'-mi` and
`las'-o baltă` are ordinary spoken Romanian, which means `subs` (film dialogue) and the
incoming `social` are precisely the sources that will produce them in volume. Ingest
social before this is fixed and the migration has to be redone, on a larger table.

## The defect

The token pattern is `[a-zăâîșț](?:[a-zăâîșț\-']*[a-zăâîșț])?` — a token **must start and
end with a letter**, so the regex alone cannot emit `las'`. The breakage is in the
*post-regex* elision/hyphen-splitting step in `tokenize()` (read `wrodfreq/tokenizer.py`;
the split runs only when a token contains `-`). It splits `las'-o` at the hyphen and hands
back the pieces **without re-applying the edge invariant the regex enforces**, so the
trailing apostrophe survives on `las'`.

So this is not a new policy question. It is one invariant, already defined by the regex,
not being re-checked after a transformation. Restore it.

A related symptom of the same gap, found while verifying: the output is not always a fixed
point. `tokenize("da'-a'-a'")` yields `["da'-a'", 'a']`, and re-tokenizing `"da'-a'"`
yields `["da'", 'o']`-shaped output rather than itself. **Every token the tokenizer emits
must satisfy `tokenize(t) == [t]`.** That property is what the API layer now relies on
(see `docs/decisions/ADR-001-zipf-argument-tokenization.md` decision 2, whose premise this
brief corrects), so make it true and assert it.

## Precedent — follow it exactly

The hyphen version of this same bug was fixed in two parts, and both parts were needed:
`df501d0` fixed the *tokenizer* (a run of 2+ hyphens became a token boundary) and
`build/migrate_dashes.py` cleaned the *data*. Here the data half is already done. Read
both that commit and `migrate_dashes.py` before starting.

## The good news: no migration should be needed. Verify that, do not assume it

Today's migration folded `las'` into `las`, which is exactly what the fixed tokenizer will
produce directly. So the table should already be in the post-fix state and the fix is
purely forward-looking.

**Prove it rather than trusting it.** For every key in `merged`, the fixed tokenizer must
either reproduce it exactly (`tokenize(w) == [w]`) or the key must not be something the
fixed tokenizer could ever emit — and in the latter case you have found residue the
migration missed, which is a new finding to report, not to quietly migrate. Report the
count of each class before changing anything downstream.

## What to build

1. The fix in `wrodfreq/tokenizer.py`: re-apply the edge invariant after hyphen-splitting,
   and iterate to a fixed point so nested cases (`da'-a'-a'`) settle.
2. Tests in the existing tokenizer test file, matching its conventions:
   - **A property test over the emitted stream**: for a corpus of varied Romanian input,
     every token `t` satisfies `tokenize(t) == [t]`, no token starts or ends with `'` or
     `-`, and no token is empty. This is the assertion whose absence let this through —
     an existing invariant test covers empty tokens only.
   - The six strings above as explicit regression cases.
   - Confirmation that genuine compounds still survive intact: `mass-media`, `site-ul`,
     `e-mail`, `ma'-sa` stay one token, and `într-o` still splits to `într` + `o`.
3. The before/after proof from the section above, printed.

## Constraints

- **`data/wrodfreq.db` is the only copy of 38.4M counted rows.** Read-only unless the
  proof above shows a migration is genuinely required; if it does, **stop and ask** rather
  than writing one, because that changes the cost of this brief entirely.
- **A crawl is running.** Do not touch `build/fetch_social.py`,
  `build/run_social_fetch.sh`, `data/checkpoints/social_fetch*` or `data/raw/`. Check with
  `pgrep -f 'build/fetch_social.py'`.
- The tokenizer lives in exactly one module and every ingester shares it — that is
  deliberate, because two sources tokenized differently cannot be merged and nothing would
  tell you. There is a test asserting byte-identical token streams across ingesters; it
  must still pass.
- Do **not** widen the character class. Foreign diacritics are decided separately in
  `docs/decisions/ADR-002-foreign-diacritics-and-legacy-variants.md` (do not widen), and
  this fix is independent of it.
- The reference `wordfreq` is not in this repo's `.venv`; it is at
  `~/devbox/otios/.venv/bin/python3`, reached by subprocess the way `build/validate.py` does.

## Deliver

- The tokenizer fix and the tests, `.venv/bin/python -m pytest tests/ -q` green with the count.
- The `merged`-reachability proof, with counts per class.
- `build/validate.py` run, with its result quoted — it is the gate, and a tokenizer change
  is exactly what it exists to catch. Check 1 (function words) and check 2 (concordance
  against `wordfreq`) are the ones to read closely.
- Entries in `docs/activity-history.md` and `docs/BACKLOG.md` (there is an open `- [ ]`
  item for this corner; close it), in the prose style already there.
- A report: what the fix changed, the reachability counts, validate.py's output, and
  whether any `merged` key turned out to be unreachable.

## Stop and ask

- If the proof shows a migration is needed after all.
- If `validate.py` check 1 or check 2 moves at all. A tokenizer change that shifts the
  function-word band or the concordance score is a finding, not a detail to absorb.
- If restoring the invariant would change how genuine compounds (`mass-media`, `site-ul`)
  tokenize. That is a spec §3 question, not yours.
