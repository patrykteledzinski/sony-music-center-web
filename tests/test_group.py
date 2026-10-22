import unittest
from unittest.mock import Mock, patch
from sony_group import GroupPlayback


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.sony=dict(online=True,uri='http://server/media/song.mp3',state='PLAYING',
                       position=30,position_age=0,duration_seconds=100,title='Song')
        self.vol=dict(uri=self.sony['uri'],status='play',seek=30000)
        self.api=Mock();self.api.state.side_effect=lambda:dict(self.vol)
        self.group=GroupPlayback(lambda:dict(self.sony),lambda uri:dict(offset=0),self.api,Mock())

    def enable(self):
        self.group.set_enabled(True);self.api.reset_mock()

    def test_disabled_never_changes_audio(self):
        self.group.tick();self.api.state.assert_not_called();self.api.command.assert_not_called()

    def test_same_track_aligned_does_not_seek(self):
        self.enable();self.group.tick();self.api.command.assert_not_called()
        self.assertEqual(self.group.snapshot()['drift_seconds'],0)

    def test_drift_corrected_with_cooldown(self):
        self.vol['seek']=10000;self.enable()
        self.group.tick();self.group.tick()
        self.api.command.assert_called_once_with('seek',position=30)

    def test_changed_track_loads_exact_sony_uri(self):
        self.vol['uri']='other';self.enable();self.group.tick()
        self.assertEqual(self.api.call.call_args.args[0],'replaceAndPlay')
        self.assertEqual(self.api.call.call_args.args[1]['uri'],self.sony['uri'])

    def test_paused_leader_does_not_start_new_track_audibly(self):
        self.vol['uri']='other';self.sony['state']='PAUSED_PLAYBACK';self.enable();self.group.tick()
        self.api.call.assert_not_called();self.api.command.assert_called_once_with('pause')

    def test_pause_resume_and_stop_are_mirrored(self):
        self.enable();self.sony['state']='PAUSED_PLAYBACK';self.group.tick()
        self.api.command.assert_called_with('pause')
        self.sony['state']='PLAYING';self.vol['status']='pause';self.group.tick()
        self.api.call.assert_called_with('replaceAndPlay', unittest.mock.ANY)
        self.sony['state']='STOPPED';self.group.tick()
        self.api.command.assert_called_with('stop')

    def test_seek_segment_uses_relative_position(self):
        self.group.context=lambda uri:dict(offset=20)
        self.vol['seek']=10000;self.enable();self.group.tick()
        self.api.command.assert_not_called()
        self.assertEqual(self.group.snapshot()['drift_seconds'],0)

    def test_offline_leader_pauses_follower_and_reports_error(self):
        self.enable();self.sony['online']=False;self.group.tick()
        self.api.command.assert_called_with('pause')
        self.assertIn('Sony',self.group.snapshot()['error'])

    def test_invalid_or_offline_enable_does_not_change_mode(self):
        with self.assertRaises(ValueError):self.group.set_enabled('true')
        self.sony['online']=False
        with self.assertRaises(ValueError):self.group.set_enabled(True)
        self.assertFalse(self.group.snapshot()['enabled'])

    def test_external_source_is_not_claimed_as_synchronized(self):
        self.enable();self.group.context=lambda uri:None;self.group.tick()
        self.api.call.assert_not_called()
        self.assertIsNone(self.group.snapshot()['drift_seconds'])
        self.assertIn('radio',self.group.snapshot()['message'])

    def test_disable_keeps_devices_independent(self):
        self.enable();self.group.set_enabled(False);self.group.tick()
        self.api.command.assert_not_called()

    def test_http_files_use_volumio2_http_entrypoint(self):
        self.vol['uri']='other';self.enable();self.group.tick()
        item=self.api.call.call_args.args[1]
        self.assertEqual(item['service'],'webradio')
        self.assertEqual(item['type'],'song')
        self.assertEqual(item['duration'],100)

    def test_http_playlist_is_accepted_as_matching_source(self):
        self.group.playlist_url=lambda uri:'http://server/group.m3u'
        self.vol['uri']='http://server/group.m3u'
        self.enable();self.group.tick()
        self.api.call.assert_not_called()

    def test_loading_same_track_is_not_repeated_each_tick(self):
        self.vol['uri']='other';self.enable();self.group.tick();self.group.tick()
        self.api.call.assert_called_once()


class GroupRouteTests(unittest.TestCase):
    def setUp(self):
        import app
        self.app=app
        self.client=app.app.test_client()

    def test_foreign_origin_cannot_toggle_or_control(self):
        for path in ['/api/group','/api/group/control/play']:
            result=self.client.post(path,json={'enabled':True},headers={'Origin':'http://unrelated.example'})
            self.assertEqual(result.status_code,403)

    def test_volumio_origin_preflight_is_allowed(self):
        with patch.object(self.app.volumio_monitor,'snapshot',return_value={'url':'http://192.168.8.110:80'}):
            result=self.client.options('/api/group',headers={'Origin':'http://192.168.8.110'})
        self.assertEqual(result.status_code,204)
        self.assertEqual(result.headers['Access-Control-Allow-Origin'],'http://192.168.8.110')

    def test_readonly_state_can_be_shared(self):
        result=self.client.get('/api/group')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json['mode'],'approximate')
        self.assertEqual(result.headers['Access-Control-Allow-Origin'],'*')

    def test_mpd_gets_small_playlist_instead_of_audio_as_playlist_input(self):
        uri='http://server/media/long.mp3'
        url=self.app.group_playlist_url(uri)
        from urllib.parse import urlsplit
        response=self.client.get(urlsplit(url).path)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.text,'#EXTM3U\n'+uri+'\n')
        self.assertEqual(self.client.get('/group-playlist/unknown.m3u').status_code,404)

    def test_disabled_group_rejects_control(self):
        with patch.object(self.app.group_playback,'snapshot',return_value={'enabled':False}):
            result=self.client.post('/api/group/control/play',json={})
        self.assertEqual(result.status_code,409)

