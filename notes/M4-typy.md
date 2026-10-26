# M4 — Dziennik: typy encji, VID, układ współrzędnych

## Znalezione pola w obiekcie encji (vtable baza+0x3D5000)
- **VID**: offset **0x2b0** — unikalny dla każdej encji (207/207 różnych, wartości ~9800-22500).
- **Pozycja**: X=0x268, Y=0x26c (z M3).
- **f5c** (offset 0x5c): wartość 8 (większość) vs 0 (mniejszość). Dzieli encje na dwie grupy:
    - f5c==8: race@74 ∈ {80,100,110,140,160,220,300...} — to moby (wilki/psy różnych ras).
    - f5c==0: race@74 ∈ {29,36,44} — inna grupa (NPC? inny gatunek?). DO USTALENIA.
- **race@74** (offset 0x74): rasa/vnum postaci (kilkanaście wartości). Kandydat na pole typu.
- Uwaga: metin NIE został jeszcze jednoznacznie zidentyfikowany (test "nieruchomości" nie
  wyodrębnił go — zbyt wiele mobów stało w miejscu; wszystkie <2.0 ruchu).

## WAŻNE ustalenia
- **Gracz (hero) NIE jest na liście encji** (vtable 0x5e5000) — porusza się inaczej niż wszystkie
  encje. Hero ma osobną klasę. Dla radaru OK (gracz = środek z globalnej pozycji M2).
- **Oś Y odwrócona** między globalną pozycją gracza (M1/M2) a pozycją encji: we wszystkich
  pomiarach encje miały Y < Y gracza (choć na ekranie są też na północ). Zgadza się z negacją
  `m_kPPosCur.y = -m_y` ze źródeł. → przy radarze ODBIĆ oś Y; potwierdzić wzrokowo.
- X zgadza się między układami (gracz X zawsze w środku rozrzutu encji X).

## OSTATECZNA pozycja encji (find_hero.py) — POPRAWNE
- Triplet na pozycji gracza: (0x264=globalX, 0x268=-43258, 0x26c=globalY).
- **world X = 0x264, world Y = 0x26c** — gracz i moby w tym samym układzie (global 0x6a3014/0x6a301c).
- **0x268 = pixel-Y / wartość przekształcona** (gracz -43258 vs moby +40000; negacja z M1) — IGNORUJEMY.
- Radar: X=0x264, Y=0x26c, środek = global gracza. Lokalnie moby bywają w pasie E-W (mały rozrzut Y).

## POTWIERDZENIE (track_one.py): pozycje są w układzie ŚWIATA
- Obrót kamery o 180° (postać stoi) → ŻADNE pole się nie zmieniło (d=0.0). Pozycje encji są
  camera-independent. „Radar rusza się z kamerą" to był efekt porównania z obracającym się ekranem.
- X=0x264, Y=0x26c potwierdzone. Dodatkowe kopie: X też na 0x1c0/0x270/0x27c, Y na 0x1c8/0x278.
- 0x268 = pixel-Y (gracz -43258, moby +32000) — ignorować.
- "Linia" na radarze = moby lokalnie stoją w pasie E-W (Y zmienia się mało), to realna geometria.

## WNIOSEK: bliskie metiny = system obiektów MAPY (nie postacie) — WYCZERPUJĄCO SPRAWDZONE
- Pełna enumeracja 177 CInstanceBase (vtable base+0x3D5000, baza DYNAMiczna ~0xb30000): tylko 1 metin
  (Metin Cierpienia 8001, daleki). Bliski "Metin Walki" (vnum 8002) NIE istnieje jako żywa postać.
- find_vnum8002.py: 0 obiektów vnum 8002 pod 0x4B0 z poprawną vtable.
- find_walki_live.py: "Metin Walki" (inline) ani "Metin Cierpienia" (wskaźnik) nie wiszą na 0x354
  żadnego żywego obiektu z modułową vtable — tylko bufory tekstu/UI i zwolnione obiekty (vtable=0).
- near_vtables.py: w promieniu 1500 od gracza TYLKO vtable mobów ma obiekty z pozycją @0x264/0x268.
  Żadna inna klasa-postać nie stoi przy graczu.
