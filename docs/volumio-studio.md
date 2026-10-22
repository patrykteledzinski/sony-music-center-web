# Studio — Volumio × Sony

Lokalny interfejs dla Volumio 2, dostępny pod `/studio/` na Raspberry Pi.
Grafitowy motyw, sterowanie odtwarzaniem, głośnością, przewijaniem, kolejką,
losowaniem i powtarzaniem. Biblioteka korzysta z istniejącego API Volumio,
w tym serwerów DLNA. Podgląd Sony jest tylko do odczytu.

## Przełączanie

- Studio: `http://192.168.8.110/studio/` lub `/?studio=1`.
- Oryginalny interfejs: `http://192.168.8.110/?classic=1`.
- Wybór jest zapamiętywany osobno w każdej przeglądarce.
- W oryginalnym interfejsie przycisk „Studio ↗” wraca do nowego wyglądu.

Studio domyślnie odczytuje Sony Music Center pod `http://192.168.8.50:8088`.
Przycisk „Połączenie z Sony” pozwala zmienić ten adres w przeglądarce.
Endpoint `/api/studio/status` na serwerze udostępnia wyłącznie odczyt stanu
z CORS; nie otwiera dostępu między domenami do komend sterujących Sony.
Socket.IO na Pi obsługuje sterowanie Volumio. Bez połączenia komendy nie są
kolejkowane. Wejście na stronę nie zmienia odtwarzania ani głośności.

## Instalacja i przywrócenie

Pliki źródłowe: `volumio-ui/`. Nie wymagają kompilacji ani dodatkowych
procesów na Pi. Klient Socket.IO pochodzi z tej samej instalacji Volumio
(licencja w `SOCKET-IO-LICENSE.txt`). Nie ma zewnętrznych fontów ani CDN.

Po zweryfikowaniu klucza hosta przez SSH i zainstalowaniu lokalnie `paramiko`:

```sh
python3 scripts/deploy_volumio_studio.py --host 192.168.8.110
```

Skrypt pyta o hasło, wgrywa pliki do `www/studio` i `www3/studio`, zachowuje
oryginały w `studio-original.html` oraz kopie indeksów w
`/data/studio-ui-backups/`. Dodaje przekierowanie do strony startowej.
Aktualizacja systemu Volumio może nadpisać te pliki — wtedy ponów instalację.

Przywrócenie oryginalnej strony startowej:

```sh
python3 scripts/deploy_volumio_studio.py --host 192.168.8.110 --restore
```

Nie wymaga restartu Volumio. Skrypt nie zmienia konfiguracji audio,
usług systemowych ani biblioteki. Sterowanie dotyczy Volumio; karta Sony
pokazuje rzeczywisty, niezależny stan zestawu.

## Graj razem (tryb testowy)

Checkbox w Sony Music Center i Studio steruje jednym stanem na serwerze.
Sony prowadzi: utwory i kolejkę wybieraj w Sony, a Volumio podąża za źródłem,
pauzą, wznowieniem i zatrzymaniem. Głośność każdego urządzenia pozostaje
niezależna. W Studio przyciski odtwarzania i następny/poprzedni sterują wtedy
Sony; przewijanie i wybór utworów wykonuj w panelu Sony.

Obsługiwane są pliki odtwarzane przez Sony z biblioteki serwera, w tym
fragmenty przygotowane przy przewijaniu. Radio, AUDIO IN i inne źródła nie
są synchronizowane. Volumio otrzymuje bieżący plik; jego dotychczasowa kolejka
zostaje zastąpiona. Odznaczenie checkboxa kończy podążanie, bez zatrzymywania
odtwarzaczy ani automatycznego przywracania kolejki. Po restarcie serwera tryb
jest wyłączony.

To synchronizacja przybliżona. Kontroler sprawdza stan co około 2 sekundy;
przy różnicy pozycji ponad 1,5 sekundy przewija Volumio, z odstępem co najmniej
12 sekund między korektami. Nie ma wspólnego zegara ani pomiaru wyjść audio,
więc podawana różnica pozycji nie gwarantuje zgodności dźwięku. Może wystąpić
echo lub przerwa przy korekcie. Awaria Sony wstrzymuje Volumio.

Stan: `GET /api/group`. Zmiana: `POST /api/group` z JSON `{"enabled":true}`.
Polecenia między panelami są ograniczone do originu serwera oraz wykrytego
Volumio; obce originy są odrzucane.

Weryfikacja na sprzęcie 21.10.2026: oba urządzenia odtwarzały ten sam plik
HTTP; pauza i wznowienie podążały za Sony. Oba panele pokazywały ten sam stan
checkboxa. W ostatnim krótkim pomiarze raportowane odchylenia po starcie
wynosiły od -0,44 do +0,24 s, a we wcześniejszej próbie dochodziły do 1,3 s.
Nie wykonano pomiaru akustycznego ani długotrwałego testu dryfu.

Podczas wcześniejszych prób Raspberry Pi utraciło łączność; po restarcie
wróciło do sieci i test został zakończony. Nie ustalono przyczyny utraty
łączności. Dla Volumio 2 dodano małą playlistę M3U zamiast podawania dużego
pliku jako wejścia parsera playlist, a wznowienie HTTP realizowane jest
przez ponowne otwarcie playlisty i korektę pozycji.

Po testach przywrócono 61 pozycji pierwotnej kolejki Volumio, wznowiono jego
poprzedni utwór, wstrzymano Sony zgodnie z wcześniejszym stanem i wyłączono
tryb grupowy. Kopia wcześniejszego stanu znajduje się lokalnie w
`data/group-live-test-backup.json`, a próbki czasu w
`data/group-live-test-results.json`.
