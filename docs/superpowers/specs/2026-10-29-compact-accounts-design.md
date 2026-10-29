# Kompaktowy panel kont — korekta użytkownika

Zastępuje układ kart z poprzedniego projektu. Użytkownik polecił uprościć dodawanie do loginu i hasła, przenieść CH i dodatkowe opcje do uruchamiania oraz zastosować techniczny, gęsty układ.

- Ekran kont jest tabelą: zaznaczenie, login, status, ostatni CH/postać, stan bota, akcje.
- Nad tabelą jest stale dostępny formularz login + hasło + Dodaj. Edycja korzysta z tego samego formularza. Nie ma nazwy profilu ani opcji sesji podczas dodawania.
- Import z pliku CSV/TSV lub wklejenie danych: login i hasło, po jednym koncie na wiersz; obsługiwane średnik, przecinek i tabulator oraz standardowe cytowanie CSV. Opcjonalny nagłówek login;haslo/password. Maksymalnie 500 kont i 256 KB. Walidacja całej listy przed zapisem, istniejące i powtórzone loginy są pomijane bez nadpisania haseł.
- Nowe konta są nieaktywne: sam zapis lub import nie loguje ani nie uruchamia gry.
- Uruchom / Przeloguj otwiera małe okno CH, pozycji postaci, bota po wejściu i restartu klienta. Dla jednego konta wstępnie pokazuje ostatnie opcje. Dla zaznaczonych kont stosuje wspólne opcje. Konta już w grze są przelogowywane.
- Dotychczasowe profile, szyfrowanie DPAPI i funkcje radaru pozostają zgodne. Hasła nie są zwracane, logowane ani zapisywane w pamięci trwałej przeglądarki.
- Styl: neutralne szarości, pojedynczy niebieski akcent, małe odstępy i standardowe kroje. Bez portretów na liście, ozdobnych kart, etapów wprowadzających i zewnętrznych fontów.

Warianty: same dwa pola na kartach zachowywałyby zbędne odstępy; sam CSV utrudniałby dodanie jednego konta. Wybrano tabelę z szybkim formularzem oraz opcjonalnym importem.
