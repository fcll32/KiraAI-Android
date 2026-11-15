import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from linux_installer import Artifact, InstallationCancelled
import storage_space as space


class StorageSpaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.cancel = threading.Event()
        self.report = self.directory / 'space.json'

    def test_same_volume_uses_largest_simultaneous_phase_and_one_margin(self):
        phase_a = [space.Allocation('scratch', self.directory / 'scratch', 30),
                   space.Allocation('download', self.directory / 'cache', 70)]
        phase_b = [space.Allocation('extraction', self.directory / 'target', 90)]
        with patch.object(space.shutil, 'disk_usage', return_value=SimpleNamespace(free=space.SAFETY_MARGIN + 100)):
            result = space.require_space('install', phase_a, self.cancel, self.report, alternative_phases=(phase_b,))
        volume, = result['volumes']
        self.assertEqual(volume['required_bytes'], space.SAFETY_MARGIN + 100)
        self.assertEqual(volume['roles'], ['scratch', 'download'])
        self.assertEqual(result, json.loads(self.report.read_text()))

    def test_adequate_other_volume_cannot_hide_insufficient_destination(self):
        first = SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=10), free=space.SAFETY_MARGIN + 99)
        second = SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=20), free=space.SAFETY_MARGIN + 10000)
        requests = [space.Allocation('program', self.directory / 'rootfs', 100),
                    space.Allocation('backup', self.directory / 'state', 200)]
        with patch.object(space, '_directory', side_effect=[first, second]), \
                patch.object(space.shutil, 'disk_usage', side_effect=lambda path: path), \
                self.assertRaisesRegex(space.SpaceUnavailable, '可用存储不足'):
            space.require_space('install', requests, self.cancel, self.report)
        values = json.loads(self.report.read_text())['volumes']
        self.assertEqual([volume['decision'] for volume in values], ['insufficient', 'sufficient'])

    def test_unknown_space_blocks_and_cancelled_check_never_publishes(self):
        allocation = space.Allocation('backup', self.directory, 1)
        with patch.object(space.shutil, 'disk_usage', side_effect=OSError('fixture')), \
                self.assertRaisesRegex(space.SpaceUnavailable, '无法确认'):
            space.require_space('backup', [allocation], self.cancel, self.report)
        original = self.report.read_bytes()
        self.assertEqual(json.loads(original)['decision'], 'unknown')
        self.cancel.set()
        with self.assertRaises(InstallationCancelled):
            space.require_space('different-action', [allocation], self.cancel, self.report)
        self.assertEqual(self.report.read_bytes(), original)

    def test_cached_and_partial_artifacts_are_not_counted_twice(self):
        data = b'pinned artifact' * 512
        artifact = Artifact('base.tar.xz', 'https://example.com/base.tar.xz', hashlib.sha256(data).hexdigest(), len(data))
        target = self.directory / artifact.filename
        partial = target.with_suffix('.xz.part')
        partial.write_bytes(data[:4096])
        known_partial = min(4096, getattr(partial.stat(), 'st_blocks', 0) * 512)
        self.assertEqual(space.download_bytes(artifact, self.directory, self.cancel), len(data) - known_partial)
        target.write_bytes(data)
        self.assertEqual(space.download_bytes(artifact, self.directory, self.cancel), 0)
        target.write_bytes(b'x' * len(data))
        self.assertEqual(space.download_bytes(artifact, self.directory, self.cancel), len(data) - known_partial)

    def test_tree_separates_logical_allocation_and_copy_estimate(self):
        source = self.directory / 'program'
        source.mkdir()
        (source / 'source.py').write_bytes(b'123')
        (source / 'venv').mkdir()
        (source / 'venv' / 'ignore').write_bytes(b'456789')
        report = space.tree_size(source, self.cancel, skip_top_level=('venv',))
        self.assertEqual(report['logical_bytes'], 3)
        self.assertEqual(report['copy_bytes'], space.estimated_bytes(0) + space.estimated_bytes(3))
        if hasattr(source.stat(), 'st_blocks'):
            self.assertGreaterEqual(report['allocated_bytes'], 0)
        else:
            self.assertIsNone(report['allocated_bytes'])

    def test_tar_estimate_covers_hardlink_copy_fallback(self):
        target = self.directory / 'archive.tar'
        with tarfile.open(target, 'w') as archive:
            regular = tarfile.TarInfo('program/data')
            regular.size = 5
            archive.addfile(regular, io.BytesIO(b'hello'))
            link = tarfile.TarInfo('program/copy')
            link.type = tarfile.LNKTYPE
            link.linkname = regular.name
            archive.addfile(link)
        self.assertGreaterEqual(space.tar_bytes(target, self.cancel), 2 * space.estimated_bytes(5))


if __name__ == '__main__':
    unittest.main()
