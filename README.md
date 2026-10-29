# m2lor — radar/ESP dla Metin2 (projekt edukacyjny)

Black-box radar/ESP i narzędzia pomocnicze dla klienta Metin2, pisane po to, żeby nauczyć się
**ataku** (reverse engineering, odczyt pamięci, DLL injection), a potem **obrony** własnego,
lokalnego serwera.

> Wszystko działa wyłącznie lokalnie: prywatny serwer w VirtualBox i własny klient.
> Projekt nie jest przeznaczony do użycia na publicznych serwerach.

Szczegóły celu i architektury: [SPEC.md](SPEC.md). Notatki z kolejnych etapów: [notes/](notes/).

## Co tu jest

| Katalog | Zawartość |
|---|---|
| `recon/` | Python + `pymem`: skanery pamięci (RTTI, encje, HP, pozycje), radar (`radar_map.py`), launcher, auto-chodzenie/farma |
| `dll/` | `autopot_dll.cpp`: wstrzykiwany DLL (picie mikstur bez fokusu okna, named pipe) |
| `injector/` | `inject.cpp`: prosty injector `LoadLibrary` |
| `data/` | konfiguracja użytkownika: `radar_mobs.txt`, `potion_vnums.txt` |
| `notes/` | notatki z rozpoznania (M1–M4, analiza rozbudowy) |
| `logs/` | wyniki wybranych skanów referencyjnych |

## Czego NIE ma w repo

Plików gry/klienta (paczki `.epk/.eix`, archiwa `.rar`, tekstury `.dds`) ani **cache minimap**
`data/maps/`, bo to zasoby gry. `recon/map_assets.py` buduje je sam z paczek klienta
przy pierwszym uruchomieniu radaru na danej mapie.

Ścieżki do klienta są wpisane na sztywno w skryptach (`PACK_DIR`, `PROTO_DIR`, `DEFAULT_GAME`),
domyślnie `C:\Metin\Terenzo 2024 - Muchomor Upgrade\GameCore\...`. Jeśli klient leży gdzie
indziej, popraw je przed uruchomieniem.

## Wymagania

- Windows, Python 3 (klient gry jest 32-bit, DLL i injector budowane jako 32-bit)
- `pip install -r requirements.txt` (`pymem`, `numpy`, `Pillow`)
- do budowy DLL/injektora: Visual Studio Build Tools z „Desktop development with C++”
  (ścieżka do `vcvars32.bat` w `build.bat`)

## Uruchomienie (panel w przeglądarce)

Po zbudowaniu DLL (niżej) wystarczy dwuklik w **`panel.bat`**: prosi o uprawnienia administratora,
uruchamia hub (`recon/hub.py`) i otwiera http://127.0.0.1:8765. Na ekranie **Moje konta**:

1. Kliknij **+ Dodaj konto**. Wpisz login i hasło do gry, wybierz kanał oraz pozycję postaci (1–4 na ekranie wyboru).
2. **Zapisz konto** zapisuje profil bez otwierania gry. **Zapisz i uruchom** od razu uruchamia grę i loguje postać.
3. Na karcie obserwujesz uruchamianie, logowanie i wybór postaci. **Edytuj konto** zmienia dane także przy wyłączonej grze; puste hasło zachowuje zapisane hasło tego samego loginu.
4. Po wejściu do świata **Panel postaci** otwiera radar i ustawienia funkcji. **Włącz/Wyłącz bota** steruje botem osobno od gry.

Hasło jest szyfrowane przez Windows DPAPI. Błędy logowania pozostają na karcie; po poprawieniu danych można ponowić logowanie. Opcja wznowienia gry działa po pierwszym uruchomieniu konta. Klienty uruchomione ręcznie znajdziesz w **Postacie w grze**; stamtąd można przypisać im konto.

Dalej wszystko jest w przeglądarce: wszystkie funkcje i trasa na radarze,
narzędzia (ponowne wstrzyknięcie DLL, kalibracja, skan, budowa mapy), ścieżka gry i pliki
`data/*_vnums.txt` / `radar_mobs.txt` (Ustawienia) oraz logi (Dziennik). Bot działa bez okna
(`radar_map.py --headless`); stare okno tkinter można włączyć w Ustawieniach do diagnostyki.

Testy profili i lokalnego API (z osobnym magazynem, bez uruchamiania gry): `python -m unittest discover -s tests -v`.

## Uruchomienie ręczne (konsola)

```bat
build.bat
```
Buduje `dll\autopot.dll` i `injector\inject.exe` (nie są commitowane).

Z terminala **jako administrator** (wymagane do `OpenProcess` i wstrzyknięcia DLL):

```bat
py -3 recon\launch.py
```
Launcher uruchamia klienta, czeka, aż postać wejdzie do świata (logowanie robisz ręcznie),
i dopiero wtedy odpala radar. Radar możesz też uruchomić sam:

```bat
py -3 recon\radar_map.py [nazwa_mapy] [-e vCore.exe]
```
