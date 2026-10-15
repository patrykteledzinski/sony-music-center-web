"""Pause and DLNA regressions, without contacting the device."""
from pathlib import Path
import html
import mimetypes
import re
import threading
import unittest
from unittest.mock import Mock
import xml.etree.ElementTree as ET
from test_regressions import functions


class PauseTests(unittest.TestCase):
    def test_local_mp3_metadata_has_dlna_profile(self):
        ns = functions('dlna_features', 'didl_metadata', 'clean_title',
                       Path=Path, html=html, mimetypes=mimetypes, re=re)
        xml = ns['didl_metadata'](Path("Artist's track.mp3"),
                                  'http://server/media/track.mp3')
        resource = ET.fromstring(xml).find('{urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/}item/{urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/}res')
        protocol = resource.attrib['protocolInfo']
        self.assertIn('DLNA.ORG_PN=MP3;', protocol)
        self.assertIn('DLNA.ORG_OP=01;', protocol)
        self.assertIn('DLNA.ORG_CI=0;', protocol)
        self.assertNotIn('DLNA.ORG_PN=MP3', ns['dlna_features']('audio/flac'))

    def pause(self, states):
        return functions('sony_pause', play_lock=threading.RLock(),
                         sony_transport=Mock(side_effect=['ok'] + states),
                         xml_value=lambda value, _: value,
                         time=Mock())

    def test_pause_waits_for_confirmation(self):
        ns = self.pause(['PLAYING', 'PAUSED_PLAYBACK'])
        ns['sony_pause']()
        self.assertEqual(ns['sony_transport'].call_count, 3)
        ns['time'].sleep.assert_called_once_with(0.2)

    def test_ignored_pause_is_an_error(self):
        ns = self.pause(['PLAYING'] * 6)
        with self.assertRaisesRegex(RuntimeError, 'nie potwierdziło pauzy'):
            ns['sony_pause']()


if __name__ == '__main__':
    unittest.main()


class LegacyMetadataTests(unittest.TestCase):
    def refresh(self, state='STOPPED', uri='http://192.0.2.50:8088/media/Artist%27s%20track.mp3', metadata='old'):
        from urllib.parse import urlparse
        import urllib.parse
        return functions('refresh_stopped_file_metadata',
                         sony_transport=Mock(side_effect=[state, {'TrackURI': uri, 'TrackMetaData': metadata}]),
                         xml_value=lambda value, name: value.get(name, '') if isinstance(value, dict) else value,
                         urlparse=urlparse, urllib=__import__('urllib'),
                         SERVER_IP='192.0.2.50', WEB_PORT=8088,
                         set_transport_file=Mock())

    def test_old_stopped_local_file_is_reloaded_without_autoplay(self):
        ns = self.refresh()
        ns['refresh_stopped_file_metadata']()
        ns['set_transport_file'].assert_called_once_with("Artist's track.mp3", autoplay=False)

    def test_paused_file_is_never_reloaded(self):
        ns = self.refresh(state='PAUSED_PLAYBACK')
        ns['refresh_stopped_file_metadata']()
        ns['set_transport_file'].assert_not_called()
        self.assertEqual(ns['sony_transport'].call_count, 1)

    def test_radio_and_correct_metadata_are_not_reloaded(self):
        for kwargs in [{'uri': 'http://radio.example/live'}, {'metadata': 'DLNA.ORG_OP=01;DLNA.ORG_FLAGS=0170'}]:
            ns = self.refresh(**kwargs)
            ns['refresh_stopped_file_metadata']()
            ns['set_transport_file'].assert_not_called()
