"""Asynchronous Sony transitions and useful SOAP error messages."""
import unittest
from unittest.mock import Mock, patch
import app


class SwitchTests(unittest.TestCase):
    def test_full_fault_is_decoded_beyond_old_truncation(self):
        details = ('<Envelope>' + ' ' * 400 + '<errorCode>701</errorCode>'
                   '<errorDescription>Transition not available</errorDescription></Envelope>')
        error = app.SonySoapError('AVTransport', 'Play', 500, details)
        self.assertEqual(error.upnp_code, '701')
        self.assertIn('Transition not available', str(error))
        self.assertIn('AVTransport/Play', str(error))
        self.assertNotIn('<Envelope>', str(error))

    def run_play(self, code, states):
        error = app.SonySoapError('AVTransport', 'Play', 500,
                                  f'<errorCode>{code}</errorCode>')
        play = patch.object(app, 'sony_play', side_effect=error)
        transport = patch.object(app, 'sony_transport', side_effect=states)
        return play, transport

    def state(self, name, status='OK'):
        return (f'<CurrentTransportState>{name}</CurrentTransportState>'
                f'<CurrentTransportStatus>{status}</CurrentTransportStatus>')

    def test_transition_to_autoplay_is_success(self):
        play, transport = self.run_play('701', [self.state('TRANSITIONING'), self.state('PLAYING')])
        with play, transport, patch.object(app.time, 'sleep'):
            app.sony_play_confirmed()

    def test_stopped_transient_retries_play_then_confirms(self):
        play, transport = self.run_play('701', [self.state('STOPPED')] * 4 + ['ok', self.state('PLAYING')])
        with play, transport as calls, patch.object(app.time, 'sleep'):
            app.sony_play_confirmed()
        self.assertIn(unittest.mock.call('Play', '<Speed>1</Speed>'), calls.call_args_list)

    def test_permanent_error_is_not_retried(self):
        play, transport = self.run_play('714', [self.state('STOPPED')])
        with play, transport as calls, patch.object(app.time, 'sleep') as sleep:
            with self.assertRaises(app.SonySoapError):
                app.sony_play_confirmed()
            sleep.assert_not_called()
        self.assertEqual(calls.call_count, 1)

    def test_device_error_is_not_reported_as_success(self):
        play, transport = self.run_play('701', [self.state('STOPPED', 'ERROR_OCCURRED')])
        with play, transport, patch.object(app.time, 'sleep'):
            with self.assertRaises(app.SonySoapError):
                app.sony_play_confirmed()

    def test_transition_wait_is_bounded(self):
        play, transport = self.run_play('701', [self.state('TRANSITIONING')] * 21)
        with play, transport as calls, patch.object(app.time, 'sleep'):
            with self.assertRaises(app.SonySoapError):
                app.sony_play_confirmed()
        self.assertEqual(calls.call_count, 21)
