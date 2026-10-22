"""The Volumio skin gets read-only Sony state without enabling cross-origin controls."""
import unittest
from unittest.mock import patch
import app


class StudioTests(unittest.TestCase):
    def test_cross_origin_preview_returns_real_status_without_caching(self):
        status = dict(online=True, state='PLAYING', title='Track', volume=24,
                      position=10, duration_seconds=100)
        with patch.object(app, 'sony_status', return_value=status):
            response = app.app.test_client().get('/api/studio/status',
                headers={'Origin': 'http://192.168.8.110'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, status)
        self.assertEqual(response.headers['Access-Control-Allow-Origin'], '*')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_preview_is_read_only(self):
        response = app.app.test_client().post('/api/studio/status')
        self.assertEqual(response.status_code, 405)

    def test_control_routes_do_not_allow_cross_origin_access(self):
        response = app.app.test_client().options('/api/control/play',
            headers={'Origin': 'http://192.168.8.110'})
        self.assertNotIn('Access-Control-Allow-Origin', response.headers)
