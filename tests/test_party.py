import io
import unittest
import urllib.error
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

import app
from sony_party import PartyClient, PartyError, PartyConflict, NAMESPACE, SOAP


def state(name='IDLE', session=0):
    return dict(PartyState=name, PartyMode='IDLE' if name == 'IDLE' else 'PARTY',
                SessionID=str(session), NumberOfListeners='0')


class PartyTests(unittest.TestCase):
    def setUp(self):
        self.party = PartyClient('http://sony:8080')

    def test_start_and_stop_use_dynamic_session(self):
        self.party.call = Mock(side_effect=[state(), {}, state('SINGING', 987654),
                                           state('SINGING', 987654), {}, state()])
        self.assertEqual(self.party.start()['state'], 'SINGING')
        self.assertEqual(self.party.stop()['state'], 'IDLE')
        self.party.call.assert_any_call('X_Start', PartyMode='PARTY', ListenerList='')
        self.party.call.assert_any_call('X_Abort', SingerSessionID=987654)

    def test_repeated_requests_are_idempotent(self):
        self.party.call = Mock(return_value=state('SINGING', 123))
        self.party.start()
        self.party.call.assert_called_once_with('X_GetState')
        self.party.call = Mock(return_value=state())
        self.party.stop()
        self.party.call.assert_called_once_with('X_GetState')

    def test_listener_is_not_aborted_or_restarted(self):
        for operation in (self.party.start, self.party.stop):
            self.party.call = Mock(return_value=state('LISTENING', 123))
            with self.assertRaises(PartyConflict):
                operation()
            self.party.call.assert_called_once_with('X_GetState')

    def test_no_abort_with_invalid_session(self):
        self.party.call = Mock(return_value=state('SINGING', 0))
        with self.assertRaises(PartyConflict):
            self.party.stop()
        self.party.call.assert_called_once_with('X_GetState')

    def test_sony_endpoint_namespace_and_encoding(self):
        payload = (f'<s:Envelope xmlns:s="{SOAP}"><s:Body>'
                   f'<u:X_StartResponse xmlns:u="{NAMESPACE}">'
                   '<SingerSessionID>123</SingerSessionID>'
                   '</u:X_StartResponse></s:Body></s:Envelope>').encode()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = payload
        with patch('sony_party.urllib.request.urlopen', return_value=response) as open_:
            self.assertEqual(self.party.call('X_Start', PartyMode='PARTY', ListenerList='')['SingerSessionID'], '123')
        req = open_.call_args.args[0]
        self.assertEqual(req.full_url, 'http://sony:8080/Party_Control')
        self.assertEqual(req.get_header('Soapaction'), f'"{NAMESPACE}#X_Start"')
        self.assertEqual(req.get_header('Connection'), 'close')
        xml = ET.fromstring(req.data)
        self.assertEqual(xml.attrib[f'{{{SOAP}}}encodingStyle'], 'http://schemas.xmlsoap.org/soap/encoding/')
        self.assertIsNotNone(xml.find(f'.//{{{NAMESPACE}}}X_Start'))

    def test_fault_is_read_from_http_error(self):
        payload = b'<Envelope><Fault><errorCode>701</errorCode><errorDescription>Not available</errorDescription></Fault></Envelope>'
        error = urllib.error.HTTPError('http://sony', 500, 'error', {}, io.BytesIO(payload))
        with patch('sony_party.urllib.request.urlopen', side_effect=error):
            with self.assertRaisesRegex(PartyError, '701.*Not available'):
                self.party.status()

    def test_transient_start_state_is_polled(self):
        self.party.call = Mock(side_effect=[state(), {}, state('STARTING'), state('SINGING', 44)])
        with patch('sony_party.time.sleep'):
            self.assertEqual(self.party.start()['session_id'], 44)

    def test_retry_abort_refreshes_dynamic_session_id(self):
        error = PartyError('busy')
        error.upnp_code = '501'
        self.party.call = Mock(side_effect=[state('SINGING', 12), error,
                                           state('SINGING', 13), {}, state()])
        with patch('sony_party.time.sleep'):
            self.assertEqual(self.party.stop()['state'], 'IDLE')
        self.party.call.assert_any_call('X_Abort', SingerSessionID=12)
        self.party.call.assert_any_call('X_Abort', SingerSessionID=13)

    def test_api_status_start_stop_and_error_codes(self):
        client = app.app.test_client()
        fake = Mock()
        fake.status.return_value = dict(state='IDLE', listeners=0)
        fake.start.return_value = dict(state='SINGING', listeners=0)
        fake.stop.side_effect = PartyConflict('listener')
        with patch.object(app, 'party_client', fake):
            self.assertEqual(client.get('/api/party/status').json['state'], 'IDLE')
            self.assertEqual(client.post('/api/party/start').json['state'], 'SINGING')
            self.assertEqual(client.post('/api/party/stop').status_code, 409)
            fake.status.side_effect = PartyError('offline')
            self.assertEqual(client.get('/api/party/status').status_code, 502)
