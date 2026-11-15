"""Move KiraAI and NapCat from the active private Debian base to a new one (plan R2B).

Never started automatically: an existing installation only moves when the host calls
`migrate` on an explicit user request, and rollback only when it calls `rollback`.

Migration (journal states preparing -> backed_up -> verified -> root_published -> complete):
1. The source base's KiraAI program (without its venv), host tools, NapCat program and QQ
   data are copied into a verified backup; KiraAI data lives outside the base and is shared.
2. The new base is unpacked into a staging directory, the backed-up trees are copied in
   and re-verified, and `prepare_target` installs system packages and rebuilds the venv
   there, bound to a throwaway copy of the KiraAI data so the real data is never touched.
3. Only then is the staging directory published and the base binding switched. The old
   base is kept unchanged. Every step can be re-run after an interruption.

Rollback (complete -> rollback_copying -> rollback_ready -> rolled_back) switches back to the
old base and carries NapCat and QQ data written after the migration with it. It is refused
once the KiraAI program in the new base has changed (an official update): the shared KiraAI
data may then have a format the old program cannot read, and a KiraAI backup restore is the
safe way back.

Derived from the R2B prototype that ran on an Android 11 emulator fixture on 2026-11-14.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import uuid

from linux_installer import checkpoint, digest, unpack_rootfs
from runtime_paths import (BASE_BINDING, KIRA_DATA, KIRA_PROGRAM, KIRA_REQUIREMENTS, NAPCAT_DATA, NAPCAT_PENDING,
                           NAPCAT_PROGRAM, ROOT_MARKER, RuntimePaths, binding_value, bound_base, generation_digest,
                           private_path, read_private_json)
from storage_space import Allocation, require_space, tar_bytes, tree_size

_COPIED = {
    'kira_program': KIRA_PROGRAM,
    'host_tools': '/opt/kira-host',
    'napcat_program': NAPCAT_PROGRAM,
    'napcat_data': NAPCAT_DATA,
}
# Written by NapCat and QQ after a migration and carried back by a rollback.
_CARRIED = ('napcat_program', 'napcat_data')
_LIMIT = 200_000
JOURNAL = 'transaction.json'


class MigrationRefused(ValueError):
    """Nothing was changed; the message is safe to show."""


def _write_record(path, value):
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix='migration-record-')
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _identity(path, cancel, *, skip_venv=False, copy_to=None):
    """Hash a tree (optionally while copying it), never following links out of it."""
    rows = []
    root = Path(path)
    stack = [(root, PurePosixPath('.'))]
    while stack:
        checkpoint(cancel)
        if len(rows) >= _LIMIT:
            raise ValueError('Migration tree exceeds its file count limit')
        current, relative = stack.pop()
        try:
            info = current.lstat()
        except FileNotFoundError:
            if current == root:
                return {'present': False}
            raise
        output = None if copy_to is None else copy_to.joinpath(*relative.parts)
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISDIR(info.st_mode):
            rows.append([relative.as_posix(), 'directory', mode])
            if output is not None:
                output.mkdir()
            children = []
            for child in current.iterdir():
                checkpoint(cancel)
                if skip_venv and current == root and child.name == 'venv':
                    continue
                if len(rows) + len(stack) + len(children) >= _LIMIT:
                    raise ValueError('Migration tree exceeds its file count limit')
                children.append((child, relative / child.name))
            stack.extend(sorted(children, reverse=True))
        elif stat.S_ISREG(info.st_mode):
            sha = hashlib.sha256()
            flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
            with os.fdopen(os.open(current, flags), 'rb') as source:
                opened = os.fstat(source.fileno())
                if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError('Source changed while reading migration input')
                if output is None:
                    while chunk := source.read(128 * 1024):
                        checkpoint(cancel)
                        sha.update(chunk)
                else:
                    with output.open('xb') as target:
                        while chunk := source.read(128 * 1024):
                            checkpoint(cancel)
                            sha.update(chunk)
                            target.write(chunk)
                        target.flush()
                        os.fsync(target.fileno())
                    output.chmod(mode)
            rows.append([relative.as_posix(), 'file', mode, info.st_size, sha.hexdigest()])
        elif stat.S_ISLNK(info.st_mode):
            link = os.readlink(current)
            if (current == root or Path(link).is_absolute() or '\\' in link
                    or not (current.parent / link).resolve().is_relative_to(root.resolve())):
                raise ValueError('Migration link escapes its program or data tree')
            rows.append([relative.as_posix(), 'link', link])
            if output is not None:
                output.symlink_to(link, target_is_directory=current.is_dir())
        else:
            raise ValueError('Migration input contains an unsupported special file')
    if copy_to is not None:
        for relative, kind, *info in reversed(rows):
            if kind == 'directory':
                copy_to.joinpath(*PurePosixPath(relative).parts).chmod(info[0])
    identity = hashlib.sha256(json.dumps(rows, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
    return {'present': True, 'sha256': identity, 'entries': len(rows)}


def _source_identities(paths, cancel, backup=None):
    roles = {**_COPIED, 'kira_data': KIRA_DATA}
    return {role: _identity(paths.from_guest(guest), cancel, skip_venv=role == 'kira_program',
                            copy_to=backup / role if backup is not None else None)
            for role, guest in roles.items()}


def _verify_backup(backup, identities, cancel):
    if backup.is_symlink() or not backup.is_dir():
        raise ValueError('Migration backup is not an owned private directory')
    for role, identity in identities.items():
        if _identity(backup / role, cancel) != identity:
            raise ValueError('Migration backup no longer matches its recorded source')


def _verify_target(paths, identities, cancel):
    for role, guest in _COPIED.items():
        if _identity(paths.from_guest(guest), cancel, skip_venv=role == 'kira_program') != identities[role]:
            raise ValueError('Staged program or data differs from the verified backup')
    if _identity(paths.data, cancel) != identities['kira_data']:
        raise ValueError('Kira data changed while migration was pending')


def _environment_identity(paths, cancel, verify_guest):
    requirements_sha = digest(paths.from_guest(KIRA_REQUIREMENTS), cancel)
    result = verify_guest(paths, cancel)
    if (not isinstance(result, dict) or result.get('requirements_sha256') != requirements_sha
            or result.get('interpreter_verified') is not True or result.get('dependency_check_passed') is not True):
        raise ValueError('Target dependency environment has no verified interpreter or requirements identity')
    # Only fixed noncredential facts are committed to the journal.
    return {'requirements_sha256': requirements_sha, 'interpreter_verified': True, 'dependency_check_passed': True}


@contextmanager
def _isolated_probe_paths(workspace, root, backup, identities, cancel):
    """A throwaway app home bound to the staged base and a copy of the KiraAI data."""
    with tempfile.TemporaryDirectory(dir=workspace, prefix='probe-') as probe_home:
        paths = RuntimePaths(probe_home, root)
        paths.state.mkdir()
        (paths.state / 'shm').mkdir()
        if identities['kira_data']['present']:
            if _identity(backup / 'kira_data', cancel, copy_to=paths.data) != identities['kira_data']:
                raise ValueError('Staged Kira probe data differs from the verified backup')
        else:
            paths.data.mkdir()
        yield paths


def _workspaces(home):
    directory = RuntimePaths(home).state / 'migrations'
    if not directory.is_dir() or directory.is_symlink():
        return []
    return [path for path in sorted(directory.iterdir())
            if not path.is_symlink() and path.is_dir() and (path / JOURNAL).is_file()]


def status(home):
    """Facts for a UI: the latest migration's state and whether a rollback could be attempted."""
    home = Path(home).resolve()
    current = bound_base(home)
    latest = None
    for workspace in _workspaces(home):
        try:
            record = read_private_json(workspace / JOURNAL)
        except (OSError, ValueError):
            continue
        latest = {'state': record.get('state'), 'source': record.get('source'), 'target': record.get('target')}
    if latest is None:
        return {'state': 'none'}
    active = binding_value(home, *current) if current else None
    latest['active_is_target'] = active == latest['target']
    return latest


