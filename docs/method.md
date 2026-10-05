# Metodologia wROdfreq

Acest document explică, pas cu pas și fără termeni de specialitate, cum se obține
numărul pe care îl vezi când scrii `zipf_frequency('cuvânt')`. Cine vrea să cite tabelul
într-o lucrare găsește la final ce să scrie. Sursele sunt descrise în
[sources.md](sources.md).

Versiunea descrisă aici: **0.2.0**.

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

Tabelul conține **6.064.995 de cuvinte**. Câte surse le văd sigur:

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
mare (`tinereță`: 128) arată că familia are forme comune cu cuvinte mult mai frecvente,
așa că trebuie citită cu grijă.

Lema se caută după forma din dicționar (`înmărmuri`, nu `înmărmurit`). Dacă
cuvântul nu e o lemă, sau dacă pachetul nu conține stratul de leme,
`lemma_frequency` întoarce valoarea obișnuită, `zipf_frequency`.

Stratul de leme este **separat** de numărătorile pe forme. Se poate aduna, dar nu se
poate desface, așa că nu amestecăm cele două.

> **Licență.** Harta formelor vine din datele DEX Online. Condițiile de redistribuire
> sunt în curs de clarificare cu DEX Online; când vor fi clare, vor fi trecute aici.

## 6. Cum verificăm că tabelul e bun

`build/validate.py` rulează la fiecare construcție și **oprește construcția** dacă ceva
nu e în regulă:

1. **Cuvintele-unealtă sunt sus.** `de`, `și`, `la`, `un`, `cu` trebuie să aibă Zipf între
   6,0 și 7,5. Dacă nu, numitorul e greșit. Este cea mai importantă verificare. Acum:
   `de` 7,68, `și` 7,34, `la` 7,23, `un` 6,94, `cu` 7,08.
2. **Acord cu `wordfreq`.** Luăm perechile de cuvinte pe care `wordfreq` le vede clar
   diferite (cel puțin 0,3 pe scara Zipf) și verificăm că le ordonăm la fel. Cerința:
   cel puțin 93%. Acum: **94,8%**. Valorile noastre ies în medie cu 0,20 mai mici decât
   cele din `wordfreq`.
3. **59 de perechi alese de mână** (de exemplu, un cuvânt comun trebuie să fie peste unul
   rar). Acum: toate corecte.
4. **Acoperirea DEX.** Cel puțin 95% din lemele cu frecvență peste 0,8 trebuie să apară.
   Acum: 97,7%. Dacă scade, înseamnă că un filtru cu dicționar s-a strecurat înapoi.
5. **Raportul de dezacord** (nu trece/pică): primele 100 de cuvinte după `spread`, de
   citit cu ochii. Aici se văd nume proprii, termeni din domenii și forme fără diacritice.
6. **Reconstruire identică.** Dacă rulezi din nou pașii 2–5 pe aceleași numărători,
   fișierul de date iese identic, bit cu bit.

Coeficientul Spearman față de `wordfreq` este afișat (acum 0,851) dar **nu este o
condiție**: între cuvinte cu frecvențe foarte apropiate, ordinea depinde de zgomot (și
în `wordfreq`), nu de calitate.

Pe lângă acestea, un set de teste (229) rulează la fiecare modificare; vezi `.github/`.

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
- **Adrese web rupte în cuvinte.** Aproximativ 23 de mii de intrări sunt șiruri lungi cu
  cratime, luate din adrese web. Aproape toate sunt la Zipf în jur de 0 sau sub, deci nu afectează
  cuvintele obișnuite.
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

## 8. Versiuni și citare

Versiunea `MAJOR.MINOR.PATCH`. **MINOR se schimbă când se schimbă panoul de surse**.
Pachetul îți spune exact ce construcție folosești:

```python
>>> import wrodfreq
>>> wrodfreq.build_info()
{'version': '0.2.0', 'sources': ['eu', 'news', 'social', 'subs', 'web', 'wiki'],
 'built': '2026-10-05'}
```

Într-o lucrare, scrie versiunea și lista surselor din `build_info()`, de exemplu:

> wROdfreq 0.2.0 (construit la 5 octombrie 2026; surse: eu, news, social, subs, web,
> wiki), https://github.com/gov2-ro/wrodfreq

## 9. Cum se reface totul de la zero

```bash
python build/ingest_<sursă>.py     # câte unul pentru wiki, web, news, subs, eu, social
python build/compute_zipf.py       # valoarea Zipf și pragul, pe sursă
python build/merge.py              # combinarea surselor
python build/build_lemma_layer.py  # stratul de leme (are nevoie de harta DEX)
python build/build_package.py      # fișierele de date din pachet
python build/validate.py           # verificările din secțiunea 6
```

Colectarea surselor mari durează zile; fiecare script poate fi oprit și reluat din
punctul în care a rămas.
