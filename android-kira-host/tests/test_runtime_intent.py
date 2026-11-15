"""Exercise persisted intent and owned recovery without live accounts or endpoints."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

RUNTIME = Path(__file__).resolve().parents[1] / 'runtime'
sys.path.insert(0, str(RUNTIME))
from runtime_intent import RuntimeIntent


class RuntimeIntentTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('runtime_intent_fixture', RUNTIME / 'host_runtime.py')
        self.runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runtime)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.files = self.home / 'linux-runtime'
        self.files.mkdir()
        self.path = self.files / 'runtime-intent.json'
        self.root = self.home / 'guest'
        self.root.mkdir()
        (self.files / 'components.json').write_text('["kira","napcat"]', encoding='utf-8')

    def intent(self):
        return RuntimeIntent(self.path)

    def active(self):
        runtime = self.runtime
        runtime._intent_store = self.intent()
        runtime._intent_store.commit({name: {'wanted': True, 'revision': 1} for name in ('kira', 'napcat')})
        runtime._home = self.home
        runtime._manager_ready = True
        runtime._service_owner = 'current-service'
        runtime._revisions.update(kira=1, napcat=1)
        runtime._desired.update(('kira', 'napcat'))
        for name in ('kira', 'napcat'):
            runtime._components[name] = dict(state='ready', selected=True, installed=True)

    def owner(self, name, *, code=None, ready=True, since=100, deadline=240):
        runtime = self.runtime
        process = Mock(pid=110 if name == 'kira' else 111, returncode=code)
        process.poll.return_value = code
        record = dict(process=process, run_id=name + '-owner', revision=runtime._revisions[name],
                      log=None, ready=ready, ready_since=since, deadline=deadline)
        runtime._component_processes[name] = record
        return record

    def observe(self, now=100):
        runtime = self.runtime
        with patch.object(runtime.time, 'monotonic', return_value=now), \
                patch.object(runtime, '_terminate_owned') as terminate:
            runtime._monitor_components(self.home)
        return terminate

    def invoke(self, *, resume=False, token='current-service'):
        return self.runtime.run(self.home, '/fixture/native', '{}', '/fixture/seed.zip', 'x86_64',
                                '[]', '["kira"]', '[]', None, resume, token)

    @contextmanager
    def prepared(self):
        with ExitStack() as stack:
            for name, result in {
                '_android_timezone': 'UTC', '_abi_manifest': (None, {}),
                '_node_manifest': (None, {}), '_kernel_capabilities': {},
                '_stage_native': {}, '_prepare_rootfs': self.root,
                '_prepare_node': None, '_stage_source': self.root,
                '_configure_guest': None, '_bootstrap_requires_network': False,
            }.items():
                stack.enter_context(patch.object(self.runtime, name, return_value=result))
            yield

    def test_two_abnormal_exit_retries_then_persistent_latch(self):
        runtime = self.runtime
        self.active()
        kira = self.owner('kira')
        for now, delay, attempts in ((100, 1, 1), (102, 4, 2), (107, None, 2)):
            napcat = self.owner('napcat', code=7, since=now)
            terminate = self.observe(now)
            terminate.assert_called_once_with(napcat['process'], self.home, napcat['run_id'])
            self.assertIs(runtime._component_processes['kira'], kira)
            self.assertEqual(runtime._components['kira']['state'], 'ready')
            self.assertEqual(self.intent().values['napcat']['attempts'], attempts)
            if delay is None:
                self.assertNotIn('napcat', runtime._desired)
                self.assertEqual(self.intent().values['napcat']['blocked'], 'retries_exhausted')
            else:
                self.assertEqual(runtime._recovery_due.pop('napcat'), (1, now + delay))
                self.assertIn('napcat', runtime._desired)

    def test_run_loop_recovers_real_outer_process_twice(self):
        runtime = self.runtime
        processes = []

        def spawn(*args):
            run_id = 'synthetic-' + str(len(processes))
            script = ('from pathlib import Path\nimport sys, time\n'
                      'gate = Path(sys.argv[1])\nend = time.monotonic() + 5\n'
                      'while not gate.exists() and time.monotonic() < end: time.sleep(.01)\n'
                      'sys.exit(7 if gate.exists() else 9)')
            process = subprocess.Popen([sys.executable, '-c', script, str(self.files / run_id)],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(process)
            return process, run_id

        def version(*args):
            record = runtime._component_processes['kira']
            (self.files / record['run_id']).touch()
            return 'fixture'

        try:
            with self.prepared(), patch.object(runtime, 'RECOVERY_DELAYS', (.02, .04)), \
                    patch.object(runtime, '_configure_webui'), patch.object(runtime, '_bootstrap'), \
                    patch.object(runtime, '_check_panel_port'), patch.object(runtime, '_ready', return_value=True), \
                    patch.object(runtime, '_kira_version', side_effect=version), patch.object(runtime, '_spawn', side_effect=spawn):
                self.invoke()
            self.assertEqual([process.returncode for process in processes], [7, 7, 7])
            self.assertEqual(self.intent().values['kira']['blocked'], 'retries_exhausted')
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=3)

    def test_stable_owner_resets_budget_only_after_abnormal_exit(self):
        runtime = self.runtime
        self.active()
        runtime._intent_store.commit({'kira': {'attempts': 2}})
        self.owner('kira', code=8, since=100)
        self.observe(now=400)
        self.assertEqual(runtime._recovery_due['kira'], (1, 401))
        self.assertEqual(self.intent().values['kira']['attempts'], 1)

    def test_panel_health_clean_exit_and_startup_timeout_never_recover(self):
        runtime = self.runtime
        self.active()
        kira = self.owner('kira')
        runtime._components['kira']['health'] = {'state': 'unreachable'}
        self.observe().assert_not_called()
        self.assertIs(runtime._component_processes['kira'], kira)
        kira['process'].poll.return_value = kira['process'].returncode = 0
        self.owner('napcat', ready=False, deadline=50)
        self.observe()
        self.assertFalse(runtime._recovery_due)
        stored = self.intent().values
        self.assertEqual(stored['kira']['blocked'], 'outer_exit')
        self.assertEqual(stored['napcat']['blocked'], 'startup_failed')

    def test_explicit_stop_removes_scheduled_retry_and_keeps_selection(self):
        runtime = self.runtime
        self.active()
        self.owner('napcat', code=9)
        self.observe()
        with patch.object(runtime, '_spawn') as spawn, patch.object(runtime.time, 'monotonic', return_value=100):
            runtime._start_component('napcat', '/fixture/native', self.root, self.home, {}, self.root, node_prepared=False)
        spawn.assert_not_called()
        self.assertTrue(runtime.control('napcat', 'stop', 'current-service'))
        self.assertFalse(runtime._recovery_due)
        with patch.object(runtime, '_spawn') as spawn:
            runtime._start_component('napcat', '/fixture/native', self.root, self.home, {}, self.root, node_prepared=False)
        spawn.assert_not_called()
        stored = self.intent().values
        self.assertFalse(stored['napcat']['wanted'])
        self.assertTrue(stored['kira']['wanted'])
        self.assertEqual(runtime._read_component_selection(self.home), ['kira', 'napcat'])

    def test_resume_filters_persisted_stop_latch_and_global_stop_marker(self):
        runtime = self.runtime
        for changes, manual in (({'wanted': False}, False),
                                ({'wanted': True, 'blocked': 'retries_exhausted', 'attempts': 2}, False),
                                ({'wanted': True, 'blocked': None}, True)):
            with self.subTest(changes=changes, manual=manual):
                self.intent().commit({'kira': changes})
                marker = self.files / 'manual-stop'
                if manual: marker.touch()
                else: marker.unlink(missing_ok=True)
                with patch.object(runtime, '_prepare_rootfs') as prepare:
                    self.invoke(resume=True)
                prepare.assert_not_called()
                self.assertEqual(self.intent().values['kira']['wanted'], changes['wanted'])
        (self.files / 'manual-stop').unlink()

        def finish(name, *args, **kwargs):
            runtime._desired.discard(name)

        self.intent().commit({'kira': {'blocked': 'retries_exhausted', 'attempts': 2}})
        with self.prepared(), patch.object(runtime, '_start_component', side_effect=finish) as start:
            self.invoke(resume=False)
        start.assert_called_once()
        self.assertIsNone(self.intent().values['kira']['blocked'])
        self.assertEqual(self.intent().values['kira']['attempts'], 0)

    def test_service_destruction_keeps_intent_and_stale_owner_cannot_stop(self):
        runtime = self.runtime
        self.active()
        kira = self.owner('kira')
        before = self.path.read_bytes()
        with patch.object(runtime, '_terminate_owned') as terminate:
            for stale in (None, 'old-service'):
                self.assertFalse(runtime.control('kira', 'stop', stale))
                self.assertFalse(runtime.control('all', 'stop', stale))
                self.assertFalse(runtime.stop(True, stale))
            terminate.assert_not_called()
            self.assertFalse(runtime._cancel.is_set())
            self.assertIs(runtime._component_processes['kira'], kira)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertTrue(runtime.stop(False, 'current-service'))
            terminate.assert_called_once_with(kira['process'], self.home, kira['run_id'])
            self.assertEqual(self.path.read_bytes(), before)
            self.assertTrue(runtime.stop(True, 'current-service'))
        self.assertTrue(all(value['wanted'] is False for value in self.intent().values.values()))

    def test_pre_bridge_stop_is_applied_before_later_single_component_start(self):
        runtime = self.runtime
        self.intent().commit({name: {'wanted': True, 'revision': 1} for name in ('kira', 'napcat')})
        pending = self.files / 'manual-stop-pending'
        pending.write_bytes(b'1')

        def finish(name, *args, **kwargs):
            runtime._desired.discard(name)

        with self.prepared(), patch.object(runtime, '_start_component', side_effect=finish) as start:
            self.invoke(resume=False)
        start.assert_called_once()
        self.assertEqual(start.call_args.args[0], 'kira')
        self.assertFalse(pending.exists())
        self.assertTrue(self.intent().values['kira']['wanted'])
        self.assertFalse(self.intent().values['napcat']['wanted'])

    def test_pending_stop_retries_failed_intent_commit_and_preserves_new_marker(self):
        self.intent().commit({'kira': {'wanted': True}, 'napcat': {'wanted': True, 'blocked': 'cleanup_unconfirmed'}})
        pending = self.files / 'manual-stop-pending'
        applying = self.files / 'manual-stop-applying'
        pending.write_bytes(b'1')
        attempt = self.intent()
        with patch.object(attempt, 'commit', side_effect=OSError('fixture persistence failure')):
            with self.assertRaisesRegex(OSError, 'fixture persistence failure'):
                attempt.consume_pending_stop()
        self.assertTrue(applying.exists())
        self.assertFalse(pending.exists())
        self.assertTrue(self.intent().values['kira']['wanted'])

        retry = self.intent()
        original = retry.commit

        def publish_new_stop(changes):
            pending.write_bytes(b'2')
            original(changes)

        with patch.object(retry, 'commit', side_effect=publish_new_stop):
            self.assertTrue(retry.consume_pending_stop())
        self.assertEqual(pending.read_bytes(), b'2')
        self.assertFalse(applying.exists())
        self.assertTrue(self.intent().consume_pending_stop())
        self.assertFalse(pending.exists())
        stored = self.intent().values
        self.assertFalse(stored['kira']['wanted'])
        self.assertFalse(stored['napcat']['wanted'])
        self.assertEqual(stored['napcat']['blocked'], 'cleanup_unconfirmed')

    def test_unconfirmed_cleanup_latches_even_after_status_is_lost(self):
        runtime = self.runtime
        self.active()
        self.owner('napcat', code=7)
        with patch.object(runtime, '_terminate_owned', side_effect=RuntimeError('fixture cleanup failure')):
            runtime._monitor_components(self.home)
        self.assertFalse(runtime._recovery_due)
        self.assertEqual(self.intent().values['napcat']['blocked'], 'cleanup_unconfirmed')
        runtime._component_processes.clear()
        runtime._unsafe_stop.clear()
        (self.files / 'status.json').unlink(missing_ok=True)
        with self.assertRaisesRegex(RuntimeError, 'cleanup is unresolved'):
            self.invoke()

    def test_shared_environment_cleanup_blocks_both_components_after_process_reclaim(self):
        runtime = self.runtime
        self.active()
        process = Mock(pid=131)
        with patch.object(runtime, '_terminate_owned', side_effect=RuntimeError('fixture cleanup failure')):
            with self.assertRaises(runtime.ProcessCleanupError):
                runtime._cleanup_owned(process, self.home, 'shared-guest', None)
        self.assertTrue(all(value['blocked'] == 'cleanup_unconfirmed' for value in self.intent().values.values()))
        runtime._unsafe_stop.clear()
        with self.assertRaisesRegex(RuntimeError, 'cleanup is unresolved'):
            self.invoke(token='replacement-service')

    def test_write_failure_refuses_recovery_and_retains_intent_file(self):
        runtime = self.runtime
        self.active()
        self.owner('napcat', code=7)
        before = self.path.read_bytes()
        with patch.object(runtime._intent_store, 'commit', side_effect=OSError('fixture write failure')):
            self.observe()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn('napcat', runtime._desired)
        self.assertNotIn('napcat', runtime._recovery_due)
        self.assertEqual(runtime._components['napcat']['error'], 'IntentWriteError')

    def test_admitted_stop_blocks_next_owner_until_cleanup_returns(self):
        runtime = self.runtime
        self.active()
        self.owner('kira')
        entered, finish_run, in_stop, finish_stop = (threading.Event() for _ in range(4))

        def body(*args):
            entered.set()
            if not finish_run.wait(3): raise TimeoutError('fixture run did not finish')

        def cleanup(*args):
            in_stop.set()
            if not finish_stop.wait(3): raise TimeoutError('fixture stop did not finish')

        with patch.object(runtime, '_run_locked', side_effect=body), \
                patch.object(runtime, '_terminate_owned', side_effect=cleanup), ThreadPoolExecutor(2) as workers:
            old = workers.submit(self.invoke)
            try:
                self.assertTrue(entered.wait(2))
                stop = workers.submit(runtime.stop, True, 'current-service')
                self.assertTrue(in_stop.wait(2))
                finish_run.set()
                with self.assertRaisesRegex(RuntimeError, 'already running'):
                    self.invoke(token='next-service')
            finally:
                finish_run.set()
                finish_stop.set()
            self.assertTrue(stop.result(2))
            old.result(2)
        self.assertIsNone(runtime._service_owner)
        self.assertFalse(runtime._run_lock.locked())


if __name__ == '__main__':
    unittest.main()
