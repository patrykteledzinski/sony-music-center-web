from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import app


class TrackQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'album').mkdir()
        for name in ['01.m4a', '02.m4a', '03.m4a']:
            (self.root / 'album' / name).touch()
        self.state = dict(items=['album/02.m4a'],index=0,source='Pojedynczy utwór',current_removed=False)
        self.played = []
        for patcher in [patch.object(app, 'MUSIC_ROOT', self.root),
                        patch.object(app, 'queue_state', self.state),
                        patch.object(app, 'save_queue'),
                        patch.object(app, 'set_transport_file', side_effect=lambda path: self.played.append(path) or path)]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = app.app.test_client()

    def test_single_track_click_builds_folder_queue_at_clicked_position(self):
        response = self.client.post('/api/play-track', json={'path':'album/02.m4a'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.state['items'], ['album/01.m4a','album/02.m4a','album/03.m4a'])
        self.assertEqual(self.state['index'], 1)
        self.assertEqual(response.json['count'], 3)
        self.assertEqual(self.played, ['album/02.m4a'])

    def test_existing_custom_order_and_source_are_preserved(self):
        self.state.update(items=['album/03.m4a','album/02.m4a','album/01.m4a'],source='Playlista: moja')
        self.client.post('/api/play-track', json={'path':'album/02.m4a'})
        self.assertEqual(self.state['items'], ['album/03.m4a','album/02.m4a','album/01.m4a'])
        self.assertEqual(self.state['source'], 'Playlista: moja')
        self.assertEqual(self.state['index'], 1)

    def test_explicit_single_song_playlist_is_preserved(self):
        self.state['source'] = 'Playlista: jeden utwór'
        self.client.post('/api/play-track', json={'path':'album/02.m4a'})
        self.assertEqual(self.state['items'], ['album/02.m4a'])

    def test_click_then_natural_end_advances_to_next_folder_track(self):
        from sony_queue import QueueFollower
        self.client.post('/api/play-track', json={'path':'album/02.m4a'})
        follower = QueueFollower()
        status=dict(online=True,state='PLAYING',source='album/02.m4a',uri='source',position=343,
                    duration=345.86,end_tolerance=3)
        follower.observe(status,self.state['items'],self.state['index'],now=0)
        next_index=follower.observe({**status,'state':'STOPPED','position':0},
                                    self.state['items'],self.state['index'],now=1)
        self.assertEqual(next_index, 2)
        app.play_queue_index(next_index)
        self.assertEqual(self.played, ['album/02.m4a','album/03.m4a'])
