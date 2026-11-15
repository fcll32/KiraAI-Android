"""Measure private data and check additional disk space before owned writes."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile

from linux_installer import MAX_ROOTFS_BYTES, MAX_ROOTFS_FILES, checkpoint, digest


BLOCK_ESTIMATE = 4096
SAFETY_MARGIN = 32 * 1024 * 1024
REPORT = 'space-preflight.json'


class SpaceUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Allocation:
    role: str
    destination: Path
    bytes: int


def estimated_bytes(size):
    """Round file content to 4 KiB and allow a block per entry for metadata."""
    return ((size + BLOCK_ESTIMATE - 1) // BLOCK_ESTIMATE + 1) * BLOCK_ESTIMATE


def _directory(path):
    path = Path(path).absolute()
    while True:
        try:
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise OSError('Storage destination is not a private directory')
            return path
        except FileNotFoundError:
            if path == path.parent:
                raise
            path = path.parent


def require_space(action, allocations, cancel, report_path=None, *, alternative_phases=()):
    """Check the concurrent extra bytes on each distinct destination volume."""
    volumes = {}
    for phase in (allocations, *alternative_phases):
        for identity, use in _volume_totals(phase, cancel).items():
            previous = volumes.setdefault(identity, use)
            if use['extra_bytes'] > previous['extra_bytes']:
                previous['roles'] = use['roles']
                previous['extra_bytes'] = use['extra_bytes']
            free, other = previous['available_bytes'], use['available_bytes']
            previous['available_bytes'] = None if free is None or other is None else min(free, other)
    decision = 'sufficient'
    for volume in volumes.values():
        volume['reserve_bytes'] = SAFETY_MARGIN
        volume['required_bytes'] = volume['extra_bytes'] + SAFETY_MARGIN
        available = volume['available_bytes']
        if available is None:
            volume['decision'] = 'unknown'
            decision = 'unknown'
        elif available < volume['required_bytes']:
            volume['decision'] = 'insufficient'
            if decision != 'unknown':
                decision = 'insufficient'
        else:
            volume['decision'] = 'sufficient'
    result = {'version': 1, 'action': action, 'decision': decision, 'volumes': list(volumes.values())}
    checkpoint(cancel)
    if report_path is not None:
        _write_report(Path(report_path), result)
    if decision == 'unknown':
        raise SpaceUnavailable('无法确认本次操作所需的可用空间')
    if decision == 'insufficient':
        raise SpaceUnavailable('可用存储不足，请在设备状态查看预计所需空间')
    return result


def _volume_totals(allocations, cancel):
    volumes = {}
    for allocation in allocations:
        checkpoint(cancel)
        if type(allocation.bytes) is not int or allocation.bytes < 0:
            raise ValueError('Invalid additional storage requirement')
        if allocation.bytes == 0:
            continue
        try:
            directory = _directory(allocation.destination)
            identity = ('volume', directory.stat().st_dev)
        except OSError:
            directory = None
            identity = ('unknown', allocation.destination)
        try:
            available = shutil.disk_usage(directory).free if directory is not None else None
        except OSError:
            available = None
        volume = volumes.setdefault(identity, {'roles': [], 'extra_bytes': 0, 'available_bytes': available})
        volume['roles'].append(allocation.role)
        volume['extra_bytes'] += allocation.bytes
        previous = volume['available_bytes']
        volume['available_bytes'] = None if previous is None or available is None else min(previous, available)
    return volumes


def _write_report(path, report):
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix='.space-', suffix='.tmp', dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False)
        os.replace(temporary, path)
    except OSError:
        # A full disk can also refuse the diagnostic file. Keep the original decision.
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def download_bytes(artifact, cache, cancel):
    target = Path(cache) / artifact.filename
    partial = target.with_name(target.name + '.part')
    if target.is_symlink() or partial.is_symlink():
        raise ValueError('Runtime download cache must not contain symbolic links')
    if target.is_file() and target.stat().st_size == artifact.size and digest(target, cancel) == artifact.sha256:
        return 0
    resumed = 0
    if partial.is_file():
        info = partial.stat()
        if info.st_nlink == 1:
            resumed = min(info.st_size, getattr(info, 'st_blocks', 0) * 512)
    # An oversize part is discarded by the downloader before any new writes.
    return artifact.size - min(artifact.size, resumed)


def tree_size(path, cancel, *, skip_top_level=()):
    """Return logical, allocated and copy estimates without following links."""
    path = Path(path)
    logical = copy = count = 0
    allocated = 0
    stack = [path]
    seen = set()
    while stack:
        checkpoint(cancel)
        current = stack.pop()
        try:
            info = current.lstat()
        except FileNotFoundError:
            if current == path:
                continue
            raise
        count += 1
        if count > MAX_ROOTFS_FILES:
            raise SpaceUnavailable('文件数量超过空间预检上限')
        identity = info.st_dev, info.st_ino
        if identity not in seen:
            seen.add(identity)
            blocks = getattr(info, 'st_blocks', None)
            allocated = None if allocated is None or blocks is None else allocated + blocks * 512
        regular = stat.S_ISREG(info.st_mode)
        logical += info.st_size if regular else 0
        copy += estimated_bytes(info.st_size if regular else 0)
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(current) as children:
                for child in children:
                    checkpoint(cancel)
                    if current != path or child.name not in skip_top_level:
                        stack.append(Path(child.path))
                    if len(stack) + count > MAX_ROOTFS_FILES:
                        raise SpaceUnavailable('文件数量超过空间预检上限')
    return {'logical_bytes': logical, 'allocated_bytes': allocated, 'copy_bytes': copy}


def tar_bytes(path, cancel):
    total = logical = 0
    sizes, hardlinks = {}, []
    with tarfile.open(path, 'r:*') as archive:
        for count, member in enumerate(archive, start=1):
            checkpoint(cancel)
            size = member.size if member.isfile() else 0
            logical += size
            total += estimated_bytes(size)
            if count > MAX_ROOTFS_FILES or logical > MAX_ROOTFS_BYTES:
                raise ValueError('Runtime archive exceeds installation limits')
            if member.isfile():
                sizes[member.name.lstrip('./')] = size
            elif member.islnk():
                hardlinks.append(member.linkname.lstrip('./'))
    # The extractor can fall back to copying hard links on unsupported filesystems.
    return total + sum(estimated_bytes(sizes.get(name, 0)) for name in hardlinks)


def zip_bytes(archive, cancel):
    total = logical = 0
    for count, member in enumerate(archive.infolist(), start=1):
        checkpoint(cancel)
        logical += member.file_size
        total += estimated_bytes(member.file_size)
        if count > MAX_ROOTFS_FILES or logical > MAX_ROOTFS_BYTES:
            raise ValueError('Runtime archive exceeds installation limits')
    return total
