# YouTube2 samodzielnie na Raspberry Pi

**Aktualna implementacja przewijania:** [silnik v2](../youtube2-v2/README.md).
Poniżej zachowano dokumentację wcześniejszych poprawek i zgodności.

Wdrożone na Raspberry Pi 3, Volumio 2.917 (32-bit ARMv7). YouTube2 korzysta
z `http://127.0.0.1:18888`: do wyszukiwania i odtwarzania nie potrzebuje już
serwera Sony Music Center. Nadal wymaga połączenia z Internetem.

## Środowisko

Osobne środowisko Alpine 3.22.6 ARMv7 znajduje się w
`/data/youtube-standalone/runtime`. Nie zastępuje systemu ani Node 8 Volumio.
Zainstalowano w nim Python 3.12.15, Node 22.23.2, FFmpeg 6.1.2 i certyfikaty
CA z podpisanych repozytoriów Alpine; yt-dlp 2026.08.19 skopiowano do
`/app/.tools/youtube-resolver`. `/app/.tools/youtube-node` wskazuje na
`/usr/bin/node` wewnątrz tego środowiska. Resolver używa upstream EJS z GitHub.

Usługa `youtube-standalone.service` uruchamia `/app/service.py` jako użytkownik
`volumio` z `RootDirectory=/data/youtube-standalone/runtime`. Startuje wraz
z systemem. Pliki `service.py` oraz `sony_youtube.py` znajdują się w `/app`.
Dla DNS istnieje osobny `/etc/resolv.conf`; `/dev/null` i `/dev/urandom` są
węzłami urządzeń. Nie potrzeba Dockera ani wymiany karty SD.

Serwis słucha wyłącznie na loopback. Przyjmuje tylko identyfikatory filmów,
nie dowolne adresy. Podpisane URL pozostają w pamięci lokalnego resolvera.
Ogranicza ekstrakcję do jednego zadania. Nagrania o znanej długości są
dekodowane przez FFmpeg do WAV PCM 44,1 kHz / 16 bit / stereo. Endpoint WAV
obsługuje `HEAD`, pojedyncze zakresy `Range`, `Content-Length` i odpowiedzi
206/416. Żądany offset bajtów jest przeliczany na czas wejściowy FFmpeg,
więc przewijanie nie wymaga pobierania ani konwersji całego filmu. Maksymalnie
trzy dekodery ograniczają obciążenie. Nowe połączenie audio dla tego samego
filmu zamyka poprzednie; HEAD nie zajmuje slotu dekodera.
Stara ścieżka MP3 pozostaje dla zgodności i nagrań bez znanej długości.
Nie zapisuje pobieranych utworów na karcie. FFmpeg jest zamykany po
rozłączeniu klienta. Bufor metadanych przechowuje do 32 wyników przez 5 minut.

Mostek `ytdl-bridge.js` zastępuje plik:
`/data/plugins/music_service/youtube2/node_modules/ytdl-core/lib/index.js`.
Oryginalny moduł pozostaje obok jako `index.legacy.js`.
Poprawiony parser wyszukiwania i kontroler odtwarzania z
`../youtube2-legacy/` nadal są wymagane. Ponowna instalacja YouTube2 może
nadpisać lokalne poprawki.

## Sprawdzenie i utrzymanie

```sh
systemctl is-active youtube-standalone
curl http://127.0.0.1:18888/health
sudo systemctl restart youtube-standalone
```

Rzeczywisty test YouTube2: „Chopin: Complete Nocturnes (Luke Faulkner)”,
stan `play`, pozycje 987, 3246 i 5248 ms. Audio i metadane dostarczane przez
usługę loopback na Pi. Po teście przywrócono wcześniejszą kolejkę i głośność.
Włączono także `persistent_queue` w MPD, aby kolejka przetrwała restart
Volumio. Kopia wcześniejszej konfiguracji MPD znajduje się w prywatnym
katalogu backupu.
Wyniki testu i zapis stanu: `.local-backups/youtube2-standalone/`.
Testy `tests/test_youtube_standalone.py` sprawdzają walidację adresów,
nieujawnianie podpisanych URL, HEAD bez ekstrakcji i sprzątanie po błędzie.

