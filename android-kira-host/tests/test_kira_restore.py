import errno
import hashlib
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
from storage_space import SpaceUnavailable

REQUIRED = {'main.py': b'restored main', 'requirements.txt': b'aiohttp==3\n', 'core/supervisor.py': b'supervisor',
            'core/launcher.py': b'launcher', 'webui/app.py': b'app'}


class Killed(BaseException):
    """Stands in for the process dying: nothing may catch it and tidy up."""


class KiraRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'files'
        self.state = self.home / 'linux-runtime'
        self.root = self.state / 'rootfs-0123456789abcdef'
        self.program = self.root / 'opt/KiraAI'
        self.data = self.home / 'kira-core'
        self.folder = self.state / 'backups'
        self.work = self.state / 'restore'
        for path in (self.program / 'venv/bin', self.program / 'core', self.data / 'plugins', self.folder):
            path.mkdir(parents=True)
        (self.program / 'main.py').write_bytes(b'current main')
        (self.program / 'core/supervisor.py').write_bytes(b'current supervisor')
        (self.program / 'venv/bin/python').write_bytes(b'interpreter')
        (self.program / 'venv/.kira-requirements.json').write_text('{"sha256": "old"}')
        (self.program / 'venv/.kira-venv.json').write_text('{"version": 1}')
        (self.data / 'chat.db').write_bytes(b'current chat')
        (self.data / 'plugins/new.py').write_bytes(b'installed after the backup')
        self.cancel = threading.Event()
        self.outside = Path(self.temp.name) / 'outside'
        self.outside.write_bytes(b'preserve')

    def name(self, number):
        return f'kira-20261115-120000-{number:08x}.tar'

    def members(self, archive, *, program=REQUIRED, data=None, extra=()):
        def directory(name):
            info = tarfile.TarInfo(name); info.type = tarfile.DIRTYPE; info.mode = 0o755
            archive.addfile(info)
        def regular(name, body):
            info = tarfile.TarInfo(name); info.size = len(body); info.mode = 0o644
            archive.addfile(info, io.BytesIO(body))
        directory('opt/KiraAI'); directory('opt/KiraAI/core'); directory('opt/KiraAI/webui')
        for name, body in program.items():
            regular('opt/KiraAI/' + name, body)
        directory('var/lib/kira')
        for name, body in (data if data is not None else {'chat.db': b'backed-up chat', 'config/webui.json': b'{}'}).items():
            regular('var/lib/kira/' + name, body)
        for info, body in extra:
            archive.addfile(info, io.BytesIO(body) if body is not None else None)

    def backup(self, number, **content):
        """A backup published through the normal verified path."""
        name = self.name(number)
        with tarfile.open(self.folder / (name + '.partial'), 'w') as archive:
            self.members(archive, **content)
        with patch.object(backups.time, 'time', return_value=number):
            record, _ = backups.finalize_backup(self.folder, name, self.cancel)
        return name, record['sha256']

    def forged(self, number, extra):
        """An archive with a matching completion record whose content never passed inspection."""
        name = self.name(number)
        path = self.folder / name
        with tarfile.open(path, 'w') as archive:
            self.members(archive, extra=extra)
        info = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.folder / (name + '.json')).write_text(json.dumps({
            'schema': 'kira-backup/v1', 'state': 'complete', 'filename': name, 'size': info.st_size, 'sha256': digest,
            'archive_mtime_ns': info.st_mtime_ns, 'archive_inode': info.st_ino, 'created_at_unix_ms': number}))
        return name, digest

    def restore(self, name, digest, **kwargs):
        return backups.restore_backup(self.home, self.folder, name, digest, self.program, self.data, self.cancel, **kwargs)

    def snapshot(self):
        trees = {}
        for label, base in (('program', self.program), ('data', self.data)):
            for path in sorted(base.rglob('*')):
                if path.is_file() and not path.is_symlink():
                    trees[label + '/' + path.relative_to(base).as_posix()] = path.read_bytes()
        return trees

    def assert_untouched(self, before):
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(list(self.work.glob('stage-*')) if self.work.exists() else [])
        self.assertFalse(list(self.work.glob('previous-*')) if self.work.exists() else [])
        self.assertEqual(self.outside.read_bytes(), b'preserve')

    def link(self, name, target, kind=tarfile.SYMTYPE):
        info = tarfile.TarInfo(name); info.type = kind; info.linkname = target
        return info, None

    def test_verified_backup_replaces_program_and_data_and_keeps_what_it_replaced(self):
        name, digest = self.backup(1)
        result = self.restore(name, digest)
        self.assertEqual((self.program / 'main.py').read_bytes(), b'restored main')
        self.assertEqual((self.data / 'chat.db').read_bytes(), b'backed-up chat')
        self.assertFalse((self.data / 'plugins/new.py').exists())
        # The Python environment belongs to the base system and follows the program directory.
        self.assertEqual((self.program / 'venv/bin/python').read_bytes(), b'interpreter')
        self.assertTrue(runtime._kira_installed(self.root))
        previous = self.home / result['previous']
        self.assertEqual((previous / 'program/main.py').read_bytes(), b'current main')
        self.assertEqual((previous / 'data/chat.db').read_bytes(), b'current chat')
        self.assertEqual((previous / 'data/plugins/new.py').read_bytes(), b'installed after the backup')
        self.assertFalse((previous / 'program/venv').exists())
        journal = json.loads((self.work / 'journal.json').read_text())
        self.assertEqual((journal['state'], journal['sha256']), ('completed', digest))
        self.assertFalse(list(self.work.glob('stage-*')))
        self.assertTrue((self.folder / name).is_file())

    def test_a_later_restore_keeps_only_the_most_recently_replaced_trees(self):
        first, first_digest = self.backup(1)
        second, second_digest = self.backup(2, data={'chat.db': b'second backup'})
        self.restore(first, first_digest)
        result = self.restore(second, second_digest)
        kept = list(self.work.glob('previous-*'))
        self.assertEqual([path.relative_to(self.home).as_posix() for path in kept], [result['previous']])
        self.assertEqual((kept[0] / 'data/chat.db').read_bytes(), b'backed-up chat')
        self.assertEqual((self.data / 'chat.db').read_bytes(), b'second backup')

    def test_same_stat_damage_and_a_swapped_archive_are_refused(self):
        name, digest = self.backup(1)
        before = self.snapshot()
        archive = self.folder / name
        stat = archive.stat()
        with archive.open('r+b') as stream:
            stream.seek(600)
            stream.write(b'X')
        os.utime(archive, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.restore(name, digest)
        self.assert_untouched(before)
        other, _ = self.backup(2, data={'chat.db': b'a different backup'})
        with self.assertRaisesRegex(ValueError, 'changed after it was chosen'):
            self.restore(other, digest)
        self.assert_untouched(before)

    def test_links_inside_the_restored_trees_are_kept(self):
        name, digest = self.forged(1, [
            self.link('var/lib/kira/config/current.json', 'webui.json'),
            self.link('var/lib/kira/config/up.db', '../chat.db'),
            self.link('var/lib/kira/guest-absolute', '/var/lib/kira/chat.db'),
            self.link('opt/KiraAI/core/twin.py', 'opt/KiraAI/core/supervisor.py', tarfile.LNKTYPE)])
        self.restore(name, digest)
        self.assertEqual(os.readlink(self.data / 'config/current.json'), 'webui.json')
        self.assertEqual(os.readlink(self.data / 'config/up.db'), '../chat.db')
        self.assertEqual(os.readlink(self.data / 'guest-absolute'), '/var/lib/kira/chat.db')
        self.assertEqual((self.program / 'core/twin.py').read_bytes(), b'supervisor')
        self.assertEqual(self.outside.read_bytes(), b'preserve')

    def test_links_and_paths_that_leave_the_restored_trees_are_refused(self):
        device = tarfile.TarInfo('var/lib/kira/node'); device.type = tarfile.CHRTYPE
        clash = tarfile.TarInfo('var/lib/kira/chat.db'); clash.size = 1
        through = tarfile.TarInfo('var/lib/kira/escape/planted'); through.size = 1
        cases = {
            'relative escape': [self.link('var/lib/kira/config/out', '../../outside')],
            'absolute host path': [self.link('var/lib/kira/passwd', '/etc/passwd')],
            'absolute path to the real file': [self.link('var/lib/kira/real', str(self.outside))],
            'hard link to another tree': [self.link('var/lib/kira/twin', 'opt/KiraAI/main.py', tarfile.LNKTYPE)],
            'hard link outside': [self.link('var/lib/kira/twin', '../outside', tarfile.LNKTYPE)],
            'device node': [(device, None)],
            'duplicate file': [(clash, b'x')],
            'write through a link': [self.link('var/lib/kira/escape', '.'), (through, b'x')],
            'parent traversal': [(tarfile.TarInfo('var/lib/kira/../../../outside'), b'')],
            'foreign root': [(tarfile.TarInfo('etc/hosts'), b'')],
        }
        before = self.snapshot()
        for number, (label, extra) in enumerate(cases.items(), 1):
            with self.subTest(label):
                name, digest = self.forged(number, extra)
                with self.assertRaises(ValueError):
                    self.restore(name, digest)
                self.assert_untouched(before)

    def test_incomplete_program_and_bundled_environment_are_refused(self):
        before = self.snapshot()
        partial = {key: value for key, value in REQUIRED.items() if key != 'core/launcher.py'}
        name, digest = self.backup(1, program=partial)
        with self.assertRaisesRegex(ValueError, 'complete Kira program'):
            self.restore(name, digest)
        self.assert_untouched(before)
        name, digest = self.backup(2, program={**REQUIRED, 'venv/bin/python': b'stale'})
        with self.assertRaisesRegex(ValueError, 'Python environment'):
            self.restore(name, digest)
        self.assert_untouched(before)

    def test_cancellation_and_a_full_disk_publish_nothing(self):
        name, digest = self.backup(1)
        before = self.snapshot()
        self.cancel.set()
        with self.assertRaises(InstallationCancelled):
            self.restore(name, digest)
        self.assert_untouched(before)
        self.cancel.clear()
        # Cancellation that arrives while files are being unpacked.
        mkdir, calls = os.mkdir, []
        def cancel_later(path, *args, **kwargs):
            calls.append(path)
            if len(calls) == 4:
                self.cancel.set()
            return mkdir(path, *args, **kwargs)
        with patch.object(backups.os, 'mkdir', cancel_later), self.assertRaises(InstallationCancelled):
            self.restore(name, digest)
        self.assert_untouched(before)
        self.cancel.clear()
        fsync = os.fsync
        def full(descriptor):
            raise OSError(errno.ENOSPC, 'No space left on device')
        with patch.object(backups.os, 'fsync', full), self.assertRaises(OSError):
            self.restore(name, digest)
        self.assertIs(os.fsync, fsync)
        self.assert_untouched(before)
        # The same archive still restores once the obstacle is gone.
        self.restore(name, digest)
        self.assertEqual((self.data / 'chat.db').read_bytes(), b'backed-up chat')

    def test_a_swap_that_fails_part_way_is_rolled_back(self):
        name, digest = self.backup(1)
        before = self.snapshot()
        for failing in (1, 2, 3, 4):
            with self.subTest(rename=failing):
                rename, calls = os.rename, []
                def refuse(source, destination):
                    calls.append(source)
                    if len(calls) == failing:
                        raise OSError(errno.EIO, 'I/O error')
                    return rename(source, destination)
                with patch.object(backups.os, 'rename', refuse), self.assertRaises(OSError):
                    self.restore(name, digest)
                self.assert_untouched(before)
                self.assertEqual(json.loads((self.work / 'journal.json').read_text())['state'], 'rolled_back')

    def test_a_process_killed_during_the_swap_is_recovered_on_the_next_start(self):
        name, digest = self.backup(1)
        before = self.snapshot()
        for failing in (1, 2, 3, 4):
            with self.subTest(killed_before_rename=failing):
                rename, calls = os.rename, []
                def die(source, destination):
                    calls.append(source)
                    if len(calls) == failing:
                        raise Killed()
                    return rename(source, destination)
                # A killed process runs no handler: recovery happens only on the next start.
                with patch.object(backups.os, 'rename', die), patch.object(backups, 'recover_restore', lambda home: 'none'), \
                        self.assertRaises(Killed):
                    self.restore(name, digest)
                self.assertEqual(json.loads((self.work / 'journal.json').read_text())['state'], 'swapping')
                self.assertEqual(backups.recover_restore(self.home), 'rolled_back')
                self.assert_untouched(before)
                self.assertEqual(backups.recover_restore(self.home), 'none')

    def test_a_process_killed_after_both_trees_arrived_is_completed_on_the_next_start(self):
        name, digest = self.backup(1)
        with patch.object(backups, '_finish_restore', side_effect=Killed()), self.assertRaises(Killed):
            self.restore(name, digest)
        self.assertEqual((self.program / 'main.py').read_bytes(), b'restored main')
        self.assertFalse((self.program / 'venv').exists())
        self.assertEqual(backups.recover_restore(self.home), 'completed')
        self.assertEqual((self.program / 'venv/bin/python').read_bytes(), b'interpreter')
        self.assertEqual((self.data / 'chat.db').read_bytes(), b'backed-up chat')
        self.assertEqual(len(list(self.work.glob('previous-*'))), 1)
        self.assertFalse(list(self.work.glob('stage-*')))

    def test_recovery_refuses_a_journal_it_cannot_trust(self):
        self.work.mkdir()
        before = self.snapshot()
        for journal in ('not json', '{"schema": "other"}',
                        json.dumps({'schema': 'kira-restore/v1', 'state': 'swapping', 'stage': '../../kira-core',
                                    'previous': 'previous-20261115-120000-0000abcd', 'program': 'kira-core', 'data': 'kira-core'}),
                        json.dumps({'schema': 'kira-restore/v1', 'state': 'swapping', 'stage': 'stage-' + '0' * 32,
                                    'previous': 'previous-20261115-120000-0000abcd', 'program': '../outside', 'data': 'kira-core'})):
            (self.work / 'journal.json').write_text(journal)
            with self.assertRaises(ValueError):
                backups.recover_restore(self.home)
            self.assertEqual(self.snapshot(), before)
        (self.work / 'journal.json').unlink()
        self.assertEqual(backups.recover_restore(self.home), 'none')

    def test_missing_data_directory_is_restored_and_rolls_back_to_missing(self):
        name, digest = self.backup(1)
        import shutil
        shutil.rmtree(self.data)
        rename, calls = os.rename, []
        def refuse(source, destination):
            calls.append(source)
            if len(calls) == 3:
                raise OSError(errno.EIO, 'I/O error')
            return rename(source, destination)
        with patch.object(backups.os, 'rename', refuse), self.assertRaises(OSError):
            self.restore(name, digest)
        self.assertFalse(self.data.exists())
        self.assertEqual((self.program / 'main.py').read_bytes(), b'current main')
        self.restore(name, digest)
        self.assertEqual((self.data / 'chat.db').read_bytes(), b'backed-up chat')

    def request(self, **values):
        (self.state / 'restore-request.json').write_text(json.dumps({'schema': 'kira-restore-request/v1', **values}))

    def host_restore(self):
        with patch.object(runtime, '_operation_cancel', self.cancel), patch.object(runtime, 'state'), \
                patch.object(runtime, '_component_state') as published:
            runtime._restore_kira(self.root, self.home, io.BytesIO())
        return published

    def test_host_restores_only_the_archive_named_by_one_confirmed_request(self):
        name, digest = self.backup(1)
        before = self.snapshot()
        with self.assertRaisesRegex(runtime.RestoreFailed, '恢复请求无效'):
            self.host_restore()
        for values in ({'filename': name}, {'filename': '../' + name, 'sha256': digest},
                       {'filename': name, 'sha256': 'f' * 63}, {'filename': 'kira-20261115-120000-0000ffff.tar', 'sha256': digest}):
            self.request(**values)
            with self.assertRaises(runtime.RestoreFailed):
                self.host_restore()
            self.assertFalse((self.state / 'restore-request.json').exists())
            self.assert_untouched(before)
        self.request(filename=name, sha256='0' * 64)
        with self.assertRaisesRegex(runtime.RestoreFailed, '没有改动') as failure:
            self.host_restore()
        self.assertIn('changed after it was chosen', str(failure.exception.__cause__))
        self.assert_untouched(before)
        self.request(filename=name, sha256=digest)
        published = self.host_restore()
        self.assertEqual((self.data / 'chat.db').read_bytes(), b'backed-up chat')
        self.assertFalse((self.state / 'restore-request.json').exists())
        arguments, extra = published.call_args
        self.assertEqual(arguments[:2], ('kira', 'stopped'))
        self.assertEqual((extra['installed'], extra['restored']), (True, 'backups/' + name))
        # The consumed request cannot trigger a second restore.
        with self.assertRaisesRegex(runtime.RestoreFailed, '恢复请求无效'):
            self.host_restore()

    def test_host_reports_an_unfinished_swap_differently_from_an_untouched_failure(self):
        name, digest = self.backup(1)
        self.request(filename=name, sha256=digest)
        rename = os.rename
        def die(source, destination):
            raise OSError(errno.EIO, 'I/O error')
        # The swap fails and its rollback cannot run either: the journal still says 'swapping'.
        with patch.object(backups.os, 'rename', die), patch.object(backups, 'recover_restore', lambda home: 'none'), \
                self.assertRaisesRegex(runtime.RestoreFailed, '下次启动会自动回滚或完成'):
            self.host_restore()
        self.assertIs(os.rename, rename)
        self.assertEqual(backups.recover_restore(self.home), 'rolled_back')
        self.assertEqual((self.data / 'chat.db').read_bytes(), b'current chat')

    def test_host_checks_free_space_before_unpacking(self):
        name, digest = self.backup(1)
        before = self.snapshot()
        self.request(filename=name, sha256=digest)
        with patch.object(runtime, 'require_space', side_effect=SpaceUnavailable('可用存储不足')), \
                patch.object(runtime, 'restore_backup') as restore, self.assertRaises(SpaceUnavailable):
            self.host_restore()
        restore.assert_not_called()
        self.assert_untouched(before)

    def test_only_kira_accepts_the_restore_action(self):
        with self.assertRaisesRegex(ValueError, 'Only Kira'):
            runtime.control('napcat', 'restore')
        with self.assertRaisesRegex(ValueError, 'Only Kira'):
            runtime.control('all', 'restore')


if __name__ == '__main__': unittest.main()
