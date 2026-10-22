"""Recover a Sony endpoint through SSDP, preserving the device's UPnP identity."""
import ipaddress
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def atomic_json(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False,
                                         encoding='utf-8') as stream:
            name = stream.name
            json.dump(values, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def describe(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'http' or parsed.username or parsed.password:
        raise ValueError('Invalid device description URL')
    if not ipaddress.IPv4Address(parsed.hostname).is_private:
        raise ValueError('Device must have a local IPv4 address')
    with urllib.request.urlopen(url, timeout=2) as response:
        root = ET.fromstring(response.read(256 * 1024))
    ns = {'d': 'urn:schemas-upnp-org:device-1-0'}
    device = root.find('d:device', ns)
    if device is None:
        raise ValueError('Missing UPnP device')
    def value(name):
        return device.findtext('d:' + name, '', ns).strip()
    if 'sony' not in value('manufacturer').lower() or not value('UDN'):
        raise ValueError('Not a Sony device')
    if 'MediaRenderer' not in value('deviceType'):
        raise ValueError('Not a media renderer')
    return dict(udn=value('UDN'), model=value('modelName'))


def discover_locations():
    locations = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.settimeout(0.3)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        message = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n'
                   'MAN: "ssdp:discover"\r\nMX: 2\r\n'
                   'ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n')
        sock.sendto(message.encode(), ('239.255.255.250', 1900))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                data, _ = sock.recvfrom(8192)
            except socket.timeout:
                continue
            for line in data.decode(errors='replace').splitlines():
                key, sep, value = line.partition(':')
                if sep and key.lower() == 'location':
                    locations.add(value.strip())
    return sorted(locations)[:16]


class SonyEndpoint:
    def __init__(self, base, state_file, config_file, logger):
        self.base = base
        self.state_file = Path(state_file)
        self.config_file = Path(config_file)
        self.logger = logger
        self.lock = threading.Lock()
        self.last_attempt = float('-inf')
        try:
            self.identity = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            self.identity = None

    def remember(self):
        try:
            identity = describe(self.base + '/description.xml')
            if self.identity and identity['udn'] != self.identity['udn']:
                self.logger.warning('Adres Sony wskazuje inne urządzenie; zachowuję zapamiętaną tożsamość')
                return
            self.identity = identity
            atomic_json(self.state_file, identity)
        except (OSError, ValueError, ET.ParseError) as exc:
            self.logger.warning('Nie udało się zapamiętać tożsamości Sony: %s', exc)

    def recover(self, failed_base):
        with self.lock:
            if self.base != failed_base:
                return True
            if time.monotonic() - self.last_attempt < 30:
                return False
            self.last_attempt = time.monotonic()
            matches = {}
            try:
                for url in discover_locations():
                    try:
                        identity = describe(url)
                        if self.identity:
                            if identity['udn'] != self.identity['udn']:
                                continue
                        elif identity['model'] != 'CMT-MX700Ni/MX750Ni':
                            continue
                        parsed = urllib.parse.urlparse(url)
                        base = f'http://{parsed.hostname}:{parsed.port or 80}'
                        matches[base] = identity
                    except (OSError, ValueError, ET.ParseError):
                        continue
            except OSError as exc:
                self.logger.warning('Wykrywanie Sony nie powiodło się: %s', exc)
            if len(matches) != 1:
                return False
            base, identity = next(iter(matches.items()))
            self.base, self.identity = base, identity
            try:
                atomic_json(self.state_file, identity)
                # Explicit environment overrides remain under operator control.
                if not any(os.environ.get(k) for k in ('SONY_WEB_SONY_IP', 'SONY_WEB_SONY_PORT')):
                    config = json.loads(self.config_file.read_text()) if self.config_file.exists() else {}
                    parsed = urllib.parse.urlparse(base)
                    config.update(sony_ip=parsed.hostname, sony_port=parsed.port)
                    atomic_json(self.config_file, config)
            except (OSError, ValueError) as exc:
                self.logger.warning('Sony odnalezione, ale zapis konfiguracji nie powiódł się: %s', exc)
            self.logger.info('Odnaleziono Sony: %s -> %s', failed_base, base)
            return base != failed_base

    def open(self, req, timeout=4):
        base = self.base
        path = urllib.parse.urlsplit(req.full_url).path
        req.full_url = base + path
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except (OSError, urllib.error.URLError):
            recovered = self.recover(base)
            action = req.get_header('Soapaction', '').strip('"').split('#')[-1]
            # Do not replay commands: a timed-out action may already have executed.
            if recovered and (action.startswith('Get') or action.startswith('X_Get')):
                req.full_url = self.base + path
                return urllib.request.urlopen(req, timeout=timeout)
            raise
