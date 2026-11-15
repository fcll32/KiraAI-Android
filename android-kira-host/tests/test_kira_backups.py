import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
import kira_backups as backups
import host_runtime as runtime
from linux_installer import InstallationCancelled


class KiraBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'backups'
        self.folder.mkdir()
        self.cancel = threading.Event()

    def name(self, number):
        return f'kira-20261106-120000-{number:08x}.tar'

    def archive(self, path, unsafe=False):
        with tarfile.open(path, 'w') as archive:
            for name, body in [('opt/KiraAI/main.py', b'original program'), ('var/lib/kira/chat.db', b'user data')]:
                info = tarfile.TarInfo(name)
                info.size = len(body)
                archive.addfile(info, io.BytesIO(body))
            if unsafe:
                info = tarfile.TarInfo('../outside')
                info.size = 1
                archive.addfile(info, io.BytesIO(b'x'))

    def complete(self, number, *, mtime_ns=None):
        name = self.name(number)
        partial = self.folder / (name + '.partial')
        self.archive(partial)
        if mtime_ns is not None:
            os.utime(partial, ns=(mtime_ns, mtime_ns))
        with patch.object(backups.time, 'time', return_value=number):
            return backups.finalize_backup(self.folder, name, self.cancel)

    def test_third_verified_backup_retires_only_oldest_complete_pair(self):
        for number in (1, 2, 3):
            record, warnings = self.complete(number)
            self.assertFalse(warnings)
            self.assertEqual(record['state'], 'complete')
        self.assertEqual({path.name for path in self.folder.glob('*.tar')}, {self.name(2), self.name(3)})
        self.assertFalse((self.folder / (self.name(1) + '.json')).exists())
        for number in (2, 3):
            record = json.loads((self.folder / (self.name(number) + '.json')).read_text())
            self.assertEqual(record['size'], (self.folder / self.name(number)).stat().st_size)
            self.assertEqual(len(record['sha256']), 64)

    def test_failed_or_cancelled_new_backup_preserves_old_bytes(self):
        self.complete(1); self.complete(2)
        before = {path.name: path.read_bytes() for path in self.folder.iterdir()}
        bad = self.folder / (self.name(3) + '.partial')
        bad.write_bytes(b'incomplete')
        with self.assertRaises(ValueError):
            backups.finalize_backup(self.folder, self.name(3), self.cancel)
        self.archive(bad)
        self.cancel.set()
        with self.assertRaises(InstallationCancelled):
            backups.finalize_backup(self.folder, self.name(3), self.cancel)
        for name, data in before.items():
            self.assertEqual((self.folder / name).read_bytes(), data)
        self.assertFalse((self.folder / self.name(3)).exists())

    def test_legacy_is_validated_before_retirement_unknown_and_partial_stay(self):
        old = self.folder / self.name(1)
        self.archive(old)
        malformed = self.folder / self.name(9)
        malformed.write_bytes(b'not a backup')
        unknown = self.folder / 'notes.tar'; unknown.write_bytes(b'keep')
        partial = self.folder / 'other.partial'; partial.write_bytes(b'keep')
        self.complete(2); self.complete(3)
        self.assertTrue(malformed.exists())
        self.assertEqual(unknown.read_bytes(), b'keep')
        self.assertEqual(partial.read_bytes(), b'keep')
        self.assertTrue((old.with_name(old.name + '.json')).exists() or not old.exists())

    def test_same_stat_damage_does_not_displace_older_verified_backup(self):
        self.complete(1)
        # Use a timestamp that also round-trips through WSL's Windows drive mount.
        # Set it before finalization so the genuine receipt still matches the file.
        self.complete(2, mtime_ns=1_700_000_000_000_000_000)
        damaged = self.folder / self.name(2)
        before = damaged.stat()
        with damaged.open('r+b') as stream:
            stream.seek(512)
            stream.write(b'X')
        os.utime(damaged, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = damaged.stat()
        self.assertEqual((before.st_ino, before.st_size, before.st_mtime_ns),
                         (after.st_ino, after.st_size, after.st_mtime_ns))
        receipt = damaged.with_suffix('.tar.json')
        recorded = receipt.read_bytes()
        _, warnings = self.complete(3)
        self.assertTrue(warnings)
        self.assertTrue(damaged.is_file())
        self.assertEqual(receipt.read_bytes(), recorded)
        self.assertTrue((self.folder / self.name(1)).is_file())
        self.assertTrue((self.folder / self.name(3)).is_file())

    def test_unsafe_paths_cannot_target_other_files(self):
        outside = Path(self.temp.name) / 'outside'; outside.write_bytes(b'preserve')
        with self.assertRaises(ValueError):
            backups.finalize_backup(self.folder, '../outside', self.cancel)
        unsafe = self.folder / (self.name(1) + '.partial')
        self.archive(unsafe, unsafe=True)
        with self.assertRaises(ValueError):
            backups.finalize_backup(self.folder, self.name(1), self.cancel)
        self.assertEqual(outside.read_bytes(), b'preserve')

    def test_symbolic_links_are_not_followed_or_retired(self):
        outside = Path(self.temp.name) / 'outside.tar'
        self.archive(outside)
        link = self.folder / self.name(9)
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest('Platform does not grant symbolic-link creation')
        before = outside.read_bytes()
        self.complete(1); self.complete(2); self.complete(3)
        self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_bytes(), before)
        partial = self.folder / (self.name(4) + '.partial')
        partial.symlink_to(outside)
        with self.assertRaises(ValueError):
            backups.finalize_backup(self.folder, self.name(4), self.cancel)
        self.assertEqual(outside.read_bytes(), before)

    def test_retirement_failure_keeps_new_backup_and_reports_warning(self):
        self.complete(1); self.complete(2)
        blocked = self.folder / self.name(1)
        unlink = Path.unlink
        def refuse(path, *args, **kwargs):
            if path == blocked:
                raise PermissionError('test lock')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', refuse):
            record, warnings = self.complete(3)
        self.assertTrue(warnings)
        self.assertTrue(blocked.exists())
        self.assertEqual(record['state'], 'complete')
        self.assertTrue((self.folder / self.name(3)).is_file())

    def test_host_rejects_a_linked_backup_directory_before_running_tar(self):
        home = Path(self.temp.name) / 'app-files'
        home.mkdir()
        with patch.object(Path, 'is_symlink', return_value=True), patch.object(runtime, '_command') as command:
            with self.assertRaisesRegex(ValueError, 'private app files'):
                runtime._backup_kira('/native', home / 'rootfs', home, {}, io.BytesIO())
            command.assert_not_called()


if __name__ == '__main__': unittest.main()
