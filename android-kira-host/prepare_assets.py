"""Package original official Kira and checksum-bound Linux host artifacts."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNTIME = ROOT / '.runtime/android-linux-runtime'
OFFICIAL = ROOT / '.runtime/official-kira/v3.1.2'
OUTPUT = HERE / 'build/runtime'
COMMIT = '89013745ba3281bdc248e704bbcb619417cf4f57'
DIST_SHA = 'eba1639b7e7c7ca8089b8e3beb51be93c555cb42f19d6e51e40f9f9676affe09'
ABIS = {'arm64-v8a': ('arm64v8', 183), 'x86_64': ('amd64', 62)}
NATIVE = {'libproot.so', 'libprootloader.so', 'libtalloc.so', 'libandroid-shmem.so'}


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def verified_copy(source, target, checksum, size):
    if source.stat().st_size != size or sha(source) != checksum:
        raise ValueError('Artifact integrity mismatch: ' + source.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or sha(target) != checksum:
        shutil.copyfile(source, target)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def original_seed():
    actual = subprocess.check_output(['git', '-C', str(OFFICIAL), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != COMMIT:
        raise ValueError('Official seed repository is not at the reviewed release commit')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    # Read Git blobs directly. Git archive can apply the Windows checkout's
    # autocrlf settings, which would change bytes in the upstream release.
    tree = subprocess.check_output(['git', '-C', str(OFFICIAL), 'ls-tree', '-rz', COMMIT])
    objects = []
    for record in tree.split(b'\0'):
        if not record:
            continue
        metadata, path = record.split(b'\t', 1)
        mode, kind, oid = metadata.split()
        if kind != b'blob' or mode not in (b'100644', b'100755'):
            raise ValueError('Official seed contains an unsupported Git object')
        objects.append((mode, oid, path.decode('utf-8')))
    batch = subprocess.run(['git', '-C', str(OFFICIAL), 'cat-file', '--batch'],
                           input=b''.join(oid + b'\n' for _, oid, _ in objects),
                           stdout=subprocess.PIPE, check=True).stdout
    position = 0
    original_files = []
    for mode, oid, path in objects:
        end = batch.index(b'\n', position)
        actual_oid, kind, length = batch[position:end].split()
        size = int(length)
        data = batch[end + 1:end + 1 + size]
        if actual_oid != oid or kind != b'blob' or len(data) != size:
            raise ValueError('Official Git object delivery mismatch')
        calculated_oid = hashlib.sha1(b'blob ' + str(size).encode() + b'\0' + data).hexdigest()
        if calculated_oid != oid.decode():
            raise ValueError('Official Git blob digest mismatch')
        original_files.append((path, int(mode, 8), data))
        position = end + size + 2
    pip = json.loads((RUNTIME / 'artifacts/pip-progress-manifest.json').read_text(encoding='utf-8'))
    wheel = RUNTIME / 'artifacts/pip' / pip['filename']
    if sha(wheel) != pip['sha256'] or wheel.stat().st_size != pip['size']:
        raise ValueError('Pip wheel integrity mismatch')
    dist = ROOT / '.runtime/official-kira/artifacts/dist-v3.1.2.zip'
    if sha(dist) != DIST_SHA or dist.stat().st_size != 2232346:
        raise ValueError('Official frontend release integrity mismatch')
    with zipfile.ZipFile(dist) as frontend:
        icon_name = 'dist/icon.png' if 'dist/icon.png' in frontend.namelist() else 'icon.png'
        icon = OUTPUT / 'res/drawable/kira_icon.png'
        icon.parent.mkdir(parents=True, exist_ok=True)
        icon.write_bytes(frontend.read(icon_name))
    source_manifest = []
    seed = OUTPUT / 'assets/official-kira-seed.zip'
    seed.parent.mkdir(parents=True, exist_ok=True)
    temporary = seed.with_suffix('.part')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as dst:
        for name, mode, data in original_files:
            if name.startswith('/') or '..' in Path(name).parts:
                raise ValueError('Unsafe original source member')
            entry = zipfile.ZipInfo('KiraAI/' + name, (2026, 1, 1, 0, 0, 0))
            entry.external_attr = mode << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(entry, data)
            source_manifest.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
        for name, path in [('wheels/' + pip['filename'], wheel), ('dist.zip', dist)]:
            entry = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_STORED
            entry.external_attr = 0o100644 << 16
            dst.writestr(entry, path.read_bytes())
    temporary.replace(seed)
    write_json(OUTPUT / 'official-source-integrity.json', {'commit': COMMIT, 'version': 'v3.1.2', 'files': source_manifest})
    return ({'version': 'v3.1.2', 'initial_commit': COMMIT,
             'remote_url': 'https://github.com/KiraAI-Dev/KiraAI',
             'seed_sha256': sha(seed), 'seed_size': seed.stat().st_size},
            {key: pip[key] for key in ('filename', 'sha256', 'size')} | {'version': pip['package_version']},
            {'version': 'v3.1.2', 'sha256': DIST_SHA, 'size': dist.stat().st_size})


def prepare():
    native = json.loads((RUNTIME / 'artifacts/native-runtime-manifest.json').read_text(encoding='utf-8'))
    nodes = json.loads((RUNTIME / 'artifacts/node-runtime-manifest.json').read_text(encoding='utf-8'))
    roots = {item['architecture']: item for item in native['rootfs']}
    manifest = {'version': 1, 'abis': {}}
    for abi, (architecture, machine) in ABIS.items():
        records = native['native_binaries'][abi]
        if len(records) != 4 or {item['apk_filename'] for item in records} != NATIVE:
            raise ValueError('Incomplete native ABI set')
        hashes = {}
        for item in records:
            source = Path(item['source']).resolve()
            if not source.is_relative_to((RUNTIME / 'artifacts').resolve()):
                raise ValueError('Native source escapes artifacts directory')
            with source.open('rb') as stream:
                header = stream.read(64)
            if header[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', header, 18)[0] != machine:
                raise ValueError('Wrong native ELF architecture')
            verified_copy(source, OUTPUT / 'jniLibs' / abi / item['apk_filename'], item['sha256'], item['bytes'])
            hashes[item['apk_filename']] = item['sha256']
        root = roots[architecture]
        root_record = {'filename': f'debian-bookworm-{architecture}-rootfs.tar.gz',
                       'url': root['url'], 'sha256': root['sha256'], 'size': root['bytes']}
        node_record = {key: nodes['abis'][abi][key] for key in ('filename', 'url', 'sha256', 'size', 'version', 'topdir', 'strip_components')}
        node_record['minimum_kernel'] = nodes['compatibility']['minimum_kernel']
        for record, source in [(root_record, RUNTIME / 'artifacts' / architecture / 'debian-bookworm-slim-rootfs.tar.gz'),
                               (node_record, RUNTIME / 'artifacts/node' / node_record['filename'])]:
            record['asset'] = f'linux-runtime/{abi}/' + record['filename'] + '.payload'
            verified_copy(source, OUTPUT / 'bundled-assets' / abi / record['asset'], record['sha256'], record['size'])
        manifest['abis'][abi] = {'nativeSHA': hashes, 'rootfsArtifact': root_record, 'nodeArtifact': node_record}
    repository, pip, frontend = original_seed()
    manifest.update(officialRepository=repository, pipArtifact=pip, frontendArtifact=frontend)
    manifest['officialSeed'] = {'filename': 'official-kira-seed.zip', 'asset': 'official-kira-seed.zip',
                                'sha256': repository['seed_sha256'], 'size': repository['seed_size']}
    write_json(OUTPUT / 'assets/host-runtime.json', manifest)
    for directory, pattern, prefix in [(RUNTIME / 'artifacts/licenses', '*.txt', ''),
                                        (RUNTIME / 'artifacts/node', '*LICENSE*', 'Node-')]:
        for source in directory.glob(pattern):
            target = OUTPUT / 'assets/licenses' / (prefix + source.name)
            verified_copy(source, target, sha(source), source.stat().st_size)
    print(json.dumps(repository, indent=2))


if __name__ == '__main__':
    prepare()
