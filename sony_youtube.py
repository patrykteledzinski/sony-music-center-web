"""Compatibility resolver for the legacy Volumio YouTube2 client."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time


class CancelledResolution(RuntimeError):
    pass


class YouTubeResolver:
    def __init__(self, project):
        self.project = Path(project)
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(2)
        self.cache = {}

    @staticmethod
    def convert(info):
        formats = []
        for item in info.get('formats', []):
            codec = item.get('acodec') or ''
            if item.get('vcodec') != 'none' or not codec.startswith('mp4a') or not item.get('url'):
                continue
            # YouTube also returns variants such as 139-drc / 140-0.
            # The legacy client expects the numeric base itag.
            format_id = re.fullmatch(r'(\d+)(?:[-.].*)?', str(item.get('format_id', '')))
            if not format_id:
                continue
            formats.append(dict(itag=int(format_id[1]), url=item['url'],
                mimeType='audio/mp4; codecs="' + codec + '"', container='mp4',
                codecs=codec, audioCodec=codec, audioBitrate=item.get('abr') or 128,
                bitrate=int((item.get('tbr') or item.get('abr') or 128) * 1000),
                audioSampleRate=str(item.get('asr') or 44100), contentLength=str(item.get('filesize') or ''),
                hasAudio=True, hasVideo=False, isLive=False, isHLS=False, isDashMPD=False))
        formats.sort(key=lambda item: item['audioBitrate'], reverse=True)
        if not formats:
            raise ValueError('Brak zgodnego strumienia AAC dla starego Volumio')
        return dict(videoDetails=dict(videoId=info['id'], title=info.get('title') or info['id'],
            lengthSeconds=str(int(info.get('duration') or 0)),
            isLiveContent=bool(info.get('is_live') or info.get('live_status') == 'is_live'), thumbnails=info.get('thumbnails') or [],
            externalChannelId=info.get('channel_id') or '', ownerChannelName=info.get('uploader') or '',
            author=dict(name=info.get('uploader') or '',id=info.get('channel_id') or '')),
            formats=formats, related_videos=[], response={})

    @staticmethod
    def _cancellable_run(command, env, cancelled, timeout=55):
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True)
        deadline = time.monotonic() + timeout
        try:
            while True:
                if cancelled():
                    raise CancelledResolution('Zmieniono nagranie.')
                if time.monotonic() > deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    stdout, stderr = process.communicate(timeout=.2)
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()

    def resolve(self, video_id, cancelled=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise ValueError('Nieprawidłowy identyfikator filmu YouTube')
        with self.lock:
            cached = self.cache.get(video_id)
            if cached and time.monotonic() - cached[0] < 300:
                return cached[1]
        if cancelled is None:
            acquired = self.slots.acquire(timeout=25)
        else:
            deadline = time.monotonic() + 25
            acquired = False
            while time.monotonic() < deadline:
                if cancelled():
                    raise CancelledResolution('Zmieniono nagranie.')
                if self.slots.acquire(timeout=.2):
                    acquired = True
                    break
        if not acquired:
            raise RuntimeError('Resolver jest zajęty; spróbuj ponownie za chwilę')
        try:
            # Another request may have resolved this video while we waited.
            with self.lock:
                cached = self.cache.get(video_id)
                if cached and time.monotonic() - cached[0] < 300:
                    return cached[1]
            tools = self.project / '.tools'
            if not (tools / 'youtube-resolver/yt_dlp').is_dir() or not (tools / 'youtube-node').is_file():
                raise RuntimeError('Brak lokalnych narzędzi resolvera YouTube')
            env = dict(os.environ, PYTHONPATH=str(tools / 'youtube-resolver'))
            command = [sys.executable, '-m', 'yt_dlp', '--skip-download', '--no-playlist',
                '--socket-timeout', '8', '--retries', '0', '--extractor-retries', '0',
                '--cache-dir', str(tools / 'youtube-cache'),
                '--js-runtimes', 'node:' + str(tools / 'youtube-node'),
                '--remote-components', 'ejs:github', '--dump-single-json',
                'https://www.youtube.com/watch?v=' + video_id]
            result = (self._cancellable_run(command, env, cancelled) if cancelled is not None else
                      subprocess.run(command, env=env, capture_output=True, text=True, timeout=55))
            if result.returncode:
                raise RuntimeError('YouTube nie udostępnił strumienia audio dla tego filmu')
            converted = self.convert(json.loads(result.stdout))
            with self.lock:
                self.cache[video_id] = (time.monotonic(), converted)
                while len(self.cache) > 32:
                    del self.cache[next(iter(self.cache))]
            return converted
        finally:
            self.slots.release()
