"""Panel observations retain component ownership and leave lifecycle control free."""
from concurrent.futures import Future
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

RUNTIME = Path(__file__).resolve().parents[1] / 'runtime'
sys.path.insert(0, str(RUNTIME))


class HostHealthTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('host_health_fixture', RUNTIME / 'host_runtime.py')
        self.runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runtime)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.runtime._home = self.home
        self.runtime._manager_ready = True
        (self.home / 'linux-runtime').mkdir()
        (self.home / 'linux-runtime/components.json').write_text('["kira","napcat"]', encoding='utf-8')
        self.now = 20_000
        self.boot_count = 7
        clock = patch.object(self.runtime, 'boot_elapsed_ms', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def component(self, name, *, ready=True, run_id=None):
        runtime = self.runtime
        revision = runtime._revisions.setdefault(name, 0)
        runtime._desired.add(name)
        process = Mock(pid=101 + revision, returncode=None)
        process.poll.return_value = None
        run_id = run_id or name + '-run'
        record = dict(process=process, run_id=run_id, revision=revision,
                      ready=ready, deadline=time.monotonic() + 240, log=None)
        runtime._component_processes[name] = record
        runtime._components[name] = dict(state='ready' if ready else 'starting', run_id=run_id,
                                         version='未知', pid=process.pid, installed=True, selected=True)
        runtime._publish_components(self.home)
        return record

    def observe(self):
        self.runtime._observe_panels(self.home, self.boot_count)

    def status(self):
        return json.loads((self.home / 'linux-runtime/status.json').read_text(encoding='utf-8'))

    def job(self, name, kind):
        _, actual_kind, result = self.runtime._panel_jobs[name]
        self.assertEqual(actual_kind, kind)
        return result

    @contextmanager
    def blocked_job(self, name, kind, result_value):
        entered, release = threading.Event(), threading.Event()

        def block(*args, **kwargs):
            if entered.is_set():
                return False
            entered.set()
            if not release.wait(5):
                raise TimeoutError('test did not release panel worker')
            return result_value

        with patch.object(self.runtime, name, side_effect=block) as operation:
            pending = None
            try:
                self.observe()
                self.assertTrue(entered.wait(2))
                component = 'kira' if name in ('_ready', '_kira_version') else 'napcat'
                pending = self.job(component, kind)
                yield operation, pending
            finally:
                release.set()
                if pending is not None:
                    pending.result(timeout=2)

    def while_blocked(self, call):
        result = Future()

        def work():
            try:
                result.set_result(call())
            except Exception as error:
                result.set_exception(error)

        threading.Thread(target=work, daemon=True).start()
        return result.result(timeout=2)

    def test_slow_version_leaves_stop_and_exit_monitor_free(self):
        runtime = self.runtime
        self.component('kira')
        napcat = self.component('napcat')
        napcat['next_health_ms'] = self.now + runtime.PROBE_INTERVAL_MS
        with self.blocked_job('_kira_version', 'version', 'late-version') as (version, pending):
            self.observe()
            self.assertIs(self.job('kira', 'version'), pending)
            version.assert_called_once()
            with patch.object(runtime, '_terminate_owned'):
                self.assertTrue(self.while_blocked(lambda: runtime.control('kira', 'stop')))
                napcat['process'].poll.return_value = napcat['process'].returncode = 7
                self.while_blocked(lambda: runtime._monitor_components(self.home))
            self.assertFalse(pending.done())
            components = self.status()['components']
            self.assertEqual(components['kira']['state'], 'stopped')
            self.assertNotIn('health', components['kira'])
            self.assertEqual(components['napcat']['state'], 'error')
        self.observe()
        self.assertNotIn('kira', runtime._panel_jobs)
        self.assertEqual(self.status()['components']['kira']['version'], '未知')

    def test_slow_startup_leaves_start_deadline_enforced_and_drops_late_ready(self):
        runtime = self.runtime
        kira = self.component('kira', ready=False)
        with self.blocked_job('_ready', 'startup', True) as (ready, pending):
            self.observe()
            ready.assert_called_once()
            self.assertEqual(self.status()['components']['kira']['state'], 'starting')
            kira['deadline'] = time.monotonic() - 1
            with patch.object(runtime, '_terminate_owned'):
                self.while_blocked(lambda: runtime._monitor_components(self.home))
            self.assertFalse(pending.done())
            self.assertEqual(self.status()['components']['kira']['state'], 'error')
        self.observe()
        self.assertNotIn('kira', runtime._panel_jobs)
        self.assertEqual(self.status()['components']['kira']['state'], 'error')

    def test_late_health_cannot_update_replacement_and_slot_is_reclaimed(self):
        runtime = self.runtime
        self.component('napcat')
        with patch.object(runtime, '_terminate_owned'):
            with self.blocked_job('probe', 'health', True) as (probe, pending):
                self.assertTrue(self.while_blocked(lambda: runtime.control('napcat', 'restart')))
                replacement = self.component('napcat', run_id='replacement-run')
                self.assertEqual(replacement['revision'], 1)
                self.observe()
                self.assertIs(self.job('napcat', 'health'), pending)
                self.assertFalse(pending.done())
                probe.assert_called_once()
        with patch.object(runtime, 'probe', return_value=False) as probe:
            self.observe()
            health = self.status()['components']['napcat']['health']
            self.assertEqual(health['run_id'], 'replacement-run')
            self.assertEqual(health['state'], 'unknown')
            self.assertIsNone(health['observed_elapsed_ms'])
            self.job('napcat', 'health').result(timeout=2)
            self.observe()
            probe.assert_called_once()
        health = self.status()['components']['napcat']['health']
        self.assertIsNone(health['last_success_elapsed_ms'])
        self.assertNotIn('napcat', runtime._panel_jobs)

    def test_maintenance_revision_rejects_health_before_owner_cleanup(self):
        runtime = self.runtime
        napcat = self.component('napcat')
        with self.blocked_job('probe', 'health', True):
            runtime._component_busy.add('napcat')
            self.assertTrue(self.while_blocked(lambda: runtime.control('napcat', 'update')))
            self.assertIs(runtime._component_processes['napcat'], napcat)
            self.assertGreater(runtime._revisions['napcat'], napcat['revision'])
            runtime._component_busy.remove('napcat')
        self.observe()
        self.assertNotIn('napcat', runtime._panel_jobs)
        self.assertNotIn('health', self.status()['components']['napcat'])

    def test_global_stop_rejects_observation_without_waiting_for_worker(self):
        runtime = self.runtime
        self.component('napcat')
        with self.blocked_job('probe', 'health', True) as (_, pending):
            with patch.object(runtime, '_terminate_owned'):
                self.while_blocked(runtime.stop)
            self.assertTrue(runtime._cancel.is_set())
            self.assertFalse(pending.done())
            self.assertEqual(runtime._components['napcat']['state'], 'stopped')
        self.observe()
        self.assertNotIn('napcat', runtime._panel_jobs)
        self.assertNotIn('health', runtime._components['napcat'])

    def test_health_publication_retries_and_keeps_transfer_status(self):
        runtime = self.runtime
        self.component('napcat')
        runtime.state(self.home, 'starting', '依赖下载', 51, stage='python_deps',
                      transfer_done=20, transfer_total=200)
        with patch.object(runtime, 'probe', return_value=True) as probe:
            self.observe()
            self.job('napcat', 'health').result(timeout=2)
            before = self.status()
            with patch.object(runtime, '_atomic_json', side_effect=OSError('transient test failure')) as publish:
                self.observe()
            publish.assert_called_once()
            self.assertEqual(self.status(), before)
            self.observe()
            first = self.status()['components']['napcat']['health']
            self.assertEqual(first['state'], 'unknown')
            self.assertEqual(first['observed_elapsed_ms'], self.now)
            self.now += runtime.PROBE_INTERVAL_MS
            self.observe()
            self.job('napcat', 'health').result(timeout=2)
            self.observe()
            self.assertEqual(probe.call_count, 2)
        after = self.status()
        self.assertEqual({key: after[key] for key in before if key != 'components'},
                         {key: before[key] for key in before if key != 'components'})
        self.assertEqual(after['components']['napcat']['state'], 'ready')
        self.assertEqual(after['components']['napcat']['health'], {
            'state': 'reachable', 'observed_elapsed_ms': self.now, 'last_success_elapsed_ms': self.now,
            'boot_count': self.boot_count, 'pid': os.getpid(), 'run_id': 'napcat-run',
        })

    def test_kira_version_keeps_authentication_inside_one_deadline(self):
        config = self.home / 'kira-core/webui.json'
        config.parent.mkdir()
        config.write_text('{"access_token":"fixture-key"}', encoding='utf-8')
        runtime = self.runtime
        with patch.object(runtime, 'request', side_effect=[
            (200, b'{"access_token":"fixture-bearer"}'), (200, b'{"version":"v3.1.2"}'),
        ]) as request:
            self.assertEqual(runtime._kira_version(self.home), 'v3.1.2')
        login, version = request.call_args_list
        self.assertEqual(login.args, (5267, '/api/auth/login'))
        self.assertEqual(login.kwargs['method'], 'POST')
        self.assertEqual(json.loads(login.kwargs['body']), {'access_token': 'fixture-key'})
        self.assertEqual(version.args, (5267, '/api/version'))
        self.assertEqual(version.kwargs['headers'], {'Authorization': 'Bearer fixture-bearer'})
        self.assertEqual(login.kwargs['deadline'], version.kwargs['deadline'])


if __name__ == '__main__':
    unittest.main()
