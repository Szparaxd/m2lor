# Analiza rozbudowy radaru/bota — 5 nowych funkcji

Data: 2026-10-26. Źródło: recon 4 subagentów Opus na `Source Client` + konwencje projektu.
Status: **analiza (nic jeszcze nie zbudowane)**. To jest „jak najlepiej to zrobić", nie implementacja.

Klient: dwa buildy — `GameCoreTop.exe` (python27.dll, pending-call opróżniany) i `vCore.exe`
(hook per-klatka na RunOneFrame). Kanał sterowania = potok `\\.\pipe\m2autopot_<pid>` → DLL.

---

## Podsumowanie i rekomendowana kolejność

| # | Funkcja | Trudność | Czy realnie działa | Ryzyko serwera |
|---|---------|----------|--------------------|----------------|
| 1 | **Gracze osobnym kolorem** | trywialna | TAK (czysty odczyt) | brak |
| 2 | **Auto-loot** | średnia | TAK (serwer waliduje tylko dystans) | niskie |
| 3 | **Auto-chodzenie po ścieżce** | średnio-wysoka | TAK (prawdziwy pathing klienta) | średnie |
| 4 | **Szybszy ruch** | niska | **NIE** na dobrze zabezp. serwerze | wysokie (gumka/kick) |
| 5 | **Szybszy atak** | niska | **NIE** na dobrze zabezp. serwerze | wysokie (kick) |

**Proponowana kolejność: 1 → 2 → 3.** Funkcje 4 i 5 traktować jako **eksperyment do fazy
obrony** (sprawdzenie, czy Twój serwer waliduje prędkość), a nie jako działający cheat.

Kluczowy podział architektoniczny:
- **Odczyt** (radar): funkcje 1 + wykrywanie dropów w 2 + pozycje w 3 — tylko `ReadProcessMemory`.
- **Sterowanie przez Pythona** (istniejący potok → `Py_AddPendingCall` → `PyRun_SimpleString`):
  działa na GameCoreTop. Dotyczy auto-loot (`player.PickCloseItem()`).
- **Sterowanie natywne** (hook per-klatka, jak picie na vCore): potrzebne tam, gdzie funkcji
  NIE ma w Pythonie — **auto-chodzenie** (`NEW_MoveToDestPixelPositionDirection`) i speed (`SetMoveSpeed`).

---

## 1. Gracze osobnym kolorem (najłatwiejsze — zacząć tu)

**Dlaczego teraz źle:** radar koloruje po vnum/randze z `mob_proto`; gracze mają vnum = rasa
(0–7), nie ma ich w proto → gałąź `mob` (czerwony).

**Rozwiązanie:** klient trzyma autorytatywny typ encji z serwera w polu
`CActorInstance::m_eActorType` (DWORD). Enum `EType` (`GameLib/ActorInstance.h:75-89`):

```
0 ENEMY(mob)  1 NPC  2 STONE(metin)  3 WARP  4 DOOR  5 BUILDING
6 PC(GRACZ)   7 POLY  8 HORSE  9 GOTO  10 OBJECT
```

**Offset — za darmo z istniejącej kalibracji:** `m_eActorType` leży **4 bajty przed** `m_eRace`
(= vnum, który radar już czyta). Czyli `actor_type_off = vnum_off - 4` (domyślnie **-0x74**).
Delta -4 jest stała w obu buildach (oba pola w tej samej strukturze), więc po kalibracji vnum
masz typ gratis.

**Co zrobić w `radar_map.py`:** w `_iter_mobs` odczytać `atype = read_uint(o + vnum_off - 4)`;
w `_classify` rozstrzygać najpierw po `atype` (6→nowy kolor „player", 1→npc, 2→metin, 0→mob,
dalej boss/king z proto jak teraz), fallback po vnum<8 gdy `atype` poza 0–10.

**Nakład:** ~1 h. Czysty odczyt, zero ryzyka. **Dobry pierwszy krok — namacalny efekt od razu.**

---

## 2. Auto-loot (zbieranie z ziemi)

**Wykrywanie dropów:** singleton `CPythonItem`, mapa `m_GroundItemInstanceMap`
(`UserInterface/PythonItem.h:72`), **klucz = VID**. Struktura `TGroundItemInstance`
(PythonItem.h:44-70) ma wirtualny destruktor → **vtable na +0x00** → radar skanuje tą samą
metodą RTTI co `CEffectInstance`. Pola: `+0x04` vnum, `+0x08/0x0C/0x10` = pozycja (x, −y, z)
— **offsety do kalibracji na żywo** (jak eff_x). Konwencja −Y i px=wx/100 identyczna jak w radarze.

