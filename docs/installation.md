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
