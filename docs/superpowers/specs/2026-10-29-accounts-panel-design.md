# Panel kont

Zaakceptowany przez użytkownika kierunek: makieta „Moje konta”, następnie polecenie „Wdróż ten układ w programie”.

## Cel i układ
Domyślny ekran „Moje konta” pokazuje zapisane konta także przy wyłączonej grze. Widoczny przycisk „+ Dodaj konto” otwiera formularz: nazwa w panelu (opcjonalna), login, hasło z podglądem, kanał CH1–CH4, postać 1–4, bot po wejściu, restart po zamknięciu i autologowanie. „Zapisz konto” nie uruchamia gry. „Zapisz i uruchom” zapisuje profil i uruchamia go. Edycja odbywa się w tym samym formularzu. Puste hasło przy edycji tego samego loginu zachowuje dotychczasowe hasło. Zmiana loginu wymaga podania hasła.

Karty kont pokazują nazwę, login, kanał, pozycję postaci, stan logowania i bota. Akcje: uruchom i zaloguj / ponów po błędzie, edytuj, panel postaci, pokaż okno gry i osobno włącz/wyłącz bota dla postaci w świecie. Błędy pozostają na kartach. Proces logowania pokazuje etapy uruchomienia gry, logowania, wyboru postaci i wejścia do świata. W trakcie uruchamiania blokujemy ponowne kliknięcia i edycję.

## Ograniczenia
- Python i natywny JS/CSS; bez nowych zależności.
- Zachowanie istniejących kont, szyfrowania DPAPI, radaru, tras, funkcji bota i lokalnych zabezpieczeń HTTP Host/Origin.
- Hasła nie trafiają do odpowiedzi API, logów ani pamięci przeglądarki poza bieżącym polem formularza.
- Działające klienty bez konta pozostają dostępne w „Postacie w grze”; można przypisać im konto w nowym formularzu.
- Problemy z plikiem gry i uprawnieniami wskazują naprawę w ustawieniach przed uruchamianiem.
- Zapis i walidacja nie pozostawiają częściowo zmienionych profili.

## Rozważone podejścia
1. Tylko formularz w starej zakładce — mało zmian, ale utrzymuje rozproszone uruchamianie.
2. Panel oparty na kontach — wybrany, łączy dane, uruchomienie i stan; wymaga rozszerzenia API o zapis bez PID.
3. Kreator pierwszego uruchomienia — pomaga początkowo, lecz nie upraszcza codziennej pracy.

## Decyzje wdrożeniowe
Rozszerzamy istniejący Manager, nie tworzymy drugiego magazynu. Nowe API POST /api/autologin (utworzenie), POST /api/autologin/<id>/save (edycja). Stara konfiguracja po PID korzysta ze wspólnego zapisu. Zapis nowego konta z restartem nie otwiera gry przed pierwszym uruchomieniem. Ponowienie resetuje błąd logowania na działającym kliencie bez uruchamiania drugiej kopii. Używamy obecnego checkoutu, ponieważ to z niego działa lokalny panel i jego zasoby użytkownika.
