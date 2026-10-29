# Accounts Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wdrożyć zaakceptowany panel kont i uruchamianie bez wcześniejszego otwierania gry.

**Architecture:** Rozszerzamy Manager profili i istniejące lokalne API. Nowy ekran kont korzysta z tych samych procesów, stanów i widoku radaru.

**Tech Stack:** Python standard library, natywny JavaScript, HTML, CSS.

**Spec:** docs/superpowers/specs/2026-10-29-accounts-panel-design.md

## Global Constraints
- Bez nowych zależności; istniejące konta i DPAPI muszą pozostać zgodne.
- Hasła nie są zwracane przez API ani logowane.
- Zachowujemy zabezpieczenia Host/Origin, radar i obsługę klientów bez konta.

## Review Focus
- Nieudany zapis nie może zmieniać profilu ani pozostawiać pustego konta.
- Zapis z restartem nie uruchamia jeszcze nieużywanego konta.
- Podwójne uruchomienie nie tworzy dwóch klientów; ponów odblokowuje błąd na istniejącym.
- Odświeżanie listy nie kasuje wpisywanych danych i nie gubi fokusu.
- Brak pliku gry, uprawnień, klienta i bota daje czytelną następną akcję.

### Task 1: Profile bez działającego klienta
**Files:** recon/autologin.py, recon/hub.py, tests/test_accounts.py
**Interfaces:** Manager.save_profile(body, profile_id=None, pid=None), configure(pid, body) jako zgodny adapter, launch(profile_id), nowe ścieżki HTTP.
- [x] Napisz i uruchom testy tworzenia bez PID, zachowania hasła, atomowej walidacji, starej konfiguracji PID i ponawiania.
- [x] Zaimplementuj zapis z nazwą, walidację, bezpieczny start, statusy i API.
- [x] Uruchom `python -m unittest discover -s tests -v`.

### Task 2: Widok kont
**Files:** web/index.html, web/app.js, web/accounts.js, web/style.css, README.md
**Interfaces:** accountsView.refresh(), openEditor(profile, pid), route #/ i #/konta -> konta, #/postacie -> działające klienty.
- [x] Przebuduj start, formularz i karty zgodnie z makietą; wykorzystaj rzeczywiste statusy i istniejące akcje bota.
- [x] Sprawdź składnię JS i Python, testy API, następnie UI w przeglądarce z izolowanym magazynem i bez uruchamiania klientów.
- [x] Sprawdź dodawanie, edycję z pustym hasłem, zachowanie wpisywania przy pollingu i szerokość mobilną.

### Task 3: Przegląd i udostępnienie
- [x] Świeży przegląd zmian; popraw istotne problemy i uruchom pełne testy.
- [x] Uruchom zaktualizowany hub lokalnie i otwórz właściwy panel.
- [x] Zapisz wyniki w dokumencie wykonania i podaj użytkownikowi stan wdrożenia.

## Wynik wykonania
- 18/18 testów `python -m unittest discover -s tests -v`; rzeczywisty zapis do izolowanych plików i szyfrowanie DPAPI, lokalny HTTP z kontrolą Origin.
- `node --check web/accounts.js`, `node --check web/app.js`, `python -m py_compile recon/autologin.py recon/hub.py`, `git diff --check` — poprawne.
- UI na izolowanym serwerze: dodanie konta bez uruchomienia gry; edycja z pustym hasłem; Enter zapisuje bez uruchamiania; polling zachowuje wpisaną nazwę i fokus; układ mieści się w 360px bez przewijania poziomego; brak błędów JS.
- Niezależny przegląd wskazał trzy istotne problemy. Błąd przypisania klienta przy malejącym PID i znikanie błędu uruchomienia mają testy RED→GREEN. Niezamierzone uruchamianie po Enter odtworzono w UI przed poprawką i sprawdzono po niej.
- Właściwy hub działa na http://127.0.0.1:8765/ jako administrator. Plik gry istnieje; 3 dotychczasowe konta są widoczne. Nie zmieniano danych istniejących kont i nie uruchamiano ich na potrzeby testów.
- Zmiany innych sesji w CLAUDE.md, build.bat i logach pozostawiono poza tym wdrożeniem.
