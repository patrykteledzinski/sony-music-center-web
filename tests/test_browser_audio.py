"""Browser audio endpoints use temporary files and never control the Sony."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import app
from sony_seek import SeekCache


class BrowserAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.cache = SeekCache(cls.root / 'browser', app.APP_DIR)
        for name, codec in [('test.mp3', 'libmp3lame'), ('test.m4a', 'aac'), ('lossless.m4a', 'alac')]:
            subprocess.run([cls.cache.ffmpeg(), '-nostdin', '-v', 'error',
                            '-f', 'lavfi', '-i', 'sine=frequency=600:duration=3:sample_rate=48000',
                            '-c:a', codec, str(cls.root / name)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.sony_cache = SeekCache(self.root / 'sony', app.APP_DIR)
        for p in [patch.object(app, 'MUSIC_ROOT', self.root),
                  patch.object(app, 'browser_cache', self.cache),
                  patch.object(app, 'seek_cache', self.sony_cache)]:
            p.start()
            self.addCleanup(p.stop)
        self.sony = patch.object(app, 'sony_transport', side_effect=AssertionError('Browser must not control Sony'))
        self.transport = self.sony.start()
        self.addCleanup(self.sony.stop)
        self.client = app.app.test_client()

    def prepare(self, path):
        return self.client.post('/api/browser/audio', json={'uri': app.music_url(path)})

    def test_mp3_uses_original_with_no_conversion(self):
        response = self.prepare('test.mp3')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['url'], '/media/test.mp3')
        self.transport.assert_not_called()

    def test_aac_and_alac_are_browser_wav_with_range_support(self):
        for name in ['test.m4a', 'lossless.m4a']:
            response = self.prepare(name)
            self.assertEqual(response.status_code, 200, response.json)
            url = response.json['url']
            self.assertTrue(url.endswith('.wav'))
            self.assertEqual(response.json['source'], name)
            with self.client.get(url, headers={'Range': 'bytes=0-63'}) as audio:
                self.assertEqual(audio.status_code, 206)
                self.assertEqual(audio.mimetype, 'audio/wav')
                self.assertEqual(len(audio.data), 64)
                self.assertIn('bytes 0-63/', audio.headers['Content-Range'])
            self.assertEqual(self.prepare(name).json['url'], url)
        self.transport.assert_not_called()

    def test_sony_fragment_maps_to_full_original_audio(self):
        path = self.root / 'test.m4a'
        token, record = self.sony_cache.create(path, path.name, 1, 3)
        uri = f'http://{app.SERVER_IP}:{app.WEB_PORT}/seek-media/{token}.{record["extension"]}'
        response = self.client.post('/api/browser/audio', json={'uri': uri})
        self.assertEqual(response.status_code, 200, response.json)
        browser_token = Path(response.json['url']).stem
        prepared = self.cache.record(browser_token)
        self.assertEqual(prepared['source'], 'test.m4a')
        self.assertEqual(prepared['offset'], 0)
        self.assertAlmostEqual(prepared['duration'], 3, delta=.1)
        self.assertTrue((self.sony_cache.directory / (token + '.aac')).exists())
        self.transport.assert_not_called()

    def test_browser_wav_keeps_decoded_samples(self):
        response = self.prepare('test.m4a')
        token = Path(response.json['url']).stem
        def samples(path):
            return subprocess.run([self.cache.ffmpeg(), '-nostdin', '-v', 'error',
                                   '-i', str(path), '-f', 'f32le', '-c:a', 'pcm_f32le', '-'],
                                  capture_output=True, check=True).stdout
        self.assertEqual(samples(self.root / 'test.m4a'),
                         samples(self.cache.directory / (token + '.wav')))

    def test_external_urls_missing_files_and_bad_tokens_are_rejected(self):
        for uri in ['http://radio.example/live', 'file:///etc/passwd', app.music_url('missing.mp3')]:
            response = self.client.post('/api/browser/audio', json={'uri': uri})
            self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.client.get('/browser-media/' + 'a' * 32 + '.wav').status_code, 404)
        self.assertEqual(self.client.post('/api/browser/audio', json=[]).status_code, 400)
        self.transport.assert_not_called()
