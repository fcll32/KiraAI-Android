"""CLI progress contracts plus real pip transfers against a slow local server."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / 'runtime'))
from dependency_progress import DependencyProgress, ProgressLogTail, safe_filename
import host_runtime as boot


class ProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (HERE / 'tests/tmp').mkdir(exist_ok=True)

    def test_partial_crlf_records_and_unknown_total_remain_indeterminate(self):
        parser = DependencyProgress('pip')
        self.assertEqual(parser.feed(b'Downloading https://user:private@example.invalid/a/pkg-1-py3-none-any.whl?token=secret\rProgr')[-1]['transfer_label'], 'pkg-1-py3-none-any.whl')
        event = parser.feed(b'ess 1024 of 0\r\n')[-1]
        self.assertEqual(event['transfer_done_bytes'], 1024)
        self.assertEqual(event['transfer_total_bytes'], 0)
        self.assertIsNone(event['transfer_percent'])
        self.assertNotIn('secret', json.dumps(event))
        self.assertNotIn('private', json.dumps(event))
        finished = parser.finish(True)
        self.assertFalse(finished['transfer_active'])
        self.assertIsNone(finished['transfer_percent'])

    def test_speed_excludes_resumed_prefix_and_new_file_resets_old_totals(self):
        ticks = iter([0, 0, 1, 2, 3])
        parser = DependencyProgress('pip', clock=lambda: next(ticks))
        parser.feed(b'Downloading first.whl\nProgress 4096 of 8192\n')
        event = parser.feed(b'Progress 6144 of 8192\n')[-1]
        self.assertEqual(event['transfer_bytes_per_second'], 1024.0)
        self.assertEqual(event['transfer_percent'], 75)
        event = parser.feed(b'Downloading second.whl\n')[-1]
        self.assertEqual(event['transfer_done_bytes'], 0)
        self.assertIsNone(event['transfer_percent'])
        self.assertEqual(event['transfer_label'], 'second.whl')

    def test_apt_percent_is_not_misrepresented_as_bytes_and_install_is_separate(self):
        parser = DependencyProgress('apt')
        event = parser.feed(b'dlstatus:7:33.3333:Downloading https://private:secret@example.invalid/x\n')[-1]
        self.assertEqual(event['transfer_kind'], 'download')
        self.assertEqual(event['transfer_percent'], 33.33)
        self.assertEqual(event['transfer_done_bytes'], 0)
        self.assertEqual(event['transfer_total_bytes'], 0)
        self.assertNotIn('secret', json.dumps(event))
        event = parser.feed(b'pmstatus:python3:arm64:55.0:Configuring untrusted log text\n')[-1]
        self.assertEqual(event['transfer_kind'], 'install')
        self.assertEqual(event['transfer_percent'], 55)
        self.assertIn('python3:arm64', event['transfer_label'])

    def test_invalid_and_oversized_lines_cannot_create_progress(self):
        parser = DependencyProgress('pip')
        self.assertFalse(parser.feed(b'X' * 10000))
        self.assertFalse(parser.feed(b'Progress 1 of 2\n'))
        self.assertEqual(parser.feed(b'Progress 1 of 2\n')[-1]['transfer_percent'], 50)
        self.assertFalse(parser.feed(b'Progress 99999999999999999999 of 1\n'))
        event = parser.feed(b'Progress 3 of 2\n')[-1]
        self.assertIsNone(event['transfer_percent'])
        apt = DependencyProgress('apt')
        self.assertFalse(apt.feed(b'dlstatus:1:nan:bad\npmstatus:pkg:101:bad\n'))

    def test_cache_and_install_records_do_not_claim_a_network_download(self):
        parser = DependencyProgress('pip')
        self.assertEqual(parser.feed(b'Using cached package.whl (2 MB)\n')[-1]['transfer_kind'], 'resolve')
        self.assertEqual(parser.feed(b'Installing collected packages: package\n')[-1]['transfer_kind'], 'install')
        self.assertEqual(safe_filename('https://token:secret@example.invalid/a/../../unsafe%0Apassword.whl?key=private'), 'Python 依赖')

    def test_log_tail_excludes_old_output_and_handles_append_and_truncation(self):
        with tempfile.TemporaryDirectory(dir=HERE / 'tests/tmp') as directory:
            path = Path(directory) / 'log'
            path.write_bytes(b'Progress 1 of 1\n')
            parser = DependencyProgress('pip')
            tail = ProgressLogTail(path, path.stat().st_size, parser)
            self.assertEqual(tail.poll(), [])
            with path.open('ab') as stream:
                stream.write(b'Progress 10 of 100\n')
            self.assertEqual(tail.poll()[-1]['transfer_percent'], 10)
            path.write_bytes(b'Progress 2 of 8\n')
            self.assertEqual(tail.poll()[-1]['transfer_percent'], 25)


class SlowServer(ThreadingHTTPServer):
    daemon_threads = True


class RealPipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wheel = HERE.parent / '.runtime/android-linux-runtime/artifacts/pip/pip-26.3-py3-none-any.whl'
        raw = cls.wheel.read_bytes()
        if hashlib.sha256(raw).hexdigest() != 'f3b60b20ffaadd4415ed7957212a38ac63fa4c5fa375bd0f4a797777940fad13':
            raise AssertionError('Reviewed pip CLI wheel differs from its pinned SHA256')
        cls.raw = raw
        cls.environment = dict(os.environ, PYTHONPATH=str(cls.wheel), PYTHONUNBUFFERED='1',
            PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK='1', NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
        version = subprocess.check_output([sys.executable, '-m', 'pip', '--version'], env=cls.environment, text=True)
        if not version.startswith('pip 26.3 '):
            raise AssertionError('The real CLI fixture must execute the reviewed pip 26.3 wheel')

    def setUp(self):
        (HERE / 'tests/tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=HERE / 'tests/tmp')
        self.folder = Path(self.temp.name)
        self.length = True
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != '/' + owner.wheel.name:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                if owner.length:
                    self.send_header('Content-Length', str(len(owner.raw)))
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    for offset in range(0, len(owner.raw), 65536):
                        self.wfile.write(owner.raw[offset:offset+65536])
                        self.wfile.flush()
                        time.sleep(0.055)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True
            def log_message(self, *args):
                pass
        self.server = SlowServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.process = None
        boot._cancel.clear()

    def tearDown(self):
        boot._cancel.clear()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def cli(self):
        return [sys.executable, '-m', 'pip', 'download', '--no-deps', '--no-cache-dir', '--disable-pip-version-check',
            '--progress-bar=raw', '--dest', str(self.folder / 'download'),
            'http://127.0.0.1:' + str(self.server.server_port) + '/' + self.wheel.name]

    def download(self):
        parser = DependencyProgress('pip')
        log_path = self.folder / 'command.log'
        events = []
        with log_path.open('wb', buffering=0) as log:
            self.process = subprocess.Popen(self.cli(), env=self.environment, stdout=log, stderr=subprocess.STDOUT)
            tail = ProgressLogTail(log_path, 0, parser)
            deadline = time.monotonic() + 25
            while self.process.poll() is None:
                events.extend(tail.poll())
                if time.monotonic() >= deadline:
                    raise TimeoutError('Loopback pip download did not finish')
                time.sleep(0.05)
            events.extend(tail.poll())
        self.assertEqual(self.process.returncode, 0, log_path.read_text(encoding='utf-8', errors='replace'))
        self.assertEqual((self.folder / 'download' / self.wheel.name).read_bytes(), self.raw)
        return events, parser

    def test_real_cli_known_length_reports_live_partial_bytes_before_completion(self):
        events, parser = self.download()
        downloads = [event for event in events if event['transfer_kind'] == 'download' and event['transfer_total_bytes']]
        partials = [event for event in downloads if 0 < event['transfer_done_bytes'] < len(self.raw)]
        self.assertGreaterEqual(len(partials), 2)
        self.assertTrue(all(0 < event['transfer_percent'] < 100 for event in partials))
        self.assertTrue(any(event['transfer_bytes_per_second'] > 0 for event in partials))
        self.assertEqual(downloads[-1]['transfer_done_bytes'], len(self.raw))
        self.assertEqual(downloads[-1]['transfer_percent'], 100)
        self.assertFalse(parser.finish(True)['transfer_active'])

    def test_real_cli_unknown_length_reports_bytes_but_never_invents_percent(self):
        self.length = False
        events, parser = self.download()
        progress = [event for event in events if event['transfer_kind'] == 'download' and event['transfer_done_bytes'] > 0]
        self.assertGreaterEqual(len(progress), 2)
        self.assertTrue(all(event['transfer_total_bytes'] == 0 and event['transfer_percent'] is None for event in progress))
        self.assertIsNone(parser.finish(True)['transfer_percent'])

    def test_command_monitor_cancel_clears_transfer_and_reaps_the_real_pip_process(self):
        home = self.folder / 'home'; home.mkdir()
        boot.state(home, 'starting', 'Installing Python dependencies', 65, stage='python_dependencies')
        original_state = boot.state
        observed = []
        def record(*args, **kwargs):
            original_state(*args, **kwargs)
            if kwargs.get('transfer_done_bytes', 0) > 0:
                observed.append(kwargs)
                if kwargs.get('transfer_active'):
                    boot._cancel.set()
        def spawn(*args):
            self.process = subprocess.Popen(self.cli(), env=self.environment, stdout=args[-1], stderr=subprocess.STDOUT,
                                            start_new_session=True, stdin=subprocess.DEVNULL)
            boot._process = self.process
            return self.process, 'isolated-loopback-progress'
        with (self.folder / 'command.log').open('wb', buffering=0) as log, \
                patch.object(boot, '_spawn', side_effect=spawn), patch.object(boot, 'state', side_effect=record):
            with self.assertRaises(boot.InstallationCancelled):
                boot._command('/unused', self.folder, home, {}, ['/python', '-m', 'pip', 'install'], log, timeout=15)
        self.assertTrue(observed)
        self.assertIsNotNone(self.process.poll())
        self.assertIsNone(boot._process)
        current = json.loads((home / 'linux-runtime/status.json').read_text(encoding='utf-8'))
        self.assertFalse(current['transfer_active'])
        self.assertFalse(current['transfer_complete'])
        self.assertEqual(current['progress'], 65)


if __name__ == '__main__':
    unittest.main(verbosity=2)