- → Bliski/atakowalny metin renderuje system OBIEKTÓW MAPY/TERENU (CPythonBackground, obiekty sektora),
  pozycje inaczej (graf sceny). To OSOBNY DUŻY PODSYSTEM, nie kolejny offset. Do przyszłej sesji,
  jeśli w ogóle warto (radar wrogów działa bez tego).

## (stary) drobny problem: pozycja metinów
- metin_check.py: przez vtable mobów (0x5e5000) + nazwa "Metin" wykrywa tylko 1 metin, często
  daleki (dist 6940), choć na ekranie są 2 i gracz stoi przy jednym. → METINY to prawdopodobnie
  OSOBNA KLASA (inna vtable, jak hero) i/lub inny offset pozycji. Ten w liście mobów to duplikat.
- USTALENIA (find_metin_class.py, nearby.py, chk):
  - 59 napisów "Metin ..." w pamięci = SŁOWNIK typów (definicje), nie instancje.
  - Tylko część metinów ma obiekt z nazwą w liście mobów (np. 0x22b9a4b4 "Metin Cierpienia",
    vtable mobów, ale daleki). Bliski metin NIE ma nazwy pod 0x354 → metiny inaczej trzymają nazwę.
  - Obiekty w dist=0 (pos = pozycja gracza) to GRACZ (hero) w liście mobów (2 pod-obiekty, jeden vid=0),
    NIE metin. Czyli hero JEST też w liście 0x5e5000 (2 obiekty na pozycji gracza).
  - race@74 NIE jest typem (hero=59, metin=110, różne). f5c też nie.
- DO ZROBIENIA (następna sesja): znaleźć VNUM metina (Metin2: zwykle 8xxx) lub pewny odczyt nazwy;
  metiny wykrywane jako punkty, ale kolorowanie żółte zawodzi. Rozważyć osobną vtable metina.

## ŚRODEK RADARU — obiekt HERO (cam_test.py, hero_final.py) — ROZWIĄZANE
- PROBLEM: global gracza (0x6a3014/0x6a3018) to pozycja w układzie KAMERY (zmienia się przy
  obrocie kamery!) → dlatego radar „kręcił się". Tylko 0x6a301c (wysokość) stabilne.
- ROZWIĄZANIE: obiekt gracza (hero) ma UNIKALNĄ vtable **base+0x3D6CA0** (dokładnie 1 obiekt).
  Pozycja świata: **X @ obj+0x8, −Y @ obj+0xC, Z @ obj+0x10**. STABILNA przy obrocie kamery (d=0.0).
- Środek radaru = (hero+0x8, −(hero+0xC)). Moby w (0x264, 0x268). Teraz radar NIE kręci się z kamerą.
- radar_preview.py: szuka hero po vtable w rescan(), centruje z tej pozycji.

## KLASYFIKACJA przez NAZWĘ (classify.py) — ROZWIĄZANE
- Nazwa encji = std::string na offsecie 0x354 (MSVC 32-bit: buf[16], size@+0x10, cap@+0x14).
  cap<16 → inline; cap>=16 → 0x354 to WSKAŹNIK (np. "Metin Cierpienia" = 16 znaków → wskaźnik).
- Poprawne nazwy: Wilk, Dziki Pies, Głodny Zabłąkany Pies, Nieb. Alfa Wilk, "Metin Cierpienia".
  Metin JEST na liście (ta sama vtable). race@74 to NIE typ (porzucone) — nazwa jest pewna.
- Radar: metin=żółty kwadrat, mob=czerwona kropka, unknown=szary.

## OSTATECZNY schemat współrzędnych (dump_positions.py + find_hero2.py) — TO JEST POPRAWNE
- Płaszczyzna 2D mapy = **(X=0x264, Y=0x268)**. ratio std ~1.2 = prawdziwe 2D (scatter = chmura).
  Para (0x264, 0x26c) dawała ratio ~9 = PAS (0x26c to wysokość/Z) — stąd linia. Błąd naprawiony.
