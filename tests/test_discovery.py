import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

from sony_discovery import SonyEndpoint


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.config = root / 'config.json'
        self.config.write_text('{"sony_ip":"192.168.8.100","web_port":8088}')
        self.endpoint = SonyEndpoint('http://192.168.8.100:8080', root / 'device.json',
                                     self.config, Mock())
        self.identity = dict(udn='uuid:sony-one', model='CMT-MX700Ni/MX750Ni')
        self.endpoint.identity = self.identity

    def request(self, action):
        return urllib.request.Request(self.endpoint.base + '/AVTransport/ctrl', data=b'soap',
                                      headers={'SOAPAction': f'"urn:test#{action}"'})

    def test_read_recovers_and_persists_without_losing_settings(self):
        result = Mock()
        with patch.dict('os.environ', {}, clear=True), \
             patch('sony_discovery.discover_locations', return_value=['http://192.168.8.102:8080/description.xml']), \
             patch('sony_discovery.describe', return_value=self.identity), \
             patch('sony_discovery.urllib.request.urlopen', side_effect=[urllib.error.URLError('offline'), result]) as open_:
            self.assertIs(self.endpoint.open(self.request('GetTransportInfo')), result)
            self.assertEqual(open_.call_count, 2)
            self.assertEqual(open_.call_args.args[0].full_url, 'http://192.168.8.102:8080/AVTransport/ctrl')
        self.assertEqual(json.loads(self.config.read_text()),
                         dict(sony_ip='192.168.8.102', sony_port=8080, web_port=8088))

    def test_other_device_is_rejected_and_search_is_throttled(self):
        with patch('sony_discovery.discover_locations', return_value=['http://192.168.8.103:8080/description.xml']) as discover, \
             patch('sony_discovery.describe', return_value=dict(self.identity, udn='uuid:other')):
            self.assertFalse(self.endpoint.recover(self.endpoint.base))
            self.assertFalse(self.endpoint.recover(self.endpoint.base))
            discover.assert_called_once()
        self.assertEqual(self.endpoint.base, 'http://192.168.8.100:8080')

    def test_mutation_recovers_but_is_not_replayed(self):
        def recover(base):
            self.endpoint.base = 'http://192.168.8.102:8080'
            return True
        with patch.object(self.endpoint, 'recover', side_effect=recover), \
             patch('sony_discovery.urllib.request.urlopen', side_effect=urllib.error.URLError('timeout')) as open_:
            with self.assertRaises(urllib.error.URLError):
                self.endpoint.open(self.request('Next'))
            open_.assert_called_once()

    def test_http_fault_does_not_trigger_discovery(self):
        with patch.object(self.endpoint, 'recover') as recover, \
             patch('sony_discovery.urllib.request.urlopen', side_effect=urllib.error.HTTPError('url', 500, 'fault', {}, None)):
            with self.assertRaises(urllib.error.HTTPError):
                self.endpoint.open(self.request('GetTransportInfo'))
            recover.assert_not_called()

    def test_ambiguous_unpinned_devices_are_rejected(self):
        self.endpoint.identity = None
        with patch('sony_discovery.discover_locations', return_value=[
                'http://192.168.8.102:8080/description.xml', 'http://192.168.8.103:8080/description.xml']), \
             patch('sony_discovery.describe', return_value=self.identity):
            self.assertFalse(self.endpoint.recover(self.endpoint.base))

    def test_party_uses_current_endpoint(self):
        from sony_party import PartyClient
        party = PartyClient(self.endpoint.base, opener=self.endpoint.open)
        self.endpoint.base = 'http://192.168.8.102:8080'
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><u:X_GetStateResponse xmlns:u="urn:schemas-sony-com:service:Party:1"><PartyState>IDLE</PartyState></u:X_GetStateResponse></s:Body></s:Envelope>'
        with patch('sony_discovery.urllib.request.urlopen', return_value=response) as open_:
            self.assertEqual(party.call('X_GetState')['PartyState'], 'IDLE')
            self.assertEqual(open_.call_args.args[0].full_url, 'http://192.168.8.102:8080/Party_Control')
