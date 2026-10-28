# Analiza: panel zarządzania w przeglądarce (multi-klient, nick + avatar klasy)

Data: 2026-10-28. Cel: zamiast okna tkinter per klient — jeden panel WWW (jak lewa kolumna + okno szczegółów
m2bob), obsługujący kilka zalogowanych klientów/postaci naraz.

## 1. Stan obecny — co przeszkadza

| Problem | Gdzie | Skutek przy wielu klientach |
|---|---|---|
| `pymem.Pymem(process)` łączy się po NAZWIE exe | `radar_map.py:445` | dwa `vCore.exe` → radar podłączy się zawsze do pierwszego |
| `RadarMap` (~3000 linii) miesza logikę bota z tkinter (`tk.BooleanVar` jako konfiguracja) | `radar_map.py:619-700` | nie da się uruchomić bota bez okna ani sterować nim z zewnątrz |
| wspólne pliki stanu: `data/ui_state.json`, logi sesji | `load_ui_geometry`, `data/logs/` | klienci nadpisują sobie ustawienia |
| fallback `SendInput` wymaga fokusu | `autopot.press_key` | przy kilku oknach działa tylko ścieżka DLL (pending call / hook) — to już mamy |

Co JEST gotowe pod multi-klienta: potok DLL per PID (`\\.\pipe\m2autopot_<pid>`), marker `hook_<pid>.ok`,
RVA rozwiązywane w DLL po sygnaturach, auto-kalibracja offsetów (`calibrate.py`).

## 2. Proponowana architektura

```
 przeglądarka (SPA)  ◄── WebSocket + HTTP ──►  HUB (Python, FastAPI, 127.0.0.1:8765)
                                                 │  lista procesów gry, spawn/stop workerów,
                                                 │  serwuje frontend, mapy PNG, avatary
                     ┌───────────────────────────┼───────────────────────────┐
                 WORKER pid=1234             WORKER pid=5678             WORKER ...
                 (BotCore, headless)         (BotCore, headless)
                 pymem + DLL pipe            pymem + DLL pipe
                     │                           │
                 vCore.exe #1               vCore.exe #2
```

- **Worker = osobny proces per klient** (obecny `RadarMap` bez tkintera). Powód: skan pamięci (~0,22 s,
  numpy) i pętle walki/chodzenia trzymają GIL — w jednym procesie N klientów by się dławiło; crash
  jednego workera nie kładzie reszty. Worker łączy się do huba `ws://127.0.0.1:8765/agent/<pid>`,
  wysyła snapshot stanu 5–10 Hz, odbiera komendy (`set` opcji, `toggle`, `goto`, `stop`).
- **Hub** = agregator: enumeruje procesy (`GameCoreTop.exe`, `vCore.exe`), startuje workera po kliknięciu
  „Start", trzyma ostatni snapshot każdego klienta, rozsyła do przeglądarek. Może też: Show/Hide okna gry
  (`EnumWindows` po PID → `ShowWindow`), zmiana tytułu okna na nick, uruchamianie nowego klienta (logika
  z `launch.py`).
- **Frontend**: lista klientów (avatar, nick, poziom, paski HP/PM, status bota, Start/Stop/Pokaż/Ukryj)
  + widok klienta z zakładkami odpowiadającymi obecnemu panelowi (Mikstura, Loot, Farm, Chodzenie,
  Buffy, Speed) i radarem na `<canvas>` (tło = istniejący cache `data/maps/*.png`, encje jako JSON).
- **Bezpieczeństwo**: nasłuch tylko na 127.0.0.1 + sprawdzanie nagłówka `Origin` na WebSocket (inaczej
  dowolna strona otwarta w przeglądarce mogłaby się połączyć z `ws://127.0.0.1` i sterować botem).

Odrzucone warianty: (a) jeden proces Python dla wszystkich klientów — GIL i wspólna awaria;
(b) serwer WebSocket w samej DLL (cel z SPEC) — dużo C++, sens dopiero przy porcie logiki do DLL.

## 3. Nick, klasa, avatar — skąd brać

- **Nick**: `CPythonPlayer` + 0x14 = `std::string` (zweryfikowane na GameCoreTop; vCore do sprawdzenia —
  ten build ma przesunięcia pól, więc przewidzieć sondę/kalibrację po znanym nicku).