def migrate(home, artifact, archive, cancel, *, exclusive_owner, prepare_target, verify_guest, publish_binding=None):
    """Move the stopped installation to `artifact`'s base under the caller's exclusive execution lease.

    prepare_target(paths, cancel, napcat_present) installs system packages and rebuilds the
    venv inside the staged base; verify_guest(paths, cancel) proves the interpreter works.
    """
    with exclusive_owner:
        return _migrate(home, artifact, archive, cancel, prepare_target, verify_guest, publish_binding)


def _migrate(home, artifact, archive, cancel, prepare_target, verify_guest, publish_binding):
    home = Path(home).resolve()
    runtime = RuntimePaths(home).state
    current = bound_base(home)
    if current is None:
        raise MigrationRefused('没有已核验的当前基础环境，无法迁移')
    checkpoint(cancel)
    target = runtime / ('rootfs-' + artifact.sha256[:16])
    workspace = private_path(runtime, 'migrations/' + artifact.sha256)
    workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
    journal = workspace / JOURNAL
    backup = workspace / 'backup'
    try:
        record = read_private_json(journal)
    except FileNotFoundError:
        if current[1] == artifact.sha256:
            raise MigrationRefused('已经在使用这个基础环境，无需迁移')
        if os.path.lexists(target) or os.path.lexists(backup):
            raise ValueError('Migration target or backup is not owned by a pending transaction')
        record = {'version': 1, 'state': 'preparing', 'source': binding_value(home, *current),
                  'target': binding_value(home, target, artifact.sha256)}
        _write_record(journal, record)
    if record['version'] != 1 or record['target'] != binding_value(home, target, artifact.sha256):
        raise ValueError('Migration target identity differs from the transaction')
    if record['state'] in ('rollback_copying', 'rollback_ready', 'rolled_back'):
        raise MigrationRefused('这次迁移已经回退；如需再次迁移，请先清理迁移记录')
    prior = private_path(home, record['source']['root'])
    if prior == target or generation_digest(prior) != record['source']['sha256']:
        raise ValueError('Migration source no longer matches its verified base')
    if current[0] not in (prior, target):
        raise ValueError('The active base changed while migration was pending')
    if current[0] == target:
        if record['state'] not in ('root_published', 'complete'):
            raise ValueError('The active target has no publish record')
        # Data and program may legitimately change after the binding switches.
        if record['state'] != 'complete':
            _write_record(journal, {**record, 'state': 'complete'})
        return target

    source = RuntimePaths(home, prior)
    if os.path.lexists(source.from_guest(NAPCAT_PENDING)):
        raise MigrationRefused('本机 NapCat 安装尚未完成，请先完成或取消安装再迁移')
    identities = record.get('identities')
    if identities is None:
        copy_bytes = sum(tree_size(source.from_guest(guest), cancel, skip_top_level=('venv',) if role == 'kira_program' else ())
                         ['copy_bytes'] for role, guest in {**_COPIED, 'kira_data': KIRA_DATA}.items())
        require_space('base-migration-backup', [Allocation('backup', workspace, copy_bytes)], cancel,
                      runtime / 'space-preflight.json')
        with tempfile.TemporaryDirectory(dir=workspace, prefix='copy-') as work:
            staged = Path(work) / 'backup'
            staged.mkdir(mode=0o700)
            identities = _source_identities(source, cancel, staged)
            if not identities['kira_program']['present']:
                raise MigrationRefused('当前基础环境里没有 KiraAI 程序，无需迁移')
            _verify_backup(staged, identities, cancel)
            if os.path.lexists(backup):
                _verify_backup(backup, identities, cancel)
            else:
                os.replace(staged, backup)
            record.update(state='backed_up', identities=identities)
            _write_record(journal, record)
    _verify_backup(backup, identities, cancel)
    if _source_identities(source, cancel) != identities:
        raise ValueError('Source changed after creating the verified migration backup')

    if not os.path.lexists(target):
        if record['state'] not in ('backed_up', 'verified'):
            raise ValueError('Published migration target is missing')
        archive = Path(archive)
        if archive.stat().st_size != artifact.size or digest(archive, cancel) != artifact.sha256:
            raise ValueError('Target base archive does not match its fixed identity')
        copies = sum(_tree_bytes(backup / role) for role in _COPIED if identities[role]['present'])
        require_space('base-migration-target', [Allocation('target', runtime, tar_bytes(archive, cancel) + copies)],
                      cancel, runtime / 'space-preflight.json')
        with tempfile.TemporaryDirectory(dir=runtime, prefix='rootfs.stage-r2b-') as work:
            stage = Path(work) / 'guest'
            unpack_rootfs(archive, stage, cancel)
            paths = RuntimePaths(home, stage)
            for role, guest in _COPIED.items():
                destination = paths.from_guest(guest)
                if os.path.lexists(destination):
                    raise ValueError('Target base already owns a migrated program or data path')
                if identities[role]['present']:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    copied = _identity(backup / role, cancel, copy_to=destination)
                    if copied != identities[role]:
                        raise ValueError('Target copy no longer matches its verified backup')
            with _isolated_probe_paths(workspace, stage, backup, identities, cancel) as probe_paths:
                prepare_target(probe_paths, cancel, identities['napcat_program']['present'])
                proof = _environment_identity(probe_paths, cancel, verify_guest)
            _verify_target(paths, identities, cancel)
            _write_record(stage / ROOT_MARKER, {'sha256': artifact.sha256, 'version': 1})
            record.update(state='verified', environment=proof)
            _write_record(journal, record)
            checkpoint(cancel)
            if os.path.lexists(target):
                raise ValueError('Migration target appeared before publish')
            os.replace(stage, target)
    if record['state'] not in ('verified', 'root_published') or generation_digest(target) != artifact.sha256:
        raise ValueError('Migration target does not match its publish preimage')
    paths = RuntimePaths(home, target)
    _verify_target(paths, identities, cancel)
    with _isolated_probe_paths(workspace, target, backup, identities, cancel) as probe_paths:
        if _environment_identity(probe_paths, cancel, verify_guest) != record['environment']:
            raise ValueError('Target dependency identity changed before activation')
    _verify_target(paths, identities, cancel)
    if _source_identities(source, cancel) != identities or bound_base(home) != current:
        raise ValueError('Migration source or active binding changed before activation')
    record['state'] = 'root_published'
    _write_record(journal, record)

    def commit():
        checkpoint(cancel)
        _write_record(runtime / BASE_BINDING, record['target'])
    if publish_binding is None:
        commit()
    else:
        publish_binding(commit)
    _write_record(journal, {**record, 'state': 'complete'})
    return target


