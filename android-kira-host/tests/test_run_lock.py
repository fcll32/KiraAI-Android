"""Run-lock fault injection against the real bridge entry, without a guest."""
from contextlib import ExitStack, contextmanager
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[1] / 'runtime'
sys.path.insert(0, str(RUNTIME))


class RunLockFailureTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('run_lock_fixture', RUNTIME / 'host_runtime.py')
        self.runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runtime)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = (Path(self.temp.name) / 'home').resolve()
        self.root = self.home / 'guest'
        self.root.mkdir(parents=True)

    def invoke(self, components='[]'):
        return self.runtime.run(str(self.home), '/fixture/native', '{}', '/fixture/seed.zip',
                                'arm64-v8a', '[]', components, '[]')

    @contextmanager
    def prepared(self):
        # Installation boundaries are fixtures; lifecycle, status files and run lock are real.
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

    def assert_retry_succeeds(self):
        self.assertFalse(self.runtime._run_lock.locked())
        with self.prepared():
            self.invoke()
        self.assertFalse(self.runtime._run_lock.locked())
        self.assertFalse(self.runtime._manager_ready)
        self.assertFalse(self.runtime._component_processes)

    def fail_directory_once(self, target):
        original = Path.mkdir
        def mkdir(path, *args, **kwargs):
            if path == target:
                raise OSError('fixture directory failure')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', new=mkdir):
            with self.assertRaisesRegex(OSError, 'fixture directory failure'):
                self.invoke()
        self.assert_retry_succeeds()

    def test_home_directory_failure_releases_lock_and_allows_retry(self):
        self.fail_directory_once(self.home)

    def test_runtime_directory_failure_releases_lock_and_allows_retry(self):
        self.fail_directory_once(self.home / 'linux-runtime')

    def test_initial_and_final_status_write_failure_allows_retry(self):
        with patch.object(self.runtime, '_atomic_json', side_effect=OSError('fixture status failure')):
            with self.assertRaisesRegex(OSError, 'fixture status failure'):
                self.invoke()
        self.assert_retry_succeeds()

    def test_final_snapshot_read_failure_allows_retry(self):
        with self.prepared(), patch.object(self.runtime, '_read_json', side_effect=OSError('fixture final read')):
            with self.assertRaisesRegex(OSError, 'fixture final read'):
                self.invoke()
        self.assert_retry_succeeds()

    @contextmanager
    def failed_final_write(self):
        read, write = self.runtime._read_json, self.runtime._atomic_json
        final_snapshot = False
        def read_snapshot(path, default):
            nonlocal final_snapshot
            value = read(path, default)
            if Path(path) == self.home / 'linux-runtime/status.json':
                final_snapshot = True
            return value
        def write_snapshot(path, value):
            if final_snapshot and Path(path) == self.home / 'linux-runtime/status.json':
                raise OSError('fixture final write')
            return write(path, value)
        with patch.object(self.runtime, '_read_json', side_effect=read_snapshot), \
             patch.object(self.runtime, '_atomic_json', side_effect=write_snapshot):
            yield

    def test_final_snapshot_write_failure_allows_retry(self):
        with self.prepared(), self.failed_final_write():
            with self.assertRaisesRegex(OSError, 'fixture final write'):
                self.invoke()
        self.assert_retry_succeeds()

    def test_refused_entry_does_not_unlock_another_running_worker(self):
        self.runtime._run_lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                self.invoke()
            self.assertTrue(self.runtime._run_lock.locked())
        finally:
            self.runtime._run_lock.release()

    def test_status_failure_preserves_unresolved_real_process_ownership(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        record = {'process': child, 'run_id': 'c01-owned-fixture', 'revision': 0,
                  'ready': True, 'log': io.BytesIO()}
        def register(*args, **kwargs):
            self.runtime._components['kira'].update(state='ready', pid=child.pid)
            self.runtime._component_processes['kira'] = record
            self.runtime._process = child
            self.runtime._cancel.set()
        try:
            with self.prepared(), patch.object(self.runtime, '_configure_guest', side_effect=register), \
                 patch.object(self.runtime, '_terminate_owned', side_effect=self.runtime.ProcessCleanupError('fixture cleanup unresolved')), \
                 self.failed_final_write():
                with self.assertRaisesRegex(OSError, 'fixture final write'):
                    self.invoke('["kira"]')
            self.assertFalse(self.runtime._run_lock.locked())
            self.assertIsNone(child.poll())
            self.assertIs(self.runtime._component_processes['kira'], record)
            self.assertIs(self.runtime._process, child)
            self.assertIn('kira', self.runtime._unsafe_stop)
            with self.assertRaisesRegex(RuntimeError, 'cleanup is unresolved'):
                self.invoke()
            self.assertFalse(self.runtime._run_lock.locked())
            self.assertIs(self.runtime._component_processes['kira'], record)
            self.assertIn('kira', self.runtime._unsafe_stop)
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
