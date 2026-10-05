# Text de trimis către DEX Online

Ciornă de mesaj, de trimis de pe adresa proiectului. Completați părțile dintre
paranteze drepte. Nu s-a trimis nimic.

---

**Subiect:** Cerere de acord pentru folosirea formelor flexionare din DEX Online într-un tabel deschis de frecvențe

Bună ziua,

Numele meu este [nume], iar proiectul meu se numește **wROdfreq**: un tabel deschis cu
frecvența cuvintelor din limba română, construit din șase corpusuri de text și publicat
ca pachet Python (`pip install wrodfreq`) și ca bază de date pentru cercetători.
Codul este la https://github.com/gov2-ro/wrodfreq.

Pentru a calcula frecvența unui cuvânt împreună cu toate formele lui (de exemplu,
frecvența verbului *a înmărmuri* adunată din *înmărmurit*, *înmărmurește* etc.) am folosit
datele DEX Online: din baza de date publică am extras lista de lexeme și formele lor
flexionare (aproximativ 318.000 de lexeme și 2,3 milioane de forme) și legătura dintre
fiecare formă și lema ei.

Aș dori să vă cer acordul pentru următoarele, separat:

1. **Valorile derivate.** În pachet și în baza de date publicăm doar un număr de frecvență
   pentru fiecare lemă (aproximativ 180.000 de leme), calculat de noi. Nu publicăm
   definiții, texte sau alt conținut al dicționarului. Este în regulă?
2. **Harta formă → lemă.** Pentru reproductibilitate, am dori să publicăm ca fișier
   separat (SQLite, într-o arhivă a proiectului) harta formelor flexionare către leme
   extrasă din DEX Online. Este permis? Dacă da, sub ce licență și cu ce mențiune?
   Dacă nu, putem publica doar scriptul de extragere, iar utilizatorii își construiesc
   singuri fișierul din arhiva DEX Online.
3. **Atribuirea.** Cum doriți să fiți menționați în documentație și în pachet?

Pentru claritate: termenii de licență ai datelor DEX Online nu ne sunt pe deplin limpezi
pentru acest tip de folosire, de aceea vă scriem înainte de a publica. Până la răspunsul
dumneavoastră nu redistribuim harta formelor.

Vă mulțumesc pentru DEX Online și pentru timpul acordat.

Cu respect,
[nume]
[adresă de e-mail] · https://github.com/gov2-ro/wrodfreq