**Niuans VID:** VID jest kluczem mapy, nie ma go w strukturze. Skan RTTI daje vnum+pozycję
(do radaru/decyzji). Do precyzyjnego podnoszenia po VID trzeba przejść drzewo `std::map` MSVC
albo użyć wariantu bez-VID.

**Podnoszenie (przez istniejący potok + pending-call, działa na GameCoreTop):**
- **Prosto (MVP):** `player.PickCloseItem()` — klient sam bierze najbliższy ≤300 jedn.
  (`PythonPlayerInput.cpp:45,18`; 500 na koniu). Zero czytania VID. Minus: bierze cokolwiek.
- **Selektywnie po vnum:** `net.SendItemPickUpPacket(vid)` (`PythonNetworkStreamModule.cpp:843`)
  — pakiet = nagłówek + VID, pomija kliencki check ownership (serwer i tak waliduje).

**Zasięg:** trzeba być w zasięgu (≤300). Po grindzie dropy zwykle są blisko → MVP może polegać
na samym `PickCloseItem()` bez ruchu; pełny zasięg wymaga funkcji ruchu (p. 3).

**Pętla (w radarze, jak `_autopot_loop`):** skan RTTI → filtr vnum (plik `data/loot_vnums.txt`,
pusty=wszystko) → najbliższy w ≤~290 → komenda przez potok → **throttle po naszej stronie**
(brak limitera w pakiecie!): cooldown ~150–300 ms + zbiór „ostatnio żądane" z TTL ~1 s
(klucz = VID lub (round(wx),round(wy),vnum)), bo pakiet jest asynchroniczny i item znika z mapy
dopiero po ~0.3–1 s.

**Format komendy:** `player.PickCloseItem()` albo
`__import__('net').SendItemPickUpPacket(VID)` (jedna linia).

**Nakład:** ~pół dnia (RTTI dropów + kalibracja offsetów + pętla). **Dobra wartość, działa.**

---

## 3. Auto-chodzenie po narysowanej ścieżce (waypointy)

**Jak klient naprawdę chodzi:** klik w ziemię →
`CInstanceBase::NEW_MoveToDestPixelPositionDirection` → `NEW_Goto`
(`InstanceBaseMovement.cpp:242,251`) → lokalna animacja marszu. Pakiety pozycji lecą **same** z
handlerów `OnMove/OnMoving` co ~300 ms (`PythonPlayerEventHandler.cpp:63,77`) jako
`HEADER_CG_CHARACTER_MOVE` (bieżąca pozycja, nie teleport). Serwer dostaje walidowalny strumień.

**Czego NIE robić:** `chr.SetPixelPosition` = tylko lokalne ustawienie pozycji, bez pakietu →
przy najbliższym OnMove klient wyśle „skok" → anti-speedhack: gumka do ostatniej pozycji / kick.

