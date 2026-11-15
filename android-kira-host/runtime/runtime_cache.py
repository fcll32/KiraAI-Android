"""Import only required APK archives; retire proven disposable copies."""
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading

from linux_installer import checkpoint, digest
from runtime_paths import RuntimePaths, choose_base, generation_digest, read_private_json

RECEIPT = 'bundled-runtime-ready.json'
REPORT = 'cache-reclaim.json'
ROOT_FILES = ('bin/sh', 'usr/bin/env', 'etc/os-release')
SOURCE_FILES = ('main.py', 'requirements.txt', 'core/supervisor.py', 'core/launcher.py', 'webui/app.py')

def _read(path):
    try:
        value = read_private_json(path)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def _files(root, names):
    root = Path(root)
    return all((root / name).resolve().is_relative_to(root.resolve()) and (root / name).is_file()
               for name in names)

def validate_base(root):
    if not _files(root, ROOT_FILES):
        raise ValueError('基础环境关键文件缺失，已保留现有程序和数据，需修复后再启动')

def _node_ready(root, node):
    generation = root / 'opt' / ('kira-node-' + node['sha256'][:20])
    return (_read(generation / '.kira-node.json').get('sha256') == node['sha256']
            and _files(generation, ('bin/node', 'bin/npm', 'bin/npx', 'LICENSE')))

def _directory(path, boundary):
    """Reject linked parents before touching a disposable private copy."""
    current = Path(boundary)
    parts = Path(path).relative_to(current).parts
    for part in (None, *parts):
        if part is not None:
            current = current / part
        if current.is_symlink() or (os.path.lexists(current) and not current.is_dir()):
            raise ValueError('Cache parent is not an owned directory')

def _source_ready(home, root, manifest, cancel=None):
    paths = RuntimePaths(home, root)
    pip = manifest['pipArtifact']
    wheel = root / 'opt/kira-host/wheels' / pip['filename']
    return (_read(paths.state / 'official-install.json').get('state') == 'complete'
            and _files(root / 'opt/KiraAI', SOURCE_FILES)
            and _files(root / 'opt/kira-host/wheels', (pip['filename'],))
            and wheel.stat().st_size == pip['size'] and pip['size'] <= 32 * 1024 * 1024
            and digest(wheel, cancel or threading.Event()) == pip['sha256'])

def plan(home, manifest_json, abi):
    """Pure import decision; it never edits a guest, marker or user data."""
    manifest = json.loads(manifest_json) if isinstance(manifest_json, str) else manifest_json
    section = manifest['abis'][abi]
    base, node, seed = section['rootfsArtifact'], section['nodeArtifact'], manifest['officialSeed']
    needed = [base['filename'], node['filename'], seed['filename']]
    try:
        root = choose_base(Path(home), base['sha256'])
        if generation_digest(root) != base['sha256']:
            return json.dumps({'needed': needed})
        validate_base(root)
        record = _read(RuntimePaths(home).state / RECEIPT)
        if (record.get('schema') != 1 or record.get('abi') != abi or record.get('base_sha256') != base['sha256']
                or record.get('root') != root.relative_to(home).as_posix() or record.get('guest_verified') is not True):
            return json.dumps({'needed': needed})
        needed.remove(base['filename'])
        node_generation = root / 'opt' / ('kira-node-' + node['sha256'][:20])
        node_repair = os.path.lexists(node_generation) and not _node_ready(root, node)
        if node_repair or (record.get('node_sha256') == node['sha256'] and record.get('node_verified') is True
                and _node_ready(root, node)):
            needed.remove(node['filename'])
        if record.get('seed_sha256') == seed['sha256'] and _source_ready(home, root, manifest):
            needed.remove(seed['filename'])
        return json.dumps({'needed': needed, 'node_repair_required': node_repair})
    except (OSError, ValueError) as error:
        return json.dumps({'needed': [], 'repair_required': True, 'detail': str(error)}, ensure_ascii=False)

def _publish(path, value):
    fd, name = tempfile.mkstemp(prefix='.cache-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

def _same(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

def _retire(path, record, cancel):
    """Delete just this verified ordinary, exclusively linked archive."""
    checkpoint(cancel)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return 0
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != record['size']
            or digest(path, cancel) != record['sha256'] or _same(path.lstat()) != _same(info)):
        raise ValueError('Cache copy is not a verified disposable file')
    checkpoint(cancel)
    path.unlink()
    return getattr(info, 'st_blocks', 0) * 512

def complete(home, root, manifest, abi, cancel, *, node_verified):
    """Called only after the real Kira endpoint became ready in this run."""
    paths = RuntimePaths(home, root)
    _directory(paths.state / 'downloads', paths.home)
    section = manifest['abis'][abi]
    base, node, seed = section['rootfsArtifact'], section['nodeArtifact'], manifest['officialSeed']
    if generation_digest(root) != base['sha256'] or not _source_ready(home, root, manifest, cancel):
        raise ValueError('Installed runtime does not match its completion identity')
    validate_base(root)
    node_verified = node_verified is True and _node_ready(root, node)
    checkpoint(cancel)
    record = {'schema': 1, 'abi': abi, 'root': root.relative_to(home).as_posix(),
              'base_sha256': base['sha256'], 'node_sha256': node['sha256'], 'seed_sha256': seed['sha256'],
              'guest_verified': True, 'node_verified': node_verified is True}
    _publish(paths.state / RECEIPT, record)
    allowed = [base, seed] + ([node] if node_verified else [])
    report = {'schema': 1, 'reclaimed_allocated_bytes': 0, 'removed': [], 'retained': []}
    for artifact in allowed:
        name = artifact['filename']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}', name):
            raise ValueError('Unsafe cache artifact name')
        path = paths.state / 'downloads' / name
        try:
            existed = os.path.lexists(path)
            report['reclaimed_allocated_bytes'] += _retire(path, artifact, cancel)
            if existed:
                report['removed'].append(name)
        except (OSError, ValueError):
            report['retained'].append(name)
    # Preserve the previous useful measurement across already-clean startups.
    if report['removed'] or report['retained'] or not (paths.state / REPORT).exists():
        _publish(paths.state / REPORT, report)
    return report

def retire_attempt(home, root, attempt, copies, cancel):
    """Only the caller's completed, unreferenced attempt may invoke this."""
    attempt = Path(attempt)
    paths = RuntimePaths(home, root)
    if (attempt.parent != paths.state / 'napcat/attempts'
            or not re.fullmatch('[0-9a-f]{32}', attempt.name)
            or os.path.lexists(root / '.napcat-host-pending.json')):
        raise ValueError('Invalid completed attempt')
    _directory(attempt, paths.home)
    record = _read(attempt / 'attempt.json')
    if record.get('state') != 'complete' or record.get('copies') != copies:
        raise ValueError('Attempt is not a completed owned transaction')
    for name, identity in copies.items():
        if name not in ('install.sh', 'NapCat.Shell.zip', 'QQ.deb'):
            raise ValueError('Unexpected attempt copy')
        _retire(attempt / name, identity, cancel)
