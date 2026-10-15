# Contributing

Bug reports and pull requests are welcome. Include the Sony model, firmware,
Python version, actual audio codec and a redacted error message. State whether
the problem involves direct playback, converted PCM, seeking, PARTY or browser
listening. Do not upload copyrighted music to this repository.

Install `requirements-dev.txt` and run:

```bash
python -m unittest discover -s tests -v
python scripts/check_public_files.py
```

Tests must not contact or control a real Sony. Mock its SOAP responses and
use temporary generated audio. Check that Stop, pause and failed playback do
not accidentally advance the queue. Keep unrelated playback features working.
Hardware experiments should be explicitly described and must not update
firmware. Document any new format limitations and configuration options.

Do not commit config.json, data/, playlists/, cache/, .tools/, local archives,
logs or credentials. Changes to your own network belong in config.json.