**Problem:** prawdziwa ścieżka ruchu **nie jest wystawiona do Pythona** (brak `player.goto(x,y)`;
są tylko `StartMouseWalking`/`EndMouseWalking`, nie „do punktu"). Więc czysty `PyRun_SimpleString`
jej nie dosięgnie.

**Rozwiązanie = wywołanie natywne z hooka per-klatka** (główny wątek, bufor pakietów bezpieczny —
dokładnie jak picie na vCore). Nowa komenda natywna `!goto <gx> <gy>`, analogicznie do `!drink`.
Do zlokalizowania w binarce (RVA, jak przy drink):
- `CPythonPlayer`/`NEW_GetMainActorPtr()` → wskaźnik głównego aktora.
- `CInstanceBase::NEW_MoveToDestPixelPositionDirection` (`__thiscall`, 1 arg = TPixelPosition*{x,y,z}).
- `CPythonBackground::GlobalPositionToLocalPosition` (radar ma globalne, `NEW_Goto` chce lokalnych)
  — albo konwersja natywnie w DLL, albo w radarze; rekomendacja: radar operuje globalnymi, DLL konwertuje.
- `NEW_Stop`/`EndWalking` dla komendy `!stop`.

**Pętla (w radarze):** lista waypointów (świat) → dla celu i: `d=hypot(cel-hero)`; `d<PRÓG`
(~100–200 jedn.) → następny; inaczej (re)wyślij `!goto` (nie co klatkę — aktor idzie sam;
keep-alive co 500–1000 ms). **Anti-stuck:** jeśli `d` nie maleje przez ~1.5 s mimo ruchu →
kolizja (`NEW_CanMoveToDestPixelPosition`), ruch pośredni w bok (offset ±X), po N próbach pomiń
waypoint. Nie wysyłaj celu bliższego niż kilkadziesiąt jednostek (`NEW_Goto` ignoruje za bliskie).

**UI:** user klika na radarze → waypoint w świecie; lista edytowalna; start/stop/pętla.

**Nakład:** 1–2 dni (najwięcej RE — znalezienie RVA funkcji ruchu + hook na GameCoreTop,
dziś hook per-klatka mamy tylko na vCore). **Działa i jest realistyczne na poziomie pakietów.**

---

## 4 + 5. Szybszy ruch i szybszy atak — werdykt: na dobrze zabezpieczonym serwerze NIE

**Gdzie to żyje:** `POINT_ATT_SPEED`=17, `POINT_MOV_SPEED`=19 w tablicy punktów XOR 0xE73AC1DA
(`Packet.h:1519+`; offsety +0x44 / +0x4C; 100=100%). ALE — **te wartości zasilają tylko UI**.
Realna prędkość animacji (`m_fMovSpd`/`m_fAtkSpd`, `ActorInstance.h:773-774`, init 1.0) jest
ustawiana **z pakietów serwera** `char_update` (`PythonNetworkStreamPhaseGameActor.cpp:242,271`
→ `NetworkActorManager.cpp:490-491` → `SetMoveSpeed/SetAttackSpeed`). Nie ma ścieżki, w której
lokalna tablica XOR zmienia realną prędkość.

**Werdykt:**
- **Ruch:** edycja XOR → tylko liczba w UI. Natywne `SetMoveSpeed` przyspieszy animację (ruch
  jest root-motion, więc i lokalną pozycję), ale serwer autorytatywnie waliduje dystans/czas
  → gumka / kick. Dodatkowo serwer co `char_update` nadpisuje `m_fMovSpd`.
- **Atak:** szybsza animacja → więcej `SendAttackPacket` (`PythonPlayerEventHandler.cpp:136,169`),
  ale serwer liczy własny cooldown z atk_speed i odrzuca nadmiarowe. Zysk minimalny, ryzyko duże.
  Klient ma wręcz wbudowany detektor `ATTACK_TIME_LOG` → `ATTACK(SPEED_HACK)` przy odstępie <0,1 s.

**Jeśli jednak eksperymentować** (by sprawdzić, czy *Twój* serwer waliduje): natywnie w hooku
per-klatka co klatkę przepisywać `m_fMovSpd/m_fAtkSpd` na gracza albo hookować
`CInstanceBase::SetMoveSpeed/SetAttackSpeed` i mnożyć argument. Do skalibrowania: adresy tych
funkcji + offsety pól float (sąsiednie, init 1.0) + wskaźnik gracza.

**To jest najcenniejsze dla FAZY OBRONY:** jeśli przyspieszenie zadziała na Twoim serwerze =
masz lukę do załatania (brak server-side walidacji prędkości/cooldownu).

---

## Faza obrony (notatki na później, zebrane od agentów)

- **ESP/radar:** wykrywać uchwyty z `PROCESS_VM_READ` na proces klienta; honeypot-encje poza
  zasięgiem widzenia; **najmocniej: serwer nie wysyła danych o encjach poza realnym LoS** — nie ma czego czytać.
- **Auto-loot:** server-side rate-limit + analiza rozkładu czasów (wariancja), honeypot-dropy,
  korelacja z brakiem ruchu kursora.
- **Auto-chodzenie:** entropia trasy (idealnie proste odcinki, zerowa wariancja), równe interwały
  300 ms 24/7, ruch bez poprzedzającego inputu; server-side pathfinding/kolizje.
- **Speed:** twarda walidacja czasu/dystansu między pakietami + własny cooldown ataku + progi naruszeń.
  Wszystko, co robi wstrzyknięta DLL, to tylko *propozycja* — serwer musi ją weryfikować.
