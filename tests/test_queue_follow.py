import threading
import unittest
from unittest.mock import Mock, patch
from sony_queue import QueueFollower
import app


class QueueFollowTests(unittest.TestCase):
    def setUp(self):
        self.follow = QueueFollower()
        self.items = ['one.mp3', 'two.mp3']

    def observe(self, state, position=0, duration=10, now=0, index=0, mode='NORMAL', **kwargs):
        status = dict(online=True, state=state, source=self.items[index],
                      uri='http://source/' + self.items[index], position=position, duration=duration)
        status.update(kwargs)
        return self.follow.observe(status, self.items, index, mode=mode, now=now)

    def test_natural_end_advances_once(self):
        self.observe('PLAYING', position=9)
        self.assertEqual(self.observe('STOPPED', now=1), 1)
        self.assertIsNone(self.observe('STOPPED', now=2))

    def test_early_stop_and_decode_error_do_not_advance(self):
        self.observe('PLAYING', position=2)
        self.assertIsNone(self.observe('STOPPED', now=1))
        self.observe('PLAYING', position=9)
        self.assertIsNone(self.observe('STOPPED', now=1, error=True))

    def test_manual_stop_remains_suppressed_during_delayed_stop(self):
        self.observe('PLAYING', position=9)
        self.follow.suppress()
        self.observe('PLAYING', position=9, now=.1)
        self.assertIsNone(self.observe('STOPPED', now=1))
        self.observe('PLAYING', position=9, now=3)
        self.assertEqual(self.observe('STOPPED', now=4), 1)

    def test_pause_and_offline_do_not_count_as_played_time(self):
        self.observe('PLAYING', position=2)
        self.observe('PAUSED_PLAYBACK', position=2, now=1)
        self.assertIsNone(self.observe('STOPPED', now=100))
        self.observe('PLAYING', position=9)
        self.observe('UNKNOWN', online=False, now=1)
        self.assertIsNone(self.observe('STOPPED', now=2))

    def test_radio_changed_uri_and_restart_do_not_advance(self):
        self.assertIsNone(self.observe('STOPPED', now=1))
        self.observe('PLAYING', position=9)
        self.assertIsNone(self.observe('STOPPED', source=None, now=1))
        self.observe('PLAYING', position=9)
        self.assertIsNone(self.observe('STOPPED', uri='new-source', now=1))

    def test_last_track_stops_normally_and_repeat_modes_work(self):
        self.observe('PLAYING', position=9, index=1)
        self.assertIsNone(self.observe('STOPPED', now=1, index=1))
        self.observe('PLAYING', position=9, index=1)
        self.assertEqual(self.observe('STOPPED', now=1, index=1, mode='REPEAT_ALL'), 0)
        self.observe('PLAYING', position=9)
        self.assertEqual(self.observe('STOPPED', now=1, mode='REPEAT_ONE'), 0)

    def test_actual_aac_tail_clock_lags_at_end(self):
        # Captured on Sony: fragment starts at 337, ends after RelTime 6,
        # original duration is 345.86. Its counter lags behind actual audio.
        self.observe('PLAYING', position=343, duration=345.86, now=7.5, end_tolerance=3)
        self.assertEqual(self.observe('STOPPED', position=337, duration=345.86,
                                      now=8.1, end_tolerance=3), 1)

    def test_aac_manual_stop_and_early_stop_still_do_not_advance(self):
        self.observe('PLAYING', position=343, duration=345.86, end_tolerance=3)
        self.follow.suppress()
        self.assertIsNone(self.observe('STOPPED', now=1, end_tolerance=3))
        self.observe('PLAYING', position=300, duration=345.86, end_tolerance=3)
        self.assertIsNone(self.observe('STOPPED', now=1, end_tolerance=3))

    def test_short_end_transition_keeps_playing_observation(self):
        self.observe('PLAYING', position=9)
        self.observe('TRANSITIONING', now=.3)
        self.assertEqual(self.observe('STOPPED', now=1), 1)

    def test_monitor_advances_without_http_requests(self):
        state = dict(items=list(self.items), index=0, current_removed=False)
        responses = [
            '<CurrentTransportState>PLAYING</CurrentTransportState>',
            '<TrackURI>same-uri</TrackURI><RelTime>0:00:10</RelTime><TrackDuration>0:00:10</TrackDuration>',
            '<CurrentTransportState>STOPPED</CurrentTransportState>',
            '<TrackURI>same-uri</TrackURI><RelTime>0:00:00</RelTime><TrackDuration>0:00:10</TrackDuration>',
            '<PlayMode>NORMAL</PlayMode>',
        ]
        with patch.object(app, 'queue_state', state), patch.object(app, 'queue_follower', self.follow), \
             patch.object(app, 'sony_transport', side_effect=responses), \
             patch.object(app, 'local_playback_context', return_value={'source':'one.mp3','token':None}), \
             patch.object(app, 'play_queue_index') as play:
            app.queue_monitor_tick()
            app.queue_monitor_tick()
            play.assert_called_once_with(1)
