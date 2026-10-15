"""Exercise real FFmpeg and Flask routes using temporary audio, never the Sony."""
from pathlib import Path
import html
import math
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import app
from sony_seek import SeekCache


class Transport:
    def __init__(self):
        self.uri = app.music_url('test.mp3')
        self.state = 'PLAYING'
        self.position = 2
        self.calls = []

    def __call__(self, action, extra=''):
        self.calls.append(action)
        if action == 'GetTransportInfo':
            return f'<CurrentTransportState>{self.state}</CurrentTransportState>'
        if action == 'GetPositionInfo':
            return (f'<TrackURI>{html.escape(self.uri)}</TrackURI>'
                    '<TrackDuration>0:00:20</TrackDuration>'
                    f'<RelTime>0:00:{self.position:02d}</RelTime>')
        if action == 'SetAVTransportURI':
            self.uri = app.xml_value(extra, 'CurrentURI')
            self.state = 'STOPPED'
            self.position = 0
        if action == 'Play':
            self.state = 'PLAYING'
        if action == 'Pause':
            self.state = 'PAUSED_PLAYBACK'
        return ''


class SeekTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.cache = SeekCache(cls.root / 'cache', app.APP_DIR)
        subprocess.run([cls.cache.ffmpeg(), '-nostdin', '-loglevel', 'error',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=20',
                        '-c:a', 'libmp3lame', str(cls.root / 'test.mp3')], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.transport = Transport()
        self.patches = [patch.object(app, 'MUSIC_ROOT', self.root),
                        patch.object(app, 'seek_cache', self.cache),
                        patch.object(app, 'sony_transport', self.transport),
                        patch.object(app, 'soap', return_value='<CurrentVolume>30</CurrentVolume>')]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = app.app.test_client()

    def seek(self, seconds):
        return self.client.post('/api/seek', json=dict(seconds=seconds, uri=self.transport.uri))

    def test_seek_repeatedly_uses_original_time_and_range_requests(self):
        response = self.seek(5)
        self.assertEqual(response.status_code, 200, response.json)
        uri = response.json['uri']
        token = uri.rsplit('/', 1)[-1][:-4]
        self.assertAlmostEqual(self.cache.duration(self.cache.directory / (token + '.mp3')), 15, delta=.15)
        self.transport.position = 2
        status = app.sony_status()
        self.assertEqual(status['position'], 7)
        self.assertEqual(status['duration_seconds'], 20)
        self.assertTrue(status['seekable'])
        # The second seek is relative to the original song, not the first fragment.
        self.assertEqual(self.seek(10).status_code, 200)
        status = app.sony_status()
        self.assertEqual(status['position'], 10)
        self.assertEqual(status['duration_seconds'], 20)
        route = self.transport.uri.split(f':{app.WEB_PORT}', 1)[1]
        with self.client.get(route, headers={'Range': 'bytes=0-31'}) as media:
            self.assertEqual(media.status_code, 206)
            self.assertEqual(len(media.data), 32)
            self.assertIn('DLNA.ORG_OP=01', media.headers['contentFeatures.dlna.org'])
        # Records survive service restarts.
        new_cache = SeekCache(self.cache.directory, app.APP_DIR)
        self.assertEqual(new_cache.record(token)['offset'], 5)

    def test_seek_preserves_pause(self):
        self.transport.state = 'PAUSED_PLAYBACK'
        response = self.seek(3)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.transport.state, 'PAUSED_PLAYBACK')
        self.assertIn('Pause', self.transport.calls)

    def test_rejects_bad_positions_without_changing_source(self):
        original = self.transport.uri
        for value in [-1, 21, '5', True, None, math.inf, math.nan]:
            response = self.seek(value)
            self.assertEqual(response.status_code, 400, (value, response.json))
            self.assertEqual(self.transport.uri, original)
        self.assertNotIn('SetAVTransportURI', self.transport.calls)

    def test_radio_and_stale_requests_are_rejected(self):
        self.transport.uri = 'http://radio.example/live'
        self.assertEqual(self.seek(4).status_code, 400)
        self.assertFalse(app.sony_status()['seekable'])
        self.assertEqual(self.client.post('/api/seek', json={'seconds': 4, 'uri': 'old'}).status_code, 400)
        self.assertNotIn('SetAVTransportURI', self.transport.calls)

    def test_bad_cache_ids_do_not_expose_files(self):
        for token in ['..', 'bad', 'a' * 32]:
            self.assertEqual(self.client.get('/seek-media/' + token + '.mp3').status_code, 404)

    def test_lossless_wav_and_flac_keep_exact_samples(self):
        for suffix, codec, rate, bits in [('wav', 'pcm_s16le', 44100, 16),
                                          ('flac', 'flac', 96000, 24)]:
            with self.subTest(format=suffix):
                source = self.root / ('lossless.' + suffix)
                command = [self.cache.ffmpeg(), '-nostdin', '-loglevel', 'error',
                           '-f', 'lavfi', '-i', f'sine=frequency=660:duration=2:sample_rate={rate}',
                           '-ac', '2', '-c:a', codec]
                if suffix == 'flac':
                    command += ['-sample_fmt', 's32']
                subprocess.run(command + ['-y', str(source)], check=True)
                token, record = self.cache.create(source, source.name, 1, 2)
                output = self.cache.directory / (token + '.wav')
                self.assertEqual(record['extension'], 'wav')
                self.assertEqual(record['pcm']['rate'], rate)
                self.assertEqual(record['pcm']['bits'], bits)
                self.assertEqual(record['pcm']['channels'], 2)
                self.assertAlmostEqual(self.cache.duration(output), 1, delta=.01)
                def samples(path):
                    return subprocess.run([self.cache.ffmpeg(), '-nostdin', '-loglevel', 'error',
                                           '-i', str(path), '-f', 's32le', '-c:a', 'pcm_s32le', '-'],
                                          capture_output=True, check=True).stdout
                # Compare every decoded sample against the original after exactly 1 second.
                self.assertEqual(samples(output), samples(source)[rate * 2 * 4:])
                self.transport.uri = app.music_url(source.name)
                response = self.seek(1)
                self.assertEqual(response.status_code, 200, response.json)
                route = response.json['uri'].split(f':{app.WEB_PORT}', 1)[1]
                self.assertTrue(route.endswith('.wav'))
                with self.client.get(route, headers={'Range': 'bytes=0-31'}) as media:
                    self.assertEqual(media.status_code, 206)
                    self.assertEqual(media.mimetype, 'audio/wav')
                    self.assertNotIn('DLNA.ORG_PN=MP3', media.headers['contentFeatures.dlna.org'])
                self.assertTrue(app.sony_status()['seekable'])
                self.assertEqual(self.client.get(route[:-4] + '.mp3').status_code, 404)

    def test_aac_seek_keeps_compressed_packets(self):
        source = self.root / 'aac.m4a'
        subprocess.run([self.cache.ffmpeg(), '-nostdin', '-loglevel', 'error',
                        '-f', 'lavfi', '-i', 'sine=frequency=700:duration=8:sample_rate=48000',
                        '-ac', '2', '-c:a', 'aac', '-y', str(source)], check=True)
        self.transport.uri = app.music_url(source.name)
        app.set_transport_file(source.name)
        self.assertTrue(self.transport.uri.endswith('.aac'))
        response = self.seek(3)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['uri'].endswith('.aac'))
        token = Path(response.json['uri']).stem
        record = self.cache.record(token)
        self.assertEqual(record['extension'], 'aac')
        self.assertNotIn('pcm', record)
        def packets(path):
            return subprocess.run([self.cache.ffmpeg(), '-nostdin', '-v', 'error',
                                   '-i', str(path), '-map', '0:a:0', '-c:a', 'copy', '-f', 'adts', '-'],
                                  capture_output=True, check=True).stdout
        self.assertIn(packets(self.cache.directory / (token + '.aac')), packets(source))
        route = response.json['uri'].split(f':{app.WEB_PORT}', 1)[1]
        with self.client.get(route) as media:
            self.assertEqual(media.mimetype, 'audio/vnd.dlna.adts')

    def test_alac_start_converts_to_pcm_48khz_without_mp3(self):
        source = self.root / 'alac.m4a'
        subprocess.run([self.cache.ffmpeg(), '-nostdin', '-v', 'error',
                        '-f', 'lavfi', '-i', 'sine=frequency=700:duration=2:sample_rate=96000',
                        '-ac', '2', '-c:a', 'alac', '-sample_fmt', 's16p', '-y', str(source)], check=True)
        app.set_transport_file(source.name)
        self.assertTrue(self.transport.uri.endswith('.wav'))
        record = self.cache.record(Path(self.transport.uri).stem)
        self.assertEqual(record['pcm']['source_rate'], 96000)
        self.assertEqual(record['pcm']['rate'], 48000)
        self.assertEqual(record['pcm']['bits'], 16)
        self.assertEqual(self.transport.state, 'PLAYING')

    def test_legacy_mp3_records_still_work(self):
        import json
        token, _ = self.cache.create(self.root / 'test.mp3', 'test.mp3', 5, 20)
        file = self.cache.directory / (token + '.json')
        record = json.loads(file.read_text())
        del record['extension']
        file.write_text(json.dumps(record))
        self.assertEqual(self.cache.record(token)['extension'], 'mp3')


if __name__ == '__main__':
    unittest.main()
