# Sursele wROdfreq

Tabelul de frecvențe se construiește din **șase corpusuri** de text românesc. Fiecare
este numărat separat, iar abia apoi rezultatele se combină (vezi [method.md](method.md)).
Acest document spune, pentru fiecare sursă: ce este, cât de mare e, din ce perioadă
vine, ce licență are și ce trebuie să știi când te bazezi pe ea.

Cifrele sunt cele din construcția `0.2.0` (octombrie 2026). Ele sunt citite din baza de
date, nu scrise de mână: `build_info()` și tabela `sources` din `wrodfreq.db` au
întotdeauna ultimul cuvânt.

## Pe scurt

| id | Sursă | Registru | Cuvinte numărate | Documente* | Prag** |
|---|---|---|---:|---:|---:|
| `web` | CulturaX (română) | pagini web | 23,80 mld | 40,3 mil. | −0,68 |
| `news` | CC-News (română) | presă | 2,21 mld | 6,6 mil. | 0,35 |
| `subs` | OpenSubtitles (română) | dialog din filme | 2,01 mld | 371 mil. rânduri | 0,40 |
| `social` | 15 subreddituri românești | conversații online | 0,54 mld | 13,7 mil. | 0,97 |
| `wiki` | Wikipedia în română | enciclopedic | 0,11 mld | 442 mii | 1,66 |
| `eu` | Europarl + DGT | formal, juridic | 0,08 mld | 4,9 mil. rânduri | 1,77 |
| | **Total** | | **28,75 mld** | | |

\* Ce înseamnă „document" diferă de la o sursă la alta — vezi mai jos la fiecare.
\*\* Pragul de încredere, pe scara Zipf: sub această valoare o sursă nu poate spune
nimic sigur despre un cuvânt (explicat în [method.md](method.md), secțiunea 5).

Toate cele șase sunt marcate **„contemporan"**. Prin asta înțelegem texte din ultimele
decenii, scrise cu ortografia de azi. O sursă „istorică" (de exemplu cărți din secolul
al XIX-lea) ar fi marcată separat și nu ar intra în valoarea implicită.

---

## `web` — CulturaX, partea în română

- **Ce este.** Pagini web românești, adunate din arhiva Common Crawl și curățate. Este
  baza statistică a tabelului: fără ea, cuvintele rare nu pot fi măsurate.
- **De unde.** `huggingface.co/datasets/uonlp/CulturaX`.
- **Mărime.** 40,3 milioane de documente, 23,8 miliarde de cuvinte.
- **Licență.** Mixtă. CulturaX pornește din mC4 (ODC-BY) și OSCAR (CC0); detaliile sunt
  în fișa setului de date. Noi păstrăm doar **numărători**, niciodată textul.
- **De știut.**
  - Este de departe cea mai mare sursă, de peste zece ori cât următoarea (`news`). Tocmai de
    aceea nu o lăsăm să domine rezultatul (vezi [method.md](method.md), secțiunea 6).
  - Fiind adunat automat, conține și murdărie: adrese web rupte în bucăți (care apar
    ca „cuvinte" lungi cu cratime), text în engleză și text scris fără diacritice.
    Cuvintele cu totul străine apar rar și, de obicei, doar în această sursă.
  - Din cauza mărimii, aproape 4,6 milioane dintre cuvintele din tabel sunt văzute
    sigur doar aici.

## `news` — CC-News, partea în română

- **Ce este.** Articole de presă românești din arhiva Common Crawl News. Este registrul
  pe care CulturaX îl reprezintă slab.
- **De unde.** `huggingface.co/datasets/stanford-oval/ccnews`, un set curățat și
  deduplicat. Păstrăm doar rândurile etichetate ca fiind în română.
- **Perioadă.** Iunie 2016 – iunie 2024. Atenție: data este data **colectării**, nu a
  publicării articolului.
- **Mărime.** 6,6 milioane de articole, 2,2 miliarde de cuvinte.
- **Licență.** Termenii Common Crawl (folosire pentru cercetare); vezi fișa setului.
- **De știut.** Limbaj de presă: nume de politicieni, firme, localități, sport. Multe
  nume proprii au aici frecvență mare, iar în celelalte surse una mică.

## `subs` — OpenSubtitles, partea în română

- **Ce este.** Subtitrări de filme și seriale. Cel mai apropiat lucru deschis de limba
  vorbită: replici scurte, expresii de zi cu zi, interjecții.
- **De unde.** OPUS, `opus.nlpl.eu/OpenSubtitles` (versiunea 2024).
- **Mărime.** 371 de milioane de rânduri, 2,0 miliarde de cuvinte.
- **Licență.** Nu există o licență formală. Drepturile rămân la autorii subtitrărilor;
  setul este colectat pentru cercetare (Lison și Tiedemann, 2016). Noi păstrăm doar
  numărători.
- **De știut.**
  - „Document" înseamnă aici **un rând de subtitrare**, nu un film. Exportul nu păstrează
    granițele dintre filme. Deci numărul de documente nu spune în câți filme apare un
    cuvânt.
  - Multe subtitrări sunt traduceri, deci unele expresii sună a traducere.
  - Data filmelor nu se păstrează; tratați sursa ca pe un instantaneu fără dată.

## `social` — 15 subreddituri românești

- **Ce este.** Comentarii și postări din: r/Romania, r/CasualRO, r/programare,
  r/Bucuresti, r/AskRomania, r/moldova, r/cluj, r/Iasi, r/Sibiu, r/Timisoara, r/Oradea,
  r/Craiova, r/Brasov, r/Constanta și r/romani. Este singurul registru colocvial scris:
  cum scriu oamenii când nu se gândesc la ortografie.
- **De unde.** Arhiva Arctic Shift (`arctic-shift.photon-reddit.com`), descărcată cu
  `build/fetch_social.py`. Istoricul r/Romania începe în martie 2010.
- **Mărime.** 13,7 milioane de comentarii și postări, 0,54 miliarde de cuvinte.
- **Licență.** Fără licență formală de redistribuire; conținut public, adunat pentru
  cercetare. Din el se păstrează **doar numărători** de cuvinte, niciodată textul sau
  numele autorilor.
- **Cum a fost curățat.**
  - Se scot adresele web, trimiterile `u/…` și `r/…`, codul și legăturile Markdown.
  - Se scot mesajele șterse, cele ale roboților și ale echipelor de moderare.
  - Se **filtrează limba**: mesajele în engleză sau fără semnale de română sunt lăsate
    deoparte. Filtrul caută cuvinte-unealtă românești
    (`și`, `sunt`, `pentru`…) și litere cu diacritice, și le cântărește față de cele
    englezești. Dintr-un eșantion de 1,46 milioane de mesaje, 72,7% au rămas;
    restul erau în altă limbă, fără semnal de română, șterse, de roboți sau goale.
- **Independența.** Cifra de „documente" numără **mesaje**, nu autori (ca să rămână
  comparabilă cu celelalte surse). Independența se măsoară separat: **264.357 de autori
  distincți**, iar primii 100 de autori scriu 9,5% din mesaje. Într-o măsurătoare pe primele patru subreddituri, circa 70% dintre autorii
  fiecăruia nu scriu în r/Romania, deci nu e o singură comunitate sub 15 nume.
