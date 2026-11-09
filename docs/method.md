# Metodologia wROdfreq

- [1. Ce este și ce nu este](#1-ce-este-și-ce-nu-este)
- [2. Scara Zipf, pe scurt](#2-scara-zipf-pe-scurt)
- [3. Pașii, în ordine](#3-pașii-în-ordine)
  - [Pasul 1 — Împărțim textul în cuvinte (tokenizare)](#pasul-1--împărțim-textul-în-cuvinte-tokenizare)
  - [Pasul 2 — Numărăm, pe sursă](#pasul-2--numărăm-pe-sursă)
  - [Pasul 3 — Calculăm valoarea Zipf, pe sursă](#pasul-3--calculăm-valoarea-zipf-pe-sursă)
  - [Pasul 4 — Cine are voie să vorbească? (pragul)](#pasul-4--cine-are-voie-să-vorbească-pragul)
  - [Pasul 5 — Combinăm sursele](#pasul-5--combinăm-sursele)
- [4. Ce primești pentru un cuvânt](#4-ce-primești-pentru-un-cuvânt)
- [5. Stratul de leme](#5-stratul-de-leme)
- [6. Cum verificăm că tabelul e bun](#6-cum-verificăm-că-tabelul-e-bun)
- [7. Limite cunoscute](#7-limite-cunoscute)
- [8. Versiuni și citare](#8-versiuni-și-citare)
- [9. Cum se reface totul de la zero](#9-cum-se-reface-totul-de-la-zero)
- [10. Corecturile din 0.2.1](#10-corecturile-din-021)

Acest document explică, pas cu pas și fără termeni de specialitate, cum se obține
numărul pe care îl vezi când scrii `zipf_frequency('cuvânt')`. Cine vrea să cite tabelul
într-o lucrare găsește la final ce să scrie. Sursele sunt descrise în
[sources.md](sources.md).

Versiunea descrisă aici: **0.2.1** (construcția `eb23d6fe219530ceb9bc65caef6475dc`).
Ce s-a corectat față de 0.2.0 este în [secțiunea 10](#10-corecturile-din-021).

## 1. Ce este și ce nu este

wROdfreq răspunde la o singură întrebare: **cât de des apare un cuvânt în româna de
azi?** Pentru fiecare cuvânt dă un număr, plus câteva semne care spun cât de sigur e acel
număr.

Nu este: un lematizator (nu află singur forma de dicționar a unui cuvânt), un analizor
de părți de vorbire, un corpus de text și nici o listă de „cuvinte corecte". Un cuvânt
scris greșit des apare în tabel, pentru că tabelul măsoară ce se scrie, nu ce e corect.

## 2. Scara Zipf, pe scurt

Valoarea unui cuvânt este **logaritmul în baza 10 al numărului de apariții la un
miliard de cuvinte**. Câteva repere:

| Zipf | Cam cât de des apare | Exemplu |
|---:|---|---|
| 7 | o dată la 100 de cuvinte | `de`, `a`, `în`, `și` |
| 6 | o dată la 1.000 | `unde` (5,98), `sunt` (6,61) |
| 5 | o dată la 10.000 | `casă` (5,07) |
| 4 | o dată la 100.000 | cuvinte destul de rare |
| 3 | o dată la un milion | cuvinte rare |
| 1–2 | o dată la zeci/sute de milioane | nume rare, termeni tehnici |

Fiecare unitate înseamnă de zece ori mai des. Tabelul rotunjește la **două zecimale**; a
treia ar fi doar zgomot.

Scara este aceeași ca la proiectul `wordfreq`, așa că numerele se pot compara.

## 3. Pașii, în ordine

Fiecare pas este un script separat din `build/`. Oricare poate fi rulat din nou oricând,
iar rezultatul este identic, bit cu bit.

![Etapele de construcție ale wROdfreq. Șase corpusuri deschise sunt numărate separat în source_counts și sources. compute_zipf.py scrie source_zipf, tot pe sursă. merge.py obține tabela merged fără să înlocuiască tabelele surselor. O ramură opțională, build_lemma_layer.py, folosește harta DEX și scrie tabela separată lemma_zipf. build_package.py scrie trei fișiere de date pentru pachetul pip wrodfreq. validate.py rulează ultimul, citește tabelele și două intrări de referință și oprește construcția dacă o verificare pică. Baza de date SQLite brută este a doua ieșire.](figures/pipeline-ro.png)

`diagram-designer` agent by [iello](https://github.com/iello/)

### Pasul 1 — Împărțim textul în cuvinte (tokenizare)

Un singur program, `wrodfreq/tokenizer.py`, face asta pentru toate sursele. Dacă două
surse ar tăia textul diferit, rezultatele lor nu s-ar mai putea combina, și nimeni nu ar
observa. De aceea există un test care verifică ca toate sursele să dea exact aceleași
cuvinte pentru același text.

Reguli:

- Se pune totul cu litere mici. Se unifică `ş` și `ţ` (cu sedilă) în `ș` și `ț` (cu virgulă).
- Un cuvânt este un șir de litere românești (`a-z`, `ă`, `â`, `î`, `ș`, `ț`), eventual cu
  cratimă sau apostrof în interior: `cluj-napoca`, `mass-media`.
- Formele elidate se despart: `într-o` devine `într` și `o`.
- **Cuvintele scurte se numără.** Cele mai frecvente cuvinte românești au una sau două
  litere (`a`, `o`, `de`, `la`, `cu`, `nu`, `se`). Dacă le-am arunca, toate procentele ar
  ieși umflate.
- **Numerele nu se numără**, nici la numărător, nici la numitor.
- **Nu filtrăm cu un dicționar.** Se numără orice cuvânt, ca să apară și `laptop`,
  `selfie`, `covid`, `clujean`. Un filtru cu dicționarul le-ar ascunde.

### Pasul 2 — Numărăm, pe sursă

Pentru fiecare sursă și fiecare cuvânt păstrăm de câte ori apare și în câte documente.
Păstrăm și **numărul total de cuvinte** al sursei, adică numitorul. Numărătorile pe
fiecare sursă rămân păstrate: tabelul final se obține din ele, nu le înlocuiește.

### Pasul 3 — Calculăm valoarea Zipf, pe sursă

```
zipf = log10( apariții / total cuvinte din sursă × 1.000.000.000 )
```

### Pasul 4 — Cine are voie să vorbească? (pragul)

O sursă contează pentru un cuvânt **doar dacă îl vede de cel puțin 5 ori**. Sub 5, sursa
**se abține**: nu spune „rar", spune „nu știu". Nu raportează niciodată zero.

Motivul: zero este o afirmație („cuvântul e rar"), dar un corpus mic care nu a văzut un
cuvânt, de cele mai multe ori e doar mic. Dacă am trece zerouri în medie, Wikipedia
(110 milioane de cuvinte) ar trage în jos un cuvânt pe care CulturaX (23,8 miliarde) l-a
măsurat bine.

Pragul se **calculează din mărimea fiecărei surse**, nu se fixează: 5 apariții în
110 milioane de cuvinte înseamnă altceva decât 5 în 23,8 miliarde. În construcția
curentă, pragurile sunt între −0,68 (web) și 1,77 (eu); valorile sunt în tabela `sources`.

### Pasul 5 — Combinăm sursele

Se iau doar sursele marcate „contemporan" care au văzut cuvântul de cel puțin 5 ori.

| Câte surse îl văd sigur | Ce facem |
|---|---|
| 5 sau 6 | **Media tăiată:** scoatem cea mai mare și cea mai mică valoare, facem media celorlalte |
| 3 sau 4 | Media simplă |
| 2 | Media simplă (încredere scăzută) |
| 1 | Valoarea acelei surse (încredere scăzută) |
| 0 | Cuvântul nu intră în tabel |

Două decizii care merită explicate:

- **Nu tăiem sub 5 surse.** Din 3 valori, scoțând cea mare și cea mică, rămâne una: ar
  însemna „alege corpusul din mijloc", ceea ce e mai prost decât media simplă.
- **Nu cântărim sursele după mărime.** CulturaX este de peste zece ori mai mare decât
  următoarea sursă. Dacă l-am cântări după mărime, tabelul ar deveni „CulturaX, cu pași
  în plus", iar tăierea nu ar mai avea rost.

Baza de date conține **6.064.995 de cuvinte**; din pachet lipsesc 23.453 de șiruri cu trei sau mai multe cratime (vezi secțiunea 7), deci pachetul conține **6.041.542**. Câte surse văd sigur cuvintele din baza de date:

| Surse sigure | Cuvinte |
|---:|---:|
| 6 | 62.937 |
| 5 | 93.356 |
| 4 | 154.887 |
| 3 | 222.973 |
| 2 | 669.070 |
| 1 | 4.861.772 |

Cele mai multe cuvinte (80%) sunt cuvinte rare, văzute sigur de o singură sursă, aproape
întotdeauna web. Despre ele tabelul spune „există și apare cam așa de des", nu mai mult.
Alte 27,4 milioane de forme au fost văzute, dar sub pragul tuturor surselor, și nu apar.

## 4. Ce primești pentru un cuvânt

`frequency_detail('birjă')` întoarce nu un număr, ci patru:

- **`zipf`** — valoarea combinată, descrisă mai sus.
- **`n_reliable`** — câte surse l-au văzut sigur (cel puțin 5 ori). Este o **măsură de
  confirmare**: un cuvânt văzut sigur de 6 surse e cu adevărat în uz; unul văzut de o
  singură sursă poate fi o greșeală de scriere sau un nume rar.
- **`n_attesting`** — câte surse l-au văzut măcar o dată. Diferența față de `n_reliable`
  arată zona „există, dar e rar".
- **`spread`** — diferența dintre cea mai mare și cea mai mică valoare dintre surse. Un
  `spread` mare înseamnă că sursele nu sunt de acord: cuvântul ține de un registru
  (`iohannis`: 5,37 în presă, 1,11 în subtitrări) sau se schimbă în timp.

Și `by_source('birjă')` arată valoarea fiecărei surse; `None` înseamnă „s-a abținut", nu
„zero".

## 5. Stratul de leme

În română, un cuvânt are multe forme. Verbul `a înmărmuri` apare în text în forme
ca `înmărmurit`, `înmărmurește`, `înmărmuriți`. Dacă numeri doar forma din dicționar,
verbul pare aproape dispărut: `înmărmuri` singur are Zipf **0,81**, iar toată
familia lui **1,66**.

`lemma_frequency('înmărmuri')` adună frecvența tuturor formelor unui cuvânt din
dicționar (o „lemă"), folosind harta formelor din DEX Online (aproximativ 318 mii
de lexeme și 2,3 milioane de forme). Tabelul de leme are **180.569 de intrări**.

Trei reguli fac diferența între o hartă bună și una greșită:

1. **O formă ambiguă se împarte, nu se dublează.** Aproximativ 12% dintre forme aparțin
   mai multor leme. `vești` aparține lui `veste` (o știre) și lui `veșcă` (un obiect
   rar). Dacă i-am da fiecăreia toate aparițiile, `veșcă` ar primi tot ce are `veste`.
2. **Împărțirea se face după cât de frecvent este fiecare candidat.** Pentru `vești`,
   `veste` este de mii de ori mai frecventă decât `veșcă`, așa că primește aproape tot.
3. **Pentru documente se ia maximul formelor, nu suma.** Un document în care apar două
   forme ale aceleiași leme nu se numără de două ori.

`lemma_detail(cuvânt)` mai dă și numărul de forme, valoarea formei din dicționar luată
singură și **`family_ratio`**: de câte ori ar fi fost mai mare totalul familiei dacă formele
ambigue nu s-ar fi împărțit. Valoarea 1 înseamnă o familie fără ambiguitate. O valoare
mare arată că familia are forme comune cu cuvinte mult mai frecvente, așa că trebuie
citită cu grijă.

Cele două totaluri se compară **pe aceleași surse**: sursele în care lema, după
împărțire, trece de pragul de 5 apariții (`docs/decisions/ADR-005`). Totalul fără
împărțire este cel puțin la fel de mare în fiecare sursă, deci raportul este cel puțin 1.
În 0.2.0, totalul fără împărțire folosea și surse în plus, mari și cu valori mici, care îl
trăgeau în jos. Așa au ieșit 2.382 de rapoarte sub 1 (de exemplu `detoxica`: 0,03). Din
0.2.1 nu mai există niciunul.

Lema se caută după forma din dicționar (`înmărmuri`, nu `înmărmurit`). Dacă
cuvântul nu e o lemă, sau dacă pachetul nu conține stratul de leme,
`lemma_frequency` întoarce valoarea obișnuită, `zipf_frequency`.

Stratul de leme este **separat** de numărătorile pe forme. Se poate aduna, dar nu se
poate desface, așa că nu amestecăm cele două.

> **Licență.** Harta formelor vine din datele DEX Online. Condițiile de redistribuire
> sunt în curs de clarificare cu DEX Online; când vor fi clare, vor fi trecute aici.

## 6. Cum verificăm că tabelul e bun

`build/validate.py` rulează la fiecare construcție și **oprește construcția** dacă ceva
nu e în regulă. Din 0.2.1 verificarea **doar citește**: nu modifică baza de date și nu
reface nicio tabelă. Pentru o versiune publică rulează în modul strict (`--strict`). În
acest mod, o verificare care nu poate rula (de exemplu, lipsește `wordfreq`) pică, nu
este sărită. Pe lângă cele de mai jos, verificarea strictă controlează:

- **Integritatea.** Pentru fiecare sursă, suma aparițiilor este egală cu numitorul.
  Fiecare valoare derivată se recalculează din numărători și se compară cu cea din bază.
- **Proveniența.** Baza de date este exact starea descrisă de manifestul construcției:
  aceleași surse, aceleași numărători (după amprenta lor).
- **Fișierele livrate.** Fiecare fișier din pachet și din TSV are amprenta din manifest și
  conține exact rândurile și valorile din baza de date.

Verificările de calitate (măsurate pe 0.2.1):

1. **Cuvintele-unealtă sunt sus.** `de`, `și`, `la`, `un`, `cu` trebuie să aibă Zipf între
   6,0 și 8,0. Dacă nu, numitorul e greșit. Este cea mai importantă verificare. Acum:
   `de` 7,68, `și` 7,34, `la` 7,23, `un` 6,93, `cu` 7,07.
2. **Acord cu `wordfreq`.** Luăm perechile de cuvinte pe care `wordfreq` le vede clar
   diferite (cel puțin 0,3 pe scara Zipf) și verificăm că le ordonăm la fel. Cerința:
   cel puțin 93%. Acum: **94,8%**. Valorile noastre ies în medie cu 0,20 mai mici decât
   cele din `wordfreq`. În plus, cel puțin 99% din cuvintele cu Zipf ≥ 3 din `wordfreq`
   trebuie să fie în tabel. Acum: 43.095 din 43.413 (99,27%). Referința este `wordfreq`
   3.1.1; raportul îi păstrează amprenta.
3. **59 de perechi alese de mână** (de exemplu, un cuvânt comun trebuie să fie peste unul
   rar). Acum: toate prezente și toate corecte. O pereche care lipsește pică.
4. **Acoperirea DEX.** Cel puțin 95% din lemele cu frecvență peste 0,8 trebuie să apară.
   Acum: 97,7%. Dacă scade, înseamnă că un filtru cu dicționar s-a strecurat înapoi.
5. **Raportul de dezacord** (nu trece/pică): primele 100 de cuvinte după `spread`, de
   citit cu ochii. Aici se văd nume proprii, termeni din domenii și forme fără diacritice.
6. **Reconstruire identică.** Comanda separată `build/check_rebuild.py` copiază doar
   numărătorile într-o bază nouă, reface pașii 2–5 și compară rezultatul cu cel verificat.
   Fișierele trebuie să fie identice bit cu bit, iar tabelele, identice ca rânduri.

Coeficientul Spearman față de `wordfreq` este afișat (acum 0,851) dar **nu este o
condiție**: între cuvinte cu frecvențe foarte apropiate, ordinea depinde de zgomot (și
în `wordfreq`), nu de calitate.

Pe lângă acestea, un set de teste (peste 400, fără date reale) rulează la fiecare
modificare; vezi `.github/`.

## 7. Limite cunoscute

Tabelul nu e perfect. Iată ce știm și nu am rezolvat.

- **Litere neromânești.** Tokenizatorul cunoaște doar literele românești, deci un cuvânt
  străin cu diacritice se rupe: `Düsseldorf` devine `d` + `sseldorf`. Afectează circa 0,3%
  dintre cuvinte, în coada rară (doar 52 de intrări trec de Zipf 3). Am măsurat și am
  decis să nu lărgim alfabetul acum (`docs/decisions/ADR-002`).
- **Diacritice scrise greșit.** O parte mare dintre „literele străine" din `web` sunt
  de fapt română scrisă cu tastatură străină (`dupã`, `cã`). Ele apar ca fragmente.
- **Forme fără diacritice.** Mulți scriu `sa` în loc de `să`. Tabelul le păstrează ca
  forme separate, cu frecvența lor reală; nu le unificăm.
- **Adrese web rupte în cuvinte.** Aproximativ 23 de mii de intrări sunt șiruri cu trei sau
  mai multe cratime (adrese web, titluri în engleză, denumiri chimice, liste de localități).
  Sunt în baza de date, dar **nu sunt în pachet**: `zipf_frequency('state-of-the-art')`
  dă 0,0. Cuvintele cu una sau două cratime (`cluj-napoca`, `play-off-ul`) rămân, dar
  și printre ele sunt multe șiruri fără valoare, nemăsurate încă.
- **Engleză în text.** Toate sursele conțin cuvinte englezești (`the` are 5,0–6,4 în
  fiecare). Nu le scoatem: tabelul măsoară ce apare, nu ce e românesc.
- **Surse traduse.** `subs` și `eu` sunt în mare parte traduceri.
- **`documents` nu înseamnă același lucru peste tot.** La `subs` și `eu` e un rând de
  text, la `social` un mesaj, la `wiki` și `web` un document. Nu e o dovadă de
  independență; vezi [sources.md](sources.md).
- **Numerele nu se numără.** `zipf_frequency('123')` dă valoarea minimă, nu ce dă
  `wordfreq`.
- **Nu e lematizator.** Stratul de leme pornește de la forma din dicționar. Nu ghicește
  forma unui cuvânt.
- **Perioada.** Sursele sunt texte din ultimele decenii. Tabelul nu spune nimic despre
  româna veche sau despre cum se scria acum o sută de ani.
- **`n_reliable` numără surse, nu dovedește independența lor.** Sursele se suprapun în
  parte: site-uri de știri apar și în `web`, iar pe Reddit se citează știri. Când o singură
  sursă e sigură, `spread` este 0. Valoarea 0 nu înseamnă că sursele sunt de acord, ci că
  nu există cu ce compara.
- **Proveniența celor cinci surse vechi.** Pentru `eu`, `news`, `subs`, `web` și `wiki`,
  numărătorile din 0.2.0 sunt păstrate și verificate după amprentă. Fișierele brute din
  care au fost numărate nu au fost înregistrate atunci, deci verificarea nu dovedește o
  renumărare de la zero. Doar `social` a fost renumărat din fișierele brute în 0.2.1.

## 8. Versiuni și citare

Versiunea `MAJOR.MINOR.PATCH`. **MINOR se schimbă când se schimbă panoul de surse**.
Pachetul îți spune exact ce construcție folosești:

```python
>>> import wrodfreq
>>> info = wrodfreq.build_info()
>>> info['version'], info['build_id'], info['built']
('0.2.1', 'eb23d6fe219530ceb9bc65caef6475dc', '2026-11-09')
>>> info['sources']
['eu', 'news', 'social', 'subs', 'web', 'wiki']
```

`build_id` identifică exact construcția: aceleași numărători, același cod, aceeași dată de
construcție dau același `build_id` și aceleași fișiere, bit cu bit. Într-o lucrare, scrie
versiunea, `build_id` și lista surselor, de exemplu:

> wROdfreq 0.2.1, construcția eb23d6fe219530ceb9bc65caef6475dc (9 noiembrie 2026; surse:
> eu, news, social, subs, web, wiki), https://github.com/gov2-ro/wrodfreq

## 9. Cum se reface totul de la zero

```bash
python build/ingest_<sursă>.py --db STAGING.db   # câte unul pentru wiki, web, news, subs, eu, social
python build/compute_zipf.py --db STAGING.db     # valoarea Zipf și pragul, pe sursă
python build/merge.py --db STAGING.db            # combinarea surselor
python build/build_lemma_layer.py --db STAGING.db   # stratul de leme (are nevoie de harta DEX)
python build/build_manifest.py --db STAGING.db --build-date AAAA-LL-ZZ --out inputs.json
python build/build_package.py --db STAGING.db --manifest inputs.json --out-root builds
python build/validate.py --db STAGING.db --generation builds/<build_id> --strict --report r.json
python build/check_rebuild.py --db STAGING.db --generation builds/<build_id> --work-dir tmp
```

Colectarea surselor mari durează zile. Fiecare script poate fi oprit și reluat din
punctul în care a rămas: poziția se salvează în aceeași tranzacție cu numărătorile, deci
o oprire nu pierde și nu dublează nimic. Pașii completi, cu promovarea și revenirea, sunt
în `docs/release-runbook.md`.

## 10. Corecturile din 0.2.1

Pe 9 noiembrie 2026, un audit al construcției 0.2.0 a găsit două erori în date. Toate
testele și verificările de calitate treceau totuși.

1. **`social` era numărat parțial de două ori.** O rulare de probă scrisese în baza
   reală, iar rularea completă a adunat peste ea. Suma aparițiilor era 566.678.860, iar
   numitorul 536.523.553. Diferența, 30.155.307, este exact rularea de probă. În 0.2.1,
   `social` a fost renumărat din cele 30 de fișiere brute păstrate, cu același
   tokenizator și aceleași filtre. Rezultatul este exact 536.523.553 de cuvinte în
   13.717.928 de mesaje.
2. **2.382 de leme aveau `family_ratio` sub 1.** Explicația și corectura sunt în
   secțiunea 5.

Ce s-a schimbat față de 0.2.0 (măsurat, `compare_builds.py`):

- **`social`**: valorile Zipf ale cuvintelor doar au scăzut (median −0,015; cel mult
  −0,30, adică o numărătoare dublată în întregime). 13.955 de cuvinte nu mai trec pragul
  în `social`.
- **Tabelul combinat**: 2.036 de cuvinte ies din tabel (toate erau sigure doar în
  `social`, chiar peste prag) și niciunul nu intră. 11.919 cuvinte au acum o sursă sigură
  mai puțin. 158.480 de valori se schimbă cu mai mult de 0,005; pentru jumătate dintre
  cuvinte schimbarea este 0. Schimbările mari (până la ±0,93) apar acolo unde `social` era
  una din doar două surse. În pachet sunt acum 6.039.510 cuvinte (înainte 6.041.542).
- **Leme**: 180.565 (înainte 180.569); 25.455 de valori se schimbă cu mai mult de 0,005;
  rapoarte `family_ratio` sub 1: 0 (înainte 2.382).

Celelalte cinci surse nu s-au schimbat: amprentele numărătorilor lor sunt identice cu
cele din 0.2.0. Construcția a schimbat și felul în care se lucrează, ca eroarea să nu se
repete: poziția de reluare se salvează odată cu numărătorile, o rulare de probă scrie
mereu într-o bază separată, iar verificarea doar citește (`docs/decisions/ADR-004`,
`ADR-005`, `ADR-006`).
