"""Guest environment settings, telemetry default and ownership boundaries."""
import io
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
import host_runtime as runtime
from runtime_environment import EnvironmentConfigurationError, load_environment


class RuntimeEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.path = self.home / 'linux-runtime/environment.json'
        self.path.parent.mkdir()

    def save(self, value):
        self.path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def guest(self):
        argv = runtime._guest_argv('/apk/lib', self.home / 'root', self.home,
            ['/opt/KiraAI/venv/bin/python', '-u', '/opt/KiraAI/main.py'], 'owned-run')
        start = argv.index('/usr/bin/env')
        end = argv.index('/bin/sh', start)
        self.assertEqual(argv[start + 1], '-i')
        assignments = argv[start + 2:end]
        self.assertEqual(len(assignments), len({item.split('=', 1)[0] for item in assignments}))
        return argv, dict(item.split('=', 1) for item in assignments)

    def test_missing_settings_disable_telemetry_and_preserve_process_markers(self):
        _, environment = self.guest()
        self.assertEqual(environment['KIRA_TELEMETRY_ENABLED'], '0')
        self.assertEqual(environment['KIRA_LINUX_RUN_ID'], 'owned-run')
        self.assertEqual(environment['KIRA_LINUX_HOST_PID'], str(os.getpid()))
        self.assertIn(environment['KIRA_NODE_AVAILABLE'], ('0', '1', 'unknown'))

    def test_values_remain_literal_argv_assignments_and_do_not_modify_shell(self):
        value = ' leading space=a=b; $(touch /tmp/forbidden) `id` "quoted" 中文 '
        self.save({'CUSTOM_VALUE': value, 'EMPTY': '', 'KIRA_TELEMETRY_ENABLED': 'false'})
        argv, environment = self.guest()
        self.assertEqual(environment['CUSTOM_VALUE'], value)
        self.assertEqual(environment['EMPTY'], '')
        self.assertEqual(environment['KIRA_TELEMETRY_ENABLED'], 'false')
        self.assertIn('CUSTOM_VALUE=' + value, argv)
        self.assertNotIn(value, runtime._BOOTSTRAP_SHELL)
        self.assertNotIn('CUSTOM_VALUE', os.environ)

    def test_each_new_guest_reads_the_current_file_and_user_can_override_defaults(self):
        self.save({'KIRA_TELEMETRY_ENABLED': 'false', 'CUSTOM_VALUE': 'first'})
        self.assertEqual(self.guest()[1]['CUSTOM_VALUE'], 'first')
        self.save({'KIRA_TELEMETRY_ENABLED': '1', 'CUSTOM_VALUE': 'second'})
        self.assertEqual(self.guest()[1]['CUSTOM_VALUE'], 'second')
        self.assertEqual(self.guest()[1]['KIRA_TELEMETRY_ENABLED'], '1')
        self.path.unlink()
        self.assertEqual(load_environment(self.home), {'KIRA_TELEMETRY_ENABLED': '0'})

    def test_invalid_settings_fail_without_echoing_values(self):
        secret = 'do-not-echo-this-value'
        cases = [[], None, {'BAD-NAME': secret}, {'9KEY': secret}, {'K' * 129: secret},
            {'VALUE': 1}, {'VALUE': None}, {'VALUE': secret + '\n'}, {'VALUE': secret + '\r'},
            {'VALUE': secret + '\0'}, {'KIRA_LINUX_RUN_ID': secret},
            {'KIRA_LINUX_HOST_PID': secret}, {'KIRA_NODE_AVAILABLE': secret},
            {f'KEY_{index}': secret for index in range(129)}]
        for value in cases:
            with self.subTest(value_type=type(value).__name__):
                self.save(value)
                with self.assertRaises(EnvironmentConfigurationError) as error:
                    load_environment(self.home)
                self.assertNotIn(secret, str(error.exception))
        for data in (b'\xff', b'{"PRIVATE":"' + secret.encode(), b'{"A":"1","A":"2"}', b' ' * 65537):
            with self.subTest(data_length=len(data)):
                self.path.write_bytes(data)
                with self.assertRaises(EnvironmentConfigurationError) as error:
                    load_environment(self.home)
                self.assertNotIn(secret, str(error.exception))

    def test_boundary_size_and_count_are_accepted(self):
        self.save({f'KEY_{index}': '' for index in range(128)})
        self.assertEqual(len(load_environment(self.home)), 129)  # includes the telemetry default
        prefix, suffix = b'{"VALUE":"', b'"}'
        self.path.write_bytes(prefix + b'x' * (65536 - len(prefix) - len(suffix)) + suffix)
        self.assertEqual(len(self.path.read_bytes()), 65536)
        self.assertEqual(len(load_environment(self.home)['VALUE']), 65536 - len(prefix) - len(suffix))

    def test_bad_settings_prevent_process_creation_and_do_not_change_ownership(self):
        self.save({'KIRA_LINUX_RUN_ID': 'must-not-replace-ownership'})
        with patch.object(runtime, '_process', None), patch.object(runtime, '_run_id', None), \
                patch.object(runtime, '_operation_cancel', threading.Event()), \
                patch.object(runtime.subprocess, 'Popen') as create:
            with self.assertRaises(EnvironmentConfigurationError):
                runtime._spawn('/apk/lib', self.home / 'root', self.home, {}, ['/bin/true'], io.BytesIO())
            create.assert_not_called()
            self.assertIsNone(runtime._process)
            self.assertIsNone(runtime._run_id)


