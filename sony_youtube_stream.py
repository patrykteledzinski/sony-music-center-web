"""Versioned, cancellable PCM streams. Source URLs never leave this process."""
from collections import OrderedDict
from dataclasses import dataclass, field
import json
import os
import re
import select
import socket
import struct
import subprocess
import threading
import time

from sony_youtube import CancelledResolution

RATE = 44100
FRAME = 4
BYTE_RATE = RATE * FRAME


class StaleStream(Exception):
    pass


@dataclass(eq=False)
class Ticket:
    session: str
    revision: int
    video: str
    offset_ms: int
    connection: object
    cancelled: threading.Event = field(default_factory=threading.Event)
    process: object = None

    def cancel(self):
        self.cancelled.set()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class StreamRegistry:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest = OrderedDict()
        self.active = set()

    def reserve(self, session, revision, video, offset_ms, connection):
        with self.lock:
            prior = self.latest.get(session)
            descriptor = (revision, video, offset_ms)
            if prior and (revision < prior[0] or (revision == prior[0] and descriptor != prior)):
                raise StaleStream()
            self.latest[session] = descriptor
            self.latest.move_to_end(session)
            old = [ticket for ticket in self.active if ticket.session == session and ticket.revision < revision]
            ticket = Ticket(session, revision, video, offset_ms, connection)
            self.active.add(ticket)
            # Bound tombstones without forgetting any active session.
            busy = {item.session for item in self.active}
            for key in list(self.latest):
                if len(self.latest) <= 128:
                    break
                if key not in busy:
                    del self.latest[key]
        for item in old:
            item.cancel()
        return ticket

    def remove(self, ticket):
        with self.lock:
            self.active.discard(ticket)

    def cancel(self, session):
        with self.lock:
            prior = self.latest.get(session, (0, '', 0))
            self.latest[session] = (prior[0] + 1, '', -1)
            old = [item for item in self.active if item.session == session]
        for item in old:
            item.cancel()

    def stats(self):
        with self.lock:
            return {'active': len(self.active), 'sessions': len(self.latest)}


def pcm_header(size):
    return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', size + 36, b'WAVE', b'fmt ',
                       16, 1, 2, RATE, BYTE_RATE, FRAME, 16, b'data', size)


def parse_range(value, total):
    if not value:
        return 0, total - 1
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
    if not match or not any(match.groups()):
        raise ValueError('range')
    a, b = match.groups()
    if not a:
        count = int(b)
        if count <= 0:
            raise ValueError('range')
        return max(0, total - count), total - 1
    first, last = int(a), min(int(b), total - 1) if b else total - 1
    if first > last or first >= total:
        raise ValueError('range')
    return first, last


