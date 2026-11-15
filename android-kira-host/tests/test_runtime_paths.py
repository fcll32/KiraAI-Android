"""Verify the Android host's active path contract and no-migration guard."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))

import host_runtime
from linux_installer import Artifact, InstallationCancelled
import napcat_runtime
from runtime_paths import (BASE_BINDING, KIRA_DATA, KIRA_PROGRAM, KIRA_REQUIREMENTS, KIRA_VENV, NAPCAT_CODE,
                           NAPCAT_CONFIG, NAPCAT_DATA, PREFLIGHT_REPORT, ROOT_MARKER, BaseSelectionError,
                           RuntimePaths, choose_base, readable_generations)


class RuntimePathsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.paths = RuntimePaths(self.home)
        self.paths.state.mkdir()
        self.old = Artifact('old.tar.gz', 'https://test.invalid/old.tar.gz', 'a' * 64, 42)
        self.new = Artifact('new.tar.gz', 'https://test.invalid/new.tar.gz', 'b' * 64, 92)
        host_runtime._cancel.clear()
        self.addCleanup(host_runtime._cancel.clear)

    def put(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def generation(self, artifact, directory='kira-linux'):
        root = self.home / directory / ('rootfs-' + artifact.sha256[:16])
        self.put(root / ROOT_MARKER, json.dumps({'sha256': artifact.sha256, 'version': 1}).encode())
        for relative in ('bin/sh', 'usr/bin/env', 'etc/os-release'):
            self.put(root / relative, b'fixture base key file')
        return root

    def populate(self, root):
        paths = RuntimePaths(self.home, root)
        self.put(paths.from_guest(KIRA_REQUIREMENTS), b'fixture-pkg~=2.1\n')
        self.put(paths.from_guest(KIRA_PROGRAM + '/core/config/default.py'), b'VERSION = "v9.1.2"\n')
        self.put(paths.from_guest(KIRA_VENV + '/pyvenv.cfg'), b'version = 3.11.17\n')
        self.put(paths.from_guest(KIRA_DATA + '/webui.json'), b'{"access_token":"private-fixture-value"}')
        self.put(paths.from_guest(NAPCAT_CONFIG), b'{"token":"napcat-private-fixture-value"}')
        self.put(paths.from_guest(NAPCAT_CODE + '/napcat.mjs'), b'const v="4.18.42"||"1.0.0-dev";')
        self.put(paths.from_guest(NAPCAT_DATA + '/history'), b'private-qq-data')
        return paths

    def test_legacy_base_wins_over_empty_same_digest_base_and_readers_follow_it(self):
        legacy = self.generation(self.old)
        self.generation(self.old, directory='linux-runtime')
        paths = self.populate(legacy)
        with patch.object(host_runtime, 'download') as download:
            self.assertEqual(host_runtime._prepare_rootfs(self.paths.state, self.old, self.home), legacy)
            download.assert_not_called()
        self.assertEqual(readable_generations(self.home), (legacy,))
        self.assertEqual(host_runtime._requirements_digest(paths.from_guest(KIRA_PROGRAM)),
                         hashlib.sha256(b'fixture-pkg~=2.1\n').hexdigest())
        self.assertEqual(napcat_runtime.metadata(legacy)['version'], 'v4.18.42')
        self.assertEqual(napcat_runtime.token_path(legacy), paths.from_guest(NAPCAT_CONFIG))
        napcat_runtime.configure_local(legacy)
        self.assertEqual(json.loads(paths.from_guest(NAPCAT_CONFIG).read_text())['token'], 'napcat-private-fixture-value')
        self.assertEqual(napcat_runtime._backup(legacy, self.paths.state / 'napcat', host_runtime._cancel)
                         .joinpath('qq-data/history').read_bytes(), b'private-qq-data')
        argv = host_runtime._guest_argv(self.home, legacy, self.home, host_runtime._official_command(), 'test-run')
        self.assertEqual(argv[argv.index('-r') + 1], str(legacy))
        self.assertIn(str(paths.data) + ':' + KIRA_DATA, argv)
        self.assertEqual(paths.status_paths()['napcat_config'], paths.from_guest(NAPCAT_CONFIG).relative_to(self.home).as_posix())

    def test_base_change_blocks_before_download_even_when_new_base_is_already_cached(self):
        original = self.generation(self.old)
        self.populate(original)
        existing = self.generation(self.new, directory='linux-runtime')
        before = {path: path.read_bytes() for path in original.rglob('*') if path.is_file()}
        with patch.object(host_runtime, 'download') as download, patch.object(host_runtime, 'unpack_rootfs') as unpack:
            with self.assertRaisesRegex(BaseSelectionError, '已保留原 Linux 环境'):
                host_runtime._prepare_rootfs(self.paths.state, self.new, self.home)
            download.assert_not_called()
            unpack.assert_not_called()
        report = (self.paths.state / PREFLIGHT_REPORT).read_text(encoding='utf-8')
        self.assertNotIn('private-fixture-value', report)
        self.assertNotIn('private-qq-data', report)
        result = json.loads(report)
        self.assertEqual(result['reason'], 'base_change_requires_migration')
        self.assertEqual(result['requested_base_sha256'], self.new.sha256)
        self.assertEqual(result['existing_bases'][1]['kira_version'], 'v9.1.2')
        self.assertEqual(result['existing_bases'][1]['python_version'], '3.11.17')
        self.assertTrue(result['python_environment']['rebuild_required_for_base_change'])
        self.assertGreater(result['space']['archive_and_copy_lower_bound_bytes'], self.new.size)
        self.assertEqual({path: path.read_bytes() for path in original.rglob('*') if path.is_file()}, before)
        self.assertTrue((existing / ROOT_MARKER).is_file())
        self.assertFalse((self.paths.state / BASE_BINDING).exists())

    def test_recorded_identity_does_not_fall_back_to_another_ready_root(self):
        legacy = self.generation(self.old)
        host_runtime._prepare_rootfs(self.paths.state, self.old, self.home)
        self.generation(self.new, directory='linux-runtime')
        self.assertEqual(choose_base(self.home, self.old.sha256), legacy)
        with self.assertRaises(BaseSelectionError):
            choose_base(self.home, self.new.sha256)
        (legacy / ROOT_MARKER).unlink()
        self.assertEqual(readable_generations(self.home), ())
        with self.assertRaises(BaseSelectionError):
            choose_base(self.home, self.old.sha256)

    def test_incomplete_guest_or_existing_kira_data_blocks_without_new_identity(self):
        malformed = self.paths.state / ('rootfs-' + self.old.sha256[:16])
        malformed.mkdir()
        with self.assertRaises(BaseSelectionError):
            choose_base(self.home, self.new.sha256)
        malformed.rmdir()
        self.put(self.paths.data / 'memory.sqlite', b'keep on preflight failure')
        with self.assertRaises(BaseSelectionError):
            choose_base(self.home, self.new.sha256)

    def test_fresh_install_and_canceled_report_never_replace_a_base(self):
        target = self.paths.state / ('rootfs-' + self.new.sha256[:16])
        self.assertEqual(choose_base(self.home, self.new.sha256), target)
        self.assertFalse(target.exists())
        self.populate(self.generation(self.old))
        host_runtime._cancel.set()
        with self.assertRaises(InstallationCancelled):
            host_runtime._prepare_rootfs(self.paths.state, self.new, self.home)
        self.assertFalse(target.exists())
        self.assertFalse((self.paths.state / PREFLIGHT_REPORT).exists())

    def test_host_state_paths_cannot_escape_or_be_confused_with_prefix_siblings(self):
        paths = RuntimePaths(self.home, self.generation(self.old))
        self.assertEqual(paths.from_guest('/run/kira-host/napcat/attempts/a'),
                         self.paths.state / 'napcat/attempts/a')
        self.assertEqual(paths.from_guest('/run/kira-host-sibling/a'),
                         paths.root / 'run/kira-host-sibling/a')
        with self.assertRaises(ValueError):
            paths.from_guest('/run/kira-host/../outside')
        with self.assertRaises(ValueError):
            paths.as_guest_state(self.home / 'outside')
