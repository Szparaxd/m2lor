# Projekt: Radar/ESP dla Metin2 (black-box, przez DLL injection)

**Data:** 2026-10-15
**Autor:** adam.lorek@pamproject.pl (+ Claude)
**Status:** zatwierdzony projekt — gotowy do planu implementacji M1

---

## 1. Cel i kontekst

Nauka **obrony** lokalnego, prywatnego serwera Metin2 poprzez wcześniejsze nauczenie się
**ataku** (red team → blue team). Pierwszy projekt ofensywny: **radar/ESP** — wykrycie encji
wokół gracza (gracze, moby, kamienie metin), odczyt ich pozycji i typu, oraz wyświetlenie ich
na żywo w osobnym programie (jak miał m2bob).

Technika do opanowania: **DLL injection** do klienta gry.

### Zasady (wybór użytkownika)
- **Black-box jak atakujący:** offsety i struktury znajdujemy przez reverse engineering
  (głównie Cheat Engine). Źródła klienta (`Source Client`) służą **wyłącznie do weryfikacji**
  po fakcie — nie do znajdowania.
- **Nauka przez robienie:** każdy etap kończy się widocznym efektem; tłumaczymy „dlaczego",
  nie tylko „jak".
- Wszystko działa **wyłącznie lokalnie** (własny serwer w VirtualBox, własny klient) — zero
  wpływu na innych graczy.
- Obrona (wykrywanie/blokowanie tych technik) to **osobny, późniejszy etap**. Teraz 100% atak.

