"""Explicit uninstall of local NapCat (plan R5C4): settings kept and restored, QQ data optional, crash recovery."""
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import napcat_runtime as napcat
import host_runtime as runtime
import test_napcat_runtime as base_tests
from linux_installer import InstallationCancelled


class Killed(BaseException):
    """Stands in for the process dying: no handler in the code under test may tidy up after it."""


def fixture():
    # Built here, not at module level, so the loader does not rerun the base class's own tests.
    class Fixture(base_tests.NapCatRuntimeTests):
        def runTest(self):
            pass
    return Fixture()


class NapCatUninstallTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root, self.home, self.cancel = self.f.root, self.f.home, self.f.cancel
        self.f.ensure()
        self.code = napcat._path(self.root, napcat._NAPCAT)
        self.program = napcat._path(self.root, napcat._INSTALL)
        self.data = napcat._path(self.root, napcat._DATA)
        webui = json.loads((self.code / 'config/webui.json').read_text())
        webui['token'] = 'token-chosen-by-user'
        (self.code / 'config/webui.json').write_text(json.dumps(webui))
        (self.code / 'config/onebot11_10001.json').write_text('{"network": {"websocketServers": []}}')
        (self.code / 'plugins/notes').mkdir(parents=True)
        (self.code / 'plugins/notes/index.js').write_text('module.exports = "user plugin"')
        os.symlink('index.js', self.code / 'plugins/notes/main.js')
        self.data.mkdir(parents=True)
        (self.data / 'login.db').write_bytes(b'qq login state')
        self.runtime = self.home / 'linux-runtime/napcat'
        self.preserved = self.runtime / napcat.PRESERVED
        self.journal = self.runtime / napcat.UNINSTALL_JOURNAL

    def tree(self, base):
        return {p.relative_to(base).as_posix(): (os.readlink(p) if p.is_symlink() else p.read_bytes())
                for p in sorted(Path(base).rglob('*')) if p.is_symlink() or p.is_file()}

    def state(self):
        return json.loads(self.journal.read_text())['state'] if self.journal.exists() else None

    def test_uninstall_keeps_settings_and_qq_data_and_reinstall_restores_them(self):
        settings = {name: self.tree(self.code / name) for name in ('config', 'plugins')}
        summary = napcat.uninstall_summary(self.root, self.home)
        self.assertTrue(summary['installed_files'])
        self.assertGreater(summary['program_bytes'], 0)
        self.assertEqual(summary['qq_data_bytes'], len(b'qq login state'))
        result = napcat.uninstall(self.root, self.home, self.cancel)
        self.assertEqual(result['state'], 'complete')
        self.assertFalse(os.path.lexists(self.program))
        self.assertFalse(napcat.metadata(self.root)['installed'])
        self.assertFalse(napcat.has_installation_files(self.root))
        self.assertEqual((self.data / 'login.db').read_bytes(), b'qq login state')
        for name, files in settings.items():
            self.assertEqual(self.tree(self.preserved / name), files)
        self.assertEqual([p.name for p in self.program.parent.iterdir() if p.name.startswith('.napcat-uninstall')], [])
        # A fresh install puts the kept settings back over the installer's defaults.
        self.f.ensure()
        self.assertTrue(napcat.metadata(self.root)['installed'])
        webui = json.loads((self.code / 'config/webui.json').read_text())
        self.assertEqual(webui['token'], 'token-chosen-by-user')
        self.assertEqual((webui['host'], webui['port']), ('127.0.0.1', 6099))
        self.assertEqual(self.tree(self.code / 'plugins'), settings['plugins'])
        self.assertTrue((self.code / 'config/onebot11_10001.json').is_file())
        self.assertTrue((self.code / 'config/napcat.json').is_file())
        self.assertFalse(os.path.lexists(self.preserved))
        self.assertEqual((self.data / 'login.db').read_bytes(), b'qq login state')

    def test_qq_data_is_deleted_only_when_requested(self):
        napcat.uninstall(self.root, self.home, self.cancel, delete_qq_data=True)
        self.assertFalse(os.path.lexists(self.data))
        self.assertFalse(os.path.lexists(self.program))
        self.assertTrue((self.preserved / 'config/webui.json').is_file())
        with self.assertRaises(napcat.UninstallRefused):
            napcat.uninstall(self.root, self.home, self.cancel, delete_qq_data='yes')

    def test_refusals_leave_everything_in_place(self):
        before = self.tree(self.root)
        cases = []
        pending = napcat._path(self.root, napcat._PENDING)
        def with_pending():
            pending.write_text('{}')
            self.addCleanup(lambda: pending.unlink(missing_ok=True))
        cases.append(('unfinished installation', with_pending, lambda: pending.unlink()))
        config = self.code / 'config'
        moved = self.code / 'config-real'
        def linked_config():
            os.rename(config, moved)
            os.symlink(moved, config)
        def unlink_config():
            config.unlink()
            os.rename(moved, config)
        cases.append(('settings behind a link', linked_config, unlink_config))
        def waiting():
            self.preserved.mkdir(parents=True)
        cases.append(('earlier settings not yet restored', waiting, lambda: self.preserved.rmdir()))
        for label, arrange, undo in cases:
            with self.subTest(label):
                arrange()
                with self.assertRaises(napcat.UninstallRefused):
                    napcat.uninstall(self.root, self.home, self.cancel)
                undo()
                self.assertEqual(self.tree(self.root), before)
                self.assertFalse(list(self.runtime.glob('preserved.stage-*')))
                self.assertIn(self.state(), (None, 'abandoned'))
        napcat.uninstall(self.root, self.home, self.cancel)
        with self.assertRaisesRegex(napcat.UninstallRefused, '没有安装'):
            napcat.uninstall(self.root, self.home, self.cancel)

    def test_cancellation_before_the_commit_changes_nothing(self):
        before = self.tree(self.root)
        copy, calls = napcat._copy, []
        def cancel_midway(source, target, cancel):
            calls.append(source)
            if len(calls) == 2:
                self.cancel.set()
            return copy(source, target, cancel)
        with patch.object(napcat, '_copy', cancel_midway), self.assertRaises(InstallationCancelled):
            napcat.uninstall(self.root, self.home, self.cancel)
        self.assertEqual(self.tree(self.root), before)
        self.assertFalse(os.path.lexists(self.preserved))
        self.assertFalse(list(self.runtime.glob('preserved.stage-*')))
        self.assertEqual(self.state(), 'abandoned')

    def test_a_process_killed_while_keeping_settings_is_discarded_on_the_next_start(self):
        before = self.tree(self.root)
        stage = self.runtime / ('preserved.stage-' + '0' * 32)
        (stage / 'config').mkdir(parents=True)
        (stage / 'config/half-copied.json').write_text('{')
        napcat._atomic(self.journal, {'schema': napcat.UNINSTALL_SCHEMA, 'state': 'preserving', 'stage': stage.name})
        self.assertEqual(napcat.recover_uninstall(self.root, self.home), 'abandoned')
        self.assertFalse(stage.exists())
        self.assertEqual(self.tree(self.root), before)
        self.assertEqual(napcat.recover_uninstall(self.root, self.home), 'none')

    def test_a_process_killed_after_the_commit_is_finished_on_the_next_start(self):
        for label, target, attribute in (('before the program was moved aside', napcat, '_finish_uninstall'),
                                         ('while deleting the moved program', napcat, '_remove')):
            with self.subTest(label):
                if not os.path.lexists(self.program):
                    self.f.ensure()
                original = getattr(target, attribute)
                calls = []
                def die(*args, **kwargs):
                    calls.append(args)
                    if attribute == '_remove' and not str(args[0]).split('/')[-1].startswith('.napcat-uninstall'):
                        return original(*args, **kwargs)
                    raise Killed()
                with patch.object(target, attribute, die), self.assertRaises(Killed):
                    napcat.uninstall(self.root, self.home, self.cancel)
                self.assertEqual(self.state(), 'deleting')
                self.assertTrue((self.preserved / 'config/webui.json').is_file())
                self.assertEqual(napcat.recover_uninstall(self.root, self.home), 'completed')
                self.assertFalse(os.path.lexists(self.program))
                self.assertEqual([p.name for p in self.program.parent.iterdir() if p.name.startswith('.napcat-uninstall')], [])
                self.assertEqual((self.data / 'login.db').read_bytes(), b'qq login state')
                self.assertEqual(self.state(), 'complete')

    def test_recovery_refuses_a_journal_it_cannot_trust(self):
        before = self.tree(self.root)
        self.runtime.mkdir(parents=True, exist_ok=True)
        for journal in ('not json', '{"schema": "other", "state": "deleting"}',
                        json.dumps({'schema': napcat.UNINSTALL_SCHEMA, 'state': 'preserving', 'stage': '../../guest'}),
                        json.dumps({'schema': napcat.UNINSTALL_SCHEMA, 'state': 'deleting', 'trash': {'program': '../../guest'}}),
                        json.dumps({'schema': napcat.UNINSTALL_SCHEMA, 'state': 'deleting', 'trash': {'home': '.napcat-uninstall-' + '0' * 32}})):
            self.journal.write_text(journal)
            with self.assertRaises(ValueError):
                napcat.recover_uninstall(self.root, self.home)
            self.assertEqual(self.tree(self.root), before)
        self.journal.unlink()

    def request(self, **values):
        (self.home / 'linux-runtime' / runtime._UNINSTALL_REQUEST).write_text(
            json.dumps({'schema': 'napcat-uninstall-request/v1', **values}))

    def host_uninstall(self):
        with patch.object(runtime, '_operation_cancel', self.cancel), patch.object(runtime, 'state'), \
                patch.object(runtime, '_component_state') as published:
            runtime._uninstall_napcat(self.root, self.home, io.BytesIO())
        return published

    def test_host_uninstalls_only_on_one_confirmed_request(self):
        before = self.tree(self.root)
        with self.assertRaisesRegex(napcat.UninstallRefused, '卸载请求无效'):
            self.host_uninstall()
        for values in ({}, {'delete_qq_data': 'true'}, {'delete_qq_data': 1}):
            self.request(**values)
            with self.assertRaises(napcat.UninstallRefused):
                self.host_uninstall()
            self.assertFalse((self.home / 'linux-runtime' / runtime._UNINSTALL_REQUEST).exists())
            self.assertEqual(self.tree(self.root), before)
        self.request(delete_qq_data=False)
        published = self.host_uninstall()
        self.assertFalse(os.path.lexists(self.program))
        self.assertTrue(self.data.exists())
        arguments, extra = published.call_args
        self.assertEqual(arguments[:2], ('napcat', 'stopped'))
        self.assertIn('QQ 登录数据已保留', arguments[2])
        self.assertIs(extra['installed'], False)
        with self.assertRaisesRegex(napcat.UninstallRefused, '卸载请求无效'):
            self.host_uninstall()

    def test_only_napcat_accepts_the_uninstall_action(self):
        with self.assertRaisesRegex(ValueError, 'Only the local NapCat'):
            runtime.control('kira', 'uninstall')
        with self.assertRaisesRegex(ValueError, 'Only the local NapCat'):
            runtime.control('all', 'uninstall')


if __name__ == '__main__': unittest.main()