def _tree_bytes(path):
    total = 0
    for folder, names, files in os.walk(path):
        names[:] = [name for name in names if not (Path(folder) / name).is_symlink()]
        for name in files:
            try:
                total += (Path(folder) / name).lstat().st_size
            except OSError:
                pass
    return total


def _remove(path):
    path = Path(path)
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif os.path.lexists(path):
        for folder, names, _ in os.walk(path):
            for name in names:
                child = Path(folder) / name
                if not child.is_symlink():
                    os.chmod(child, 0o700)
        shutil.rmtree(path)


def rollback(home, cancel, *, exclusive_owner, publish_binding=None):
    """Switch back to the base the last completed migration left, keeping later NapCat and QQ writes."""
    with exclusive_owner:
        return _rollback(home, cancel, publish_binding)


def _rollback(home, cancel, publish_binding):
    home = Path(home).resolve()
    runtime = RuntimePaths(home).state
    current = bound_base(home)
    if current is None:
        raise MigrationRefused('没有已核验的当前基础环境，无法回退')
    found = None
    for workspace in _workspaces(home):
        record = read_private_json(workspace / JOURNAL)
        if record.get('version') == 1 and record.get('state') in ('complete', 'rollback_copying', 'rollback_ready', 'rolled_back'):
            found = (workspace, record)
    if found is None:
        raise MigrationRefused('没有可以回退的迁移')
    workspace, record = found
    journal = workspace / JOURNAL
    source_root = private_path(home, record['source']['root'])
    target_root = private_path(home, record['target']['root'])
    if record['state'] == 'rolled_back':
        return source_root
    if current[0] == source_root and record['state'] == 'rollback_ready':
        # The binding switch finished; only the journal update was interrupted.
        _finish_rollback(workspace, record, journal)
        return source_root
    if current[0] != target_root or current[1] != record['target']['sha256']:
        raise MigrationRefused('当前使用的不是这次迁移的新基础环境，无法回退')
    if generation_digest(source_root) != record['source']['sha256']:
        raise MigrationRefused('旧基础环境已经不完整，无法回退')
    identities = record['identities']
    target, source = RuntimePaths(home, target_root), RuntimePaths(home, source_root)
    # The shared KiraAI data may only go back to the very program that wrote it before the migration.
    for role in ('kira_program', 'host_tools'):
        if _identity(target.from_guest(_COPIED[role]), cancel, skip_venv=role == 'kira_program') != identities[role]:
            raise MigrationRefused('迁移后 KiraAI 程序已经更新，旧环境可能读不了现在的数据，不能直接回退；请改用 KiraAI 备份恢复')
    # Nothing runs in the old base after a migration; if it changed, someone else touched it.
    for role in ('kira_program', 'host_tools'):
        if _identity(source.from_guest(_COPIED[role]), cancel, skip_venv=role == 'kira_program') != identities[role]:
            raise MigrationRefused('旧基础环境在迁移后被改动，无法安全回退')
    carry = workspace / 'rollback'
    if record['state'] == 'complete':
        _remove(carry)
        carry.mkdir(mode=0o700)
        carried = {}
        for role in _CARRIED:
            carried[role] = _identity(target.from_guest(_COPIED[role]), cancel, copy_to=carry / role)
            if carried[role]['present'] and _identity(carry / role, cancel) != carried[role]:
                raise ValueError('Rollback copy differs from the NapCat or QQ data it was made from')
        record = {**record, 'state': 'rollback_copying', 'carried': carried}
        _write_record(journal, record)
        record = {**record, 'state': 'rollback_ready'}
        _write_record(journal, record)
    if record['state'] == 'rollback_copying':
        for role in _CARRIED:
            if _identity(carry / role, cancel) != record['carried'][role]:
                raise ValueError('Rollback copy is incomplete; run the rollback again')
        record = {**record, 'state': 'rollback_ready'}
        _write_record(journal, record)
    # Swap the carried NapCat program and QQ data into the old base. Each step can be repeated.
    replaced = workspace / 'rollback-replaced'
    replaced.mkdir(mode=0o700, exist_ok=True)
    for role in _CARRIED:
        checkpoint(cancel)
        destination = source.from_guest(_COPIED[role])
        incoming = carry / role
        if not os.path.lexists(incoming):
            if _identity(destination, cancel) != record['carried'][role]:
                raise ValueError('Rollback lost the carried ' + role)
            continue
        if os.path.lexists(destination):
            aside = replaced / role
            if os.path.lexists(aside):
                _remove(aside)
            os.rename(destination, aside)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.rename(incoming, destination)
        if _identity(destination, cancel) != record['carried'][role]:
            raise ValueError('Rolled back ' + role + ' differs from its carried copy')

    def commit():
        _write_record(runtime / BASE_BINDING, record['source'])
    if publish_binding is None:
        commit()
    else:
        publish_binding(commit)
    _finish_rollback(workspace, record, journal)
    return source_root


def _finish_rollback(workspace, record, journal):
    _write_record(journal, {**record, 'state': 'rolled_back'})
    # The pre-migration NapCat and QQ copies were superseded by the carried ones.
    _remove(workspace / 'rollback-replaced')
    _remove(workspace / 'rollback')
