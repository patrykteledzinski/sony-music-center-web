import unittest
from unittest.mock import patch
from sony_youtube import YouTubeResolver


class YouTubeResolverTests(unittest.TestCase):
    def test_only_compatible_audio_is_returned(self):
        info=dict(id='abcdefghijk',title='Title',duration=120,formats=[
            dict(format_id='140',vcodec='none',acodec='mp4a.40.2',url='https://example/audio',abr=128),
            dict(format_id='139',vcodec='none',acodec='mp4a.40.5',url='https://example/low',abr=48),
            dict(format_id='251',vcodec='none',acodec='opus',url='https://example/opus'),
            dict(format_id='18',vcodec='avc1',acodec='mp4a.40.2',url='https://example/video')])
        result=YouTubeResolver.convert(info)
        self.assertEqual(len(result['formats']),2)
        self.assertEqual(result['formats'][0]['itag'],140)
        self.assertTrue(result['formats'][0]['hasAudio'])
        self.assertFalse(result['formats'][0]['hasVideo'])
        self.assertEqual(result['videoDetails']['lengthSeconds'],'120')

    def test_audio_variant_ids_keep_the_numeric_base_itag(self):
        result=YouTubeResolver.convert(dict(id='abcdefghijk',formats=[
            dict(format_id='139-drc',vcodec='none',acodec='mp4a.40.5',url='https://example/low',abr=48),
            dict(format_id='140-drc',vcodec='none',acodec='mp4a.40.2',url='https://example/high',abr=128),
            dict(format_id='140-0',vcodec='none',acodec='mp4a.40.2',url='https://example/alternate',abr=128),
            dict(format_id='unknown',vcodec='none',acodec='mp4a.40.2',url='https://example/invalid')]))
        self.assertEqual([x['itag'] for x in result['formats']], [140,140,139])

    def test_arbitrary_urls_and_shell_input_are_rejected_before_execution(self):
        resolver=YouTubeResolver('/tmp')
        for value in ['http://127.0.0.1','$(id)','abc','a'*12]:
            with patch('sony_youtube.subprocess.run') as run:
                with self.assertRaises(ValueError):resolver.resolve(value)
                run.assert_not_called()

    def test_waiting_request_reuses_metadata_completed_by_another_request(self):
        import time
        from unittest.mock import Mock
        resolver=YouTubeResolver('/tmp/no-tools-needed')
        converted={'videoDetails':{'videoId':'abcdefghijk'},'formats':[]}
        slots=Mock()
        def acquire(**kwargs):
            self.assertEqual(kwargs,{'timeout':25})
            resolver.cache['abcdefghijk']=(time.monotonic(),converted)
            return True
        slots.acquire.side_effect=acquire
        resolver.slots=slots
        with patch('sony_youtube.subprocess.run') as run:
            self.assertIs(resolver.resolve('abcdefghijk'),converted)
            run.assert_not_called()
        slots.release.assert_called_once_with()

    def test_cancelled_resolution_terminates_its_process(self):
        import os,sys,time,subprocess
        from sony_youtube import CancelledResolution
        real_popen=subprocess.Popen
        children=[]
        def start(*args,**kwargs):
            child=real_popen(*args,**kwargs);children.append(child);return child
        started=time.monotonic()
        with patch('sony_youtube.subprocess.Popen',side_effect=start):
            with self.assertRaises(CancelledResolution):
                YouTubeResolver._cancellable_run([sys.executable,'-c','import time;time.sleep(30)'],
                    dict(os.environ),lambda:time.monotonic()-started>.15,timeout=5)
        self.assertIsNotNone(children[0].poll())
        self.assertLess(time.monotonic()-started,3)

    def test_absent_audio_is_reported(self):
        with self.assertRaises(ValueError):YouTubeResolver.convert(dict(id='abcdefghijk',formats=[]))

class YouTubeStreamTests(unittest.TestCase):
    def setUp(self):
        import app
        self.app=app
        self.client=app.app.test_client()

    def test_head_never_starts_network_or_decoder(self):
        with patch.object(self.app.youtube_resolver,'resolve') as resolve, \
             patch.object(self.app.subprocess,'Popen') as popen:
            response=self.client.head('/youtube-audio/abcdefghijk.mp3')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.mimetype,'audio/mpeg')
        resolve.assert_not_called();popen.assert_not_called()

    def test_invalid_stream_id_is_rejected(self):
        response=self.client.get('/youtube-audio/not-an-id.mp3')
        self.assertEqual(response.status_code,400)

    def test_stream_limit_does_not_create_more_decoders(self):
        import threading
        slots=threading.BoundedSemaphore(1);slots.acquire()
        with patch.object(self.app,'youtube_stream_slots',slots), \
             patch.object(self.app.subprocess,'Popen') as popen:
            response=self.client.get('/youtube-audio/abcdefghijk.mp3')
        self.assertEqual(response.status_code,429);popen.assert_not_called()
        slots.release()

    def test_decoder_start_failure_closes_source_and_releases_slot(self):
        import io,threading
        source=io.BytesIO(b'audio')
        slots=threading.BoundedSemaphore(1)
        with patch.object(self.app,'youtube_stream_slots',slots), \
             patch.object(self.app.youtube_resolver,'resolve',return_value={'formats':[{'url':'https://example/audio'}]}), \
             patch.object(self.app.urllib.request,'urlopen',return_value=source), \
             patch.object(self.app.seek_cache,'ffmpeg',return_value='/ffmpeg'), \
             patch.object(self.app.subprocess,'Popen',side_effect=OSError('unavailable')):
            response=self.client.get('/youtube-audio/abcdefghijk.mp3')
        self.assertEqual(response.status_code,502)
        self.assertTrue(source.closed)
        self.assertTrue(slots.acquire(blocking=False));slots.release()
