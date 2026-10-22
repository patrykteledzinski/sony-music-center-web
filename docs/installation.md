# Uruchamianie jako usługa

Zwykłe uruchomienie `python app.py` uruchamia również monitor kolejki.
Nie zastępuj go samym `flask run` lub importem przez WSGI: w tej wersji
monitor jest uruchamiany w punkcie wejścia skryptu.

Poniższy przykład zakłada katalog `~/sony-music-center-web` z utworzonym
`.venv` oraz skonfigurowanym `config.json`. Dostosuj ścieżki w pliku usługi,
jeżeli wybrałeś inne miejsce. Użytkownik usługi musi czytać katalog muzyki.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/sony-web.service ~/.config/systemd/user/sony-web.service
systemctl --user daemon-reload
systemctl --user enable --now sony-web.service
```

Sprawdzenie i restart:

```bash
systemctl --user status sony-web.service
journalctl --user -u sony-web.service -n 100 --no-pager
systemctl --user restart sony-web.service
```

Uruchamianie usług użytkownika bez aktywnego logowania wymaga włączenia
lingering przez administratora, np. `sudo loginctl enable-linger "$USER"`.

Istniejąca instalacja z usługą systemową może nadal korzystać ze swojego
pliku `/etc/systemd/system/sony-web.service`; nie uruchamiaj równocześnie
drugiej usługi na tym samym porcie.

## Diagnostyka

- Brak połączenia: sprawdź IP Sony, dostęp do portu 8080 i sieć lokalną.
- DATA ERROR: sprawdź rzeczywisty kodek, nie tylko rozszerzenie pliku.
- UPnP 701 podczas startu: Sony bywa jeszcze w stanie przejściowym; aplikacja
  oczekuje i ponawia tylko znany błąd przejściowy.
- Playlista kończy się po jednym utworze: sprawdź liczbę pozycji w kolejce
  i to, czy tryb NORMAL nie doszedł do jej końca. Log monitora zawiera indeks.
- FFmpeg: sprawdź systemowy plik wykonywalny, pakiet imageio-ffmpeg albo
  `ffmpeg_binary` w konfiguracji.
- Odsłuch w przeglądarce: zezwól na audio przyciskiem „Uruchom dźwięk”.

Zmiany ustawień wymagają restartu aplikacji. Zapisane radio i playlisty są
w `state_dir`; zachowaj ten katalog przy aktualizacji lub zmianie lokalizacji.

### Automatyczne odnajdywanie Sony

Po błędzie połączenia panel szuka urządzenia przez SSDP/UPnP (maksymalnie raz
na 30 sekund). Zapamiętuje identyfikator UDN w `data/sony_device.json`, aby
po zmianie IP połączyć się z tym samym zestawem. Gdy identyfikator nie jest
jeszcze znany, akceptuje tylko jednoznacznie wykryty CMT-MX700Ni/MX750Ni.
Nowy adres i port zapisuje w aktywnym pliku konfiguracji; pozostałe ustawienia
są zachowane. Przy nadpisaniu adresu zmiennymi `SONY_WEB_SONY_IP` lub
`SONY_WEB_SONY_PORT` zmiana obowiązuje tylko w bieżącym procesie.

Odczyt stanu po odnalezieniu urządzenia jest ponawiany automatycznie. Komendy
sterujące nie są ponawiane, ponieważ urządzenie mogło je wykonać przed utratą
odpowiedzi; w takim przypadku należy ponowić żądaną czynność w panelu.
Wykrywanie wymaga dostępnego multicastu UDP 239.255.255.250:1900 w sieci lokalnej.

### Odnajdywanie Volumio

Aby włączyć monitor, ustaw `volumio_ip` i opcjonalnie `volumio_port` (domyślnie
80) w `config.json`; dostępne są też `SONY_WEB_VOLUMIO_IP` i
`SONY_WEB_VOLUMIO_PORT`. Bez `volumio_ip` monitor jest wyłączony.
Co 30 sekund panel sprawdza API Volumio. Po utracie połączenia wyszukuje
adresy urządzeń UPnP i weryfikuje API oraz stałe `id` Volumio.
Nowy adres zapisuje w `data/volumio_device.json` razem z identyfikatorem;
korzysta z niego także po restarcie. Plik ten ma pierwszeństwo przed
początkowym adresem z konfiguracji. Aby wybrać inne Raspberry Pi, usuń
ten plik, ustaw nowy adres i uruchom usługę ponownie.

Stan i aktualny link do Volumio pojawiają się u góry panelu, a dane JSON
są dostępne pod `/api/volumio/status`. Monitor działa w osobnym wątku
i nie blokuje sterowania Sony. Wykrywanie wymaga dostępnego SSDP multicast
w sieci lokalnej i włączonego ogłaszania renderera UPnP przez Volumio.