Resolver obsługuje także warianty formatów YouTube z sufiksami, np.
`139-drc`, `140-drc` i `140-0`; starsza konwersja ich identyfikatorów na liczbę
powodowała błąd 502 dla części filmów.

Długie skoki realizuje kontroler YouTube2 przez otwarcie
`/audio/<videoId>/<sekunda>.wav`. FFmpeg dekoduje od wskazanego czasu,
a MPD odtwarza pozostałą część. Kontroler dodaje offset do rzeczywistej
pozycji MPD, zachowuje pełną długość filmu i synchronizuje wskaźnik co
2 sekundy. Normalizacja dotyczy także odczytów `mpdPlugin.getState`
przekazywanych bezpośrednio do rdzenia Volumio: pozostała długość fragmentu
nie może nadpisywać pełnej długości filmu między odświeżeniami. Dla źródła
`/2603.wav` o pozostałej długości 443 s interfejs nadal pokazuje 3046 s,
a pozycja wynosi 2603 s plus rzeczywisty postęp MPD. Inne źródła MPD nie są
normalizowane. Kolejne żądania przewijania są porządkowane; ostatnie wskazanie
zastępuje oczekujące. Zapobiega to powrotom do początku w starym MPD,
który przy dużych skokach po HTTP przyjmował żądanie, ale nie utrzymywał
nowej pozycji.

Przewijanie działa w klasycznym Volumio i Studio dla nagrań o znanej
długości mieszczących się w WAV RIFF (około 6 godzin 45 minut). Studio ma
suwak oraz przyciski −10/+10 sekund. Po skoku może wystąpić kilkusekundowa
przerwa na ponowne otwarcie źródła. Strumienie MP3 bez znanej długości
nadal są traktowane jak radio i nie udostępniają przewijania.
Konto Google, automatyczne miksy i wszystkie playlisty nie zostały
zweryfikowane. Zmiany po stronie YouTube mogą wymagać aktualizacji yt-dlp/EJS.
Karta Sony w Studio nadal korzysta z serwera Sony, niezależnie od YouTube.

Test przewijania: „Wizja Na Żywo” (`GqpWctCCwUM`), długość 3046 sekund,
`stream=false`. API Volumio potwierdziło odtwarzanie po skoku do 60 sekund
(pozycja 62038 ms) i z powrotem do 20 sekund (22002 ms). Wynik:
`.local-backups/youtube2-standalone/seek-result.json`. Kolejkę i głośność
przywrócono po teście.

## Cofnięcie do poprzedniego resolvera serwerowego

Kopia mostka serwerowego znajduje się na Pi pod
`/data/youtube-standalone/ytdl-bridge-server-backup.js`.

```sh
cp /data/youtube-standalone/ytdl-bridge-server-backup.js /data/plugins/music_service/youtube2/node_modules/ytdl-core/lib/index.js
sudo systemctl restart volumio
sudo systemctl disable --now youtube-standalone
```

Restart Volumio przerywa odtwarzanie. Pełny backup starej wtyczki opisano
w `../youtube2-legacy/README.md`.

## Stabilność szybkiego przełączania

Kontroler YouTube2 ignoruje spóźnione metadane wcześniejszego wyboru,
także gdy rozpoczęto już przygotowanie nowego filmu. Ochrona parsera
pustej odpowiedzi `playlistinfo` zapewnia tekstowy `trackType`, zapobiegając
`split of undefined` między odczytem statusu a wyczyszczeniem listy MPD.
`updateQueue error: null` w starym module MPD jest odrębnym, mylącym wpisem
po otrzymaniu pustej listy; nie oznacza niepowodzenia żądania MPD.

Resolver oczekuje do 25 s na zajęty slot zamiast natychmiast odrzucać
następny film. Po oczekiwaniu ponownie sprawdza pamięć podręczną, żeby nie
pobierać tych samych metadanych dwukrotnie. Mostek na Pi ma limit 90 s,
obejmujący oczekiwanie i ekstrakcję (limit ekstrakcji 55 s).
Testy `test-selection-race.js` i `test-play-controller.js` sprawdzają wybór
nowszego filmu, pusty rekord MPD i normalizację czasu; testy Pythona
obejmują wykorzystanie metadanych ukończonych przez poprzednie żądanie.
