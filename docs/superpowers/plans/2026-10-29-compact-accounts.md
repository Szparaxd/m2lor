# Compact Accounts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task.

**Goal:** Tabela kont, login/hasło przy dodawaniu, opcje sesji przy uruchomieniu.
**Architecture:** Wspólny istniejący magazyn, atomowy import i rozszerzenie launch(options); tabela i modal w natywnym HTML/JS.
**Tech Stack:** Python standard library, HTML/JS/CSS; bez nowych zależności.
**Spec:** docs/superpowers/specs/2026-10-29-compact-accounts-design.md

## Review Focus
- Nie nadpisywać haseł istniejących loginów podczas importu.
- Błędny wiersz i błąd zapisu nie mogą zostawić częściowo dodanych kont.
- Rozdzielić zapis danych od włączenia automatyzacji.
- Zmiana CH/pozycji ma działać również dla klienta już w grze.
- Polling ma zachować zaznaczenia, wpisywanie i otwarte opcje sesji.

### Task 1: Import i opcje sesji
**Files:** recon/autologin.py, recon/hub.py, tests/test_accounts.py, tests/test_compact_accounts.py.
- [x] Testy RED tworzenia tylko z poświadczeń, CSV/TSV, cytowania, duplikatów, atomowości, opcji startu i przelogowania.
- [x] Implementacja i pełny zestaw testów GREEN: początkowo 29/29.

### Task 2: Tabela i kompaktowy wygląd
**Files:** web/accounts.js, web/index.html, web/style.css, web/app.js, README.md.
- [x] Formularz tylko login/hasło, import, stabilne wiersze i zaznaczenia.
- [x] Modal opcji sesji dla jednego lub wielu kont; techniczny styl.
- [x] Testy przeglądarkowe na osobnym magazynie: import, edycja, modal i wybór CH, polling, desktop/mobile.

### Task 3: Sprawdzenie i wdrożenie
- [x] Niezależny przegląd i poprawa istotnych problemów; pełne testy i sprawdzenie składni.
- [x] Restart samego huba (bez zamykania klientów i radarów), otwarcie panelu.
- [x] Commit wyłącznie plików tego zadania i zapis wyników.

## Wynik wykonania
- 33/33 testy `python -m unittest discover -s tests -v`; `node --check` obu plików JS, `py_compile` zmienionego Pythona i `git diff --check` poprawne.
- Niezależny przegląd wskazał zły wybór separatora na podstawie znaków w haśle oraz niezatrzymywanie działającego bota przy opcji bot OFF podczas przelogowania. Oba przypadki mają regresje RED→GREEN i poprawki. Separator wyznacza pierwsze pole CSV; hasło nie wpływa na jego rozpoznanie. Hub przekazuje funkcję zatrzymania radaru do Managera.
- Przeglądarka z osobnym magazynem: dodanie login/hasło bez startu, CSV (dodano 2/pominięto 1), wyczyszczenie pola importu po sukcesie, zaznaczenie trzech kont, wybór CH4 i postaci 3, zlecenie trzech sesji bez uruchomienia prawdziwych klientów testowych, edycja i Enter bez startu. Polling zachowuje wiersze, zaznaczenia i pola.
- Desktop 1100 px: gęsta tabela mieści opcje działających kont; mobile 360 px: szerokość dokumentu 360 px, przewija się wyłącznie tabela 640 px. Brak błędów JS.
- Restart samego huba weryfikuje proces zajmujący port 8765 przed zatrzymaniem. Panel działa jako administrator, ścieżka gry jest poprawna, nowy endpoint importu odpowiada. Wszystkie 3 dotychczasowe konta i ich działające klienty/radary pozostały dostępne. Nie importowano ani nie edytowano prawdziwych poświadczeń w ramach testów.
- Zmiany innych sesji w CLAUDE.md, build.bat, DLL, bottrace/radar_map i plikach danych pozostawiono poza commitem.
