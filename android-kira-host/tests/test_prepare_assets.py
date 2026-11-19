"""Latest-release selection keeps seed source and frontend bytes together."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile


HOST = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('host_asset_preparer', HOST / 'prepare_assets.py')
preparer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preparer)
RUN = subprocess.run


class PrepareAssetsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='kira-latest-release-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.upstream = self.home / 'upstream'
        RUN(['git', 'init', str(self.upstream)], check=True, capture_output=True, timeout=30)
        self.downloads = 0
        self.release('v9.0.1', 123, b'first release\n')
        for name, value in [('OFFICIAL', self.home / 'official'), ('RUNTIME', self.home / 'runtime'),
                            ('OUTPUT', self.home / 'build')]:
            binding = patch.object(preparer, name, value)
            binding.start()
            self.addCleanup(binding.stop)
        binding = patch.object(preparer.subprocess, 'run', side_effect=self.run_command)
        binding.start()
        self.addCleanup(binding.stop)

    def git(self, *arguments):
        return RUN(['git', '-C', str(self.upstream), *arguments], check=True,
                   capture_output=True, text=True, timeout=30).stdout.strip()

    def release(self, version, asset_id, source):
        (self.upstream / 'main.py').write_bytes(source)
        self.git('-c', 'core.autocrlf=false', 'add', 'main.py')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '--no-gpg-sign', '-m', version)
        self.git('tag', version)
        self.commit = self.git('rev-parse', 'HEAD')
        frontend = io.BytesIO()
        with zipfile.ZipFile(frontend, 'w') as archive:
            archive.writestr('dist/icon.png', b'icon-' + version.encode())
        self.frontend = frontend.getvalue()
        self.metadata = {'tag_name': version, 'assets': [{
            'id': asset_id, 'name': 'dist.zip', 'size': len(self.frontend),
            'digest': 'sha256:' + hashlib.sha256(self.frontend).hexdigest()}]}

    def run_command(self, arguments, **kwargs):
        if arguments[0] == 'gh':
            if arguments[2].endswith('/releases/latest'):
                return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(self.metadata))
            if '/releases/assets/' in arguments[2]:
                self.downloads += 1
                kwargs['stdout'].write(self.frontend)
                return subprocess.CompletedProcess(arguments, 0)
            self.fail('Unexpected GitHub request: ' + repr(arguments))
        arguments = [str(self.upstream) if value == 'https://github.com/KiraAI-Dev/KiraAI.git'
                     else value for value in arguments]
        return RUN(arguments, **kwargs)

    def test_new_latest_release_changes_source_commit_and_frontend(self):
        first = preparer.latest_official_release()
        self.assertEqual(first[1:3], ('v9.0.1', self.commit))
        self.release('v9.0.2', 124, b'second release\n')
        repository, version, commit, dist, digest = preparer.latest_official_release()
        self.assertEqual((version, commit), ('v9.0.2', self.commit))
        self.assertNotEqual(commit, first[2])
        self.assertEqual(dist.read_bytes(), self.frontend)
        self.assertEqual(digest, hashlib.sha256(self.frontend).hexdigest())
        actual_source = RUN(['git', '-C', str(repository), 'show', commit + ':main.py'],
                            check=True, capture_output=True, timeout=30).stdout
        self.assertEqual(actual_source, b'second release\n')

    def test_verified_frontend_cache_is_reused_and_corruption_is_repaired(self):
        first = preparer.latest_official_release()
        preparer.latest_official_release()
        self.assertEqual(self.downloads, 1)
        first[3].write_bytes(b'damaged cached archive')
        preparer.latest_official_release()
        self.assertEqual(self.downloads, 2)
        self.assertEqual(first[3].read_bytes(), self.frontend)

    def test_bad_download_is_rejected_and_partial_file_removed(self):
        self.frontend = b'bad download'
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            preparer.latest_official_release()
        self.assertFalse(list(preparer.OFFICIAL.rglob('*.part')))
        self.assertFalse(list(preparer.OFFICIAL.rglob('dist.zip')))

    def test_missing_frontend_or_digest_aborts_preparation(self):
        for assets in ([], [{'name': 'dist.zip', 'digest': None}]):
            with self.subTest(assets=assets):
                self.metadata['assets'] = assets
                with self.assertRaises(ValueError):
                    preparer.latest_official_release()
        self.assertEqual(self.downloads, 0)

    def test_seed_records_selected_version_commit_and_original_bytes(self):
        artifacts = preparer.RUNTIME / 'artifacts'
        wheel = artifacts / 'pip/pip.whl'
        wheel.parent.mkdir(parents=True)
        wheel.write_bytes(b'pip fixture')
        pip = {'filename': wheel.name, 'size': wheel.stat().st_size,
               'sha256': preparer.sha(wheel), 'package_version': 'fixture'}
        (artifacts / 'pip-progress-manifest.json').write_text(json.dumps(pip), encoding='utf-8')
        repository, _, frontend = preparer.original_seed()
        self.assertEqual(repository['version'], 'v9.0.1')
        self.assertEqual(repository['initial_commit'], self.commit)
        integrity = json.loads((preparer.OUTPUT / 'official-source-integrity.json').read_text())
        self.assertEqual(integrity['commit'], self.commit)
        self.assertEqual(integrity['version'], repository['version'])
        with zipfile.ZipFile(preparer.OUTPUT / 'assets/official-kira-seed.zip') as archive:
            self.assertEqual(archive.read('KiraAI/main.py'), b'first release\n')
            self.assertEqual(archive.read('dist.zip'), self.frontend)
        self.assertEqual(frontend['sha256'], hashlib.sha256(self.frontend).hexdigest())
        self.assertEqual(frontend['version'], repository['version'])


if __name__ == '__main__':
    unittest.main()