### Środowisko
- Pliki: `C:\Metin\Terenzo 2024 - Muchomor Upgrade` (Terenzo 2024 „Muchomor Upgrade").
- Klient: `GameCore\Klient Terenzo 2024\GameCoreTop.exe` — oparty o `python27.dll`, prawie na
  pewno 32-bit.
- Także: `Dump_Proto`, `Source Client` (tylko weryfikacja), `VirtualBox` (serwer, na później).
- OS: Windows 11. Zainstalowane: Visual Studio, Cheat Engine.

---

## 2. Wybrane podejście

**Podejście A — „Recon w Pythonie → uzbrojenie w DLL":** najpierw szybkie rozpoznanie pamięci
w języku, który użytkownik zna (Python + `pymem`, odczyt z zewnątrz przez `ReadProcessMemory`),
a dopiero po ustaleniu offsetów przeniesienie logiki do wstrzykiwanego DLL w C++. Radar w C#.

Powód: najszybszy widoczny efekt i najwięcej zrozumienia; DLL injection i tak jest realnie
przećwiczone (etap M5), tylko w dobrej kolejności (najpierw wiedza, potem broń).

---

## 3. Architektura docelowa

```
GameCoreTop.exe (gra)                         Radar (C#, WinForms)
  └─ nasz DLL (C++)  ──── TCP 127.0.0.1:51337 ────►  rysuje punkty wokół gracza
     czyta pamięć,         (snapshoty encji, JSON)
     enumeruje encje
```

Do tej architektury dochodzimy etapami; recon (M1–M4) dzieje się z zewnątrz, bez DLL.

### Model mentalny pamięci gry
Każda postać na ekranie (gracz, moby, inni gracze, kamienie metin) to obiekt tego samego typu
(„instancja postaci"). Istnieje **menedżer** trzymający zbiór wszystkich widocznych instancji,
zwykle indeksowany przez **VID** (unikalne ID postaci). Każda instancja zawiera m.in.:
pozycję (X, Y, Z jako float), VID, typ/rasę (rozróżnienie mob/gracz/metin) i nazwę.
Cel: znaleźć ten zbiór i dla każdej instancji odczytać pozycję + typ.

---

## 4. Kamienie milowe

| # | Etap | Narzędzie | Widoczny efekt |
|---|------|-----------|----------------|
| M0 | Setup środowiska | CE + Python `pymem` | (CE jest; zostaje sprawdzić odczyt z Pythona) |
| M1 | Pozycja gracza (live) | CE → Python | konsola wypisuje zmieniające się X/Y/Z przy bieganiu |
| M2 | Stabilny wskaźnik do gracza | CE pointer scan | adres działa po restarcie gry (pointer path) |
| M3 | Lista encji wokół gracza | CE + Python | wypis pozycji wszystkich pobliskich postaci |
| M4 | Klasyfikacja: gracz/mob/metin + VID | CE + Python | każda encja z typem, np. „metin @ (x,y)" |
| M5 | Przeniesienie odczytu do DLL (C++) + injekcja | VS, C++ | DLL wstrzyknięty, loguje te same encje od środka |
| M6 | Radar (C#) + IPC | VS, C# | okno radaru rysuje kropki na żywo wokół gracza |

Po M4 istnieje „mózg" bota (Python). M5–M6 to opakowanie w prawdziwego bota z DLL i UI.
Bonus: w M3/M4 zrobimy brzydki podgląd w Pythonie (konsola lub ~20 linii pygame/matplotlib),
żeby zobaczyć efekt na długo przed M6.

---

## 5. Strategia reverse engineeringu (M1–M4)

- **M1 — pozycja gracza:** w CE skan wartości `float` podczas biegania (increased/decreased).
  Wyłapanie X, Y (współrzędne świata, duże liczby). Python odczytuje je na żywo.
- **M2 — stabilny wskaźnik:** adres z M1 znika po restarcie (dynamiczna alokacja). Pointer scan
  w CE → łańcuch `[[GameCoreTop.exe+XXXX]+off]...` prowadzący zawsze do pozycji; przy okazji
  adres instancji gracza (pozycja to pole w środku instancji).
- **M3 — zbiór encji:** od instancji gracza pointer scan „kto na nią wskazuje" → kontener
  wszystkich instancji. Ustalamy: start kontenera, sposób iteracji (krok/węzły), offset pozycji
  w instancji (ten sam dla wszystkich). Efekt: pętla wypisująca pozycje pobliskich.
- **M4 — typ i VID:** pole rasy/vnum odróżniające metin/mob/gracza (metiny mają charakterystyczne
  numery). Porównanie wartości obok metina vs moba vs gracza. Dodanie VID.

Weryfikacja: dopiero po znalezieniu offsetu „na ślepo" — zajrzenie do `Source Client`, czy pole
się zgadza (zamknięcie pętli uczenia).

---

## 6. IPC i format danych (M5–M6)

- **Kanał:** lokalny TCP `127.0.0.1:51337`. DLL (C++) = serwer; radar (C#) = klient.
  Powód: identyczne w C++ i C#, łatwe do debugowania, czysty rozdział procesów.
- **Ramka:** `[4 bajty długości][JSON]`. ~10–20 ramek/s.
- **Przykład:**
  ```json
  {
    "player": { "x": 352100.0, "y": 681540.0 },
    "entities": [
      { "vid": 1024, "x": 352250.0, "y": 681400.0, "type": "metin" },
      { "vid": 1050, "x": 351980.0, "y": 681600.0, "type": "mob" },
      { "vid": 2001, "x": 352300.0, "y": 681700.0, "type": "player" }
    ]
  }
  ```
- JSON (nie binarnie) na etap nauki — czytelny, łatwy do sprawdzenia. Przełączenie na format
  binarny to opcjonalne ćwiczenie później (YAGNI).
- DLL wysyła surowe współrzędne świata; przeliczenie na pozycje na radarze robi C#.

---

## 7. Radar (C#, WinForms) — M6

- Jedno okno, rysowanie w `OnPaint`, odświeżanie `Timer` (~30 FPS).
- **Gracz zawsze na środku.** Encja: `screenX = środek + (ent.x − player.x) * skala` (analogicznie Y).
- Kolory wg typu: metin = żółty kwadrat, mob = czerwona kropka, gracz = niebieska kropka. Legenda.
- Zoom (+/- lub suwak); rysowanie tylko encji w promieniu (np. 10000 jednostek).
- Brak danych/połączenia → „oczekiwanie na grę...", bez crasha.
- **Świadomie pominięte (YAGNI):** przezroczysty overlay na grze, obrót wg kierunku patrzenia,
  nazwy na radarze, historia. Naturalne rozszerzenia później.

---

## 8. Struktura projektu

```
C:\Metin\radar-project\
├─ SPEC.md           # ten dokument
├─ recon\            # Python: odczyt/szukanie pamięci (M1–M4)
│   ├─ read_player.py
│   └─ scan_entities.py
├─ dll\              # C++: wstrzykiwany DLL (M5) — projekt VS
├─ injector\         # prosty injector (LoadLibrary)
├─ radar\            # C#: WinForms radar (M6) — projekt VS
└─ notes\            # dziennik RE: offsety, pointer paths, „jak znalazłem"
```

Folder `notes\` = dziennik reverse engineeringu; przy każdym offsecie komentarz jak go znaleziono.

---

## 9. Testowanie i weryfikacja

- M1–M4: stań obok znanego metina/moba → skrypt ma wypisać właściwe pozycje/typy; porównaj z ekranem.
- Weryfikacja offsetów: po znalezieniu — zerknięcie do `Source Client`.
- M5: DLL loguje te same dane co skrypt z M4 → muszą się zgadzać.
- M6: encja na radarze w tym samym kierunku/odległości co na ekranie gry.

---

## 10. Ryzyka

- **Crash klienta przy złym odczycie:** M1–M4 czytają z zewnątrz (`ReadProcessMemory`) — błędny
  adres = błąd, nie crash gry. Ryzyko dopiero w DLL (M5) → ostrożne sprawdzanie wskaźników.
- **Zmienne offsety po restarcie/patchu:** dlatego M2 (pointer path od stałego modułu) przed dalej.
- **Ochrona klienta/antycheat:** ten klient (python27) raczej bez agresywnej ochrony; każda
  napotkana przeszkoda = materiał na lekcję obrony, nie porażka.
- **Środowisko:** wyłącznie lokalnie (VirtualBox + własny klient).

---

## 11. Zależności

- Python + `pymem` (`pip install pymem`), opcjonalnie `pygame`/`matplotlib` (szybki podgląd).
- Visual Studio: komponenty C++ (DLL) i .NET/C# (radar). Cheat Engine — jest.

---

## 12. Poza zakresem (teraz)

- Jakakolwiek modyfikacja/zapis pamięci (teleport, speedhack) — to osobny, późniejszy temat.
- Hookowanie funkcji i sieci — późniejsze etapy nauki.
- Warstwa obronna (detekcja injekcji, integrity checks, walidacja serwera) — osobny rozdział.
