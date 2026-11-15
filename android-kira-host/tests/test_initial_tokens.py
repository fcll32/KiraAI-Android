"""First defaults are immutable history, never a mirror of edited credentials."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
import host_runtime
import napcat_runtime
from initial_tokens import remember_initial_token


class InitialTokenTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)

    def history(self, component):
        return self.home / 'linux-runtime' / ('initial-' + component + '-token.json')

    def test_first_kira_default_is_saved_but_rotation_never_updates_history(self):
        host_runtime._configure_webui(self.home)
        config = self.home / 'kira-core/webui.json'
        first = json.loads(config.read_text())['access_token']
        self.assertGreaterEqual(len(first), 32)
        original = self.history('kira').read_bytes()
        self.assertEqual(json.loads(original)['token'], first)
        value = json.loads(config.read_text())
        value['access_token'] = 'user-changed-fixture'
        config.write_text(json.dumps(value))
        host_runtime._configure_webui(self.home)
        self.assertEqual(json.loads(config.read_text())['access_token'], 'user-changed-fixture')
        self.assertEqual(self.history('kira').read_bytes(), original)

    def test_existing_kira_token_is_preserved_and_never_mislabeled_as_original(self):
        config = self.home / 'kira-core/webui.json'
        config.parent.mkdir()
        config.write_text(json.dumps({'access_token': 'existing-edited-fixture'}))
        host_runtime._configure_webui(self.home)
        record = json.loads(self.history('kira').read_text())
        self.assertEqual(record['state'], 'unavailable')
        self.assertNotIn('token', record)
        self.assertEqual(json.loads(config.read_text())['access_token'], 'existing-edited-fixture')

    def test_napcat_fresh_default_history_survives_later_manual_changes(self):
        root = self.home / 'guest'
        root.mkdir()
        napcat_runtime.configure_local(root, initial_home=self.home, record_initial=True)
        path = napcat_runtime.token_path(root)
        first = json.loads(path.read_text())['token']
        original = self.history('napcat').read_bytes()
        value = json.loads(path.read_text())
        value['token'] = 'changed-napcat-fixture'
        path.write_text(json.dumps(value))
        napcat_runtime.configure_local(root, initial_home=self.home, record_initial=False)
        self.assertEqual(json.loads(original)['token'], first)
        self.assertEqual(self.history('napcat').read_bytes(), original)
        self.assertEqual(json.loads(path.read_text())['token'], 'changed-napcat-fixture')

    def test_unknown_history_and_corrupt_history_are_never_repaired_from_current(self):
        remember_initial_token(self.home, 'kira')
        original = self.history('kira').read_bytes()
        self.assertFalse(remember_initial_token(self.home, 'kira', 'edited-fixture'))
        self.assertEqual(self.history('kira').read_bytes(), original)
        self.history('napcat').write_bytes(b'{incomplete')
        self.assertFalse(remember_initial_token(self.home, 'napcat', 'edited-fixture'))
        self.assertEqual(self.history('napcat').read_bytes(), b'{incomplete')

    def test_snapshot_write_failure_does_not_rotate_or_block_the_component(self):
        with patch('initial_tokens.os.open', side_effect=PermissionError('fixture')):
            host_runtime._configure_webui(self.home)
        self.assertTrue((self.home / 'kira-core/webui.json').is_file())
        self.assertFalse(self.history('kira').exists())


if __name__ == '__main__':
    unittest.main()
