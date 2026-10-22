# YouTube2 0.1.6 — lokalna poprawka zgodności

**Aktualna implementacja przewijania:** [silnik v2](../youtube2-v2/README.md).
Poniżej zachowano dokumentację wcześniejszych poprawek i zgodności.

**Aktualne wdrożenie:** resolver działa już bezpośrednio na Pi. Zobacz
[tryb samodzielny](../youtube2-standalone/README.md). Poniższy opis zachowuje
szczegóły wcześniejszego rozwiązania serwerowego i wspólnych poprawek wtyczki.

Volumio 2.917 / Node 8.11.1, sprawdzone na Raspberry Pi 3.
Zainstalowana wersja 0.1.6 jest najnowszą na gałęzi `volumio-2.x`:
https://github.com/patrickkfkan/volumio-youtube2/tree/volumio-2.x

Aktualna główna wersja 2.5.2 wymaga Bookworm i Node >=20; gałąź
buster-backports 1.4.2 wymaga Volumio >=3 i Node >=14. Nie zastępowano
systemowego Node ani całego Volumio.

## Zmiana

Poprawiono `node_modules/youtube-scrape/scraper.js`:

- nie pobiera usuniętego `searchSubMenuRenderer.groups`, którego brak
  powodował `TypeError: filterWrapper is not iterable` i nierozwiązany Promise;
- używa standardowych filtrów wyszukiwania video/channel/playlist;
- brak API key nie przerywa pierwszej strony wyników;
- zmieniony / niepoprawny HTML kończy żądanie błędem zamiast zawieszenia;
- żądania HTTP mają limit 15 sekund.

`compatibility.patch` pokazuje zmiany względem zainstalowanego modułu.
Pełny poprawiony moduł jest w `scraper.js`; licencja upstream w `LICENSE`.
Nie zmieniono numeru oficjalnej wtyczki — to lokalna poprawka, nie wydanie
jej autora. Ponowna instalacja modułu przez npm może ją nadpisać.

## Weryfikacja

- Na Node 8.11.1: test bez sieci przeszedł.
- Rzeczywiste wyszukiwanie `Chopin`: 20 wyników.
- API `/api/v1/browse?uri=youtube2`: 13 pozycji po restarcie.
- Studio: YouTube2 otwiera się i pokazuje 13 pozycji bez błędów JavaScript.
- Wcześniejsze odtwarzanie przywrócono po restarcie Volumio.

Pobieranie audio zaktualizowano przez `ytdl-bridge.js`: zachowuje pomocnicze
funkcje oryginalnego `ytdl-core`, a metadane pobiera z serwera Sony Music Center
pod `http://192.168.8.50:8088/api/youtube/info/<videoId>`. Oryginalny plik
pozostaje na Pi jako `node_modules/ytdl-core/lib/index.legacy.js`.

Serwer używa `yt-dlp 2026.08.19` i osobnego Node `v24.21.0` w `.tools/`.
Nie zmieniano Node 8 na Pi. Narzędzia mogą być odtworzone przez instalację
`requirements-resolver.txt` do `.tools/youtube-resolver` oraz umieszczenie
zgodnego Node >=20 pod `.tools/youtube-node` (plik wykonywalny).
Resolver korzysta z komponentu EJS upstream i pamięci podręcznej
`.tools/youtube-cache`. Nowy endpoint przyjmuje tylko 11-znakowe identyfikatory
filmów, ogranicza ekstrakcję do dwóch równoległych zadań i buforuje wyniki
na 5 minut. Podpisane adresy źródłowe pozostają po stronie serwera.

Audio pobiera Python; FFmpeg dekoduje bajty przez stdin i udostępnia MP3
192 kb/s przez `/youtube-audio/<videoId>.mp3`. Ominięto w ten sposób awarię
HTTPS używanego builda FFmpeg i niezgodność starego MPD z bezpośrednim
strumieniem Google. Strumieniowanie jest ograniczone do dwóch połączeń,
procesy są zamykane po rozłączeniu klienta. Nie zapisuje się plików muzycznych.

`play-controller.js` dodaje gotowy strumień bezpośrednio do MPD, bez próby
parsowania pliku audio jako playlisty, i zachowuje metadane utworu.
Pochodzi z kontrolera YouTube2 0.1.6 (GPL-3.0); pełna licencja w
`PLAY-CONTROLLER-LICENSE`. Skaner wyszukiwania ma osobną licencję MIT.

Końcowy rzeczywisty test: YouTube2 odtworzyło „Chopin: Complete Nocturnes
(Luke Faulkner)”, poprawnie raportowało tytuł i stan `play`, a pozycja wzrosła
z około 1,5 do 5,5 sekundy. Test zakończono i przywrócono zapisany stan
sprzed próby oraz głośność. Wynik znajduje się w
`.local-backups/youtube2-update/playback-test-result.json`.

**Ograniczenia:** wymagany działający serwer Sony Music Center i Internet.
Strumień jest traktowany jak radio: przewijanie i pauza mogą nie działać jak
w pliku lokalnym. Nie potwierdzono działania konta Google, automatycznych
miksów ani wszystkich playlist starej wtyczki. To lokalna integracja, nie
oficjalna aktualizacja YouTube2 do 2.5.2.

Uruchomienie testu bez sieci na Pi:

```sh
node test-scraper.js /data/plugins/music_service/youtube2/node_modules/youtube-scrape/scraper.js
```

## Kopia zapasowa

Przed zmianą zapisano prywatne archiwa całej wtyczki i konfiguracji na Pi:
`/data/sony-web-backups/youtube2-20261021T142014Z/plugin.tar.gz`
i `config.tar.gz`. Nie publikować konfiguracji — może zawierać dane logowania.
Lokalny zapis poprzedniego stanu odtwarzania i ścieżki backupu:
`.local-backups/youtube2-update/`.

Aby cofnąć integrację, odtwórz z `plugin.tar.gz` pliki
`youtube2/node_modules/youtube-scrape/scraper.js`,
`youtube2/node_modules/ytdl-core/lib/index.js` i
`youtube2/lib/controller/play/index.js` w `/data/plugins/music_service/`,
następnie przeładuj usługę `volumio`. Odtwarzanie zostanie na chwilę przerwane.
