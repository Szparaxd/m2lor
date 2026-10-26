# Plan implementacji M1 — Pozycja gracza (live)

> **Dla wykonującego:** plan realizujemy wspólnie, zadanie po zadaniu. Kroki mają checkboxy
> (`- [ ]`) do odhaczania. Część zadań jest RĘCZNA (Cheat Engine) — tam „test" = widoczny efekt
> w grze, nie assert. Część jest w Pythonie — tam jest konkretny kod i weryfikacja uruchomieniem.

**Cel:** Uruchomić skrypt w Pythonie, który na żywo wypisuje współrzędne X/Y/Z Twojej postaci —
biegasz w grze, liczby się zmieniają zgodnie z ruchem.

**Architektura:** Odczyt z ZEWNĄTRZ (bez wstrzykiwania) przez `ReadProcessMemory` opakowane w
bibliotekę `pymem`. Adresy współrzędnych znajdujesz ręcznie w Cheat Engine (black-box), a Python
tylko je odczytuje w pętli. Adres na tym etapie jest dynamiczny (zmienia się po restarcie gry) —
to OK, stabilny wskaźnik robimy dopiero w M2.

**Tech stack:** Cheat Engine, Python 3 (64-bit lub 32-bit), biblioteka `pymem`.

**Spec:** `C:\Metin\radar-project\SPEC.md` (sekcje 4 i 5, etap M1).

## Global Constraints

- Praca **black-box**: offsety/adresy znajdujemy sami w CE; do `Source Client` zaglądamy TYLKO
  do weryfikacji po fakcie.
- Wszystko **lokalnie** (własny klient + serwer w VirtualBox).
- Klient `GameCoreTop.exe` jest 32-bitowy → adresy mieszczą się w 32 bitach. `pymem` czyta proces
  32-bit z Pythona 64-bit bez problemu; jeśli pojawią się błędy z enumeracją modułu, użyj Pythona
  32-bit.
