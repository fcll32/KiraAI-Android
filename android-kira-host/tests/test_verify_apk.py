"""Required-native completeness uses build intent and trusted host inputs, never APK discovery."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

HOST = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('host_apk_verifier', HOST / 'verify_apk.py')
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)

# Independent fixture contract for the standard Chaquopy 17.2 / Python 3.11 host.
HOST_LIBRARIES = (
    'libchaquopy_java.so', 'libpython3.11.so', 'libpython3.so',
    'libcrypto_chaquopy.so', 'libcrypto_python.so', 'libsqlite3_chaquopy.so',
    'libsqlite3_python.so', 'libssl_chaquopy.so', 'libssl_python.so',
)
PREPARED_LIBRARIES = ('libproot.so', 'libprootloader.so', 'libtalloc.so', 'libandroid-shmem.so')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def archive_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


def elf(name, abi, alignment=16384):
    data = bytearray(120)
    data[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<H', data, 18, 183 if abi == 'arm64-v8a' else 62)
    struct.pack_into('<Q', data, 32, 64)
    struct.pack_into('<HH', data, 54, 56, 1)
    struct.pack_into('<I', data, 64, 1)
    struct.pack_into('<Q', data, 112, alignment)
    return bytes(data) + name.encode('ascii')


class VerifyApkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='kira-verify-apk-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        prepared = self.home / 'build/runtime'
        (prepared / 'assets').mkdir(parents=True)
        official = {'main.py': b'official supervisor fixture', 'requirements.txt': b'fixture==1\n'}
        frontend, wheel = b'frontend fixture', b'pip wheel fixture'
        self.seed = archive_bytes({**{'KiraAI/' + name: data for name, data in official.items()},
                                   'dist.zip': frontend, 'wheels/pip.whl': wheel})
        self.manifest = {
            'abis': {}, 'officialRepository': {'version': 'fixture-version'},
            'officialSeed': {'sha256': sha(self.seed), 'size': len(self.seed)},
            'frontendArtifact': {'sha256': sha(frontend), 'size': len(frontend)},
            'pipArtifact': {'filename': 'pip.whl', 'sha256': sha(wheel), 'size': len(wheel)},
        }
        self.assets = {'assets/official-kira-seed.zip': self.seed}
        for abi in ('arm64-v8a', 'x86_64'):
            section = {'nativeSHA': {name: sha(elf(name, abi)) for name in PREPARED_LIBRARIES}}
            for key in ('rootfsArtifact', 'nodeArtifact'):
                data = (abi + ':' + key).encode()
                asset = abi + '/' + key + '.payload'
                section[key] = {'asset': asset, 'size': len(data), 'sha256': sha(data)}
                self.assets['assets/' + asset] = data
            self.manifest['abis'][abi] = section
        self.originals = {'commit': 'fixture-commit', 'files': [
            {'path': name, 'size': len(data), 'sha256': sha(data)} for name, data in official.items()]}
        (prepared / 'official-source-integrity.json').write_text(json.dumps(self.originals), encoding='utf-8')
        self.write_prepared_manifest()
        binding = patch.object(verifier, 'HERE', self.home)
        binding.start()
        self.addCleanup(binding.stop)

    def write_prepared_manifest(self):
        path = self.home / 'build/runtime/assets/host-runtime.json'
        path.write_text(json.dumps(self.manifest), encoding='utf-8')

    def apk(self, abis=('arm64-v8a',), omit=(), overrides=None):
        files = dict(self.assets)
        files['assets/host-runtime.json'] = json.dumps(self.manifest).encode()
        for abi in abis:
            for name in HOST_LIBRARIES + PREPARED_LIBRARIES:
                path = 'lib/' + abi + '/' + name
                if path not in omit:
                    files[path] = elf(name, abi)
        files.update(overrides or {})
        path = self.home / 'candidate.apk'
        path.write_bytes(archive_bytes(files))
        return path

    def test_standard_profile_matches_trusted_host_library_contract(self):
        profile = json.loads(verifier.NATIVE_PROFILES.read_text())['modes'][verifier.DEFAULT_HOST_MODE]
        names = {name for source in profile['hostNativeSources'] for name in source['requiredLibraries']}
        self.assertEqual(names, set(HOST_LIBRARIES))
        self.assertTrue(all(source['coordinate'] for source in profile['hostNativeSources']))

    def test_arm64_selection_does_not_require_other_prepared_abis(self):
        result = verifier.verify(self.apk(), ['arm64-v8a'])
        self.assertEqual(result['abis'], ['arm64-v8a'])
        self.assertEqual(set(result['required_native_libraries']['arm64-v8a']), set(HOST_LIBRARIES + PREPARED_LIBRARIES))
        self.assertEqual(result['native_libraries_16k_verified'], len(HOST_LIBRARIES + PREPARED_LIBRARIES))
        self.assertEqual(result['official_files_unchanged'], len(self.originals['files']))

    def test_universal_selection_accepts_both_selected_abis(self):
        result = verifier.verify(self.apk(('arm64-v8a', 'x86_64')), ['x86_64', 'arm64-v8a'])
        self.assertEqual(result['abis'], ['arm64-v8a', 'x86_64'])

    def test_missing_interpreter_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Missing required native.*libpython3\\.11\\.so'):
            verifier.verify(self.apk(omit=['lib/arm64-v8a/libpython3.11.so']), ['arm64-v8a'])

    def test_removing_all_native_libraries_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'APK ABI set differs.*arm64-v8a'):
            verifier.verify(self.apk(abis=()), ['arm64-v8a'])

    def test_every_required_host_and_prepared_library_is_mandatory(self):
        for name in HOST_LIBRARIES + PREPARED_LIBRARIES:
            with self.subTest(library=name), self.assertRaisesRegex(ValueError, 'Missing required native.*' + re.escape(name)):
                verifier.verify(self.apk(omit=['lib/arm64-v8a/' + name]), ['arm64-v8a'])

    def test_selected_abi_missing_from_apk_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing=\\['x86_64'\\]"):
            verifier.verify(self.apk(), ['arm64-v8a', 'x86_64'])

    def test_unselected_abi_in_apk_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unexpected=\\['x86_64'\\]"):
            verifier.verify(self.apk(('arm64-v8a', 'x86_64')), ['arm64-v8a'])

    def test_omitted_empty_or_text_expected_abi_list_is_rejected(self):
        path = self.apk()
        for value in (None, [], [''], 'arm64-v8a'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'expected ABI list'):
                verifier.verify(path, value)

    def test_duplicate_expected_abi_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate expected ABI'):
            verifier.verify(self.apk(), ['arm64-v8a', 'arm64-v8a'])

    def test_unknown_expected_abi_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown expected ABI.*armeabi-v7a'):
            verifier.verify(self.apk(), ['armeabi-v7a'])

    def test_expected_abi_absent_from_trusted_preparation_is_rejected(self):
        self.manifest['abis'].pop('x86_64')
        self.write_prepared_manifest()
        with self.assertRaisesRegex(ValueError, 'Expected ABI absent from prepared runtime.*x86_64'):
            verifier.verify(self.apk(), ['x86_64'])

    def test_unknown_host_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown native host mode'):
            verifier.verify(self.apk(), ['arm64-v8a'], 'unapproved-no-python-mode')

    def test_existing_prepared_native_hash_check_remains_active(self):
        name = 'lib/arm64-v8a/libproot.so'
        with self.assertRaisesRegex(ValueError, 'Packaged PRoot library mismatch'):
            verifier.verify(self.apk(overrides={name: elf('libproot.so', 'arm64-v8a') + b'changed'}), ['arm64-v8a'])

    def test_existing_load_alignment_check_remains_active(self):
        name = 'lib/arm64-v8a/libpython3.11.so'
        with self.assertRaisesRegex(ValueError, 'Native load segment is not 16KiB compatible'):
            verifier.verify(self.apk(overrides={name: elf('libpython3.11.so', 'arm64-v8a', 4096)}), ['arm64-v8a'])

    def test_cli_requires_external_build_abi_selection(self):
        result = subprocess.run([sys.executable, str(HOST / 'verify_apk.py'), 'not-opened.apk'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--expected-abis', result.stderr)


if __name__ == '__main__':
    unittest.main()
