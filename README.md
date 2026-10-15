# Sony Music Center Web

Panel WWW do sterowania wieżą **Sony CMT-MX700Ni / MX750Ni** przez lokalną
sieć UPnP. Odtwarza muzykę z Ubuntu, zarządza kolejką i playlistami, obsługuje
radio internetowe, PARTY STREAMING oraz odsłuch w przeglądarce.

Nieoficjalny projekt społecznościowy, niezwiązany z Sony. Interfejs jest po
polsku. [English quick start](docs/README.en.md).

## Funkcje

- Odtwarzanie, pauza, Stop, poprzedni/następny, regulacja głośności.
- Biblioteka folderów, wyszukiwanie, kolejka i zapisywane playlisty.
- Automatyczne przechodzenie do kolejnego utworu bez otwartej przeglądarki.
- Klikanie paska postępu: przewijanie realizowane przez serwer z FFmpeg.
- MP3 bez rekompresji; AAC przepakowane do ADTS bez rekompresji.
- ALAC/FLAC podawane jako PCM. Materiał ponad 48 kHz jest konwertowany do
  48 kHz z zachowaniem głębi bitowej; oryginalne pliki pozostają bez zmian.
- Stacje radia internetowego i przyciski Party On / Party Off.
- Opcjonalny odsłuch w przeglądarce z niezależną głośnością i przybliżoną
  synchronizacją z Sony. Nie jest to odbiornik protokołu PARTY STREAMING.

## Wymagania

- Ubuntu/Linux, Python **3.10+** (lokalnie sprawdzany na Pythonie 3.12).
- Sony i serwer w tej samej sieci. Wieża musi mieć dostęp do adresu i portu
  serwera; `server_ip` musi wskazywać rzeczywisty adres LAN, a nie localhost.
- FFmpeg do obsługi kodeków, przewijania i odsłuchu w przeglądarce.
- Odczyt katalogu muzyki i zapis katalogu stanu aplikacji.

Testowano na CMT-MX700Ni/MX750Ni, firmware 1.27. Zgodność innych modeli
wymaga sprawdzenia. Aplikacja nie aktualizuje firmware.

## Instalacja

```bash
git clone https://github.com/patrykteledzinski/sony-music-center-web.git
cd sony-music-center-web
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-media.txt
cp config.example.json config.json
```

Pakiet `imageio-ffmpeg` dostarcza lokalny plik FFmpeg na obsługiwanych
platformach. Możesz zamiast tego zainstalować systemowy `ffmpeg` i użyć
`requirements.txt`. Python venv może wymagać pakietu `python3-venv` w Ubuntu.

Edytuj **`config.json`**: wpisz adres Sony, adres serwera oraz katalog muzyki.
Plik ten jest ignorowany przez Git. Następnie:

```bash
.venv/bin/python app.py
```

Otwórz `http://ADRES_SERWERA:8088`. Port musi być dostępny również dla Sony.
[Uruchamianie przez systemd](docs/installation.md).

## Konfiguracja

| Klucz | Znaczenie |
| --- | --- |
| `sony_ip`, `sony_port` | IPv4 wieży i port UPnP, zwykle 8080 |
| `server_ip`, `web_port` | IPv4 i port serwera widocznego dla Sony |
| `music_root` | Katalog z muzyką, obsługuje `~` |
| `state_dir` | Katalog zapisu `data/`, `playlists/` i `cache/`; domyślnie katalog projektu |
| `bind_host` | Adres nasłuchiwania panelu, domyślnie `0.0.0.0` |
| `ffmpeg_binary` | Opcjonalna ścieżka do konkretnego pliku FFmpeg |

Każdy klucz można nadpisać zmienną `SONY_WEB_<NAZWA_WIELKIMI_LITERAMI>`, np.
`SONY_WEB_MUSIC_ROOT`. `SONY_WEB_CONFIG` wskazuje alternatywny plik JSON.
Ścieżki względne liczone są od katalogu projektu. Brak `config.json` oznacza
użycie ustawień przykładowej sieci — dopasuj je przed łączeniem z Sony.

## Używanie i ograniczenia

Kliknięcie utworu zachowuje istniejącą playlistę, jeśli zawiera ona ten
utwór; w innym przypadku buduje kolejkę folderu od wybranego utworu. Ręczny
Stop w panelu blokuje automatyczne przejście. W trybie NORMAL koniec ostatniej
pozycji zatrzymuje kolejkę.

Nie każdy kodek akceptowany przez FFmpeg jest obsługiwany przez Sony.
Przewijanie i konwersja wymagają przygotowania fragmentu — mogą potrwać.
Limit jednego fragmentu to 768 MiB, a czasu przygotowania 120 sekund.
Radio nie jest przewijane i nie ma odsłuchu w przeglądarce.
[Opis architektury i formatów](docs/architecture.md).

**Panel nie ma logowania ani TLS. Używaj go w zaufanej sieci domowej.**
Użytkownik z dostępem do panelu może sterować wieżą i pobierać pliki muzyczne.
Nie przekierowuj portu bezpośrednio do Internetu. [Bezpieczeństwo](SECURITY.md).

## Testy

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

Testy używają tymczasowych plików i symulowanych odpowiedzi Sony. Nie wysyłają
poleceń do urządzenia. QuickJS sprawdza logikę interfejsu; nie zastępuje testu
odtwarzania w rzeczywistej przeglądarce. GitHub Actions uruchamia testy na
Pythonie 3.11 i 3.12. [Współpraca](CONTRIBUTING.md).

## Licencja

[MIT](LICENSE). Licencje zewnętrznych bibliotek i FFmpeg pozostają odrębne;
ich binaria nie są publikowane w tym repozytorium.