- **Poziom / HP / PM**: tablica punktów (już czytana): [1]=lvl, [5]/[6]=HP, [7]/[8]=PM.
- **Klasa**: model własnego aktora z `CRaceData` (`_actor_model`) → `d:/ymir work/pc/<klasa>/...` lub
  `/pc2/` — z tego klasa i (pc/pc2) płeć. Weryfikacja/zapas: `player.player.job` z bazy po nicku
  (0 woj.M, 1 ninja K, 2 sura M, 3 szaman K, 4 woj.K, 5 ninja M, 6 sura K, 7 szaman M).
- **Avatary** — są w paczkach klienta, czytelne naszym `eterpack.py` (pack `ETC`):
  - `d:/ymir work/ui/game/windows/face_{warrior,assassin,sura,shaman}.sub` → wycinek 44×44 z
    `Windows.dds` (np. warrior: left 156 top 152 right 199 bottom 195) — ikona z okna postaci w grze,
  - `d:/ymir work/ui/intro/public_intro/face/face_<klasa>_<m|w>_0{1,2}.sub` → `public_intro.dds`
    (z podziałem na płeć — lepsze do panelu).
  Jednorazowy skrypt: `.sub` → wycięcie z DDS (Pillow) → `web/static/faces/<klasa>_<m|w>.png`.

## 4. Plan etapów (każdy kończy się widocznym efektem)

- **W0 — podłączanie po PID** (mały, od razu przydatny): `RadarMap(pid=...)`, `-p/--pid` w CLI, lista
  procesów gry; logi/ustawienia/ui_state per postać (klucz = nick). Dwa radary tk na dwóch klientach.
- **W1 — tożsamość postaci**: `identity.py` (nick, klasa, płeć, lvl, HP/PM) + wyciągnięcie avatarów.
  Widoczne w obecnym radarze (tytuł okna / pasek).
- **W2 — rozdzielenie logiki od widoku**: `BotCore` (stan + pętle + `settings` jako dataclass/dict +
  `snapshot()` + `command()`), tkinter zostaje cienką nakładką. Największy etap — refaktor bez zmiany
  zachowania; uwaga na równoległe sesje edytujące `radar_map.py`.
- **W3 — hub + lista klientów w przeglądarce**: FastAPI + WebSocket, odpowiednik lewej kolumny m2bob.
- **W4 — widok klienta**: zakładki z opcjami + radar na canvasie.
- **W5 — funkcje grupowe** (to, czego tkinter nie da łatwo): hub widzi wszystkie postacie naraz →
  follow drugiej postaci, buffy szamana na postać z innego klienta (z `ANALIZA-skille-follow-kon.md`),
  wspólne profile ustawień.

Stos: Python `fastapi` + `uvicorn` (hub i worker), frontend TypeScript (Vite + React/Svelte) albo na start
statyczny HTML+JS bez kroku budowania.

## 5. Stan realizacji

**W0 + W1 zrobione (2026-10-28). Test na żywo GameCoreTop OK** (Tester3: nick @+0x14, lv 5, wojowniczka —
model `pc2/warrior` zgodny z `job=4` w bazie; nagłówek + tytuł okna + geometria per nick). NIE sprawdzone: vCore,
dwa klienty naraz.
- `recon/clients.py` — lista klientów (PID, exe, tytuł okna), `pick(exe, pid)`; `python recon/clients.py` wypisuje listę.
- `radar_map.py -p PID` (bez `-e`: jedyny klient z GameCoreTop/vCore; przy kilku — błąd z listą PID).
  Geometria okna w `data/ui_state.json` per nick (`chars`), ślad sesji `data/logs/<stamp>_radar_<nick>_*`
  (ta część siedzi w kodzie `bottrace` z innej sesji — jeszcze niezacommitowana).
- `launch.py --new` uruchamia KOLEJNEGO klienta i czeka na nowy PID; `-p` podpina radar do istniejącego.
- `recon/identity.py` — nick (GameCoreTop +0x14; inne buildy: sonda std::string w pierwszych 0x200 B,
  offset logowany jako `[id] nick @ CPythonPlayer+0x..`), klasa/płeć z modelu własnego aktora,
  zapas: `player.job` z bazy; poziom z punktu [1].
- `recon/extract_faces.py` → `data/faces/` (13 PNG). Atlasy DDS leżą w `d:/ymir work/ui/` (nie obok `.sub`).
- Radar: nagłówek panelu (portret + „Nick - Klasa lv N”) i tytuł okna z nickiem i PID.

