"""Bounded growth for host logs, install records and caches (plan R5C3).

Rules confirmed by the user on 2026-11-15:
- a component log over 8 MiB is rotated and two older logs are kept;
- only the three most recent NapCat install records are kept;
- NapCat download caches keep only the current version;
- two old panel backups are kept;
- APT, pip and npm caches are cleared only when free space is under 1 GiB.
(The downloaded APK update is handled by the Android app, which can tell whether it was installed.)

Never touched: KiraAI or QQ data, plugins, Kira backups (their own two-copy rule applies),
an unfinished NapCat installation and its files, failed install records (listed for the
user instead) and anything whose name or type this module does not recognise.

`apply` runs only while no guest process exists, so nothing it removes can be in use.
"""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time

from linux_installer import checkpoint
from runtime_paths import NAPCAT_PENDING, RuntimePaths, read_private_json

LOG_LIMIT = 8 * 1024 * 1024
LOG_KEEP = 2
ATTEMPT_KEEP = 3
PANEL_BACKUP_KEEP = 2
PACKAGE_CACHE_FREE = 1024 * 1024 * 1024
REPORT = 'retention-report.json'

_HEX32 = re.compile(r'[0-9a-f]{32}')
_LOG = re.compile(r'[A-Za-z0-9_.-]+\.log')
_DOWNLOAD = (re.compile(r'NapCat\.Shell-v\d+\.\d+\.\d+\.zip'), re.compile(r'napcat-install-[0-9a-f]{40}\.sh'),
             re.compile(r'linuxqq_\d+\.\d+\.\d+-\d+_(?:arm64|amd64)\.deb'))
# Package-manager caches inside the guest: (guest-relative directory, what it is).
_PACKAGE_CACHES = (('var/cache/apt/archives', 'apt'), ('root/.cache/pip', 'pip'), ('root/.npm/_cacache', 'npm'))


def _owned_dir(path):
    return not Path(path).is_symlink() and Path(path).is_dir()


def _bytes(path):
    path = Path(path)
    if path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for folder, names, files in os.walk(path):
        names[:] = [name for name in names if not (Path(folder) / name).is_symlink()]
        for name in files:
            try:
                total += (Path(folder) / name).lstat().st_size
            except OSError:
                pass
    return total


def _delete(path):
    path = Path(path)
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    for folder, names, _ in os.walk(path):
        for name in names:
            child = Path(folder) / name
            if not child.is_symlink():
                os.chmod(child, 0o700)
    shutil.rmtree(path)


def rotate_log(path):
    """Before a writer opens it: shift name.log -> name.log.1 -> name.log.2, dropping the oldest. Returns bytes dropped."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= LOG_LIMIT:
        return 0
    dropped = 0
    oldest = path.with_name(path.name + '.' + str(LOG_KEEP))
    if oldest.is_file() and not oldest.is_symlink():
        dropped = oldest.stat().st_size
        oldest.unlink()
    for index in range(LOG_KEEP - 1, 0, -1):
        older = path.with_name(path.name + '.' + str(index))
        if older.is_file() and not older.is_symlink():
            os.replace(older, path.with_name(path.name + '.' + str(index + 1)))
    os.replace(path, path.with_name(path.name + '.1'))
    return dropped


def truncate_log(path):
    """For a log a running process keeps open in append mode: copy it to .1, then empty it in place."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= LOG_LIMIT:
        return 0
    dropped = 0
    oldest = path.with_name(path.name + '.' + str(LOG_KEEP))
    if oldest.is_file() and not oldest.is_symlink():
        dropped = oldest.stat().st_size
        oldest.unlink()
    for index in range(LOG_KEEP - 1, 0, -1):
        older = path.with_name(path.name + '.' + str(index))
        if older.is_file() and not older.is_symlink():
            os.replace(older, path.with_name(path.name + '.' + str(index + 1)))
    copy = path.with_name(path.name + '.1')
    with path.open('rb') as source, copy.open('wb') as target:
        shutil.copyfileobj(source, target, 1024 * 1024)
    # Writers use O_APPEND, so their next write lands at the start of the emptied file.
    os.truncate(path, 0)
    return dropped


def _record(report, kind, removed=None, size=0, keep=None, reason=None):
    if removed is not None:
        report['removed'].append({'kind': kind, 'name': removed, 'bytes': size})
        report['reclaimed_bytes'][kind] = report['reclaimed_bytes'].get(kind, 0) + size
    if keep is not None:
        report['kept_for_review'].append({'kind': kind, 'name': keep, 'reason': reason})


def _logs(state, report):
    for path in sorted(state.iterdir()):
        if _LOG.fullmatch(path.name) and not path.is_symlink() and path.is_file():
            dropped = rotate_log(path)
            if dropped:
                _record(report, 'logs', path.name + '.' + str(LOG_KEEP), dropped)


def _pending(root):
    """The attempt and shell archive an unfinished NapCat installation still needs."""
    path = Path(root) / NAPCAT_PENDING[1:]
    if not os.path.lexists(path):
        return None, set(), False
    try:
        value = read_private_json(path, 65536)
        names = {value['shell']['filename']} if isinstance(value.get('shell'), dict) else set()
        return value.get('attempt'), names, True
    except (OSError, ValueError, KeyError, TypeError):
        return None, set(), True


