#!/usr/bin/env python3
"""Loopback-only YouTube audio service for the legacy Volumio plugin.

Runs as volumio inside its own Alpine userspace, never the host Python/Node.
"""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess
import struct
import socket
import threading
import urllib.request

from sony_youtube import YouTubeResolver
from sony_youtube_stream import YouTubeStreamService

PORT = 18888
resolver = YouTubeResolver(Path(__file__).parent)
resolver.slots = threading.BoundedSemaphore(1)
v2_streams = YouTubeStreamService(resolver)
streams = threading.BoundedSemaphore(1)
ID = r'([A-Za-z0-9_-]{11})'
BYTE_RATE = 44100 * 2 * 2
wav_slots = threading.BoundedSemaphore(3)
active_lock = threading.Lock()
active_wav = {}


def cancel_previous(video_id):
    with active_lock:
        previous = active_wav.pop(video_id, None)
    if previous:
        handler, process = previous
        process.terminate()
        try:
            handler.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def wav_header(size):
    return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', size + 36, b'WAVE',
        b'fmt ', 16, 1, 2, 44100, BYTE_RATE, 4, 16, b'data', size)


def byte_range(value, total):
    if not value:
        return 0, total - 1
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
    if not match or not any(match.groups()):
        raise ValueError('Invalid range')
    start, end = match.groups()
    if not start:
        count = int(end)
        if not count:
            raise ValueError('Empty suffix')
        return max(0, total - count), total - 1
    first = int(start)
    last = min(int(end), total - 1) if end else total - 1
    if first > last or first >= total:
        raise ValueError('Unsatisfiable range')
    return first, last


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, fmt, *args):
        # Do not log signed source URLs or exception text from upstream.
        pass

    def json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def wav_audio(self, video_id, offset=0):
        process = None
        started = False
        acquired = False
        if self.command != 'HEAD':
            cancel_previous(video_id)
            acquired = wav_slots.acquire(timeout=5)
            if not acquired:
                return self.json(429, {'error': 'Audio stream busy'})
        try:
            info = resolver.resolve(video_id)
            duration = int(info['videoDetails']['lengthSeconds']) - offset
            if duration <= 0 or duration * BYTE_RATE > 0xffffffff - 36:
                return self.json(422, {'error': 'Przewijanie dostępne dla nagrań o znanej długości do 6 godzin.'})
            header = wav_header(duration * BYTE_RATE)
            total = len(header) + duration * BYTE_RATE
            try:
                first, last = byte_range(self.headers.get('Range'), total)
            except ValueError:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{total}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            length = last - first + 1
            prefix = header[first:min(last + 1, len(header))] if first < len(header) else b''
            audio_start = max(0, first - len(header))
            remaining = length - len(prefix)
            skip = audio_start % 4
            if self.command != 'HEAD' and remaining:
                process = subprocess.Popen([
                    '/usr/bin/ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                    '-rw_timeout', '15000000', '-ss', str(offset + (audio_start // 4) / 44100),
                    '-re', '-i', info['formats'][0]['url'],
                    '-t', str((remaining + skip + 3) // 4 / 44100),
                    '-vn', '-ac', '2', '-ar', '44100', '-codec:a', 'pcm_s16le',
                    '-f', 's16le', 'pipe:1'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                with active_lock:
                    previous = active_wav.get(video_id)
                    active_wav[video_id] = (self, process)
                # Cover overlapping requests that both passed the initial cancellation.
                if previous:
                    previous[1].terminate()
                    try:
                        previous[0].connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            self.send_response(206 if self.headers.get('Range') else 200)
            self.send_header('Content-Type', 'audio/wav')
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(length))
            if self.headers.get('Range'):
                self.send_header('Content-Range', f'bytes {first}-{last}/{total}')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            started = True
            if self.command == 'HEAD':
                return
            self.wfile.write(prefix)
            if process is not None:
                if skip:
                    process.stdout.read(skip)
                while remaining:
                    chunk = process.stdout.read(min(32768, remaining))
                    if not chunk:
                        # Rounded metadata can exceed the decoded end by <1s.
                        if process.wait(timeout=3) == 0 and remaining <= BYTE_RATE:
                            self.wfile.write(bytes(remaining))
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    remaining -= len(chunk)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            if not started:
                self.json(502, {'error': 'YouTube audio unavailable'})
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stdout.close()
            with active_lock:
                active = active_wav.get(video_id)
                if active and active[0] is self:
                    del active_wav[video_id]
            if acquired:
                wav_slots.release()

    def do_POST(self):
        if not v2_streams.route(self):
            self.json(404, {"error": "Not found"})

    def do_GET(self):
        if v2_streams.route(self):
            return
        offset_match = re.fullmatch('/audio/' + ID + r'/(\d{1,6})\.wav', self.path)
        if offset_match:
            return self.wav_audio(offset_match[1], int(offset_match[2]))
        match = re.fullmatch('/audio/' + ID + r'\.wav', self.path)
        if match:
            return self.wav_audio(match[1])
        if self.path == '/health':
            return self.json(200, {'ok': True, 'standalone': True})
        match = re.fullmatch('/api/youtube/info/' + ID, self.path)
        if match:
            try:
                info = copy.deepcopy(resolver.resolve(match[1]))
                info['formats'] = [dict(itag=140,
                    url=f'http://127.0.0.1:{PORT}/audio/{match[1]}.mp3',
                    mimeType='audio/mpeg; codecs="mp3"', container='mp3',
                    codecs='mp3', audioCodec='mp3', audioBitrate=192,
                    bitrate=192000, audioSampleRate='44100',
                    hasAudio=True, hasVideo=False, isLive=False,
                    isHLS=False, isDashMPD=False)]
                if 0 < int(info['videoDetails'].get('lengthSeconds') or 0) * BYTE_RATE <= 0xffffffff - 36:
                    info['formats'][0].update(
                        url=f'http://127.0.0.1:{PORT}/audio/{match[1]}.wav',
                        mimeType='audio/wav; codecs="pcm_s16le"', container='wav',
                        codecs='pcm_s16le', audioCodec='pcm_s16le',
                        bitrate=BYTE_RATE * 8, audioBitrate=BYTE_RATE * 8 / 1000)
                return self.json(200, info)
            except (ValueError, RuntimeError, subprocess.TimeoutExpired):
                return self.json(502, {'error': 'Nie udało się pobrać audio z YouTube. Spróbuj ponownie.'})
        match = re.fullmatch('/audio/' + ID + r'\.mp3', self.path)
        if not match:
            return self.json(404, {'error': 'Not found'})
        if self.command == 'HEAD':
            self.send_response(200)
            self.send_header('Content-Type', 'audio/mpeg')
            self.end_headers()
            return
        if not streams.acquire(blocking=False):
            return self.json(429, {'error': 'Audio stream busy'})
        source = process = None
        try:
            info = resolver.resolve(match[1])
            source = urllib.request.urlopen(info['formats'][0]['url'], timeout=15)
            process = subprocess.Popen([
                '/usr/bin/ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-re', '-i', 'pipe:0', '-vn', '-ac', '2', '-ar', '44100',
                '-codec:a', 'libmp3lame', '-b:a', '192k', '-flush_packets', '1',
                '-f', 'mp3', 'pipe:1'], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

            def feed():
                try:
                    while chunk := source.read(16384):
                        process.stdin.write(chunk)
                        process.stdin.flush()
                except (OSError, ValueError):
                    pass
                finally:
                    try:
                        process.stdin.close()
                    except (OSError, ValueError):
                        pass

            threading.Thread(target=feed, daemon=True).start()
            self.send_response(200)
            self.send_header('Content-Type', 'audio/mpeg')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            while chunk := process.stdout.read(4096):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            if process is None:
                self.json(502, {'error': 'YouTube audio unavailable'})
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stdout.close()
            if source is not None:
                source.close()
            streams.release()


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
