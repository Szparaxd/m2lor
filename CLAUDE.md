# radar-project — zasady pracy

Black-box radar/bot do lokalnego serwera Metin2 (nauka ataku, potem obrony). Python = "mozg"
(czyta pamiec przez pymem, decyduje), wstrzyknieta `dll/autopot.dll` = wykonawca w grze (potok
`\\.\pipe\m2autopot_<pid>`). Szczegoly: `README.md`, `SPEC.md`, `notes/`.

## ZASADA NACZELNA: loguj obficie — diagnoza z samych logow

Kazda funkcja bota ma logowac tyle, zebym **ja (Claude) mogl samodzielnie dojsc do wnioskow
wylacznie z plikow logow — bez proszenia uzytkownika o liczby z panelu ani o opis tego, co widzi
na ekranie.** To jest wymog, nie opcja.

- **Dodajesz/zmieniasz funkcje → w tej samej zmianie dokladasz jej logowanie.** Funkcja bez logow
  = niedokonczona.
- **Co logowac:** kazda decyzje i jej powod; liczby wejsciowe (odleglosci, VID-y, pozycje, licznosci,
  progi); co serwer **przyjal/odrzucil** (wnioskowane z efektu: zgon/znikniecie VID, zmiana HP,
  brak zmiany); bledy (pelny wyjatek). Przy pomiarach podawaj wartosci surowe, nie tylko werdykt,
  zeby dalo sie przeliczyc wnioski inaczej.
- **Dwa kanaly (oba wlaczone przez `WALK_DEBUG=True` w radar_map.py):**
  - `self._dbg("...", *a)` → czytelna linia do `data/logs/<stamp>_radar_<nick>_walk.log`.
  - `self._tr().rec("typ", pole=..., ...)` → rekord JSONL do `data/logs/<stamp>_radar_<nick>_trace.jsonl`
    (pelny slad do analizy/odtworzenia; `recon/replay.py`).
  - Zasada glosnosci: **JSONL co zdarzenie/paczke** (pelny material), **czytelny log throttlowany**
    (~1 s albo przy zmianie stanu), zeby sie nie zalewal. Wzor: `_area_attack_step` (pola n/near/far/
    metin/hop/gone/maxhit/dists + per-zgon `area_gone` z dystansem i via=direct/hop).
- **DLL** loguje przez `dbg(...)` do `logs/dll_debug.txt` (komenda odebrana, pending-call, wynik
  natywnego wywolania). Dokladaj dbg przy kazdej nowej komendzie `!...`.
- **Gdy diagnozuje:** czytam `data/logs/` (najnowszy `*_walk.log` / `*_trace.jsonl` danej postaci) +
  `logs/dll_debug.txt`. Jesli brakuje danych do wniosku — to blad logowania do naprawienia, nie powod,
  by pytac uzytkownika.

## Budowanie DLL (gdy gra dziala — plik zablokowany)

`build.bat` linkuje do `dll\autopot.dll`; gdy klient dziala, stara DLL jest zmapowana i link pada
(`LNK1104 nie mozna otworzyc`). Wtedy: zbuduj do `dll\autopot_new.dll`, `Rename-Item` starej na
`autopot_old_<ts>.dll`, `Move-Item` nowej na `autopot.dll`. Dzialajace gry trzymaja stara mape;
**nowa DLL wchodzi dopiero po restarcie klienta** (radar wstrzykuje przy starcie). vcvars32:
`C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars32.bat`.

## Dwa buildy klienta

- **GameCoreTop.exe** (python27, ImageBase 0x400000): pending-calls sa opruzniane → Python i natywne
  `p_AddPendingCall` dzialaja. Marker `logs/caps_<pid>.ok`.
- **vCore.exe**: kolejka NIE opruzniana → tylko hook per-klatka (natywne `!drink/!skill/...`). Marker
  `logs/hook_<pid>.ok`. RVA szukane sygnaturami (launcher podmienia exe).
- Po dodaniu natywnej komendy dopisz jej flage do markera caps/hook (radar sprawdza `has_*`).

## Git / uruchamianie

- Zdalne: `github.com/Szparaxd/m2lor`. Prace rob na gałęzi (nie commituj prosto na `main`).
- Commituj tylko pliki swojej zmiany; inne sesje edytuja te same pliki (`radar_map.py`, `botapi.py`)
  — nie wciagaj ich niezwiazanych zmian do swojego commita.
- Panel w przegladarce to jedyny UI: `panel.bat` (hub + `radar --headless` per klient). Zob.
  `notes/ANALIZA-panel-www.md`.
