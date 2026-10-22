# YouTube2 — silnik przewijania v2

Lokalna integracja dla Volumio 2.917 / Node 8.11.1 na Raspberry Pi 3.
Działa samodzielnie na Pi. Oryginalny parser wyszukiwania YouTube2 pozostaje
w `../youtube2-legacy/`; nowy kontroler zastępuje poprzednie poprawki przewijania.

## Kontrakt czasu i sterowania

- `seek-engine.js` ma jeden stan sesji: wybrany film, niezmienną pełną długość,
  żądaną pozycję w milisekundach i pozycję potwierdzoną przez MPD.
- Każdy wybór filmu ma losową sesję, każda zmiana pozycji — rosnącą rewizję.
  Odpowiedź starszej operacji nie może nadpisać nowszej.
- Krótki debounce i jedna kolejka operacji odtwarzacza scalają szybkie skoki.
  MPD dostaje atomowo `stop`, `clear`, `addid`, `play`; nie jest obserwowana
  pusta lista pomiędzy tymi poleceniami.
- Potwierdzenie wymaga aktualnego URL, stanu `play`, rozpoznanej długości PCM
  i rosnącej pozycji. Sam ACK polecenia nie oznacza sukcesu.
- Przy pauzie nowe wskazanie jest zapamiętywane i publikowane bez włączania
  audio. Wznowienie otwiera strumień od tego miejsca.
- Dla YouTube2 wyłączony jest symulowany licznik rdzenia Volumio. W tej wersji
  jego zaplanowany callback potrafi zwiększyć czas nawet po zatrzymaniu.
  Jedna aktualizacja z MPD na sekundę ustawia pozycję dla obu interfejsów.
  Inne źródła muzyki korzystają z dotychczasowego licznika.
- Ostatnia pełna sekunda pozostaje możliwym celem przewijania, ponieważ
  stary MPD zaokrągla długość krótkiego fragmentu do całych sekund.

`play-controller.js` jest adapterem do Kew, MPD i stanu Volumio. Adapter MPD
instaluje się raz i normalizuje wyłącznie własne źródło. Przejściowe stany
przewijania nie są przekazywane jako koniec nagrania; prawdziwy koniec jest
przekazywany rdzeniowi, który zachowuje obsługę kolejki i powtarzania.
Zachowane pomocnicze metody upstream YouTube2 podlegają GPL-3.0 (`LICENSE`).

## Strumień na Pi

`sony_youtube_stream.py` działa w osobnym środowisku Alpine razem z resolverem.
Adresy API są dostępne wyłącznie przez loopback:

- `GET /v2/info/<videoId>` — pełna długość i informacja o przewijaniu;
- `GET|HEAD /v2/audio/<session>/<revision>/<videoId>/<offsetMs>.wav`;
- `POST /v2/cancel/<session>` — anulowanie dotychczasowych rewizji;
- `GET /v2/health` — wersja i liczba aktywnych strumieni.

Każdy strumień dekoduje źródło od wskazanego czasu do PCM stereo 44,1 kHz,
16 bit. Obsługiwane są pojedyncze zakresy bajtów i prawidłowe nagłówki WAV.
FFmpeg musi dostarczyć próbki przed odpowiedzią audio 200/206. Nowa rewizja
zamyka tylko starsze rewizje tej samej sesji; równoległe żądania zakresów
tej samej rewizji mogą współistnieć. HEAD nie uruchamia dekodera ani nie
anuluje audio. Rejestr jest ograniczony, liczba dekoderów wynosi maksymalnie 3,
a zapisy i odczyty mają limity czasu. Procesy są sprzątane po rozłączeniu,
anulowaniu i błędzie. Podpisane URL nigdy nie trafiają do przeglądarki.

Anulowanie otwierania filmu przerywa także proces ekstrakcji yt-dlp wraz
z jego procesami potomnymi, zamiast pozostawiać zajęty resolver.
Stare adresy `/audio/...` pozostają tylko dla zgodności; nowy silnik ich nie używa.

## Studio

`volumio-ui/studio-seek.js` oddziela szkic przeciągania od potwierdzonej
pozycji. Podczas oczekiwania pokazuje „Przewijanie…”. Nie wpisuje żądanego
czasu do zegara odtwarzania. Wspólna obsługa obejmuje dotyk, mysz, klawiaturę
oraz ±10 s. Przyciski korzystają z ostatniego żądania, więc szybkie kliknięcia
sumują się. Kanał Socket.IO `pushYoutubeSeek` publikuje numer rewizji, fazę,
stałą długość i faktyczną pozycję.

## Weryfikacja

```sh
.tools/youtube-node integrations/youtube2-v2/test-seek-engine.js
.tools/youtube-node integrations/youtube2-v2/test-volumio-adapter.js
.tools/youtube-node integrations/youtube2-v2/test-studio-seek.js
/home/set/.local/share/sony-web-venv/bin/python -m unittest discover -s tests
```

Scenariusze Node działają także na rzeczywistym Node 8 w Pi. Test integracyjny
`live-mpd-test.js` odmawia użycia produkcyjnego portu 6600; wymaga osobnego,
bezgłośnego MPD, np. na 16600. Sprawdza początek/końcówkę, szybkie skoki,
stałą długość, pauzę i wznowienie. Testy HTTP obejmują anulowanie rewizji,
sprzątanie, walidację, zakresy oraz błędy dekodera. Test próbek na Pi porównał
bajty PCM z offsetów 0, 2129000 i 2000 ms z niezależnym FFmpeg — są identyczne.

Prywatne wyniki i zapis poprzedniej kolejki:
`.local-backups/youtube-seek-v2/`; nie publikować plików ze stanem użytkownika.

## Wdrożenie i wycofanie

```sh
python scripts/deploy_youtube_seek_v2.py --host 192.168.8.110 --known-hosts /tmp/volumio-known-hosts
```

Hasło jest pytane interaktywnie. Wdrożenie zapisuje manifest i kopie wszystkich
zmienianych plików przed podmianą, a pliki podmienia przez rename. Restart
usług przerywa odtwarzanie. Skrypt nie zastępuje kolejki ani nie zmienia głośności.
Odtwarzanie zapisuje/przywraca osobny skrypt weryfikacyjny podczas wdrożenia.

Do wycofania użyj ścieżki backupu wypisanej przez instalator:

```sh
python scripts/deploy_youtube_seek_v2.py --host 192.168.8.110 --known-hosts /tmp/volumio-known-hosts --restore /data/sony-web-backups/youtube-seek-v2-YYYYMMDDTHHMMSSZ
```

Kopie są sprawdzane sumami SHA-256 przed odtworzeniem. Pierwszy backup
sprzed v2 zapisano lokalnie w `first-remote-backup.txt` w prywatnym katalogu.

Przewijanie dotyczy nagrań o znanej długości, mieszczących się w standardowym
WAV RIFF (około 6 h 45 min). Transmisje na żywo i materiały wymagające
logowania nie są przedstawiane jako przewijalne. Dostępność źródła nadal
zależy od YouTube i połączenia z Internetem.