**W2 jako cienka warstwa API (2026-10-28)** — pełny podział `radar_map.py` odłożony (inna sesja go edytuje):
- `recon/botapi.py`: `snapshot()` czyta pola RadarMap (tożsamość, HP/PM, pozycja, mapa, opcje, buffy, statusy,
  opcjonalnie encje w promieniu 6000); `command(name, value)` ustawia zmienną tk i woła istniejący handler `_on_*`
  w wątku GUI (`root.after` + czekanie na wynik) = jak klik w panelu. Opcje opisuje tabela `OPTIONS` w module;
  opcje nieobecne w danej wersji radaru są pomijane (np. `pot_sizes` z pracy innej sesji).
- Serwer HTTP (stdlib) 127.0.0.1, losowy port, nagłówek `X-Bot-Token`; port+token w `data/workers/<pid>.json`
  (znika przy wyjściu radaru) — po tym pliku hub (W3) znajdzie workery.
  `GET /api/snapshot[?entities=1]`, `GET /api/options`, `POST /api/command {"name","value"}`.
- W `radar_map.py` tylko import + `self.api = botapi.start(self)` na końcu konstruktora.
- Przetestowane na atrapie radaru z prawdziwym tkinterem (token 401, snapshot, komendy w MainThread, clamp,
  błędy 400/404, sprzężenie pot_low→pot_high z handlera). **Test z grą OK** (GameCoreTop, Tester3 lv 6):
  snapshot 24 ms z encjami / 1 ms bez, 62 moby; komendy farm_radius/pot_cd/autoloot/pot_low widoczne w panelu tk,
  wartości przywrócone, log radaru bez błędów.

**W3 — hub + lista postaci w przeglądarce (2026-10-28)**, `python recon/hub.py` → http://127.0.0.1:8765:
- `recon/hub.py` (stdlib, bez instalacji): łączy `clients.list_clients()` z `data/workers/*.json`, pobiera snapshoty
  równolegle (timeout 1,5 s), usuwa pliki po martwych radarach. Akcje: polecenie do radaru, start radaru (proces
  odłączony od huba, log `logs/radar_<pid>.txt`), stop przez `POST /api/shutdown` w botapi (czysto: zapis okna),
  pokaż/ukryj okno gry (`ShowWindow`, także okna już ukryte), nowy klient (`launch.py --new`).
  Ochrona: tylko 127.0.0.1, sprawdzany `Host` (DNS rebinding) i `Origin` przy POST.
- `web/` (czysty HTML/CSS/JS, bez budowania): tabliczki postaci jak lewa kolumna m2bob — portret z gry w złotej
  ramce, nick, klasa z płcią, poziom, mapa, paski HP/PM, przełączniki (Mikstura, Loot, Farma, Chodzenie, Buffy —
  te, które radar ma), Pokaż/Ukryj okno gry, Uruchom/Zamknij radar. Odświeżanie co 1 s bez przebudowy DOM.
- Test na żywo OK (Tester3): start radaru z panelu, Loot wł./wył., ukrycie i pokazanie okna gry (radar dalej
  czyta HP), zamknięcie i ponowne uruchomienie radaru, widok mobilny. NIE testowane: „Uruchom nowego klienta”
  z panelu (ta sama ścieżka co `launch.py --new`, sprawdzona wcześniej z konsoli).

**W4 — widok postaci (2026-10-28)**, `#/p/<pid>` (nick na liście jest linkiem):
- Radar na `<canvas>`: tło = `data/maps/<mapa>.png` z huba (`/maps/…`, px = świat/100), encje kolorami rodzajów,
  metiny jako kwadraty, waypointy trasy (bieżący jaśniejszy), trasa A*, okrąg farmy, pierścień celu walki; zoom
  przyciskami i kółkiem. Odświeżanie ~4×/s (`GET /api/clients/<pid>/snapshot`, bez piętrzenia zapytań).
- Zakładki opcji (Mikstura, Loot, Farma, Chodzenie, Buffy, Speedhack) generowane z `options` radaru: przełączniki,
  suwaki (wysyłka po puszczeniu; zależne suwaki aktualizowane z odpowiedzi), rozmiary mikstur, buffy z listy radaru.
- botapi: `overlay` w snapshocie z encjami + opcja `loot_radius` (z pracy drugiej sesji, gdy istnieje).
- hub: `Cache-Control: no-cache` dla plików strony (inaczej po zmianie app.js przeglądarka trzymała stary).
- Test na żywo OK (Tester3): mapa i encje zgodne z pozycją, suwak klawiaturą (pot_cd 0.30→0.35→0.30 w radarze),
  zakładki, zoom, powrót do listy. NIE testowane: klikanie po mapie (dodawanie waypointów) — nie ma jeszcze tej funkcji.
