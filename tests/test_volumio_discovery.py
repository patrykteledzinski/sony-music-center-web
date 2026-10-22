import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from volumio_discovery import VolumioMonitor, system_info


class VolumioDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / 'volumio.json'
        self.monitor = VolumioMonitor('http://192.168.8.110', self.file, Mock())
        self.info = dict(id='pi-id', variant='volumio', name='Volumio')

    def test_success_remembers_identity_and_survives_restart(self):
        with patch('volumio_discovery.system_info', return_value=self.info):
            self.monitor.check()
        self.assertTrue(self.monitor.snapshot()['online'])
        self.assertEqual(json.loads(self.file.read_text())['id'], 'pi-id')
        restored = VolumioMonitor('http://192.168.8.110', self.file, Mock())
        self.assertEqual(restored.device_id, 'pi-id')

    def test_changed_ip_is_discovered_verified_and_saved(self):
        self.monitor.device_id = 'pi-id'
        def info(base):
            if base == 'http://192.168.8.110':
                raise OSError('No route to host')
            return self.info
        with patch('volumio_discovery.system_info', side_effect=info), \
             patch('volumio_discovery.discover_locations', return_value=[
                 'http://192.168.8.115:49152/description.xml']):
            self.monitor.check()
        self.assertEqual(self.monitor.snapshot()['url'], 'http://192.168.8.115')
        restored = VolumioMonitor('http://192.168.8.110', self.file, Mock())
        self.assertEqual(restored.base, 'http://192.168.8.115')

    def test_different_volumio_at_old_ip_does_not_replace_identity(self):
        self.monitor.device_id = 'pi-id'
        with patch('volumio_discovery.system_info', return_value=dict(self.info, id='other-pi')), \
             patch('volumio_discovery.discover_locations', return_value=['http://192.168.8.115:49152/description.xml']) as discover:
            self.monitor.check()
            self.monitor.check()
        discover.assert_called_once()
        self.assertFalse(self.monitor.snapshot()['online'])
        self.assertEqual(self.monitor.device_id, 'pi-id')
        self.assertFalse(self.file.exists())

    def test_unpinned_ambiguous_devices_are_not_selected(self):
        def info(base):
            if base == self.monitor.base:
                raise OSError('offline')
            return self.info
        with patch('volumio_discovery.system_info', side_effect=info), \
             patch('volumio_discovery.discover_locations', return_value=[
                 'http://192.168.8.115:49152/description.xml',
                 'http://192.168.8.116:49152/description.xml']):
            self.monitor.check()
        self.assertFalse(self.monitor.snapshot()['online'])

    def test_disabled_monitor_does_not_contact_network(self):
        monitor = VolumioMonitor(None, self.file, Mock())
        with patch('volumio_discovery.system_info') as get:
            monitor.check()
        get.assert_not_called()
        self.assertFalse(monitor.snapshot()['enabled'])

    def test_api_rejects_other_services(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        for data in ({'id': 'other', 'variant': 'other'}, [], {'variant':'volumio'}):
            response.read.return_value = json.dumps(data).encode()
            with patch('volumio_discovery.urllib.request.urlopen', return_value=response):
                with self.assertRaises(ValueError):
                    system_info('http://192.168.8.110')
