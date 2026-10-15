# Architektura i protokół

- `app.py`: Flask, interfejs HTML/JS, biblioteka, radio, kolejka, playlisty,
  endpointy mediów i koordynacja odtwarzania.
- `sony_config.py`: lokalny JSON, nadpisania środowiskowe i walidacja ustawień.
- `sony_seek.py`: FFmpeg, parametry audio, fragmenty i ograniczenia cache.
- `sony_queue.py`: rozpoznawanie końca utworu i wybór kolejnej pozycji.
- `sony_party.py`: oddzielny klient SOAP Sony PARTY.

## Dwa protokoły sterowania

Standardowe AVTransport i RenderingControl używają przestrzeni
`urn:schemas-upnp-org:service:<service>:1` oraz `/<service>/ctrl`.
PARTY używa `urn:schemas-sony-com:service:Party:1` i `/Party_Control`.
Przy wyłączeniu PARTY zawsze odczytywany jest bieżący SessionID; panel nie
próbuje przerwać sesji, w której Sony jest tylko uczestnikiem.

## Pliki audio

Sony pobiera pliki po HTTP z adresu podanego przez aplikację. Serwer wspiera
zakresy bajtów i metadane DLNA potrzebne również do pauzy.

MP3 nie jest rekompresowane. AAC jest przepakowywane do ADTS, bo część
poprawnych kontenerów M4A była odrzucana przez wieżę. ALAC/FLAC są dekodowane
do WAV/PCM także przy rozpoczęciu odtwarzania. PCM ponad 48 kHz jest
resamplowane do 48 kHz, z zachowaniem bitów i kanałów. Parametry nieobsługiwane
przez Sony mogą dawać czytelny błąd zamiast cichej konwersji stratnej.

Sony odrzucało Seek REL_TIME. Przewijanie przygotowuje nowy fragment od
wskazanego czasu, a panel dolicza offset do licznika i pokazuje czas całego
utworu. Cache zawiera fragmenty oraz opisy JSON; oryginały nie są zmieniane.
Limit fragmentu: 768 MiB. Limit przygotowania: 120 sekund.

## Koniec utworu

Monitor serwera odczytuje transport i pozycję. Przejście PLAYING -> STOPPED
przy końcu znanego utworu uruchamia następną pozycję; ręczny Stop w panelu
anuluje przejście. Dla AAC uwzględniono do 3 sekund opóźnienia licznika Sony.
To rozpoznawanie na podstawie stanu, a nie dedykowane zdarzenie EOF; fizyczny
pilot i błędy urządzenia nie zawsze pozwalają jednoznacznie odróżnić koniec
od zatrzymania. Monitor nie uruchamia się przy zwykłym imporcie modułu.

## Odsłuch w przeglądarce

Włącza się dopiero po kliknięciu użytkownika. Ma własną głośność i nie wysyła
poleceń sterujących do Sony. MP3 pobierane jest bezpośrednio, inne formaty
jako WAV/PCM z osobnego cache/browser. Pełny utwór jest synchronizowany
z oryginalną pozycją; offset fragmentu Sony nie jest liczony dwa razy.

Synchronizacja jest przybliżona, z korektą różnicy ponad 2 sekundy. QuickJS
testuje logikę kontrolek i odrzucanie nieaktualnych odpowiedzi; zgodność
odtwarzacza wymaga też sprawdzenia w rzeczywistej przeglądarce. Odsłuch nie
jest implementacją odbiornika PARTY STREAMING. Radio nie jest przewijane
ani dołączane do odsłuchu przeglądarkowego.
