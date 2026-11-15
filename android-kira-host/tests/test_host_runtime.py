"""Regression coverage for independent APK/core updates and existing data."""
import hashlib
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
import host_runtime as runtime


class HostRuntimeTests(unittest.TestCase):
    def test_apt_progress_uses_an_independent_descriptor_and_keeps_arguments(self):
        process = Mock()
        process.poll.return_value = 0
        process.returncode = 0
        command = ['/usr/bin/apt-get', '-o', 'APT::Status-Fd=1', '-y', 'install', '/tmp/a package.deb']
        with tempfile.TemporaryDirectory() as folder, patch.object(runtime, '_spawn', return_value=(process, 'test-run')) as spawn, \
                patch.object(runtime, '_cleanup_owned'), patch.object(runtime, 'state'):
            runtime._command('/unused', Path(folder), Path(folder), {}, command, io.BytesIO())
        launch = spawn.call_args.args[-2]
        self.assertEqual(launch[:4], ['/bin/sh', '-c', 'exec 3>&1; exec "$@"', 'kira-apt-progress'])
        self.assertEqual(launch[4:], [command[0], '-o', 'APT::Status-Fd=3', *command[3:]])
        self.assertEqual(command[2], 'APT::Status-Fd=1')

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.root = self.home / 'guest'
        self.root.mkdir()
        self.seed = self.home / 'official.zip'
        panel = self.home / 'panel.zip'
        with zipfile.ZipFile(panel, 'w') as archive:
            archive.writestr('index.html', '<html>official</html>')
            archive.writestr('assets/panel.js', 'official-panel')
        wheel = b'wheel-fixture'
        self.original = {'main.py': b'official supervisor', 'requirements.txt': b'uuid~=1.30\n',
                         'core/supervisor.py': b'official loop', 'core/launcher.py': b'official launcher',
                         'webui/app.py': b'official webui'}
        with zipfile.ZipFile(self.seed, 'w') as archive:
            for name, content in self.original.items():
                archive.writestr('KiraAI/' + name, content)
            archive.writestr('wheels/pip-26.3-py3-none-any.whl', wheel)
            archive.writestr('dist.zip', panel.read_bytes())
        self.manifest = {
            'officialRepository': {'seed_size': self.seed.stat().st_size,
                'seed_sha256': hashlib.sha256(self.seed.read_bytes()).hexdigest(),
                'initial_commit': 'a' * 40, 'version': 'v3.1.2'},
            'pipArtifact': {'filename': 'pip-26.3-py3-none-any.whl', 'size': len(wheel),
                'sha256': hashlib.sha256(wheel).hexdigest(), 'version': '26.3'},
            'frontendArtifact': {'size': panel.stat().st_size,
                'sha256': hashlib.sha256(panel.read_bytes()).hexdigest(), 'version': 'v3.1.2'}}
        runtime._cancel.clear()

    def tearDown(self):
        runtime._cancel.clear()
        self.temporary.cleanup()

    def install(self):
        return runtime._stage_source(self.seed, self.root, self.home, self.manifest)

    def test_first_seed_preserves_every_official_source_byte(self):
        code = self.install()
        for name, content in self.original.items():
            self.assertEqual((code / name).read_bytes(), content)
        self.assertFalse((code / 'linux_kira_entry.py').exists())
        self.assertEqual((self.home / 'kira-core/dist/.version').read_text(), 'v3.1.2')

    def test_new_apk_seed_keeps_core_and_panel_officially_updated_in_place(self):
        code = self.install()
        (code / 'main.py').write_bytes(b'new official supervisor')
        (code / 'requirements.txt').write_bytes(b'new official requirements\n')
        panel = self.home / 'kira-core/dist/index.html'
        panel.write_bytes(b'new official panel')
        self.manifest['officialRepository']['version'] = 'v9.0.0'
        self.manifest['officialRepository']['seed_sha256'] = 'b' * 64
        self.assertEqual(self.install(), code)
        self.assertEqual((code / 'main.py').read_bytes(), b'new official supervisor')
        self.assertEqual(panel.read_bytes(), b'new official panel')
        self.assertEqual(runtime._requirements_digest(code),
                         hashlib.sha256(b'new official requirements\n').hexdigest())

    def test_prior_data_and_token_preserved_during_first_panel_migration(self):
        data = self.home / 'kira-core'
        (data / 'dist').mkdir(parents=True)
        (data / 'dist/index.html').write_text('old panel')
        (data / 'memory.sqlite').write_bytes(b'existing conversations')
        config = {'host': '0.0.0.0', 'port': 5267, 'access_token': 'sensitive', 'other': True}
        (data / 'webui.json').write_text(json.dumps(config))
        self.install()
        runtime._configure_webui(self.home)
        self.assertEqual((data / 'memory.sqlite').read_bytes(), b'existing conversations')
        self.assertEqual(json.loads((data / 'webui.json').read_text()),
                         {**config, 'host': '127.0.0.1'})
        backups = list((self.home / 'linux-runtime/panel-backups').glob('*/index.html'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), 'old panel')

    def test_interrupted_first_seed_recovers_before_starting_core(self):
        with patch.object(runtime, '_install_frontend', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.install()
        code = self.root / 'opt/KiraAI'
        (code / 'local-note.txt').write_text('keep on recovery')
        self.assertEqual(self.install(), code)
        self.assertEqual((code / 'local-note.txt').read_text(), 'keep on recovery')
        record = json.loads((self.home / 'linux-runtime/official-install.json').read_text())
        self.assertEqual(record['state'], 'complete')

    def test_bad_seed_cannot_publish_source_or_modify_user_data(self):
        self.manifest['officialRepository']['seed_sha256'] = 'c' * 64
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / 'opt/KiraAI').exists())
        self.assertFalse((self.home / 'kira-core').exists())

    def test_existing_incomplete_core_is_preserved_for_recovery(self):
        code = self.root / 'opt/KiraAI'
        code.mkdir(parents=True)
        (code / 'user.txt').write_text('preserve')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual((code / 'user.txt').read_text(), 'preserve')

    def test_zip_traversal_is_rejected(self):
        archive = self.home / 'unsafe.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('../escape.py', 'bad')
        with zipfile.ZipFile(archive) as source:
            with self.assertRaises(ValueError):
                runtime._extract_zip(source, self.home / 'stage')
        self.assertFalse((self.home / 'escape.py').exists())

    def test_official_supervisor_owns_updates_and_restarts(self):
        self.assertEqual(runtime._official_command(), ['/opt/KiraAI/venv/bin/python', '-u',
                         '/opt/KiraAI/main.py', '--data-dir', '/var/lib/kira'])
        argv = runtime._guest_argv('/apk/lib', self.root, self.home,
                                   runtime._official_command(), 'run')
        self.assertIn(str(self.home / 'kira-core') + ':/var/lib/kira', argv)
        self.assertIn('/opt/KiraAI', argv)
        self.assertNotIn('--_child', argv)
        self.assertNotIn('--disable-webui-auth', argv)

    def test_old_kernel_is_recorded_without_blocking_python(self):
        with patch.object(runtime.os, 'uname', return_value=type('Uname', (), {'release': '4.14.190'})(), create=True):
            capabilities = runtime._kernel_capabilities('4.18')
        self.assertFalse(capabilities['node_kernel_in_supported_range'])
        self.assertIsNone(capabilities['node_available'])

    def test_readiness_requests_html_for_official_spa_route(self):
        request = Mock(side_effect=[(200, b'{"status":"ok"}'),
                                    (200, b'<!doctype html><html><body>Official panel</body></html>')])
        with patch.object(runtime, 'request', request), patch('panel_health.request', request), \
                patch.object(runtime, 'deadline_after', return_value=12.5):
            self.assertTrue(runtime._ready())
        self.assertEqual(request.call_args_list, [
            unittest.mock.call(5267, '/api/health', deadline=12.5, max_bytes=4096),
            unittest.mock.call(5267, '/', headers={'Accept': 'text/html'},
                               max_bytes=8192, prefix=True, deadline=12.5)])

    def test_panel_port_probe_sets_reuse_before_binding(self):
        probe = Mock()
        context = Mock()
        context.__enter__ = Mock(return_value=probe)
        context.__exit__ = Mock(return_value=False)
        with patch.object(runtime.socket, 'socket', return_value=context):
            runtime._check_panel_port()
        self.assertEqual(probe.method_calls, [
            unittest.mock.call.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1),
            unittest.mock.call.bind(('127.0.0.1', 5267))])

    @unittest.skipIf(os.name == 'nt', 'Android/Linux socket reuse semantics differ from Windows')
    def test_panel_port_probe_rejects_active_listener(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            with self.assertRaises(OSError):
                runtime._check_panel_port(listener.getsockname()[1])

    @unittest.skipIf(os.name == 'nt', 'Android/Linux socket reuse semantics differ from Windows')
    def test_panel_port_probe_accepts_time_wait_after_core_stops(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', 0))
            address = listener.getsockname()
            listener.listen(1)
            with socket.create_connection(address, timeout=2) as client:
                accepted, _ = listener.accept()
                accepted.close()
                self.assertEqual(client.recv(1), b'')
        with socket.socket() as exclusive:
            with self.assertRaises(OSError):
                exclusive.bind(address)
        runtime._check_panel_port(address[1])

    def test_cancelled_seed_never_publishes_code(self):
        runtime._cancel.set()
        with self.assertRaises(runtime.InstallationCancelled):
            self.install()
        self.assertFalse((self.root / 'opt/KiraAI').exists())


class ComponentIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        runtime._home = self.home
        runtime._cancel.clear()
        runtime._manager_ready = True
        runtime._active_component = None
        runtime._active_cancel = None
        runtime._components.clear()
        runtime._components.update({name: {'state': 'ready', 'pid': index + 100, 'version': 'test'}
                                    for index, name in enumerate(('kira', 'napcat'))})
        runtime._desired.clear()
        runtime._desired.update(('kira', 'napcat'))
        runtime._revisions.clear()
        runtime._revisions.update(kira=0, napcat=0)
        runtime._component_processes.clear()
        runtime._component_busy.clear()
        runtime._unsafe_stop.clear()
        runtime._installs.clear()
        runtime._updates.clear()
        runtime._backups.clear()

    def tearDown(self):
        runtime._components.clear()
        runtime._component_processes.clear()
        runtime._unsafe_stop.clear()
        runtime._desired.clear()
        runtime._manager_ready = False
        runtime._active_component = runtime._active_cancel = None
        runtime._process = None
        runtime._cancel.clear()
        self.temporary.cleanup()

    def record(self, name, returncode=None):
        process = Mock()
        process.pid = 123
        process.poll.return_value = returncode
        process.returncode = returncode
        record = {'process': process, 'log': Mock(), 'run_id': name + '-ownership',
                  'revision': 0, 'ready': True}
        runtime._component_processes[name] = record
        return record

    def test_stopping_napcat_signals_only_its_owned_processes(self):
        kira, napcat = self.record('kira'), self.record('napcat')
        with patch.object(runtime, '_terminate_owned') as terminate:
            self.assertTrue(runtime.control('napcat', 'stop'))
        terminate.assert_called_once_with(napcat['process'], self.home, 'napcat-ownership')
        self.assertIs(runtime._component_processes['kira'], kira)
        self.assertEqual(runtime._components['kira']['state'], 'ready')
        self.assertEqual(runtime._components['napcat']['state'], 'stopped')
        self.assertEqual(runtime._desired, {'kira'})

    def test_install_cancellation_keeps_other_component_running(self):
        kira = self.record('kira')
        runtime._active_component = 'napcat'
        runtime._active_cancel = threading.Event()
        with patch.object(runtime, '_terminate_owned') as terminate:
            runtime.control('napcat', 'stop')
        self.assertTrue(runtime._active_cancel.is_set())
        self.assertFalse(runtime._cancel.is_set())
        terminate.assert_not_called()
        self.assertIs(runtime._component_processes['kira'], kira)
        self.assertEqual(json.loads((self.home / 'linux-runtime/status.json').read_text(encoding='utf-8'))['components']['kira']['state'], 'ready')

    def test_unexpected_napcat_exit_retains_kira_and_error(self):
        kira = self.record('kira')
        napcat = self.record('napcat', returncode=7)
        with patch.object(runtime, '_terminate_owned') as terminate:
            runtime._monitor_components(self.home)
        terminate.assert_called_once_with(napcat['process'], self.home, 'napcat-ownership')
        self.assertIs(runtime._component_processes['kira'], kira)
        self.assertEqual(runtime._components['napcat']['state'], 'error')
        self.assertEqual(runtime._desired, {'kira'})
        current = json.loads((self.home / 'linux-runtime/status.json').read_text(encoding='utf-8'))
        self.assertEqual(current['state'], 'ready')
        self.assertEqual(current['components']['kira']['state'], 'ready')

    def test_closed_manager_refuses_late_bridge_commands(self):
        runtime._manager_ready = False
        self.assertFalse(runtime.control('napcat', 'restart'))
        self.assertEqual(runtime._revisions['napcat'], 0)

    def test_unconfirmed_stop_keeps_ownership_and_blocks_restart(self):
        record = self.record('napcat')
        with patch.object(runtime, '_terminate_owned', side_effect=RuntimeError('still running')):
            self.assertFalse(runtime._stop_component('napcat'))
        self.assertIs(runtime._component_processes['napcat'], record)
        self.assertFalse(record['log'].close.called)
        self.assertTrue(runtime._components['napcat']['unsafe_stop'])
        self.assertFalse(runtime.control('napcat', 'start'))

    def test_stop_after_worker_selection_prevents_installation(self):
        runtime.control('napcat', 'stop')
        with patch('napcat_runtime.ensure') as install:
            runtime._start_component('napcat', '/native', self.home, self.home, {}, self.home,
                                     node_prepared=True)
        install.assert_not_called()

    def test_command_cleanup_failure_keeps_unregistered_process_and_blocks_spawn(self):
        runtime._active_component = 'napcat'
        process, log = Mock(), io.BytesIO()
        process.pid, process.returncode = 123, 0
        process.poll.return_value = 0
        def spawn(*args):
            runtime._process = process
            return process, 'installer-ownership'
        with patch.object(runtime, '_spawn', side_effect=spawn), \
                patch.object(runtime, '_terminate_owned', side_effect=RuntimeError('still running')):
            with self.assertRaises(runtime.ProcessCleanupError):
                runtime._command('/native', self.home, self.home, {}, ['/true'], log)
        self.assertIs(runtime._process, process)
        self.assertIs(runtime._component_processes['napcat']['process'], process)
        self.assertTrue(runtime._components['napcat']['unsafe_stop'])
        with patch.object(runtime.subprocess, 'Popen') as popen:
            with self.assertRaises(runtime.ProcessCleanupError):
                runtime._spawn('/native', self.home, self.home, {}, ['/true'], log)
        popen.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'Requires the same-UID Linux process table')
    def test_reparented_component_child_is_stopped_by_run_identity(self):
        run_id = uuid.uuid4().hex
        environment = dict(os.environ, KIRA_LINUX_RUN_ID=run_id)
        parent = subprocess.Popen([sys.executable, '-c',
            'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(120)"], '
            'start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)'],
            env=environment, start_new_session=True)
        try:
            parent.wait(timeout=10)
            self.assertTrue(runtime._owned_children(parent, run_id))
            runtime._terminate_owned(parent, self.home, run_id, timeout=0.1)
            self.assertFalse(runtime._owned_children(parent, run_id))
        finally:
            for child in runtime._owned_children(parent, run_id): child.kill()
            if parent.poll() is None: parent.kill()
            parent.wait(timeout=5)


class ComponentOwnershipHandoffTests(unittest.TestCase):
    """Exercise publication and cancellation with actual owned OS processes."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.root = self.home / 'guest'
        self.root.mkdir()
        (self.home / 'linux-runtime').mkdir()
        self.processes, self.logs, self.threads, self.thread_errors = [], [], [], []
        self.argv_requests, self.markers = [], {}
        self.cancel_hook = None
        self.aborted = threading.Event()
        runtime._home = self.home
        runtime._process = runtime._run_id = None
        runtime._cancel.clear()
        runtime._active_component = runtime._active_cancel = None
        runtime._manager_ready = True
        runtime._components.clear()
        runtime._components.update({name: {'state': 'stopped', 'pid': None, 'version': 'fixture'}
                                    for name in ('kira', 'napcat')})
        runtime._desired.clear()
        runtime._revisions.clear()
        runtime._revisions.update(kira=0, napcat=0)
        runtime._component_processes.clear()
        runtime._component_busy.clear()
        runtime._unsafe_stop.clear()
        runtime._updates.clear()
        runtime._backups.clear()
        self.adapters = ExitStack()
        self.adapters.enter_context(patch.object(runtime, '_guest_argv', side_effect=self.guest_argv))
        self.adapters.enter_context(patch.object(runtime, '_bootstrap'))
        # Fixed production ports may already be in use on the connected phone.
        self.adapters.enter_context(patch.object(runtime, '_check_panel_port'))

    def tearDown(self):
        self.aborted.set()
        for thread in self.threads:
            thread.join(timeout=12)
        for record in runtime._component_processes.values():
            self.remember(record['process'])
            if record['log'] is not None: self.logs.append(record['log'])
        if runtime._process is not None: self.remember(runtime._process)
        for process in self.processes:
            if process.poll() is None: process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in self.logs:
            if not log.closed: log.close()
        self.adapters.close()
        runtime._components.clear()
        runtime._component_processes.clear()
        runtime._component_busy.clear()
        runtime._unsafe_stop.clear()
        runtime._desired.clear()
        runtime._installs.clear()
        runtime._updates.clear()
        runtime._backups.clear()
        runtime._process = runtime._run_id = None
        runtime._manager_ready = False
        runtime._active_component = runtime._active_cancel = None
        runtime._cancel.clear()
        self.temporary.cleanup()

    def remember(self, process):
        if not any(existing is process for existing in self.processes):
            self.processes.append(process)
        return process

    def wait_for(self, condition):
        deadline = time.monotonic() + 10
        while not condition():
            if self.aborted.is_set() or time.monotonic() >= deadline:
                raise AssertionError('Process fixture did not reach its synchronization point')
            time.sleep(0.01)

    def python_argv(self, marker):
        return [sys.executable, '-u', '-c',
                'import os,sys,time; from pathlib import Path; '
                'Path(sys.argv[1]).write_text(str(os.getpid()),encoding="ascii"); time.sleep(120)',
                str(marker)]

    def guest_argv(self, native_dir, root, home, command, run_id):
        marker = self.home / ('child-' + run_id + '.ready')
        self.markers[run_id] = marker
        self.argv_requests.append((tuple(command), run_id))
        hook, self.cancel_hook = self.cancel_hook, None
        if hook is not None:
            hook['marker'] = marker
            hook['run_id'] = run_id
            hook['spawning'].set()
            if not hook['locked'].wait(timeout=10):
                raise AssertionError('Controller did not claim the publication lock')
        return self.python_argv(marker)

    def start(self, name, action='start'):
        self.assertTrue(runtime.control(name, action))
        runtime._start_component(name, '/native', self.root, self.home, os.environ.copy(), self.root,
                                 node_prepared=False)
        record = runtime._component_processes.get(name)
        if record is not None:
            self.remember(record['process'])
            self.logs.append(record['log'])
        return record

    def cancel_before_registration(self, name, action='start'):
        hook = {'spawning': threading.Event(), 'locked': threading.Event()}
        self.cancel_hook = hook

        def controller():
            try:
                if not hook['spawning'].wait(timeout=10):
                    raise AssertionError('Component never reached process creation')
                with runtime._component_lock:
                    hook['locked'].set()
                    # The worker cannot register while this thread owns this
                    # lock. Wait until real Popen has published command ownership.
                    self.wait_for(lambda: runtime._process is not None and hook['marker'].is_file())
                    hook['process'] = self.remember(runtime._process)
                    if not runtime.control(name, 'stop'):
                        raise AssertionError('Component stop was not accepted')
            except BaseException as error:
                self.thread_errors.append(error)
                hook['locked'].set()

        thread = threading.Thread(target=controller, name='fixture-cancel-before-registration')
        self.threads.append(thread)
        thread.start()
        self.start(name, action)
        thread.join(timeout=12)
        self.assertFalse(thread.is_alive(), 'Cancellation controller did not finish')
        if self.thread_errors: raise self.thread_errors[0]
        return hook['process'], hook['run_id']

    def test_cancelled_unregistered_process_cleans_once_and_restarts_through_control(self):
        with patch.object(runtime, '_terminate_owned', wraps=runtime._terminate_owned) as terminate:
            old, run_id = self.cancel_before_registration('kira')
            self.assertIsNotNone(old.poll())
            terminate.assert_called_once_with(old, self.home, run_id)
            self.assertIsNone(runtime._process)
            self.assertNotIn('kira', runtime._component_processes)
            self.assertFalse(runtime._unsafe_stop)
            record = self.start('kira')
            self.assertIsNotNone(record, 'Normal restart did not register a process')
            self.wait_for(self.markers[record['run_id']].is_file)
            self.assertIsNone(record['process'].poll())
            self.assertNotEqual(record['process'].pid, old.pid)
            self.assertIsNone(runtime._process)
            self.assertEqual(len(self.argv_requests), 2)
            self.assertEqual(terminate.call_count, 1)

    def test_failed_unregistered_cleanup_keeps_owner_log_and_blocks_new_process(self):
        with patch.object(runtime, '_terminate_owned', side_effect=RuntimeError('injected stop failure')) as terminate:
            process, run_id = self.cancel_before_registration('kira')
        terminate.assert_called_once_with(process, self.home, run_id)
        self.assertIsNone(process.poll())
        self.assertIs(runtime._process, process)
        record = runtime._component_processes['kira']
        self.assertIs(record['process'], process)
        self.assertEqual(record['run_id'], run_id)
        self.assertFalse(record['log'].closed)
        self.assertTrue(runtime._components['kira']['unsafe_stop'])
        self.assertIn('kira', runtime._unsafe_stop)
        self.assertFalse(runtime.control('kira', 'start'))
        runtime._start_component('kira', '/native', self.root, self.home, os.environ.copy(), self.root,
                                 node_prepared=False)
        with self.assertRaises(runtime.ProcessCleanupError):
            runtime._spawn('/native', self.root, self.home, os.environ.copy(), ['/fixture'], record['log'])
        self.assertEqual(len(self.argv_requests), 1)
        self.assertIs(runtime._process, process)

    def test_cleanup_does_not_clear_owner_replaced_during_termination(self):
        log = (self.home / 'ownership.log').open('ab', buffering=0)
        self.logs.append(log)
        old, run_id = runtime._spawn('/native', self.root, self.home, os.environ.copy(), ['/fixture'], log)
        self.remember(old)
        replacement = self.remember(subprocess.Popen(self.python_argv(self.home / 'replacement.ready'),
            cwd=self.home, env=os.environ.copy(), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        terminating, replaced = threading.Event(), threading.Event()
        replacement_id = uuid.uuid4().hex

        def replace_owner():
            if not terminating.wait(timeout=10): return
            with runtime._process_lock:
                runtime._process, runtime._run_id = replacement, replacement_id
            replaced.set()

        thread = threading.Thread(target=replace_owner, name='fixture-replace-command-owner')
        self.threads.append(thread)
        thread.start()
        terminate = runtime._terminate_owned

        def terminate_while_owner_changes(process, home, owned_run_id):
            terminating.set()
            self.assertTrue(replaced.wait(timeout=10), 'Process lock was held during slow cleanup')
            terminate(process, home, owned_run_id)

        with patch.object(runtime, '_terminate_owned', side_effect=terminate_while_owner_changes):
            runtime._cleanup_owned(old, self.home, run_id, log)
        thread.join(timeout=12)
        self.assertFalse(thread.is_alive())
        self.assertIsNotNone(old.poll())
        self.assertIs(runtime._process, replacement)
        self.assertEqual(runtime._run_id, replacement_id)
        self.assertIsNone(replacement.poll())
        self.assertFalse(runtime._unsafe_stop)

    def test_registered_stop_start_transfers_ownership_to_a_new_process(self):
        first = self.start('kira')
        self.assertIsNotNone(first)
        self.wait_for(self.markers[first['run_id']].is_file)
        self.assertIsNone(first['process'].poll())
        self.assertIsNone(runtime._process)
        self.assertTrue(runtime.control('kira', 'stop'))
        self.assertIsNotNone(first['process'].poll())
        self.assertTrue(first['log'].closed)
        self.assertNotIn('kira', runtime._component_processes)
        self.assertIsNone(runtime._process)
        second = self.start('kira')
        self.assertIsNotNone(second)
        self.wait_for(self.markers[second['run_id']].is_file)
        self.assertIsNone(second['process'].poll())
        self.assertNotEqual(first['process'].pid, second['process'].pid)
        self.assertNotEqual(first['run_id'], second['run_id'])
        self.assertIsNone(runtime._process)
        self.assertEqual(len(self.argv_requests), 2)

    def test_cancelling_napcat_install_preserves_live_kira_process_and_state(self):
        (self.home / 'linux-runtime/components.json').write_text('["kira","napcat"]', encoding='utf-8')
        kira = self.start('kira')
        self.assertIsNotNone(kira)
        self.wait_for(self.markers[kira['run_id']].is_file)
        kira['ready'] = True
        runtime._component_state('kira', 'ready', 'Fixture Kira is running')
        before = runtime._component_snapshot()['kira']

        def install(root, home, command, log, cancel, publish, updating=False):
            command(['/fixture-napcat-install'], log, timeout=20)
            self.fail('Cancelled installer returned successfully')

        with patch('napcat_runtime.ensure', side_effect=install), \
                patch.object(runtime, '_terminate_owned', wraps=runtime._terminate_owned) as terminate:
            installer, run_id = self.cancel_before_registration('napcat', 'install')
        terminate.assert_called_once_with(installer, self.home, run_id)
        self.assertIsNotNone(installer.poll())
        self.assertIs(runtime._component_processes['kira'], kira)
        self.assertIsNone(kira['process'].poll())
        self.assertFalse(kira['log'].closed)
        self.assertEqual(runtime._component_snapshot()['kira'], before)
        self.assertEqual(runtime._components['napcat']['state'], 'stopped')
        self.assertEqual(runtime._desired, {'kira'})
        self.assertIsNone(runtime._process)
        self.assertFalse(runtime._unsafe_stop)


if __name__ == '__main__':
    unittest.main()
