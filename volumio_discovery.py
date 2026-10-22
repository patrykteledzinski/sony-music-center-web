"""Monitor Volumio and rediscover its HTTP API using SSDP renderer addresses."""
import ipaddress
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
import urllib.request

from sony_discovery import atomic_json, discover_locations


def system_info(base):
    with urllib.request.urlopen(base + '/api/v1/getSystemInfo', timeout=3) as response:
        data = json.loads(response.read(128 * 1024))
    if not isinstance(data, dict) or data.get('variant') != 'volumio' or not data.get('id'):
        raise ValueError('To urządzenie nie jest Volumio')
    return data


class VolumioMonitor:
    def __init__(self, base, state_file, logger):
        self.base = base
        self.state_file = Path(state_file)
        self.logger = logger
        self.device_id = None
        self.lock = threading.Lock()
        self.last_discovery = float('-inf')
        self.state = dict(enabled=bool(base), online=False, url=base, name='Volumio', error=None)
        try:
            saved = json.loads(self.state_file.read_text())
            parsed = urlsplit(saved['url'])
            if parsed.scheme != 'http' or not ipaddress.IPv4Address(parsed.hostname).is_private:
                raise ValueError('Invalid saved Volumio address')
            if base:
                self.base = saved['url']
                self.device_id = saved['id']
                self.state['url'] = self.base
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def accept(self, base, info):
        if self.device_id and info['id'] != self.device_id:
            raise ValueError('Pod tym adresem odpowiada inne Volumio')
        previous = self.base
        self.base, self.device_id = base, info['id']
        with self.lock:
            self.state.update(online=True, url=base, name=info.get('name') or 'Volumio',
                              error=None, checked_at=time.time())
        try:
            atomic_json(self.state_file, dict(url=base, id=self.device_id))
        except OSError as exc:
            self.logger.warning('Nie udało się zapisać adresu Volumio: %s', exc)
        if previous != base:
            self.logger.info('Odnaleziono Volumio: %s -> %s', previous, base)

    def check(self):
        if not self.base:
            return
        try:
            info = system_info(self.base)
            self.accept(self.base, info)
            return
        except (OSError, ValueError, KeyError) as exc:
            with self.lock:
                self.state.update(online=False, error=str(exc), checked_at=time.time())
        if time.monotonic() - self.last_discovery < 30:
            return
        self.last_discovery = time.monotonic()
        matches = {}
        try:
            # UPnP port is not the Volumio web port. Only use the advertised host.
            hosts = set()
            for location in discover_locations():
                try:
                    host = urlsplit(location).hostname
                    if ipaddress.IPv4Address(host).is_private:
                        hosts.add(host)
                except (ValueError, TypeError):
                    continue
            web_port = urlsplit(self.base).port or 80
            for host in sorted(hosts):
                base = f'http://{host}' + (f':{web_port}' if web_port != 80 else '')
                try:
                    info = system_info(base)
                    if not self.device_id or info['id'] == self.device_id:
                        matches[base] = info
                except (OSError, ValueError, KeyError):
                    continue
        except OSError as exc:
            self.logger.warning('Wykrywanie Volumio nie powiodło się: %s', exc)
        if len(matches) == 1:
            base, info = next(iter(matches.items()))
            self.accept(base, info)

    def run(self, stop):
        while not stop.is_set():
            try:
                self.check()
            except Exception:
                self.logger.exception('Błąd monitora Volumio')
            stop.wait(30)
