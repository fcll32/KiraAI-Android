"""Data preservation, interrupted installation and local endpoint boundaries."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
import napcat_runtime as napcat
import storage_space
from linux_installer import Artifact, InstallationCancelled


class NapCatRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home / 'guest'
        self.root.mkdir()
        self.cancel = threading.Event()
        self.publish = Mock()
        self.commands = []
        self.requests = []
        self.version = napcat.SHELL_VERSION
        self.sources = {}
        self.archive = self.make_archive(self.version)
        self.installer = self.home / 'installer.sh'
        self.installer.write_bytes(b'#!/bin/bash\n# official fixture\n')
        self.qq = self.home / 'QQ.deb'
        self.qq.write_bytes(b'official qq fixture')
        self.addCleanup(patch.stopall)
        patch.object(napcat, '_arch', return_value='arm64').start()
        patch.object(napcat, 'download', side_effect=self.fake_download).start()

    def make_archive(self, version):
        path = self.home / ('shell-' + version + '.zip')
        files = {
            'napcat.mjs': ('const release = "' + version.lstrip('v') + '" || "1.0.0-dev";').encode(),
            'package.json': b'{"name":"napcat","version":"0.0.1"}',
            'static/index.html': b'<html>official</html>',
            'static/assets/official.js': b'official bundled asset',
            'native/packet/MoeHoo.linux.arm64.node': b'official packet addon',
            'native/napi2native/napi2native.linux.arm64.node': b'official native addon',
            'config/napcat.json': b'{"consoleLog":true}',
        }
        with zipfile.ZipFile(path, 'w') as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        self.sources[version] = path
        return path

    def fake_download(self, artifact, cache, cancel, progress, **kwargs):
        napcat.checkpoint(cancel)
        self.requests.append(artifact)
        progress(artifact.size, artifact.size)
        if artifact == napcat.INSTALLER:
            return self.installer
        if artifact in napcat.QQ_ARTIFACTS.values():
            return self.qq
        return self.sources[self.version]

    def install_fixture(self, version=None, qq_version=napcat.QQ_VERSION):
        version = version or self.version
        code = napcat._path(self.root, napcat._NAPCAT)
        code.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.sources[version]) as archive:
            for item in archive.infolist():
                target = code / item.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                if item.filename.startswith(('config/', 'plugins/')) and target.exists():
                    continue
                target.write_bytes(archive.read(item))
        qq = napcat._path(self.root, napcat._QQ)
        (qq / 'qq').write_bytes(b'official QQ binary')
        (qq / 'resources/app/package.json').write_text(json.dumps({
            'version': qq_version, 'main': './loadNapCat.js'}), encoding='utf-8')
        (qq / 'resources/app/loadNapCat.js').write_bytes(b'official loader')

    def fake_command(self, argv, log, timeout=1800):
        napcat.checkpoint(self.cancel)
        self.commands.append(argv)
        if 'napcat-dependencies' in argv:
            guest = argv[argv.index('napcat-dependencies') + 1]
            path = self.home / 'linux-runtime' / guest.removeprefix('/run/kira-host/')
            path.write_text('\n'.join(napcat._DYNAMIC_PACKAGES) + '\n', encoding='utf-8')
        if 'napcat-install' in argv:
            current = napcat.metadata(self.root)['qq_version'] or napcat.QQ_VERSION
            self.install_fixture(qq_version=current)

    def ensure(self, **kwargs):
        return napcat.ensure(self.root, self.home, self.fake_command, Mock(),
                             self.cancel, self.publish, **kwargs)

    def latest(self, version):
        self.version = version
        path = self.make_archive(version)
        artifact = Artifact('NapCat.Shell-' + version + '.zip',
            'https://github.com/xxynet/NapCatQQ/releases/download/' + version + '/NapCat.Shell.zip',
            hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size)
        patch.object(napcat, '_latest_shell', return_value=(version, artifact)).start()
        return artifact

    def test_verified_install_checks_program_files_and_uses_local_authenticated_defaults(self):
        result = self.ensure()
        self.assertEqual(result, {'installed': True, 'version': self.version, 'qq_version': napcat.QQ_VERSION})
        webui = json.loads(napcat.token_path(self.root).read_text())
        self.assertEqual((webui['host'], webui['port']), ('127.0.0.1', 6099))
        self.assertGreaterEqual(len(webui['token']), 32)
        onebot = json.loads((napcat.token_path(self.root).parent / 'onebot11.json').read_text())
        endpoint = onebot['network']['websocketServers'][0]
        self.assertEqual((endpoint['host'], endpoint['messagePostFormat']), ('127.0.0.1', 'array'))
        self.assertTrue(endpoint['token'])
        self.assertTrue(any(argv[0] == '/usr/bin/apt-get' and 'install' in argv for argv in self.commands))
        installer = next(argv for argv in self.commands if 'napcat-install' in argv)
        self.assertIn('--skip-deps', installer[2])
        self.assertNotIn('--force', ' '.join(installer))
        self.assertFalse((self.root / '.napcat-host-pending.json').exists())

    def test_insufficient_storage_does_not_run_downloads_or_guest_installation(self):
        with patch.object(storage_space.shutil, 'disk_usage', return_value=Mock(free=0)), \
                self.assertRaises(storage_space.SpaceUnavailable):
            self.ensure()
        self.assertEqual(self.requests, [])
        self.assertEqual(self.commands, [])
        self.assertFalse((self.root / '.napcat-host-pending.json').exists())
        report = json.loads((self.home / 'linux-runtime' / storage_space.REPORT).read_text())
        self.assertEqual(report['decision'], 'insufficient')

    def test_new_apk_keeps_existing_official_update_and_account_data(self):
        self.make_archive('v9.0.0')
        self.install_fixture('v9.0.0', '9.0.0-99999')
        data = napcat._path(self.root, napcat._DATA)
        data.mkdir(parents=True)
        (data / 'login.db').write_bytes(b'keep login')
        result = self.ensure()
        self.assertEqual(result['version'], 'v9.0.0')
        self.assertEqual(self.requests, [])
        self.assertEqual(self.commands, [])
        self.assertEqual((data / 'login.db').read_bytes(), b'keep login')

    def test_update_backs_up_program_config_plugins_and_qq_without_downgrading_qq(self):
        self.install_fixture(qq_version='9.0.0-99999')
        napcat.configure_local(self.root)
        token = napcat.token_path(self.root).read_bytes()
        code = napcat._path(self.root, napcat._NAPCAT)
        plugin = code / 'plugins/custom.py'
        plugin.parent.mkdir()
        plugin.write_bytes(b'user plugin')
        data = napcat._path(self.root, napcat._DATA)
        data.mkdir(parents=True)
        (data / 'login.db').write_bytes(b'user login')
        self.latest('v4.18.43')
        result = self.ensure(updating=True)
        self.assertEqual(result['qq_version'], '9.0.0-99999')
        self.assertFalse(any(item in napcat.QQ_ARTIFACTS.values() for item in self.requests))
        self.assertEqual(napcat.token_path(self.root).read_bytes(), token)
        self.assertEqual(plugin.read_bytes(), b'user plugin')
        backup = next((self.home / 'linux-runtime/napcat/backups').iterdir())
        self.assertEqual((backup / 'qq-data/login.db').read_bytes(), b'user login')
        self.assertEqual((backup / 'program/opt/QQ/resources/app/app_launcher/napcat/plugins/custom.py').read_bytes(), b'user plugin')
        self.assertEqual(json.loads((backup / 'backup.json').read_text())['state'], 'complete')

    def test_failed_partial_update_requires_reinstallation_in_a_new_attempt(self):
        self.install_fixture()
        self.latest('v4.18.43')
        original_command = self.fake_command
        attempts = []
        def fail_once(argv, log, timeout=1800):
            original_command(argv, log, timeout)
            if 'napcat-install' in argv:
                attempts.append(argv[-1])
                if len(attempts) == 1:
                    # Core/version now look complete while an unrelated asset
                    # is damaged: a pending journal must prevent a fast return.
                    napcat._path(self.root, napcat._NAPCAT + '/static/assets/official.js').write_bytes(b'damaged')
                    raise RuntimeError('interrupted official copy')
        self.fake_command = fail_once
        with self.assertRaises(RuntimeError):
            self.ensure(updating=True)
        self.assertTrue((self.root / '.napcat-host-pending.json').exists())
        self.ensure()
        self.assertEqual(len(attempts), 2)
        self.assertNotEqual(*attempts)
        records = [json.loads(path.read_text()) for path in (self.home / 'linux-runtime/napcat/attempts').glob('*/attempt.json')]
        self.assertEqual({record['state'] for record in records}, {'failed', 'complete'})
        self.assertEqual(napcat._path(self.root, napcat._NAPCAT + '/static/assets/official.js').read_bytes(), b'official bundled asset')

    def test_cancelled_backup_never_runs_installer_or_restores_user_data(self):
        self.install_fixture()
        self.latest('v4.18.43')
        original = napcat._copy
        def cancel_copy(source, target, cancel):
            if '/backups/' in str(target).replace('\\', '/'):
                cancel.set()
            return original(source, target, cancel)
        with patch.object(napcat, '_copy', side_effect=cancel_copy):
            with self.assertRaises(InstallationCancelled):
                self.ensure(updating=True)
        self.assertFalse(any('napcat-install' in argv for argv in self.commands))
        self.assertEqual(napcat.metadata(self.root)['version'], napcat.SHELL_VERSION)
        backup = next((self.home / 'linux-runtime/napcat/backups').iterdir())
        self.assertEqual(json.loads((backup / 'backup.json').read_text())['state'], 'copying')

    def test_copy_keeps_body_mode_and_times_when_security_labels_cannot_be_copied(self):
        source = self.home / 'official.sh'
        payload = b'#!/bin/bash\nprintf official-installer\n'
        source.write_bytes(payload)
        for mode in (0o750, 0o440):
            with self.subTest(mode=mode):
                destination = self.home / ('attempt-' + str(mode) + '.sh')
                os.utime(source, ns=(1_700_000_000_000_000_000, 1_700_000_001_000_000_000))
                os.chmod(source, mode)
                before = source.stat()
                # Linux invokes this while copying Android security labels.
                # Emulate its permission failure on Windows as well as Linux.
                with patch.object(shutil, '_copyxattr', side_effect=PermissionError('security label denied'), create=True):
                    napcat._copy(source, destination, self.cancel)
                copied = destination.stat()
                self.assertEqual(stat.S_IMODE(copied.st_mode), stat.S_IMODE(before.st_mode))
                self.assertEqual((copied.st_atime_ns, copied.st_mtime_ns), (before.st_atime_ns, before.st_mtime_ns))
                self.assertEqual(destination.read_bytes(), payload)

    def test_backup_keeps_tree_data_and_directory_metadata_without_security_labels(self):
        self.install_fixture()
        program = napcat._path(self.root, napcat._INSTALL)
        data = napcat._path(self.root, napcat._DATA)
        data.mkdir(parents=True)
        (data / 'login.db').write_bytes(b'preserve QQ login bytes')
        os.chmod(program, 0o750)
        os.utime(program, ns=(1_700_000_002_000_000_000, 1_700_000_003_000_000_000))
        before = program.stat()
        runtime = self.home / 'linux-runtime/napcat'
        with patch.object(shutil, '_copyxattr', side_effect=PermissionError('directory label denied'), create=True):
            backup = napcat._backup(self.root, runtime, self.cancel)
        copied = (backup / 'program').stat()
        self.assertEqual(stat.S_IMODE(copied.st_mode), stat.S_IMODE(before.st_mode))
        self.assertEqual(copied.st_mtime_ns, before.st_mtime_ns)
        self.assertEqual((backup / 'qq-data/login.db').read_bytes(), b'preserve QQ login bytes')
        self.assertEqual((backup / 'program/opt/QQ/qq').read_bytes(), b'official QQ binary')
        self.assertEqual(json.loads((backup / 'backup.json').read_text())['state'], 'complete')

    @unittest.skipIf(os.name == 'nt', 'Requires POSIX symbolic links without Windows link privileges')
    def test_backup_preserves_symbolic_links_instead_of_following_them(self):
        self.install_fixture()
        program = napcat._path(self.root, napcat._INSTALL)
        external = self.home / 'outside-program'
        external.mkdir()
        (external / 'private.txt').write_bytes(b'outside backup tree')
        (program / 'directory-link').symlink_to(external, target_is_directory=True)
        (program / 'dangling-link').symlink_to('missing-target')
        backup = napcat._backup(self.root, self.home / 'linux-runtime/napcat', self.cancel)
        self.assertTrue((backup / 'program/directory-link').is_symlink())
        self.assertEqual(os.readlink(backup / 'program/directory-link'), str(external))
        self.assertTrue((backup / 'program/dangling-link').is_symlink())
        self.assertEqual(os.readlink(backup / 'program/dangling-link'), 'missing-target')
        self.assertEqual((external / 'private.txt').read_bytes(), b'outside backup tree')

    def test_backup_file_permission_failure_does_not_report_complete_or_run_update(self):
        self.install_fixture()
        self.latest('v4.18.43')
        original = napcat._path(self.root, napcat._QQ + '/qq').read_bytes()
        with patch.object(napcat, '_copy', side_effect=PermissionError('file body cannot be read')):
            with self.assertRaises(PermissionError):
                self.ensure(updating=True)
        backup = next((self.home / 'linux-runtime/napcat/backups').iterdir())
        self.assertEqual(json.loads((backup / 'backup.json').read_text())['state'], 'copying')
        self.assertFalse(any('napcat-install' in argv for argv in self.commands))
        self.assertEqual(napcat._path(self.root, napcat._QQ + '/qq').read_bytes(), original)

    def test_installer_exit_zero_cannot_hide_missing_official_asset(self):
        original = self.fake_command
        def incomplete(argv, log, timeout=1800):
            original(argv, log, timeout)
            if 'napcat-install' in argv:
                napcat._path(self.root, napcat._NAPCAT + '/static/assets/official.js').unlink()
        self.fake_command = incomplete
        with self.assertRaisesRegex(ValueError, 'incomplete program file'):
            self.ensure()
        self.assertTrue((self.root / '.napcat-host-pending.json').exists())

    def test_existing_tokens_and_business_settings_are_preserved(self):
        self.install_fixture()
        path = napcat.token_path(self.root)
        path.write_text(json.dumps({'token': 'original authentication', 'host': '0.0.0.0',
                                    'port': 9999, 'loginRate': 7, 'autoLoginAccount': '12345'}))
        napcat.configure_local(self.root)
        value = json.loads(path.read_text())
        self.assertEqual(value['token'], 'original authentication')
        self.assertEqual((value['autoLoginAccount'], value['loginRate']), ('12345', 7))
        self.assertEqual((value['host'], value['port']), ('127.0.0.1', 6099))
        saved = path.read_bytes()
        napcat.configure_local(self.root)
        self.assertEqual(path.read_bytes(), saved)

    def test_enabled_external_account_endpoint_blocks_without_rewriting_config(self):
        self.install_fixture()
        path = napcat.token_path(self.root).parent / 'onebot11_12345.json'
        content = json.dumps({'network': {'websocketServers': [{
            'enable': True, 'host': '0.0.0.0', 'token': 'preserve'}]}})
        path.write_text(content)
        with self.assertRaisesRegex(ValueError, 'non-loopback'):
            napcat.configure_local(self.root)
        self.assertEqual(path.read_text(), content)
        self.assertFalse(napcat.token_path(self.root).exists())

    def test_disabled_external_endpoint_preserved_but_enabled_remote_client_and_empty_auth_rejected(self):
        self.install_fixture()
        path = napcat.token_path(self.root).parent / 'onebot11_12345.json'
        row = {'enable': False, 'host': '0.0.0.0', 'token': ''}
        value = {'network': {'httpServers': [row]}}
        path.write_text(json.dumps(value))
        napcat.configure_local(self.root)
        self.assertEqual(json.loads(path.read_text()), value)
        for network in ({'websocketClients': [{'enable': True, 'url': 'ws://example.com', 'token': 'auth'}]},
                        {'websocketServers': [{'enable': True, 'host': '127.0.0.1', 'token': ''}]}):
            path.write_text(json.dumps({'network': network}))
            with self.assertRaises(ValueError):
                napcat.configure_local(self.root)

    def test_metadata_does_not_confuse_monorepo_version_with_running_release(self):
        self.install_fixture()
        self.assertEqual(napcat.metadata(self.root)['version'], napcat.SHELL_VERSION)
        napcat._path(self.root, napcat._NAPCAT + '/native/packet/MoeHoo.linux.arm64.node').unlink()
        self.assertFalse(napcat.metadata(self.root)['installed'])

    def test_transfer_progress_reports_bytes_actual_increment_speed_and_closes_on_cancel(self):
        artifact = Artifact('sample.zip', 'https://example.com/sample.zip', 'a' * 64, 100)
        def transfer(artifact, cache, cancel, progress, **kwargs):
            progress(20, 100)
            progress(60, 100)
            raise InstallationCancelled('cancelled')
        with patch.object(napcat, 'download', side_effect=transfer), \
                patch.object(napcat.time, 'monotonic', side_effect=[10.0, 11.0]):
            with self.assertRaises(InstallationCancelled):
                napcat._get(artifact, self.home, self.cancel, self.publish)
        events = self.publish.call_args_list
        self.assertTrue(all(call.args[2] is None for call in events))
        measured = events[-2].kwargs
        self.assertEqual((measured['transfer_done_bytes'], measured['transfer_total_bytes']), (60, 100))
        self.assertEqual(measured['transfer_bytes_per_second'], 40.0)
        self.assertEqual(measured['transfer_percent'], 60.0)
        self.assertFalse(events[-1].kwargs['transfer_active'])
        self.assertFalse(events[-1].kwargs['transfer_complete'])


if __name__ == '__main__':
    unittest.main()
