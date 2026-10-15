"""Prepare seekable audio files for Sony renderers without time-based Seek."""
from functools import lru_cache
from pathlib import Path
import json
import os
import math
import re
import shutil
import subprocess
import uuid


class SeekCache:
    MAX_BYTES = 768 * 1024 * 1024

    def __init__(self, directory, app_dir, ffmpeg_binary=None):
        self.directory = Path(directory)
        self.app_dir = Path(app_dir)
        self.ffmpeg_binary = ffmpeg_binary

    def ffmpeg(self):
        if self.ffmpeg_binary:
            if not Path(self.ffmpeg_binary).is_file() or not os.access(self.ffmpeg_binary, os.X_OK):
                raise RuntimeError('Configured FFmpeg binary is missing or not executable')
            return self.ffmpeg_binary
        binary = shutil.which('ffmpeg')
        if binary:
            return binary
        bundled = sorted((self.app_dir / '.tools/imageio_ffmpeg/binaries').glob('ffmpeg-*'))
        if bundled:
            return str(bundled[0])
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError):
            raise RuntimeError('FFmpeg is required: install ffmpeg or requirements-media.txt') from None

    def duration(self, path):
        path = Path(path)
        stat = path.stat()
        return self._duration(str(path), stat.st_mtime_ns, stat.st_size)

    @lru_cache(maxsize=256)
    def _duration(self, path, modified, size):
        result = subprocess.run(
            [self.ffmpeg(), '-nostdin', '-hide_banner', '-i', path],
            capture_output=True, text=True, timeout=15,
        )
        match = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', result.stderr)
        if not match:
            raise ValueError('Nie można ustalić długości tego pliku audio')
        hours, minutes, seconds = map(float, match.groups())
        return hours * 3600 + minutes * 60 + seconds

    def pcm_format(self, path):
        stat = Path(path).stat()
        return self._pcm_format(str(path), stat.st_mtime_ns, stat.st_size)

    @lru_cache(maxsize=256)
    def _pcm_format(self, path, modified, size):
        result = subprocess.run(
            [self.ffmpeg(), '-nostdin', '-hide_banner', '-i', path],
            capture_output=True, text=True, timeout=15,
        )
        line = next((line for line in result.stderr.splitlines() if 'Audio:' in line), '')
        sample = re.search(r', (u8|s16|s32|s64|flt|dbl)p?(?: \((\d+) bit\))?(?:,|$)', line)
        rate = re.search(r'(\d+) Hz', line)
        channels = re.search(r'(\d+) channels', line)
        if channels:
            count = int(channels.group(1))
        elif ', mono,' in line:
            count = 1
        elif ', stereo,' in line:
            count = 2
        else:
            layout = re.search(r', (\d+)\.(\d+)(?:\([^)]*\))?,', line)
            count = sum(map(int, layout.groups())) if layout else 0
        if not sample or not rate or not count:
            raise ValueError('Nie można określić parametrów audio bez zmiany jakości')
        kind, precision = sample.groups()
        if kind in ('flt', 'dbl'):
            bits = 32 if kind == 'flt' else 64
            codec = 'pcm_f' + str(bits) + 'le'
        else:
            bits = int(precision) if precision else int(kind[1:])
            if bits not in (8, 16, 24, 32, 64):
                raise ValueError('Nieobsługiwana głębia bitowa audio')
            codec = 'pcm_u8' if bits == 8 else 'pcm_s' + str(bits) + 'le'
        source_codec = re.search(r'Audio: ([a-zA-Z0-9_]+)', line).group(1)
        return dict(codec=codec, source_codec=source_codec, bits=bits, rate=int(rate.group(1)), channels=count)

    def record(self, token):
        if not re.fullmatch(r'[a-f0-9]{32}', token):
            raise ValueError('Nieprawidłowy identyfikator przewijania')
        try:
            data = json.loads((self.directory / (token + '.json')).read_text())
            if not isinstance(data, dict):
                raise ValueError()
            extension = data.get('extension', 'mp3')  # Read fragments from older versions.
            if extension not in ('mp3', 'wav', 'm4a', 'aac') or not (self.directory / (token + '.' + extension)).is_file():
                raise ValueError()
            return {**data, 'extension': extension}
        except (OSError, ValueError, TypeError):
            raise ValueError('Plik przewijania wygasł. Uruchom utwór z biblioteki.') from None

    def create(self, source, relative, seconds, duration, keep_token=None, output_rate=None, force_pcm=False):
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise ValueError('Pozycja musi być liczbą sekund')
        if not math.isfinite(seconds) or seconds < 0 or seconds >= duration:
            raise ValueError('Pozycja poza długością utworu')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cleanup(keep_token)
        source = Path(source)
        # Copy MP3/AAC packets; decode other formats to integer/float PCM.
        # Callers may request resampling; channels and bit depth are preserved.
        audio = self.pcm_format(source)
        extension = ('wav' if force_pcm else
                     'mp3' if audio['source_codec'] == 'mp3' else
                     'aac' if audio['source_codec'] == 'aac' else 'wav')
        pcm = dict(audio) if extension == 'wav' else None
        if pcm and output_rate is not None:
            pcm['source_rate'] = pcm['rate']
            pcm['rate'] = output_rate
        estimate = (source.stat().st_size if extension != 'wav' else
                    math.ceil((duration - seconds) * pcm['rate'] * pcm['channels'] * (pcm['bits'] // 8)) + 65536)
        if estimate > self.MAX_BYTES or shutil.disk_usage(self.directory).free < estimate + 128 * 1024 * 1024:
            raise ValueError('Za mało miejsca w pamięci podręcznej na przewinięcie tego pliku')
        token = uuid.uuid4().hex
        output = self.directory / (token + '.' + extension)
        partial = self.directory / (token + '.partial')
        codec = ['-c:a', 'copy'] if extension != 'wav' else ['-c:a', pcm['codec']]
        if pcm and output_rate is not None:
            codec += ['-ar', str(output_rate)]
        container = 'adts' if extension == 'aac' else ('mp4' if extension == 'm4a' else extension)
        container_options = ['-movflags', '+faststart'] if extension == 'm4a' else []
        command = [self.ffmpeg(), '-nostdin', '-hide_banner', '-loglevel', 'error',
                   *(['-ss', str(seconds)] if seconds > 0 else []),
                   '-i', str(source), '-map', '0:a:0', '-vn',
                   *codec, '-map_metadata', '-1', *container_options, '-f', container, '-y', str(partial)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            if result.returncode or not partial.is_file() or partial.stat().st_size == 0:
                raise RuntimeError('FFmpeg nie przygotował pliku do przewijania')
            if partial.stat().st_size > self.MAX_BYTES:
                raise ValueError('Przygotowany plik przekracza limit pamięci podręcznej')
            record = dict(source=relative, offset=float(seconds), duration=float(duration), extension=extension)
            if pcm:
                record['pcm'] = pcm
            partial.replace(output)
            (self.directory / (token + '.json')).write_text(json.dumps(record), encoding='utf-8')
            return token, record
        except Exception:
            output.unlink(missing_ok=True)
            (self.directory / (token + '.json')).unlink(missing_ok=True)
            raise
        finally:
            partial.unlink(missing_ok=True)

    def cleanup(self, keep_token=None):
        # Preserve the current stream and one recent file; only delete our cache files.
        files = sorted((p for p in self.directory.iterdir() if p.suffix in ('.mp3', '.wav', '.m4a', '.aac')),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        retained = 0
        for path in files:
            if path.stem == keep_token:
                continue
            retained += 1
            if retained > 1:
                path.unlink(missing_ok=True)
                path.with_suffix('.json').unlink(missing_ok=True)
