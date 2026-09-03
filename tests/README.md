# Testovi

Ovaj direktorij sadrži integracijske testove javnog API ugovora, napisane pomoću
Pythonova ugrađenog modula `unittest`. Očekivanja su definirana prema zahtjevima
projekta, bez čitanja implementacije prilikom pisanja testova.

Testovi ne uvoze aplikacijske module i ne zamjenjuju OCR ili YOLO strategije mockovima.
Šalju stvarne HTTP zahtjeve aplikaciji i provjeravaju odgovore te primljene callbackove.
Lokalni testni server kontrolira preuzimanje slika i odgovore callback primatelja.

## Preduvjeti

- Python 3.11 ili noviji i instaliran UV.
- Projektne ovisnosti instalirane naredbom `uv sync`.
- Tesseract dostupan procesu koji pokreće testove, s instaliranim jezikom `eng`.
- Za opcionalni YOLO test: dostupne težine modela ili mrežni pristup za njihovo preuzimanje.

Pillow generira testne slike u memoriji. Runner ne koristi pytest.

Na Windowsu provjeri Tesseract iz istog terminala u kojem ćeš pokrenuti testove:

```powershell
tesseract --version
tesseract --list-langs
uv run python -c "import shutil; print(shutil.which('tesseract'))"
```

Posljednja naredba treba ispisati putanju, a ne `None`. Nakon promjene `PATH`-a
ponovno pokreni terminal i aplikaciju iz koje ga otvaraš.

Alternativno, za taj terminal možeš zadati izvršnu datoteku:

```powershell
$env:INFERENCE_TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
```

## Pokretanje

Naredbe pokreni iz korijena repozitorija:

```powershell
uv sync
uv run python utils.py test
```

Sve provjere, uključujući stvarnu YOLO inferenciju:

```powershell
uv run python utils.py test --yolo
```

Zaustavljanje nakon prvog neuspjeha:

```powershell
uv run python utils.py test --failfast
```

Opcije se mogu kombinirati. `--yolo` postavlja `RUN_YOLO_TESTS=1`; ako je ta varijabla
već postavljena u okolini, YOLO test uključuje se i bez opcije.

Runner automatski pronalazi datoteke `tests/test_*.py`. Moguće je i izravno pokretanje
putem standardnog unittest sučelja:

```powershell
uv run python -m unittest discover -s tests -p "test_*.py" -v
```

Samo jedan test, primjerice stvarni OCR:

```powershell
uv run python -m unittest discover -s tests -p "test_*.py" -k real_ocr -v
```

## Kako radi testno okruženje

Testna klasa pokreće vlastiti Uvicorn proces s jednim workerom i lokalni HTTP server
na adresi `127.0.0.1`, na slobodnim portovima. Aplikaciju ne treba pokretati ručno.
Testovi ne šalju zahtjeve postojećem serveru na portu 8000.

Lokalni server generira slike, simulira neispravne odgovore i prihvaća callbackove.
Provjera paralelizma zadržava preuzimanja pomoću događaja: tako deset poslova ostaje
aktivno neovisno o brzini OCR-a. Nakon provjera otpuštaju se zadržani zahtjevi i gase
testni procesi. Gašenje testnog procesa nije provjera graceful shutdown ponašanja.

Testni proces nasljeđuje okolinu, ali postavlja ove vrijednosti:

| Postavka | Testna vrijednost |
| --- | --- |
| INFERENCE_MAX_CONCURRENT_REQUESTS | 10 |
| INFERENCE_MAX_IMAGE_BYTES | 1048576 (1 MiB) |
| INFERENCE_DOWNLOAD_TIMEOUT | 20 sekundi |
| INFERENCE_CALLBACK_TIMEOUT | 3 sekunde |
| INFERENCE_CALLBACK_ATTEMPTS | 3 |
| INFERENCE_TESSERACT_LANGUAGE | eng |
| INFERENCE_TESSERACT_TIMEOUT | 10 sekundi |

Ove vrijednosti vrijede samo za testni proces; runner ne uređuje projektni `.env`.
Ostale postavke, poput putanje Tesseracta ili YOLO modela, i dalje ovise o konfiguraciji
aplikacije. Težine modela mogu ostati na disku nakon YOLO provjere.

Čekanje callbacka i uvjeta ograničeno je na 30 sekundi, odnosno 180 sekundi kada je
YOLO uključen. Početno čekanje pokretanja aplikacije ostaje 30 sekundi.

## Pokrivenost

