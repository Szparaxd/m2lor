# M3 — Dziennik: lista encji (vtable scan)

## Metoda
- find_actors.py: szukamy par (X,Y) w promieniu wokół gracza → analiza „wstecz" po wskaźniku
  w modul (vtable) → filtr is_vtable (wskazuje na funkcje) → diagnostyka: liczba trafień,
  odstęp adresów (median_gap), liczba różnych offsetów.
- Rozróżnienie: tablice danych = dużo trafień, mały/regularny gap, dużo offsetów.
  Prawdziwe obiekty postaci = umiarkowana liczba, DUŻY gap (osobna alokacja), mało offsetów.

## Wynik (sesja 23:13, baza 0x210000)
- Śmieci odrzucone: 0x5ea0fc (tablica 40000, gap 0x50), 0x5dfbf4 (siatka krok 3200),
  0x5e7984 (krok 200).
- POSTACIE: trzy vtable pod-obiektów tej samej encji dają te same ~224 pozycje:
    0x5e5b30 (xoff 0x258, 450 trafień)
    0x5e4fb0 (xoff 0x510, 224 trafienia)
    0x5e5000 (xoff 0x268, 224 trafienia)  <-- WYBRANE
- Pozycje rozrzucone wokół gracza, gap ~0x7c8 (osobne alokacje) = realne postacie.

## WYBRANE do radaru
- vtable = 0x5e5000  → offset od bazy modułu = 0x5e5000 - 0x210000 = **0x3D5000**
- offset X w obiekcie = **0x268**,  offset Y = **0x26c**  (m_x, m_y sąsiadują)
- Dostęp: dla każdego obiektu O (adres, pod którym w pamięci leży wartość base+0x3D5000):
    X = read_float(O + 0x268),  Y = read_float(O + 0x26c)

## Walidacja z grą (sesja 23:18) ✅
- Scena: gracz przy "Metin Cierpienia", wokół mnóstwo wilków/psów, minimapa czerwona.
- scan_entities.py: 207 encji w r=20000, ~50 w bliskim zasięgu — zgadza się z obrazem.
- Pozycje różne, bez duplikatów, wiele na "okrągłych" X (spawny mobów). Realne postacie.
- OBSERWACJA: gracza NIE ma na liście (najbliższa encja dist~720; Y gracza 18970 > Y mobów).
  Dla radaru OK (gracz = środek z globalnej pozycji M2). Sprawdzić przy M4, czy gracz ma
  tę samą vtable czy inną (hero bywa osobno).
- M3 ZALICZONE. Deliverable: recon/find_actors.py, recon/scan_entities.py.

## TODO / do sprawdzenia
- Potwierdzić, że ~224 to faktycznie widoczne postacie (porównać z grą).
- M4: znaleźć pole typu (mob/gracz/metin) i VID w obiekcie.
- Wydajność: skan całej sterty ~15-30s — za wolno na radar na żywo. Plan: skan zbiera adresy
  obiektów raz, potem tylko re-odczyt pozycji znanych obiektów co klatkę + rzadki pełny rescan.
- UWAGA negacja Y (z M1) — przy rysowaniu radaru oś Y odbić.
