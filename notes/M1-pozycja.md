# M1 — Dziennik: pozycja gracza

> Zapisuj tu każdy znaleziony adres/offset + JEDNO zdanie „jak go znalazłem".

## Środowisko (Zadanie 1)
- Data: 2026-10-21
- pymem zainstalowany: TAK (1.14.0)
- Podpięcie do GameCoreTop.exe działa: TAK — ale TYLKO gdy terminal uruchomiony jako Administrator
  (bez admina: CouldNotOpenProcess / OpenProcess odmawia). Lekcja: odczyt pamięci = bariera uprawnień.
- PID z sesji: 1744 (zmienny)
- Adres bazowy modułu: 0x210000 (zmienny — ASLR)

## Metoda (Zadanie 2)
- Zamiast ręcznego Cheat Engine używamy własnego skanera `recon/scan_floats.py` (migawki + filtry
  inc/dec/same/changed) — bardziej w duchu Podejścia A, mechaniczną część robi kod.

## Adresy współrzędnych (Zadanie 2) — UWAGA: dynamiczne, zmieniają się po restarcie gry
- Adres X: 0x6a3014  (typ Float, wartość ~40-42 tys.) — znalezione skanerem: filtry changed/same
  → 150 kandydatów → watch + ruch; ta wartość zmieniała się płynnie i monotonicznie.
- Adres Y: 0x6a301c  (typ Float, wartość ~18-19 tys.) — j.w. (8 bajtów za X).
- Adres Z (opcj.): prawdopodobnie 0x6a3018 (między X a Y) — do potwierdzenia.
- Pozycja kamery (NIE gracza): 0x6a2930 / 0x6a2960 / 0x6a2980 — drgały skokowo, większe wahania.
- Która oś to która: PROWIZORYCZNIE 0x6a3014=X, 0x6a301c=Y; dokładne mapowanie osi do potwierdzenia
  ruchem wzdłuż jednej osi świata.
- Realny zakres wartości X/Y (do sanity-check): X ~ 40000-42000, Y ~ 18000-19000 w tej lokacji;
  ogólnie współrzędne świata rzędu 10^4-10^5.
- UWAGA: adresy z TEJ sesji gry, dynamiczne (zmienią się po restarcie) → M2 zrobi pointer path.

## M2 — stabilny dostęp
- addr_info.py: 0x6a3014 jest WEWNĄTRZ modułu GameCoreTop.exe (baza 0x210000, rozmiar 0x50d000).
  → adres STATYCZNY, offset od bazy = 0x493014. Brak potrzeby pointer scan!
- Stabilny dostęp: base_modulu + 0x493014 (X), +0x49301c (Y), +0x493018 (Z?).
- pymem odczytuje bazę modułu co uruchomienie → przeżywa restart i ASLR.
- TEST PO RESTARCIE (read_player_stable.py): odczyt poprawny, X/Y zgodne z ruchem. ✅
- UWAGA: baza po restarcie = znowu 0x210000 → ASLR dla tego exe prawdopodobnie WYŁĄCZONY
  (stały ImageBase). Tu nawet goły adres absolutny byłby stabilny; base+offset jest poprawne
  w obu wariantach. [Nota do obrony: brak ASLR = łatwiej atakować.]
- Z @ 0x6a3018 to NIE wysokość (skacze ~4000 przy bieganiu po płaskim) — pewnie bleed kamery.
  Dla radaru nieistotne (potrzeba tylko X/Y). Prawdziwe Z znaleźć później, jeśli trzeba.
- Klawisze (GetAsyncKeyState) działają: RMB+WASD sterowanie, LMB klik-chodzenie, CTRL itd.
- M2 ZALICZONE. Deliverable: recon/addr_info.py, recon/read_player_stable.py, logs/read_player_stable.log.

## Weryfikacja ze źródeł (Zadanie 4)
- Klasa: CActorInstance (GameLib/ActorInstance.h). Pola: `float m_x; m_y; m_z;` (linia 763+).
- TPixelPosition = struktura {float x, y, z}. Warianty: m_kPPosCur/Src/Dst/Atk/Last.
- ActorInstancePosition.cpp:77-79:  m_kPPosCur.x=+m_x;  m_kPPosCur.y=-m_y;  m_kPPosCur.z=+m_z;
  → KLIENT NEGUJE Y w reprezentacji PixelPosition. Ważne dla radaru (oś Y do odbicia).
- Potwierdzenie, że to float X/Y świata: TAK.

## Wnioski
- M1 ZALICZONE: zewnętrzny odczyt pozycji (ReadProcessMemory via pymem) działa i zgadza się z ruchem.
- Odczyt wymaga uprawnień administratora (OpenProcess).
- Adresy dynamiczne → M2 zrobi stabilny wskaźnik (pointer scan).
- Pamiętać o negacji Y przy rysowaniu radaru (M6).
- Deliverables: recon/_env_check.py, recon/scan_floats.py, recon/read_player.py.
