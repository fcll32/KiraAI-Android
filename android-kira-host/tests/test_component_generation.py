"""Component stop ownership under controlled monitor/control interleavings."""
from contextlib import ExitStack
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

RUNTIME = Path(__file__).resolve().parents[1] / 'runtime'
sys.path.insert(0, str(RUNTIME))


class ComponentGenerationTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('component_generation_fixture', RUNTIME / 'host_runtime.py')
        self.runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runtime)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.root = self.home / 'guest'
        self.root.mkdir()
        (self.home / 'linux-runtime').mkdir()
        self.runtime._home = self.home
        self.runtime._manager_ready = True
        self.runtime._revisions.update(kira=0, napcat=0)
        self.runtime._desired.update(('kira', 'napcat'))
        self.runtime._components.update({name: {'state': 'ready', 'pid': 100 + index,
            'version': 'fixture', 'error': None, 'run_id': name + '-fixture'}
            for index, name in enumerate(('kira', 'napcat'))})
        self.logs = []
        self.addCleanup(self.close_logs)

    def close_logs(self):
        logs = self.logs + [item['log'] for item in self.runtime._component_processes.values()]
        for log in logs:
            if log is not None:
                log.close()

    def record(self, pid=101, run_id='old-run', revision=0, returncode=7, ready=True):
        process = Mock()
        process.pid, process.returncode = pid, returncode
        process.poll.return_value = returncode
        log = io.BytesIO()
        self.logs.append(log)
        return {'process': process, 'run_id': run_id, 'revision': revision, 'ready': ready,
                'deadline': 0, 'log': log}

    @staticmethod
    def owner(record):
        return (record, record['process'], record['run_id'], record['revision'])

    def stale_monitor_interleaving(self, timeout=False):
        runtime = self.runtime
        old = self.record(returncode=None if timeout else 7, ready=not timeout)
        runtime._component_processes['kira'] = old
        new = self.record(202, 'new-run', 1, returncode=None)
        stop_reached, replacement_published = threading.Event(), threading.Event()
        errors, terminated = [], []
        original_stop = runtime._stop_component
        def stop(name, **kwargs):
            if threading.current_thread().name == 'old-monitor':
                stop_reached.set()
                if not replacement_published.wait(3):
                    raise AssertionError('Replacement was not published')
            return original_stop(name, **kwargs)
        def monitor():
            try:
                runtime._monitor_components(self.home)
            except BaseException as error:
                errors.append(error)
        with ExitStack() as adapters:
            adapters.enter_context(patch.object(runtime, '_stop_component', side_effect=stop))
            adapters.enter_context(patch.object(runtime, '_terminate_owned',
                side_effect=lambda process, home, run_id: terminated.append((process.pid, run_id))))
            for name in ('_configure_webui', '_bootstrap', '_check_panel_port'):
                adapters.enter_context(patch.object(runtime, name))
            adapters.enter_context(patch.object(runtime, '_ready', return_value=False))
            adapters.enter_context(patch.object(runtime, '_spawn', return_value=(new['process'], new['run_id'])))
            thread = threading.Thread(target=monitor, name='old-monitor')
            thread.start()
            try:
                self.assertTrue(stop_reached.wait(3))
                self.assertTrue(runtime.control('kira', 'restart'))
                runtime._start_component('kira', '/fixture/native', self.root, self.home, {}, self.root,
                                         node_prepared=False)
                replacement = runtime._component_processes['kira']
                before = copy.deepcopy(runtime._components['kira'])
                self.assertIs(replacement['process'], new['process'])
                replacement_published.set()
                thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(terminated, [(old['process'].pid, old['run_id'])])
                self.assertIs(runtime._component_processes.get('kira'), replacement)
                self.assertEqual(runtime._components['kira'], before)
                self.assertIn('kira', runtime._desired)
                self.assertFalse(replacement['log'].closed)
            finally:
                replacement_published.set()
                thread.join(3)

    def test_stale_exit_monitor_cannot_stop_control_replacement(self):
        self.stale_monitor_interleaving()

    def test_stale_timeout_monitor_cannot_stop_control_replacement(self):
        self.stale_monitor_interleaving(timeout=True)

    def test_rejected_owner_cannot_change_new_state_desired_or_busy(self):
        runtime = self.runtime
        old, new = self.record(), self.record(202, 'new-run', 1, returncode=None)
        runtime._component_processes['kira'] = new
        runtime._component_busy.add('kira')
        runtime._revisions['kira'] = 1
        before = copy.deepcopy(runtime._components['kira'])
        for owner in (self.owner(old), self.owner(new)):
            with self.subTest(same_record=owner[0] is new), patch.object(runtime, '_terminate_owned') as terminate:
                self.assertFalse(runtime._stop_component('kira', failure='old exit', expected=owner))
            terminate.assert_not_called()
        self.assertIs(runtime._component_processes['kira'], new)
        self.assertEqual(runtime._components['kira'], before)
        self.assertIn('kira', runtime._desired)
        self.assertIn('kira', runtime._component_busy)

    def test_changed_run_id_or_revision_invalidates_same_record_object(self):
        runtime = self.runtime
        for field, new_value in (('run_id', 'replacement-run'), ('revision', 5)):
            with self.subTest(field=field):
                old = self.record()
                runtime._component_processes['kira'] = old
                owner = self.owner(old)
                old[field] = new_value
                with patch.object(runtime, '_terminate_owned') as terminate:
                    self.assertFalse(runtime._stop_component('kira', failure='old exit', expected=owner))
                terminate.assert_not_called()
                self.assertIn('kira', runtime._desired)
                self.assertNotIn('kira', runtime._component_busy)

    def test_claim_blocks_a_restart_spawn_until_owned_cleanup_finishes(self):
        runtime = self.runtime
        old = self.record(returncode=None)
        runtime._component_processes['kira'] = old
        claimed, finish_cleanup = threading.Event(), threading.Event()
        errors = []
        def terminate(*args):
            claimed.set()
            if not finish_cleanup.wait(3):
                raise AssertionError('Cleanup fixture was not released')
        def stop():
            try:
                runtime._stop_component('kira', failure='old exit', expected=self.owner(old))
            except BaseException as error:
                errors.append(error)
        with patch.object(runtime, '_terminate_owned', side_effect=terminate), \
             patch.object(runtime, '_spawn') as spawn:
            thread = threading.Thread(target=stop)
            thread.start()
            try:
                self.assertTrue(claimed.wait(3))
                self.assertIn('kira', runtime._component_busy)
                self.assertTrue(runtime.control('kira', 'restart'))
                runtime._start_component('kira', '/fixture/native', self.root, self.home, {}, self.root,
                                         node_prepared=False)
                spawn.assert_not_called()
            finally:
                finish_cleanup.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertNotIn('kira', runtime._component_busy)
        self.assertIn('kira', runtime._desired)

    def test_failed_claimed_cleanup_retains_guard_and_refuses_new_run(self):
        runtime = self.runtime
        old = self.record(returncode=None)
        runtime._component_processes['kira'] = old
        with patch.object(runtime, '_terminate_owned', side_effect=runtime.ProcessCleanupError('fixture still owned')):
            self.assertFalse(runtime._stop_component('kira', failure='old exit', expected=self.owner(old)))
        self.assertIs(runtime._component_processes['kira'], old)
        self.assertFalse(old['log'].closed)
        self.assertIn('kira', runtime._unsafe_stop)
        self.assertNotIn('kira', runtime._component_busy)
        self.assertFalse(runtime.control('kira', 'restart'))
        with self.assertRaisesRegex(RuntimeError, 'cleanup is unresolved'):
            runtime.run(str(self.home), '/fixture/native', '{}', '/fixture/seed.zip', 'arm64-v8a')
        self.assertFalse(runtime._run_lock.locked())

    def test_old_cleanup_completion_cannot_publish_over_replaced_record(self):
        self.replaced_during_cleanup(fail=False)

    def test_monitor_cleanup_failure_publishes_the_retained_unsafe_owner(self):
        runtime = self.runtime
        old = self.record()
        runtime._component_processes['kira'] = old
        runtime._publish_components(self.home)
        with patch.object(runtime, '_terminate_owned', side_effect=runtime.ProcessCleanupError('fixture still owned')):
            runtime._monitor_components(self.home)
        status = json.loads((self.home / 'linux-runtime/status.json').read_text(encoding='utf-8'))
        self.assertTrue(status['components']['kira'].get('unsafe_stop'))
        self.assertEqual(status['components']['kira']['run_id'], old['run_id'])
        self.assertEqual(status['components']['kira']['pid'], old['process'].pid)
        self.assertIs(runtime._component_processes['kira'], old)
        self.assertFalse(runtime.control('kira', 'restart'))
        with self.assertRaisesRegex(RuntimeError, 'cleanup is unresolved'):
            runtime.run(str(self.home), '/fixture/native', '{}', '/fixture/seed.zip', 'arm64-v8a')
        self.assertFalse(runtime._run_lock.locked())
        self.assertIs(runtime._component_processes['kira'], old)
        self.assertIn('kira', runtime._unsafe_stop)

    def test_old_cleanup_error_cannot_mark_replacement_unsafe(self):
        self.replaced_during_cleanup(fail=True)

    def replaced_during_cleanup(self, fail):
        runtime = self.runtime
        old, new = self.record(), self.record(202, 'new-run', 1, returncode=None)
        runtime._component_processes['kira'] = old
        expected_state = {'state': 'ready', 'pid': 202, 'version': 'new', 'run_id': 'new-run'}
        def terminate(*args):
            # Deliberately replace the table out of band to exercise completion CAS.
            with runtime._component_lock:
                runtime._component_processes['kira'] = new
                runtime._components['kira'] = dict(expected_state)
                runtime._revisions['kira'] = 1
                runtime._desired.add('kira')
            if fail:
                raise runtime.ProcessCleanupError('old cleanup failed')
        with patch.object(runtime, '_terminate_owned', side_effect=terminate):
            self.assertFalse(runtime._stop_component('kira', failure='old exit', expected=self.owner(old)))
        self.assertIs(runtime._component_processes['kira'], new)
        self.assertEqual(runtime._components['kira'], expected_state)
        self.assertIn('kira', runtime._desired)
        self.assertNotIn('kira', runtime._unsafe_stop)
        self.assertFalse(new['log'].closed)


if __name__ == '__main__':
    unittest.main()
