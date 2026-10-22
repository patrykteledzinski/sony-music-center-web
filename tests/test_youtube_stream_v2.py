import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch
from sony_youtube_stream import StreamRegistry, StaleStream, YouTubeStreamService, BYTE_RATE
from tests.test_youtube_standalone import service

SESSION='a'*24

class RegistryTests(unittest.TestCase):
    def test_latest_revision_cancels_only_older_revision_of_same_session(self):
        registry=StreamRegistry();one=registry.reserve(SESSION,1,'abcdefghijk',0,Mock())
        other=registry.reserve('b'*24,1,'abcdefghijk',0,Mock());two=registry.reserve(SESSION,2,'abcdefghijk',5000,Mock())
        self.assertTrue(one.cancelled.is_set());self.assertFalse(other.cancelled.is_set());self.assertFalse(two.cancelled.is_set())
        with self.assertRaises(StaleStream):registry.reserve(SESSION,1,'abcdefghijk',0,Mock())

    def test_same_revision_ranges_can_coexist(self):
        registry=StreamRegistry();a=registry.reserve(SESSION,2,'abcdefghijk',5000,Mock());b=registry.reserve(SESSION,2,'abcdefghijk',5000,Mock())
        self.assertFalse(a.cancelled.is_set());self.assertFalse(b.cancelled.is_set())
        with self.assertRaises(StaleStream):registry.reserve(SESSION,2,'abcdefghijk',9000,Mock())

    def test_cleanup_of_old_request_does_not_remove_new_request(self):
        registry=StreamRegistry();a=registry.reserve(SESSION,1,'abcdefghijk',0,Mock());b=registry.reserve(SESSION,2,'abcdefghijk',5000,Mock())
        registry.remove(a);self.assertEqual(registry.active,{b})

    def test_cancel_rejects_delayed_old_requests_but_allows_future_resume(self):
        registry=StreamRegistry();old=registry.reserve(SESSION,4,'abcdefghijk',0,Mock());registry.cancel(SESSION)
        self.assertTrue(old.cancelled.is_set())
        with self.assertRaises(StaleStream):registry.reserve(SESSION,4,'abcdefghijk',0,Mock())
        future=registry.reserve(SESSION,6,'abcdefghijk',9000,Mock());self.assertFalse(future.cancelled.is_set())


class StreamHTTPTests(unittest.TestCase):
    def setUp(self):
        self.resolver=Mock();self.resolver.resolve.return_value={'videoDetails':{'lengthSeconds':'120','title':'Test'},'formats':[{'url':'https://signed.example/private'}]}
        self.streams=YouTubeStreamService(self.resolver)
        self.patch=patch.object(service,'v2_streams',self.streams);self.patch.start()
        self.server=service.ThreadingHTTPServer(('127.0.0.1',0),service.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.02},daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.patch.stop()

    def request(self,path,method='GET',headers=None):
        try:r=urllib.request.urlopen(urllib.request.Request(self.base+path,method=method,headers=headers or {}))
        except urllib.error.HTTPError as e:r=e
        with r:return r.status,dict(r.headers),r.read()

    def test_metadata_never_discloses_signed_source(self):
        status,_,body=self.request('/v2/info/abcdefghijk');self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['durationMs'],120000);self.assertNotIn(b'signed.example',body)

    def test_live_content_is_not_advertised_as_seekable(self):
        self.resolver.resolve.return_value['videoDetails']['isLiveContent']=True
        status,_,body=self.request('/v2/info/abcdefghijk');self.assertFalse(json.loads(body)['seekable'])

    def test_head_reports_remaining_duration_without_decoder_or_cancellation(self):
        old=self.streams.registry.reserve(SESSION,1,'abcdefghijk',0,Mock())
        with patch('sony_youtube_stream.subprocess.Popen') as popen:
            status,headers,body=self.request('/v2/audio/'+SESSION+'/2/abcdefghijk/90500.wav','HEAD')
        self.assertEqual(status,200);self.assertEqual(body,b'');self.assertEqual(int(headers['Content-Length']),44+29500*BYTE_RATE//1000)
        popen.assert_not_called();self.assertFalse(old.cancelled.is_set())

    def test_invalid_range_and_past_end_are_rejected(self):
        path='/v2/audio/'+SESSION+'/1/abcdefghijk/0.wav'
        status,headers,_=self.request(path,'HEAD',{'Range':'bytes=9999999999-'})
        self.assertEqual(status,416);self.assertIn('Content-Range',headers)
        self.assertEqual(self.request('/v2/audio/'+SESSION+'/1/abcdefghijk/120000.wav','HEAD')[0],422)

    def test_pcm_is_obtained_before_success_and_seek_offset_is_passed_to_decoder(self):
        real_popen=subprocess.Popen;commands=[]
        def decoder(command,**kwargs):
            commands.append(command)
            return real_popen([sys.executable,'-c','import os;os.write(1,b"\\x12\\x34\\x56\\x78"*4096)'],**kwargs)
        with patch('sony_youtube_stream.subprocess.Popen',side_effect=decoder):
            status,headers,body=self.request('/v2/audio/'+SESSION+'/1/abcdefghijk/90500.wav',headers={'Range':'bytes=0-1023'})
        self.assertEqual(status,206);self.assertEqual(len(body),1024);self.assertEqual(body[:4],b'RIFF');self.assertEqual(body[44:48],b'\x12\x34\x56\x78')
        self.assertEqual(commands[0][commands[0].index('-ss')+1],'90.500000000')
        for _ in range(100):
            if self.streams.registry.stats()['active']==0:break
            time.sleep(.01)
        self.assertEqual(self.streams.registry.stats()['active'],0)
        self.assertTrue(self.streams.slots.acquire(blocking=False));self.streams.slots.release()

    def test_failed_decoder_does_not_return_successful_audio_headers(self):
        with patch('sony_youtube_stream.subprocess.Popen',side_effect=OSError('missing')):
            status,_,body=self.request('/v2/audio/'+SESSION+'/1/abcdefghijk/0.wav')
        self.assertEqual(status,503);self.assertIn(b'error',body)

    def test_session_and_video_validation_precede_resolver(self):
        self.assertEqual(self.request('/v2/audio/not-a-session/1/abcdefghijk/0.wav')[0],404)
        self.resolver.resolve.assert_not_called()
