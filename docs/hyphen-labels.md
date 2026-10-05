# Hyphenated rows — labelling decisions (2026-10-05)

A record of how 360 sampled one-hyphen rows of `merged` were judged, the policy behind
it, and what is still open. **Re-validate this** (see `docs/BACKLOG.md`, "Re-validate the
hyphen decisions") before a major release and after any change to the tokenizer,
`normalize()`, or the corpus panel. The sample is small; treat every percentage here as
indicative, not measured on the full table.

Labels: **K** keep (a real Romanian word, compound or name that belongs in a frequency
table), **J** junk (a slug, fragment, glued pair, artifact), **?** open (needs the owner's
policy call). Labelled by the owner (`owner`) and by Claude (`claude`); Claude's first 22
proposals were accepted by the owner, the rest were applied by the same policy and are
**not individually confirmed**.

## Policy

1. **Hyphen + article ending is correct orthography, not junk** (`site-ul`, `wp-ul`,
   `pnț-ului`, `idn-urilor`). Keep. "Belongs to the root" is the lemma layer's job.
2. **Prefix and combining-form compounds are real** (`anti-poluare`, `ex-șefa`,
   `psiho-socială`), even where the dictionary writes them joined. Keep.
3. **Dictionary fixed phrases, reduplications, clitics and elisions are real** (`coada-mielului`,
   `ușor-ușor`, `trimițându-li`, `se-ntâlnește`). Keep.
4. **Two people, places or items glued by a hyphen are not vocabulary** (`iohannis-dăncilă`,
   `aiud-turda`): the hyphen stands for a dash. Junk.
5. **Line-break artifacts, fragments, slugs, abbreviation strings and plain English
   phrases are junk.** Colloquial `X-s` (= *X sunt*) follows the owner's marks: junk.
6. **Not yet decided** (all `?` below): hyphenated Asian names, Arabic `al-` names, single
   hyphenated proper names, chemical names, time phrases (`sâmbătă-dimineață`), stray
   two-word hyphenations (`pe-loc`).

## What the sample says

| stratum | rows in `merged` | sampled | K | J | ? | junk share of decided |
|---|---:|---:|---:|---:|---:|---:|
| `dex` | 6,130 | 60 | 55 | 3 | 2 | 5% |
| `corroborated` | 23,181 | 60 | 42 | 10 | 8 | 19% |
| `two_sources` | 74,099 | 60 | 28 | 22 | 10 | 44% |
| `one_src_common` | 14,081 | 60 | 18 | 26 | 16 | 59% |
| `one_src_rare` | 701,782 | 120 | 30 | 80 | 10 | 73% |

No stratum is nearly all junk, so **no blanket rule on a stratum is safe**: even the rare
one-source stratum (73% junk) is about a quarter real words. Cheap detectors tested against
these labels (decided rows only, small sample):

| rule | fires | right |
|---|---:|---:|
| joined twin ≥ 2.0 Zipf above → J (e.g. `lo-gica`, `logica`) | 13 | 85% (wrong: `ori-care`, `re-descoperit`) |
| joined twin ≥ 1.0 Zipf above → J | 30 | 70% |
| ends in `-ul/-ului/-uri/-urile/-urilor/-lor/-lui/-ii` → K | 44 | 91% (wrong: `odb-ii`, `jedi-ii`, `laic-uri`) |
| `is_dex = 1` → K | 58 | 95% (wrong: `brandy-u`, `cec-u`, `hard-back`) |
| `n_reliable ≥ 3` → K | 73 | 86% (wrong: name pairs, `png-cd`) |
| `n_reliable = 1` and web docs ≤ 20 → J | 130 | 65% |

The twin rule and the ending rule are the useful ones; neither is a filter by itself.

## The 360 rows

| word | label | class | by |
|---|---|---|---|
| al-muhajireen | ? | Arabic al- name | claude |
| al-muqayyar | ? | Arabic al- name | claude |
| al-okhdood | ? | Arabic al- name | claude |
| al-qadisiyya | ? | Arabic al- name | claude |
| ould-chikh | ? | Arabic al- name | claude |
| altyn-kel | ? | Asian name (hyphen in name) | claude |
| chi-lung | ? | Asian name (hyphen in name) | claude |
| chun-hui | ? | Asian name (hyphen in name) | claude |
| hamgil-do | ? | Asian name (hyphen in name) | claude |
| hung-liang | ? | Asian name (hyphen in name) | claude |
| hyun-seoy | ? | Asian name (hyphen in name) | claude |
| kim-lin | ? | Asian name (hyphen in name) | claude |
| nana-san | ? | Asian name (hyphen in name) | claude |
| ping-yao | ? | Asian name (hyphen in name) | claude |
| sang-min | ? | Asian name (hyphen in name) | claude |
| se-chan | ? | Asian name (hyphen in name) | claude |
| seong-un | ? | Asian name (hyphen in name) | claude |
| social-educativă | ? | adjective pair | claude+owner |
| anti-liane | ? | adjective/noun pair | claude |
| kama-manas | ? | adjective/noun pair | claude |
| mao-mao | ? | adjective/noun pair | claude |
| me-me | ? | adjective/noun pair | claude |
| pro-libertate | ? | adjective/noun pair | claude |
| alfa-pentilcinamil | ? | chemical term | claude |
| butadien-epoxid | ? | chemical term | claude |
| metil-triptofan | ? | chemical term | claude |
| ribozid-nicotinamidei | ? | chemical term | claude |
| b-aldolazei | ? | chemical term + ending | claude+owner |
| vai-de | ? | interjection fragment | claude+owner |
| carido-respirator | ? | single proper name | claude |
| etixx-quick | ? | single proper name | claude |
| gordon-levitt | ? | single proper name | claude |
| hilișeu-horia | ? | single proper name | claude |
| montreuil-bellay | ? | single proper name | claude |
| nagarno-karabah | ? | single proper name | claude |
| port-valais | ? | single proper name | claude |
| puez-odle | ? | single proper name | claude |
| scărița-belioara | ? | single proper name | claude |
| sint-laureins | ? | single proper name | claude |
| sud-oset | ? | single proper name | claude |
| dă-ia | ? | stray hyphen, two words | claude |
| dă-up | ? | stray hyphen, two words | claude |
| pe-loc | ? | stray hyphen, two words | claude |
| alaltăieri-dimineață | ? | time phrase | claude |
| ieri-dimineata | ? | time phrase | claude |
| sâmbătă-dimineață | ? | time phrase | claude+owner |
| add-package | J | English / foreign phrase | claude |
| artist-run | J | English / foreign phrase | claude |
| asylum-curb | J | English / foreign phrase | claude |
| cat-chat | J | English / foreign phrase | claude |
| coo-ee | J | English / foreign phrase | claude |
| falling-walls | J | English / foreign phrase | claude |
| gi-jane | J | English / foreign phrase | claude |
| hand-built | J | English / foreign phrase | claude |
| hard-back | J | English / foreign phrase | claude |
| jedi-ii | J | English / foreign phrase | claude |
| lake-s | J | English / foreign phrase | claude |
| menu-icon | J | English / foreign phrase | claude |
| mere-vieille | J | English / foreign phrase | claude |
| micro-greens | J | English / foreign phrase | claude |
| multi-crew | J | English / foreign phrase | claude |
| neo-psychedelia | J | English / foreign phrase | claude |
| pre-configured | J | English / foreign phrase | claude |
| slider-img | J | English / foreign phrase | claude |
| tv-review | J | English / foreign phrase | claude |
| x-tasy | J | English / foreign phrase | claude |
| young-woo | J | English / foreign phrase | claude |
| hell-no | J | English phrase | claude+owner |
| d-dc | J | abbreviation string | claude |
| d-rod | J | abbreviation string | claude |
| dbv-t | J | abbreviation string | claude |
| donalam-srl | J | abbreviation string | claude |
| dv-s | J | abbreviation string | claude |
| e-bar | J | abbreviation string | claude |
| e-hd | J | abbreviation string | claude |
| heinz-j | J | abbreviation string | claude |
| lido-h | J | abbreviation string | claude |
| mb-sr | J | abbreviation string | claude |
| np-gf | J | abbreviation string | claude |
| odb-ii | J | abbreviation string | claude |
| png-cd | J | abbreviation string | claude |
| pro-v | J | abbreviation string | claude |
| rfa-sc | J | abbreviation string | claude |
| ss-bb | J | abbreviation string | claude |
| tnb-supliment | J | abbreviation string | claude |
| bun-s | J | colloquial -s (= sunt) | claude |
| clopotele-s | J | colloquial -s (= sunt) | claude |
| noștri-s | J | colloquial -s (= sunt) | claude |
| ru-meu | J | colloquial -s (= sunt) | claude |
| alb-stralucitoare | J | fragment / slug | claude |
| an-scolar | J | fragment / slug | claude |
| aparat-sudura | J | fragment / slug | claude |
| atare-urlând | J | fragment / slug | claude |
| blog-uripolitică | J | fragment / slug | claude |
| cearc-acuma | J | fragment / slug | claude |
| consilierii-judeteni | J | fragment / slug | claude |
| copilă-fecioară | J | fragment / slug | claude |
| față-drepta | J | fragment / slug | claude |
| grupurile-client | J | fragment / slug | claude |
| guma-turbo | J | fragment / slug | claude |
| imperialist-sovietică | J | fragment / slug | claude |
| intra-extra | J | fragment / slug | claude |
| la-picioare | J | fragment / slug | claude |
| lumea-auto | J | fragment / slug | claude |
| material-spirituale | J | fragment / slug | claude |
| national-ceausism | J | fragment / slug | claude |
| o-timp | J | fragment / slug | claude |
| ocluziigasro-intestinale | J | fragment / slug | claude |
| oh-cult | J | fragment / slug | claude |
| opt-cozi | J | fragment / slug | claude |
| pietist-experiențialistă | J | fragment / slug | claude |
| proteină-carbon | J | fragment / slug | claude |
| redactare-care | J | fragment / slug | claude |
| rolul-cult | J | fragment / slug | claude |
| românești-farmconect | J | fragment / slug | claude |
| sentință-trăsnet | J | fragment / slug | claude |
| sora-copil | J | fragment / slug | claude |
| stat-societate | J | fragment / slug | claude |
| sub-banda | J | fragment / slug | claude |
| volumetrie-definitie | J | fragment / slug | claude |
| văzut-cadru | J | fragment / slug | claude |
| zero-doi | J | fragment / slug | claude |
| nicolae-iordache | J | full name | claude+owner |
| adu-m | J | line-break artifact | claude |
| brandy-u | J | line-break artifact | claude |
| ca-dru | J | line-break artifact | claude |
| cec-u | J | line-break artifact | claude |
| for-fait | J | line-break artifact | claude |
| insufici-ent | J | line-break artifact | claude |
| juma'-de | J | line-break artifact | claude |
| laic-uri | J | line-break artifact | claude |
| poix-de | J | line-break artifact | claude |
| poli-morfe | J | line-break artifact | claude+owner |
| sovie-tice | J | line-break artifact | claude |
| stârc-de | J | line-break artifact | claude |
| vânzare-cump | J | line-break artifact | claude |
| iohannis-dăncilă | J | name pair | claude+owner |
| c-enter | J | owner mark: irelevant | owner |
| copacii-s | J | owner mark: irelevant | owner |
| d-lover | J | owner mark: irelevant | owner |
| deloitte-ii | J | owner mark: irelevant | owner |
| do-rește | J | owner mark: irelevant | owner |
| kiv-fra | J | owner mark: irelevant | owner |
| lo-gica | J | owner mark: irelevant | owner |
| solitudinea-revista | J | owner mark: irelevant | owner |
| stinsu-s | J | owner mark: irelevant | owner |
| supra-puse | J | owner mark: irelevant | owner |
| tiber-eu | J | owner mark: irelevant | owner |
| vor-scadea | J | owner mark: irelevant | owner |
| abc-paramount | J | person/place PAIR | claude |
| airport-zaventem | J | person/place PAIR | claude |
| belin-vale | J | person/place PAIR | claude |
| bohdano-nadejdivka | J | person/place PAIR | claude |
| bute-elena | J | person/place PAIR | claude |
| bărăganu-potârnichea | J | person/place PAIR | claude |
| carmen-nicoleta | J | person/place PAIR | claude |
| cezar-ivanescu | J | person/place PAIR | claude |
| cezar-mihai | J | person/place PAIR | claude |
| ciomad-balvanyos | J | person/place PAIR | claude |
| compton-phillips | J | person/place PAIR | claude |
| dimitrie-sorin | J | person/place PAIR | claude |
| dracopol-ispir | J | person/place PAIR | claude |
| dreux-soubise | J | person/place PAIR | claude |
| duckadam-iovan | J | person/place PAIR | claude |
| honterus-buchdr | J | person/place PAIR | claude |
| hull-bedford | J | person/place PAIR | claude |
| irene-lux | J | person/place PAIR | claude |
| jerxen-orbke | J | person/place PAIR | claude |
| lazăr-aurel | J | person/place PAIR | claude |
| niemann-stirnemann | J | person/place PAIR | claude |
| parsi-bastogi | J | person/place PAIR | claude |
| pnl-pc | J | person/place PAIR | claude |
| ponta-felix | J | person/place PAIR | claude |
| ptolemeu-caesar | J | person/place PAIR | claude |
| rasova-malul | J | person/place PAIR | claude |
| românia-palestina | J | person/place PAIR | claude |
| rusia-olanda | J | person/place PAIR | claude |
| sadler-greene | J | person/place PAIR | claude |
| tarnița-lăpustești | J | person/place PAIR | claude |
| ursula-sandner | J | person/place PAIR | claude |
| valonia-bruxelles | J | person/place PAIR | claude |
| vidican-nanci | J | person/place PAIR | claude |
| vrba-wetzler | J | person/place PAIR | claude |
| vâlcan-popescu | J | person/place PAIR | claude |
| aiud-turda | J | place pair | claude+owner |
| banca-de | J | sentence fragment | claude+owner |
| franta-noua | J | slug, no diacritics | claude+owner |
| ir-uri | K | abbreviation + ending | claude+owner |
| anglo-saxon | K | combining-form compound | claude |
| cafeniu-roșcat | K | combining-form compound | claude |
| ceco-apendiculare | K | combining-form compound | claude |
| culturalo-religioase | K | combining-form compound | claude |
| foto-audio | K | combining-form compound | claude |
| franco-elvețian | K | combining-form compound | claude |
| franco-prusian | K | combining-form compound | claude |
| histerosalpingo-ooforectomie | K | combining-form compound | claude |
| juridico-politice | K | combining-form compound | claude |
| liberal-democrat | K | combining-form compound | claude |
| nazo-gastrică | K | combining-form compound | claude |
| nord-esticii | K | combining-form compound | claude |
| psiho-socială | K | combining-form compound | claude |
| reto-romani | K | combining-form compound | claude |
| sanitaro-digitale | K | combining-form compound | claude |
| schizo-incestuosă | K | combining-form compound | claude |
| slero-tegumentar | K | combining-form compound | claude |
| tibeto-birmană | K | combining-form compound | claude |
| topo-geodezice | K | combining-form compound | claude |
| tragico-comice | K | combining-form compound | claude |
| turco-fanariot | K | combining-form compound | claude |
| vegeto-vasculară | K | combining-form compound | claude |
| vetero-testamentar | K | combining-form compound | claude |
| vulvo-vaginite | K | combining-form compound | claude |
| est-germanul | K | compass + noun | claude |
| nord-europenii | K | compass + noun | claude |
| sud-esticii | K | compass + noun | claude |
| vest-europeni | K | compass + noun | claude |
| galben-crem | K | compound | claude+owner |
| nave-mamă | K | compound | claude+owner |
| bun-rămasul | K | dictionary fixed phrase | claude |
| buruiană-albă | K | dictionary fixed phrase | claude |
| caldă-caldă | K | dictionary fixed phrase | claude |
| car-wash | K | dictionary fixed phrase | claude |
| coada-mielului | K | dictionary fixed phrase | claude |
| floarea-nopții | K | dictionary fixed phrase | claude |
| juke-box | K | dictionary fixed phrase | claude |
| lemnul-domnului | K | dictionary fixed phrase | claude |
| lupul-bălții | K | dictionary fixed phrase | claude |
| mușcata-dracului | K | dictionary fixed phrase | claude |
| mă-ta | K | dictionary fixed phrase | claude |
| ori-care | K | dictionary fixed phrase | claude |
| rău-famat | K | dictionary fixed phrase | claude |
| rău-voitor | K | dictionary fixed phrase | claude |
| scris-citit | K | dictionary fixed phrase | claude |
| unghia-păsării | K | dictionary fixed phrase | claude |
| ușor-ușor | K | dictionary fixed phrase | claude |
| se-ntâlnește | K | elision | claude+owner |
| ș-apăi | K | elision | claude+owner |
| că-ncerci | K | elision / clitic | claude |
| daruindu-ma | K | elision / clitic | claude |
| ma-nchin | K | elision / clitic | claude |
| oră-ntreagă | K | elision / clitic | claude |
| pe-adâncile | K | elision / clitic | claude |
| pe-amandoi | K | elision / clitic | claude |
| se-nalta | K | elision / clitic | claude |
| se-ocupa | K | elision / clitic | claude |
| zile-ncoace | K | elision / clitic | claude |
| best-sellerului | K | loan + ending | claude+owner |
| raw-vegan | K | loan used in Romanian | claude |
| sex-tape | K | loan used in Romanian | claude |
| stand-uperi | K | loan used in Romanian | claude |
| u-boat | K | loan used in Romanian | claude+owner |
| vibe-coderi | K | loan used in Romanian | claude |
| casting-urile | K | loan/abbr + ending | claude |
| e-posterului | K | loan/abbr + ending | claude |
| hard-diskul | K | loan/abbr + ending | claude |
| kitsch-urile | K | loan/abbr + ending | claude |
| mega-ursul | K | loan/abbr + ending | claude |
| offline-uri | K | loan/abbr + ending | claude |
| pin-uri | K | loan/abbr + ending | claude |
| ping-pongiste | K | loan/abbr + ending | claude |
| pos-uri | K | loan/abbr + ending | claude |
| ransomware-uri | K | loan/abbr + ending | claude |
| review-urile | K | loan/abbr + ending | claude |
| saint-simonismului | K | loan/abbr + ending | claude |
| self-posturi | K | loan/abbr + ending | claude |
| sparring-partnerul | K | loan/abbr + ending | claude |
| srl-ist | K | loan/abbr + ending | claude |
| top-modelul | K | loan/abbr + ending | claude |
| tweet-uiesc | K | loan/abbr + ending | claude |
| u-boote | K | loan/abbr + ending | claude |
| veto-uri | K | loan/abbr + ending | claude |
| sud-coreencelor | K | loan/abbr + ending (owner type -lor) | owner |
| land-lui | K | loan/abbr + ending (owner type -lui) | owner |
| altceva-ul | K | loan/abbr + ending (owner type -ul) | owner |
| bajaj-ul | K | loan/abbr + ending (owner type -ul) | owner |
| bong-ul | K | loan/abbr + ending (owner type -ul) | owner |
| boogie-ul | K | loan/abbr + ending (owner type -ul) | owner |
| burrito-ul | K | loan/abbr + ending (owner type -ul) | owner |
| glades-ul | K | loan/abbr + ending (owner type -ul) | owner |
| hairline-ul | K | loan/abbr + ending (owner type -ul) | owner |
| hebei-ul | K | loan/abbr + ending (owner type -ul) | owner |
| id-ul | K | loan/abbr + ending (owner type -ul) | owner |
| impromptu-ul | K | loan/abbr + ending (owner type -ul) | owner |
| raw-ul | K | loan/abbr + ending (owner type -ul) | owner |
| reload-ul | K | loan/abbr + ending (owner type -ul) | owner |
| runway-ul | K | loan/abbr + ending (owner type -ul) | owner |
| site-ul | K | loan/abbr + ending (owner type -ul) | owner |
| slot-ul | K | loan/abbr + ending (owner type -ul) | owner |
| spp-ul | K | loan/abbr + ending (owner type -ul) | owner |
| toolbar-ul | K | loan/abbr + ending (owner type -ul) | owner |
| umts-ul | K | loan/abbr + ending (owner type -ul) | owner |
| volumatic-ul | K | loan/abbr + ending (owner type -ul) | owner |
| wp-ul | K | loan/abbr + ending (owner type -ul) | owner |
| acquis-ului | K | loan/abbr + ending (owner type -ului) | owner |
| fiv-ului | K | loan/abbr + ending (owner type -ului) | owner |
| linkage-ului | K | loan/abbr + ending (owner type -ului) | owner |
| nup-ului | K | loan/abbr + ending (owner type -ului) | owner |
| pnț-ului | K | loan/abbr + ending (owner type -ului) | owner |
| quatar-ului | K | loan/abbr + ending (owner type -ului) | owner |
| scriitorului-filosof | K | loan/abbr + ending (owner type -ului) | owner |
| www-ului | K | loan/abbr + ending (owner type -ului) | owner |
| ț-ului | K | loan/abbr + ending (owner type -ului) | owner |
| cabernet-urilor | K | loan/abbr + ending (owner type -urilor) | owner |
| idn-urilor | K | loan/abbr + ending (owner type -urilor) | owner |
| cale-cheie | K | noun + noun/apposition | claude |
| factorilor-cheie | K | noun + noun/apposition | claude |
| franceză-canadiană | K | noun + noun/apposition | claude |
| harta-program | K | noun + noun/apposition | claude |
| jurații-surpriză | K | noun + noun/apposition | claude |
| mașina-școală | K | noun + noun/apposition | claude |
| meciul-cheie | K | noun + noun/apposition | claude |
| obiectivele-cheie | K | noun + noun/apposition | claude |
| puștii-mitraliere | K | noun + noun/apposition | claude |
| păsările-maimuță | K | noun + noun/apposition | claude |
| secretară-recepționistă | K | noun + noun/apposition | claude |
| soldați-copii | K | noun + noun/apposition | claude |
| zonei-pivot | K | noun + noun/apposition | claude |
| centru-drepata | K | owner mark: relevant | owner |
| co-inculpați | K | owner mark: relevant | owner |
| consultanta-licitatii | K | owner mark: relevant | owner |
| iarba-tâlharului | K | owner mark: relevant | owner |
| om-broască | K | owner mark: relevant | owner |
| super-complexe | K | owner mark: relevant | owner |
| super-înghețat | K | owner mark: relevant | owner |
| ante-globalizare | K | prefix compound | claude |
| antero-posterioare | K | prefix compound | claude |
| anti-americanismul | K | prefix compound | claude |
| anti-lukașenko | K | prefix compound | claude |
| anti-poluare | K | prefix compound | claude+owner |
| auto-asigurate | K | prefix compound | claude |
| auto-vulnerabilizarea | K | prefix compound | claude |
| co-localizate | K | prefix compound | claude |
| dez-oligarhizare | K | prefix compound | claude |
| ex-nevasta | K | prefix compound | claude |
| extra-taxe | K | prefix compound | claude |
| extra-vehiculare | K | prefix compound | claude |
| intra-antrenament | K | prefix compound | claude |
| multi-colorată | K | prefix compound | claude |
| neo-fasciste | K | prefix compound | claude |
| non-occidentale | K | prefix compound | claude |
| non-strânsă | K | prefix compound | claude |
| post-criza | K | prefix compound | claude |
| post-dezastru | K | prefix compound | claude |
| post-psd | K | prefix compound | claude |
| post-script | K | prefix compound | claude |
| post-usr | K | prefix compound | claude |
| pre-tranzacționare | K | prefix compound | claude |
| pre-uzat | K | prefix compound | claude |
| pro-capitaliste | K | prefix compound | claude |
| pseudo-academic | K | prefix compound | claude |
| re-descoperit | K | prefix compound | claude |
| semi-parasit | K | prefix compound | claude |
| semi-salbatici | K | prefix compound | claude |
| stră-străbunicii | K | prefix compound | claude |
| supra-alimentare | K | prefix compound | claude |
| supra-nationala | K | prefix compound | claude |
| ex-șefa | K | prefix compound + article | claude+owner |
| neo-fasciștii | K | prefix compound + article | claude+owner |
| social-democrația | K | prefix compound + article | owner note |
| trimițându-li | K | verb + clitic | claude+owner |
