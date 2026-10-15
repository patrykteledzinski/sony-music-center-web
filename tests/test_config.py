from pathlib import Path
import json
import tempfile
import unittest
from sony_config import load_settings


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, values):
        (self.root/'config.json').write_text(json.dumps(values))

    def test_defaults_do_not_depend_on_deployed_machine(self):
        settings=load_settings(self.root, {})
        self.assertEqual(settings.state_dir,self.root)
        self.assertEqual(settings.sony_ip,'192.168.1.100')
        self.assertEqual(settings.web_port,8088)
        self.assertFalse((self.root/'config.json').exists())

    def test_local_config_and_relative_paths(self):
        self.write(dict(sony_ip='192.0.2.10',server_ip='192.0.2.20',music_root='music',state_dir='state'))
        settings=load_settings(self.root,{})
        self.assertEqual(settings.sony_ip,'192.0.2.10')
        self.assertEqual(settings.music_root,self.root/'music')
        self.assertEqual(settings.state_dir,self.root/'state')

    def test_environment_overrides_file(self):
        self.write(dict(web_port=8088))
        settings=load_settings(self.root,dict(SONY_WEB_WEB_PORT='9090',SONY_WEB_MUSIC_ROOT='tracks'))
        self.assertEqual(settings.web_port,9090)
        self.assertEqual(settings.music_root,self.root/'tracks')

    def test_explicit_config_path(self):
        (self.root/'alternate.json').write_text('{"web_port":9091}')
        self.assertEqual(load_settings(self.root,{'SONY_WEB_CONFIG':'alternate.json'}).web_port,9091)
        with self.assertRaises(ValueError):
            load_settings(self.root,{'SONY_WEB_CONFIG':'missing.json'})

    def test_invalid_ports_addresses_and_keys_fail_early(self):
        for values in [dict(web_port=0),dict(web_port=True),dict(web_port=70000),
                       dict(sony_ip='http://host/path'),dict(server_ip=True),
                       dict(unknown='value'),dict(music_root=''),dict(ffmpeg_binary=42)]:
            with self.subTest(values=values):
                self.write(values)
                with self.assertRaises(ValueError):
                    load_settings(self.root,{})

    def test_malformed_or_non_object_json_is_rejected(self):
        for content in ['{', '[]', 'null']:
            (self.root/'config.json').write_text(content)
            with self.assertRaises(ValueError):
                load_settings(self.root,{})
