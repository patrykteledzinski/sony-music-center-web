"""Portable settings with optional local JSON and environment overrides."""
from dataclasses import dataclass
from pathlib import Path
import ipaddress
import json
import os


@dataclass(frozen=True)
class Settings:
    sony_ip: str
    sony_port: int
    server_ip: str
    web_port: int
    music_root: Path
    state_dir: Path
    bind_host: str
    ffmpeg_binary: str | None
    volumio_ip: str | None = None
    volumio_port: int = 80


def load_settings(project_dir, environ=None):
    project_dir = Path(project_dir).resolve()
    environ = os.environ if environ is None else environ
    explicit = environ.get('SONY_WEB_CONFIG')
    file = Path(explicit).expanduser() if explicit else project_dir / 'config.json'
    if not file.is_absolute():
        file = project_dir / file
    values = {}
    if file.exists():
        try:
            values = json.loads(file.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ValueError(f'Cannot read configuration {file}: {exc}') from exc
        if not isinstance(values, dict):
            raise ValueError('Configuration must be a JSON object')
    elif explicit:
        raise ValueError(f'Configuration does not exist: {file}')
    defaults = dict(sony_ip='192.168.1.100',sony_port=8080,server_ip='192.168.1.50',
                    web_port=8088,music_root=str(Path.home()/'Music'),state_dir='.',
                    bind_host='0.0.0.0',ffmpeg_binary=None,volumio_ip=None,volumio_port=80)
    unknown = set(values) - set(defaults)
    if unknown:
        raise ValueError('Unknown settings: '+', '.join(sorted(unknown)))
    for name, default in defaults.items():
        values[name] = environ.get('SONY_WEB_'+name.upper(), values.get(name, default))
    for name in ('sony_ip','server_ip','bind_host','volumio_ip'):
        if name == 'volumio_ip' and values[name] is None:
            continue
        try:
            if not isinstance(values[name], str):
                raise ipaddress.AddressValueError('not a string')
            values[name] = str(ipaddress.IPv4Address(values[name]))
        except ipaddress.AddressValueError as exc:
            raise ValueError(f'{name} must be an IPv4 address') from exc
    for name in ('sony_port','web_port','volumio_port'):
        raw = values[name]
        try:
            port = int(raw)
        except (ValueError,TypeError) as exc:
            raise ValueError(f'{name} must be a port number') from exc
        if isinstance(raw,bool) or str(port) != str(raw) or not 1 <= port <= 65535:
            raise ValueError(f'{name} must be a port between 1 and 65535')
        values[name] = port
    for name in ('music_root','state_dir'):
        if not isinstance(values[name],str) or not values[name].strip():
            raise ValueError(f'{name} must be a path')
        path = Path(values[name]).expanduser()
        values[name] = (path if path.is_absolute() else project_dir/path).resolve()
    if values['ffmpeg_binary'] and not isinstance(values['ffmpeg_binary'], str):
        raise ValueError('ffmpeg_binary must be a path or null')
    if values['ffmpeg_binary']:
        path = Path(values['ffmpeg_binary']).expanduser()
        values['ffmpeg_binary'] = str((path if path.is_absolute() else project_dir/path).resolve())
    return Settings(**values)