- Środek radaru = obiekt HERO: inna vtable **base+0x3cfd10** (nie mob base+0x3D5000),
  znajdowany jako jedyny obiekt z prawdziwą vtable, którego 0x264 ≈ globalX.
- Hero ma PRZECIWNY ZNAK na 0x268 niż moby → środek Y = **-hero.0x268**. Wtedy moby tworzą
  chmurę 2D wokół gracza (gracz bywa na skraju stada — zgodne z ekranem).
- ŚRODEK (prosto i stabilnie, bez szukania hero): X = global 0x6a3014, Y = **−global 0x6a3018**
  (środkowa wartość globalnego tripletu gracza ma przeciwny znak niż 0x268 mobów).
  Zweryfikowane: gracz ląduje w ŚRODKU chmury mobów (157 w promieniu 12000). ✓
- radar_preview.py: ENT_OFF_Y=0x268, środek z globali (0x6a3014, -0x6a3018).

## (nieaktualne) próba pos_fields.py — WAŻNE
- Poprzednio błędnie: X=0x268, Y=0x26c. Okazało się: 0x268 to Y, a 0x26c to Z (wysokość,
  std~420 na płaskim → moby układały się w linię).
- Prawdziwa pozycja ŚWIATA encji: wektor {x,y,z} po 12 bajtów:
    **X = 0x264, Y = 0x268, Z = 0x26c**  (std X/Y ~4000 = dobry rozrzut 2D).
- Kolejne wektory (dst/src) od 0x270, 0x27c (ten sam wzorzec, Z na 0x278/0x284).
- To są współrzędne świata (niezależne od kamery; ~30 "zmian" przy obrocie to łażące moby).
- Radar poprawiony na X=0x264, Y=0x268 + klawisze [ ] do dostrajania offsetu na żywo.

## Plan dalej
- Zbudować podglądowy radar (Python) — natychmiastowy efekt wizualny + kalibracja orientacji
  (odbicie Y / obrót) na żywo względem ekranu. To też zweryfikuje wykrywanie encji lepiej niż logi.
- Potem wrócić do klasyfikacji: z radarem łatwiej wskazać metina wzrokowo i odczytać jego pola.

## ROZWIAZANE: wykrywanie bliskich metinow przez EFEKT swiecenia (metin_detect.py)
- Bliski metin NIE jest postacia (CInstanceBase) — potwierdzone ostatecznie. Jest renderowany
  jako EFEKT: CEffectInstance (vtable base+0x3ce250, RTTI ".?AVCEffectInstance@@").
- IDENTYFIKACJA metina: m_pkEftData @ CEffectInstance+0x290 -> CEffectData; nazwa pliku =
  std::string @ CEffectData+0x44 (MSVC: cap@+0x14>=16 => wskaznik, inaczej inline). Metin =
  nazwa zawiera "metinstone" (d:/ymir work/effect/background/metinstone_loop_*.mse).
- POZYCJA swiata (translacja m_matGlobal): X=+0x280, Y=-(+0x284), Z=+0x288. Y NEGOWANE jak hero.
- WERYFIKACJA (na zywo, gracz (29279,35064)): 2 metiny, oba STABILNE (nieruchome):
    bliski  dist=422  (29638,34841)  metinstone_loop_2_aqua.mse   <- "Metin Walki" obok gracza
    daleki  dist=4704 (29389,39767)  metinstone_loop_2.mse
- RTTI DZIALA w tym kliencie: vtable-4 -> COL; COL+0xC -> TypeDescriptor; TD+8 -> nazwa ".?AV...".
  To mocne narzedzie do identyfikacji klas (uzyte w rtti_near.py / stationary_rtti.py).
- Uwaga: "Metin Cierpienia" (race 8001) w liscie postaci to PULOWANY/nieaktualny obiekt
  (pozycja wedrowala 26229,39031 -> 24563,38153), NIE odpowiada zadnemu realnemu metinowi.
  Dlatego podejscie przez liste postaci zawodzilo. Prawdziwe metiny = tylko efekty metinstone.
- Filtr innych efektow tla: fire_general, warpgate, direction, empire, armor/sword/gm (gracz) —
  odrzucane bo brak "metinstone" w nazwie.
