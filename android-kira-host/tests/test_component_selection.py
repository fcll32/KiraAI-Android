"""Verify optional NapCat selection through the Python bridge and installer boundary."""
from contextlib import ExitStack, contextmanager
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

RUNTIME = Path(__file__).resolve().parents[1] / 'runtime'
sys.path.insert(0, str(RUNTIME))
import napcat_runtime


class ComponentSelectionTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('component_selection_fixture', RUNTIME / 'host_runtime.py')
        self.runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runtime)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.files = self.home / 'linux-runtime'
        self.files.mkdir()
        self.root = self.home / 'guest'
        self.root.mkdir()

    def select(self, *names):
        (self.files / 'components.json').write_text(json.dumps(names), encoding='utf-8')

    def run_host(self, selection=None, actions=()):
        return self.runtime.run(self.home, '/fixture/native', '{}', '/fixture/seed.zip',
                                'x86_64', '[]', selection, json.dumps(list(actions)))

    def status(self):
        return json.loads((self.files / 'status.json').read_text(encoding='utf-8'))

    @contextmanager
    def prepared(self):
        with ExitStack() as stack:
            for name, value in {
                '_android_timezone': 'UTC', '_abi_manifest': (None, {}),
                '_node_manifest': (None, {}), '_kernel_capabilities': {},
                '_stage_native': {}, '_prepare_rootfs': self.root,
                '_prepare_node': None, '_stage_source': self.root,
                '_configure_guest': None, '_bootstrap_requires_network': False,
            }.items():
                stack.enter_context(patch.object(self.runtime, name, return_value=value))
            yield

    def ready_host(self, *, installed=False):
        runtime = self.runtime
        runtime._home = self.home
        runtime._manager_ready = True
        runtime._revisions.update(kira=0, napcat=0)
        runtime._components.update(kira={'state': 'ready', 'installed': True},
            napcat={'state': 'stopped', 'installed': installed, 'installing': False, 'install_failed': False})
        runtime._desired.add('kira')

    def test_new_and_legacy_all_runs_start_only_kira_without_napcat_actions(self):
        runtime = self.runtime

        def start(name, *args, **kwargs):
            runtime._desired.discard(name)

        with self.prepared(), patch.object(runtime, '_start_component', side_effect=start) as component, \
                patch.object(napcat_runtime, 'ensure') as ensure, patch.object(napcat_runtime, 'configure_local') as configure:
            self.run_host()
            self.run_host('["kira","napcat"]')
        self.assertEqual([call.args[0] for call in component.call_args_list], ['kira', 'kira'])
        ensure.assert_not_called()
        configure.assert_not_called()
        napcat = self.status()['components']['napcat']
        self.assertFalse(napcat['selected'])
        self.assertFalse(napcat['installed'])

    def test_empty_selection_and_uninstalled_update_skip_guest_preparation(self):
        self.select()
        update = {'component': 'napcat', 'action': 'update'}
        with patch.object(self.runtime, '_abi_manifest') as prepare:
            self.run_host('["kira","napcat"]', [update])
        prepare.assert_not_called()
        self.assertEqual(self.status()['state'], 'stopped')
        self.assertFalse(self.status()['components']['kira']['selected'])
        self.assertEqual(json.loads((self.files / 'components.json').read_text(encoding='utf-8')), [])

    def test_old_qq_data_migrates_selection_without_claiming_installation(self):
        digest = '1' * 64
        guest = self.home / 'kira-linux' / ('rootfs-' + digest[:16])
        data = guest / 'root/.config/QQ/login.db'
        data.parent.mkdir(parents=True)
        data.write_bytes(b'legacy QQ fixture')
        marker = guest / '.kira-rootfs.json'
        marker.write_text(json.dumps({'sha256': digest}), encoding='utf-8')
        self.assertEqual(self.runtime._read_component_selection(self.home), ['kira', 'napcat'])
        with patch.object(self.runtime, '_abi_manifest') as prepare:
            self.run_host('["napcat"]')
        prepare.assert_not_called()
        self.assertTrue(self.status()['components']['napcat']['selected'])
        self.assertFalse(self.status()['components']['napcat']['installed'])
        self.select('kira')
        self.assertEqual(self.runtime._read_component_selection(self.home), ['kira'])
        self.assertEqual(data.read_bytes(), b'legacy QQ fixture')
        (self.files / 'components.json').write_bytes('["kira"]'.encode('utf-16'))
        self.assertEqual(self.runtime._read_component_selection(self.home), [])

    def test_all_restart_obeys_current_saved_selection_and_stop_preserves_it(self):
        runtime = self.runtime
        self.ready_host(installed=True)
        self.select('kira')
        self.assertTrue(runtime.control('all', 'restart'))
        self.assertEqual(runtime._desired, {'kira'})
        self.assertEqual(runtime._revisions, {'kira': 1, 'napcat': 0})
        self.assertTrue(runtime.control('napcat', 'start'))
        self.assertEqual(runtime._desired, {'kira'})
        self.assertTrue(runtime.control('all', 'stop'))
        self.assertEqual(runtime._read_component_selection(self.home), ['kira'])

    def test_uninstalled_start_restart_and_update_require_explicit_install(self):
        runtime = self.runtime
        self.ready_host()
        self.select('kira', 'napcat')
        for action in ('start', 'restart', 'update'):
            self.assertTrue(runtime.control('napcat', action))
        self.assertEqual(runtime._revisions['napcat'], 0)
        self.assertNotIn('napcat', runtime._desired | runtime._installs | runtime._updates)
        self.assertTrue(runtime.control('napcat', 'install'))
        queued = self.status()['components']['napcat']
        self.assertTrue(queued['selected'])
        self.assertTrue(queued['installing'])
        self.assertFalse(queued['installed'])
        with patch.object(napcat_runtime, 'ensure', side_effect=OSError('fixture download failed')) as ensure:
            runtime._start_component('napcat', '/fixture/native', self.root, self.home, {}, self.root,
                                     node_prepared=False)
        ensure.assert_called_once()
        self.assertFalse(ensure.call_args.kwargs['updating'])
        status = self.status()
        self.assertTrue(status['components']['kira']['running'])
        napcat = status['components']['napcat']
        self.assertTrue(napcat['install_failed'])
        self.assertFalse(napcat['installing'])
        self.assertFalse(napcat['installed'])

    def test_cold_explicit_install_queues_napcat_only(self):
        runtime = self.runtime
        self.select('kira', 'napcat')
        observed = []

        def start(name, *args, **kwargs):
            observed.append((name, runtime._desired.copy(), runtime._installs.copy()))
            runtime._installs.discard(name)
            runtime._desired.discard(name)

        with self.prepared(), patch.object(runtime, '_start_component', side_effect=start):
            self.run_host('[]', [{'component': 'napcat', 'action': 'install'}])
        self.assertEqual(observed, [('napcat', {'napcat'}, {'napcat'})])

    def test_plain_installed_start_verifies_metadata_without_installer(self):
        runtime = self.runtime
        self.ready_host(installed=True)
        self.select('kira', 'napcat')
        self.assertTrue(runtime.control('napcat', 'start'))
        process = Mock(pid=145)
        with patch.object(napcat_runtime, 'metadata', return_value={'installed': True, 'version': 'v4.18.42'}), \
                patch.object(napcat_runtime, 'ensure') as ensure, \
                patch.object(napcat_runtime, 'configure_local') as configure, \
                patch.object(runtime, '_check_panel_port'), \
                patch.object(runtime, '_spawn', return_value=(process, 'napcat-fixture')):
            runtime._start_component('napcat', '/fixture/native', self.root, self.home, {}, self.root,
                                     node_prepared=False)
        runtime._component_processes['napcat']['log'].close()
        ensure.assert_not_called()
        configure.assert_called_once_with(self.root)
        self.assertTrue(self.status()['components']['napcat']['installed'])


if __name__ == '__main__':
    unittest.main()
