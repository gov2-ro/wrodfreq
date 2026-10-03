# LexicRo — evaluation, what to take, what to offer

**Status:** nothing decided; nobody is blocked. Written 2026-10-03.
**Sources read 2026-10-03:** <https://lexicro.com/> and the author's write-up,
<https://dev.to/peterabolins/building-the-romanian-nlp-api-that-should-already-exist-2gg7>.
Everything below about LexicRo comes from those two pages — no code or model was inspected,
and the figures are theirs, not measured here.

**Why this file exists:** the owner has had a brief exchange with LexicRo's author and
intends to offer wROdfreq's results for integration. This is the background for that
conversation, and a list of what is worth taking in the other direction. It began as a
`docs/BACKLOG.md` entry and outgrew a checklist item.

## What LexicRo ships

- `POST /analyze` — lemma, POS, case, gender, number, person, tense per token.
- `GET /conjugate/{verb}` — conjugation tables, seven moods including *perfect simplu*
  and *viitor I*.
- Built on `bert-base-romanian-cased-v1`, fine-tuned for morphological tagging, served
  through FastAPI + Docker with an OpenAPI spec.
- Code MIT. **Model weights "still being worked out"** at the time of writing.
- Hosted freemium: free tier 1,000 requests/day, no card; paid tiers fund development.
- Stated data sources: DEXonline (313k+ lemmas), RoLEX (330k morphosyntactic entries),
  UD Romanian RRT treebank (9.5k sentences, CC BY-SA 4.0), MULTEXT-East Romanian word-form
  lexicon (428k entries, CC BY-SA 4.0), and verbecc Romanian XML templates for conjugation.

## The fit is disjoint, not merely complementary

LexicRo does morphology — which this project explicitly refuses to do (*not a lemmatizer,
not a tagger*). LexicRo has **no word-frequency data**, which is the only thing this
project does. Neither duplicates the other, and neither has to compromise to interoperate.

That matters for the conversation's framing: this is a trade between two projects with
non-overlapping scope, not one asking a favour of the other.

## What we could take from it

Ranked by value. None of this is committed to.

### 1. MULTEXT-East Romanian word-form lexicon — 428k entries, CC BY-SA 4.0

The interesting one, because it is a **clearly licensed** morphological lexicon, and the
lemma layer's blocker is precisely a licensing one: `inflected_forms.db` is DEX-derived
with unresolved redistribution terms, which is why `lemma_frequency()` ships as `0.0`
while 180,539 validated lemma frequencies sit unused locally. A MULTEXT-East-derived
paradigm map might be shippable where the DEX-derived one is not.

**It is not a free fix, and should not be presented as one.** Three real caveats:

- **Coverage drops a lot.** 428k word forms against DEX's 2,269,003 inflected forms. The
  honest next step is to measure what fraction of the current lemma layer survives on
  MULTEXT-East alone — not to assume a licensed substitute is an equivalent one.
- **It is an older resource**, so its weakest area is modern vocabulary — exactly where
  this project's open-vocabulary stance is the whole point. `selfie`, `covid` and
  `clujean` are the test cases.
- **CC BY-SA is share-alike.** A shipped data file incorporating it may itself have to be
  CC BY-SA. **This project currently cannot assess that, because it has no `LICENSE` file
  and no `license` field in `pyproject.toml`.** Settling the project's own licence is a
  prerequisite for this entire option, not a detail to tidy afterwards.

A third possibility worth keeping in view: use MULTEXT-East not as a replacement but as an
**independent second opinion** on the DEX-derived map — 200,601 of the form→lemma rows are
ambiguous, and two independently-built paradigm sources agreeing is stronger evidence than
either alone.

### 2. UD Romanian RRT treebank — 9.5k sentences, CC BY-SA 4.0

Gold-standard tokenization, so a candidate for a seventh `validate.py` check against an
**external** reference. Every current check is internal or compares against `wordfreq`,
which shares this project's broad approach; a treebank is genuinely independent evidence
about tokenization.

Expect convention mismatches rather than agreement: UD splits clitics where a frequency
table may deliberately not, and scoping an agreed subset — asserting conformance where
the conventions genuinely coincide, and documenting where they do not — *is* the work.
A check that fails for a known convention difference is worse than no check.

### 3. The morphological tagger as a better ambiguity split

Spec §9 splits an ambiguous surface form across its candidate lemmas weighted by each
lemma's own headword frequency. That is a **prior**. A tagger could split by the
distribution actually **observed in context**, which is strictly better evidence, and
200,601 form→lemma rows are ambiguous.

Two reasons it stays filed rather than pursued: it would mean running a BERT model over
28.2B tokens, and it imports a tagger dependency into a project whose discipline is not
being one. Record it as the principled alternative; **do not pursue it without first
measuring that the frequency prior is actually wrong.** The measurement is cheap and the
implementation is not.

## What we offer

- Per-surface-form and per-lemma frequency returned alongside `/analyze`'s lemma and POS.
- `n_reliable` / `spread` as a **ranking prior when a form has several possible analyses**.
  Frequency is the standard disambiguation signal for exactly this and they have none, so
  it plugs into a gap rather than adding a feature.
- The per-source breakdown (`by_source`), which lets a consumer ask whether a form is
  frequent in speech-like text or in legal text — useful to anyone analysing real input.

Note the lemma-level offer is currently gated by the same DEX question as everything else,
so it is honest to offer surface-form frequency now and the lemma layer conditionally.

## Ask the author one question first

LexicRo is built on DEXonline and RoLEX. Its own site states licensing for MULTEXT-East
and UD RRT but **not** for those two. So its author has already met this project's
deferred blocker, from the same direction.

How they resolved it — a licence grant, an arrangement with dexonline.ro, or an open
question they are carrying too — is directly useful intel and may unblock
`lemma_frequency()` without any of the MULTEXT-East trade-offs above. **Worth asking
plainly rather than inferring from the omission**, which could equally be an oversight on
a young project's attribution page.

## Related

- `docs/BACKLOG.md` — the pointer entry for this file.
- DEX licensing is deferred on purpose; see `docs/NEXT-SESSION.md` open question 1. This
  file does not reopen that decision, it records a possible route around it.
- `docs/decisions/ADR-002-foreign-diacritics-and-legacy-variants.md` — if a re-ingest is
  ever costed, a morphological resource is worth re-examining in the same pass.
