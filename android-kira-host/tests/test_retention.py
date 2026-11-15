"""Retention rules confirmed on 2026-11-15 (plan R5C3): bounded logs, records and caches; user data untouched."""
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
import retention
import host_runtime as runtime
import napcat_runtime as napcat
from linux_installer import InstallationCancelled

MIB = 1024 * 1024


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'files'
        self.state = self.home / 'linux-runtime'
        self.root = self.state / 'rootfs-0123456789abcdef'
        self.napcat = self.state / 'napcat'
        for path in (self.root / 'opt/KiraAI', self.home / 'kira-core', self.napcat / 'attempts', self.napcat / 'downloads',
                     self.state / 'backups', self.state / 'panel-backups'):
            path.mkdir(parents=True)
        self.cancel = threading.Event()
        self.user_data = {self.home / 'kira-core/chat.db': b'chat', self.root / 'root/.config/QQ/login.db': b'qq',
                          self.state / 'backups/kira-20261115-120000-0000abcd.tar': b'backup',
                          self.root / 'opt/KiraAI/plugins/user.py': b'plugin'}
        for path, body in self.user_data.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)

    def apply(self, **kwargs):
        return retention.apply(self.home, self.root, self.cancel, **kwargs)

    def assert_user_data_untouched(self):
        for path, body in self.user_data.items():
            self.assertEqual(path.read_bytes(), body)

    def big(self, path, size, fill=b'x'):
        path.write_bytes(fill * size)

    def test_logs_over_the_limit_rotate_and_keep_two_older_copies(self):
        log = self.state / 'kira.log'
        for generation in (b'1', b'2', b'3', b'4'):
            self.big(log, retention.LOG_LIMIT + 1, generation)
            self.apply()
        self.assertFalse(log.exists())
        self.assertEqual((self.state / 'kira.log.1').read_bytes()[:1], b'4')
        self.assertEqual((self.state / 'kira.log.2').read_bytes()[:1], b'3')
        self.assertFalse((self.state / 'kira.log.3').exists())
        small = self.state / 'napcat.log'
        small.write_bytes(b'short log')
        self.apply()
        self.assertEqual(small.read_bytes(), b'short log')
        self.assert_user_data_untouched()

    def test_a_live_log_is_emptied_in_place_so_an_append_writer_keeps_working(self):
        log = self.state / 'kira.log'
        writer = log.open('ab', buffering=0)
        self.addCleanup(writer.close)
        writer.write(b'a' * (retention.LOG_LIMIT + 1))
        retention.truncate_log(log)
        writer.write(b'after')
        self.assertEqual(log.read_bytes(), b'after')
        self.assertEqual((self.state / 'kira.log.1').stat().st_size, retention.LOG_LIMIT + 1)

    def attempt(self, name, state, age):
        path = self.napcat / 'attempts' / name
        path.mkdir()
        (path / 'attempt.json').write_text(json.dumps({'state': state}))
        (path / 'install.sh').write_bytes(b'copy')
        moment = time.time() - age
        os.utime(path / 'attempt.json', (moment, moment))
        return path

    def test_only_three_recent_install_records_are_kept_and_failures_wait_for_the_user(self):
        names = {}
        for index, (state, age) in enumerate([('complete', 10), ('complete', 20), ('failed', 30),
                                              ('complete', 40), ('complete', 50), ('failed', 60), ('installing', 70)]):
            names[index] = self.attempt('%032x' % index, state, age).name
        stray = self.napcat / 'attempts/notes'; stray.mkdir()
        report = self.apply()
        remaining = {path.name for path in (self.napcat / 'attempts').iterdir()}
        # Newest three finished, the old failure kept for review, the running one untouched; two old completes go.
        self.assertEqual(remaining, {names[0], names[1], names[2], names[5], names[6], 'notes'})
        reasons = {(item['name'], item['reason']) for item in report['kept_for_review']}
        self.assertIn((names[5], 'failed'), reasons)
        self.assertIn(('notes', 'unrecognised'), reasons)

    def test_an_unfinished_installation_keeps_all_its_records_and_downloads(self):
        for index in range(6):
            self.attempt('%032x' % index, 'complete', 10 * index)
        old = self.napcat / 'downloads/NapCat.Shell-v4.18.42.zip'; old.write_bytes(b'old shell')
        (self.root / napcat.NAPCAT_PENDING[1:]).write_text(json.dumps(
            {'attempt': '%032x' % 5, 'shell': {'filename': 'NapCat.Shell-v4.18.42.zip'}}))
        self.apply(napcat_current=('NapCat.Shell-v4.9.43.zip',))
        self.assertEqual(len(list((self.napcat / 'attempts').iterdir())), 6)
        self.assertTrue(old.exists())

    def test_download_cache_keeps_only_the_current_version(self):
        downloads = self.napcat / 'downloads'
        files = {'NapCat.Shell-v4.18.42.zip': b'old', 'NapCat.Shell-v4.9.43.zip': b'current',
                 'napcat-install-' + 'a' * 40 + '.sh': b'old installer', napcat.INSTALLER.filename: b'installer',
                 'linuxqq_3.2.34-53644_arm64.deb': b'qq', 'NapCat.Shell-v4.9.44.zip.part': b'partial', 'notes.txt': b'?'}
        for name, body in files.items():
            (downloads / name).write_bytes(body)
        os.symlink('/etc/passwd', downloads / 'NapCat.Shell-v1.0.0.zip')
        report = self.apply(napcat_current=('NapCat.Shell-v4.9.43.zip', napcat.INSTALLER.filename, 'linuxqq_3.2.34-53644_arm64.deb'))
        self.assertEqual({p.name for p in downloads.iterdir()},
                         {'NapCat.Shell-v4.9.43.zip', napcat.INSTALLER.filename, 'linuxqq_3.2.34-53644_arm64.deb',
                          'notes.txt', 'NapCat.Shell-v1.0.0.zip'})
        self.assertEqual(report['reclaimed_bytes']['napcat_downloads'], len(b'old') + len(b'old installer') + len(b'partial'))
        self.assertTrue(os.path.exists('/etc/passwd'))

    def test_two_panel_backups_are_kept(self):
        for index in range(4):
            path = self.state / 'panel-backups' / ('%032x' % index)
            path.mkdir()
            (path / 'index.html').write_text('panel %d' % index)
            os.utime(path, (time.time() - 100 * index,) * 2)
        self.apply()
        self.assertEqual(sorted(p.name for p in (self.state / 'panel-backups').iterdir()), ['%032x' % 0, '%032x' % 1])

    def caches(self):
        apt = self.root / 'var/cache/apt/archives'
        (apt / 'partial').mkdir(parents=True)
        (apt / 'lock').write_bytes(b'')
        (apt / 'python3_3.11.deb').write_bytes(b'deb')
        (apt / 'partial/half.deb').write_bytes(b'half')
        pip = self.root / 'root/.cache/pip/http'; pip.mkdir(parents=True); (pip / 'wheel').write_bytes(b'wheel')
        npm = self.root / 'root/.npm/_cacache/content'; npm.mkdir(parents=True); (npm / 'blob').write_bytes(b'blob')
        return apt, pip, npm

    def test_package_caches_are_cleared_only_when_space_is_low(self):
        apt, pip, npm = self.caches()
        usage = shutil.disk_usage(self.root)
        plenty = usage._replace(free=retention.PACKAGE_CACHE_FREE * 2)
        with patch.object(retention.shutil, 'disk_usage', return_value=plenty):
            report = self.apply()
        self.assertFalse(report['package_caches_cleaned'])
        self.assertTrue((apt / 'python3_3.11.deb').exists() and (pip / 'wheel').exists() and (npm / 'blob').exists())
        low = usage._replace(free=retention.PACKAGE_CACHE_FREE - 1)
        with patch.object(retention.shutil, 'disk_usage', return_value=low):
            report = self.apply()
        self.assertTrue(report['package_caches_cleaned'])
        self.assertFalse((apt / 'python3_3.11.deb').exists() or (apt / 'partial/half.deb').exists())
        self.assertTrue((apt / 'lock').exists() and (apt / 'partial').is_dir())
        self.assertFalse((self.root / 'root/.cache/pip/http').exists() or (self.root / 'root/.npm/_cacache/content').exists())
        self.assert_user_data_untouched()

    def test_one_failing_category_does_not_stop_the_others_and_cancellation_stops_all(self):
        for index in range(4):
            path = self.state / 'panel-backups' / ('%032x' % index)
            path.mkdir()
            os.utime(path, (time.time() - 100 * index,) * 2)
        with patch.object(retention, '_logs', side_effect=PermissionError('locked')):
            report = self.apply()
        self.assertEqual(report['errors'], [{'kind': 'logs', 'error': 'PermissionError'}])
        self.assertEqual(len(list((self.state / 'panel-backups').iterdir())), 2)
        self.cancel.set()
        with self.assertRaises(InstallationCancelled):
            self.apply()
        self.assertTrue(json.loads((self.state / retention.REPORT).read_text())['errors'])

    def test_host_startup_keeps_baseline_downloads_when_nothing_is_installed(self):
        downloads = self.napcat / 'downloads'
        (downloads / napcat.SHELL.filename).write_bytes(b'baseline')
        (downloads / 'NapCat.Shell-v4.18.42.zip').write_bytes(b'superseded')
        with patch.object(runtime, '_cancel', self.cancel):
            runtime._apply_retention(self.home, self.root)
        self.assertTrue((downloads / napcat.SHELL.filename).exists())
        self.assertFalse((downloads / 'NapCat.Shell-v4.18.42.zip').exists())
        with patch.object(retention, 'apply', side_effect=RuntimeError('unexpected')), patch.object(runtime, '_cancel', self.cancel):
            runtime._apply_retention(self.home, self.root)
        self.assertEqual(json.loads((self.state / retention.REPORT).read_text())['errors'][0]['error'], 'RuntimeError')
        self.assert_user_data_untouched()


if __name__ == '__main__': unittest.main()
