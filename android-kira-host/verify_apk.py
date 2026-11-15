"""Verify packaged runtime bytes and original official source before delivery."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import zipfile

HERE = Path(__file__).resolve().parent
NATIVE_PROFILES = HERE / 'apk-native-profiles.json'
DEFAULT_HOST_MODE = 'chaquopy-3.11-v1'


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def native_expectations(manifest, expected_abis, host_mode):
    """Build selection and a repository-owned host mode define required files, never the APK."""
    if expected_abis is None or isinstance(expected_abis, (str, bytes)):
        raise ValueError('Explicit expected ABI list from the build selection is required')
    abis = list(expected_abis)
    if not abis or not all(isinstance(abi, str) and abi for abi in abis):
        raise ValueError('A non-empty expected ABI list from the build selection is required')
    if len(abis) != len(set(abis)):
        raise ValueError('Duplicate expected ABI in build selection')
    profile_bytes = NATIVE_PROFILES.read_bytes()
    profiles = json.loads(profile_bytes)
    if profiles.get('version') != 1 or host_mode not in profiles.get('modes', {}):
        raise ValueError('Unknown native host mode: ' + str(host_mode))
    profile = profiles['modes'][host_mode]
    unknown = set(abis) - set(profile['supportedAbis'])
    if unknown:
        raise ValueError('Unknown expected ABI for host mode: ' + ', '.join(sorted(unknown)))
    absent = set(abis) - set(manifest['abis'])
    if absent:
        raise ValueError('Expected ABI absent from prepared runtime: ' + ', '.join(sorted(absent)))
    host_libraries = {name for item in profile['hostNativeSources'] for name in item['requiredLibraries']}
    if not host_libraries:
        raise ValueError('Native host profile has no required libraries')
    required = {}
    for abi in sorted(abis):
        prepared = manifest['abis'][abi]['nativeSHA']
        if not prepared:
            raise ValueError('Prepared native library set is empty for ' + abi)
        required[abi] = sorted(host_libraries | set(prepared))
    return sorted(abis), required, {
        'mode': host_mode, 'sha256': checksum(profile_bytes),
        'host_native_sources': profile['hostNativeSources'],
        'prepared_native_source': profile['preparedNativeSource'],
    }


def verify(apk, expected_abis=None, host_mode=DEFAULT_HOST_MODE):
    manifest = json.loads((HERE / 'build/runtime/assets/host-runtime.json').read_text())
    originals = json.loads((HERE / 'build/runtime/official-source-integrity.json').read_text())
    abis, required, native_profile = native_expectations(manifest, expected_abis, host_mode)
    with zipfile.ZipFile(apk) as package:
        if package.testzip() is not None:
            raise ValueError('APK CRC verification failed')
        packaged_manifest = json.loads(package.read('assets/host-runtime.json'))
        if packaged_manifest != manifest:
            raise ValueError('APK runtime manifest differs from prepared assets')
        actual_abis = sorted({name.split('/')[1] for name in package.namelist()
                              if name.startswith('lib/') and name.endswith('.so')})
        if actual_abis != abis:
            missing = sorted(set(abis) - set(actual_abis))
            extra = sorted(set(actual_abis) - set(abis))
            raise ValueError('APK ABI set differs from build selection; missing=' + repr(missing)
                             + ', unexpected=' + repr(extra))
        files = set(package.namelist())
        for abi, names in required.items():
            missing = ['lib/' + abi + '/' + name for name in names if 'lib/' + abi + '/' + name not in files]
            if missing:
                raise ValueError('Missing required native libraries: ' + ', '.join(missing))
        seed = package.read('assets/official-kira-seed.zip')
        record = manifest['officialSeed']
        if checksum(seed) != record['sha256'] or len(seed) != record['size']:
            raise ValueError('APK official seed mismatch')
        with zipfile.ZipFile(io.BytesIO(seed)) as source:
            for item in originals['files']:
                data = source.read('KiraAI/' + item['path'])
                if len(data) != item['size'] or checksum(data) != item['sha256']:
                    raise ValueError('Official source bytes changed: ' + item['path'])
            actual = {name for name in source.namelist() if name.startswith('KiraAI/')}
            if actual != {'KiraAI/' + item['path'] for item in originals['files']}:
                raise ValueError('Unexpected files in official source seed')
            for name, key in [('dist.zip', 'frontendArtifact'), ('wheels/' + manifest['pipArtifact']['filename'], 'pipArtifact')]:
                data = source.read(name)
                if checksum(data) != manifest[key]['sha256'] or len(data) != manifest[key]['size']:
                    raise ValueError('Official seed dependency mismatch')
        elf_count = 0
        for name in package.namelist():
            if not name.startswith('lib/') or not name.endswith('.so'):
                continue
            data = package.read(name)
            if data[:6] != b'\x7fELF\x02\x01':
                raise ValueError('Unexpected native library format')
            header = struct.unpack_from('<Q', data, 32)[0]
            size, count = struct.unpack_from('<HH', data, 54)
            for index in range(count):
                offset = header + index * size
                if struct.unpack_from('<I', data, offset)[0] == 1:
                    alignment = struct.unpack_from('<Q', data, offset + 48)[0]
                    if alignment < 16384:
                        raise ValueError('Native load segment is not 16KiB compatible: ' + name)
            elf_count += 1
        for abi in abis:
            section = manifest['abis'][abi]
            for name, digest in section['nativeSHA'].items():
                if checksum(package.read('lib/' + abi + '/' + name)) != digest:
                    raise ValueError('Packaged PRoot library mismatch')
            for key in ('rootfsArtifact', 'nodeArtifact'):
                item = section[key]
                data = package.read('assets/' + item['asset'])
                if len(data) != item['size'] or checksum(data) != item['sha256']:
                    raise ValueError('Packaged Linux archive mismatch')
    data = apk.read_bytes()
    return {'apk': str(apk.resolve()), 'size': len(data), 'sha256': checksum(data),
            'abis': abis, 'native_libraries_16k_verified': elf_count,
            'expected_abis': abis, 'native_profile': native_profile,
            'required_native_libraries': required,
            'official_version': manifest['officialRepository']['version'],
            'official_commit': originals['commit'], 'official_files_unchanged': len(originals['files']),
            'seed_sha256': record['sha256'], 'apk_crc_valid': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('apk', type=Path)
    parser.add_argument('--expected-abis', required=True,
                        help='Comma-separated ABI list from the same -PhostAbis build selection; never inferred from APK')
    parser.add_argument('--host-mode', default=DEFAULT_HOST_MODE,
                        help='Repository-owned versioned native host profile (default: %(default)s)')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = verify(args.apk, args.expected_abis.split(','), args.host_mode)
    serialized = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + '\n', encoding='utf-8')
    print(serialized)