- Nazwa procesu do podpięcia: **`GameCoreTop.exe`**.
- Każdy znaleziony adres/offset zapisujemy w `notes\M1-pozycja.md` z komentarzem „jak znalazłem".
- Brak repo git w `C:\Metin` → zamiast `git commit` krok kończymy zapisaniem pliku + notatki.
  (Opcjonalnie: możesz na początku zrobić `git init` w `radar-project\` — wtedy realnie commituj.)

## Review Focus

Rzeczy, które realnie potrafią ugryźć na tym etapie (poza tym, co testują kroki):

- **Gra niewłączona / zły proces** → skrypt ma czytelnie powiedzieć „nie znalazłem procesu
  GameCoreTop.exe", a nie rzucić surowym tracebackiem. (pokryte w Zadaniu 1 i 4)
- **Adres z innej sesji gry** → po restarcie gry adres jest nieaktualny; odczyt zwróci śmieci lub
  błąd. Skrypt ma to wykryć (wartości absurdalne / wyjątek) i poprosić o nowy adres. (Zadanie 4)
- **Pomylenie X/Y/Z** → weryfikacja ruchem w znanym kierunku (Zadanie 3) rozstrzyga, które pole
  jest które.
- **Float vs double** → Metin2 trzyma pozycję jako 4-bajtowy float; skan typu „Float" w CE i
  `struct` format `'<f'` w Pythonie muszą się zgadzać. (Zadanie 2 i 4)
- **32/64-bit mismatch** → objawia się błędem przy odczycie/module; obejście w Global Constraints.

---

## Zadanie 1: Środowisko Pythona + potwierdzenie podpięcia do procesu

**Pliki:**
- Utwórz: `C:\Metin\radar-project\recon\_env_check.py`
- Utwórz (jeśli nie ma): `C:\Metin\radar-project\notes\M1-pozycja.md`

**Interfejs:**
- Produkuje: potwierdzenie, że `pymem` potrafi podpiąć się do `GameCoreTop.exe` i zwrócić adres
  bazowy modułu. Reszta zadań zakłada, że to działa.

- [ ] **Krok 1: Zainstaluj pymem**

W terminalu:
```bash
pip install pymem
```
Oczekiwane: instalacja bez błędów (`Successfully installed pymem-...`).

- [ ] **Krok 2: Uruchom grę i zaloguj się postacią**

Odpal `GameCore\Klient Terenzo 2024\GameCoreTop.exe`, zaloguj się i wejdź postacią do świata
(tak, żeby postać stała na mapie). Zostaw grę włączoną.

- [ ] **Krok 3: Napisz skrypt sprawdzający podpięcie**

`recon/_env_check.py`:
```python
import pymem

PROCESS = "GameCoreTop.exe"

def main():
    try:
        pm = pymem.Pymem(PROCESS)
    except pymem.exception.ProcessNotFound:
        print(f"[!] Nie znalazlem procesu {PROCESS}. Czy gra jest wlaczona?")
        return

    base = pymem.process.module_from_name(pm.process_handle, PROCESS).lpBaseOfDll
    print(f"[+] Podpieto do {PROCESS} (PID {pm.process_id})")
    print(f"[+] Adres bazowy modulu: {hex(base)}")

if __name__ == "__main__":
    main()
```

- [ ] **Krok 4: Uruchom i zweryfikuj**

Run: `python recon/_env_check.py`
Oczekiwane: wypis „Podpieto do GameCoreTop.exe (PID ...)" i adres bazowy (np. `0x400000`).
Jeśli „Nie znalazlem procesu" — upewnij się, że gra działa i nazwa procesu się zgadza
(Menedżer zadań → Szczegóły).

- [ ] **Krok 5: Zapisz notatkę**

W `notes/M1-pozycja.md` zanotuj: datę, że podpięcie działa, PID i adres bazowy z tej sesji
(adres bazowy bywa stały dla `.exe`, przyda się później).

---

## Zadanie 2: Znalezienie adresów X/Y (i Z) w Cheat Engine

**Pliki:**
- Modyfikuj: `C:\Metin\radar-project\notes\M1-pozycja.md` (zapis znalezionych adresów)

**Interfejs:**
- Produkuje: 2–3 adresy pamięci (szesnastkowo) odpowiadające współrzędnym X, Y (opcjonalnie Z)
  postaci w BIEŻĄCEJ sesji gry. Zadanie 4 (skrypt Python) te adresy odczytuje.

To zadanie jest RĘCZNE. „Test" = wartość w CE zmienia się dokładnie wtedy, gdy ruszasz postacią.

- [ ] **Krok 1: Podepnij Cheat Engine do gry**

CE → ikona komputera (Select a process) → `GameCoreTop.exe` → Open.

- [ ] **Krok 2: Pierwszy skan — typ Float, „Unknown initial value"**

W CE ustaw: Value Type = **Float**, Scan Type = **Unknown initial value** → **First Scan**.
(Nie znamy liczby, więc zaczynamy od „nieznanej".)

- [ ] **Krok 3: Zawężaj przez ruch**

Przebiegnij postacią kawałek w JEDNĄ stronę. W CE: Scan Type = **Increased value** (jeśli
współrzędna rosła) lub **Decreased value** → **Next Scan**. Powtarzaj: ruch → Increased/Decreased
→ Next Scan. Gdy stoisz w miejscu, użyj **Unchanged value**, żeby odsiać szum.
Oczekiwane: lista kandydatów kurczy się z milionów do kilkunastu.

- [ ] **Krok 4: Wytypuj X i Y**

Metin2 używa dużych współrzędnych świata. Gdy zostanie kilka–kilkanaście adresów, dodaj je do
dolnej tabeli (dwuklik). Biegnij w jednej osi (np. tylko w górę mapy) — tylko jedna wartość
powinna się wyraźnie zmieniać → to jedna oś. Potem w drugą oś → druga wartość. Tak rozróżnisz X
i Y. (Z = wysokość, zmienia się rzadziej — opcjonalnie.)

- [ ] **Krok 5: Potwierdź „zamrożeniem"**

Zaznacz checkbox „Active/Freeze" przy wytypowanym adresie X i spróbuj biec w tej osi — ruch w tej
osi powinien być zablokowany/szarpać. To potwierdza, że to właściwy adres. Odznacz po teście.
Oczekiwane: freeze wyraźnie wpływa na pozycję.

- [ ] **Krok 6: Zapisz adresy do notatek**

W `notes/M1-pozycja.md` zapisz: adres X, adres Y (i Z jeśli masz), typ = Float, oraz
JEDNO zdanie jak je znalazłeś. Zaznacz wyraźnie: „adresy z sesji dnia ..., dynamiczne".

---

## Zadanie 3: Skrypt Python — odczyt pozycji na żywo

**Pliki:**
- Utwórz: `C:\Metin\radar-project\recon\read_player.py`

**Interfejs:**
- Konsumuje: adresy X/Y/Z z Zadania 2 (podawane jako argumenty wiersza poleceń, w hex).
- Produkuje: skrypt, który w pętli wypisuje aktualne X/Y/Z. To widoczny efekt M1.

- [ ] **Krok 1: Napisz skrypt odczytu w pętli**

`recon/read_player.py`:
```python
import sys
import time
import pymem

PROCESS = "GameCoreTop.exe"

def parse_addr(s):
    return int(s, 16)

def main():
    if len(sys.argv) < 3:
        print("Uzycie: python read_player.py <addr_X_hex> <addr_Y_hex> [addr_Z_hex]")
        print("Przyklad: python read_player.py 0x1A2B3C4 0x1A2B3C8")
        return

    addr_x = parse_addr(sys.argv[1])
    addr_y = parse_addr(sys.argv[2])
    addr_z = parse_addr(sys.argv[3]) if len(sys.argv) > 3 else None

    try:
        pm = pymem.Pymem(PROCESS)
    except pymem.exception.ProcessNotFound:
        print(f"[!] Nie znalazlem procesu {PROCESS}. Czy gra jest wlaczona?")
        return

    print("[+] Czytam pozycje. Biegaj postacia. Ctrl+C aby przerwac.")
    try:
        while True:
            x = pm.read_float(addr_x)
            y = pm.read_float(addr_y)
            if addr_z is not None:
                z = pm.read_float(addr_z)
                print(f"X={x:12.2f}  Y={y:12.2f}  Z={z:10.2f}")
            else:
                print(f"X={x:12.2f}  Y={y:12.2f}")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[+] Koniec.")
    except Exception as e:
        print(f"[!] Blad odczytu: {e} (czy adres jest z TEJ sesji gry?)")

if __name__ == "__main__":
    main()
```

- [ ] **Krok 2: Uruchom z adresami z Zadania 2**

Run (podstaw swoje adresy):
```bash
python recon/read_player.py 0xTWOJADRESX 0xTWOJADRESY
```
Oczekiwane: lecą linie `X=... Y=...`.

- [ ] **Krok 3: Weryfikacja ruchem (to jest „test" M1)**

Biegnij postacią w górę mapy → jedna współrzędna rośnie/maleje monotonicznie. Biegnij w bok →
druga. Stań → wartości zamarzają. Jeśli X i Y są zamienione względem intuicji, popraw kolejność
argumentów i/lub zaktualizuj notatki.
Oczekiwane: ruch na ekranie zgadza się ze zmianą liczb. ✅ To jest widoczny efekt M1.

---

## Zadanie 4: Utwardzenie skryptu (czytelne błędy) + weryfikacja względem źródeł

**Pliki:**
- Modyfikuj: `C:\Metin\radar-project\recon\read_player.py`
- Modyfikuj: `C:\Metin\radar-project\notes\M1-pozycja.md`

**Interfejs:**
- Produkuje: skrypt odporny na typowe pomyłki (gra wyłączona, adres z innej sesji) + potwierdzenie,
  że trafiliśmy w pole pozycji (zamknięcie pętli uczenia).

- [ ] **Krok 1: Dodaj kontrolę „rozsądnej" wartości**

Pod odczytem X/Y dodaj prosty sanity-check, żeby wyłapać adres z nieaktualnej sesji
(śmieciowe wartości). Wstaw w pętli, zaraz po odczycie:
```python
            if not (-1.0e7 < x < 1.0e7) or not (-1.0e7 < y < 1.0e7):
                print("[!] Wartosci wygladaja na smieci — adres pewnie z innej sesji gry. "
                      "Znajdz adres ponownie w CE.")
                break
```
(Zakres 1e7 obejmuje realne współrzędne świata Metin2 z dużym zapasem; dostosuj, jeśli Twoje
wartości są inne — zanotuj realny zakres w notatkach.)

- [ ] **Krok 2: Uruchom ponownie, potwierdź że normalny odczyt nadal działa**

Run: `python recon/read_player.py 0xTWOJADRESX 0xTWOJADRESY`
Oczekiwane: działa jak w Zadaniu 3; sanity-check nie fałszuje alarmu przy normalnej grze.

- [ ] **Krok 3: (Weryfikacja black-box → źródła) Potwierdź, że to pole pozycji**

Dopiero TERAZ, gdy adres działa: w `Source Client` wyszukaj w kodzie nazwy pól pozycji postaci
(np. `GetPixelPosition`, `m_kPPosDst`, `m_x`, `m_y`, `fx`, `fy` lub podobne). Cel: potwierdzić
„tak, pozycja postaci jest trzymana jako float X/Y w świecie" — bez przepisywania offsetów.
To krok domykający naukę: odkryłeś sam → sprawdziłeś, że dobrze.

- [ ] **Krok 4: Zapisz podsumowanie M1 w notatkach**

W `notes/M1-pozycja.md` dopisz: realny zakres wartości X/Y, która oś to która, i jedno zdanie
z weryfikacji ze źródeł. Zaznacz: „adresy dynamiczne → M2 zrobi stabilny wskaźnik".

---

## Definicja ukończenia M1

- `python recon/read_player.py <X> <Y>` wypisuje pozycję na żywo, zgodną z ruchem w grze.
- Skrypt czytelnie reaguje na: brak procesu i adres z nieaktualnej sesji.
- `notes/M1-pozycja.md` zawiera adresy, zakres wartości, mapowanie osi i notkę z weryfikacji.
- Wiesz, że adresy są dynamiczne — co prowadzi prosto do **M2 (stabilny wskaźnik / pointer scan)**.
