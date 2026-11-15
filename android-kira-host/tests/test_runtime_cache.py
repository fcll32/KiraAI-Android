"""Archive import/reclamation must preserve installed programs and unknown data."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
import runtime_cache as cache
from linux_installer import InstallationCancelled
from runtime_paths import ROOT_MARKER, BASE_BINDING, RuntimePaths, binding_value

class RuntimeCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.paths = RuntimePaths(self.home)
        self.cancel = threading.Event()
        self.manifest = {'abis': {'arm64-v8a': {}}, 'pipArtifact': self.artifact('pip.whl', b'wheel')}
        self.base = self.artifact('base.tar.gz', b'base')
        self.node = self.artifact('node.tar.xz', b'node')
        self.seed = self.artifact('official-kira-seed.zip', b'seed')
        self.manifest['abis']['arm64-v8a'].update(rootfsArtifact=self.base, nodeArtifact=self.node)
        self.manifest['officialSeed'] = self.seed
        self.root = self.paths.state / ('rootfs-' + self.base['sha256'][:16])
        for name in cache.ROOT_FILES:
            self.write(self.root / name, b'installed')
        self.write(self.root / ROOT_MARKER, json.dumps({'version': 1, 'sha256': self.base['sha256']}).encode())
        self.write(self.paths.state / BASE_BINDING, json.dumps(binding_value(self.home, self.root, self.base['sha256'])).encode())
        for name in cache.SOURCE_FILES:
            self.write(self.root / 'opt/KiraAI' / name, b'official-updated')
        self.write(self.root / 'opt/kira-host/wheels/pip.whl', b'wheel')
        self.write(self.paths.state / 'official-install.json', b'{"state":"complete"}')
        generation = self.root / 'opt' / ('kira-node-' + self.node['sha256'][:20])
        for name in ('bin/node', 'bin/npm', 'bin/npx', 'LICENSE'):
            self.write(generation / name, b'node')
        self.write(generation / '.kira-node.json', json.dumps({'sha256': self.node['sha256']}).encode())
        for artifact, value in [(self.base, b'base'), (self.node, b'node'), (self.seed, b'seed')]:
            self.write(self.paths.state / 'downloads' / artifact['filename'], value)

    @staticmethod
    def artifact(name, value):
        return {'filename': name, 'sha256': hashlib.sha256(value).hexdigest(), 'size': len(value)}
    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    def plan(self):
        return json.loads(cache.plan(self.home, self.manifest, 'arm64-v8a'))
    def complete(self, node=True):
        return cache.complete(self.home, self.root, self.manifest, 'arm64-v8a', self.cancel, node_verified=node)

    def test_three_starts_do_not_reimport_retired_archives_or_reseed_updated_code(self):
        self.assertEqual(len(self.plan()['needed']), 3)
        report = self.complete()
        self.assertEqual(len(report['removed']), 3)
        for _ in range(3):
            self.assertEqual(self.plan()['needed'], [])
        self.assertEqual((self.root / 'opt/KiraAI/main.py').read_bytes(), b'official-updated')

    def test_missing_base_key_refuses_repair_in_place(self):
        self.complete()
        (self.root / 'bin/sh').unlink()
        self.assertTrue(self.plan()['repair_required'])
        self.assertTrue((self.root / 'opt/KiraAI/main.py').exists())

    def test_missing_node_and_bad_wheel_request_only_their_own_archives(self):
        self.complete()
        (self.root / 'opt' / ('kira-node-' + self.node['sha256'][:20]) / 'bin/node').unlink()
        (self.root / 'opt/kira-host/wheels/pip.whl').write_bytes(b'wrong')
        self.assertEqual(self.plan()['needed'], [self.seed['filename']])
        self.assertTrue(self.plan()['node_repair_required'])

    def test_changed_base_identity_is_blocked_without_switching(self):
        self.complete()
        self.manifest['abis']['arm64-v8a']['rootfsArtifact'] = self.artifact('new-base.tar.gz', b'new')
        self.assertTrue(self.plan()['repair_required'])

    def test_corrupted_archive_is_retained_and_never_deletes_unknown_files(self):
        folder = self.paths.state / 'downloads'
        (folder / self.base['filename']).write_bytes(b'bad!')
        self.write(folder / 'unknown.cache', b'preserve')
        report = self.complete()
        self.assertIn(self.base['filename'], report['retained'])
        self.assertEqual((folder / 'unknown.cache').read_bytes(), b'preserve')

    def test_cancellation_and_full_disk_do_not_publish_or_remove_archives(self):
        self.cancel.set()
        with self.assertRaises(InstallationCancelled):
            self.complete()
        self.cancel.clear()
        with patch.object(cache, '_publish', side_effect=OSError('full disk')):
            with self.assertRaises(OSError):
                self.complete()
        self.assertEqual(len(list((self.paths.state / 'downloads').iterdir())), 3)
        self.assertFalse((self.paths.state / cache.RECEIPT).exists())

    def test_unproven_node_is_retained(self):
        self.complete(node=False)
        self.assertEqual(self.plan()['needed'], [self.node['filename']])

    def test_only_bound_complete_attempt_copies_are_retired(self):
        attempt = self.paths.state / 'napcat/attempts' / ('a' * 32)
        copies = {'QQ.deb': self.artifact('QQ.deb', b'qq')}
        self.write(attempt / 'QQ.deb', b'qq')
        self.write(attempt / 'keep.log', b'evidence')
        self.write(attempt / 'attempt.json', json.dumps({'state': 'installing', 'copies': copies}).encode())
        with self.assertRaises(ValueError):
            cache.retire_attempt(self.home, self.root, attempt, copies, self.cancel)
        self.write(attempt / 'attempt.json', json.dumps({'state': 'complete', 'copies': copies}).encode())
        cache.retire_attempt(self.home, self.root, attempt, copies, self.cancel)
        self.assertFalse((attempt / 'QQ.deb').exists())
        self.assertTrue((attempt / 'keep.log').exists())

    def test_pending_or_outside_attempt_is_never_reclaimed(self):
        copies = {'QQ.deb': self.artifact('QQ.deb', b'qq')}
        outside = self.home / ('b' * 32)
        attempt = self.paths.state / 'napcat/attempts' / ('c' * 32)
        for folder in (outside, attempt):
            self.write(folder / 'QQ.deb', b'qq')
            self.write(folder / 'attempt.json', json.dumps({'state': 'complete', 'copies': copies}).encode())
        self.write(self.root / '.napcat-host-pending.json', b'{}')
        for folder in (outside, attempt):
            with self.assertRaises(ValueError):
                cache.retire_attempt(self.home, self.root, folder, copies, self.cancel)
            self.assertEqual((folder / 'QQ.deb').read_bytes(), b'qq')

    def test_hardlinked_archive_is_retained(self):
        source = self.paths.state / 'downloads' / self.base['filename']
        destination = self.home / 'preserve'
        os.link(source, destination)
        self.assertIn(self.base['filename'], self.complete()['retained'])
        self.assertEqual(destination.read_bytes(), b'base')

if __name__ == '__main__':
    unittest.main()
