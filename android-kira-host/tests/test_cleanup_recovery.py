"""C09 proof, unknown processes, PID reuse, cancellation and durable unlock."""
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import signal
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

RUNTIME = Path(__file__).resolve().parents[1] / 'runtime'
sys.path.insert(0, str(RUNTIME))
import cleanup_recovery as recovery
from linux_installer import InstallationCancelled
from runtime_intent import RuntimeIntent


class CleanupProofTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.proc = self.home / 'proc'
        self.proc.mkdir()
        self.native = self.home / 'native'
        self.cancel = threading.Event()
        self.exe = {}
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(recovery.os, 'getuid', return_value=0, create=True))
        stack.enter_context(patch.object(recovery.os, 'getpid', return_value=999))
        stack.enter_context(patch.object(recovery.signal, 'SIGKILL', 9, create=True))
        stack.enter_context(patch.object(recovery.os, 'readlink', side_effect=lambda p: self.exe[int(p.parent.name)]))

    def process(self, pid, executable, argv, environment=b'', birth='12'):
        folder = self.proc / str(pid)
        folder.mkdir(exist_ok=True)
        (folder / 'stat').write_text(f'{pid} (test) S 1 ' + '0 ' * 17 + birth)
        (folder / 'cmdline').write_bytes(argv + b'\0')
        (folder / 'environ').write_bytes(environment)
        self.exe[pid] = str(executable)
        return folder

    def inspect(self):
        return recovery.inspect_processes(self.home, self.native, 'top.kira.test', self.cancel, proc=self.proc)

    def test_known_android_host_is_allowed_but_unknown_same_uid_is_not(self):
        self.process(21, '/system/bin/app_process64', b'top.kira.test')
        self.assertEqual(self.inspect(), [])
        self.process(22, '/system/bin/sh', b'/system/bin/sh')
        with self.assertRaises(recovery.CleanupUnconfirmed):
            self.inspect()

    def test_missing_metadata_of_a_live_process_blocks_proof(self):
        folder = self.process(21, '/system/bin/app_process64', b'top.kira.test')
        (folder / 'environ').unlink()
        with self.assertRaises(recovery.CleanupUnconfirmed):
            self.inspect()

    def test_manual_verification_never_signals_a_remaining_guest(self):
        self.process(22, self.native / 'libproot.so', b'proot')
        with patch.object(recovery.os, 'kill') as kill, self.assertRaises(recovery.CleanupUnconfirmed):
            recovery.verify_stopped(self.home, self.native, 'top.kira.test', self.cancel, proc=self.proc)
        kill.assert_not_called()

    def test_retry_signals_guest_before_tracer_and_rechecks_identity(self):
        self.process(21, self.native / 'libproot.so', b'proot')
        self.process(22, '/usr/bin/python', b'python', b'KIRA_LINUX_RUN_ID=' + b'a' * 32 + b'\0')
        observed = []
        def terminate(pid, value):
            observed.append((pid, value))
            (self.proc / str(pid) / 'stat').write_text(f'{pid} (test) Z 1 ' + '0 ' * 17 + '12')
        with patch.object(recovery.os, 'kill', side_effect=terminate):
            recovery.verify_stopped(self.home, self.native, 'top.kira.test', self.cancel, retry=True, proc=self.proc)
        self.assertEqual(observed, [(22, signal.SIGTERM), (21, signal.SIGTERM)])
        self.process(21, self.native / 'libproot.so', b'proot', birth='13')
        with patch.object(recovery.os, 'kill') as kill:
            recovery._signal(recovery.OwnedProcess(21, '12', True), signal.SIGTERM, self.proc)
        kill.assert_not_called()

    def test_cancel_never_unlocks(self):
        self.cancel.set()
        with self.assertRaises(InstallationCancelled):
            recovery.verify_stopped(self.home, self.native, 'top.kira.test', self.cancel, proc=self.proc)


class CleanupCommitTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('cleanup_runtime_fixture', RUNTIME / 'host_runtime.py')
        self.runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runtime)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.state = self.home / 'linux-runtime'
        self.state.mkdir()
        self.intent = self.state / 'runtime-intent.json'
        RuntimeIntent(self.intent).commit({'kira': {'wanted': True, 'blocked': 'cleanup_unconfirmed'}})
        (self.state / 'status.json').write_text(json.dumps({'state': 'error', 'components': {
            'kira': {'unsafe_stop': True, 'state': 'error', 'installed': True}}}))

    def recover(self):
        return json.loads(self.runtime.recover_cleanup(self.home, '/fixture/native', 'top.kira.test', False, 'fixture-owner'))

    def test_verified_unlock_persists_stopped_state_and_releases_ownership(self):
        with patch.object(self.runtime, 'verify_stopped'):
            result = self.recover()
        self.assertTrue(result['released'])
        values = RuntimeIntent(self.intent).values
        self.assertTrue(all(v['wanted'] is False and v['blocked'] is None for v in values.values()))
        status = json.loads((self.state / 'status.json').read_text(encoding='utf-8'))
        self.assertEqual(status['state'], 'stopped')
        self.assertTrue(status['components']['kira']['installed'])
        self.assertFalse(self.runtime._run_lock.locked())
        self.assertIsNone(self.runtime._service_owner)

    def test_unknown_or_failed_status_write_keeps_persistent_blocker(self):
        for fail_proof in (True, False):
            with self.subTest(fail_proof=fail_proof), \
                    patch.object(self.runtime, 'verify_stopped', side_effect=recovery.CleanupUnconfirmed('unknown') if fail_proof else None), \
                    patch.object(self.runtime, '_atomic_json', side_effect=OSError('fixture full disk')):
                self.assertFalse(self.recover()['released'])
            self.assertTrue(all(v['blocked'] == 'cleanup_unconfirmed' for v in RuntimeIntent(self.intent).values.values()))

    def test_running_backend_excludes_recovery(self):
        before = self.intent.read_bytes()
        self.runtime._run_lock.acquire()
        try:
            with patch.object(self.runtime, 'verify_stopped') as verify:
                self.assertFalse(self.recover()['released'])
                verify.assert_not_called()
        finally:
            self.runtime._run_lock.release()
        self.assertEqual(self.intent.read_bytes(), before)

    def test_intent_commit_failure_after_status_write_still_blocks_next_start(self):
        commit = RuntimeIntent.commit
        def fail_release(store, changes):
            stored_status = json.loads((self.state / 'status.json').read_text(encoding='utf-8'))
            if stored_status.get('state') == 'stopped':
                raise OSError('fixture failed release')
            return commit(store, changes)
        with patch.object(self.runtime, 'verify_stopped'), patch.object(RuntimeIntent, 'commit', fail_release):
            self.assertFalse(self.recover()['released'])
        self.assertTrue(all(v['blocked'] == 'cleanup_unconfirmed' for v in RuntimeIntent(self.intent).values.values()))

    def test_known_live_handle_still_blocks_if_process_scan_misses_it(self):
        process = Mock()
        process.poll.return_value = None
        self.runtime._component_processes['kira'] = {'process': process}
        with patch.object(self.runtime, 'verify_stopped'):
            self.assertFalse(self.recover()['released'])
        self.assertEqual(RuntimeIntent(self.intent).values['kira']['blocked'], 'cleanup_unconfirmed')

    def test_busy_manager_keeps_current_intent_owner(self):
        self.runtime._manager_ready = True
        self.runtime._service_owner = 'existing-owner'
        with patch.object(self.runtime, 'verify_stopped') as verify:
            self.assertFalse(self.recover()['released'])
        verify.assert_not_called()
        self.assertEqual(self.runtime._service_owner, 'existing-owner')


if __name__ == '__main__':
    unittest.main()