- **De știut.**
  - Vocabularul e colocvial și plin de englezisme. Asta e voit: e ce scriu oamenii.
  - Mulți scriu **fără diacritice** („sa", „ca", „fata"). Așa ajung în tabel și formele
    fără diacritice, cu frecvență reală.
  - Din filtru scapă uneori propoziții mixte sau citate în altă limbă. Cuvintele engleze
    rămase au frecvențe asemănătoare cu cele din celelalte surse și nu schimbă
    rezultatul.
  - Mesajele foarte scurte, fără niciun cuvânt-unealtă (de exemplu „Exact."), sunt
    omise. Sunt aproximativ 6,6% din mesajele citite.

## `wiki` — Wikipedia în română

- **Ce este.** Articole de enciclopedie: limbaj îngrijit, multe nume proprii, termeni
  tehnici și științifici.
- **De unde.** `huggingface.co/datasets/wikimedia/wikipedia`, instantaneul din 1
  noiembrie 2023.
- **Mărime.** 442.389 de articole, 110,5 milioane de cuvinte.
- **Licență.** CC BY-SA 4.0. Se cere menționarea sursei.
- **De știut.**
  - Fiind cea mai mică după `eu`, are pragul cel mai ridicat: multe cuvinte rare sunt
    pur și simplu invizibile aici, iar asta e normal.
  - Conține multe nume străine cu litere neromânești (`Düsseldorf`, `Zürich`); vezi
    secțiunea despre limite din [method.md](method.md).

## `eu` — Europarl și DGT

- **Ce este.** Două colecții europene: dezbateri din Parlamentul European (vorbite, apoi
  transcrise) și texte juridice ale Comisiei Europene (DGT, memorie de traduceri).
  Limbaj formal, birocratic.
- **De unde.** OPUS: Europarl v8 și DGT v2021.
- **Mărime.** Aproximativ 4,9 milioane de rânduri, 84,7 milioane de cuvinte. Cea mai
  mică sursă.
- **Licență.** Europarl: liber pentru cercetare (Koehn, 2005). DGT: decizia
  2011/833/UE a Comisiei, reutilizare permisă cu menționarea sursei.
- **De știut.**
  - Sunt texte **traduse** din alte limbi europene, deci limba e ușor „de traducere".
  - Spune bine dacă un cuvânt există în limbajul formal. Nu spune nimic despre limba de
    zi cu zi.
  - „Document" înseamnă rând sau paragraf, nu actul întreg.

---

## Ce lipsește, și de ce

- **Cărți și literatură.** Wikisource și Gutenberg în română sunt din secolul al
  XIX-lea și începutul secolului XX. Ar trage în sus formele vechi și nu ar muta nicio
  cifră din valoarea contemporană, așa că nu sunt în panou.
- **CoRoLa** (corpusul de referință al limbii române). Acoperă 1945 până azi și listele
  lui de frecvențe nu au date. Cu ortografia dinainte de 1953 ar apărea de 50–110 de ori
  prea des, față de CulturaX (`condițiune`, `comisiune`).
- **Twitter/X** și **Google Books**: primul nu mai oferă date deschise, al doilea nu are
  română.
- **Limba vorbită, înregistrată.** Cel mai apropiat înlocuitor este `subs`, cu limitele
  de mai sus.

## Cum adăugăm o sursă

O sursă nouă are un script `build/ingest_<id>.py` care folosește **același tokenizator**
(`wrodfreq/tokenizer.py`) ca toate celelalte; fără asta, sursele nu pot fi combinate.
Ea primește o perioadă (`contemporan` / `mixt` / `istoric`), un prag calculat din mărimea
ei și o notă despre ce înseamnă „document" la ea. Odată cu panoul se schimbă versiunea
`MINOR` (de la `0.2` la `0.3`).