def _attempts(napcat, root, report, cancel):
    directory = napcat / 'attempts'
    if not _owned_dir(directory):
        return
    protected, _, pending = _pending(root)
    finished = []
    for path in directory.iterdir():
        checkpoint(cancel)
        if not _HEX32.fullmatch(path.name) or not _owned_dir(path):
            _record(report, 'install_records', keep=path.name, reason='unrecognised')
            continue
        try:
            record = read_private_json(path / 'attempt.json', 65536)
            state = record.get('state') if isinstance(record, dict) else None
            moment = (path / 'attempt.json').stat().st_mtime
        except (OSError, ValueError):
            state, moment = None, path.stat().st_mtime
        if path.name == protected or state in ('preparing', 'installing') or state is None:
            if path.name != protected and state is None:
                _record(report, 'install_records', keep=path.name, reason='unreadable')
            continue
        finished.append((moment, path.name, state, path))
    finished.sort(reverse=True)
    if pending:
        # An unfinished installation may still need any earlier record for diagnosis or recovery.
        return
    for _, name, state, path in finished[ATTEMPT_KEEP:]:
        if state != 'complete':
            # Evidence of a failed or cancelled installation stays until the user looks at it.
            _record(report, 'install_records', keep=name, reason=state)
            continue
        size = _bytes(path)
        _delete(path)
        _record(report, 'install_records', removed=name, size=size)


def _downloads(napcat, root, current, report):
    directory = napcat / 'downloads'
    if not _owned_dir(directory):
        return
    _, needed, pending = _pending(root)
    keep = set(current) | needed
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            _record(report, 'napcat_downloads', keep=path.name, reason='not a regular file')
            continue
        partial = path.name.endswith('.part') and any(rule.fullmatch(path.name[:-5]) for rule in _DOWNLOAD)
        known = any(rule.fullmatch(path.name) for rule in _DOWNLOAD)
        if not (known or partial):
            _record(report, 'napcat_downloads', keep=path.name, reason='unrecognised')
            continue
        if pending or path.name in keep:
            continue
        size = path.stat().st_size
        path.unlink()
        _record(report, 'napcat_downloads', removed=path.name, size=size)


def _panel_backups(state, report):
    directory = state / 'panel-backups'
    if not _owned_dir(directory):
        return
    backups = []
    for path in directory.iterdir():
        if not _HEX32.fullmatch(path.name) or not _owned_dir(path):
            _record(report, 'panel_backups', keep=path.name, reason='unrecognised')
            continue
        backups.append((path.stat().st_mtime, path.name, path))
    backups.sort(reverse=True)
    for _, name, path in backups[PANEL_BACKUP_KEEP:]:
        size = _bytes(path)
        _delete(path)
        _record(report, 'panel_backups', removed=name, size=size)


def _package_caches(root, report, cancel):
    usage = shutil.disk_usage(root)
    report['free_bytes_before'] = usage.free
    if usage.free >= PACKAGE_CACHE_FREE:
        return
    report['package_caches_cleaned'] = True
    for relative, kind in _PACKAGE_CACHES:
        directory = Path(root) / relative
        if not _owned_dir(directory):
            continue
        for path in directory.iterdir():
            checkpoint(cancel)
            if path.is_symlink():
                continue
            if kind == 'apt' and path.name in ('lock',):
                continue
            if kind == 'apt' and path.name == 'partial' and path.is_dir():
                for item in path.iterdir():
                    if item.is_file() and not item.is_symlink():
                        size = item.stat().st_size
                        item.unlink()
                        _record(report, 'package_caches', removed='apt/partial/' + item.name, size=size)
                continue
            if kind == 'apt' and not (path.is_file() and path.name.endswith('.deb')):
                continue
            size = _bytes(path)
            _delete(path)
            _record(report, 'package_caches', removed=kind + '/' + path.name, size=size)


def apply(home, root, cancel, *, napcat_current=()):
    """Apply every rule once; a failure in one category is reported and never blocks startup.

    napcat_current: download file names the installed (or, if none, the bundled baseline) NapCat needs.
    """
    paths = RuntimePaths(home, root)
    state = paths.state
    report = {'version': 1, 'at_unix_ms': int(time.time() * 1000), 'reclaimed_bytes': {}, 'removed': [],
              'kept_for_review': [], 'errors': [], 'package_caches_cleaned': False}
    napcat = state / 'napcat'
    steps = (('logs', lambda: _logs(state, report)),
             ('install_records', lambda: _attempts(napcat, root, report, cancel)),
             ('napcat_downloads', lambda: _downloads(napcat, root, napcat_current, report)),
             ('panel_backups', lambda: _panel_backups(state, report)),
             ('package_caches', lambda: _package_caches(root, report, cancel)))
    for kind, step in steps:
        checkpoint(cancel)
        try:
            step()
        except (OSError, ValueError) as error:
            report['errors'].append({'kind': kind, 'error': type(error).__name__})
    report['reclaimed_total_bytes'] = sum(report['reclaimed_bytes'].values())
    temporary = state / (REPORT + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, state / REPORT)
    return report
