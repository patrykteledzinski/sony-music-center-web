"""Isolated regression tests: no device requests or application data writes."""
import ast
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1].joinpath('app.py').read_text()
TREE = ast.parse(SOURCE)


def functions(*names, **globals_):
    nodes = []
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node = ast.parse(ast.unparse(node)).body[0]
            node.decorator_list = []
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'isolated', 'exec'), globals_)
    return globals_


class RegressionTests(unittest.TestCase):
    def queue(self):
        state = dict(items=['a.mp3', 'b.mp3', 'c.mp3'], index=1, source='album')
        return functions('play_queue_index', 'set_queue', 'queue_next',
                         'queue_previous', 'api_queue_remove', 'queue_public',
                         queue_state=state, play_lock=threading.RLock(),
                         queue_lock=threading.RLock(), save_queue=Mock(),
                         set_transport_file=Mock(side_effect=lambda x: x),
                         sony_next_native=Mock(), sony_previous_native=Mock(),
                         safe_path=Path, clean_title=lambda x: x,
                         request=SimpleNamespace(get_json=lambda **kw: {'index': 1}),
                         jsonify=lambda **kw: kw, is_audio=lambda p: True,
                         relative_path=str)

    def test_failed_play_preserves_index_and_storage(self):
        ns = self.queue()
        ns['set_transport_file'].side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            ns['play_queue_index'](2)
        self.assertEqual(ns['queue_state']['index'], 1)
        ns['save_queue'].assert_not_called()

    def test_failed_new_queue_preserves_old_queue(self):
        ns = self.queue()
        before = dict(ns['queue_state'])
        ns['set_transport_file'].side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            ns['set_queue'](['new.mp3'])
        self.assertEqual(ns['queue_state'], before)

    def test_removed_current_has_no_active_item_and_next_is_not_skipped(self):
        ns = self.queue()
        self.assertTrue(ns['api_queue_remove']()['ok'])
        self.assertFalse(any(i['active'] for i in ns['queue_public']()['items']))
        ns['queue_next']()
        ns['set_transport_file'].assert_called_once_with('c.mp3')
        self.assertTrue(ns['queue_public']()['items'][1]['active'])

    def test_play_failure_only_accepted_if_already_playing(self):
        for state in ['PLAYING', 'STOPPED']:
            ns = functions('sony_play_confirmed', sony_play=Mock(side_effect=RuntimeError('play')),
                           sony_transport=Mock(return_value=state), xml_value=lambda x, _: x)
            if state == 'PLAYING':
                ns['sony_play_confirmed']()
            else:
                with self.assertRaises(RuntimeError):
                    ns['sony_play_confirmed']()

    def test_radio_read_modify_write_is_locked(self):
        lock = threading.RLock()
        def read():
            self.assertTrue(lock._is_owned())
            return [{'id': 'old'}]
        def save(stations):
            self.assertTrue(lock._is_owned())
        ns = functions('add_radio_station', 'delete_radio_station', radio_lock=lock,
                       radio_stations=read, save_radio_stations=save,
                       validate_radio_url=lambda x: x,
                       uuid=SimpleNamespace(uuid4=lambda: SimpleNamespace(hex='new')))
        ns['add_radio_station']('Test', 'http://example.test')
        ns['delete_radio_station']('old')

    def test_inline_paths_use_attribute_escaping(self):
        for value in ['folder.path', 'track.path', 'p.name', 'item.path']:
            self.assertNotIn('${JSON.stringify(' + value + ')}', SOURCE)
            self.assertIn('${attrArg(' + value + ')}', SOURCE)


if __name__ == '__main__':
    unittest.main()
