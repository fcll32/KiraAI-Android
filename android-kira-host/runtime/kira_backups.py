"""Validate Kira backup publication before retiring older completed archives.

Restoration replaces the program and data trees only after a verified archive has
been unpacked into a private staging directory. The trees it replaces are moved
aside, never deleted by the swap, and an interrupted swap is rolled back.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import time
import uuid
from linux_installer import checkpoint
from runtime_paths import KIRA_DATA, KIRA_PROGRAM, RuntimePaths, private_path, read_private_json

LIMIT = 2
NAME = re.compile(r'kira-\d{8}-\d{6}-[0-9a-f]{8}\.tar')
RESTORE_DIR = 'restore'
RESTORE_JOURNAL = 'journal.json'
RESTORE_SCHEMA = 'kira-restore/v1'
# The official entry points a restored program must contain before it may replace the current one.
RESTORE_REQUIRED = ('main.py', 'requirements.txt', 'core/supervisor.py', 'core/launcher.py', 'webui/app.py')
RESTORE_MEMBER_LIMIT = 500_000
_ROOTS = ((KIRA_PROGRAM[1:], 'program'), (KIRA_DATA[1:], 'data'))
_STAGE = re.compile(r'stage-[0-9a-f]{32}')
_PREVIOUS = re.compile(r'previous-\d{8}-\d{6}-[0-9a-f]{8}')


def _regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Backup must be a regular private file')
    return info


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _write_record(path, record):
    temporary = path.with_name(path.name + '.tmp')
    if temporary.is_symlink() or path.is_symlink():
        raise ValueError('Backup completion record is not private')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _hash(path, cancel, before):
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
            raise ValueError('Backup changed before validation')
        while data := stream.read(128 * 1024):
            checkpoint(cancel)
            digest.update(data)
    if _identity(before) != _identity(_regular(path)):
        raise ValueError('Backup changed during validation')
    return digest.hexdigest()


def _inspect(path, cancel):
    before = _regular(path)
    if before.st_size < 1024 or before.st_size % 512:
        raise ValueError('Backup archive is incomplete')
    with path.open('rb') as stream:
        stream.seek(-1024, 2)
        if stream.read() != bytes(1024):
            raise ValueError('Backup archive has no complete end marker')
    roots = set()
    with tarfile.open(path, 'r:') as archive:
        for member in archive:
            checkpoint(cancel)
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts:
                raise ValueError('Backup archive contains an unsafe path')
            text = name.as_posix().rstrip('/')
            root = next((root for root in (KIRA_PROGRAM[1:], KIRA_DATA[1:])
                         if text == root or text.startswith(root + '/')), None)
            if root is None or not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError('Backup archive contains an unexpected object')
            roots.add(root)
            if member.isfile():
                with archive.extractfile(member) as stream:
                    while stream.read(128 * 1024):
                        checkpoint(cancel)
    if roots != {KIRA_PROGRAM[1:], KIRA_DATA[1:]}:
        raise ValueError('Backup archive is missing program or user data')
    return before, _hash(path, cancel, before)


def _record(path, info, digest, created=None):
    return {'schema': 'kira-backup/v1', 'state': 'complete', 'filename': path.name,
            'size': info.st_size, 'sha256': digest, 'archive_mtime_ns': info.st_mtime_ns,
            'archive_inode': info.st_ino,
            'created_at_unix_ms': created if created is not None else info.st_mtime_ns // 1_000_000}


def _completed(path, cancel):
    record_path = path.with_name(path.name + '.json')
    try:
        record = read_private_json(record_path, 8192)
    except FileNotFoundError:
        # Legacy archives require full inspection before entering retention.
        info, digest = _inspect(path, cancel)
        record = _record(path, info, digest)
        _write_record(record_path, record)
        return record, info
    info = _regular(path)
    if (not isinstance(record, dict) or record.get('schema') != 'kira-backup/v1' or record.get('state') != 'complete'
            or record.get('filename') != path.name or record.get('size') != info.st_size
            or type(record.get('created_at_unix_ms')) is not int
            or not re.fullmatch('[0-9a-f]{64}', str(record.get('sha256', '')))):
        raise ValueError('Backup completion record is invalid')
    if _hash(path, cancel, info) != record['sha256']:
        raise ValueError('Backup content does not match its completion record')
    if record.get('archive_mtime_ns') != info.st_mtime_ns or record.get('archive_inode') != info.st_ino:
        record = _record(path, info, record['sha256'], record['created_at_unix_ms'])
        _write_record(record_path, record)
    return record, info


def finalize_backup(folder, filename, cancel):
    """Commit a validated .partial and retain two verified archives; never restore data."""
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir() or not NAME.fullmatch(filename):
        raise ValueError('Invalid private backup destination')
    destination = folder / filename
    partial = folder / (filename + '.partial')
    if destination.exists() or destination.is_symlink():
        raise ValueError('Backup name already exists')
    info, digest = _inspect(partial, cancel)
    checkpoint(cancel)
    # This bounded publication is the commit point. Cancellation before it never retires old data.
    os.replace(partial, destination)
    record = _record(destination, info, digest, int(time.time() * 1000))
    _write_record(folder / (filename + '.json'), record)
    warnings = []
    completed = []
    for path in folder.iterdir():
        if not NAME.fullmatch(path.name) or path.is_symlink():
            continue
        try:
            if path == destination:
                completed.append((record, info, path))
            else:
                old_record, old_info = _completed(path, cancel)
                completed.append((old_record, old_info, path))
        except (OSError, ValueError, tarfile.TarError):
            warnings.append('An existing backup could not be verified and was retained')
    checkpoint(cancel)
    completed.sort(key=lambda item: (item[0]['created_at_unix_ms'], item[2].name), reverse=True)
    # Keep the newly validated backup even if the clock moved backwards.
    keep = {destination}
    for _, _, path in completed:
        if len(keep) < LIMIT:
            keep.add(path)
    for _, snapshot, path in completed:
        if path in keep:
            continue
        try:
            metadata = path.with_name(path.name + '.json')
            _regular(metadata)
            if _identity(_regular(path)) != _identity(snapshot):
                raise ValueError('Backup changed before retirement')
            path.unlink()
            metadata.unlink(missing_ok=True)
        except (OSError, ValueError):
            warnings.append('An old backup could not be retired; check backup management')
    return record, warnings


def _locate(name):
    """Map an archive member name to its tree ('program' or 'data') and the path inside that tree."""
    path = PurePosixPath(name)
    if not name or '\0' in name or path.is_absolute() or '..' in path.parts:
        raise ValueError('Backup archive contains an unsafe path')
    text = path.as_posix()
    for root, kind in _ROOTS:
        if text == root:
            return kind, ()
        if text.startswith(root + '/'):
            return kind, PurePosixPath(text[len(root) + 1:]).parts
    raise ValueError('Backup archive contains an unexpected object')


def _check_link(parts, target):
    """A link may only point inside the tree that holds it, or at a guest path inside either tree."""
    link = PurePosixPath(target)
    if not target or '\0' in target:
        raise ValueError('Backup archive contains an unsafe link')
    if link.is_absolute():
        text = link.as_posix()
        if '..' in link.parts or not any(text == root or text.startswith(root + '/') for root in (KIRA_PROGRAM, KIRA_DATA)):
            raise ValueError('Backup archive contains a link that leaves the restored data')
        return
    depth = list(parts[:-1])
    for part in link.parts:
        if part == '..':
            if not depth:
                raise ValueError('Backup archive contains a link that leaves the restored data')
            depth.pop()
        elif part != '.':
            depth.append(part)


def _real_directory(base, parts):
    """Create missing ancestors as private directories; refuse to pass through links or files."""
    current = base
    for part in parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            os.mkdir(current, 0o700)
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Backup archive writes through a link or a file')
    return current


def _extract(archive_path, stage, cancel):
    """Unpack a verified archive into stage/program and stage/data without following anything it contains."""
    for _, kind in _ROOTS:
        os.mkdir(stage / kind, 0o700)
    directories = []
    count = 0
    with tarfile.open(archive_path, 'r:') as archive:
        for member in archive:
            checkpoint(cancel)
            count += 1
            if count > RESTORE_MEMBER_LIMIT:
                raise ValueError('Backup archive contains too many objects')
            kind, parts = _locate(member.name)
            base = stage / kind
            if member.isdir():
                target = _real_directory(base, parts)
                directories.append((target, member.mode, member.mtime))
                continue
            if not parts:
                raise ValueError('Backup archive contains an unexpected object')
            target = _real_directory(base, parts[:-1]) / parts[-1]
            if os.path.lexists(target):
                raise ValueError('Backup archive contains a duplicate object')
            if member.isfile():
                with archive.extractfile(member) as source, target.open('xb') as output:
                    while data := source.read(128 * 1024):
                        checkpoint(cancel)
                        output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(target, member.mode & 0o777)
                os.utime(target, (member.mtime, member.mtime))
            elif member.issym():
                _check_link(parts, member.linkname)
                os.symlink(member.linkname, target)
            elif member.islnk():
                linked_kind, linked_parts = _locate(member.linkname)
                if linked_kind != kind or not linked_parts:
                    raise ValueError('Backup archive contains a link that leaves the restored data')
                source = _real_directory(base, linked_parts[:-1]) / linked_parts[-1]
                if not stat.S_ISREG(source.lstat().st_mode):
                    raise ValueError('Backup archive contains a hard link to a missing file')
                try:
                    os.link(source, target)
                except OSError:
                    # Some private filesystems refuse hard links; an independent copy keeps the content.
                    with source.open('rb') as reader, target.open('xb') as output:
                        while data := reader.read(128 * 1024):
                            checkpoint(cancel)
                            output.write(data)
                    shutil.copystat(source, target, follow_symlinks=False)
            else:
                raise ValueError('Backup archive contains an unexpected object')
    # Directory modes go last so that read-only directories never block their own contents.
    for target, mode, mtime in reversed(directories):
        os.chmod(target, (mode & 0o777) | 0o700)
        os.utime(target, (mtime, mtime))


def _restore_work(home):
    state = RuntimePaths(home).state
    work = private_path(state, RESTORE_DIR)
    if work.is_symlink() or (os.path.lexists(work) and not work.is_dir()):
        raise ValueError('Restore working directory is not private')
    return work


def _owned(work, name, pattern):
    """A direct, real child directory of the working directory whose name this module generated."""
    if not isinstance(name, str) or not pattern.fullmatch(name):
        raise ValueError('Restore journal names an unexpected directory')
    return work / name


def _remove_tree(path):
    if path.is_symlink():
        path.unlink()
    elif os.path.lexists(path):
        # Unpacked directories may be read-only; make them traversable before removal.
        for folder, names, _ in os.walk(path):
            for name in names:
                child = Path(folder) / name
                if not child.is_symlink():
                    os.chmod(child, 0o700)
        shutil.rmtree(path)


def _move(source, destination):
    if os.path.lexists(destination):
        raise ValueError('Restore destination is already occupied')
    os.rename(source, destination)


def recover_restore(home):
    """Finish or undo a swap that a crash interrupted. Returns 'none', 'completed' or 'rolled_back'."""
    home = Path(home)
    work = _restore_work(home)
    journal_path = work / RESTORE_JOURNAL
    try:
        journal = read_private_json(journal_path, 8192)
    except FileNotFoundError:
        return 'none'
    if not isinstance(journal, dict) or journal.get('schema') != RESTORE_SCHEMA:
        raise ValueError('Restore journal is invalid; program and data were left untouched')
    if journal.get('state') != 'swapping':
        return 'none'
    stage = _owned(work, journal.get('stage'), _STAGE)
    previous = _owned(work, journal.get('previous'), _PREVIOUS)
    if not isinstance(journal.get('program'), str) or not isinstance(journal.get('data'), str):
        raise ValueError('Restore journal is invalid; program and data were left untouched')
    program = private_path(home, journal['program'])
    data = private_path(home, journal['data'])
    moved = {'program': program, 'data': data}
    if not os.path.lexists(stage / 'program') and not os.path.lexists(stage / 'data'):
        # Both new trees left staging, so both are in place: only the bookkeeping was interrupted.
        if not (program.is_dir() and data.is_dir()):
            raise ValueError('Restore journal does not match the directories on disk')
        outcome = 'completed'
    else:
        # Not every new tree arrived: put the new ones back into staging, then return the old ones.
        for kind, target in moved.items():
            if not os.path.lexists(stage / kind) and os.path.lexists(target):
                _move(target, stage / kind)
        for kind, target in moved.items():
            if os.path.lexists(previous / kind) and not os.path.lexists(target):
                _move(previous / kind, target)
        outcome = 'rolled_back'
    _finish_restore(work, journal, stage, previous, program, outcome)
    return outcome


def _finish_restore(work, journal, stage, previous, program, outcome):
    if outcome == 'completed':
        # The virtual environment belongs to the base system, not to the archive: carry it over.
        venv = previous / 'program' / 'venv'
        if os.path.lexists(venv) and not os.path.lexists(program / 'venv'):
            _move(venv, program / 'venv')
    _write_record(work / RESTORE_JOURNAL, {**journal, 'state': outcome, 'finished_at_unix_ms': int(time.time() * 1000)})
    _remove_tree(stage)
    if outcome == 'completed':
        # Exactly one set of replaced trees is kept: the one this restore just set aside.
        for path in list(work.iterdir()):
            if _PREVIOUS.fullmatch(path.name) and path != previous:
                _remove_tree(path)
    elif os.path.lexists(previous) and not any(previous.iterdir()):
        previous.rmdir()


def restore_backup(home, folder, filename, expected_sha256, program, data, cancel, *, required=RESTORE_REQUIRED):
    """Replace the stopped Kira program and data with a verified archive.

    The caller has stopped Kira and checked free space. Nothing is published until
    the whole archive is unpacked and checked; the replaced trees stay in
    linux-runtime/restore/previous-*, and the existing venv is carried over.
    """
    home, folder, program, data = Path(home), Path(folder), Path(program), Path(data)
    if folder.is_symlink() or not folder.is_dir() or not isinstance(filename, str) or not NAME.fullmatch(filename):
        raise ValueError('Invalid private backup source')
    recover_restore(home)
    work = _restore_work(home)
    work.mkdir(parents=True, exist_ok=True)
    for kind, target in (('program', program), ('data', data)):
        if target.is_symlink() or (os.path.lexists(target) and not target.is_dir()):
            raise ValueError('Current ' + kind + ' location is not a private directory')
        if not target.parent.is_dir() or target.parent.stat().st_dev != work.stat().st_dev:
            raise ValueError('Restore cannot swap directories across storage volumes')
    relative = {'program': program.resolve().relative_to(home.resolve()).as_posix(),
                'data': data.resolve().relative_to(home.resolve()).as_posix()}
    archive = folder / filename
    record, info = _completed(archive, cancel)
    if expected_sha256 is not None and record['sha256'] != expected_sha256:
        raise ValueError('Backup changed after it was chosen for restoration')
    # Leftover staging from an earlier cancelled or crashed attempt is never part of a journalled swap.
    for path in work.iterdir():
        if _STAGE.fullmatch(path.name):
            _remove_tree(path)
    stage = work / ('stage-' + uuid.uuid4().hex)
    os.mkdir(stage, 0o700)
    try:
        _extract(archive, stage, cancel)
        staged = stage / 'program'
        if not all((staged / item).is_file() and not (staged / item).is_symlink() for item in required):
            raise ValueError('Backup does not contain a complete Kira program')
        if os.path.lexists(staged / 'venv'):
            raise ValueError('Backup unexpectedly contains a Python environment')
        if _identity(info) != _identity(_regular(archive)):
            raise ValueError('Backup changed during restoration')
        checkpoint(cancel)
    except BaseException:
        _remove_tree(stage)
        raise
    # Commit point: from here the swap either completes or is rolled back, and cancellation no longer applies.
    previous = work / ('previous-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    os.mkdir(previous, 0o700)
    journal = {'schema': RESTORE_SCHEMA, 'state': 'swapping', 'stage': stage.name, 'previous': previous.name,
               'filename': filename, 'sha256': record['sha256'], 'program': relative['program'], 'data': relative['data'],
               'started_at_unix_ms': int(time.time() * 1000)}
    _write_record(work / RESTORE_JOURNAL, journal)
    try:
        for kind, target in (('program', program), ('data', data)):
            if os.path.lexists(target):
                _move(target, previous / kind)
        for kind, target in (('program', program), ('data', data)):
            _move(stage / kind, target)
    except BaseException:
        recover_restore(home)
        raise
    _finish_restore(work, journal, stage, previous, program, 'completed')
    return {'filename': filename, 'sha256': record['sha256'], 'previous': previous.relative_to(home).as_posix()}
