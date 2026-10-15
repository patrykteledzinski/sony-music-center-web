# Sony Music Center Web

An unofficial Polish-language web remote for Sony CMT-MX700Ni/MX750Ni over
the local network. Features include playback, a music library, queue and
playlists, automatic next-track playback, Internet radio, PARTY On/Off,
server-assisted seeking and optional browser listening.

Tested with firmware 1.27. Other models require verification. This project is
not affiliated with Sony and does not modify device firmware.

## Quick start

Requires Linux/Ubuntu, Python 3.10+ and FFmpeg. The Sony and server must be on
the same LAN. The Sony must be able to access the server's advertised IP and
HTTP port.

```bash
git clone https://github.com/patrykteledzinski/sony-music-center-web.git
cd sony-music-center-web
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-media.txt
cp config.example.json config.json
```

Edit `config.json`: set `sony_ip`, `server_ip` and `music_root` to your actual
network and music folder. Then run `.venv/bin/python app.py` and open
`http://YOUR_SERVER_IP:8088`.

`config.json`, generated files and user playlists are excluded from Git.
`state_dir` defaults to the project folder. Relative paths are resolved from
that folder. Override settings with `SONY_WEB_<UPPERCASE_KEY>` or choose a
JSON file with `SONY_WEB_CONFIG`.

MP3 and AAC are copied without re-encoding (AAC uses ADTS). ALAC/FLAC are
served as PCM; rates above 48 kHz are resampled to 48 kHz with bit depth
preserved. Original music files are never modified. Browser listening is
approximately synchronized and is not a Sony PARTY receiver. Radio is not
seekable and is not included in browser listening.

**Trusted LAN only:** the panel has no authentication or TLS and serves your
music files. Do not expose it directly to the Internet.

## Tests

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

Automated tests use mocked Sony responses, generated temporary audio and
optional QuickJS. They do not send commands to a physical device.

MIT license. See the [Polish README](../README.md),
[architecture notes](architecture.md) and [security policy](../SECURITY.md).
