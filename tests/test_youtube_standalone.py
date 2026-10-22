import importlib.util
import io
import json
from pathlib import Path
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('youtube_standalone',
    Path(__file__).resolve().parents[1] / 'integrations/youtube2-standalone/service.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        self.server = service.ThreadingHTTPServer(('127.0.0.1', 0), service.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
            kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, method='GET'):
        try:
            response = urllib.request.urlopen(urllib.request.Request(self.url + path, method=method))
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.read()

    def test_only_video_ids_are_accepted(self):
        with patch.object(service.resolver, 'resolve') as resolve:
            for path in ['/api/youtube/info/short', '/audio/short.mp3',
                         '/api/youtube/info/https://example.com']:
                self.assertEqual(self.request(path)[0], 404)
            resolve.assert_not_called()

    def test_metadata_uses_loopback_audio_without_exposing_signed_url_or_mutating_cache(self):
        cached = {'videoDetails': {'title': 'Music'}, 'formats': [{'url': 'https://signed.example/audio'}]}
        with patch.object(service.resolver, 'resolve', return_value=cached):
            status, body = self.request('/api/youtube/info/abcdefghijk')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['formats'][0]['url'], 'http://127.0.0.1:18888/audio/abcdefghijk.mp3')
        self.assertNotIn(b'signed.example', body)
        self.assertEqual(cached['formats'][0]['url'], 'https://signed.example/audio')

    def test_recorded_metadata_returns_seekable_wav(self):
        cached = {'videoDetails': {'lengthSeconds': '120'}, 'formats': [{'url': 'https://signed.example/audio'}]}
        with patch.object(service.resolver, 'resolve', return_value=cached):
            status, body = self.request('/api/youtube/info/abcdefghijk')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['formats'][0]['url'], 'http://127.0.0.1:18888/audio/abcdefghijk.wav')

    def test_wav_head_range_reports_exact_size_without_decoder(self):
        cached = {'videoDetails': {'lengthSeconds': '120'}, 'formats': [{'url': 'https://signed.example/audio'}]}
        with patch.object(service.resolver, 'resolve', return_value=cached), patch.object(service.subprocess, 'Popen') as popen:
            request=urllib.request.Request(self.url+'/audio/abcdefghijk.wav', method='HEAD', headers={'Range':'bytes=44-47'})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status,206)
                self.assertEqual(response.headers['Content-Length'],'4')
                self.assertEqual(response.headers['Content-Range'],'bytes 44-47/'+str(44+120*service.BYTE_RATE))
                self.assertEqual(response.read(),b'')
            popen.assert_not_called()

    def test_offset_stream_reports_remaining_duration(self):
        cached={'videoDetails':{'lengthSeconds':'120'},'formats':[{'url':'https://signed.example/audio'}]}
        with patch.object(service.resolver,'resolve',return_value=cached), patch.object(service.subprocess,'Popen') as popen:
            request=urllib.request.Request(self.url+'/audio/abcdefghijk/90.wav',method='HEAD')
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status,200)
                self.assertEqual(int(response.headers['Content-Length']),44+30*service.BYTE_RATE)
            popen.assert_not_called()

    def test_head_does_not_resolve_or_transcode(self):
        with patch.object(service.resolver, 'resolve') as resolve, patch.object(service.subprocess, 'Popen') as popen:
            self.assertEqual(self.request('/audio/abcdefghijk.mp3', 'HEAD'), (200, b''))
        resolve.assert_not_called()
        popen.assert_not_called()

    def test_decoder_failure_closes_source_and_releases_capacity(self):
        source = io.BytesIO(b'audio')
        released = threading.Event()
        class Slots(threading.BoundedSemaphore):
            def release(self, *args):
                super().release(*args)
                released.set()
        slots = Slots(1)
        with patch.object(service, 'streams', slots), \
             patch.object(service.resolver, 'resolve', return_value={'formats': [{'url': 'https://source'}]}), \
             patch.object(service.urllib.request, 'urlopen', return_value=source), \
             patch.object(service.subprocess, 'Popen', side_effect=OSError('missing')):
            # Invoke the handler over a real socket; only mock its upstream fetch.
            import http.client
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
            connection.request('GET', '/audio/abcdefghijk.mp3')
            response = connection.getresponse()
            self.assertEqual(response.status, 502)
            response.read()
            connection.close()
            self.assertTrue(released.wait(2))
        self.assertTrue(source.closed)
        self.assertTrue(slots.acquire(blocking=False))
        slots.release()

class WavRangeTests(unittest.TestCase):
    def test_header_has_correct_pcm_length_and_format(self):
        import wave
        header = service.wav_header(service.BYTE_RATE * 60)
        self.assertEqual(len(header),44)
        with wave.open(io.BytesIO(header),'rb') as wav:
            self.assertEqual(wav.getnchannels(),2)
            self.assertEqual(wav.getframerate(),44100)
            self.assertEqual(wav.getsampwidth(),2)
            self.assertEqual(wav.getnframes(),60*44100)

    def test_open_closed_and_suffix_ranges(self):
        for value, expected in [(None,(0,99)),('bytes=0-43',(0,43)),
                                ('bytes=44-',(44,99)),('bytes=90-999',(90,99)),
                                ('bytes=-10',(90,99)),('bytes=-200',(0,99))]:
            self.assertEqual(service.byte_range(value,100),expected)

    def test_invalid_and_multiple_ranges_are_rejected(self):
        for value in ['bytes=-','bytes=-0','bytes=100-','bytes=10-9','bytes=0-1,4-5','bytes=a-b']:
            with self.assertRaises(ValueError):service.byte_range(value,100)

class StreamReplacementTests(unittest.TestCase):
    def test_new_seek_stops_only_the_previous_stream_for_same_video(self):
        from unittest.mock import Mock
        handler,process=Mock(),Mock()
        other_handler,other_process=Mock(),Mock()
        active={'abcdefghijk':(handler,process),'other_video':(other_handler,other_process)}
        with patch.object(service,'active_wav',active):
            service.cancel_previous('abcdefghijk')
        process.terminate.assert_called_once_with()
        handler.connection.shutdown.assert_called_once_with(service.socket.SHUT_RDWR)
        other_process.terminate.assert_not_called()
        self.assertNotIn('abcdefghijk',active)
        self.assertIn('other_video',active)