class YouTubeStreamService:
    def __init__(self, resolver, ffmpeg='/usr/bin/ffmpeg'):
        self.resolver = resolver
        self.ffmpeg = ffmpeg
        self.registry = StreamRegistry()
        self.slots = threading.BoundedSemaphore(3)

    @staticmethod
    def disconnected(connection):
        try:
            if select.select([connection], [], [], 0)[0]:
                return connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b''
        except OSError:
            return True
        return False

    def route(self, handler):
        path = handler.path
        if path == '/v2/health':
            handler.json(200, dict(version=2, **self.registry.stats()))
            return True
        match = re.fullmatch(r'/v2/info/([A-Za-z0-9_-]{11})', path)
        if match:
            try:
                info = self.resolver.resolve(match[1], cancelled=lambda: self.disconnected(handler.connection))
                details = info['videoDetails']
                duration = int(details.get('lengthSeconds') or 0) * 1000
                live = bool(details.get('isLiveContent'))
                handler.json(200, dict(videoId=match[1], title=details.get('title'),
                    durationMs=duration, live=live,
                    seekable=not live and 0 < duration * BYTE_RATE // 1000 <= 0xffffffff - 36))
            except (CancelledResolution, BrokenPipeError, ConnectionResetError):
                pass
            except (ValueError, RuntimeError, subprocess.TimeoutExpired):
                if self.disconnected(handler.connection):
                    return True
                handler.json(503, {'error': 'Nie udało się otworzyć źródła YouTube. Spróbuj ponownie.'})
            return True
        match = re.fullmatch(r'/v2/cancel/([a-f0-9]{24})', path)
        if match and handler.command == 'POST':
            self.registry.cancel(match[1])
            handler.json(200, {'ok': True})
            return True
        match = re.fullmatch(r'/v2/audio/([a-f0-9]{24})/([1-9]\d{0,9})/([A-Za-z0-9_-]{11})/(\d{1,8})\.wav', path)
        if match and handler.command in ['GET', 'HEAD']:
            self.stream(handler, match[1], int(match[2]), match[3], int(match[4]))
            return True
        if path.startswith('/v2/'):
            handler.json(404, {'error': 'Nieprawidłowe żądanie audio.'})
            return True
        return False

    @staticmethod
    def read(process, count, ticket, timeout=18):
        deadline = time.monotonic() + timeout
        while not ticket.cancelled.is_set():
            if select.select([process.stdout], [], [], .2)[0]:
                return os.read(process.stdout.fileno(), count)
            if time.monotonic() > deadline:
                raise TimeoutError('decoder timeout')
        raise StaleStream()

    def stream(self, handler, session, revision, video, offset_ms):
        ticket = process = None
        acquired = sent = False
        started = time.monotonic()
        try:
            if handler.command != 'HEAD':
                ticket = self.registry.reserve(session, revision, video, offset_ms, handler.connection)
            info = self.resolver.resolve(video, cancelled=(lambda: ticket.cancelled.is_set()) if ticket else None)
            duration_ms = int(info['videoDetails'].get('lengthSeconds') or 0) * 1000
            if info['videoDetails'].get('isLiveContent') or offset_ms >= duration_ms:
                handler.json(422, {'error': 'Pozycja poza nagraniem lub transmisja na żywo.'})
                return
            frames = (duration_ms - offset_ms) * RATE // 1000
            size = frames * FRAME
            if size <= 0 or size > 0xffffffff - 36:
                handler.json(422, {'error': 'Nieobsługiwana długość nagrania.'})
                return
            header = pcm_header(size)
            total = len(header) + size
            try:
                first, last = parse_range(handler.headers.get('Range'), total)
            except ValueError:
                handler.send_response(416)
                handler.send_header('Content-Range', f'bytes */{total}')
                handler.send_header('Content-Length', '0')
                handler.end_headers()
                return
            prefix = header[first:min(last + 1, 44)] if first < 44 else b''
            remaining = last - first + 1 - len(prefix)
            initial = b''
            if handler.command != 'HEAD' and remaining:
                if ticket.cancelled.is_set():
                    raise StaleStream()
                acquired = self.slots.acquire(timeout=4)
                if not acquired:
                    handler.json(503, {'error': 'Odtwarzacz kończy poprzedni strumień. Spróbuj ponownie.'})
                    return
                if ticket.cancelled.is_set():
                    raise StaleStream()
                byte_offset = max(0, first - 44)
                skip = byte_offset % FRAME
                source_start = offset_ms / 1000 + (byte_offset // FRAME) / RATE
                process = subprocess.Popen([
                    self.ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
                    '-rw_timeout', '15000000', '-ss', f'{source_start:.9f}',
                    '-re', '-i', info['formats'][0]['url'],
                    '-t', f'{((remaining + skip + 3) // FRAME) / RATE:.9f}',
                    '-vn', '-ac', '2', '-ar', str(RATE), '-codec:a', 'pcm_s16le',
                    '-f', 's16le', 'pipe:1'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                ticket.process = process
                if ticket.cancelled.is_set():
                    raise StaleStream()
                initial = self.read(process, min(8192, remaining + skip), ticket)
                if not initial or len(initial) <= skip:
                    raise OSError('decoder produced no audio')
                initial = initial[skip:]
            handler.connection.settimeout(5)
            handler.send_response(206 if handler.headers.get('Range') else 200)
            handler.send_header('Content-Type', 'audio/wav')
            handler.send_header('Content-Length', str(last - first + 1))
            handler.send_header('Accept-Ranges', 'bytes')
            handler.send_header('Cache-Control', 'no-store')
            if handler.headers.get('Range'):
                handler.send_header('Content-Range', f'bytes {first}-{last}/{total}')
            handler.end_headers()
            sent = True
            if handler.command == 'HEAD':
                return
            handler.wfile.write(prefix)
            if initial:
                handler.wfile.write(initial)
                remaining -= len(initial)
            while remaining:
                chunk = self.read(process, min(32768, remaining), ticket)
                if not chunk:
                    if process.wait(timeout=2) == 0 and remaining <= BYTE_RATE:
                        handler.wfile.write(bytes(remaining))
                        remaining = 0
                    break
                handler.wfile.write(chunk)
                handler.wfile.flush()
                remaining -= len(chunk)
        except (StaleStream, CancelledResolution):
            if not sent and (ticket is None or not ticket.cancelled.is_set()):
                handler.json(409, {'error': 'Żądanie zastąpione nowszą pozycją.'})
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            if not sent:
                try:
                    handler.json(503, {'error': 'Źródło audio nie odpowiedziało. Spróbuj ponownie.'})
                except OSError:
                    pass
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stdout.close()
            if acquired:
                self.slots.release()
            if ticket is not None:
                self.registry.remove(ticket)
                print(json.dumps(dict(event='youtube-stream', session=session[:6], revision=revision,
                    offsetMs=offset_ms, cancelled=ticket.cancelled.is_set(),
                    seconds=round(time.monotonic()-started, 2))), flush=True)
