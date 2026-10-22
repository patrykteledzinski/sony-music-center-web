"""Best-effort multiroom: Sony leads, Volumio follows. No shared audio clock."""
import json
import threading
import time
import urllib.parse
import urllib.request


class VolumioAPI:
    def __init__(self, monitor):
        self.monitor = monitor

    def call(self, path, data=None):
        base = self.monitor.snapshot().get('url')
        if not base:
            raise ValueError('Volumio nie jest skonfigurowane')
        req = urllib.request.Request(base + '/api/v1/' + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read(2 * 1024 * 1024))
        if isinstance(result, dict) and result.get('error'):
            raise RuntimeError(str(result['error']))
        return result

    def state(self):
        return self.call('getState')

    def command(self, name, **values):
        return self.call('commands/?' + urllib.parse.urlencode(dict(cmd=name, **values)))


class GroupPlayback:
    def __init__(self, sony_status, context, volumio, logger, playlist_url=None):
        self.sony_status = sony_status
        self.context = context
        self.volumio = volumio
        self.logger = logger
        self.playlist_url = playlist_url or (lambda uri: uri)
        self.operation = threading.RLock()
        self.lock = threading.Lock()
        self.enabled = False
        self.last_adjusted = float('-inf')
        self.last_load = float('-inf')
        self.loading_uri = None
        self.status = dict(enabled=False, leader='sony', mode='approximate',
            message='Wyłączone', drift_seconds=None, error=None)

    def snapshot(self):
        with self.lock:
            return dict(self.status)

    def update(self, **values):
        with self.lock:
            self.status.update(values)

    def set_enabled(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('enabled musi być wartością logiczną')
        with self.operation:
            if enabled:
                # Validate both devices before changing a shared mode.
                sony = self.sony_status()
                if not sony.get('online'):
                    raise ValueError('Sony jest offline')
                self.volumio.state()
            self.enabled = enabled
            self.last_adjusted = float('-inf')
            self.last_load = float('-inf')
            self.loading_uri = None
            self.update(enabled=enabled, error=None, drift_seconds=None,
                        message='Sony prowadzi · synchronizacja przybliżona' if enabled else 'Wyłączone')
        return self.snapshot()

    def tick(self):
        with self.operation:
            if not self.enabled:
                return
            try:
                sony = self.sony_status()
                if not sony.get('online'):
                    # Do not leave the follower playing alone after leader loss.
                    self.volumio.command('pause')
                    raise RuntimeError('Brak połączenia z Sony; Volumio wstrzymane')
                uri = sony.get('uri', '')
                context = self.context(uri)
                if not context or not sony.get('duration_seconds'):
                    self.volumio.command('pause')
                    self.update(message='Wybierz plik z biblioteki Sony; radio i AUDIO IN nie są synchronizowane',
                                error=None, drift_seconds=None)
                    return
                volume_state = self.volumio.state()
                desired = sony.get('state')
                if desired not in ('PLAYING', 'PAUSED_PLAYBACK', 'STOPPED'):
                    return
                follower_uri = self.playlist_url(uri)
                current_uri = urllib.parse.unquote(volume_state.get('uri', ''))
                if current_uri not in (urllib.parse.unquote(uri), urllib.parse.unquote(follower_uri)):
                    if desired != 'PLAYING':
                        if volume_state.get('status') == 'play':
                            self.volumio.command('pause')
                        self.update(message='Gotowe · czeka na uruchomienie utworu w Sony',
                                    drift_seconds=None, error=None)
                        return
                    if self.loading_uri == uri and time.monotonic() - self.last_load < 15:
                        self.update(message='Czekam na załadowanie utworu przez Volumio', error=None, drift_seconds=None)
                        return
                    self.loading_uri, self.last_load = uri, time.monotonic()
                    self.volumio.call('replaceAndPlay', dict(uri=follower_uri, service='webradio', type='song',
                        duration=max(0, int(sony['duration_seconds'] - context.get('offset', 0))),
                        title=sony.get('title') or 'Sony Music Center', artist=sony.get('artist', '')))
                    self.last_adjusted = float('-inf')
                    self.update(message='Łączenie obu odtwarzaczy…', error=None, drift_seconds=None)
                    return
                target = max(0, sony.get('position', 0) - context.get('offset', 0))
                if desired == 'PLAYING':
                    target += sony.get('position_age', 0)
                actual = max(0, float(volume_state.get('seek', 0)) / 1000)
                drift = actual - target
                if desired == 'PLAYING' and volume_state.get('status') != 'play':
                    # Volumio 2's HTTP resume drops consumeUpdateService, leaving
                    # reported state stuck at pause. Reload the tiny playlist,
                    # then let the next sample align its position.
                    if self.loading_uri == uri and time.monotonic() - self.last_load < 15:
                        return
                    self.loading_uri, self.last_load = uri, time.monotonic()
                    self.volumio.call('replaceAndPlay', dict(uri=follower_uri, service='webradio',
                        type='song', duration=max(0, int(sony['duration_seconds'] - context.get('offset', 0))),
                        title=sony.get('title') or 'Sony Music Center', artist=sony.get('artist', '')))
                    self.last_adjusted = float('-inf')
                    self.update(message='Wznawianie Volumio i wyrównywanie pozycji…',
                                drift_seconds=None, error=None)
                    return
                elif desired == 'PAUSED_PLAYBACK':
                    self.loading_uri = None
                    if volume_state.get('status') == 'play':
                        self.volumio.command('pause')
                elif desired == 'STOPPED':
                    if volume_state.get('status') != 'stop':
                        self.volumio.command('stop')
                    self.update(message='Oba odtwarzacze zatrzymane', drift_seconds=None, error=None)
                    return
                if abs(drift) > 1.5 and time.monotonic() - self.last_adjusted >= 12:
                    self.volumio.command('seek', position=int(target))
                    self.last_adjusted = time.monotonic()
                    self.update(message='Korygowanie pozycji · dokładność przybliżona',
                                drift_seconds=round(drift, 2), error=None)
                else:
                    self.update(message='Graj razem · Sony prowadzi · dokładność przybliżona',
                                drift_seconds=round(drift, 2), error=None)
            except Exception as exc:
                self.update(error=str(exc), message='Nie udało się wyrównać odtwarzaczy', drift_seconds=None)
                self.logger.warning('Wspólne granie: %s', exc)

    def run(self, stop):
        while not stop.wait(2):
            self.tick()