Datoteka `test_api_contract.py` sadrži 16 testnih metoda, neke s više podslučajeva.
U zadanom pokretanju izvodi se 15 metoda, a YOLO metoda je preskočena.

| Područje | Što se provjerava |
| --- | --- |
| Health | HTTP 200, status `ok`, broj aktivnih poslova i limit |
| Obavezna polja | Nedostajući `image_url`, `method` ili `callback_url` daje 422 |
| Metode | Nepodržane vrijednosti i pogrešni tipovi daju 422 |
| URL-ovi | Neispravni URL-ovi te protokoli file i ftp daju 422 |
| Dodatna polja | Nepoznata polja zahtjeva daju 422 |
| Asinkronost | HTTP 202 i UUID stižu dok preuzimanje još čeka |
| Korelacija | ID-jevi poslova su jedinstveni i podudaraju se s callbackovima |
| Stvarni OCR | Generirana slika daje rezultat koji sadrži `HELLO WORLD 123` |
| Nedostajuća slika | HTTP 404 pri preuzimanju daje `failed` callback |
| Neispravna slika | Nevaljani slikovni bajtovi daju `failed` callback |
| Prevelik sadržaj | Odgovor veći od testnog limita daje `failed` callback |
| Preusmjeravanje | Redirect se ne slijedi i daje `failed` callback |
| Callback retry | Nakon dva odgovora 503 treći pokušaj koristi isti sadržaj i ključ |
| Iscrpljeni pokušaji | Nakon tri neuspješne dostave oslobađa se kapacitet |
| Paralelizam | Deset poslova je prihvaćeno, jedanaesti dobiva 429; kapacitet se vraća |
| Stvarni YOLO, opcionalno | Provjeravaju se struktura rezultata i rasponi vraćenih detekcija |

OCR provjera normalizira razmake i veličinu slova; ne zahtijeva identično formatiranje.
Tekst internih poruka pogrešaka nije fiksiran.

## Tumačenje rezultata

- `ok`: testirana očekivanja su zadovoljena.
- `FAIL`: odgovor nije zadovoljio očekivanje testa.
- `ERROR`: iznimka, nedostajući callback ili problem pokretanja spriječio je provjeru.
- `skipped`: provjera nije izvedena; uobičajeno za YOLO bez opcije `--yolo`.

Runner vraća izlazni kod **0** kada nema neuspjeha ili pogrešaka, čak i ako je YOLO
preskočen. Vraća **1** kada postoje neuspjesi ili pogreške. To je prikladno za CI.

Ako OCR test primi `status: "failed"`, provjeri dostupnost Tesseracta i jezika
`eng` u testnoj okolini. Sam generički callback ne dokazuje uzrok pogreške.
Nedostajući Tesseract namjerno ne preskače OCR test.

Ako aplikacija ne starta, runner ispisuje uhvaćeni izlaz Uvicorna. Logovi tijekom
ostalih provjera trenutačno se čuvaju u privremenoj datoteci i ne ispisuju se automatski
pri svakom neuspjehu. Za detaljnu dijagnozu ponovi problem kroz ručno pokrenutu aplikaciju.

Rezultat pojedinog pokretanja nije jamstvo prolaznosti u drugoj okolini. Ne mijenjati
očekivanja samo da bi postojeća implementacija prošla: najprije utvrditi je li problem
u aplikaciji, testnom okruženju ili samom ugovoru.

## Ograničenja i daljnji plan

- YOLO koristi praznu sliku: ne dokazuje detekciju poznatog objekta. Dodati verzioniranu,
  licenciranu sliku i provjeru očekivane klase uz toleranciju koordinata.
- Test prevelikog sadržaja koristi neispravne slikovne bajtove. Dokazuje neuspješan
  rezultat, ali ne razlikuje odbijanje zbog veličine od odbijanja pri dekodiranju.
  Dodati valjanu sliku iznad limita i provjere točne granice.
- Provjeriti granice broja piksela, EXIF rotaciju, dodatne formate i više OCR jezika.
- Dodati mrežne timeoute, prekid prijenosa i callback koji ne odgovara.
- Provjeriti zadržavanje kapaciteta tijekom callback pokušaja i oslobađanje nakon uspjeha.
- Dodati zasebne provjere graceful shutdowna i prisilnog prekida s aktivnim poslovima.
- Provjeriti nevaljane konfiguracije, izolaciju od lokalnih postavki i dugotrajno opterećenje.

Nove testove dodavati prema unaprijed definiranim zahtjevima. Mockove vanjskih sustava,
ako postanu potrebni, jasno razlikovati od provjera stvarne inferencije.