class OfficialTelemetryEnvironmentTests(unittest.TestCase):
    def test_official_pinned_client_exits_before_network_initialization(self):
        """Execute official code with network constructors replaced by traps."""
        # Prepared runtime: KIRA_RUNTIME_BUILD, the build/runtime beside these tests (exists in the cache-root test copy), or the cache root.
        relative = Path('android-kira-host/build/runtime')
        cache = 'BaiduNetdiskDownload/kira这是缓存之类/workspaces/kira'
        candidates = [Path(os.environ['KIRA_RUNTIME_BUILD'])] if os.environ.get('KIRA_RUNTIME_BUILD') else []
        candidates += [Path(__file__).resolve().parents[2] / relative, Path('D:/') / cache / relative, Path('/mnt/d') / cache / relative]
        build = next((path for path in candidates if (path / 'assets/official-kira-seed.zip').is_file()
                      and (path / 'official-source-integrity.json').is_file()), None)
        if build is None:
            self.skipTest('Pinned official Kira v3.1.2 prepared source is unavailable: set KIRA_RUNTIME_BUILD, or run from the cache-root test copy')
        seed = build / 'assets/official-kira-seed.zip'
        integrity_path = build / 'official-source-integrity.json'
        integrity = json.loads(integrity_path.read_text(encoding='utf-8'))
        self.assertEqual(integrity['commit'], '89013745ba3281bdc248e704bbcb619417cf4f57')
        item = next(item for item in integrity['files'] if item['path'] == 'core/telemetry/client.py')
        with zipfile.ZipFile(seed) as package:
            source = package.read('KiraAI/core/telemetry/client.py')
        self.assertEqual(hashlib.sha256(source).hexdigest(), item['sha256'])
        self.assertEqual(len(source), item['size'])
        network = Mock(side_effect=AssertionError('Telemetry network constructor must not run'))
        logger = Mock()
        def module(name, **attributes):
            result = types.ModuleType(name)
            result.__dict__.update(attributes)
            return result
        stubs = {
            'httpx': module('httpx', AsyncClient=network, AsyncHTTPTransport=network),
            'core': module('core'),
            'core.config': module('core.config', VERSION='v3.1.2', KiraConfig=object),
            'core.db': module('core.db'),
            'core.db.service': module('core.db.service', DatabaseService=object),
            'core.logging_manager': module('core.logging_manager', get_logger=lambda *args: logger),
            'core.statistics': module('core.statistics', Statistics=object),
            'core.utils': module('core.utils'),
            'core.utils.path_utils': module('core.utils.path_utils', get_data_path=lambda: build),
            'core.utils.system_info': module('core.utils.system_info', collect_system_profile=Mock(), get_anonymous_machine_id=Mock()),
            'core.telemetry': module('core.telemetry'),
            'core.telemetry.geolocation': module('core.telemetry.geolocation', fetch_country_code=network),
            'core.telemetry.models': module('core.telemetry.models', TelemetryEvent=object, TelemetryEventType=object),
        }
        config = Mock()
        config.get_config.return_value = {'enabled': True}
        with patch.dict(sys.modules, stubs):
            official = module('core.telemetry.client')
            official.__package__ = 'core.telemetry'
            exec(compile(source, 'official-kira-seed.zip!/KiraAI/core/telemetry/client.py', 'exec'), official.__dict__)
            for disabled in ('0', 'false', 'no'):
                with self.subTest(disabled=disabled), patch.dict(os.environ, {'KIRA_TELEMETRY_ENABLED': disabled}):
                    client = official.TelemetryClient(None, config)
                    self.assertFalse(client.enabled)
                    asyncio.run(client.initialize())
                    self.assertIsNone(client._http_client)
                    self.assertIsNone(client._worker_task)
                    self.assertIsNone(client._init_task)
        network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
