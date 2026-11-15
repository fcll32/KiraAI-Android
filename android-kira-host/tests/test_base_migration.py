"""Base migration and rollback (plan R2B) on a disposable fixture; guest commands are replaced by fakes."""
from contextlib import nullcontext
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
import base_migration as migration
import host_runtime as runtime
from linux_installer import Artifact, InstallationCancelled
from runtime_paths import BASE_BINDING, ROOT_MARKER, RuntimePaths, binding_value, bound_base


class Killed(BaseException):
    """Stands in for the process dying: nothing in the code under test may tidy up after it."""


class BaseMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / 'files'
        self.state = self.home / 'linux-runtime'
        self.state.mkdir(parents=True)
        self.cancel = threading.Event()
        source_sha = hashlib.sha256(b'old base').hexdigest()
        self.source = self.state / ('rootfs-' + source_sha[:16])
        (self.source).mkdir()
        (self.source / ROOT_MARKER).write_text(json.dumps({'sha256': source_sha, 'version': 1}))
        (self.state / BASE_BINDING).write_text(json.dumps(binding_value(self.home, self.source, source_sha)))
        self.files = {
            'opt/KiraAI/main.py': b'official kira', 'opt/KiraAI/requirements.txt': b'aiohttp==3\n',
            'opt/KiraAI/plugins/user.py': b'user plugin', 'opt/kira-host/wheels/pip.whl': b'wheel',
            'root/Napcat/opt/QQ/qq': b'qq binary', 'root/Napcat/opt/QQ/napcat/config/webui.json': b'{"token":"t"}',
            'root/.config/QQ/login.db': b'qq login'}
        for name, body in self.files.items():
            self.write(self.source / name, body)
        self.write(self.source / 'opt/KiraAI/venv/bin/python', b'old interpreter')
        self.write(self.home / 'kira-core/chat.db', b'chat history')
        os.symlink('main.py', self.source / 'opt/KiraAI/entry.py')
        # The new base: a small rootfs archive with a fixed identity.
        archive = Path(self.temp.name) / 'new-base.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for name, body in {'bin/sh': b'shell', 'etc/os-release': b'ID=debian\nVERSION_ID=13\n'}.items():
                info = tarfile.TarInfo(name); info.size = len(body); info.mode = 0o755
                tar.addfile(info, io.BytesIO(body))
        data = archive.read_bytes()
        self.archive = archive
        self.artifact = Artifact('new-base.tar.gz', 'https://example.invalid/new-base.tar.gz',
                                 hashlib.sha256(data).hexdigest(), len(data))
        self.target = self.state / ('rootfs-' + self.artifact.sha256[:16])
        self.prepared = []

    def write(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def prepare_target(self, paths, cancel, napcat_present):
        self.prepared.append((paths.root, napcat_present))
        self.assertNotEqual(paths.data, self.home / 'kira-core')
        self.write(paths.from_guest('/opt/KiraAI/venv/bin/python'), b'new interpreter')

    def verify_guest(self, paths, cancel):
        requirements = paths.from_guest('/opt/KiraAI/requirements.txt').read_bytes()
        return {'requirements_sha256': hashlib.sha256(requirements).hexdigest(),
                'interpreter_verified': paths.from_guest('/opt/KiraAI/venv/bin/python').read_bytes() == b'new interpreter',
                'dependency_check_passed': True}

    def migrate(self, **kwargs):
        return migration.migrate(self.home, self.artifact, self.archive, self.cancel, exclusive_owner=nullcontext(),
                                 prepare_target=kwargs.pop('prepare_target', self.prepare_target),
                                 verify_guest=self.verify_guest, **kwargs)

    def rollback(self, **kwargs):
        return migration.rollback(self.home, self.cancel, exclusive_owner=nullcontext(), **kwargs)

    def tree(self, root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob('*'))
                if p.is_file() and not p.is_symlink()}

    def journal(self):
        return json.loads((self.state / 'migrations' / self.artifact.sha256 / migration.JOURNAL).read_text())

    def test_migration_moves_programs_and_qq_data_rebuilds_the_venv_and_keeps_the_old_base(self):
        before = self.tree(self.source)
        self.assertEqual(self.migrate(), self.target)
        self.assertEqual(bound_base(self.home), (self.target, self.artifact.sha256))
        for name, body in self.files.items():
            self.assertEqual((self.target / name).read_bytes(), body)
        self.assertEqual(os.readlink(self.target / 'opt/KiraAI/entry.py'), 'main.py')
        self.assertEqual((self.target / 'opt/KiraAI/venv/bin/python').read_bytes(), b'new interpreter')
        self.assertEqual((self.target / 'etc/os-release').read_bytes(), b'ID=debian\nVERSION_ID=13\n')
        self.assertEqual(self.tree(self.source), before)
        self.assertEqual((self.home / 'kira-core/chat.db').read_bytes(), b'chat history')
        self.assertEqual(self.journal()['state'], 'complete')
        self.assertEqual([present for _, present in self.prepared], [True])
        self.assertEqual(migration.status(self.home)['state'], 'complete')
        self.assertTrue(migration.status(self.home)['active_is_target'])
        # Running it again only confirms the finished migration.
        self.assertEqual(self.migrate(), self.target)

    def test_refusals_change_nothing(self):
        before = self.tree(self.source)
        pending = self.source / '.napcat-host-pending.json'
        pending.write_text('{}')
        with self.assertRaisesRegex(migration.MigrationRefused, 'NapCat 安装尚未完成'):
            self.migrate()
        pending.unlink()
        self.assertEqual(bound_base(self.home)[0], self.source)
        self.assertFalse(self.target.exists())
        same = Artifact('old', 'https://example.invalid/old', bound_base(self.home)[1], 1)
        with self.assertRaisesRegex(migration.MigrationRefused, '无需迁移'):
            migration.migrate(self.home, same, self.archive, self.cancel, exclusive_owner=nullcontext(),
                              prepare_target=self.prepare_target, verify_guest=self.verify_guest)
        self.assertEqual(self.tree(self.source), before)
        self.assertEqual(bound_base(self.home)[0], self.source)

    def test_cancelled_while_backing_up_then_run_again(self):
        copied = []
        identity = migration._identity
        def cancel_during_copy(path, cancel, **kwargs):
            if kwargs.get('copy_to') is not None:
                copied.append(path)
                if len(copied) == 2:
                    self.cancel.set()
            return identity(path, cancel, **kwargs)
        with patch.object(migration, '_identity', cancel_during_copy), self.assertRaises(InstallationCancelled):
            self.migrate()
        self.assertEqual(bound_base(self.home)[0], self.source)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.journal()['state'], 'preparing')
        self.cancel.clear()
        self.assertEqual(self.migrate(), self.target)

    def test_cancelled_before_the_switch_keeps_the_old_base_active(self):
        def cancel_while_preparing(paths, cancel, napcat_present):
            self.prepare_target(paths, cancel, napcat_present)
            self.cancel.set()
        with self.assertRaises(InstallationCancelled):
            self.migrate(prepare_target=cancel_while_preparing)
        self.assertEqual(bound_base(self.home)[0], self.source)
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.state.glob('rootfs.stage-r2b-*')))
        self.assertEqual(self.journal()['state'], 'backed_up')
        self.cancel.clear()
        self.assertEqual(self.migrate(), self.target)

    def test_a_target_copy_damaged_before_publish_is_never_activated(self):
        def damage(paths, cancel, napcat_present):
            self.prepare_target(paths, cancel, napcat_present)
            paths.from_guest('/root/.config/QQ/login.db').write_bytes(b'damaged during preparation')
        with self.assertRaisesRegex(ValueError, 'differs from the verified backup'):
            self.migrate(prepare_target=damage)
        self.assertEqual(bound_base(self.home)[0], self.source)
        self.assertFalse(self.target.exists())
        self.assertEqual((self.source / 'root/.config/QQ/login.db').read_bytes(), b'qq login')

    def test_a_process_killed_between_publish_and_switch_finishes_on_the_next_run(self):
        def die(commit):
            raise Killed()
        with self.assertRaises(Killed):
            self.migrate(publish_binding=die)
        self.assertTrue(self.target.is_dir())
        self.assertEqual(bound_base(self.home)[0], self.source)
        self.assertEqual(self.journal()['state'], 'root_published')
        self.assertEqual(self.migrate(), self.target)
        self.assertEqual(bound_base(self.home)[0], self.target)

    def test_a_source_changed_after_its_backup_is_not_migrated(self):
        with patch.object(migration, '_isolated_probe_paths', side_effect=Killed()), self.assertRaises(Killed):
            self.migrate()
        self.assertEqual(self.journal()['state'], 'backed_up')
        (self.source / 'opt/KiraAI/plugins/user.py').write_bytes(b'edited after the backup')
        with self.assertRaisesRegex(ValueError, 'Source changed'):
            self.migrate()
        self.assertEqual(bound_base(self.home)[0], self.source)

    def test_rollback_returns_to_the_old_base_with_later_napcat_and_qq_writes(self):
        self.migrate()
        (self.target / 'root/.config/QQ/login.db').write_bytes(b'qq login after migration')
        (self.target / 'root/Napcat/opt/QQ/napcat/config/webui.json').write_bytes(b'{"token":"changed"}')
        self.write(self.home / 'kira-core/chat.db', b'chat written after migration')
        self.assertEqual(self.rollback(), self.source)
        self.assertEqual(bound_base(self.home)[0], self.source)
        self.assertEqual((self.source / 'root/.config/QQ/login.db').read_bytes(), b'qq login after migration')
        self.assertEqual((self.source / 'root/Napcat/opt/QQ/napcat/config/webui.json').read_bytes(), b'{"token":"changed"}')
        self.assertEqual((self.source / 'opt/KiraAI/venv/bin/python').read_bytes(), b'old interpreter')
        self.assertEqual((self.home / 'kira-core/chat.db').read_bytes(), b'chat written after migration')
        self.assertEqual(self.journal()['state'], 'rolled_back')
        self.assertTrue(self.target.is_dir())
        self.assertEqual(self.rollback(), self.source)
        with self.assertRaisesRegex(migration.MigrationRefused, '已经回退'):
            self.migrate()

    def test_rollback_is_refused_once_kiraai_was_updated_in_the_new_base(self):
        self.migrate()
        (self.target / 'opt/KiraAI/main.py').write_bytes(b'official kira, newer release')
        with self.assertRaisesRegex(migration.MigrationRefused, 'KiraAI 程序已经更新'):
            self.rollback()
        self.assertEqual(bound_base(self.home)[0], self.target)
        self.assertEqual(self.journal()['state'], 'complete')

    def test_an_interrupted_rollback_finishes_on_the_next_run(self):
        self.migrate()
        (self.target / 'root/.config/QQ/login.db').write_bytes(b'newer login')
        def die(commit):
            raise Killed()
        with self.assertRaises(Killed):
            self.rollback(publish_binding=die)
        self.assertEqual(bound_base(self.home)[0], self.target)
        self.assertEqual(self.journal()['state'], 'rollback_ready')
        self.assertEqual(self.rollback(), self.source)
        self.assertEqual((self.source / 'root/.config/QQ/login.db').read_bytes(), b'newer login')
        self.assertEqual(self.journal()['state'], 'rolled_back')

    def test_rollback_without_a_migration_is_refused(self):
        with self.assertRaisesRegex(migration.MigrationRefused, '没有可以回退'):
            self.rollback()

    def test_host_entry_points_need_explicit_confirmation_and_an_idle_runtime(self):
        manifest = {'abis': {'arm64-v8a': {'rootfsArtifact': {'filename': self.artifact.filename, 'url': self.artifact.url,
                    'sha256': self.artifact.sha256, 'size': self.artifact.size},
                    'nativeSHA': {name: '0' * 64 for name in runtime._NATIVE_FILES}}}}
        call = lambda confirmation: runtime.migrate_base(self.home, '/native', json.dumps(manifest), 'arm64-v8a', '[]', confirmation)
        with self.assertRaisesRegex(ValueError, 'explicit user confirmation'):
            call('yes')
        result = json.loads(call(runtime.MIGRATION_CONFIRMATION))
        self.assertFalse(result['migrated'])
        self.assertIn('安装包不在本机', result['detail'])
        downloads = self.state / 'downloads'; downloads.mkdir()
        (downloads / self.artifact.filename).write_bytes(self.archive.read_bytes())
        with patch.object(runtime, '_migration_hooks', return_value=(self.prepare_target, self.verify_guest)):
            with patch.object(runtime, '_manager_ready', True), self.assertRaisesRegex(RuntimeError, 'ownership'):
                call(runtime.MIGRATION_CONFIRMATION)
            result = json.loads(call(runtime.MIGRATION_CONFIRMATION))
        self.assertTrue(result['migrated'], result)
        self.assertEqual(bound_base(self.home)[0], self.target)
        with self.assertRaisesRegex(ValueError, 'explicit user confirmation'):
            runtime.rollback_base(self.home, 'yes')
        result = json.loads(runtime.rollback_base(self.home, runtime.ROLLBACK_CONFIRMATION))
        self.assertTrue(result['rolled_back'], result)
        self.assertEqual(bound_base(self.home)[0], self.source)
        self.assertFalse(runtime._run_lock.locked())


if __name__ == '__main__': unittest.main()
